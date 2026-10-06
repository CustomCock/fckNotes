"""PDF-Tab: Scrollen, Zoom, Seitensprung, Textsuche, Lesezeichen, Text markieren → Zitat in die Notiz – und im
Bearbeiten-Modus (Stift) Seiten organisieren (Seitenleiste mit Miniaturen).

Eigene Seitenansicht auf QPdfDocument statt QPdfView, weil QPdfView keine Textauswahl kann. Seiten werden in einem
Hintergrund-Renderer gerastert (QPdfPageRenderer, mehrere Threads) und zwischengespeichert – die Oberfläche wartet
nie auf eine Seite. Es gibt keine Formular-, Link- oder Skript-Ausführung. Die Datei wird in den Speicher gelesen und
sofort geschlossen, damit sie umbenannt/verschoben werden kann. Bearbeitet wird immer eine Kopie im Speicher
(Bytes, jede Änderung ein Rückgängig-Schritt); erst Ctrl+S schreibt die Datei.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtPdf import (QPdfBookmarkModel, QPdfDocument, QPdfDocumentRenderOptions, QPdfPageRenderer,
                           QPdfSearchModel)
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QComboBox, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMenu, QPushButton, QSplitter, QTreeView, QVBoxLayout)

from notex.core import pdfannot, pdfdoc, pdfforms, pdfobjects, pdfpages, pdfredact
from notex.core.pdfdoc import PageLayout
from notex.theme.theme import style_menu
from notex.theme.tokens import COLORS, SPACING
from notex.ui.viewer_page import ViewerPage, human_size
from notex.ui.widgets import IconButton

TOOLS = [
    ("select", "mouse-pointer-2", "Auswählen (Text markieren, Zitat)"),
    ("highlight", "highlighter", "Markieren: Text überstreichen"),
    ("underline", "underline", "Unterstreichen: Text überstreichen"),
    ("strikeout", "strikethrough", "Durchstreichen: Text überstreichen"),
    ("note", "sticky-note", "Notiz: auf die Seite klicken"),
    ("text", "type", "Text auf der Seite: Bereich aufziehen oder klicken"),
    ("field", "text-cursor-input", "Neues Textfeld (Formular): Bereich aufziehen"),
    ("checkbox", "square-check", "Neues Kontrollkästchen (Formular): klicken"),
    ("signature", "signature", "Unterschrift: zeichnen oder Bild wählen, dann Bereich aufziehen/klicken"),
    ("redact", "redact", "Schwärzen: Bereiche aufziehen (endgültig erst mit „Schwärzen anwenden“)"),
]
CACHE_IMAGES = 24
MAX_UNDO = 40
ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0]
IN_MEMORY_LIMIT = 256 * 1024 * 1024
PAGE_WHITE = "#ffffff"      # PDF-Seiten sind immer weiß gestaltet – unabhängig vom Blatt-Theme


def _alpha(color: str, alpha: int) -> QColor:
    result = QColor(color)
    result.setAlpha(alpha)
    return result


REGION_TOOLS = {"note", "text", "field", "checkbox", "signature", "redact"}   # Ziehen/Klicken legt einen Bereich fest
MARKUP_TOOLS = {"highlight", "underline", "strikeout", "redact_text"}          # Textauswahl → sofort anwenden


class _PdfCanvas(QAbstractScrollArea):
    page_changed = Signal(int)
    selection_changed = Signal()
    zoom_changed = Signal()
    quote_requested = Signal()
    region_chosen = Signal(int, QRectF)       # Seite, Bereich in Ansichts-Punkten (Klick = Breite/Höhe 0)
    markup_chosen = Signal()                  # Textauswahl im Markier-Werkzeug fertig
    tool_cancelled = Signal()                 # Esc: zurück zum Auswahl-Werkzeug

    def __init__(self, document: QPdfDocument) -> None:
        super().__init__()
        self.setObjectName("PdfCanvas")
        self.doc = document
        self.zoom_mode = "width"          # "width" | "page" | "custom"
        self.zoom = 1.0                   # bei "custom": 1.0 = 100 %
        self.layout_: PageLayout | None = None
        self.sizes: list[tuple[float, float]] = []
        self._images: OrderedDict[tuple[int, int], object] = OrderedDict()
        self._pending: dict[int, tuple[int, int]] = {}      # Request-ID → (Seite, Breite)
        self.renderer = QPdfPageRenderer(self)
        self.renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
        self.renderer.setDocument(document)
        self.renderer.pageRendered.connect(self._on_rendered)
        self.options = QPdfDocumentRenderOptions()
        self.options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        self.selection = None             # QPdfSelection
        self._lines: dict[int, list] = {}
        self.selection_page = -1
        self._drag_start: tuple[int, float, float] | None = None
        self.highlights: list[tuple[int, list]] = []   # (Seite, [QRectF in Punkt]) – Suchtreffer
        self.current_highlight: tuple[int, list] | None = None
        self.current_page = 0
        self.tool = "select"
        self.menu_hook = None                 # callable(menu, page, x_pt, y_pt) – ergänzt das Kontextmenü
        self.overlays: list[tuple[int, QRectF, str]] = []    # (Seite, Rechteck in Punkt, Art) z. B. Schwärz-Vorschau
        self._band: tuple[int, float, float, float, float] | None = None
        self.object_handler = None            # ObjectController: Eingefügtes anfassen (Werkzeug „Auswählen“)
        self._object_drag = False
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.verticalScrollBar().valueChanged.connect(self._track_page)

    # ---- Layout -----------------------------------------------------------------------------------
    def reset_document(self) -> None:
        self.sizes = []
        for index in range(self.doc.pageCount()):
            size = self.doc.pagePointSize(index)
            self.sizes.append((max(1.0, size.width()), max(1.0, size.height())))
        self._images.clear()
        self._pending.clear()
        self._lines = {}
        self.selection = None
        self.relayout()

    @property
    def unit(self) -> float:
        """Pixel pro Punkt bei 100 % (Bildschirm-DPI statt 72)."""
        return self.logicalDpiY() / pdfdoc.POINTS_PER_INCH

    def scale(self) -> float:
        width = self.viewport().width()
        if self.zoom_mode == "width" and self.sizes:
            return pdfdoc.fit_width_scale(self.sizes, width)
        if self.zoom_mode == "page" and self.sizes:
            return pdfdoc.fit_page_scale(self.sizes[self.current_page], width, self.viewport().height())
        return self.zoom * self.unit

    def zoom_percent(self) -> int:
        return round(self.scale() / self.unit * 100)

    def relayout(self, keep_page: bool = True) -> None:
        page, offset = self.current_page, 0.0
        if keep_page and self.layout_ is not None and self.layout_.pages:
            rect = self.layout_.pages[min(page, len(self.layout_.pages) - 1)]
            offset = (self.verticalScrollBar().value() - rect.y) / max(1.0, rect.height)
        self.layout_ = PageLayout(self.sizes, self.scale(), self.viewport().width())
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, int(self.layout_.height - self.viewport().height())))
        bar.setPageStep(self.viewport().height())
        bar.setSingleStep(40)
        hbar = self.horizontalScrollBar()
        hbar.setRange(0, max(0, int(self.layout_.width - self.viewport().width())))
        hbar.setPageStep(self.viewport().width())
        if keep_page and self.layout_.pages:
            rect = self.layout_.pages[min(page, len(self.layout_.pages) - 1)]
            bar.setValue(int(rect.y + offset * rect.height))
        self.viewport().update()
        if self.object_handler is not None:
            self.object_handler.reposition_editor()            # Eingabe bleibt auf dem Feld
        self.zoom_changed.emit()

    def set_zoom(self, mode: str, zoom: float | None = None) -> None:
        self.zoom_mode = mode
        if zoom is not None:
            self.zoom = max(pdfdoc.MIN_ZOOM, min(pdfdoc.MAX_ZOOM, zoom))
        self.relayout()

    def zoom_step(self, direction: int) -> None:
        current = self.scale() / self.unit
        steps = [z for z in ZOOM_STEPS if (z > current + 0.001 if direction > 0 else z < current - 0.001)]
        if steps:
            self.set_zoom("custom", steps[0] if direction > 0 else steps[-1])

    # ---- Navigation -------------------------------------------------------------------------------
    def _track_page(self, value: int) -> None:
        if self.layout_ is None or not self.layout_.pages:
            return
        page = self.layout_.page_at_y(value + self.viewport().height() / 3)
        if page != self.current_page:
            self.current_page = page
            self.page_changed.emit(page)
        self.viewport().update()

    def go_to(self, page: int, location: QPointF | None = None) -> None:
        if self.layout_ is None or not self.layout_.pages:
            return
        page = max(0, min(page, len(self.layout_.pages) - 1))
        rect = self.layout_.pages[page]
        y = rect.y - pdfdoc.PAGE_GAP / 2
        if location is not None and (location.y() > 0 or location.x() > 0):
            y = rect.y + location.y() * self.layout_.scale - self.viewport().height() / 4
        self.verticalScrollBar().setValue(int(y))
        self.current_page = page
        self.page_changed.emit(page)

    # ---- Rendern ----------------------------------------------------------------------------------
    def _image(self, index: int, width: int):
        key = (index, width)
        image = self._images.get(key)
        if image is not None:
            self._images.move_to_end(key)
            return image
        if key not in self._pending.values():
            dpr = self.devicePixelRatioF()
            w, h = self.sizes[index]
            size = QSize(int(width * dpr), int(width * dpr * h / w))
            request = self.renderer.requestPage(index, size, self.options)
            self._pending[request] = key
        # Ersatz: gleiche Seite in anderer Größe (beim Zoomen), sonst nichts
        for (page, _w), other in reversed(self._images.items()):
            if page == index:
                return other
        return None

    def _on_rendered(self, page: int, _size, image, _options, request: int) -> None:
        key = self._pending.pop(request, None)
        if key is None:
            return
        image.setDevicePixelRatio(self.devicePixelRatioF())
        self._images[key] = image
        while len(self._images) > CACHE_IMAGES:
            self._images.popitem(last=False)
        self.viewport().update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(COLORS.bg))
        if self.layout_ is None:
            return
        dx, dy = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        scale = self.layout_.scale
        for page in self.layout_.visible(dy, dy + self.viewport().height()):
            target = QRectF(page.x - dx, page.y - dy, page.width, page.height)
            painter.fillRect(target, QColor(PAGE_WHITE))
            image = self._image(page.index, int(page.width))
            if image is not None:
                painter.drawImage(target, image)
            painter.setPen(Qt.PenStyle.NoPen)
            for index, rects in self.highlights:
                if index == page.index:
                    painter.setBrush(_alpha(COLORS.paper_match, 150))
                    for r in rects:
                        painter.drawRect(QRectF(target.x() + r.x() * scale, target.y() + r.y() * scale,
                                                r.width() * scale, r.height() * scale))
            if self.current_highlight is not None and self.current_highlight[0] == page.index:
                painter.setBrush(_alpha(COLORS.warning, 140))
                for r in self.current_highlight[1]:
                    painter.drawRect(QRectF(target.x() + r.x() * scale, target.y() + r.y() * scale,
                                            r.width() * scale, r.height() * scale))
            for index, rect, kind in self.overlays:
                if index != page.index:
                    continue
                view = QRectF(target.x() + rect.x() * scale, target.y() + rect.y() * scale,
                              rect.width() * scale, rect.height() * scale)
                if kind == "redact":
                    painter.setBrush(QColor(0, 0, 0, 150))
                    painter.setPen(QPen(QColor(COLORS.danger), 1.5))
                else:
                    painter.setBrush(_alpha(COLORS.accent, 40))
                    painter.setPen(QPen(QColor(COLORS.accent), 1.5))
                painter.drawRect(view)
                painter.setPen(Qt.PenStyle.NoPen)
            if self._band is not None and self._band[0] == page.index:
                _i, x0, y0, x1, y1 = self._band
                painter.setBrush(_alpha(COLORS.accent, 30))
                pen = QPen(QColor(COLORS.accent), 1.2, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawRect(QRectF(target.x() + min(x0, x1) * scale, target.y() + min(y0, y1) * scale,
                                        abs(x1 - x0) * scale, abs(y1 - y0) * scale))
                painter.setPen(Qt.PenStyle.NoPen)
            if self.object_handler is not None:
                self.object_handler.paint(painter, page.index, target, scale)
                painter.setPen(Qt.PenStyle.NoPen)
            if self.selection is not None and self.selection_page == page.index:
                painter.setBrush(QColor(COLORS.accent).lighter(130))
                painter.setOpacity(0.35)
                for polygon in self.selection.bounds():
                    painter.drawPolygon(QPolygonF([QPointF(target.x() + p.x() * scale, target.y() + p.y() * scale)
                                                   for p in polygon]))
                painter.setOpacity(1.0)
        painter.end()

    # ---- Auswahl ----------------------------------------------------------------------------------
    def _doc_pos(self, event) -> tuple[float, float]:
        return (event.position().x() + self.horizontalScrollBar().value(),
                event.position().y() + self.verticalScrollBar().value())

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self._band = None
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if tool in REGION_TOOLS else Qt.CursorShape.IBeamCursor)
        self.viewport().update()

    def page_point(self, page: int, event) -> tuple[float, float]:
        """Mausposition in Ansichts-Punkten relativ zu Seite `page` (auch außerhalb der Seite)."""
        rect = self.layout_.pages[page]
        x, y = self._doc_pos(event)
        return (x - rect.x) / self.layout_.scale, (y - rect.y) / self.layout_.scale

    def _object_hit(self, event):
        hit = self.layout_.hit(*self._doc_pos(event))
        handler = self.object_handler
        if hit is None and handler is not None and handler.selected is not None:
            page = handler.selected.page
            return (page, *self.page_point(page, event))
        return hit

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton and self.layout_ is not None and self.tool == "select"
                and self.object_handler is not None):
            hit = self._object_hit(event)
            if hit is not None and self.object_handler.press(*hit, event.position()):
                self._object_drag = True
                if self.selection is not None:
                    self.selection = None
                    self.selection_changed.emit()
                return
        if event.button() == Qt.MouseButton.LeftButton and self.layout_ is not None and self.tool in REGION_TOOLS:
            hit = self.layout_.hit(*self._doc_pos(event))
            if hit is not None:
                page, x, y = hit
                self._band = (page, x, y, x, y)
            return
        if event.button() == Qt.MouseButton.LeftButton and self.layout_ is not None:
            hit = self.layout_.hit(*self._doc_pos(event))
            self._drag_start = hit
            if self.selection is not None:
                self.selection = None
                self.selection_changed.emit()
                self.viewport().update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        handler = self.object_handler
        if self._object_drag and handler is not None and handler._press is not None:
            page = handler._press[0]
            handler.move(page, *self.page_point(page, event), event.position())
            return
        if (handler is not None and self.tool == "select" and self.layout_ is not None
                and not event.buttons() & Qt.MouseButton.LeftButton):
            hit = self._object_hit(event)
            cursor = handler.cursor_for(*hit) if hit is not None else None
            self.viewport().setCursor(cursor if cursor is not None else Qt.CursorShape.IBeamCursor)
        if self._band is not None and self.layout_ is not None:
            page, x0, y0, _x1, _y1 = self._band
            x1, y1 = self.layout_.clamp_hit(page, *self._doc_pos(event))
            self._band = (page, x0, y0, x1, y1)
            self.viewport().update()
            return
        if self._drag_start is None or self.layout_ is None:
            return
        page, sx, sy = self._drag_start
        ex, ey = self.layout_.clamp_hit(page, *self._doc_pos(event))
        self.selection = self._select(page, (sx, sy), (ex, ey))
        self.selection_page = page
        self.viewport().update()

    def _line_boxes(self, page: int) -> list[tuple[float, float, float, float]]:
        """Textzeilen einer Seite (ein PDFium-Aufruf, gecacht) – Ziel für das Einrasten der Auswahl."""
        boxes = self._lines.get(page)
        if boxes is None:
            boxes = []
            for polygon in self.doc.getAllText(page).bounds():
                rect = polygon.boundingRect()
                boxes.append((rect.left(), rect.top(), rect.right(), rect.bottom()))
            self._lines[page] = boxes
        return boxes

    def _select(self, page: int, start: tuple[float, float], end: tuple[float, float]):
        """Auswahl zwischen zwei Punkten; beide rasten auf die nächste Textzeile ein (PDFium trifft sonst nur,
        wenn der Punkt genau auf einem Zeichen liegt)."""
        lines = self._line_boxes(page)
        a, b = pdfdoc.snap_to_lines(lines, *start), pdfdoc.snap_to_lines(lines, *end)
        if a is None or b is None:
            return None
        for nudge in (0.0, 1.5, -1.5, 3.0, -3.0):
            selection = self.doc.getSelection(page, QPointF(a[0] + nudge, a[1]), QPointF(b[0] - nudge, b[1]))
            if selection.isValid() and selection.text():
                return selection
        return None

    def mouseReleaseEvent(self, event) -> None:
        if self._object_drag:
            self._object_drag = False
            handler = self.object_handler
            if handler is not None and handler._press is not None:
                page = handler._press[0]
                handler.release(page, *self.page_point(page, event))
            return
        if self._band is not None:
            page, x0, y0, x1, y1 = self._band
            self._band = None
            self.viewport().update()
            if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:          # Klick statt Ziehen
                x1, y1 = x0, y0
            self.region_chosen.emit(page, QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
            return
        if self._drag_start is not None:
            self._drag_start = None
            self.selection_changed.emit()
            if self.tool in MARKUP_TOOLS and self.selected_text():
                self.markup_chosen.emit()
        super().mouseReleaseEvent(event)

    def selection_rects(self) -> list[tuple[float, float, float, float]]:
        """Zeilen-Rechtecke der Auswahl (Ansichts-Punkte) – für Markieren und Schwärzen."""
        if self.selection is None:
            return []
        out = []
        for polygon in self.selection.bounds():
            rect = polygon.boundingRect()
            out.append((rect.left(), rect.top(), rect.right(), rect.bottom()))
        return out

    def mouseDoubleClickEvent(self, event) -> None:
        """Doppelklick markiert die ganze Seite nicht – nur das Wort darunter über eine kleine Auswahl."""
        if self.layout_ is None:
            return
        hit = self.layout_.hit(*self._doc_pos(event))
        if hit is None:
            return
        if self.tool == "select" and self.object_handler is not None and self.object_handler.double_click(*hit):
            return
        page, x, y = hit
        selection = self._select(page, (x - 1, y), (x + 1, y))
        if selection is not None:
            self.selection, self.selection_page = selection, page
            self.selection_changed.emit()
            self.viewport().update()

    def selected_text(self) -> str:
        return self.selection.text() if self.selection is not None else ""

    def select_all_on_page(self) -> None:
        if not self.sizes:
            return
        selection = self.doc.getAllText(self.current_page)
        if selection.isValid() and selection.text():
            self.selection, self.selection_page = selection, self.current_page
            self.selection_changed.emit()
            self.viewport().update()

    def keyPressEvent(self, event) -> None:
        if self.object_handler is not None and self.object_handler.key(event):
            return
        if event.key() == Qt.Key.Key_Escape and (self._band is not None or self.tool != "select"):
            self._band = None
            self.tool_cancelled.emit()
            self.viewport().update()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            if self.selected_text():
                QApplication.clipboard().setText(self.selected_text())
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all_on_page()
            return
        bar = self.verticalScrollBar()
        keys = {Qt.Key.Key_PageDown: bar.pageStep(), Qt.Key.Key_PageUp: -bar.pageStep(), Qt.Key.Key_Space: bar.pageStep(),
                Qt.Key.Key_Down: bar.singleStep(), Qt.Key.Key_Up: -bar.singleStep()}
        if event.key() in keys:
            bar.setValue(bar.value() + keys[event.key()])
            return
        if event.key() == Qt.Key.Key_Home:
            self.go_to(0)
            return
        if event.key() == Qt.Key.Key_End:
            self.go_to(len(self.sizes) - 1)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_step(1 if event.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        if self.object_handler is not None:
            self.object_handler.reposition_editor()            # Eingabe scrollt mit dem Feld mit
        self.viewport().update()

    def _context_menu(self, pos) -> None:
        menu = style_menu(QMenu(self))
        has = bool(self.selected_text())
        copy = menu.addAction("Kopieren", lambda: QApplication.clipboard().setText(self.selected_text()))
        copy.setEnabled(has)
        quote = menu.addAction("Als Zitat in Notiz einfügen", self.quote_requested.emit)
        quote.setEnabled(has)
        menu.addSeparator()
        menu.addAction("Ganze Seite markieren", self.select_all_on_page)
        if self.menu_hook is not None and self.layout_ is not None:
            hit = self.layout_.hit(pos.x() + self.horizontalScrollBar().value(),
                                   pos.y() + self.verticalScrollBar().value())
            if hit is not None:
                self.menu_hook(menu, *hit)
        menu.exec(self.viewport().mapToGlobal(pos))


def _strip_outline(data: bytes) -> bytes:
    from notex.core.pdfannot import finish, open_writer
    writer = open_writer(data)
    for key in ("/Outlines", "/PageMode"):
        if key in writer._root_object:
            del writer._root_object[key]
    return finish(writer)


class PdfPage(ViewerPage):
    kind = "pdf"
    icon_name = "file-type"
    quote_requested = Signal(str)          # fertiges Markdown-Zitat
    backup_to_trash = True                 # Einstellung: vor dem ersten Überschreiben Original in den Papierkorb

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.doc = QPdfDocument(self)
        self._buffer: QBuffer | None = None
        self.error = ""
        self.data: bytes | None = None      # aktueller Stand (bearbeitbar), None = zu groß/nicht lesbar
        self.encrypted = False
        self._undo: list[tuple[bytes, int]] = []
        self._redo: list[tuple[bytes, int]] = []
        self._dirty = False
        self._backed_up = False
        self.redacted = False               # nach Schwärzen: nur „Speichern unter“ (Original bleibt unangetastet)
        self.redactions: list[tuple[int, tuple]] = []   # vorgemerkte Balken (Seite, Rechteck in Ansichts-Punkten)
        self.redact_terms: list[str] = []               # dazu markierte Wörter – für die Restprüfung danach
        self.last_leftovers: list[str] = []             # Ergebnis der Restprüfung nach dem letzten Schwärzen
        self.canvas = _PdfCanvas(self.doc)
        self.canvas.page_changed.connect(self._on_page)
        self.canvas.zoom_changed.connect(self._on_zoom)
        self.canvas.selection_changed.connect(self._on_selection)
        self.canvas.quote_requested.connect(self.quote)
        self.canvas.region_chosen.connect(self._on_region)
        self.canvas.markup_chosen.connect(self._on_markup)
        self.canvas.tool_cancelled.connect(lambda: self.set_tool("select"))
        self.canvas.menu_hook = self._extend_menu
        from notex.ui.pdf_objects import ObjectController
        self.objects = ObjectController(self)
        self.canvas.object_handler = self.objects
        self.tool_colors: dict[str, str] = {}      # Werkzeug → gewählte Farbe (sonst Standard)

        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.doc)
        self.outline = QTreeView()
        self.outline.setObjectName("PdfOutline")
        self.outline.setModel(self.bookmarks)
        self.outline.setHeaderHidden(True)
        self.outline.clicked.connect(self._bookmark_clicked)
        self.outline.activated.connect(self._bookmark_clicked)

        self.search = QPdfSearchModel(self)
        self.search.setDocument(self.doc)
        self.search_index = -1
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._run_search)
        self.search.modelReset.connect(self._results_changed)
        self.search.rowsInserted.connect(lambda *_a: self._results_changed())

        outline_button = IconButton("bookmark", "Lesezeichen ein/aus")
        outline_button.setCheckable(True)
        outline_button.toggled.connect(self.outline.setVisible)
        self.outline_button = outline_button
        prev_page = IconButton("chevron-up", "Vorige Seite")
        prev_page.clicked.connect(lambda: self.canvas.go_to(self.canvas.current_page - 1))
        next_page = IconButton("chevron-down", "Nächste Seite")
        next_page.clicked.connect(lambda: self.canvas.go_to(self.canvas.current_page + 1))
        self.page_field = QLineEdit()
        self.page_field.setFixedWidth(56)
        self.page_field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_field.setToolTip("Seite (Nummer oder Seitenbezeichnung)  Ctrl+G")
        self.page_field.returnPressed.connect(self._jump_page)
        self.page_count = QLabel()
        self.page_count.setObjectName("DataInfo")
        self.zoom_box = QComboBox()
        self.zoom_box.addItem("Seitenbreite", ("width", None))
        self.zoom_box.addItem("Ganze Seite", ("page", None))
        for z in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0):
            self.zoom_box.addItem(f"{round(z * 100)} %", ("custom", z))
        self.zoom_box.activated.connect(self._zoom_chosen)
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Im PDF suchen …")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.textChanged.connect(lambda _t: self._search_timer.start())
        self.search_field.returnPressed.connect(lambda: self.next_result(1))
        prev_hit = IconButton("chevron-left", "Vorheriger Treffer  Shift+F3")
        prev_hit.clicked.connect(lambda: self.next_result(-1))
        next_hit = IconButton("chevron-right", "Nächster Treffer  F3")
        next_hit.clicked.connect(lambda: self.next_result(1))
        self.hits_label = QLabel()
        self.hits_label.setObjectName("DataInfo")
        self.quote_button = IconButton("quote", "Markierten Text als Zitat in die Notiz im anderen Teil einfügen")
        self.quote_button.clicked.connect(self.quote)
        self.quote_button.setEnabled(False)
        self.edit_button = IconButton("pencil", "PDF bearbeiten: Seiten, Anmerkungen, Formulare, Schwärzen")
        self.edit_button.setCheckable(True)
        self.edit_button.toggled.connect(self.set_editing)
        self.edit_bar = self._build_edit_bar()
        self.edit_bar.setVisible(False)
        from notex.ui.pdf_edit import PageStrip
        self.strip = PageStrip(self.doc)
        self.strip.page_activated.connect(lambda index: self.canvas.go_to(index))
        self.strip.order_changed.connect(self.reorder)
        self.strip.customContextMenuRequested.connect(self._strip_menu)
        self.strip.setVisible(False)
        from notex.ui.pdf_edit import FormPanel
        self.form_panel = FormPanel()
        self.form_panel.apply_requested.connect(lambda values: self.fill_form(values))
        self.form_panel.field_activated.connect(self.show_field)
        self.form_panel.setVisible(False)
        self.fields: list = []
        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.strip)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(lambda: self.delete_pages() if self.strip.selected_pages() else None)

        strip = QFrame()
        strip.setObjectName("DataBar")
        bar = QHBoxLayout(strip)
        bar.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        bar.setSpacing(SPACING.sm)
        for widget in (outline_button, prev_page, next_page, self.page_field, self.page_count, self.zoom_box):
            bar.addWidget(widget)
        bar.addSpacing(SPACING.md)
        bar.addWidget(self.search_field, 1)
        for widget in (prev_hit, next_hit, self.hits_label, self.quote_button, self.edit_button):
            bar.addWidget(widget)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("PreviewSplitter")
        self.splitter.addWidget(self.strip)
        self.splitter.addWidget(self.outline)
        self.splitter.addWidget(self.canvas)
        self.splitter.addWidget(self.form_panel)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setSizes([self.strip.width(), 220, 900, 280])
        self.outline.setVisible(False)
        frame = QFrame()
        frame.setObjectName("DataFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)
        frame_layout.addWidget(strip)
        frame_layout.addWidget(self.edit_bar)
        frame_layout.addWidget(self.splitter, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING.lg, SPACING.md, SPACING.lg, SPACING.lg)
        layout.addWidget(frame)
        for sequence, slot in (("Ctrl+G", self._focus_page), ("F3", lambda: self.next_result(1)),
                               ("Shift+F3", lambda: self.next_result(-1)), ("Ctrl+Z", self.undo),
                               ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self._load()

    # ---- Laden ------------------------------------------------------------------------------------
    def _load(self, password: str = "") -> None:
        self.doc.close()
        if password:
            self.doc.setPassword(password)
        self.encrypted = bool(password)
        size = self.path.stat().st_size
        if size <= IN_MEMORY_LIMIT:
            raw = self.path.read_bytes()                    # Datei gleich wieder zu (Windows: umbenennbar)
            self.data = raw
            self._set_buffer(self._display_bytes(raw))
        else:
            self.data = None
            self.doc.load(str(self.path))
        if self.doc.error() == QPdfDocument.Error.IncorrectPassword:
            text, ok = QInputDialog.getText(self, "Passwortgeschütztes PDF", f"Passwort für „{self.path.name}“:",
                                            QLineEdit.EchoMode.Password)
            if ok and text:
                self._load(text)
                return
            self.error = "Passwortgeschützt – nicht geöffnet"
        elif self.doc.error() != QPdfDocument.Error.None_:
            self.error = f"PDF lässt sich nicht öffnen ({self.doc.error().name})"
        else:
            self.error = ""
        self._after_load()
        has_outline = self.bookmarks.rowCount(QModelIndex()) > 0
        self.outline_button.setChecked(has_outline)
        self._on_page(0)
        self.status_changed.emit()

    def _display_bytes(self, raw: bytes) -> bytes:
        """Für die Anzeige: Formularfelder sichtbar machen (PDFium zeichnet sie in QtPdf sonst nicht)."""
        try:
            return pdfforms.display_copy(raw) or raw
        except Exception:                                  # noqa: BLE001 – Anzeige darf nie am Formular scheitern
            return raw

    def _set_buffer(self, raw: bytes) -> None:
        old = self._buffer
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(raw))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.doc.load(self._buffer)
        if old is not None:
            old.close()
            old.deleteLater()

    def _after_load(self) -> None:
        if hasattr(self, "objects"):
            self.objects.invalidate()
        self.canvas.reset_document()
        self.outline_button.setEnabled(self.bookmarks.rowCount(QModelIndex()) > 0)
        self.page_count.setText(f"/ {self.doc.pageCount()}")
        if not self.strip.isHidden():
            self.strip.rebuild()
        self.refresh_form()

    # ---- Bearbeiten: Zustand, Rückgängig, Speichern -----------------------------------------------------------
    def edit_block_reason(self) -> str:
        """Warum nicht bearbeitet werden kann ("" = geht)."""
        if self.error:
            return self.error
        if self.data is None:
            return "Zu groß zum Bearbeiten (über 256 MB)"
        if self.encrypted:
            return "Passwortgeschützte PDFs lassen sich nur ansehen, nicht bearbeiten"
        return ""

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _set_dirty(self, dirty: bool) -> None:
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirty_changed.emit(dirty)
        self._update_edit_actions()
        self.status_changed.emit()

    def set_editing(self, on: bool) -> None:
        if on and self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            self.edit_button.blockSignals(True)
            self.edit_button.setChecked(False)
            self.edit_button.blockSignals(False)
            return
        if self.edit_button.isChecked() != on:
            self.edit_button.blockSignals(True)
            self.edit_button.setChecked(on)
            self.edit_button.blockSignals(False)
        self.edit_bar.setVisible(on)
        self.set_pages_visible(on)
        if not on:
            self.set_tool("select")
            self.set_form_visible(False)
            self.clear_redactions()
        self._update_edit_actions()
        self.status_changed.emit()

    @property
    def editing(self) -> bool:
        return not self.edit_bar.isHidden()

    def set_pages_visible(self, on: bool) -> None:
        self.strip.setVisible(on)
        self.pages_button.setChecked(on)
        if on:
            self.strip.rebuild()
            self.strip.select_page(self.canvas.current_page)

    def apply(self, new_data: bytes, page: int | None = None) -> None:
        """Neuen Stand übernehmen (ein Rückgängig-Schritt) und anzeigen."""
        if self.data is None:
            return
        self._undo.append((self.data, self.canvas.current_page))
        del self._undo[:-MAX_UNDO]
        self._redo.clear()
        self._show_data(new_data, self.canvas.current_page if page is None else page)
        self._set_dirty(True)

    def _show_data(self, data: bytes, page: int) -> None:
        self.data = data
        selected = self.strip.selected_pages()
        self.canvas.selection = None
        self.canvas.current_highlight = None              # Suchtreffer gehören zum alten Stand
        self.search_index = -1
        self.doc.close()
        self._set_buffer(self._display_bytes(data))
        self._after_load()
        page = max(0, min(page, self.doc.pageCount() - 1))
        self.canvas.go_to(page)
        if not self.strip.isHidden() and selected:
            self.strip.select_page(page)
        self._results_changed() if self.search_field.text() else None

    def undo(self) -> None:
        if not self._undo or self.data is None:
            return
        data, page = self._undo.pop()
        self._redo.append((self.data, self.canvas.current_page))
        self._show_data(data, page)
        self._set_dirty(True)

    def redo(self) -> None:
        if not self._redo or self.data is None:
            return
        data, page = self._redo.pop()
        self._undo.append((self.data, self.canvas.current_page))
        self._show_data(data, page)
        self._set_dirty(True)

    def _run(self, action, *args, page: int | None = None) -> bool:
        """Kern-Funktion auf den aktuellen Stand anwenden; Fehler als Meldung statt Ausnahme."""
        if self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            return False
        try:
            result = action(self.data, *args)
        except pdfpages.PdfEditError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "PDF bearbeiten", str(error))
            return False
        except Exception as error:                      # noqa: BLE001 – kaputte PDFs dürfen den Tab nie mitreißen
            from notex.ui import dialogs
            dialogs.warn(self, "PDF bearbeiten", "Das ging bei diesem PDF schief.",
                         informative=f"{type(error).__name__}: {error}")
            return False
        self.apply(result, page)
        return True

    def target_pages(self) -> list[int]:
        """Seiten für Seiten-Befehle: Auswahl in der Seitenleiste, sonst die aktuelle Seite."""
        if not self.strip.isHidden() and self.strip.selected_pages():
            return self.strip.selected_pages()
        return [self.canvas.current_page]

    def save(self) -> bool:
        if not self._dirty:
            return True
        if self.redacted:
            return self.save_as()
        try:
            from notex.ui.pdf_edit import write_pdf
            note = write_pdf(self.path, self.data, backup=self.backup_to_trash and not self._backed_up)
        except OSError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "Speichern fehlgeschlagen", str(self.path), informative=str(error))
            return False
        self._backed_up = True
        self._set_dirty(False)
        self.saved.emit(self.path, self.path)
        if note:
            self.notice.emit(note)
        return True

    def save_as(self, target: Path | str | None = None) -> bool:
        if self.data is None:
            return False
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            suggestion = self.path.with_name(f"{self.path.stem}_geschwärzt.pdf") if self.redacted else self.path
            chosen, _ = QFileDialog.getSaveFileName(self, "PDF speichern unter", str(suggestion), "PDF (*.pdf)")
            if not chosen:
                return False
            target = chosen
        target = Path(target)
        if target.suffix.lower() != ".pdf":
            target = target.with_name(target.name + ".pdf")
        if self.redacted and target.resolve() == self.path.resolve():
            from notex.ui import dialogs
            if not dialogs.confirm(self, "Original überschreiben?",
                                   "Das Original wird durch die geschwärzte Fassung ersetzt.",
                                   informative="Empfehlung: unter neuem Namen speichern und das Original behalten.",
                                   yes="Überschreiben", danger=True):
                return False
        try:
            from notex.ui.pdf_edit import write_pdf
            write_pdf(target, self.data)
        except OSError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "Speichern fehlgeschlagen", str(target), informative=str(error))
            return False
        old = self.path
        self.path = target
        self.redacted = False
        self._backed_up = True
        self._set_dirty(False)
        self.saved.emit(old, target)
        return True

    # ---- Seiten organisieren ------------------------------------------------------------------------------------
    def rotate_pages(self, degrees: int, pages: list[int] | None = None) -> bool:
        return self._run(pdfpages.rotate, pages or self.target_pages(), degrees)

    def delete_pages(self, pages: list[int] | None = None) -> bool:
        pages = pages or self.target_pages()
        return self._run(pdfpages.delete, pages, page=min(pages))

    def reorder(self, order: list[int]) -> bool:
        current = self.canvas.current_page
        return self._run(pdfpages.rearrange, list(order), page=order.index(current) if current in order else 0)

    def move_pages(self, pages: list[int], before: int) -> bool:
        return self._run(pdfpages.move, pages, before)

    def extract_pages(self, pages: list[int] | None = None, target: Path | str | None = None) -> Path | None:
        if self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            return None
        pages = pages or self.target_pages()
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            label = pdfpages.describe(pages).replace("–", "-").replace(", ", "_")
            chosen, _ = QFileDialog.getSaveFileName(self, "Seiten als neues PDF speichern",
                                                    str(self.path.with_name(f"{self.path.stem}_S{label}.pdf")),
                                                    "PDF (*.pdf)")
            if not chosen:
                return None
            target = chosen
        target = Path(target)
        try:
            from notex.ui.pdf_edit import write_pdf
            write_pdf(target, pdfpages.extract(self.data, pages))
        except (pdfpages.PdfEditError, OSError) as error:
            from notex.ui import dialogs
            dialogs.warn(self, "Seiten herauslösen", str(error))
            return None
        self.notice.emit(f"Seiten {pdfpages.describe(pages)} → {target.name}")
        self.open_requested.emit(target)
        return target

    def insert_pdf(self, source: Path | str | None = None, before: int | None = None) -> bool:
        if source is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _ = QFileDialog.getOpenFileName(self, "PDF einfügen", str(self.path.parent), "PDF (*.pdf)")
            if not chosen:
                return False
            source = chosen
        if before is None:
            before = max(self.target_pages()) + 1
        try:
            other = Path(source).read_bytes()
        except OSError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "PDF einfügen", str(error))
            return False
        return self._run(pdfpages.insert, other, before, page=before)

    def split_pdf(self, groups: list[list[int]] | None = None, folder: Path | str | None = None) -> list[Path]:
        if self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            return []
        if groups is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import SplitDialog
            dialog = SplitDialog(self, self.doc.pageCount(), self.path.parent, self.path.stem)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            groups, folder = dialog.groups, dialog.folder
        folder = Path(folder or self.path.parent)
        try:
            parts = pdfpages.split(self.data, groups)
        except pdfpages.PdfEditError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "PDF aufteilen", str(error))
            return []
        from notex.core import fileops
        from notex.ui.pdf_edit import write_pdf
        written = []
        try:
            for name, part in zip(pdfpages.part_names(self.path.stem, len(parts)), parts):
                stem, suffix = name.rsplit(".", 1)
                target = fileops.unique_path(folder, stem, "." + suffix)
                write_pdf(target, part)
                written.append(target)
        except OSError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "PDF aufteilen", str(error))
        if written:
            self.notice.emit(f"{len(written)} PDF-Dateien in {folder.name or folder} erstellt")
        return written

    def _build_edit_bar(self) -> QFrame:
        bar_frame = QFrame()
        bar_frame.setObjectName("DataBar")
        row = QHBoxLayout(bar_frame)
        row.setContentsMargins(SPACING.md, 0, SPACING.md, SPACING.sm)
        row.setSpacing(SPACING.sm)
        self.pages_button = IconButton("layout-grid", "Seitenleiste ein/aus")
        self.pages_button.setCheckable(True)
        self.pages_button.toggled.connect(lambda on: self.set_pages_visible(on) if on == self.strip.isHidden() else None)
        buttons = [
            ("rotate-ccw", "Seite(n) nach links drehen", lambda: self.rotate_pages(-90)),
            ("rotate-cw", "Seite(n) nach rechts drehen", lambda: self.rotate_pages(90)),
            ("trash", "Seite(n) löschen  Entf (Seitenleiste)", lambda: self.delete_pages()),
            ("file-output", "Seite(n) als neues PDF herauslösen …", lambda: self.extract_pages()),
            ("file-plus", "PDF hinter der Seite einfügen …", lambda: self.insert_pdf()),
            ("scissors", "PDF aufteilen …", lambda: self.split_pdf()),
        ]
        row.addWidget(self.pages_button)
        self.page_actions = []
        for name, tip, slot in buttons:
            button = IconButton(name, tip)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.page_actions.append(button)
        self.tool_row = QHBoxLayout()               # Werkzeuge: Auswahl, Anmerkungen, Formulare, Schwärzen
        self.tool_row.setSpacing(SPACING.sm)
        row.addSpacing(SPACING.md)
        row.addLayout(self.tool_row)
        self.tool_buttons: dict[str, IconButton] = {}
        for name, icon_name, tip in TOOLS:
            button = IconButton(icon_name, tip)
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, n=name: self.set_tool(n))
            self.tool_row.addWidget(button)
            self.tool_buttons[name] = button
        self.tool_buttons["select"].setChecked(True)
        self.color_button = IconButton("palette", "Farbe für Markieren/Unterstreichen/Durchstreichen/Notiz")
        self.color_button.clicked.connect(self._color_menu)
        self.tool_row.addWidget(self.color_button)
        self.form_button = IconButton("clipboard-list", "Formular ausfüllen (Feldliste) ein/aus")
        self.form_button.setCheckable(True)
        self.form_button.toggled.connect(self.set_form_visible)
        self.tool_row.addWidget(self.form_button)
        flatten = IconButton("check-check", "Anmerkungen und Formular fest einbrennen …")
        flatten.clicked.connect(lambda: self.flatten())
        self.tool_row.addWidget(flatten)
        self.redact_apply = QPushButton("Schwärzen anwenden …")
        self.redact_apply.setObjectName("Danger")
        self.redact_apply.setToolTip("Vorgemerkte Bereiche endgültig schwärzen (Seiten werden als Bild neu erzeugt)")
        self.redact_apply.clicked.connect(lambda: self.apply_redactions())
        self.redact_apply.setVisible(False)
        self.tool_row.addWidget(self.redact_apply)
        row.addStretch(1)
        self.undo_button = IconButton("undo-2", "Rückgängig  Ctrl+Z")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = IconButton("redo-2", "Wiederholen  Ctrl+Y")
        self.redo_button.clicked.connect(self.redo)
        self.save_button = IconButton("save", "Speichern  Ctrl+S")
        self.save_button.clicked.connect(self.save)
        save_as = IconButton("file-down", "Speichern unter …  Ctrl+Shift+Alt+S")
        save_as.clicked.connect(lambda: self.save_as())
        for widget in (self.undo_button, self.redo_button, self.save_button, save_as):
            row.addWidget(widget)
        return bar_frame

    # ---- Anmerkungen ----------------------------------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        if tool not in self.tool_buttons:
            tool = "select"
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool)
        self.canvas.set_tool(tool)
        self.status_changed.emit()

    def _ensure_editing(self) -> bool:
        if not self.editing:
            self.set_editing(True)
        return self.editing

    def _color(self, kind: str) -> str:
        return self.tool_colors.get(kind) or pdfannot.DEFAULT_COLORS.get(kind, "#ffd400")

    def _color_menu(self) -> None:
        from notex.ui.pdf_edit import MARK_COLORS
        tool = self.canvas.tool if self.canvas.tool in ("highlight", "underline", "strikeout", "note") else "highlight"
        menu = style_menu(QMenu(self))
        for label, value in MARK_COLORS:
            action = menu.addAction(label, lambda v=value, t=tool: self.tool_colors.__setitem__(t, v))
            action.setCheckable(True)
            action.setChecked(self._color(tool) == value)
        menu.addSeparator()
        menu.addAction("Standardfarbe", lambda t=tool: self.tool_colors.pop(t, None))
        menu.exec(self.color_button.mapToGlobal(self.color_button.rect().bottomLeft()))

    def add_markup(self, kind: str, page: int | None = None, rects: list | None = None) -> bool:
        """Markieren/Unterstreichen/Durchstreichen – ohne Angaben: die aktuelle Textauswahl."""
        if rects is None:
            rects, page = self.canvas.selection_rects(), self.canvas.selection_page
        if not rects or page is None or page < 0:
            self.notice.emit("Erst Text im PDF markieren")
            return False
        if not self._ensure_editing():
            return False
        done = self._run(pdfannot.add_markup, page, kind, list(rects), self._color(kind))
        if done:
            self.canvas.selection = None
            self.canvas.selection_changed.emit()
        return done

    def add_note(self, page: int, x: float, y: float, text: str | None = None) -> bool:
        if text is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import TextDialog
            dialog = TextDialog(self, "Notiz")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            text = dialog.values()[0]
        if not text.strip() or not self._ensure_editing():
            return False
        return self._run(pdfannot.add_note, page, x, y, text, self._color("note"))

    def add_text(self, page: int, rect: tuple, text: str | None = None, size: float = 12.0,
                 color: str | None = None, border: bool = False) -> bool:
        """Text direkt auf die Seite; `rect` in Ansichts-Punkten (Breite 0 = Standardbreite 220 pt)."""
        if text is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import TextDialog
            dialog = TextDialog(self, "Text auf der Seite", with_style=True, color=color or "#1a1a1a")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            text, size, color, border = dialog.values()
        if not text.strip() or not self._ensure_editing():
            return False
        x0, y0, x1, y1 = rect
        if x1 - x0 < 20:
            x1 = x0 + 220
        return self._run(pdfannot.add_text, page, (x0, y0, x1, max(y1, y0 + 1)), text, size,
                         color or pdfannot.DEFAULT_COLORS["text"], border)

    def annotations(self, page: int) -> list:
        if self.data is None:
            return []
        try:
            return pdfannot.list_annotations(self.data, page)
        except pdfpages.PdfEditError:
            return []

    def delete_annotation(self, page: int, index: int) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfannot.delete_annotation, page, index)

    def edit_annotation(self, page: int, index: int, text: str | None = None) -> bool:
        info = next((a for a in self.annotations(page) if a.index == index), None)
        if info is None:
            return False
        if text is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import TextDialog
            dialog = TextDialog(self, info.label + (" – Text" if info.kind == "text" else " – Kommentar"),
                                info.contents)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            text = dialog.values()[0]
        if not self._ensure_editing():
            return False
        return self._run(pdfannot.update_text, page, index, text)

    def _on_markup(self) -> None:
        tool = self.canvas.tool
        if tool in ("highlight", "underline", "strikeout"):
            self.add_markup(tool)
        elif tool == "redact_text" and hasattr(self, "mark_redaction_from_selection"):
            self.mark_redaction_from_selection()

    def _on_region(self, page: int, rect: QRectF) -> None:
        tool = self.canvas.tool
        box = (rect.left(), rect.top(), rect.right(), rect.bottom())
        if tool == "note":
            self.add_note(page, rect.left(), rect.top())
        elif tool == "text":
            self.add_text(page, box)
        elif hasattr(self, f"_region_{tool}"):
            getattr(self, f"_region_{tool}")(page, box)

    def _extend_menu(self, menu, page: int, x: float, y: float) -> None:
        if self.edit_block_reason():
            return
        pending = next((i for i, (p, (x0, y0, x1, y1)) in enumerate(self.redactions)
                        if p == page and x0 <= x <= x1 and y0 <= y <= y1), None)
        if pending is not None:
            menu.addSeparator()
            menu.addAction("Vorgemerkte Schwärzung entfernen", lambda: self.remove_redaction(pending))
            menu.addAction("Alle Vormerkungen verwerfen", self.clear_redactions)
            return
        if self.canvas.selected_text():
            menu.addSeparator()
            for kind, label in (("highlight", "Markieren"), ("underline", "Unterstreichen"),
                                ("strikeout", "Durchstreichen")):
                menu.addAction(label, lambda k=kind: self.add_markup(k))
            menu.addAction("Markierung schwärzen (vormerken)", self.mark_redaction_from_selection)
        term = self.search_field.text().strip()
        if term:
            menu.addAction(f"Jedes „{term[:30]}“ schwärzen (vormerken)", lambda: self.mark_search_results(term))
        field = next((f for f in self.fields if f.page == page and f.rect[0] - 2 <= x <= f.rect[2] + 2
                      and f.rect[1] - 2 <= y <= f.rect[3] + 2), None)
        if field is not None:
            menu.addSeparator()
            obj = self.objects.at(page, x, y)
            if obj is not None and obj.kind == "field":
                menu.addAction(f"Feld „{field.name}“ ausfüllen", lambda: self.objects.activate_field(obj))
                menu.addAction("Feld-Eigenschaften …", lambda: self.field_properties(obj))
            menu.addAction(f"Feld „{field.name}“ löschen", lambda: self.remove_field(field.name))
            return
        info = pdfannot.hit(self.annotations(page), x, y)
        if info is not None:
            menu.addSeparator()
            obj = next((o for o in self.objects.objects(page) if o.index == info.index), None)
            if info.kind == "text" and obj is not None:
                menu.addAction("Text: bearbeiten (Text, Größe, Farbe, Rahmen) …", lambda: self.edit_text_object(obj))
            elif info.kind == "stamp" and obj is not None and obj.kind in ("signature", "diagram"):
                verb = "Unterschrift ersetzen …" if obj.kind == "signature" else "Diagramm bearbeiten …"
                menu.addAction(verb, lambda: self.edit_object(obj))
            elif info.text_editable:
                menu.addAction(f"{info.label}: Kommentar bearbeiten …", lambda: self.edit_annotation(page, info.index))
            menu.addAction(f"{info.label} löschen", lambda: self.delete_annotation(page, info.index))
        elif self.editing:
            menu.addSeparator()
            menu.addAction("Notiz hier …", lambda: self.add_note(page, x, y))
            menu.addAction("Text hier …", lambda: self.add_text(page, (x, y, x, y)))

    # ---- Eingefügtes anfassen (Objekte) ------------------------------------------------------------------------
    def set_object_rect(self, obj, rect: tuple) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfobjects.set_rect, obj.page, obj.index, tuple(rect), page=obj.page)

    def delete_object(self, obj) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfobjects.delete_object, obj.page, obj.index, page=obj.page)

    def delete_selected(self) -> bool:
        obj = self.objects.selected
        if obj is None:
            self.notice.emit("Erst ein Objekt anklicken (Werkzeug „Auswählen“)")
            return False
        self.objects.select(None)
        return self.delete_object(obj)

    def edit_selected(self) -> bool:
        obj = self.objects.selected
        if obj is None:
            self.notice.emit("Erst ein Objekt anklicken (Werkzeug „Auswählen“)")
            return False
        if obj.kind == "field":
            self.objects.activate_field(obj)
            return True
        return self.edit_object(obj)

    def edit_object(self, obj) -> bool:
        """Doppelklick/„Bearbeiten …“: je nach Art Text mit Stil, Notiz/Kommentar, Unterschrift ersetzen, Feld."""
        if obj.kind == "text":
            return self.edit_text_object(obj)
        if obj.kind == "signature":
            return self.replace_signature(obj)
        if obj.kind == "diagram" and hasattr(self, "edit_diagram"):
            return self.edit_diagram(obj)
        if obj.kind == "field":
            return self.field_properties(obj)
        return self.edit_annotation(obj.page, obj.index)

    def edit_text_object(self, obj, text: str | None = None, size: float | None = None, color: str | None = None,
                         border: bool | None = None) -> bool:
        if text is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import TextDialog
            reader_annot = pdfannot.open_reader(self.data).pages[obj.page]["/Annots"][obj.index].get_object()
            old_size, old_color, old_border = pdfannot.freetext_style(reader_annot)
            dialog = TextDialog(self, "Text auf der Seite bearbeiten", obj.contents, with_style=True,
                                size=old_size, color=old_color, border=old_border)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            text, size, color, border = dialog.values()
        if not self._ensure_editing():
            return False
        return self._run(pdfannot.update_text, obj.page, obj.index, text, size, color, border, page=obj.page)

    def replace_signature(self, obj, value=None) -> bool:
        if value is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import SignatureDialog
            dialog = SignatureDialog(self, self.path.parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            value = dialog.result_value()
        if not self._ensure_editing():
            return False
        page, rect = obj.page, obj.rect
        if value[0] == "image":
            from notex.ui.pdf_edit import image_to_bytes
            image = value[1]
            target = pdfforms.fit_rect(rect, image.width() / max(1, image.height()))
            width, height, rgb, alpha = image_to_bytes(image)
            return self._run(lambda d: pdfforms.add_image(pdfannot.delete_annotation(d, page, obj.index), page,
                                                          target, width, height, rgb, alpha), page=page)
        _kind, strokes, aspect = value
        target = pdfforms.fit_rect(rect, aspect)
        return self._run(lambda d: pdfforms.add_strokes(pdfannot.delete_annotation(d, page, obj.index), page,
                                                        target, strokes), page=page)

    def field_properties(self, obj, new_name: str | None = None, multiline: bool | None = None,
                         size: float | None = None) -> bool:
        info = next((f for f in self.fields if f.name == obj.field), None)
        if info is None:
            return False
        if new_name is None and multiline is None and size is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import FieldDialog
            dialog = FieldDialog(self, info)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            new_name, multiline, size = dialog.values()
        if not self._ensure_editing():
            return False
        return self._run(pdfforms.update_field, info.name, new_name, multiline, size, page=obj.page)

    # ---- Formulare und Unterschrift ---------------------------------------------------------------------------
    def refresh_form(self) -> None:
        try:
            self.fields = pdfforms.list_fields(self.data) if self.data is not None and not self.encrypted else []
        except Exception:                                  # noqa: BLE001
            self.fields = []
        if not self.form_panel.isHidden():
            self.form_panel.set_fields(self.fields)

    def set_form_visible(self, on: bool) -> None:
        if on and not self._ensure_editing():
            on = False
        self.form_button.blockSignals(True)
        self.form_button.setChecked(on)
        self.form_button.blockSignals(False)
        self.form_panel.setVisible(on)
        if on:
            self.form_panel.set_fields(self.fields)

    def show_field(self, info) -> None:
        x0, y0, x1, y1 = info.rect
        self.canvas.go_to(info.page, QPointF(x0, y0))
        self.canvas.overlays = [o for o in self.canvas.overlays if o[2] != "field"] + \
            [(info.page, QRectF(x0, y0, x1 - x0, y1 - y0), "field")]
        self.canvas.viewport().update()
        QTimer.singleShot(1500, self._clear_field_overlay)

    def _clear_field_overlay(self) -> None:
        self.canvas.overlays = [o for o in self.canvas.overlays if o[2] != "field"]
        self.canvas.viewport().update()

    def fill_form(self, values: dict) -> bool:
        if not values:
            self.notice.emit("Nichts geändert")
            return False
        if not self._ensure_editing():
            return False
        return self._run(pdfforms.fill, values)

    def add_field(self, page: int, rect: tuple, name: str | None = None, multiline: bool | None = None) -> bool:
        if name is None:
            from notex.ui import dialogs
            name = dialogs.ask_text(self, "Neues Textfeld", "Feldname:", self._free_name("Feld"))
            if not name:
                return False
        if multiline is None:
            multiline = rect[3] - rect[1] > 34
        if not self._ensure_editing():
            return False
        return self._run(pdfforms.add_text_field, page, rect, name, "", multiline)

    def add_checkbox(self, page: int, rect: tuple, name: str | None = None) -> bool:
        if name is None:
            from notex.ui import dialogs
            name = dialogs.ask_text(self, "Neues Kontrollkästchen", "Feldname:", self._free_name("Kästchen"))
            if not name:
                return False
        if not self._ensure_editing():
            return False
        return self._run(pdfforms.add_checkbox, page, rect, name)

    def remove_field(self, name: str) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfforms.remove_field, name)

    def _free_name(self, stem: str) -> str:
        names = {f.name for f in self.fields}
        number = 1
        while f"{stem}{number}" in names:
            number += 1
        return f"{stem}{number}"

    def add_signature(self, page: int, rect: tuple, value=None) -> bool:
        """`value`: ("strokes", Striche 0..1, Seitenverhältnis) oder ("image", QImage); None = Dialog."""
        if value is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import SignatureDialog
            dialog = SignatureDialog(self, self.path.parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            value = dialog.result_value()
        if not self._ensure_editing():
            return False
        if value[0] == "image":
            from notex.ui.pdf_edit import image_to_bytes
            image = value[1]
            target = pdfforms.fit_rect(rect, image.width() / max(1, image.height()))
            width, height, rgb, alpha = image_to_bytes(image)
            return self._run(pdfforms.add_image, page, target, width, height, rgb, alpha)
        _kind, strokes, aspect = value
        return self._run(pdfforms.add_strokes, page, pdfforms.fit_rect(rect, aspect), strokes)

    def flatten(self, confirm: bool = True) -> bool:
        if confirm:
            from notex.ui import dialogs
            if not dialogs.confirm(self, "Fest einbrennen",
                                   "Alle Anmerkungen, Unterschriften und Formularfelder werden Teil der Seiten.",
                                   informative="Danach lassen sie sich nicht mehr ändern oder ausfüllen "
                                               "(Ctrl+Z geht bis zum Schließen). Sinnvoll vor dem Verschicken.",
                                   yes="Einbrennen"):
                return False
        if not self._ensure_editing():
            return False
        return self._run(pdfforms.flatten)

    def _region_field(self, page: int, box: tuple) -> None:
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            box = (box[0], box[1], box[0] + 180, box[1] + 20)
        self.add_field(page, box)

    def _region_checkbox(self, page: int, box: tuple) -> None:
        self.add_checkbox(page, box)

    def _region_signature(self, page: int, box: tuple) -> None:
        self.add_signature(page, box)

    # ---- Schwärzen ---------------------------------------------------------------------------------------------
    def mark_redaction(self, page: int, rect: tuple, term: str = "") -> None:
        if not self._ensure_editing():
            return
        x0, y0, x1, y1 = rect
        if x1 - x0 < 1 or y1 - y0 < 1:
            return
        self.redactions.append((page, (x0, y0, x1, y1)))
        if term.strip():
            self.redact_terms.append(term.strip())
        self._sync_redactions()

    def mark_redaction_from_selection(self) -> bool:
        rects, page = self.canvas.selection_rects(), self.canvas.selection_page
        if not rects or page < 0:
            self.notice.emit("Erst Text markieren")
            return False
        term = self.canvas.selected_text()
        for rect in rects:
            self.mark_redaction(page, rect)
        if term.strip():
            self.redact_terms.append(" ".join(term.split()))
        self.canvas.selection = None
        self.canvas.selection_changed.emit()
        self._sync_redactions()
        return True

    def find_text(self, term: str) -> list[tuple[int, tuple]]:
        """Alle Vorkommen von `term` (Groß/klein egal) mit Rechtecken – synchron über alle Seiten.

        Bewusst nicht über QPdfSearchModel: das liefert Treffer häppchenweise im Hintergrund, und auf langsamen
        Rechnern fehlten beim Schwärzen sonst Treffer auf späteren Seiten."""
        import re
        out: list[tuple[int, tuple]] = []
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        for page in range(self.doc.pageCount()):
            text = self.doc.getAllText(page).text()
            for match in pattern.finditer(text):
                selection = self.doc.getSelectionAtIndex(page, match.start(), len(match.group()))
                if selection.text().casefold() != match.group().casefold():
                    continue                              # Index passt nicht (seltene Sonderzeichen) – Restprüfung meldet es
                for polygon in selection.bounds():
                    rect = polygon.boundingRect()
                    out.append((page, (rect.left(), rect.top(), rect.right(), rect.bottom())))
        return out

    def mark_search_results(self, term: str | None = None) -> int:
        """Jedes Vorkommen des Suchbegriffs zum Schwärzen vormerken (z. B. einen Namen im ganzen Dokument)."""
        term = (self.search_field.text() if term is None else term).strip()
        if not term:
            self.notice.emit("Erst im PDF suchen – dann werden alle Treffer vorgemerkt")
            return 0
        hits = self.find_text(term)
        if not hits:
            self.notice.emit(f"„{term}“ kommt im PDF nicht vor")
            return 0
        if not self._ensure_editing():
            return 0
        self.redactions.extend(hits)
        self.redact_terms.append(term)
        self._sync_redactions()
        pages = len({page for page, _r in hits})
        self.notice.emit(f"„{term}“: {len(hits)} Stelle(n) auf {pages} Seite(n) zum Schwärzen vorgemerkt")
        return len(hits)

    def remove_redaction(self, index: int) -> None:
        if 0 <= index < len(self.redactions):
            del self.redactions[index]
            self._sync_redactions()

    def clear_redactions(self) -> None:
        self.redactions.clear()
        self.redact_terms.clear()
        self._sync_redactions()

    def _sync_redactions(self) -> None:
        self.canvas.overlays = [o for o in self.canvas.overlays if o[2] != "redact"] + \
            [(page, QRectF(x0, y0, x1 - x0, y1 - y0), "redact") for page, (x0, y0, x1, y1) in self.redactions]
        self.redact_apply.setVisible(bool(self.redactions))
        self.redact_apply.setText(f"Schwärzen anwenden ({len(self.redactions)}) …")
        self.canvas.viewport().update()
        self.status_changed.emit()

    def _region_redact(self, page: int, box: tuple) -> None:
        self.mark_redaction(page, box)

    def apply_redactions(self, dpi: int | None = None, options=None, confirm: bool = True) -> bool:
        """Vorgemerkte Balken endgültig anwenden: betroffene Seiten als Bild neu, Rest bereinigen, danach prüfen."""
        if not self.redactions:
            self.notice.emit("Nichts zum Schwärzen vorgemerkt")
            return False
        if self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            return False
        boxes = pdfredact.normalize_boxes(self.redactions)
        if confirm:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import RedactDialog
            dialog = RedactDialog(self, sum(len(v) for v in boxes.values()), sorted(boxes))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            dpi, options = dialog.dpi.currentData(), dialog.options()
        dpi = dpi or 200
        options = options or pdfredact.RedactOptions()
        from PySide6.QtCore import Qt as _Qt
        from notex.ui.pdf_edit import render_redacted
        QApplication.setOverrideCursor(_Qt.CursorShape.WaitCursor)
        try:
            images = {page: render_redacted(self.doc, page, rects, dpi) for page, rects in boxes.items()}
        finally:
            QApplication.restoreOverrideCursor()
        if not self._run(pdfredact.redact_pages, images, options):
            return False
        terms = list(dict.fromkeys(self.redact_terms))
        self.redactions.clear()
        self.redact_terms.clear()
        self._sync_redactions()
        self.redacted = True
        found = pdfredact.leftovers(self.data, terms) if terms else []
        if found and not options.strip_outline and any("Lesezeichen" in f for f in found) and confirm:
            from notex.ui import dialogs
            if dialogs.confirm(self, "Geschwärzte Wörter in Lesezeichen",
                               "Die Lesezeichen enthalten noch geschwärzte Wörter. Lesezeichen entfernen?",
                               yes="Lesezeichen entfernen"):
                self._run(_strip_outline)
                found = pdfredact.leftovers(self.data, terms)
        self.last_leftovers = found
        if found and confirm:
            from notex.ui import dialogs
            dialogs.warn(self, "Noch nicht überall geschwärzt",
                         "Diese Stellen enthalten die markierten Wörter weiterhin:",
                         informative="\n".join(found[:12]) + ("\n…" if len(found) > 12 else ""))
        else:
            self.notice.emit(f"Geschwärzt: {len(images)} Seite(n) neu erzeugt – Speichern legt eine neue Datei an")
        return True

    def _update_edit_actions(self) -> None:
        if not hasattr(self, "undo_button"):
            return
        self.undo_button.setEnabled(bool(self._undo))
        self.redo_button.setEnabled(bool(self._redo))
        self.save_button.setEnabled(self._dirty)

    def _strip_menu(self, pos) -> None:
        pages = self.strip.selected_pages() or [self.canvas.current_page]
        label = pdfpages.describe(pages)
        menu = style_menu(QMenu(self))
        menu.addAction(f"Nach links drehen ({label})", lambda: self.rotate_pages(-90, pages))
        menu.addAction(f"Nach rechts drehen ({label})", lambda: self.rotate_pages(90, pages))
        menu.addSeparator()
        menu.addAction("An den Anfang", lambda: self.move_pages(pages, 0))
        menu.addAction("Ans Ende", lambda: self.move_pages(pages, self.doc.pageCount()))
        menu.addSeparator()
        menu.addAction(f"Als neues PDF herauslösen … ({label})", lambda: self.extract_pages(pages))
        menu.addAction("PDF dahinter einfügen …", lambda: self.insert_pdf(before=max(pages) + 1))
        menu.addSeparator()
        delete = menu.addAction(f"Löschen ({label})", lambda: self.delete_pages(pages))
        delete.setEnabled(len(pages) < self.doc.pageCount())
        menu.exec(self.strip.viewport().mapToGlobal(pos))

    # ---- Seiten, Zoom, Lesezeichen ------------------------------------------------------------------
    def page_label(self, index: int) -> str:
        label = self.doc.pageLabel(index) if 0 <= index < self.doc.pageCount() else ""
        return label or str(index + 1)

    def _on_page(self, index: int) -> None:
        self.page_field.setText(self.page_label(index))
        if not self.strip.isHidden():
            self.strip.select_page(index)
        self.status_changed.emit()

    def _on_zoom(self) -> None:
        """Auswahlfeld nachziehen (Ctrl+Mausrad, Ctrl+Plus/Minus); freie Stufen als eigener Eintrag am Ende."""
        mode, zoom = self.canvas.zoom_mode, round(self.canvas.zoom, 2)
        index = next((i for i in range(self.zoom_box.count())
                      if self.zoom_box.itemData(i)[0] == mode and (mode != "custom" or self.zoom_box.itemData(i)[1] == zoom)), -1)
        if index < 0:
            if self.zoom_box.itemData(self.zoom_box.count() - 1)[0] == "free":
                self.zoom_box.removeItem(self.zoom_box.count() - 1)
            self.zoom_box.addItem(f"{self.canvas.zoom_percent()} %", ("free", zoom))
            index = self.zoom_box.count() - 1
        self.zoom_box.setCurrentIndex(index)
        self.status_changed.emit()

    def _zoom_chosen(self, index: int) -> None:
        mode, zoom = self.zoom_box.itemData(index)
        self.canvas.set_zoom("custom" if mode == "free" else mode, zoom)

    def _focus_page(self) -> None:
        self.page_field.setFocus()
        self.page_field.selectAll()

    def _jump_page(self) -> None:
        text = self.page_field.text().strip()
        index = self.doc.pageIndexForLabel(text) if text else -1
        if index < 0 and text.isdigit():
            index = int(text) - 1
        if 0 <= index < self.doc.pageCount():
            self.canvas.go_to(index)
            self.canvas.setFocus()
        else:
            self.page_field.setText(self.page_label(self.canvas.current_page))

    def _bookmark_clicked(self, index) -> None:
        page = index.data(int(QPdfBookmarkModel.Role.Page))
        location = index.data(int(QPdfBookmarkModel.Role.Location))
        if isinstance(page, int):
            self.canvas.go_to(page, location if isinstance(location, QPointF) else None)

    def zoom_step(self, direction: int) -> None:
        self.canvas.zoom_step(direction)

    # ---- Suche ------------------------------------------------------------------------------------
    def focus_search(self) -> None:
        self.search_field.setFocus()
        self.search_field.selectAll()

    def _run_search(self) -> None:
        self.search_index = -1
        self.search.setSearchString(self.search_field.text())
        self._results_changed()

    def _results_changed(self) -> None:
        count = self.search.rowCount(QModelIndex()) if self.search_field.text() else 0
        highlights: dict[int, list] = {}
        for i in range(min(count, 2000)):
            link = self.search.resultAtIndex(i)
            highlights.setdefault(link.page(), []).extend(link.rectangles())
        self.canvas.highlights = list(highlights.items())
        if not self.search_field.text():
            self.hits_label.setText("")
        elif 0 <= self.search_index < count:
            self.hits_label.setText(f"{self.search_index + 1} / {count}")
        else:
            self.hits_label.setText(f"{count} Treffer" if count else "Keine Treffer")
        if count and self.search_index < 0:
            self.next_result(1)
        self.canvas.viewport().update()

    def next_result(self, direction: int) -> None:
        count = self.search.rowCount(QModelIndex()) if self.search_field.text() else 0
        if not count:
            return
        self.search_index = (self.search_index + direction) % count
        link = self.search.resultAtIndex(self.search_index)
        self.canvas.current_highlight = (link.page(), link.rectangles())
        self.canvas.go_to(link.page(), link.location())
        self.hits_label.setText(f"{self.search_index + 1} / {count}")
        self.canvas.viewport().update()

    # ---- Zitat ------------------------------------------------------------------------------------
    def _on_selection(self) -> None:
        self.quote_button.setEnabled(bool(self.canvas.selected_text()))
        self.status_changed.emit()

    def quote(self) -> None:
        text = self.canvas.selected_text()
        if not text:
            return
        markdown = pdfdoc.quote_markdown(text, self.path.name, self.page_label(self.canvas.selection_page))
        if markdown:
            self.quote_requested.emit(markdown)

    # ---- ViewerPage -----------------------------------------------------------------------------------
    def status_parts(self) -> list[str]:
        if self.error:
            return [self.error, human_size(self.path.stat().st_size) if self.path.exists() else "", "PDF-Dokument", ""]
        pages = self.doc.pageCount()
        selected = len(self.canvas.selected_text())
        return [f"Seite {self.page_label(self.canvas.current_page)} ({self.canvas.current_page + 1} / {pages})",
                human_size(self.path.stat().st_size) if self.path.exists() else "",
                f"{selected} Zeichen markiert" if selected else self._mode_text(),
                f"{self.canvas.zoom_percent()} %"]

    def _mode_text(self) -> str:
        if self._dirty:
            return "PDF · geändert (Ctrl+S speichert)"
        form = f" · Formular ({len(self.fields)} Felder)" if self.fields else ""
        return ("PDF · bearbeiten" if self.editing else "PDF · nur lesen") + form

    def reload(self) -> None:
        if self._dirty:                         # extern geändert, hier ungespeichert: eigene Fassung behalten
            self.notice.emit(f"„{self.path.name}“ wurde extern geändert – deine ungespeicherten Änderungen bleiben")
            return
        page = self.canvas.current_page
        self._undo.clear()
        self._redo.clear()
        self._load()
        self.canvas.go_to(page)
        self._update_edit_actions()

    def retheme(self) -> None:
        self.canvas.viewport().update()

    def shutdown(self) -> None:
        self.doc.close()
