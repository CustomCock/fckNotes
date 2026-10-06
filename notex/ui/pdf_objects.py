"""Eingefügtes im PDF anfassen: auswählen, verschieben, Größe ändern, löschen, bearbeiten – und Formularfelder direkt
auf der Seite ausfüllen (Klick hinein).

`ObjectController` hängt am PDF-Tab: die Seitenansicht fragt ihn bei Maus/Tastatur zuerst (Werkzeug „Auswählen“)
und lässt ihn Auswahlrahmen und Griffe zeichnen. Geändert wird nur über den Qt-freien Kern (`notex.core.pdfobjects`),
jede Änderung ist ein Rückgängig-Schritt des Tabs.

Bedienung: Klick wählt aus (im Bearbeiten-Modus), Ziehen verschiebt, Griffe ändern die Größe (Unterschrift/Diagramm
behalten das Seitenverhältnis), Pfeiltasten schieben (Shift = 10 pt), Entf löscht, Doppelklick bearbeitet.
Ein Klick in ein Formularfeld öffnet die Eingabe direkt auf der Seite; Ziehen am Feld verschiebt es.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit

from notex.core import pdfobjects
from notex.core.pdfpages import PdfEditError
from notex.theme.tokens import COLORS

HANDLE = 4.0          # halbe Griffgröße in Pixeln
DRAG_START = 3.0      # ab so vielen Pixeln wird aus Klick ein Ziehen
HANDLES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")


def handle_points(rect) -> dict[str, tuple[float, float]]:
    x0, y0, x1, y1 = rect
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return {"tl": (x0, y0), "t": (xm, y0), "tr": (x1, y0), "r": (x1, ym), "br": (x1, y1), "b": (xm, y1),
            "bl": (x0, y1), "l": (x0, ym)}


def resized(rect, handle: str, dx: float, dy: float):
    """Rahmen beim Ziehen an Griff `handle` um (dx, dy) Punkte; Mindestgröße 6 pt."""
    x0, y0, x1, y1 = rect
    if "l" in handle:
        x0 = min(x0 + dx, x1 - 6)
    if "r" in handle:
        x1 = max(x1 + dx, x0 + 6)
    if handle.startswith("t"):
        y0 = min(y0 + dy, y1 - 6)
    if handle.startswith("b"):
        y1 = max(y1 + dy, y0 + 6)
    return x0, y0, x1, y1


class ObjectController(QObject):
    def __init__(self, page) -> None:
        super().__init__(page)
        self.page = page                       # PdfPage
        self.canvas = page.canvas
        self._cache: dict[int, list] = {}
        self.selected = None                   # pdfobjects.PdfObject
        self._press = None                     # (Seite, x, y, Objekt, Griff, Pixel-Position)
        self._ghost = None                     # Rahmen während des Ziehens (Ansichts-Punkte)
        self.editor = None                     # offenes Eingabefeld auf der Seite
        self._editor_obj = None

    # ---- Daten ---------------------------------------------------------------------------------
    def invalidate(self) -> None:
        self._cache.clear()
        if self.selected is not None:          # nach jeder Änderung neu suchen (Positionen im /Annots bleiben)
            again = next((o for o in self.objects(self.selected.page) if o.index == self.selected.index), None)
            self.selected = again if again is not None and again.kind == self.selected.kind else None
        self.close_editor(commit=False)

    def objects(self, page: int) -> list:
        if page not in self._cache:
            try:
                self._cache[page] = pdfobjects.list_objects(self.page.data, page) if self.page.data else []
            except PdfEditError:
                self._cache[page] = []
        return self._cache[page]

    def at(self, page: int, x: float, y: float):
        return pdfobjects.hit(self.objects(page), x, y)

    def select(self, obj) -> None:
        self.selected = obj
        self.canvas.viewport().update()
        self.page.status_changed.emit()

    # ---- Maus (vom Canvas aufgerufen; True = verbraucht) ----------------------------------------
    def _handle_at(self, page: int, x: float, y: float) -> str:
        if self.selected is None or self.selected.page != page or not self.selected.movable or not self.page.editing:
            return ""
        tolerance = (HANDLE + 2) / max(self.canvas.layout_.scale, 0.01)
        for name, (hx, hy) in handle_points(self.selected.rect).items():
            if abs(hx - x) <= tolerance and abs(hy - y) <= tolerance:
                return name
        return ""

    def press(self, page: int, x: float, y: float, pixel: QPointF) -> bool:
        if self.editor is not None:
            self.close_editor(commit=True)
        handle = self._handle_at(page, x, y)
        obj = self.selected if handle else self.at(page, x, y)
        if obj is None:
            if self.selected is not None:
                self.select(None)
            return False
        if obj.kind in ("highlight", "underline", "strikeout") and not self.page.editing:
            return False                       # im Lesemodus: Text über Markierungen auswählen können
        self._press = (page, x, y, obj, handle, QPointF(pixel))
        if self.page.editing or obj.kind == "field":
            self.select(obj)
        return True

    def move(self, page: int, x: float, y: float, pixel: QPointF) -> bool:
        if self._press is None:
            return False
        start_page, sx, sy, obj, handle, start_pixel = self._press
        if self._ghost is None and (pixel - start_pixel).manhattanLength() < DRAG_START:
            return True
        if not obj.movable or not self.page._ensure_editing():
            return True
        dx, dy = x - sx, y - sy
        if handle:
            rect = resized(obj.rect, handle, dx, dy)
            if obj.keep_aspect and len(handle) == 2:
                rect = pdfobjects.fit_aspect(obj.rect, rect, handle)
        else:
            x0, y0, x1, y1 = obj.rect
            rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        self._ghost = rect
        self.canvas.viewport().update()
        return True

    def release(self, page: int, x: float, y: float) -> bool:
        if self._press is None:
            return False
        _page, _sx, _sy, obj, _handle, _pixel = self._press
        self._press = None
        ghost, self._ghost = self._ghost, None
        if ghost is not None:
            if max(abs(a - b) for a, b in zip(ghost, obj.rect)) > 0.4:
                self.page.set_object_rect(obj, ghost)
            self.canvas.viewport().update()
            return True
        if obj.kind == "field":
            QTimer.singleShot(0, lambda o=obj: self.activate_field(o))
        return True

    def double_click(self, page: int, x: float, y: float) -> bool:
        obj = self.at(page, x, y)
        if obj is None or obj.kind == "field":
            return obj is not None
        self.page.edit_object(obj)
        return True

    def key(self, event) -> bool:
        if self.selected is None or not self.page.editing or self.editor is not None:
            return False
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            obj, self.selected = self.selected, None
            self.page.delete_object(obj)
            return True
        if event.key() == Qt.Key.Key_Escape:
            self.select(None)
            return True
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                 Qt.Key.Key_Down: (0, step)}
        if event.key() in moves and self.selected.movable:
            dx, dy = moves[event.key()]
            x0, y0, x1, y1 = self.selected.rect
            self.page.set_object_rect(self.selected, (x0 + dx, y0 + dy, x1 + dx, y1 + dy))
            return True
        return False

    # ---- Zeichnen -----------------------------------------------------------------------------------
    def paint(self, painter, page_index: int, target: QRectF, scale: float) -> None:
        def view(rect) -> QRectF:
            x0, y0, x1, y1 = rect
            return QRectF(target.x() + x0 * scale, target.y() + y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)

        obj = self.selected
        if obj is None or obj.page != page_index:
            return
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(COLORS.accent), 1.2, Qt.PenStyle.DashLine))
        painter.drawRect(view(obj.rect))
        if self._ghost is not None:
            painter.setPen(QPen(QColor(COLORS.accent), 1.6))
            painter.setBrush(QColor(122, 138, 158, 40))
            painter.drawRect(view(self._ghost))
        if obj.movable and self.page.editing and self._ghost is None:
            painter.setPen(QPen(QColor(COLORS.accent), 1))
            painter.setBrush(QColor("#ffffff"))
            for hx, hy in handle_points(obj.rect).values():
                px, py = target.x() + hx * scale, target.y() + hy * scale
                painter.drawRect(QRectF(px - HANDLE, py - HANDLE, 2 * HANDLE, 2 * HANDLE))
        painter.restore()

    def cursor_for(self, page: int, x: float, y: float):
        handle = self._handle_at(page, x, y)
        if handle:
            return {"t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor}[handle]
        obj = self.at(page, x, y)
        if obj is None:
            return None
        if obj.kind == "field":
            return Qt.CursorShape.PointingHandCursor if obj.field_kind != "text" else Qt.CursorShape.IBeamCursor
        return Qt.CursorShape.SizeAllCursor if obj.movable and self.page.editing else Qt.CursorShape.ArrowCursor

    # ---- Formularfelder direkt ausfüllen ------------------------------------------------------------
    def field_info(self, name: str):
        return next((f for f in self.page.fields if f.name == name), None)

    def activate_field(self, obj) -> None:
        """Klick in ein Feld: Text → Eingabe auf der Seite, Kästchen → umschalten, Option → wählen."""
        info = self.field_info(obj.field)
        if info is None or info.read_only:
            if info is not None:
                self.page.notice.emit(f"„{info.name}“ ist schreibgeschützt")
            return
        if getattr(self.page, "is_signature_placeholder", lambda _i: False)(info):
            self.page.sign_placeholder(obj)
            return
        if info.kind == "checkbox":
            self.page.fill_form({info.name: not info.checked})
            return
        if info.kind == "radio":
            self.page.fill_form({info.name: "" if info.value == obj.state else obj.state})
            return
        if info.kind in ("text", "choice"):
            if not self.page._ensure_editing():          # Bearbeiten-Leiste erscheint jetzt, nicht beim Übernehmen
                return
            self.open_editor(obj, info)

    def _viewport_rect(self, obj) -> QRectF:
        layout = self.canvas.layout_
        page = layout.pages[obj.page]
        dx, dy = self.canvas.horizontalScrollBar().value(), self.canvas.verticalScrollBar().value()
        x0, y0, x1, y1 = obj.rect
        scale = layout.scale
        return QRectF(page.x - dx + x0 * scale, page.y - dy + y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale)

    def open_editor(self, obj, info) -> None:
        self.close_editor(commit=False)
        rect = self._viewport_rect(obj).toRect()
        viewport = self.canvas.viewport()
        if info.kind == "choice":
            editor = QComboBox(viewport)
            editor.setEditable(False)
            for option in info.options:
                editor.addItem(option, option)
            editor.setCurrentIndex(max(0, editor.findData(info.value)))
            editor.activated.connect(lambda _i: self.close_editor(commit=True))
        elif info.multiline:
            editor = QPlainTextEdit(viewport)
            editor.setPlainText(info.value)
            editor.setToolTip("Ctrl+Enter übernimmt, Esc verwirft")
        else:
            editor = QLineEdit(viewport)
            editor.setText(info.value)
            editor.selectAll()
            editor.returnPressed.connect(lambda: self.close_editor(commit=True))
        editor.setObjectName("PdfFieldEditor")
        font = editor.font()
        font.setPixelSize(max(9, min(28, round(rect.height() * (0.18 if info.multiline else 0.6)))))
        editor.setFont(font)
        editor.setStyleSheet("background: #ffffff; color: #1a1a1a; border: 1.5px solid %s;" % COLORS.accent)
        editor.setGeometry(rect.adjusted(-1, -1, 1, 1))
        editor.installEventFilter(self)
        editor.show()
        editor.setFocus()
        self.editor, self._editor_obj = editor, (obj, info)

    def reposition_editor(self) -> None:
        if self.editor is None or self._editor_obj is None or self.canvas.layout_ is None:
            return
        obj = self._editor_obj[0]
        if obj.page >= len(self.canvas.layout_.pages):
            return
        rect = self._viewport_rect(obj).toRect()
        self.editor.setGeometry(rect.adjusted(-1, -1, 1, 1))

    def eventFilter(self, watched, event) -> bool:
        from PySide6.QtCore import QEvent
        if watched is self.editor:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    QTimer.singleShot(0, lambda: self.close_editor(commit=False))
                    return True
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                        event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    QTimer.singleShot(0, lambda: self.close_editor(commit=True))
                    return True
            if event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(0, lambda: self.close_editor(commit=True))
        return False

    def editor_value(self):
        editor = self.editor
        if isinstance(editor, QPlainTextEdit):
            return editor.toPlainText()
        if isinstance(editor, QLineEdit):
            return editor.text()
        if isinstance(editor, QComboBox):
            return editor.currentData() or ""
        return None

    def close_editor(self, commit: bool = True) -> None:
        editor, pending = self.editor, self._editor_obj
        if editor is None:
            return
        self.editor, self._editor_obj = None, None
        value = None
        try:
            value = self.editor_value_of(editor)
        finally:
            editor.removeEventFilter(self)
            editor.hide()
            editor.deleteLater()
        if commit and pending is not None and value is not None and value != pending[1].value:
            self.page.fill_form({pending[1].name: value})
        self.canvas.setFocus()

    def editor_value_of(self, editor):
        previous, self.editor = self.editor, editor
        try:
            return self.editor_value()
        finally:
            self.editor = previous
