"""PDF-Tab: Scrollen, Zoom, Seitensprung, Textsuche, Lesezeichen, Text markieren → Zitat in die Notiz – und im
Bearbeiten-Modus (Stift) Seiten organisieren (Seitenleiste mit Miniaturen).

Eigene Seitenansicht auf QPdfDocument statt QPdfView, weil QPdfView keine Textauswahl kann. Seiten werden in einem
Hintergrund-Renderer gerastert (QPdfPageRenderer, mehrere Threads) und zwischengespeichert – die Oberfläche wartet
nie auf eine Seite. Es gibt keine Formular-, Link- oder Skript-Ausführung. Die Datei wird in den Speicher gelesen und
sofort geschlossen, damit sie umbenannt/verschoben werden kann. Bearbeitet wird immer eine Kopie im Speicher
(Bytes, jede Änderung ein Rückgängig-Schritt); erst Ctrl+S schreibt die Datei.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtPdf import (QPdfBookmarkModel, QPdfDocument, QPdfDocumentRenderOptions, QPdfPageRenderer,
                           QPdfSearchModel)
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMenu, QPushButton, QSplitter, QTreeView, QVBoxLayout, QWidget)

from notex import APP_NAME
from notex.core import (pdfannot, pdfattach, pdfcomments, pdfdetect, pdfdiagram, pdfdoc, pdfdraw, pdffieldtypes,
                        pdfformdata, pdfforms, pdfimages, pdflinks, pdfobjects, pdfoutline, pdfpages, pdfredact, pdfsecurity,
                        pdfstamps, redact_patterns, xfdf)
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
    ("diagram", "network", "Diagramm (UML, Fluss …): Bereich aufziehen, dann zeichnen – später per Doppelklick ändern"),
    ("redact", "redact", "Schwärzen: Bereiche aufziehen (endgültig erst mit „Schwärzen anwenden“)"),
    ("crop", "crop", "Zuschneiden: sichtbaren Bereich aufziehen (blendet nur aus, kein Schwärzen)"),
    ("link", "link", "Link: Bereich aufziehen, dann Ziel wählen (Seite, Lesezeichen, Webadresse, anderes PDF)"),
    ("ink", "pen-line", "Stift: frei zeichnen (Farbe, Stärke, Deckkraft über „Stil“)"),
    ("eraser", "eraser", "Radierer: über Freihand-Striche fahren – entfernt immer ganze Striche"),
    ("shape", "shapes", "Formen: Rechteck, Ellipse, Linie, Pfeil, Polygon, Wolke"),
    ("stamp", "stamp", "Stempel: Genehmigt, Entwurf, Vertraulich, Erledigt, Geprüft oder eigener"),
    ("formfield", "list-plus", "Weitere Formularfelder: Optionsfeld-Gruppe, Dropdown, Listenfeld, Schaltfläche"),
    ("image", "image-plus", "Bild einfügen: Bereich aufziehen oder klicken, dann PNG/JPG/WebP wählen "
                            "(Shift beim Aufziehen: Rahmen ganz füllen)"),
    ("pickimage", "image", "Bilder im Seiteninhalt: anklicken, dann Rechtsklick – exportieren, ersetzen, zuschneiden, "
                           "löschen (Entf)"),
]
FIELD_TOOLS = ("radio", "combo", "listbox", "button")
FIELD_LABELS = [("radio", "circle-dot", "Optionsfeld-Gruppe (Bereich aufziehen)"),
                ("combo", "chevron-down", "Dropdown (Bereich aufziehen)"),
                ("listbox", "list", "Listenfeld (Bereich aufziehen)"),
                ("button", "square", "Schaltfläche: zurücksetzen oder Seite anspringen")]
SHAPE_LABELS = [("rect", "square", "Rechteck"), ("ellipse", "circle", "Ellipse"), ("line", "minus", "Linie"),
                ("arrow", "move-up-right", "Pfeil"), ("polygon", "pentagon", "Polygon (Klicks, Doppelklick/Enter beendet)"),
                ("cloud", "cloud", "Wolke")]
CACHE_IMAGES = 24
ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0]
IN_MEMORY_LIMIT = 256 * 1024 * 1024
PAGE_WHITE = "#ffffff"      # PDF-Seiten sind immer weiß gestaltet – unabhängig vom Blatt-Theme


def _attachment_temp_dir() -> Path:
    """Frischer Ordner unter cache/anhaenge für einen geöffneten Anhang; Ordner älter als ein Tag werden entfernt."""
    import shutil
    import time
    import uuid
    from notex.paths import cache_dir
    base = cache_dir() / "anhaenge"
    base.mkdir(parents=True, exist_ok=True)
    for old in base.iterdir():
        try:
            if old.is_dir() and time.time() - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            continue
    folder = base / uuid.uuid4().hex[:12]
    folder.mkdir()
    return folder


def _alpha(color: str, alpha: int) -> QColor:
    result = QColor(color)
    result.setAlpha(alpha)
    return result


REGION_TOOLS = {"note", "text", "field", "checkbox", "signature", "redact", "diagram", "crop", "link", "rect",
                "ellipse", "cloud", "stamp", "radio", "combo", "listbox", "button", "image", "pickimage",
                "imagecrop"}                                                        # Ziehen/Klicken = Bereich
PATH_TOOLS = {"ink", "eraser", "line", "arrow", "polygon"}   # Ziehen zeichnet einen Pfad; Polygon: Klicks, Doppelklick
SHAPE_TOOLS = ("rect", "ellipse", "line", "arrow", "polygon", "cloud")
MARKUP_TOOLS = {"highlight", "underline", "strikeout", "redact_text"}          # Textauswahl → sofort anwenden


class _PdfCanvas(QAbstractScrollArea):
    page_changed = Signal(int)
    selection_changed = Signal()
    zoom_changed = Signal()
    quote_requested = Signal()
    region_chosen = Signal(int, QRectF)       # Seite, Bereich in Ansichts-Punkten (Klick = Breite/Höhe 0)
    markup_chosen = Signal()                  # Textauswahl im Markier-Werkzeug fertig
    tool_cancelled = Signal()                 # Esc: zurück zum Auswahl-Werkzeug
    path_chosen = Signal(int, list)           # Seite, Punkte in Ansichts-Punkten (Stift, Radierer, Linie, Polygon)

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
        self.image_overlay = None                          # Seite → [(Rechteck, ausgewählt)] Bilder im Inhalt
        self._band: tuple[int, float, float, float, float] | None = None
        self._path: tuple[int, list] | None = None       # laufender Pfad (Seite, Punkte)
        self._hover: tuple[float, float] | None = None    # Polygon: Mausposition für die Gummiband-Linie
        self.path_color = "#d03030"
        self.path_width = 2.0
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
            if self.image_overlay is not None:
                for (x0, y0, x1, y1), chosen in self.image_overlay(page.index):
                    view = QRectF(target.x() + x0 * scale, target.y() + y0 * scale, (x1 - x0) * scale,
                                  (y1 - y0) * scale)
                    painter.setBrush(_alpha(COLORS.accent, 45) if chosen else Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(COLORS.accent), 2 if chosen else 1,
                                        Qt.PenStyle.DashLine if chosen else Qt.PenStyle.DotLine))
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
            if self._path is not None and self._path[0] == page.index:
                self._paint_path(painter, target, scale)
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

    def _paint_path(self, painter, target: QRectF, scale: float) -> None:
        """Vorschau des laufenden Pfads: Stift in Stiftfarbe, Radierer grau und breit, Polygon mit Gummiband."""
        _page, points = self._path
        mapped = [QPointF(target.x() + x * scale, target.y() + y * scale) for x, y in points]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.tool == "eraser":
            pen = QPen(QColor(120, 120, 120, 110), 8 * scale)
        else:
            pen = QPen(QColor(self.path_color), max(1.0, self.path_width * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if len(mapped) == 1 and self.tool != "polygon":
            painter.drawPoint(mapped[0])
        else:
            painter.drawPolyline(QPolygonF(mapped))
        if self.tool == "polygon" and self._hover is not None and mapped:
            hover = QPointF(target.x() + self._hover[0] * scale, target.y() + self._hover[1] * scale)
            painter.setPen(QPen(QColor(self.path_color), 1, Qt.PenStyle.DashLine))
            painter.drawLine(mapped[-1], hover)
            if len(mapped) > 1:
                painter.drawLine(hover, mapped[0])
        painter.restore()

    def finish_polygon(self) -> None:
        if self._path is None:
            return
        page, points = self._path
        self._path = self._hover = None
        self.viewport().update()
        self.path_chosen.emit(page, list(points))

    # ---- Auswahl ----------------------------------------------------------------------------------
    def _doc_pos(self, event) -> tuple[float, float]:
        return (event.position().x() + self.horizontalScrollBar().value(),
                event.position().y() + self.verticalScrollBar().value())

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self._band = None
        self._path = self._hover = None
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if tool in REGION_TOOLS | PATH_TOOLS
                                  else Qt.CursorShape.IBeamCursor)
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
        if event.button() == Qt.MouseButton.LeftButton and self.layout_ is not None and self.tool in PATH_TOOLS:
            hit = self.layout_.hit(*self._doc_pos(event))
            if hit is not None:
                page, x, y = hit
                if self.tool == "polygon" and self._path is not None and self._path[0] == page:
                    self._path[1].append((x, y))
                else:
                    self._path = (page, [(x, y)])
                self._hover = (x, y)
                self.viewport().update()
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
        if self._path is not None and self.layout_ is not None:
            page, points = self._path
            point = self.layout_.clamp_hit(page, *self._doc_pos(event))
            if self.tool == "polygon":
                self._hover = point
            elif event.buttons() & Qt.MouseButton.LeftButton:
                if self.tool in ("line", "arrow"):
                    points[1:] = [point]
                elif math.dist(point, points[-1]) >= 0.8 / max(self.layout_.scale, 0.01):
                    points.append(point)
            self.viewport().update()
            return
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
        if self._path is not None and self.tool != "polygon":
            page, points = self._path
            self._path = None
            self.viewport().update()
            self.path_chosen.emit(page, list(points))
            return
        if self._path is not None:
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
        if self.tool == "polygon" and self._path is not None:
            self.finish_polygon()
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
        if self.tool == "polygon" and self._path is not None:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_polygon()
                return
            if event.key() == Qt.Key.Key_Backspace:
                self._path[1].pop()
                if not self._path[1]:
                    self._path = None
                self.viewport().update()
                return
            if event.key() == Qt.Key.Key_Escape:
                self._path = self._hover = None
                self.viewport().update()
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
    highlight_fields = True                # Einstellung: Formularfelder in der Ansicht hinterlegen
    undo_budget_mb = 512                   # Einstellung: Speicher für Rückgängig-Schritte (Rest → cache/)
    author = ""                            # Einstellung: Name für Stempel (leer = Anmeldename)

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.doc = QPdfDocument(self)
        self._buffer: QBuffer | None = None
        self.error = ""
        self.data: bytes | None = None      # aktueller Stand (bearbeitbar), None = zu groß/nicht lesbar
        self.encrypted = False              # True = nur lesbar (Passwort fehlt bzw. Rechte verbieten Änderungen)
        self.protection = None              # pdfsecurity.Protection: Schutz, der beim Speichern wieder gilt
        self._keep_protection: bool | None = None   # Antwort auf „Schutz beibehalten?“ (je Tab einmal)
        from notex.core.undo import SnapshotStack
        from notex.paths import cache_dir
        try:
            spill = cache_dir() / "undo"
        except OSError:
            spill = None
        self.history = SnapshotStack(self.undo_budget_mb * 1024 * 1024, spill_dir=spill)
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
        self.canvas.path_chosen.connect(self._on_path)
        self.canvas.menu_hook = self._extend_menu
        self.ink_style = pdfdraw.Style("#2f6fdf", 2.0)
        self.shape_style = pdfdraw.Style("#d03030", 2.0)
        self.current_stamp = pdfstamps.BUILTIN[0]
        self.stamp_with_date = False
        self.stamp_with_user = False
        self.selected_image = None                     # pdfimages.PageImage (Werkzeug „Bilder im Inhalt“)
        self._image_cache: dict[int, list] = {}
        self._crop_target = None                       # (Seite, Index) für das Zuschneiden per Rahmen
        self.canvas.image_overlay = self._image_overlay
        from notex.ui.pdf_objects import ObjectController
        self.objects = ObjectController(self)
        self.objects.highlight_fields = self.highlight_fields
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
        from notex.ui.pdf_outline import OutlineEditor
        self.outline_editor = OutlineEditor()                # bearbeitbar im Bearbeiten-Modus (Block V1)
        self.outline_editor.changed.connect(lambda items: self.set_outline(items))
        self.outline_editor.jump.connect(self.go_to_pdf_point)
        self.outline_editor.add_requested.connect(lambda: self.add_bookmark())
        self.outline_editor.headings_requested.connect(lambda: self.bookmarks_from_headings())
        self.outline_editor.setVisible(False)

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
        outline_button.toggled.connect(self._show_outline_panel)
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
        self.comments_button = IconButton("message-square-text", "Kommentare: Liste, Antworten, Status, Export  "
                                          "Ctrl+Shift+M")
        self.comments_button.setCheckable(True)
        self.comments_button.toggled.connect(self.set_comments_visible)
        from notex.ui.pdf_comments import CommentsPanel
        self.comments_panel = CommentsPanel()
        self.comments_panel.jump.connect(self.show_comment)
        self.comments_panel.reply_requested.connect(lambda page, index: self.reply_comment(page, index))
        self.comments_panel.status_requested.connect(lambda page, index, st: self.set_comment_status(page, index, st))
        self.comments_panel.delete_requested.connect(lambda page, index: self.delete_comment(page, index))
        self.comments_panel.export_requested.connect(lambda kind: self.export_comments(kind))
        self.comments_panel.import_requested.connect(lambda: self.import_xfdf())
        self.comments_panel.setVisible(False)
        self.attach_button = IconButton("paperclip", "Anhänge: speichern, öffnen, hinzufügen, entfernen")
        self.attach_button.clicked.connect(lambda: self.show_attachments())
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
        for widget in (prev_hit, next_hit, self.hits_label, self.comments_button, self.attach_button, self.quote_button,
                       self.edit_button):
            bar.addWidget(widget)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("PreviewSplitter")
        self.splitter.addWidget(self.strip)
        self.splitter.addWidget(self.outline)
        self.splitter.addWidget(self.outline_editor)
        self.splitter.addWidget(self.canvas)
        self.splitter.addWidget(self.form_panel)
        self.splitter.addWidget(self.comments_panel)
        self.splitter.setStretchFactor(3, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setSizes([self.strip.width(), 220, 240, 900, 280, 320])
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
                               ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                               ("Ctrl+Shift+B", lambda: self.add_bookmark()),
                               ("Ctrl+Shift+M", lambda: self.set_comments_visible())):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self._load()

    # ---- Laden ------------------------------------------------------------------------------------
    def _load(self, password: str = "", retry: bool = False) -> None:
        self.doc.close()
        self.encrypted = False
        size = self.path.stat().st_size
        if size <= IN_MEMORY_LIMIT:
            raw = self.path.read_bytes()                    # Datei gleich wieder zu (Windows: umbenennbar)
            try:
                info = pdfsecurity.inspect(raw)
            except pdfpages.PdfEditError:
                info = None
            if info is not None and info.encrypted:         # entsperren: bearbeitbar im Speicher, Schutz merken
                if info.needs_password and not password:
                    text = self._ask_password(retry)
                    if text:
                        self._load(text, retry=True)
                        return
                    self.error = "Passwortgeschützt – nicht geöffnet"
                    self.data = None
                    self._finish_load()
                    return
                try:
                    raw, self.protection = pdfsecurity.unlock(raw, password)
                except pdfpages.PdfEditError as error:
                    if "Falsches Passwort" in str(error):
                        text = self._ask_password(True)
                        if text:
                            self._load(text, retry=True)
                            return
                        self.error = "Passwortgeschützt – nicht geöffnet"
                    else:
                        self.error = str(error)
                    self.data = None
                    self._finish_load()
                    return
                self.encrypted = not self.protection.can_edit
            self.data = raw
            self._set_buffer(self._display_bytes(raw))
        else:
            self.data = None
            if password:
                self.doc.setPassword(password)
                self.encrypted = True
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
        self._finish_load()

    def _ask_password(self, retry: bool = False) -> str:
        """Passwort abfragen (nur hier und nur in einer lokalen Variablen – nie im Log, nie gespeichert)."""
        prompt = ("Falsches Passwort – noch einmal:" if retry else f"Passwort für „{self.path.name}“:")
        text, ok = QInputDialog.getText(self, "Passwortgeschütztes PDF", prompt, QLineEdit.EchoMode.Password)
        return text if ok else ""

    def _finish_load(self) -> None:
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
        self.outline_button.setEnabled(self.bookmarks.rowCount(QModelIndex()) > 0 or self.editing)
        if not self.outline_editor.isHidden():
            self._refresh_outline_editor()
        self.page_count.setText(f"/ {self.doc.pageCount()}")
        if not self.strip.isHidden():
            self.strip.rebuild()
        self.refresh_form()
        self._refresh_attachments()
        self._refresh_comments()

    # ---- Bearbeiten: Zustand, Rückgängig, Speichern -----------------------------------------------------------
    def edit_block_reason(self) -> str:
        """Warum nicht bearbeitet werden kann ("" = geht)."""
        if self.error:
            return self.error
        if self.data is None:
            return "Zu groß zum Bearbeiten (über 256 MB)"
        if self.encrypted:
            if self.protection is not None:
                return ("Das PDF verbietet Änderungen – zum Bearbeiten das Rechte-Passwort eingeben "
                        "(Palette › PDF: Passwortschutz …)")
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
        self.outline_button.setEnabled(self.bookmarks.rowCount(QModelIndex()) > 0 or on)
        self._show_outline_panel(self.outline_button.isChecked())
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
        self.history.push(self.data, self.canvas.current_page)
        self._show_data(new_data, self.canvas.current_page if page is None else page)
        self._set_dirty(True)

    def _show_data(self, data: bytes, page: int) -> None:
        self.data = data
        self._image_cache.clear()
        chosen = self.selected_image
        if chosen is not None:                             # gleiche Stelle wieder auswählen (z. B. nach Zuschneiden)
            again = self.content_images(chosen.page)
            self.selected_image = again[chosen.index] if chosen.index < len(again) else None
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
        self._refresh_images()

    def undo(self) -> None:
        if not self.history.can_undo or self.data is None:
            return
        data, page = self.history.undo(self.data, self.canvas.current_page)
        self._show_data(data, page)
        self._set_dirty(True)

    def redo(self) -> None:
        if not self.history.can_redo or self.data is None:
            return
        data, page = self.history.redo(self.data, self.canvas.current_page)
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
        out = self._bytes_for_saving()
        if out is None:
            return False
        try:
            from notex.ui.pdf_edit import write_pdf
            note = write_pdf(self.path, out, backup=self.backup_to_trash and not self._backed_up)
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

    # ---- Passwortschutz (Block V6) ----------------------------------------------------------------------------
    def _ask_keep_protection(self) -> str:
        """„keep“, „remove“ oder „cancel“ – einmal je Tab gefragt."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("Passwortschutz")
        box.setText("Dieses PDF war geschützt. Soll der Schutz beim Speichern bleiben?")
        algorithm = self.protection.algorithm if self.protection else ""
        box.setInformativeText("Gespeichert wird mit AES-256" + (f" (bisher {algorithm})"
                                                                if algorithm and algorithm != "AES-256" else "") + ".")
        keep = box.addButton("Schutz beibehalten", QMessageBox.ButtonRole.AcceptRole)
        remove = box.addButton("Ohne Schutz speichern", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        clicked = box.clickedButton()
        return "keep" if clicked is keep else "remove" if clicked is remove else "cancel"

    def _bytes_for_saving(self) -> bytes | None:
        """Was auf die Platte geht: unverändert oder – bei Schutz – mit AES-256 verschlüsselt. None = abgebrochen."""
        if self.protection is None:
            return self.data
        if self._keep_protection is None:
            choice = self._ask_keep_protection()
            if choice == "cancel":
                return None
            self._keep_protection = choice == "keep"
        if not self._keep_protection:
            return self.data
        protection = self.protection
        if protection.user_password is None:              # mit Rechte-Passwort entsperrt: Öffnen-Passwort neu setzen
            text, ok = QInputDialog.getText(self, "Öffnen-Passwort", "Das bisherige Öffnen-Passwort ist unbekannt. "
                                            "Neues Öffnen-Passwort (leer = ohne):", QLineEdit.EchoMode.Password)
            if not ok:
                return None
            protection = pdfsecurity.with_passwords(protection, user_password=text)
            self.protection = protection
        try:
            return pdfsecurity.reapply(self.data, protection)
        except pdfpages.PdfEditError as error:
            from notex.ui import dialogs
            dialogs.warn(self, "Passwortschutz", str(error))
            return None

    def unlock_rights(self, password: str | None = None) -> bool:
        """Rechte-Passwort eingeben: danach ist das PDF voll bearbeitbar (falls es Änderungen verbietet)."""
        if self.protection is None:
            self.notice.emit("Dieses PDF hat keinen Passwortschutz")
            return False
        if password is None:
            text, ok = QInputDialog.getText(self, "Rechte-Passwort", "Rechte-Passwort:", QLineEdit.EchoMode.Password)
            if not ok or not text:
                return False
            password = text
        try:
            _plain, protection = pdfsecurity.unlock(self.path.read_bytes(), password)
        except (OSError, pdfpages.PdfEditError) as error:
            self.notice.emit(str(error))
            return False
        if protection.unlocked_as != "owner":
            self.notice.emit("Das ist nicht das Rechte-Passwort")
            return False
        self.protection = pdfsecurity.with_passwords(protection, user_password=self.protection.user_password)
        self.encrypted = False
        self.status_changed.emit()
        self.notice.emit("Rechte freigegeben – das PDF ist jetzt bearbeitbar")
        return True

    def edit_protection(self, protection=...) -> bool:
        """Öffnen-/Rechte-Passwort setzen, ändern oder entfernen. Gilt beim nächsten Speichern (AES-256)."""
        current = self.protection
        restricted = current is not None and current.unlocked_as == "user" and \
            current.permissions != pdfsecurity.Permissions()
        if restricted and not self.unlock_rights():
            return False
        if protection is ...:
            from notex.ui.pdf_security_ui import ProtectionDialog
            dialog = ProtectionDialog(self, self.protection)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            protection = dialog.protection()
        if self.data is None:
            return False
        if protection is not None:                         # selbst gesetzt = volle Rechte in diesem Tab
            from dataclasses import replace
            protection = replace(protection, unlocked_as="owner", algorithm="AES-256")
        self.protection = protection
        self._keep_protection = protection is not None
        self._set_dirty(True)
        self.notice.emit("Schutz wird beim Speichern angewendet (AES-256)" if protection is not None
                         else "Schutz wird beim Speichern entfernt")
        self.status_changed.emit()
        return True

    # ---- Schwärzen nach Muster (Block V6) -------------------------------------------------------------------
    def redact_patterns(self, keys: list[str] | None = None, custom: dict | None = None, choose=None,
                        apply_now: bool | None = None) -> int:
        """Muster (E-Mail, IBAN, …) suchen, Treffer in einer Vorschau abwählen, dann vormerken (oder gleich schwärzen).
        `choose` (Tests): Funktion hits → ausgewählte hits statt Vorschau-Dialog."""
        if self.data is None or self.edit_block_reason():
            self.notice.emit(self.edit_block_reason() or "Nichts zu durchsuchen")
            return 0
        if keys is None:
            from notex.ui.pdf_security_ui import PatternRedactDialog
            config = getattr(self.window(), "config", None)
            saved = list(config.get("pdf_redact_patterns", []) or []) if config is not None else []
            dialog = PatternRedactDialog(self, saved)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return 0
            keys, custom = dialog.keys(), dialog.active_custom()
            if config is not None:
                config["pdf_redact_patterns"] = dialog.custom_entries()
        from notex.ui.tasks import run_task
        data, result = self.data, {}

        def work(ctx):
            return redact_patterns.find_hits(data, keys, custom,
                                             progress=lambda i, n: ctx.step(i, n, f"Seite {i + 1} von {n} …"))

        def done(hits) -> None:
            result["hits"] = hits
            if not hits:
                self.notice.emit("Keine Treffer für die gewählten Muster")
                return
            now = apply_now
            if choose is not None:
                chosen = choose(hits)
            else:
                from notex.ui.pdf_security_ui import PatternHitsDialog
                dialog = PatternHitsDialog(self, hits)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                chosen, now = dialog.chosen(), dialog.apply_now.isChecked()
            result["count"] = self.mark_pattern_hits(chosen)
            if now and result["count"]:
                self.apply_redactions()

        worker = run_task(self, "Muster suchen …", work, on_done=done,
                          on_error=lambda message: self.notice.emit(message), show_dialog=choose is None)
        if choose is not None:                              # Tests: synchron abwarten
            worker.wait()
            from PySide6.QtCore import QCoreApplication
            for _ in range(50):
                QCoreApplication.processEvents()
                if "hits" in result:
                    break
        return int(result.get("count", 0))

    def mark_pattern_hits(self, hits: list) -> int:
        if not hits or not self._ensure_editing():
            return 0
        for hit in hits:
            for rect in hit.rects:
                self.redactions.append((hit.page, tuple(rect)))
            if hit.text not in self.redact_terms:
                self.redact_terms.append(hit.text)
        self._sync_redactions()
        pages = len({h.page for h in hits})
        self.notice.emit(f"{len(hits)} Treffer auf {pages} Seite(n) zum Schwärzen vorgemerkt")
        return len(hits)

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
        out = self._bytes_for_saving()
        if out is None:
            return False
        try:
            from notex.ui.pdf_edit import write_pdf
            write_pdf(target, out)
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
        """Bearbeiten-Leiste in Gruppen (Seiten, Kommentieren, Zeichnen, Formulare, Schwärzen, Datei); bei wenig Platz
        bricht sie gruppenweise in eine zweite Zeile um, statt das Fenster breiter zu erzwingen."""
        from notex.ui.widgets import FlowLayout
        bar_frame = QFrame()
        bar_frame.setObjectName("DataBar")
        flow = FlowLayout(bar_frame, SPACING.md)
        flow.setContentsMargins(SPACING.md, 0, SPACING.md, SPACING.sm)

        def group(*widgets) -> QHBoxLayout:
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(SPACING.sm)
            for widget in widgets:
                row.addWidget(widget)
            flow.addWidget(box)
            return row

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
            ("file", "Leere Seite einfügen …", lambda: self.insert_blank()),
            ("replace", "Seiten durch Seiten aus einem anderen PDF ersetzen …", lambda: self.replace_pages()),
            ("crop", "Seiten zuschneiden (Ränder in mm, weiße Ränder erkennen) …", lambda: self.crop_pages()),
            ("hash", "Seitenbeschriftungen (i, ii, 1, 2, A-1 …) …", lambda: self.edit_page_labels()),
        ]
        self.page_actions = []
        for name, tip, slot in buttons:
            button = IconButton(name, tip)
            button.clicked.connect(slot)
            self.page_actions.append(button)
        group(self.pages_button, *self.page_actions)
        self.tool_buttons: dict[str, IconButton] = {}
        for name, icon_name, tip in TOOLS:
            button = IconButton(icon_name, tip)
            button.setCheckable(True)
            if name == "shape":
                button.clicked.connect(lambda _c=False: self._shape_menu())
            elif name == "stamp":
                button.clicked.connect(lambda _c=False: self._stamp_menu())
            elif name == "formfield":
                button.clicked.connect(lambda _c=False: self._field_menu())
            else:
                button.clicked.connect(lambda _c=False, n=name: self.set_tool(n))
            self.tool_buttons[name] = button
        self.tool_buttons["select"].setChecked(True)
        t = self.tool_buttons
        self.color_button = IconButton("palette", "Farbe für Markieren/Unterstreichen/Durchstreichen/Notiz")
        self.color_button.clicked.connect(self._color_menu)
        self.style_button = IconButton("sliders-horizontal", "Stil für Stift und Formen: Farbe, Stärke, Füllung, "
                                       "Deckkraft, Linienstil (auch für das ausgewählte Objekt)")
        self.style_button.clicked.connect(lambda: self.edit_draw_style())
        self.form_button = IconButton("clipboard-list", "Formular ausfüllen (Feldliste) ein/aus")
        self.form_button.setCheckable(True)
        self.form_button.toggled.connect(self.set_form_visible)
        detect = IconButton("scan-text", "Formularfelder automatisch erkennen (Name, Datum, Unterschrift …)")
        detect.clicked.connect(lambda: self.detect_fields())
        flatten = IconButton("check-check", "Anmerkungen und Formular fest einbrennen …")
        flatten.clicked.connect(lambda: self.flatten())
        self.redact_apply = QPushButton("Schwärzen anwenden …")
        self.redact_apply.setObjectName("Danger")
        self.redact_apply.setToolTip("Vorgemerkte Bereiche endgültig schwärzen (Seiten werden als Bild neu erzeugt)")
        self.redact_apply.clicked.connect(lambda: self.apply_redactions())
        self.redact_apply.setVisible(False)
        group(t["select"], t["highlight"], t["underline"], t["strikeout"], t["note"], t["text"], self.color_button)
        group(t["ink"], t["eraser"], t["shape"], t["stamp"], self.style_button, t["diagram"], t["link"])
        images = IconButton("images", "Bilder im Dokument: Liste – exportieren (einzeln/alle), ersetzen, zuschneiden, "
                            "löschen")
        images.clicked.connect(lambda: self.show_images())
        group(t["image"], t["pickimage"], images)
        self.taborder_button = IconButton("list-ordered", "Tab-Reihenfolge: Nummern einblenden, bearbeiten, "
                                          "nach Zeilen/Spalten")
        self.taborder_button.setCheckable(True)
        self.taborder_button.clicked.connect(lambda _c=False: self._taborder_menu())
        formdata = IconButton("database", "Formulardaten: exportieren (JSON, XFDF, FDF), importieren, "
                              "Antworten sammeln")
        formdata.clicked.connect(lambda: self._formdata_menu(formdata))
        group(t["field"], t["checkbox"], t["formfield"], t["signature"], self.form_button, detect,
              self.taborder_button, formdata, flatten)
        patterns = IconButton("regex", "Nach Mustern schwärzen: E-Mail, Telefon, IBAN, Kreditkarte, Steuer-ID … "
                              "oder eigene Ausdrücke")
        patterns.clicked.connect(lambda: self.redact_patterns())
        self.tool_row = group(t["redact"], patterns, t["crop"], self.redact_apply)
        assert set(t) == {name for name, _i, _t in TOOLS}, "jedes Werkzeug gehört in eine Gruppe"
        self.undo_button = IconButton("undo-2", "Rückgängig  Ctrl+Z")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = IconButton("redo-2", "Wiederholen  Ctrl+Y")
        self.redo_button.clicked.connect(self.redo)
        self.save_button = IconButton("save", "Speichern  Ctrl+S")
        self.save_button.clicked.connect(self.save)
        save_as = IconButton("file-down", "Speichern unter …  Ctrl+Shift+Alt+S")
        save_as.clicked.connect(lambda: self.save_as())
        self.protect_button = IconButton("lock", "Passwortschutz: Öffnen-/Rechte-Passwort setzen, ändern, entfernen "
                                         "(AES-256)")
        self.protect_button.clicked.connect(lambda: self.edit_protection())
        group(self.undo_button, self.redo_button, self.save_button, save_as, self.protect_button)
        return bar_frame

    # ---- Seitenwerkzeuge (Block V1) --------------------------------------------------------------------------
    def insert_blank(self, at: int | None = None, size: str | tuple | None = None) -> bool:
        """Leere Seite vor Seite `at`; ohne Angaben fragt ein Dialog (Position, Größe)."""
        if at is None or size is None:
            from notex.ui.pdf_pages_ui import BlankPageDialog
            dialog = BlankPageDialog(self, self.canvas.current_page, self.doc.pageCount())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            at, size = dialog.values()
        if not self._ensure_editing():
            return False
        return self._run(pdfpages.insert_blank, at, size, page=at)

    def replace_pages(self, first: int | None = None, last: int | None = None, source: Path | str | None = None,
                      other_pages: list[int] | None = None) -> bool:
        """Seiten first…last in einem Schritt durch Seiten aus einem anderen PDF ersetzen."""
        if source is None:
            from notex.ui.pdf_pages_ui import ReplaceDialog
            pages = self.target_pages()
            first = min(pages) if first is None else first
            last = max(pages) if last is None else last
            dialog = ReplaceDialog(self, self.doc.pageCount(), first, last, self.path.parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            first, last, source, other_pages = dialog.values()
        try:
            other = Path(source).read_bytes()
        except OSError as error:
            self.notice.emit(f"Nicht lesbar: {error}")
            return False
        if not self._ensure_editing():
            return False
        return self._run(pdfpages.replace_pages, first, last, other, other_pages, page=min(first, last))

    def crop_pages(self, pages: list[int] | None = None, margins: tuple | None = None, reset: bool = False,
                   rect_page: int | None = None, rect: tuple | None = None) -> bool:
        """Zuschneiden (nur CropBox, kein Schwärzen). Ohne Ränder fragt ein Dialog – vorbelegt aus `rect`."""
        if margins is None and not reset:
            from notex.ui.pdf_pages_ui import CropDialog
            start = (0.0, 0.0, 0.0, 0.0)
            current = self.canvas.current_page if rect_page is None else rect_page
            if rect is not None:
                width, height = pdfannot.page_geometry(self.data, current).size
                x0, y0, x1, y1 = rect
                start = (min(x0, x1), min(y0, y1), width - max(x0, x1), height - max(y0, y1))
            dialog = CropDialog(self, self.doc.pageCount(), current, start, detect=self.detect_margins)
            if pages is not None and len(pages) > 1:
                dialog.scope.setCurrentIndex(1)
                dialog.range_field.setText(pdfpages.describe(pages).replace("–", "-"))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            pages, margins = dialog.values()
            reset = margins is None
        pages = pages if pages is not None else [self.canvas.current_page]
        if not self._ensure_editing():
            return False
        if reset:
            return self._run(pdfpages.reset_crop, pages, page=pages[0])
        return self._run(lambda data, p, m: pdfpages.crop(data, p, margins=m), pages, tuple(margins), page=pages[0])

    def detect_margins(self, page: int, threshold: int = 245):
        return pdfpages.detect_margins(self.data, page, threshold)

    def _region_crop(self, page: int, box: tuple) -> None:
        x0, y0, x1, y1 = box
        if abs(x1 - x0) < 10 or abs(y1 - y0) < 10:
            self.crop_pages(rect_page=page)
        else:
            self.crop_pages(rect_page=page, rect=box)
        self.set_tool("select")

    def edit_page_labels(self, ranges: list | None = None) -> bool:
        """Seitenbeschriftungen bearbeiten (Bereiche mit Stil, Präfix, Startwert); leere Liste entfernt sie."""
        if ranges is None:
            from notex.ui.pdf_pages_ui import LabelsDialog
            try:
                current = pdfpages.read_labels(self.data)
            except pdfpages.PdfEditError as error:
                self.notice.emit(str(error))
                return False
            dialog = LabelsDialog(self, self.doc.pageCount(), current)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            ranges = dialog.ranges()
        if not self._ensure_editing():
            return False
        return self._run(pdfpages.set_labels, ranges)

    # ---- Lesezeichen bearbeiten (Block V1) --------------------------------------------------------------------
    def _show_outline_panel(self, on: bool) -> None:
        editing = on and self.editing
        self.outline.setVisible(on and not editing)
        self.outline_editor.setVisible(editing)
        if editing:
            self._refresh_outline_editor()

    def show_outline_editor(self) -> None:
        self.outline_button.setChecked(True)
        self._show_outline_panel(True)

    def _refresh_outline_editor(self) -> None:
        try:
            self.outline_editor.set_items(pdfoutline.read_outline(self.data) if self.data is not None else [])
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))

    def outline_items(self) -> list:
        return pdfoutline.read_outline(self.data) if self.data is not None else []

    def set_outline(self, items: list) -> bool:
        """Ganzen Lesezeichen-Baum schreiben (ein Rückgängig-Schritt)."""
        if not self._ensure_editing():
            return False
        ok = self._run(pdfoutline.write_outline, items)
        if not ok:
            self._refresh_outline_editor()                 # Baum wieder auf den gespeicherten Stand
        return ok

    def current_pdf_top(self) -> tuple[int, float]:
        """(Seite, y in PDF-Koordinaten) des oberen Fensterrands – dorthin springt ein neues Lesezeichen."""
        page = self.canvas.current_page
        layout = self.canvas.layout_
        view_y = 0.0
        if layout is not None and layout.pages:
            rect = layout.pages[page]
            view_y = max(0.0, (self.canvas.verticalScrollBar().value() - rect.y) / layout.scale)
        geom = pdfannot.page_geometry(self.data, page)
        return page, geom.to_pdf(0.0, view_y)[1]

    def add_bookmark(self, title: str | None = None, page: int | None = None, top: float | None = None) -> bool:
        """Lesezeichen auf die aktuelle Seite/Position (oder `page`/`top`) ans Ende; Titel = markierter Text."""
        if self.data is None:
            return False
        if page is None:
            page, top = self.current_pdf_top()
        if title is None:
            from PySide6.QtWidgets import QInputDialog
            suggestion = " ".join(self.canvas.selected_text().split())[:80] or f"Seite {self.page_label(page)}"
            title, ok = QInputDialog.getText(self, "Lesezeichen hinzufügen", "Titel:", text=suggestion)
            if not ok or not title.strip():
                return False
        items = self.outline_items()
        items.append(pdfoutline.Bookmark(title.strip(), page, top))
        if self.set_outline(items):
            self.outline_button.setChecked(True)
            return True
        return False

    def bookmarks_from_headings(self, mode: str | None = None, choose=None) -> bool:
        """Überschriften (größere Schrift) finden, in einer Vorschau abwählen lassen, dann ersetzen oder anhängen.
        `choose` (Tests): Funktion headings → (Bookmarks, Modus) statt Dialog."""
        if self.data is None:
            return False
        from notex.ui.tasks import run_task
        data = self.data
        result: dict = {}

        def work(ctx):
            return pdfoutline.find_headings(data, progress=lambda i, n: ctx.step(i, n, f"Seite {i + 1} von {n} …"))

        def done(headings) -> None:
            result["headings"] = headings
            if not headings:
                self.notice.emit("Keine Überschriften gefunden (alles in einer Schriftgröße?)")
                return
            existing = self.outline_items()
            if choose is not None:
                marks, how = choose(headings)
            else:
                from notex.ui.pdf_outline import HeadingsDialog
                dialog = HeadingsDialog(self, headings, bool(existing))
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                marks, how = dialog.chosen(), dialog.mode.currentData()
            how = mode or how
            if marks:
                result["ok"] = self.set_outline((existing if how == "append" else []) + marks)
                self.outline_button.setChecked(True)

        worker = run_task(self, "Überschriften suchen …", work, on_done=done,
                          on_error=lambda message: self.notice.emit(message), show_dialog=choose is None)
        if choose is not None:                              # Tests: synchron abwarten
            worker.wait()
            from PySide6.QtCore import QCoreApplication
            for _ in range(50):
                QCoreApplication.processEvents()
                if "headings" in result:
                    break
            return bool(result.get("ok"))
        return True

    def go_to_pdf_point(self, page: int, top) -> None:
        """Zu Seite + y (PDF-Koordinaten) springen – für bearbeitbare Lesezeichen."""
        if top is None or self.data is None:
            self.canvas.go_to(page)
            return
        geom = pdfannot.page_geometry(self.data, page)
        x, y = geom.to_view(geom.box[0], float(top))
        self.canvas.go_to(page, QPointF(max(0.01, x), max(0.01, y)))

    # ---- Zeichnen, Formen, Stempel (Block V3) -------------------------------------------------------------------
    def _on_path(self, page: int, points: list) -> None:
        tool = self.canvas.tool
        if tool == "ink":
            self.add_ink(page, [points])
        elif tool == "eraser":
            self.erase(page, points)
        elif tool in ("line", "arrow", "polygon"):
            if tool != "polygon" and len(points) < 2:
                self.notice.emit("Für eine Linie ziehen (Maustaste gedrückt halten)")
                return
            self.add_shape(page, tool, points)

    def _region_rect(self, page: int, box: tuple) -> None:
        self._region_shape(page, "rect", box)

    def _region_ellipse(self, page: int, box: tuple) -> None:
        self._region_shape(page, "ellipse", box)

    def _region_cloud(self, page: int, box: tuple) -> None:
        self._region_shape(page, "cloud", box)

    def _region_shape(self, page: int, kind: str, box: tuple) -> None:
        x0, y0, x1, y1 = box
        if abs(x1 - x0) < 2 or abs(y1 - y0) < 2:
            self.notice.emit("Für eine Form einen Bereich aufziehen")
            return
        self.add_shape(page, kind, [(x0, y0), (x1, y1)])

    def add_ink(self, page: int, strokes: list, style: pdfdraw.Style | None = None) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfdraw.add_ink, page, strokes, style or self.ink_style, page=page)

    def add_shape(self, page: int, kind: str, points: list, style: pdfdraw.Style | None = None) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfdraw.add_shape, page, kind, points, style or self.shape_style, page=page)

    def erase(self, page: int, points: list) -> int:
        """Radierer: alle Freihand-Striche entfernen, die der Weg berührt – immer ganze Striche."""
        if not self._ensure_editing() or self.edit_block_reason():
            return 0
        try:
            result, removed = pdfdraw.erase_at(self.data, page, points, 4.0)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return 0
        if not removed:
            self.notice.emit("Hier ist kein Freihand-Strich")
            return 0
        self.apply(result, page)
        self.notice.emit("1 Strich entfernt" if removed == 1 else f"{removed} Striche entfernt")
        return removed

    def _shape_menu(self) -> None:
        menu = style_menu(QMenu(self))
        from notex.theme.icons import icon
        for key, icon_name, label in SHAPE_LABELS:
            action = menu.addAction(icon(icon_name), label, lambda k=key: self.set_tool(k))
            action.setCheckable(True)
            action.setChecked(self.canvas.tool == key)
        menu.addSeparator()
        menu.addAction("Stil …", lambda: self.edit_draw_style())
        button = self.tool_buttons["shape"]
        self.set_tool(self.canvas.tool)                  # Knopf-Zustand wiederherstellen, bis gewählt ist
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def edit_draw_style(self, style: pdfdraw.Style | None = None, pen: bool | None = None,
                        apply_selected: bool | None = None) -> bool:
        """Stil für Stift oder Formen; ist eine Zeichnung/Form ausgewählt, wird er auf Wunsch auch dort angewendet."""
        selected = self.objects.selected
        drawable = selected is not None and selected.kind in pdfdraw.DRAW_KINDS
        if pen is None:
            pen = self.canvas.tool in ("ink", "eraser") or (drawable and selected.kind == "ink")
        if style is None:
            from notex.ui.pdf_draw_ui import DrawStyleDialog
            dialog = DrawStyleDialog(self, self.ink_style if pen else self.shape_style, pen, drawable)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            style = dialog.style()
            apply_selected = dialog.apply_selected.isChecked()
        if pen:
            self.ink_style = style
        else:
            self.shape_style = style
        self.set_tool(self.canvas.tool)
        if drawable and apply_selected:
            if not self._ensure_editing():
                return False
            return self._run(pdfdraw.restyle, selected.page, selected.index, style, page=selected.page)
        return True

    # Stempel
    def stamp_user(self) -> str:
        if self.author.strip():
            return self.author.strip()
        import getpass
        try:
            return getpass.getuser()
        except Exception:                               # noqa: BLE001 – ohne Anmeldenamen: leer
            return ""

    def custom_stamps(self) -> list:
        from notex.paths import stamps_dir
        return pdfstamps.load_custom(stamps_dir())

    def _stamp_menu(self) -> None:
        menu = style_menu(QMenu(self))
        for spec in [*pdfstamps.BUILTIN, *self.custom_stamps()]:
            action = menu.addAction(spec.label + ("  (Bild)" if spec.image else ""),
                                    lambda sp=spec: self.choose_stamp(sp))
            action.setCheckable(True)
            action.setChecked(spec.key == self.current_stamp.key and self.canvas.tool == "stamp")
        menu.addSeparator()
        date = menu.addAction("Mit Datum und Uhrzeit", lambda: setattr(self, "stamp_with_date", not self.stamp_with_date))
        date.setCheckable(True)
        date.setChecked(self.stamp_with_date)
        user = menu.addAction(f"Mit Namen ({self.stamp_user() or 'leer'})",
                              lambda: setattr(self, "stamp_with_user", not self.stamp_with_user))
        user.setCheckable(True)
        user.setChecked(self.stamp_with_user)
        menu.addSeparator()
        menu.addAction("Eigenen Stempel anlegen …", lambda: self.create_custom_stamp())
        customs = self.custom_stamps()
        if customs:
            remove = menu.addMenu("Eigenen Stempel löschen")
            for spec in customs:
                remove.addAction(spec.label, lambda sp=spec: self.delete_custom_stamp(sp.key))
        button = self.tool_buttons["stamp"]
        self.set_tool(self.canvas.tool)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def choose_stamp(self, spec) -> None:
        self.current_stamp = spec
        self.set_tool("stamp")
        self.notice.emit(f"Stempel „{spec.label}“: auf die Seite klicken oder Bereich aufziehen")

    def create_custom_stamp(self, values: dict | None = None):
        if values is None:
            from notex.ui.pdf_draw_ui import CustomStampDialog
            dialog = CustomStampDialog(self, self.path.parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            values = dialog.values()
        from notex.paths import stamps_dir
        try:
            spec = pdfstamps.save_custom(stamps_dir(), **values)
        except (pdfpages.PdfEditError, OSError) as error:
            self.notice.emit(f"Stempel nicht angelegt: {error}")
            return None
        self.choose_stamp(spec)
        return spec

    def delete_custom_stamp(self, key: str, confirmed: bool | None = None) -> bool:
        from notex.paths import stamps_dir
        if confirmed is None:
            from notex.ui import dialogs
            confirmed = dialogs.confirm(self, "Stempel löschen?", "Diesen eigenen Stempel löschen?", yes="Löschen",
                                        danger=True, informative="Schon gesetzte Stempel in PDFs bleiben.")
        if not confirmed:
            return False
        try:
            pdfstamps.delete_custom(stamps_dir(), key)
        except (pdfpages.PdfEditError, OSError) as error:
            self.notice.emit(str(error))
            return False
        if self.current_stamp.key == key:
            self.current_stamp = pdfstamps.BUILTIN[0]
        return True

    def _region_stamp(self, page: int, box: tuple) -> None:
        self.add_stamp(page, box)

    def add_stamp(self, page: int, rect: tuple, spec=None) -> bool:
        from dataclasses import replace
        from datetime import datetime
        spec = spec or self.current_stamp
        spec = replace(spec, with_date=spec.with_date or self.stamp_with_date,
                       with_user=spec.with_user or self.stamp_with_user)
        image = None
        if spec.image:
            from PySide6.QtGui import QImage
            from notex.paths import stamps_dir
            from notex.ui.pdf_edit import image_to_bytes
            picture = QImage(str(stamps_dir() / spec.image))
            if picture.isNull():
                self.notice.emit("Das Bild dieses Stempels fehlt oder ist kaputt")
                return False
            image = image_to_bytes(picture, 800)
        if not self._ensure_editing():
            return False
        date = datetime.now().strftime("%d.%m.%Y %H:%M")
        return self._run(pdfstamps.add_stamp, page, tuple(rect), spec, date, self.stamp_user(), image, page=page)

    # ---- Links (Block V2) ---------------------------------------------------------------------------------------
    def _region_link(self, page: int, box: tuple) -> None:
        x0, y0, x1, y1 = box
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            self.notice.emit("Für einen Link einen Bereich aufziehen (z. B. über den Text, der klickbar sein soll)")
            return
        self.add_link(page, box)

    def add_link(self, page: int, rect: tuple, target=None, border: bool | None = None) -> bool:
        """Link über `rect` (Ansichts-Punkte); ohne Ziel fragt ein Dialog (Seite, Lesezeichen, Webadresse, PDF)."""
        if target is None:
            from notex.ui.pdf_links_ui import LinkDialog
            dialog = LinkDialog(self, self.doc.pageCount(), self._outline_for_links(), current_page=page)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            target, border = dialog.values()
        if not self._ensure_editing():
            return False
        return self._run(pdflinks.add_link, page, tuple(rect), target, bool(border), page=page)

    def _outline_for_links(self) -> list:
        try:
            return pdfoutline.read_outline(self.data) if self.data else []
        except Exception:                               # noqa: BLE001 – kaputte Lesezeichen: ohne Auswahl
            return []

    def link_target(self, obj):
        link = self._link_of(obj)
        return link.target if link is not None else None

    def _link_of(self, obj):
        try:
            return next((link for link in pdflinks.list_links(self.data, obj.page) if link.index == obj.index), None)
        except pdfpages.PdfEditError:
            return None

    def edit_link(self, obj, target=None, border: bool | None = None) -> bool:
        """Ziel/Rahmen eines Links ändern. Ein fremdes Ziel bleibt, solange man keins wählt."""
        link = self._link_of(obj)
        if link is None:
            return False
        if target is None and border is None:
            from notex.ui.pdf_links_ui import LinkDialog
            known = link.target if link.target.kind != "other" else None
            dialog = LinkDialog(self, self.doc.pageCount(), self._outline_for_links(), known, link.border, obj.page)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            target, border = dialog.values()
        if not self._ensure_editing():
            return False
        return self._run(pdflinks.update_link, obj.page, obj.index, target, border, page=obj.page)

    def follow_link(self, obj) -> None:
        """Klick auf einen Link im Lesemodus: Seitenziele springen; Webadressen/Dateien nur anzeigen (nie öffnen)."""
        target = self.link_target(obj)
        if target is None or target.kind == "other":
            self.notice.emit("Link mit einer anderen Aktion – wird aus Sicherheitsgründen nicht ausgeführt")
        elif target.kind == "page":
            self.go_to_pdf_point(target.page, target.top)
        else:
            what = "Webadresse" if target.kind == "url" else "Datei"
            self.notice.emit(f"Link auf {what}: {target.describe()} – wird nicht geöffnet "
                             "(Rechtsklick › Link-Ziel kopieren)")

    def copy_link_target(self, target) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(target.url if target.kind == "url" else target.file)
        self.notice.emit("Link-Ziel kopiert")

    # ---- Anhänge (Block V2) -------------------------------------------------------------------------------------
    def attachments(self) -> list:
        try:
            return pdfattach.list_attachments(self.data) if self.data else []
        except Exception:                               # noqa: BLE001 – unlesbar: leere Liste statt Absturz
            return []

    def show_attachments(self):
        """Anhänge-Liste (nicht modal). Speichern/Öffnen geht auch im Lesemodus."""
        from notex.ui.pdf_links_ui import AttachmentsDialog
        from notex.platform import external
        dialog = getattr(self, "_attach_dialog", None)
        if dialog is None:
            dialog = AttachmentsDialog(self)
            dialog.save_requested.connect(lambda key: self.save_attachment(key))
            dialog.open_requested.connect(lambda key: self.open_attachment(key))
            dialog.add_requested.connect(lambda: self.add_attachment())
            dialog.remove_requested.connect(lambda key: self.remove_attachment(key))
            availability = external.available()
            dialog.can_open, dialog.open_hint = availability.ok, availability.hint
            self._attach_dialog = dialog
        self._refresh_attachments()
        dialog.show()
        dialog.raise_()
        return dialog

    def _refresh_attachments(self) -> None:
        dialog = getattr(self, "_attach_dialog", None)
        if dialog is not None:
            dialog.set_items(self.attachments(), editable=not self.edit_block_reason())

    def _attachment(self, key: str):
        return next((a for a in self.attachments() if a.key == key), None)

    def save_attachment(self, key: str, target: Path | str | None = None) -> Path | None:
        """Anhang als Datei speichern (Dialog fragt nach dem Ort; Überschreiben nur nach Rückfrage des Dialogs)."""
        try:
            name, content = pdfattach.extract(self.data, key)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return None
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getSaveFileName(self, "Anhang speichern",
                                                     str(self.path.parent / pdfattach.safe_filename(name)))
            if not chosen:
                return None
            target = chosen
        target = Path(target)
        try:
            target.write_bytes(content)
        except OSError as error:
            self.notice.emit(f"Nicht gespeichert: {error}")
            return None
        self.notice.emit(f"Anhang gespeichert: {target.name}")
        return target

    def open_attachment(self, key: str, confirmed: bool | None = None) -> bool:
        """Anhang mit dem System-Programm öffnen – nur nach Rückfrage, Programme/Skripte nie."""
        from notex.platform import external
        from notex.ui import dialogs
        item = self._attachment(key)
        if item is None:
            return False
        if item.risky:
            dialogs.warn(self, "Anhang öffnen", f"„{item.name}“ ist ein Programm, Skript oder Makro-Dokument.",
                         informative=f"So etwas öffnet {APP_NAME} nie. Über „Speichern unter …“ lässt es sich "
                                     "ablegen und vor dem Öffnen prüfen.")
            return False
        if not external.available().ok:
            self.notice.emit(external.available().hint)
            return False
        if confirmed is None:
            confirmed = dialogs.confirm(
                self, "Anhang öffnen?", f"„{item.name}“ mit dem Standardprogramm öffnen?",
                yes="Öffnen", danger=True,
                informative=f"Typ: {item.mime or 'unbekannt'} · Größe: {human_size(item.size)}\n\n"
                            "Anhänge können Schadsoftware enthalten. Nur öffnen, wenn Sie der Quelle vertrauen.")
        if not confirmed:
            return False
        try:
            name, content = pdfattach.extract(self.data, key)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return False
        if pdfattach.risky(name, content[:8]):
            self.notice.emit("Der Anhang ist ein Programm – wird nicht geöffnet")
            return False
        path = _attachment_temp_dir() / pdfattach.safe_filename(name)
        try:
            path.write_bytes(content)
        except OSError as error:
            self.notice.emit(f"Konnte den Anhang nicht ablegen: {error}")
            return False
        if not external.open_path(path):
            self.notice.emit("Dieser Anhang lässt sich nicht öffnen")
            return False
        return True

    def add_attachment(self, path: Path | str | None = None, description: str = "") -> bool:
        if path is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getOpenFileName(self, "Datei anhängen", str(self.path.parent))
            if not chosen:
                return False
            path = chosen
        path = Path(path)
        try:
            if path.stat().st_size > pdfattach.MAX_ATTACHMENT:
                self.notice.emit(f"Zu groß (höchstens {pdfattach.MAX_ATTACHMENT // (1024 * 1024)} MB)")
                return False
            content = path.read_bytes()
        except OSError as error:
            self.notice.emit(f"Nicht lesbar: {error}")
            return False
        if not self._ensure_editing():
            return False
        done = self._run(pdfattach.add_attachment, path.name, content, description)
        self._refresh_attachments()
        return done

    def remove_attachment(self, key: str, confirmed: bool | None = None) -> bool:
        item = self._attachment(key)
        if item is None:
            return False
        if confirmed is None:
            from notex.ui import dialogs
            confirmed = dialogs.confirm(self, "Anhang entfernen?", f"„{item.name}“ aus dem PDF entfernen?",
                                        yes="Entfernen", danger=True,
                                        informative="Rückgängig mit Ctrl+Z, solange nicht gespeichert ist.")
        if not confirmed or not self._ensure_editing():
            return False
        done = self._run(pdfattach.remove_attachment, key, page=item.page)
        self._refresh_attachments()
        return done

    # ---- Anmerkungen ----------------------------------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        if tool not in self.tool_buttons and tool not in SHAPE_TOOLS and tool not in FIELD_TOOLS and \
                tool != "imagecrop":
            tool = "select"
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool or (name == "shape" and tool in SHAPE_TOOLS)
                              or (name == "formfield" and tool in FIELD_TOOLS)
                              or (name == "pickimage" and tool == "imagecrop"))
        if tool not in ("pickimage", "imagecrop"):
            self.selected_image = None
        if tool in SHAPE_TOOLS:
            icon_name = next(i for k, i, _l in SHAPE_LABELS if k == tool)
            from notex.theme.icons import icon
            self.tool_buttons["shape"].setIcon(icon(icon_name))
        style = self.ink_style if tool == "ink" else self.shape_style
        self.canvas.path_color, self.canvas.path_width = style.color, style.width
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
        obj = self.objects.at(page, x, y)
        if obj is not None and obj.kind == "image":
            self._image_object_menu(menu, obj)
            return
        content = pdfimages.hit(self.content_images(page), x, y) if self.data else None
        if content is not None and (self.canvas.tool in ("pickimage", "imagecrop") or
                                    not self.canvas.selected_text()):
            self._content_image_menu(menu, content)
            if self.canvas.tool in ("pickimage", "imagecrop"):
                return
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
        link = next((o for o in self.objects.objects(page) if o.kind == "link" and o.contains(x, y)), None)
        if link is not None:
            menu.addSeparator()
            target = self.link_target(link)
            if target is not None and target.kind in ("url", "file"):
                menu.addAction("Link-Ziel kopieren", lambda t=target: self.copy_link_target(t))
            menu.addAction("Link bearbeiten …", lambda: self.edit_link(link))
            menu.addAction("Link löschen", lambda: self.delete_object(link))
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
                if obj.kind == "diagram":
                    menu.addAction("Diagramm als draw.io-Datei speichern …", lambda: self.export_drawio(obj))
            elif info.text_editable:
                menu.addAction(f"{info.label}: Kommentar bearbeiten …", lambda: self.edit_annotation(page, info.index))
            menu.addAction(f"{info.label} löschen", lambda: self.delete_annotation(page, info.index))
        elif self.editing:
            menu.addSeparator()
            menu.addAction("Notiz hier …", lambda: self.add_note(page, x, y))
            menu.addAction("Text hier …", lambda: self.add_text(page, (x, y, x, y)))

    # ---- Bilder (Block V7) -------------------------------------------------------------------------------------
    def _read_picture(self, path: Path | str | None, title: str = "Bild wählen") -> bytes | None:
        if path is None:
            from PySide6.QtWidgets import QFileDialog
            from notex.ui.pdf_images_ui import IMAGE_FILTER
            chosen, _f = QFileDialog.getOpenFileName(self, title, str(self.path.parent), IMAGE_FILTER)
            if not chosen:
                return None
            path = chosen
        path = Path(path)
        try:
            if path.stat().st_size > pdfimages.MAX_FILE:
                self.notice.emit(f"Bild zu groß (höchstens {pdfimages.MAX_FILE // (1024 * 1024)} MB)")
                return None
            return path.read_bytes()
        except OSError as error:
            self.notice.emit(f"Nicht lesbar: {error}")
            return None

    def _region_image(self, page: int, box: tuple) -> None:
        from PySide6.QtGui import QGuiApplication
        stretch = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.insert_image(page, box, keep_aspect=not stretch)

    def insert_image(self, page: int, rect: tuple, path: Path | str | None = None, keep_aspect: bool = True,
                     opacity: float = 1.0) -> bool:
        """Bild als Objekt einfügen (Klick = natürliche Größe, Rahmen = einpassen bzw. füllen)."""
        content = self._read_picture(path, "Bild einfügen")
        if content is None or not self._ensure_editing():
            return False
        if not self._run(pdfimages.insert_image, page, tuple(rect), content, keep_aspect, opacity, page=page):
            return False
        images = [o for o in self.objects.objects(page) if o.kind == "image"]
        if images:
            self.objects.select(max(images, key=lambda o: o.index))
        return True

    def _image_obj(self, obj):
        obj = obj or self.objects.selected
        if obj is None or obj.kind != "image":
            self.notice.emit("Erst ein eingefügtes Bild anklicken (Werkzeug „Auswählen“)")
            return None
        return obj

    def rotate_image(self, obj=None, degrees: float = 90, absolute: bool = False) -> bool:
        obj = self._image_obj(obj)
        if obj is None or not self._ensure_editing():
            return False
        return self._run(pdfimages.rotate_image, obj.page, obj.index, degrees, absolute, page=obj.page)

    def image_properties(self, obj=None, angle: float | None = None, opacity: float | None = None) -> bool:
        obj = self._image_obj(obj)
        if obj is None:
            return False
        try:
            meta = pdfimages.image_meta(self.data, obj.page, obj.index)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return False
        if angle is None and opacity is None:
            from notex.ui.pdf_images_ui import ImagePropertiesDialog
            dialog = ImagePropertiesDialog(self, meta.angle, meta.opacity)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            angle, opacity = dialog.values()
        angle = meta.angle if angle is None else angle
        opacity = meta.opacity if opacity is None else opacity
        if abs(angle - meta.angle) < 0.01 and abs(opacity - meta.opacity) < 0.001:
            return False
        if not self._ensure_editing():
            return False
        page, index = obj.page, obj.index
        return self._run(lambda d: pdfimages.set_image_opacity(pdfimages.rotate_image(d, page, index, angle, True),
                                                               page, index, opacity), page=page)

    def replace_image(self, obj=None, path: Path | str | None = None) -> bool:
        obj = self._image_obj(obj)
        if obj is None:
            return False
        content = self._read_picture(path, "Bild ersetzen")
        if content is None or not self._ensure_editing():
            return False
        return self._run(pdfimages.replace_inserted, obj.page, obj.index, content, page=obj.page)

    def _save_image_bytes(self, raw: bytes, ext: str, name: str, target: Path | str | None) -> Path | None:
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getSaveFileName(self, "Bild exportieren", str(self.path.parent / f"{name}.{ext}"),
                                                     f"Bild (*.{ext})")
            if not chosen:
                return None
            target = chosen
        target = Path(target)
        if target.suffix.lower() != f".{ext}":
            target = target.with_name(target.name + f".{ext}")
        try:
            target.write_bytes(raw)
        except OSError as error:
            self.notice.emit(f"Nicht gespeichert: {error}")
            return None
        self.notice.emit(f"Bild gespeichert: {target.name}")
        return target

    def export_image(self, obj=None, target: Path | str | None = None) -> Path | None:
        obj = self._image_obj(obj)
        if obj is None:
            return None
        try:
            raw, ext = pdfimages.export_inserted(self.data, obj.page, obj.index)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return None
        return self._save_image_bytes(raw, ext, f"{self.path.stem}-S{obj.page + 1}-Bild", target)

    def _image_object_menu(self, menu, obj) -> None:
        menu.addSeparator()
        menu.addAction("Bild exportieren …", lambda: self.export_image(obj))
        if self.edit_block_reason():
            return
        menu.addAction("Bild: Drehung und Deckkraft …", lambda: self.image_properties(obj))
        menu.addAction("Bild nach rechts drehen  R", lambda: self.rotate_image(obj, 90))
        menu.addAction("Bild nach links drehen  Shift+R", lambda: self.rotate_image(obj, -90))
        menu.addAction("Bild ersetzen …", lambda: self.replace_image(obj))
        menu.addAction("Bild löschen", lambda: self.delete_object(obj))

    # Bilder im Seiteninhalt
    def content_images(self, page: int) -> list:
        if page not in self._image_cache:
            try:
                self._image_cache[page] = pdfimages.page_images(self.data, page) if self.data else []
            except Exception:                           # noqa: BLE001 – unlesbarer Inhalt: keine Bilder anbieten
                self._image_cache[page] = []
        return self._image_cache[page]

    def _image_overlay(self, page: int) -> list:
        if self.canvas.tool not in ("pickimage", "imagecrop") or self.data is None:
            return []
        chosen = self.selected_image
        return [(image.rect, chosen is not None and (chosen.page, chosen.index) == (page, image.index))
                for image in self.content_images(page)]

    def select_content_image(self, page: int, index: int | None) -> None:
        images = self.content_images(page)
        self.selected_image = images[index] if index is not None and 0 <= index < len(images) else None
        if self.selected_image is not None:
            self.notice.emit(self.selected_image.describe())
            dialog = getattr(self, "_images_dialog", None)
            if dialog is not None and dialog.isVisible():
                dialog.select(page, index)
        self.canvas.viewport().update()
        self.status_changed.emit()

    def _region_pickimage(self, page: int, box: tuple) -> None:
        x0, y0, x1, y1 = box
        image = pdfimages.hit(self.content_images(page), (x0 + x1) / 2 if x1 > x0 else x0,
                              (y0 + y1) / 2 if y1 > y0 else y0)
        self.select_content_image(page, image.index if image is not None else None)

    def _region_imagecrop(self, page: int, box: tuple) -> None:
        target, self._crop_target = self._crop_target, None
        self.set_tool("pickimage")
        if target is None or target[0] != page:
            self.notice.emit("Den Rahmen bitte auf der Seite des Bilds aufziehen")
            return
        if abs(box[2] - box[0]) < 2 or abs(box[3] - box[1]) < 2:
            self.notice.emit("Zuschneiden abgebrochen – bitte einen Rahmen aufziehen")
            return
        self.crop_content_image(page, target[1], rect=box)

    def image_key(self, event) -> bool:
        """Tasten im Werkzeug „Bilder im Inhalt“: Entf löscht, Esc hebt die Auswahl auf."""
        image = self.selected_image
        if image is None:
            return False
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_content_image(image.page, image.index)
            return True
        if event.key() == Qt.Key.Key_Escape:
            self.select_content_image(image.page, None)
            return True
        return False

    def _content_image(self, page: int, index: int):
        images = self.content_images(page)
        if not 0 <= index < len(images):
            self.notice.emit("Dieses Bild gibt es nicht mehr")
            return None
        return images[index]

    def _content_image_menu(self, menu, image) -> None:
        menu.addSeparator()
        sub = menu.addMenu(f"Bild auf der Seite ({image.width_px} × {image.height_px} px)")
        style_menu(sub)
        sub.addAction("Exportieren …", lambda: self.export_content_image(image.page, image.index))
        if self.edit_block_reason():
            return
        replace = sub.addAction("Ersetzen …", lambda: self.replace_content_image(image.page, image.index))
        replace.setEnabled(image.can_replace_here or image.can_replace_all)
        crop = sub.addAction("Zuschneiden …", lambda: self.crop_content_image(image.page, image.index))
        crop.setEnabled(image.can_crop)
        delete = sub.addAction("Löschen  Entf", lambda: self.delete_content_image(image.page, image.index))
        delete.setEnabled(image.can_delete)

    def export_content_image(self, page: int, index: int, target: Path | str | None = None) -> Path | None:
        try:
            raw, ext = pdfimages.export_content_image(self.data, page, index)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return None
        return self._save_image_bytes(raw, ext, f"{self.path.stem}-S{page + 1}-Bild{index + 1}", target)

    def export_all_images(self, folder: Path | str | None = None, wait: bool = False) -> list[Path]:
        """Alle Bilder im Hintergrund in einen Ordner exportieren (nie überschreiben: Name (2), (3) …)."""
        if self.data is None:
            return []
        if folder is None:
            from PySide6.QtWidgets import QFileDialog
            chosen = QFileDialog.getExistingDirectory(self, "Bilder exportieren nach", str(self.path.parent))
            if not chosen:
                return []
            folder = chosen
        folder, data, stem, result = Path(folder), self.data, self.path.stem, {}

        def unique(name: str) -> Path:
            target = folder / name
            number = 2
            while target.exists():
                target = folder / f"{Path(name).stem} ({number}){Path(name).suffix}"
                number += 1
            return target

        def work(ctx):
            images = pdfimages.all_images(data, progress=lambda i, n: ctx.step(i, n * 2, f"Seite {i} von {n} …"))
            written, skipped = [], 0
            for number, image in enumerate(images):
                if ctx.cancelled:
                    break
                ctx.step(len(images) + number, len(images) * 2, f"Bild {number + 1} von {len(images)} …")
                try:
                    raw, ext = pdfimages.export_content_image(data, image.page, image.index)
                    target = unique(f"{stem}-S{image.page + 1}-Bild{image.index + 1}.{ext}")
                    target.write_bytes(raw)
                    written.append(target)
                except (pdfpages.PdfEditError, OSError):
                    skipped += 1
            return written, skipped

        def done(outcome) -> None:
            written, skipped = outcome
            result["paths"] = written
            extra = f", {skipped} nicht lesbar" if skipped else ""
            self.notice.emit(f"{len(written)} Bild(er) exportiert nach {folder.name}{extra}" if written or skipped
                             else "Keine Bilder im Seiteninhalt")

        from notex.ui.tasks import run_task
        worker = run_task(self, "Bilder exportieren …", work, on_done=done,
                          on_error=lambda message: self.notice.emit(message), show_dialog=not wait)
        if wait:                                        # Tests: synchron abwarten
            worker.wait()
            from PySide6.QtCore import QCoreApplication
            for _ in range(50):
                QCoreApplication.processEvents()
                if "paths" in result:
                    break
        return list(result.get("paths", []))

    def replace_content_image(self, page: int, index: int, path: Path | str | None = None,
                              everywhere: bool | None = None, keep_aspect: bool = True) -> bool:
        image = self._content_image(page, index)
        if image is None:
            return False
        if not (image.can_replace_here or image.can_replace_all):
            self.notice.emit("Inline-Bilder lassen sich nicht ersetzen – nur exportieren oder löschen")
            return False
        if everywhere is None:
            if image.can_replace_here and image.can_replace_all:
                from dataclasses import replace
                from notex.ui.pdf_images_ui import ReplaceImageDialog
                counted = replace(image, uses=pdfimages.uses_of(self.data, page, index))
                dialog = ReplaceImageDialog(self, counted)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return False
                everywhere, keep_aspect = dialog.values()
            else:
                everywhere = not image.can_replace_here
        content = self._read_picture(path, "Bild ersetzen")
        if content is None or not self._ensure_editing():
            return False
        return self._run(pdfimages.replace_content_image, page, index, content, everywhere, keep_aspect, page=page)

    def crop_content_image(self, page: int, index: int, rect: tuple | None = None,
                           margins: tuple | None = None) -> bool:
        """Zuschneiden: Rahmen (Ansichts-Punkte) oder Ränder in Anteilen (links, oben, rechts, unten); ohne beides
        fragt ein Dialog – oder man zieht danach einen Rahmen auf der Seite auf."""
        image = self._content_image(page, index)
        if image is None:
            return False
        if not image.can_crop:
            self.notice.emit("Dieses Bild lässt sich nicht zuschneiden (Inline-Bild, Maske oder in einer Gruppe)")
            return False
        if rect is None and margins is None:
            from notex.ui.pdf_images_ui import CropImageDialog
            dialog = CropImageDialog(self, image)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if dialog.drag:
                if not self._ensure_editing():
                    return False
                self._crop_target = (page, index)
                self.set_tool("imagecrop")
                self.selected_image = image
                self.canvas.viewport().update()
                self.notice.emit("Rahmen über dem Bild aufziehen – Esc bricht ab")
                return False
            margins = dialog.margins()
        if rect is None:
            left, top, right, bottom = margins
            x0, y0, x1, y1 = image.rect
            w, h = x1 - x0, y1 - y0
            rect = (x0 + left * w, y0 + top * h, x1 - right * w, y1 - bottom * h)
        if not self._ensure_editing():
            return False
        self.selected_image = image
        return self._run(pdfimages.crop_content_image, page, index, tuple(rect), page=page)

    def delete_content_image(self, page: int, index: int, confirmed: bool | None = None) -> bool:
        image = self._content_image(page, index)
        if image is None:
            return False
        if not image.can_delete:
            self.notice.emit("Bilder in Gruppen lassen sich nur exportieren oder überall ersetzen")
            return False
        if confirmed is None:
            from notex.ui import dialogs
            confirmed = dialogs.confirm(self, "Bild löschen?", f"{image.describe()} von Seite {page + 1} entfernen?",
                                        yes="Löschen", danger=True,
                                        informative="Rückgängig mit Ctrl+Z, solange nicht gespeichert ist.")
        if not confirmed or not self._ensure_editing():
            return False
        self.selected_image = None
        return self._run(pdfimages.delete_content_image, page, index, page=page)

    def show_images(self):
        """Liste aller Bilder im Seiteninhalt (nicht modal). Exportieren geht auch im Lesemodus."""
        from notex.ui.pdf_images_ui import ImagesDialog
        dialog = getattr(self, "_images_dialog", None)
        if dialog is None:
            dialog = ImagesDialog(self)
            dialog.show_requested.connect(self._show_image)
            dialog.export_requested.connect(lambda p, i: self.export_content_image(p, i))
            dialog.export_all_requested.connect(lambda: self.export_all_images())
            dialog.replace_requested.connect(lambda p, i: self.replace_content_image(p, i))
            dialog.crop_requested.connect(lambda p, i: self.crop_content_image(p, i))
            dialog.delete_requested.connect(lambda p, i: self.delete_content_image(p, i))
            self._images_dialog = dialog
        self._refresh_images(force=True)
        dialog.show()
        dialog.raise_()
        return dialog

    def _refresh_images(self, force: bool = False) -> None:
        dialog = getattr(self, "_images_dialog", None)
        if dialog is None or (not force and not dialog.isVisible()) or self.data is None:
            return
        try:
            items = pdfimages.all_images(self.data)
        except Exception:                               # noqa: BLE001
            items = []
        dialog.set_items(items, editable=not self.edit_block_reason())

    def _show_image(self, page: int, index: int) -> None:
        image = self._content_image(page, index)
        if image is None:
            return
        if self.canvas.tool not in ("pickimage", "imagecrop"):
            self.set_tool("pickimage")
        self.selected_image = image
        self.canvas.go_to(page, QPointF(image.rect[0], max(image.rect[1], 0.1)))
        self.canvas.viewport().update()

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
        """Doppelklick/„Bearbeiten …“: je nach Art Text mit Stil, Notiz/Kommentar, Unterschrift ersetzen, Feld, Bild."""
        if obj.kind == "text":
            return self.edit_text_object(obj)
        if obj.kind == "image":
            return self.image_properties(obj)
        if obj.kind == "signature":
            return self.replace_signature(obj)
        if obj.kind == "diagram" and hasattr(self, "edit_diagram"):
            return self.edit_diagram(obj)
        if obj.kind == "field":
            return self.field_properties(obj)
        if obj.kind == "link":
            return self.edit_link(obj)
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
                         size: float | None = None, properties: dict | None = None) -> bool:
        info = next((f for f in self.fields if f.name == obj.field), None)
        if info is None:
            return False
        if new_name is None and multiline is None and size is None and properties is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import FieldDialog
            dialog = FieldDialog(self, info, self.doc.pageCount())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            new_name, multiline, size = dialog.values()
            properties = dialog.properties()
        if not self._ensure_editing():
            return False
        properties = properties or {}

        def change(data):
            data = pdfforms.update_field(data, info.name, new_name, multiline, size)
            if properties:
                name = info.name
                if new_name and new_name.strip() != info.name.split(".")[-1]:
                    name = (info.name.rsplit(".", 1)[0] + "." if "." in info.name else "") + new_name.strip()
                data = pdffieldtypes.set_properties(data, name, **properties)
            return data
        return self._run(change, page=obj.page)

    # ---- Formular: weitere Feldtypen, Tab-Reihenfolge, Daten (Block V5) -------------------------------------------
    show_tab_numbers = False

    def _field_menu(self) -> None:
        from notex.theme.icons import icon
        menu = style_menu(QMenu(self))
        for key, icon_name, label in FIELD_LABELS:
            action = menu.addAction(icon(icon_name), label, lambda k=key: self.set_tool(k))
            action.setCheckable(True)
            action.setChecked(self.canvas.tool == key)
        button = self.tool_buttons["formfield"]
        self.set_tool(self.canvas.tool)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _unique_field_name(self, base: str) -> str:
        names = {f.name for f in self.fields}
        if base not in names:
            return base
        number = 2
        while f"{base}{number}" in names:
            number += 1
        return f"{base}{number}"

    def _region_radio(self, page: int, box: tuple) -> None:
        self.add_form_field("radio", page, box)

    def _region_combo(self, page: int, box: tuple) -> None:
        self.add_form_field("combo", page, box)

    def _region_listbox(self, page: int, box: tuple) -> None:
        self.add_form_field("listbox", page, box)

    def _region_button(self, page: int, box: tuple) -> None:
        self.add_form_field("button", page, box)

    def add_form_field(self, kind: str, page: int, box: tuple, values: dict | None = None) -> bool:
        """Neues Feld im aufgezogenen Bereich; ohne `values` fragt ein Dialog (Name, Einträge, Aktion)."""
        x0, y0, x1, y1 = box
        defaults = {"radio": (140, 60), "combo": (180, 20), "listbox": (180, 70), "button": (120, 24)}[kind]
        if x1 - x0 < 8 or y1 - y0 < 8:
            box = (x0, y0, x0 + defaults[0], y0 + defaults[1])
        if values is None:
            from notex.ui.pdf_forms_ui import NewFieldDialog
            base = {"radio": "Auswahl", "combo": "Dropdown", "listbox": "Liste", "button": "Schaltfläche"}[kind]
            dialog = NewFieldDialog(self, kind, self._unique_field_name(base), self.doc.pageCount(), page)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            values = dialog.values()
        if not self._ensure_editing():
            return False
        name = values["name"]
        if kind == "radio":
            rects = pdffieldtypes.layout_radios(box, len(values["options"]), values.get("vertical", True))
            done = self._run(pdffieldtypes.add_radio_group, page, rects, name, values["options"], page=page)
        elif kind in ("combo", "listbox"):
            done = self._run(pdffieldtypes.add_choice, page, tuple(box), name, values["options"], kind == "combo",
                             "", bool(values.get("editable")), page=page)
        else:
            done = self._run(pdffieldtypes.add_button, page, tuple(box), name, values["label"],
                             values.get("action", "reset"), int(values.get("target_page", 0)), page=page)
        if done:
            self.set_tool("select")
        return done

    def _taborder_menu(self) -> None:
        self.taborder_button.setChecked(self.show_tab_numbers)
        menu = style_menu(QMenu(self))
        numbers = menu.addAction("Nummern einblenden", lambda: self.set_tab_numbers(not self.show_tab_numbers))
        numbers.setCheckable(True)
        numbers.setChecked(self.show_tab_numbers)
        menu.addSeparator()
        menu.addAction("Reihenfolge bearbeiten …", lambda: self.edit_tab_order())
        menu.addAction("Nach Zeilen ordnen", lambda: self.edit_tab_order(mode="rows"))
        menu.addAction("Nach Spalten ordnen", lambda: self.edit_tab_order(mode="columns"))
        button = self.taborder_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def set_tab_numbers(self, on: bool | None = None) -> None:
        self.show_tab_numbers = (not self.show_tab_numbers) if on is None else bool(on)
        self.taborder_button.setChecked(self.show_tab_numbers)
        self.canvas.viewport().update()

    def edit_tab_order(self, page: int | None = None, order: list[str] | None = None, mode: str | None = None) -> bool:
        """Tab-Reihenfolge der Seite: eigene Liste (`order`), „rows“ oder „columns“; ohne Angaben fragt ein Dialog."""
        page = self.canvas.current_page if page is None else page
        if order is None and mode is None:
            names = pdffieldtypes.tab_order(self.data, page) if self.data else []
            if not names:
                self.notice.emit("Auf dieser Seite gibt es keine Formularfelder")
                return False
            from notex.ui.pdf_forms_ui import TabOrderDialog
            dialog = TabOrderDialog(self, page, names)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if dialog.mode == "custom":
                order = dialog.order()
            else:
                mode = dialog.mode
        if not self._ensure_editing():
            return False
        self.set_tab_numbers(True)
        if mode is not None:
            return self._run(pdffieldtypes.auto_tab_order, page, mode, page=page)
        return self._run(pdffieldtypes.set_tab_order, page, list(order), page=page)

    def _formdata_menu(self, anchor) -> None:
        menu = style_menu(QMenu(self))
        for kind, label in (("json", "Daten als JSON exportieren …"), ("xfdf", "Daten als XFDF exportieren …"),
                            ("fdf", "Daten als FDF exportieren …")):
            menu.addAction(label, lambda k=kind: self.export_form_data(k))
        menu.addAction("Daten importieren (JSON, XFDF, FDF) …", lambda: self.import_form_data())
        menu.addSeparator()
        menu.addAction("Antworten sammeln (mehrere PDFs → CSV) …", lambda: self.collect_responses())
        menu.addAction("Formular zurücksetzen (Standardwerte)", lambda: self.reset_form())
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def export_form_data(self, kind: str, target: Path | str | None = None) -> Path | None:
        if not self.data or kind not in ("json", "xfdf", "fdf"):
            return None
        if not self.fields:
            self.notice.emit("Dieses PDF hat keine Formularfelder")
            return None
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getSaveFileName(self, "Formulardaten exportieren",
                                                     str(self.path.with_name(f"{self.path.stem}-daten.{kind}")),
                                                     f"{kind.upper()} (*.{kind})")
            if not chosen:
                return None
            target = chosen
        target = Path(target)
        export = {"json": pdfformdata.export_json, "xfdf": pdfformdata.export_xfdf_fields,
                  "fdf": pdfformdata.export_fdf}[kind]
        try:
            target.write_bytes(export(self.data, self.path.name))
        except (OSError, pdfpages.PdfEditError) as error:
            self.notice.emit(f"Nicht exportiert: {error}")
            return None
        self.notice.emit(f"Formulardaten exportiert: {target.name}")
        return target

    def import_form_data(self, source: Path | str | None = None) -> bool:
        if source is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getOpenFileName(self, "Formulardaten importieren", str(self.path.parent),
                                                     "Formulardaten (*.json *.xfdf *.fdf *.xml)")
            if not chosen:
                return False
            source = chosen
        try:
            raw = Path(source).read_bytes()[: pdfformdata.MAX_FILE + 1]
        except OSError as error:
            self.notice.emit(f"Nicht lesbar: {error}")
            return False
        if not self._ensure_editing():
            return False
        summary = []

        def run(data):
            result = pdfformdata.import_data(data, raw)
            summary.append(result.summary())
            return result.data
        done = self._run(run)
        if done and summary:
            self.notice.emit(summary[0])
        return done

    def reset_form(self) -> bool:
        if not self.fields or not self._ensure_editing():
            return False
        return self._run(pdffieldtypes.reset_values)

    def collect_responses(self, sources: list | None = None, target: Path | str | None = None) -> Path | None:
        """Mehrere ausgefüllte Kopien dieses Formulars → eine CSV-Tabelle (eine Zeile je Datei)."""
        if sources is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getOpenFileNames(self, "Ausgefüllte PDFs wählen", str(self.path.parent),
                                                      "PDF (*.pdf)")
            if not chosen:
                return None
            sources = chosen
        files = []
        for source in sources:
            try:
                files.append((Path(source).name, Path(source).read_bytes()))
            except OSError as error:
                self.notice.emit(f"Nicht lesbar: {Path(source).name} ({error})")
        text, problems = pdfformdata.collect(files)
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getSaveFileName(self, "Antworten speichern",
                                                     str(self.path.with_name(f"{self.path.stem}-antworten.csv")),
                                                     "CSV (*.csv)")
            if not chosen:
                return None
            target = chosen
        target = Path(target)
        try:
            target.write_text(text, encoding="utf-8-sig", newline="")
        except OSError as error:
            self.notice.emit(f"Nicht gespeichert: {error}")
            return None
        rows = len(files) - len(problems)
        self.notice.emit(f"{rows} Antworten gesammelt" + (f" – {len(problems)} Dateien übersprungen" if problems
                                                         else ""))
        return target

    # ---- Diagramme ---------------------------------------------------------------------------------------------
    def page_background(self, page: int, without: int | None = None, scale: float = 2.0):
        """Seite als Bild (für den Hintergrund im Diagramm-Editor) – optional ohne die Anmerkung `without`."""
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QColor, QImage, QPainter
        from PySide6.QtPdf import QPdfDocumentRenderOptions
        data = self.data
        try:
            if without is not None:
                data = pdfannot.delete_annotation(data, page, without)
            raw = self._display_bytes(data)
        except pdfpages.PdfEditError:
            raw = self.data
        doc = QPdfDocument()
        buffer = QBuffer()
        buffer.setData(QByteArray(raw))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        doc.load(buffer)
        size = doc.pagePointSize(page)
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        rendered = doc.render(page, QSize(round(size.width() * scale), round(size.height() * scale)), options)
        image = QImage(rendered.size(), QImage.Format.Format_RGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        painter.drawImage(0, 0, rendered)
        painter.end()
        doc.close()
        buffer.close()
        return image, scale

    def insert_diagram(self, page: int, top_left: tuple[float, float], diagram=None, size=(320.0, 200.0)) -> bool:
        """Diagramm-Editor für einen neuen Bereich öffnen (oder `diagram` direkt einfügen)."""
        offset = (0.0, 0.0)
        if diagram is None:
            from notex.core.diagram.model import Diagram
            from notex.ui.diagram_editor import DiagramEditor
            image, scale = self.page_background(page)
            editor = DiagramEditor(self, Diagram(*size), image, scale, top_left)
            if editor.exec() != QDialog.DialogCode.Accepted:
                return False
            diagram, offset = editor.result_diagram()
        if not diagram.shapes and not diagram.connectors:
            self.notice.emit("Leeres Diagramm – nichts eingefügt")
            return False
        if not self._ensure_editing():
            return False
        corner = (top_left[0] + offset[0], top_left[1] + offset[1])
        return self._run(pdfdiagram.add_diagram, page, corner, diagram, page=page)

    def edit_diagram(self, obj, diagram=None) -> bool:
        """Doppelklick auf ein Diagramm: Editor mit dem gespeicherten Modell; danach an derselben Stelle ersetzen."""
        try:
            current = pdfdiagram.read_diagram(self.data, obj.page, obj.index)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return False
        offset = (0.0, 0.0)
        if diagram is None:
            from notex.ui.diagram_editor import DiagramEditor
            image, scale = self.page_background(obj.page, without=obj.index)
            editor = DiagramEditor(self, current, image, scale, (obj.rect[0], obj.rect[1]))
            if editor.exec() != QDialog.DialogCode.Accepted:
                return False
            diagram, offset = editor.result_diagram()
        if not self._ensure_editing():
            return False
        if not diagram.shapes and not diagram.connectors:
            return self.delete_object(obj)
        corner = (obj.rect[0] + offset[0], obj.rect[1] + offset[1])
        return self._run(pdfdiagram.update_diagram, obj.page, obj.index, diagram, corner, page=obj.page)

    def import_drawio(self, path: str | None = None, page: int | None = None, file_page: int | None = None,
                      edit: bool = True) -> bool:
        """draw.io-Datei als Diagramm auf die (aktuelle) Seite setzen – vorher im Editor anpassbar."""
        from PySide6.QtWidgets import QFileDialog
        from notex.ui.diagram_editor import read_drawio_file
        if self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "draw.io-Datei einfügen", str(self.path.parent),
                                                  "draw.io (*.drawio *.xml *.svg *.png *.html)")
            if not path:
                return False
        diagram = read_drawio_file(self, path, file_page)
        if diagram is None:
            return False
        page = self.canvas.current_page if page is None else page
        top_left = (36.0, 36.0)
        if not edit:
            return self.insert_diagram(page, top_left, diagram)
        from notex.ui.diagram_editor import DiagramEditor
        image, scale = self.page_background(page)
        editor = DiagramEditor(self, diagram, image, scale, top_left)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return False
        result, offset = editor.result_diagram()
        return self.insert_diagram(page, (top_left[0] + offset[0], top_left[1] + offset[1]), result)

    def export_drawio(self, obj=None, path: str | None = None) -> bool:
        """Diagramm (ausgewählt oder `obj`) als .drawio speichern – zum Weiterbearbeiten in draw.io."""
        from PySide6.QtWidgets import QFileDialog
        from notex.ui.diagram_editor import write_drawio_file
        obj = obj or self.objects.selected
        if obj is None or obj.kind != "diagram":
            self.notice.emit("Erst ein Diagramm anklicken (Werkzeug „Auswählen“)")
            return False
        try:
            diagram = pdfdiagram.read_diagram(self.data, obj.page, obj.index)
        except pdfpages.PdfEditError as error:
            self.notice.emit(str(error))
            return False
        if path is None:
            suggestion = self.path.parent / f"{self.path.stem}_diagramm.drawio"
            path, _ = QFileDialog.getSaveFileName(self, "Diagramm als draw.io-Datei speichern", str(suggestion),
                                                  "draw.io (*.drawio)")
            if not path:
                return False
        if write_drawio_file(self, path, diagram):
            self.notice.emit(f"Gespeichert: {Path(path).name}")
            return True
        return False

    def _region_diagram(self, page: int, box: tuple) -> None:
        x0, y0, x1, y1 = box
        size = (x1 - x0, y1 - y0) if x1 - x0 >= 60 and y1 - y0 >= 40 else (320.0, 200.0)
        self.insert_diagram(page, (x0, y0), size=size)

    # ---- Felderkennung -----------------------------------------------------------------------------------------
    def page_words(self, page: int) -> list:
        """Wörter der Seite mit Rahmen (PDFium) – Unterstrich-Folgen und Kästchen-Zeichen als eigene Wörter."""
        out = []
        text = self.doc.getAllText(page).text()
        for match in pdfdetect.TOKEN_RE.finditer(text):
            selection = self.doc.getSelectionAtIndex(page, match.start(), len(match.group()))
            polygons = selection.bounds()
            if not polygons:
                continue
            rect = polygons[0].boundingRect()
            for polygon in polygons[1:]:
                rect = rect.united(polygon.boundingRect())
            out.append(pdfdetect.Word(match.group(), (rect.left(), rect.top(), rect.right(), rect.bottom())))
        return out

    def detect_fields(self, pages: list[int] | None = None, apply: bool = True) -> list:
        """Formularfelder erkennen und (apply) in einem Rückgängig-Schritt anlegen."""
        if self.edit_block_reason():
            self.notice.emit(self.edit_block_reason())
            return []
        pages = list(range(min(self.doc.pageCount(), 50))) if pages is None else pages
        found = []
        for page in pages:
            existing = [o.rect for o in self.objects.objects(page) if o.kind in ("field", "text", "signature",
                                                                                  "diagram", "stamp")]
            size = self.doc.pagePointSize(page)
            try:
                graphics = pdfdetect.page_graphics(self.data, page)
            except pdfpages.PdfEditError:
                graphics = pdfdetect.Graphics()
            found += pdfdetect.detect(self.page_words(page), graphics, (size.width(), size.height()), existing, page)
        pdfdetect.unique_against(found, {f.name for f in self.fields})
        if not apply:
            return found
        if not found:
            self.notice.emit("Keine Formularfelder erkannt – Felder lassen sich mit „Textfeld“ selbst aufziehen")
            return []
        if not self._ensure_editing():
            return []
        specs = [{"page": s.page, "rect": s.rect, "name": s.name, "kind": s.kind, "multiline": s.multiline,
                  "border": False} for s in found]
        if not self._run(pdfforms.add_fields, specs, page=found[0].page):
            return []
        names = ", ".join(s.name for s in found[:6]) + (" …" if len(found) > 6 else "")
        self.notice.emit(f"{len(found)} Feld(er) erkannt: {names} – reinklicken zum Ausfüllen")
        self.canvas.overlays = [o for o in self.canvas.overlays if o[2] != "field"] + \
            [(s.page, QRectF(s.rect[0], s.rect[1], s.rect[2] - s.rect[0], s.rect[3] - s.rect[1]), "field")
             for s in found]
        self.canvas.viewport().update()
        QTimer.singleShot(2500, self._clear_field_overlay)
        return found

    def is_signature_placeholder(self, info) -> bool:
        return getattr(info, "placeholder", "") == "signature"

    def sign_placeholder(self, obj, value=None) -> bool:
        """Klick in einen Unterschrifts-Platzhalter: Unterschrift zeichnen/wählen und genau dort einsetzen."""
        if value is None:
            from PySide6.QtWidgets import QDialog
            from notex.ui.pdf_edit import SignatureDialog
            dialog = SignatureDialog(self, self.path.parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            value = dialog.result_value()
        if not self._ensure_editing():
            return False
        page, rect, name = obj.page, obj.rect, obj.field
        if value[0] == "image":
            from notex.ui.pdf_edit import image_to_bytes
            image = value[1]
            target = pdfforms.fit_rect(rect, image.width() / max(1, image.height()))
            width, height, rgb, alpha = image_to_bytes(image)
            return self._run(lambda d: pdfforms.add_image(pdfforms.remove_field(d, name), page, target, width,
                                                          height, rgb, alpha), page=page)
        _kind, strokes, aspect = value
        target = pdfforms.fit_rect(rect, aspect)
        return self._run(lambda d: pdfforms.add_strokes(pdfforms.remove_field(d, name), page, target, strokes),
                         page=page)

    # ---- Formulare und Unterschrift ---------------------------------------------------------------------------
    def refresh_form(self) -> None:
        try:
            self.fields = pdfforms.list_fields(self.data) if self.data is not None and not self.encrypted else []
        except Exception:                                  # noqa: BLE001
            self.fields = []
        if not self.form_panel.isHidden():
            self.form_panel.set_fields(self.fields)

    # ---- Kommentare (Block V4) -------------------------------------------------------------------------------
    def comments(self) -> list:
        try:
            return pdfcomments.list_comments(self.data) if self.data else []
        except Exception:                               # noqa: BLE001 – unlesbar: leere Liste
            return []

    def set_comments_visible(self, on: bool | None = None) -> None:
        if on is None:                                  # Palette/Kürzel: umschalten
            on = self.comments_panel.isHidden()
        self.comments_button.blockSignals(True)
        self.comments_button.setChecked(on)
        self.comments_button.blockSignals(False)
        self.comments_panel.setVisible(on)
        if on:
            self._refresh_comments()

    def _refresh_comments(self) -> None:
        if hasattr(self, "comments_panel") and not self.comments_panel.isHidden():
            self.comments_panel.current_page = self.canvas.current_page
            self.comments_panel.set_comments(self.comments())

    def show_comment(self, page: int, rect: tuple) -> None:
        x0, y0, x1, y1 = rect
        self.canvas.go_to(page, QPointF(x0, max(0.0, y0 - 40)))
        self.canvas.overlays = [o for o in self.canvas.overlays if o[2] != "field"] + \
            [(page, QRectF(x0, y0, x1 - x0, y1 - y0), "field")]
        self.canvas.viewport().update()
        QTimer.singleShot(1500, self._clear_field_overlay)

    def reply_comment(self, page: int, index: int, text: str | None = None) -> bool:
        if text is None:
            from notex.ui.pdf_edit import TextDialog
            dialog = TextDialog(self, "Antworten", "")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            text = dialog.values()[0]
        if not text.strip() or not self._ensure_editing():
            return False
        return self._run(pdfcomments.add_reply, page, index, text, self.stamp_user(), page=page)

    def set_comment_status(self, page: int, index: int, status: str) -> bool:
        if not self._ensure_editing():
            return False
        return self._run(pdfcomments.set_status, page, index, status, self.stamp_user(), page=page)

    def delete_comment(self, page: int, index: int, confirmed: bool | None = None) -> bool:
        if confirmed is None:
            from notex.ui import dialogs
            confirmed = dialogs.confirm(self, "Kommentar löschen?", "Diesen Kommentar samt Antworten löschen?",
                                        yes="Löschen", danger=True, informative="Rückgängig mit Ctrl+Z.")
        if not confirmed or not self._ensure_editing():
            return False
        return self._run(pdfannot.delete_annotation, page, index, page=page)

    def export_comments(self, kind: str, target: Path | str | None = None) -> Path | None:
        """Zusammenfassung als Markdown/CSV/PDF oder alle Kommentare als XFDF speichern."""
        suffix = {"md": ".md", "csv": ".csv", "pdf": ".pdf", "xfdf": ".xfdf"}.get(kind)
        if suffix is None or not self.data:
            return None
        stem = self.path.stem + ("" if kind == "xfdf" else "-kommentare")
        if target is None:
            from PySide6.QtWidgets import QFileDialog
            filters = {"md": "Markdown (*.md)", "csv": "CSV (*.csv)", "pdf": "PDF (*.pdf)", "xfdf": "XFDF (*.xfdf)"}
            chosen, _f = QFileDialog.getSaveFileName(self, "Kommentare exportieren",
                                                     str(self.path.with_name(stem + suffix)), filters[kind])
            if not chosen:
                return None
            target = chosen
        target = Path(target)
        if target.resolve() == self.path.resolve():
            self.notice.emit("Das würde das PDF selbst überschreiben – bitte einen anderen Namen wählen")
            return None
        try:
            if kind == "xfdf":
                target.write_bytes(xfdf.export_xfdf(self.data, self.path.name))
            elif kind == "csv":
                target.write_text(pdfcomments.summary_csv(self.comments()), encoding="utf-8-sig", newline="")
            else:
                text = pdfcomments.summary_markdown(self.comments(), self.path.name)
                if kind == "md":
                    target.write_text(text, encoding="utf-8")
                else:
                    from notex.core.export import ExportMeta
                    from notex.ui.export_service import export_pdf
                    export_pdf(target, text, "markdown", ExportMeta(title=f"Kommentare – {self.path.name}",
                                                                    author=self.stamp_user()))
        except (OSError, pdfpages.PdfEditError) as error:
            self.notice.emit(f"Nicht exportiert: {error}")
            return None
        self.notice.emit(f"Exportiert: {target.name}")
        return target

    def import_xfdf(self, source: Path | str | None = None) -> bool:
        """Kommentare aus einer XFDF-Datei übernehmen (ein Rückgängig-Schritt; gleiche Kommentare nicht doppelt)."""
        if source is None:
            from PySide6.QtWidgets import QFileDialog
            chosen, _f = QFileDialog.getOpenFileName(self, "Kommentare importieren", str(self.path.parent),
                                                     "XFDF (*.xfdf *.xml)")
            if not chosen:
                return False
            source = chosen
        try:
            raw = Path(source).read_bytes()[: xfdf.MAX_FILE + 1]
        except OSError as error:
            self.notice.emit(f"Nicht lesbar: {error}")
            return False
        if not self._ensure_editing():
            return False
        summary = []

        def run(data):
            result = xfdf.import_xfdf(data, raw)
            summary.append(result.summary())
            return result.data
        done = self._run(run)
        if done and summary:
            self.notice.emit(summary[0])
        return done

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
        self.undo_button.setEnabled(self.history.can_undo)
        self.redo_button.setEnabled(self.history.can_redo)
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
        menu.addAction("Leere Seite davor", lambda: self.insert_blank(min(pages), "neighbor"))
        menu.addAction("Leere Seite dahinter", lambda: self.insert_blank(max(pages) + 1, "neighbor"))
        menu.addAction(f"Ersetzen durch PDF … ({label})", lambda: self.replace_pages(min(pages), max(pages)))
        menu.addAction(f"Zuschneiden … ({label})", lambda: self.crop_pages(pages=pages))
        menu.addAction("Seitenbeschriftungen …", lambda: self.edit_page_labels())
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
        if not self.comments_panel.isHidden() and self.comments_panel.this_page.isChecked():
            self.comments_panel.current_page = index
            self.comments_panel.refresh()
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
        self.history.clear()
        self._load()
        self.canvas.go_to(page)
        self._update_edit_actions()

    def retheme(self) -> None:
        self.canvas.viewport().update()

    def shutdown(self) -> None:
        self.history.clear()                            # ausgelagerte Rückgängig-Schritte löschen
        self.doc.close()
