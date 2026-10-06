"""Diagramm-Editor (draw.io-artig) für PDFs: Formen setzen, verschieben, Größe ändern, an Andockpunkten verbinden.

Bedienung wie in draw.io:
- Form links wählen, auf die Fläche klicken (Standardgröße) oder aufziehen.
- Über einer Form erscheinen ihre Andockpunkte (blaue Kreuze); von einem Andockpunkt ziehen = Verbinder, der am
  Zielpunkt einrastet. Verbinder hängen an den Formen und wandern beim Verschieben mit.
- Doppelklick bearbeitet Text (UML-Klasse: Name / Attribute / Methoden), bei Verbindern Beschriftung und
  Multiplizitäten. Entf löscht, Ctrl+Z/Ctrl+Y, Ctrl+D dupliziert, Ctrl+C/Ctrl+V, Pfeiltasten schieben.
- Oben: Beziehung (UML), Linienführung, Pfeilspitzen, gestrichelt, Farben, Schrift – gilt für die Auswahl und
  für neue Verbinder.
Die Fläche zeigt den Seitenausschnitt als Hintergrund – so passt das Diagramm genau in die Lücke im Arbeitsblatt.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from notex.core.diagram import model as M
from notex.core.diagram import drawio, render, templates
from notex.theme.tokens import COLORS
from notex.ui.diagram_paint import paint_primitives, shape_icon
from notex.ui.widgets import IconButton

MARGIN = 28.0
PORT_COLOR = "#2f6fdf"
SNAP = 5.0
HANDLE = 4.0
ARROW_LABELS = {"none": "ohne", "arrow": "Pfeil", "open": "offener Pfeil", "triangle": "Dreieck (Vererbung)",
                "diamond": "Raute hohl (Aggregation)", "diamond_filled": "Raute voll (Komposition)", "circle": "Kreis"}
PALETTE_ORDER = ("class", "rect", "rounded", "ellipse", "actor", "diamond", "note", "text", "package", "database",
                 "parallelogram", "circle", "endstate")


def _snap(value: float, on: bool) -> float:
    return round(value / SNAP) * SNAP if on else value


class DiagramCanvas(QWidget):
    changed = Signal()
    selection_changed = Signal()
    tool_changed = Signal(str)

    def __init__(self, diagram: M.Diagram, page_image: QImage | None = None, page_scale: float = 1.0,
                 page_offset: tuple[float, float] = (0.0, 0.0)) -> None:
        super().__init__()
        self.setObjectName("DiagramCanvas")
        self.model = diagram
        self.page_image = page_image
        self.page_scale = page_scale
        self.page_offset = page_offset
        self.show_page = page_image is not None
        self.snap = True
        self.zoom = 1.5
        self.tool = "select"
        self.relation = "Pfeil"
        self.route_mode = "orthogonal"
        self.selection: list[str] = []
        self.hover: str | None = None
        self._drag: dict | None = None
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._clipboard: str | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._resize_widget()

    # ---- Koordinaten ------------------------------------------------------------------------------
    def _resize_widget(self) -> None:
        self.setFixedSize(QSize(round(self.model.width * self.zoom + 2 * MARGIN + 40),
                                round(self.model.height * self.zoom + 2 * MARGIN + 40)))
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.3, min(5.0, zoom))
        self._resize_widget()

    def to_model(self, pos: QPointF) -> tuple[float, float]:
        return (pos.x() - MARGIN) / self.zoom, (pos.y() - MARGIN) / self.zoom

    def tolerance(self, pixels: float = 6.0) -> float:
        return pixels / self.zoom

    # ---- Rückgängig -------------------------------------------------------------------------------
    def checkpoint(self) -> None:
        self._undo.append(self.model.to_json())
        del self._undo[:-100]
        self._redo.clear()

    def undo(self) -> None:
        if self._undo:
            self._redo.append(self.model.to_json())
            self._restore(self._undo.pop())

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self.model.to_json())
            self._restore(self._redo.pop())

    def _restore(self, text: str) -> None:
        restored = M.Diagram.from_json(text)
        self.model.width, self.model.height = restored.width, restored.height
        self.model.shapes, self.model.connectors = restored.shapes, restored.connectors
        ids = {s.id for s in self.model.shapes} | {c.id for c in self.model.connectors}
        self.selection = [i for i in self.selection if i in ids]
        self._resize_widget()
        self._changed()

    def _changed(self) -> None:
        self.update()
        self.changed.emit()
        self.selection_changed.emit()

    # ---- Auswahl ----------------------------------------------------------------------------------
    def selected_shapes(self) -> list[M.Shape]:
        return [s for s in self.model.shapes if s.id in self.selection]

    def selected_connectors(self) -> list[M.Connector]:
        return [c for c in self.model.connectors if c.id in self.selection]

    def select(self, ids: list[str], add: bool = False) -> None:
        self.selection = list(dict.fromkeys((self.selection if add else []) + ids))
        self.update()
        self.selection_changed.emit()

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor if tool != "select" else Qt.CursorShape.ArrowCursor)
        self.tool_changed.emit(tool)
        self.update()

    # ---- Treffer ------------------------------------------------------------------------------------
    def shape_at(self, x: float, y: float) -> M.Shape | None:
        for shape in reversed(self.model.shapes):
            extra = 14 if shape.kind == "actor" else 0
            if shape.x - 2 <= x <= shape.x + shape.w + 2 and shape.y - 2 <= y <= shape.y + shape.h + extra:
                return shape
        return None

    def port_at(self, x: float, y: float, shapes: list[M.Shape] | None = None) -> tuple[M.Shape, str] | None:
        best, best_d = None, self.tolerance(7)
        for shape in shapes if shapes is not None else self.model.shapes:
            for name, px, py in shape.ports():
                d = math.dist((px, py), (x, y))
                if d <= best_d:
                    best, best_d = (shape, name), d
        return best

    def connector_at(self, x: float, y: float) -> M.Connector | None:
        for conn in reversed(self.model.connectors):
            if M.distance_to_route(M.route(self.model, conn), x, y) <= self.tolerance(5):
                return conn
        return None

    def _handles(self, shape: M.Shape) -> dict[str, tuple[float, float]]:
        x0, y0, x1, y1 = shape.rect
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        return {"tl": (x0, y0), "t": (xm, y0), "tr": (x1, y0), "r": (x1, ym), "br": (x1, y1), "b": (xm, y1),
                "bl": (x0, y1), "l": (x0, ym)}

    def handle_at(self, x: float, y: float) -> tuple[M.Shape, str] | None:
        shapes = self.selected_shapes()
        if len(shapes) != 1:
            return None
        for name, (hx, hy) in self._handles(shapes[0]).items():
            if abs(hx - x) <= self.tolerance(6) and abs(hy - y) <= self.tolerance(6):
                return shapes[0], name
        return None

    def endpoint_at(self, x: float, y: float) -> tuple[M.Connector, str] | None:
        conns = self.selected_connectors()
        if len(conns) != 1:
            return None
        points = M.route(self.model, conns[0])
        for which, point in (("source", points[0]), ("target", points[-1])):
            if math.dist(point, (x, y)) <= self.tolerance(7):
                return conns[0], which
        return None

    def area_handle_at(self, x: float, y: float) -> bool:
        return abs(x - self.model.width) <= self.tolerance(7) and abs(y - self.model.height) <= self.tolerance(7)

    # ---- Maus -----------------------------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        x, y = self.to_model(event.position())
        snap = self.snap and not event.modifiers() & Qt.KeyboardModifier.AltModifier
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.tool not in ("select", "connect"):
            sx, sy = _snap(x, snap), _snap(y, snap)
            self._drag = {"mode": "create", "kind": self.tool, "start": (sx, sy), "now": (sx, sy)}
            return
        if self.tool == "connect":
            shape = self.shape_at(x, y)
            port = self.port_at(x, y)
            if port is not None:
                self._start_connect(port[0], port[1], x, y)
            elif shape is not None:
                name, _d = M.nearest_port(shape, x, y)
                self._start_connect(shape, name, x, y)
            else:
                self._drag = {"mode": "connect", "source": M.End(None, None, _snap(x, snap), _snap(y, snap)),
                              "now": (x, y)}
            return
        if self.area_handle_at(x, y):
            self._drag = {"mode": "area", "start": (x, y), "size": (self.model.width, self.model.height),
                          "saved": False}
            return
        endpoint = self.endpoint_at(x, y)
        if endpoint is not None:
            self._drag = {"mode": "endpoint", "conn": endpoint[0].id, "which": endpoint[1], "now": (x, y)}
            return
        handle = self.handle_at(x, y)
        if handle is not None:
            shape, name = handle
            self._drag = {"mode": "resize", "shape": shape.id, "handle": name, "start": (x, y),
                          "rect": shape.rect, "saved": False}
            return
        port = self.port_at(x, y, [s for s in self.model.shapes if s.id == self.hover])
        if port is not None and not shift:
            self._start_connect(port[0], port[1], x, y)
            return
        shape = self.shape_at(x, y)
        if shape is not None:
            if shift:
                if shape.id in self.selection:
                    self.selection.remove(shape.id)
                    self.select(self.selection)
                    return
                self.select([shape.id], add=True)
            elif shape.id not in self.selection:
                self.select([shape.id])
            self._drag = {"mode": "move", "start": (x, y), "saved": False,
                          "orig": {s.id: (s.x, s.y) for s in self.selected_shapes()},
                          "free": {c.id: ((c.source.x, c.source.y), (c.target.x, c.target.y))
                                   for c in self.model.connectors}}
            return
        conn = self.connector_at(x, y)
        if conn is not None:
            self.select([conn.id], add=shift)
            return
        if not shift:
            self.select([])
        self._drag = {"mode": "band", "start": (x, y), "now": (x, y)}

    def _start_connect(self, shape: M.Shape, port: str, x: float, y: float) -> None:
        self._drag = {"mode": "connect", "source": M.End(shape.id, port), "now": (x, y)}

    def mouseMoveEvent(self, event) -> None:
        x, y = self.to_model(event.position())
        snap = self.snap and not event.modifiers() & Qt.KeyboardModifier.AltModifier
        drag = self._drag
        if drag is None:
            shape = self.shape_at(x, y)
            hover = shape.id if shape is not None else None
            if self.port_at(x, y) is not None and hover is None:
                hover = self.port_at(x, y)[0].id
            if hover != self.hover:
                self.hover = hover
                self.update()
            if self.tool == "select":
                cursor = Qt.CursorShape.ArrowCursor
                if self.area_handle_at(x, y) or self.handle_at(x, y) is not None:
                    cursor = Qt.CursorShape.SizeFDiagCursor
                elif self.port_at(x, y, [s for s in self.model.shapes if s.id == self.hover]) is not None:
                    cursor = Qt.CursorShape.CrossCursor
                elif shape is not None:
                    cursor = Qt.CursorShape.SizeAllCursor
                self.setCursor(cursor)
            return
        mode = drag["mode"]
        if mode in ("create", "connect", "band", "endpoint"):
            drag["now"] = (x, y) if mode != "create" else (_snap(x, snap), _snap(y, snap))
            if mode in ("connect", "endpoint"):
                shape = self.shape_at(x, y)
                self.hover = shape.id if shape is not None else None
        elif mode == "move":
            dx, dy = x - drag["start"][0], y - drag["start"][1]
            if not drag["saved"]:
                if abs(dx) < self.tolerance(2) and abs(dy) < self.tolerance(2):
                    return
                self.checkpoint()
                drag["saved"] = True
            for shape in self.selected_shapes():
                ox, oy = drag["orig"][shape.id]
                shape.x, shape.y = _snap(ox + dx, snap), _snap(oy + dy, snap)
            for conn in self.selected_connectors():
                (sx, sy), (tx, ty) = drag["free"][conn.id]
                if conn.source.shape is None:
                    conn.source.x, conn.source.y = sx + dx, sy + dy
                if conn.target.shape is None:
                    conn.target.x, conn.target.y = tx + dx, ty + dy
        elif mode == "resize":
            if not drag["saved"]:
                self.checkpoint()
                drag["saved"] = True
            shape = self.model.shape(drag["shape"])
            x0, y0, x1, y1 = drag["rect"]
            dx, dy = x - drag["start"][0], y - drag["start"][1]
            handle = drag["handle"]
            if "l" in handle:
                x0 = min(_snap(x0 + dx, snap), x1 - 10)
            if "r" in handle:
                x1 = max(_snap(x1 + dx, snap), x0 + 10)
            if handle.startswith("t"):
                y0 = min(_snap(y0 + dy, snap), y1 - 10)
            if handle.startswith("b"):
                y1 = max(_snap(y1 + dy, snap), y0 + 10)
            shape.x, shape.y, shape.w, shape.h = x0, y0, x1 - x0, y1 - y0
        elif mode == "area":
            if not drag["saved"]:
                self.checkpoint()
                drag["saved"] = True
            self.model.width = max(40.0, _snap(x, snap))
            self.model.height = max(30.0, _snap(y, snap))
            self._resize_widget()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag is None:
            return
        x, y = self.to_model(event.position())
        mode = drag["mode"]
        if mode == "create":
            (x0, y0), (x1, y1) = drag["start"], drag["now"]
            kind = drag["kind"]
            self.checkpoint()
            if abs(x1 - x0) < 8 or abs(y1 - y0) < 8:
                w, h = M.DEFAULT_SIZES[kind]
                shape = self.model.add_shape(kind, x0 - w / 2, y0 - h / 2)
                shape.x, shape.y = _snap(shape.x, self.snap), _snap(shape.y, self.snap)
            else:
                shape = self.model.add_shape(kind, min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            if kind == "class":
                shape.h = max(shape.h, render.min_class_height(shape))
            self.set_tool("select")
            self.select([shape.id])
            self._changed()
        elif mode == "connect":
            target = self._end_at(x, y, exclude=None)
            source = drag["source"]
            if target.shape is not None and target.shape == source.shape and target.port == source.port:
                self.update()
                return
            if source.shape is None and target.shape is None and math.dist((source.x, source.y),
                                                                           (target.x, target.y)) < 6:
                self.update()
                return
            self.checkpoint()
            conn = self.model.connect(source, target, self.relation, route=self.route_mode)
            self.select([conn.id])
            self._changed()
        elif mode == "endpoint":
            conn = self.model.connector(drag["conn"])
            if conn is not None:
                self.checkpoint()
                setattr(conn, drag["which"], self._end_at(x, y, exclude=None))
                self._changed()
        elif mode == "band":
            (x0, y0), (x1, y1) = drag["start"], drag["now"]
            lx, rx, ty, by = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            if rx - lx > 2 and by - ty > 2:
                ids = [s.id for s in self.model.shapes if s.x >= lx and s.x + s.w <= rx and s.y >= ty
                       and s.y + s.h <= by]
                ids += [c.id for c in self.model.connectors
                        if all(lx <= px <= rx and ty <= py <= by for px, py in M.route(self.model, c))]
                self.select(ids, add=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        elif drag.get("saved"):
            self._changed()
        self.update()

    def _end_at(self, x: float, y: float, exclude=None) -> M.End:
        port = self.port_at(x, y)
        if port is not None:
            return M.End(port[0].id, port[1])
        shape = self.shape_at(x, y)
        if shape is not None:
            name, _d = M.nearest_port(shape, x, y)
            return M.End(shape.id, name)
        return M.End(None, None, _snap(x, self.snap), _snap(y, self.snap))

    def mouseDoubleClickEvent(self, event) -> None:
        x, y = self.to_model(event.position())
        shape = self.shape_at(x, y)
        if shape is not None:
            self.edit_shape_text(shape)
            return
        conn = self.connector_at(x, y)
        if conn is not None:
            self.edit_connector_labels(conn)

    # ---- Text bearbeiten ------------------------------------------------------------------------------
    def edit_shape_text(self, shape: M.Shape, text: str | None = None) -> bool:
        if text is None:
            dialog = ClassDialog(self, shape.text) if shape.kind == "class" else ShapeTextDialog(self, shape.text)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            text = dialog.text()
        self.checkpoint()
        shape.text = text
        if shape.kind == "class":
            shape.h = max(shape.h, render.min_class_height(shape))
        self._changed()
        return True

    def edit_connector_labels(self, conn: M.Connector, values: tuple[str, str, str] | None = None) -> bool:
        if values is None:
            dialog = ConnectorDialog(self, conn)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            values = dialog.values()
        self.checkpoint()
        conn.label, conn.start_label, conn.end_label = values
        self._changed()
        return True

    # ---- Tastatur -------------------------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
        elif event.matches(QKeySequence.StandardKey.Redo) or (
                event.key() == Qt.Key.Key_Y and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.redo()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
        elif event.matches(QKeySequence.StandardKey.SelectAll):
            self.select([s.id for s in self.model.shapes] + [c.id for c in self.model.connectors])
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.paste()
        elif event.key() == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.duplicate()
        elif event.key() == Qt.Key.Key_Escape:
            if self.tool != "select":
                self.set_tool("select")
            else:
                self.select([])
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) and \
                self.selected_shapes():
            step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(event.key(), 0.0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(event.key(), 0.0)
            self.checkpoint()
            for shape in self.selected_shapes():
                shape.x += dx
                shape.y += dy
            self._changed()
        else:
            super().keyPressEvent(event)

    def delete_selection(self) -> None:
        if not self.selection:
            return
        self.checkpoint()
        self.model.remove(set(self.selection))
        self.selection = []
        self._changed()

    def _subset_json(self) -> str:
        shapes = self.selected_shapes()
        ids = {s.id for s in shapes}
        conns = [c for c in self.model.connectors if c.id in self.selection or
                 (c.source.shape in ids and c.target.shape in ids)]
        part = M.Diagram(self.model.width, self.model.height, copy.deepcopy(shapes), copy.deepcopy(conns))
        return part.to_json()

    def copy(self) -> None:
        if self.selection:
            self._clipboard = self._subset_json()

    def paste(self, text: str | None = None, offset: float = 15.0) -> list[str]:
        text = text or self._clipboard
        if not text:
            return []
        part = M.Diagram.from_json(text)
        mapping = {s.id: M.new_id("s") for s in part.shapes}
        self.checkpoint()
        new_ids = []
        for s in part.shapes:
            s.id = mapping[s.id]
            s.x += offset
            s.y += offset
            self.model.shapes.append(s)
            new_ids.append(s.id)
        for c in part.connectors:
            c.id = M.new_id("c")
            for end in (c.source, c.target):
                if end.shape is not None:
                    end.shape = mapping.get(end.shape)
                    if end.shape is None:
                        end.port = None
                else:
                    end.x += offset
                    end.y += offset
            self.model.connectors.append(c)
            new_ids.append(c.id)
        self.selection = new_ids
        self._changed()
        return new_ids

    def duplicate(self) -> list[str]:
        if not self.selection:
            return []
        return self.paste(self._subset_json())

    def insert_template(self, name: str) -> list[str]:
        self.checkpoint()
        box = self.model.bounds()
        oy = (box[3] + 20) if box else 10.0
        ids = templates.apply(self.model, name, 10.0, oy)
        content = self.model.bounds()
        if content is not None:
            self.model.width = max(self.model.width, content[2] + 10)
            self.model.height = max(self.model.height, content[3] + 10)
            self._resize_widget()
        self.selection = ids
        self._changed()
        return ids

    def insert_part(self, part: M.Diagram) -> list[str]:
        """Fremdes Diagramm (z. B. aus draw.io) unter den vorhandenen Inhalt setzen und auswählen."""
        box, own = part.bounds(), self.model.bounds()
        if box is None:
            return []
        part.translate(10.0 - box[0], ((own[3] + 20) if own else 10.0) - box[1])
        ids = self.paste(part.to_json(), offset=0.0)
        content = self.model.bounds()
        if content is not None:
            self.model.width = max(self.model.width, content[2] + 10)
            self.model.height = max(self.model.height, content[3] + 10)
            self._resize_widget()
            self._changed()
        return ids

    def bring_to_front(self, front: bool = True) -> None:
        shapes = self.selected_shapes()
        if not shapes:
            return
        self.checkpoint()
        rest = [s for s in self.model.shapes if s not in shapes]
        self.model.shapes = rest + shapes if front else shapes + rest
        self._changed()

    # ---- Zeichnen ------------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS.bg))
        painter.translate(MARGIN, MARGIN)
        painter.scale(self.zoom, self.zoom)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = QRectF(0, 0, self.model.width, self.model.height)
        painter.fillRect(area, QColor("#ffffff"))
        if self.show_page and self.page_image is not None:
            ox, oy = self.page_offset
            s = self.page_scale
            source = QRectF(ox * s, oy * s, self.model.width * s, self.model.height * s)
            painter.save()
            painter.setOpacity(0.55)
            painter.drawImage(area, self.page_image, source)
            painter.restore()
        if self.page_image is not None:                    # außerhalb der Seite: grau, mit Hinweis
            page_w = self.page_image.width() / self.page_scale - self.page_offset[0]
            page_h = self.page_image.height() / self.page_scale - self.page_offset[1]
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 60))
            if page_w < self.model.width:
                painter.drawRect(QRectF(page_w, 0, self.model.width - page_w, self.model.height))
            if page_h < self.model.height:
                painter.drawRect(QRectF(0, page_h, min(page_w, self.model.width), self.model.height - page_h))
            painter.restore()
        if self.snap:
            painter.save()
            painter.setPen(QPen(QColor(0, 0, 0, 34), 0))
            step = 10.0
            y = 0.0
            while y <= self.model.height:
                x = 0.0
                while x <= self.model.width:
                    painter.drawPoint(QPointF(x, y))
                    x += step
                y += step
            painter.restore()
        paint_primitives(painter, render.primitives(self.model))
        accent = QColor(COLORS.accent)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for conn in self.selected_connectors():
            points = M.route(self.model, conn)
            pen = QPen(QColor(47, 111, 223, 90), 5 / self.zoom)
            painter.setPen(pen)
            for a, b in zip(points, points[1:]):
                painter.drawLine(QPointF(*a), QPointF(*b))
            if len(self.selection) == 1:
                painter.setPen(QPen(QColor(PORT_COLOR), 1 / self.zoom))
                painter.setBrush(QColor("#ffffff"))
                for point in (points[0], points[-1]):
                    painter.drawEllipse(QPointF(*point), 4 / self.zoom, 4 / self.zoom)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        for shape in self.selected_shapes():
            painter.setPen(QPen(accent, 1.2 / self.zoom, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(*shape.rect[:2], shape.w, shape.h))
            if len(self.selected_shapes()) == 1:
                painter.setPen(QPen(accent, 1 / self.zoom))
                painter.setBrush(QColor("#ffffff"))
                h = HANDLE / self.zoom
                for hx, hy in self._handles(shape).values():
                    painter.drawRect(QRectF(hx - h, hy - h, 2 * h, 2 * h))
                painter.setBrush(Qt.BrushStyle.NoBrush)
        hover_ids = {self.hover} if self.hover else set()
        if self._drag is not None and self._drag["mode"] in ("connect", "endpoint"):
            hover_ids |= {s.id for s in self.model.shapes}
        for shape in self.model.shapes:
            if shape.id not in hover_ids or self.tool not in ("select", "connect"):
                continue
            painter.setPen(QPen(QColor(PORT_COLOR), 1.3 / self.zoom))
            r = 3 / self.zoom
            for _name, px, py in shape.ports():
                painter.drawLine(QPointF(px - r, py - r), QPointF(px + r, py + r))
                painter.drawLine(QPointF(px - r, py + r), QPointF(px + r, py - r))
        drag = self._drag
        if drag is not None and drag["mode"] in ("connect", "endpoint"):
            if drag["mode"] == "connect":
                start = M.end_point(self.model, drag["source"], drag["now"])
            else:
                conn = self.model.connector(drag["conn"])
                other = conn.target if drag["which"] == "source" else conn.source
                start = M.end_point(self.model, other, drag["now"])
            end = drag["now"]
            target = self.port_at(*end)
            if target is not None:
                end = target[0].port(target[1])
                painter.setPen(QPen(QColor(PORT_COLOR), 2 / self.zoom))
                painter.drawEllipse(QPointF(*end), 5 / self.zoom, 5 / self.zoom)
            painter.setPen(QPen(QColor(PORT_COLOR), 1.5 / self.zoom, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(*start), QPointF(*end))
        if drag is not None and drag["mode"] in ("band", "create"):
            (x0, y0), (x1, y1) = drag["start"], drag["now"]
            painter.setPen(QPen(accent, 1 / self.zoom, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(122, 138, 158, 30))
            painter.drawRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 1 / self.zoom, Qt.PenStyle.DashLine))
        painter.drawRect(area)
        painter.setPen(QPen(accent, 1 / self.zoom))
        painter.setBrush(accent)
        h = 4 / self.zoom
        painter.drawRect(QRectF(self.model.width - h, self.model.height - h, 2 * h, 2 * h))
        painter.end()


# ---- Kleine Dialoge ---------------------------------------------------------------------------------------
class ShapeTextDialog(QDialog):
    def __init__(self, parent, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Text der Form")
        self.edit = QPlainTextEdit(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        layout.addWidget(buttons)
        self.edit.setFocus()
        self.edit.selectAll()

    def text(self) -> str:
        return self.edit.toPlainText().rstrip("\n")


class ClassDialog(QDialog):
    """UML-Klasse: Name, Attribute, Methoden getrennt eingeben (gespeichert mit „--“-Trennzeilen)."""

    def __init__(self, parent, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("UML-Klasse")
        sections = (text.split("\n--\n") + ["", "", ""])[:3]
        if "\n--\n" not in text:
            sections = M.Shape("x", "class", 0, 0, 1, 1, text).class_sections() + ["", ""]
        self.name = QLineEdit(sections[0].replace("{abstract}", "").strip())
        self.abstract = QCheckBox("abstrakt (kursiv)")
        self.abstract.setChecked("{abstract}" in sections[0])
        self.attributes = QPlainTextEdit(sections[1])
        self.methods = QPlainTextEdit(sections[2])
        for edit in (self.attributes, self.methods):
            edit.setPlaceholderText("je Zeile eins, z. B.  - name: String   /   + gruessen(): void")
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("", self.abstract)
        form.addRow("Attribute", self.attributes)
        form.addRow("Methoden", self.methods)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def text(self) -> str:
        name = ("{abstract}\n" if self.abstract.isChecked() else "") + self.name.text().strip()
        return f"{name}\n--\n{self.attributes.toPlainText().rstrip()}\n--\n{self.methods.toPlainText().rstrip()}"


class ConnectorDialog(QDialog):
    def __init__(self, parent, conn: M.Connector) -> None:
        super().__init__(parent)
        self.setWindowTitle("Verbinder beschriften")
        self.label = QLineEdit(conn.label)
        self.start = QLineEdit(conn.start_label)
        self.end = QLineEdit(conn.end_label)
        self.start.setPlaceholderText("z. B. 1")
        self.end.setPlaceholderText("z. B. 0..*")
        form = QFormLayout()
        form.addRow("Beschriftung", self.label)
        form.addRow("Am Anfang", self.start)
        form.addRow("Am Ende", self.end)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return self.label.text().strip(), self.start.text().strip(), self.end.text().strip()


# ---- draw.io-Dateien -------------------------------------------------------------------------------------
def read_drawio_file(parent, path: str, page: int | None = None) -> M.Diagram | None:
    """draw.io-Datei lesen; Fehler als Meldung, mehrere Seiten per Auswahl. None = abgebrochen/Fehler."""
    try:
        file = Path(path)
        if file.stat().st_size > drawio.MAX_FILE:
            raise drawio.DrawioError("Datei zu groß")
        text = file.read_text(encoding="utf-8", errors="replace")
        names = drawio.page_names(text)
        if page is None:
            page = 0
            if len(names) > 1:
                chosen, ok = QInputDialog.getItem(parent, "draw.io-Seite", "Welche Seite?", names, 0, False)
                if not ok:
                    return None
                page = names.index(chosen)
        return drawio.read(text, page)
    except (OSError, drawio.DrawioError) as error:
        QMessageBox.warning(parent, "draw.io", f"Datei nicht lesbar: {error}")
        return None


def write_drawio_file(parent, path: str, diagram: M.Diagram) -> bool:
    try:
        Path(path).write_text(drawio.write(diagram, Path(path).stem), encoding="utf-8")
    except OSError as error:
        QMessageBox.warning(parent, "draw.io", f"Speichern fehlgeschlagen: {error}")
        return False
    return True


# ---- Editor-Fenster --------------------------------------------------------------------------------------
class DiagramEditor(QDialog):
    """Fenster mit Formen-Leiste links, Eigenschaften oben, Fläche in der Mitte. Ergebnis: `result_diagram()`."""

    def __init__(self, parent, diagram: M.Diagram | None = None, page_image: QImage | None = None,
                 page_scale: float = 1.0, page_offset: tuple[float, float] = (0.0, 0.0)) -> None:
        super().__init__(parent)
        self.setWindowTitle("Diagramm")
        self.resize(1180, 760)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.canvas = DiagramCanvas(copy.deepcopy(diagram) if diagram else M.Diagram(), page_image, page_scale,
                                    page_offset)
        self._syncing = False
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Formen-Leiste
        side = QVBoxLayout()
        side.setSpacing(4)
        self.shape_buttons: dict[str, QToolButton] = {}
        for kind in PALETTE_ORDER:
            button = QToolButton()
            button.setIcon(shape_icon(kind))
            button.setIconSize(QSize(28, 28))
            button.setToolTip(M.LABELS[kind] + " – klicken, dann auf die Fläche klicken oder aufziehen")
            button.setCheckable(True)
            button.clicked.connect(lambda _c=False, k=kind: self.canvas.set_tool(k))
            side.addWidget(button)
            self.shape_buttons[kind] = button
        side.addStretch(1)

        # Werkzeuge und Eigenschaften
        self.select_button = IconButton("mouse-pointer-2", "Auswählen / verschieben (Esc)")
        self.select_button.setCheckable(True)
        self.select_button.clicked.connect(lambda: self.canvas.set_tool("select"))
        self.connect_button = IconButton("arrow-left-right", "Verbinden: von Form zu Form ziehen")
        self.connect_button.setCheckable(True)
        self.connect_button.clicked.connect(lambda: self.canvas.set_tool("connect"))
        self.relation = QComboBox()
        self.relation.addItems(list(M.RELATIONS))
        self.relation.setCurrentText("Pfeil")
        self.relation.setToolTip("Beziehung (UML) – für neue Verbinder und die ausgewählten")
        self.relation.activated.connect(self._relation_chosen)
        self.route = QComboBox()
        self.route.addItem("rechtwinklig", "orthogonal")
        self.route.addItem("gerade", "straight")
        self.route.activated.connect(self._route_chosen)
        self.start_arrow, self.end_arrow = QComboBox(), QComboBox()
        for combo in (self.start_arrow, self.end_arrow):
            for key, label in ARROW_LABELS.items():
                combo.addItem(label, key)
        self.start_arrow.setToolTip("Spitze am Anfang")
        self.end_arrow.setToolTip("Spitze am Ende")
        self.start_arrow.activated.connect(lambda _i: self._set_conn("start_arrow", self.start_arrow.currentData()))
        self.end_arrow.activated.connect(lambda _i: self._set_conn("end_arrow", self.end_arrow.currentData()))
        self.dashed = QCheckBox("gestrichelt")
        self.dashed.clicked.connect(lambda on: self._set_both("dashed", on))
        fill = QPushButton("Füllung …")
        fill.clicked.connect(lambda: self._choose_color("fill"))
        stroke = QPushButton("Linie …")
        stroke.clicked.connect(lambda: self._choose_color("stroke"))
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 40)
        self.font_size.setValue(11)
        self.font_size.setSuffix(" pt")
        self.font_size.valueChanged.connect(lambda v: self._set_shapes("font_size", float(v)) if not self._syncing else None)
        self.bold = QCheckBox("fett")
        self.bold.clicked.connect(lambda on: self._set_shapes("bold", on))
        template = QPushButton("Vorlage …")
        template_menu = QMenu(template)
        for key, label in templates.TEMPLATES.items():
            template_menu.addAction(label, lambda k=key: self.canvas.insert_template(k))
        template.setMenu(template_menu)
        more = QPushButton("Anordnen …")
        more_menu = QMenu(more)
        more_menu.addAction("Nach vorne", lambda: self.canvas.bring_to_front(True))
        more_menu.addAction("Nach hinten", lambda: self.canvas.bring_to_front(False))
        more_menu.addSeparator()
        more_menu.addAction("Duplizieren  Ctrl+D", self.canvas.duplicate)
        more_menu.addAction("Fläche an Inhalt anpassen", self.fit_area)
        more.setMenu(more_menu)
        file_button = QPushButton("draw.io …")
        file_menu = QMenu(file_button)
        file_menu.addAction("draw.io-Datei öffnen (einfügen) …", lambda: self.import_drawio())
        file_menu.addAction("Als draw.io-Datei speichern …", lambda: self.export_drawio())
        file_button.setMenu(file_menu)
        undo = IconButton("undo-2", "Rückgängig  Ctrl+Z")
        undo.clicked.connect(self.canvas.undo)
        redo = IconButton("redo-2", "Wiederholen  Ctrl+Y")
        redo.clicked.connect(self.canvas.redo)
        delete = IconButton("trash", "Auswahl löschen  Entf")
        delete.clicked.connect(self.canvas.delete_selection)
        zoom_out = IconButton("zoom-out", "Verkleinern")
        zoom_out.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom / 1.2))
        zoom_in = IconButton("zoom-in", "Vergrößern")
        zoom_in.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom * 1.2))
        self.snap = QCheckBox("Raster")
        self.snap.setChecked(True)
        self.snap.toggled.connect(self._set_snap)
        self.page_box = QCheckBox("Seite zeigen")
        self.page_box.setChecked(self.canvas.show_page)
        self.page_box.setEnabled(page_image is not None)
        self.page_box.toggled.connect(self._set_page)
        self.size_label = QLabel()
        self.size_label.setObjectName("DataInfo")

        top = QHBoxLayout()
        for widget in (self.select_button, self.connect_button, QLabel("Beziehung"), self.relation, self.route,
                       QLabel("Anfang"), self.start_arrow, QLabel("Ende"), self.end_arrow, self.dashed):
            top.addWidget(widget)
        top.addStretch(1)
        second = QHBoxLayout()
        for widget in (fill, stroke, self.font_size, self.bold, template, more, file_button, undo, redo, delete, zoom_out,
                       zoom_in, self.snap, self.page_box, self.size_label):
            second.addWidget(widget)
        second.addStretch(1)
        hint = QLabel("Form wählen und auf die Fläche klicken · von einem blauen Andockpunkt ziehen = Verbinder · "
                      "Doppelklick = Text · Ecke unten rechts = Fläche vergrößern")
        hint.setObjectName("SettingsNote")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("In PDF übernehmen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.addLayout(side)
        body.addWidget(self.scroll, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(second)
        layout.addLayout(body, 1)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.canvas.tool_changed.connect(self._tool_changed)
        self.canvas.selection_changed.connect(self._sync_controls)
        self.canvas.changed.connect(self._update_size)
        self._tool_changed("select")
        self._update_size()
        self._fit_zoom()

    # ---- Steuerung ---------------------------------------------------------------------------------------
    def _fit_zoom(self) -> None:
        avail_w, avail_h = 900.0, 560.0
        model = self.canvas.model
        self.canvas.set_zoom(max(0.5, min(2.5, avail_w / max(model.width + 60, 1), avail_h / max(model.height + 60, 1))))

    def _tool_changed(self, tool: str) -> None:
        self.select_button.setChecked(tool == "select")
        self.connect_button.setChecked(tool == "connect")
        for kind, button in self.shape_buttons.items():
            button.setChecked(kind == tool)

    def _update_size(self) -> None:
        m = self.canvas.model
        self.size_label.setText(f"Fläche {m.width:.0f} × {m.height:.0f} pt")

    def _set_snap(self, on: bool) -> None:
        self.canvas.snap = on
        self.canvas.update()

    def _set_page(self, on: bool) -> None:
        self.canvas.show_page = on
        self.canvas.update()

    def _sync_controls(self) -> None:
        self._syncing = True
        conns = self.canvas.selected_connectors()
        shapes = self.canvas.selected_shapes()
        if conns:
            c = conns[0]
            self.start_arrow.setCurrentIndex(self.start_arrow.findData(c.start_arrow))
            self.end_arrow.setCurrentIndex(self.end_arrow.findData(c.end_arrow))
            self.route.setCurrentIndex(self.route.findData(c.route))
            self.dashed.setChecked(c.dashed)
        elif shapes:
            s = shapes[0]
            self.font_size.setValue(round(s.font_size))
            self.bold.setChecked(s.bold)
            self.dashed.setChecked(s.dashed)
        self._syncing = False

    def _relation_chosen(self, _index: int) -> None:
        name = self.relation.currentText()
        self.canvas.relation = name
        conns = self.canvas.selected_connectors()
        if conns:
            self.canvas.checkpoint()
            for c in conns:
                for key, value in M.RELATIONS[name].items():
                    setattr(c, key, value)
            self.canvas._changed()
        elif self.canvas.tool not in ("connect",):
            self.canvas.set_tool("connect")

    def _route_chosen(self, _index: int) -> None:
        self.canvas.route_mode = self.route.currentData()
        self._set_conn("route", self.route.currentData())

    def _set_conn(self, attr: str, value) -> None:
        conns = self.canvas.selected_connectors()
        if not conns or self._syncing:
            return
        self.canvas.checkpoint()
        for c in conns:
            setattr(c, attr, value)
        self.canvas._changed()

    def _set_shapes(self, attr: str, value) -> None:
        shapes = self.canvas.selected_shapes()
        if not shapes or self._syncing:
            return
        self.canvas.checkpoint()
        for s in shapes:
            setattr(s, attr, value)
            if s.kind == "class":
                s.h = max(s.h, render.min_class_height(s))
        self.canvas._changed()

    def _set_both(self, attr: str, value) -> None:
        if self.canvas.selected_connectors():
            self._set_conn(attr, value)
        if self.canvas.selected_shapes():
            self._set_shapes(attr, value)

    def _choose_color(self, which: str, color: str | None = None) -> None:
        if color is None:
            chosen = QColorDialog.getColor(QColor("#ffffff" if which == "fill" else "#1a1a1a"), self)
            if not chosen.isValid():
                return
            color = chosen.name()
        if which == "fill":
            self._set_shapes("fill", color)
        else:
            self._set_shapes("stroke", color)
            self._set_conn("stroke", color)

    # ---- draw.io -------------------------------------------------------------------------------------------
    def import_drawio(self, path: str | None = None, page: int | None = None) -> list[str]:
        """draw.io-Datei lesen (bei mehreren Seiten fragen) und in die Fläche einsetzen."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "draw.io-Datei öffnen", str(Path.home()),
                                                  "draw.io (*.drawio *.drawio.xml *.xml *.drawio.svg *.svg)")
            if not path:
                return []
        part = read_drawio_file(self, path, page)
        if part is None:
            return []
        return self.canvas.insert_part(part)

    def export_drawio(self, path: str | None = None) -> bool:
        """Ganze Fläche als .drawio speichern (draw.io öffnet sie direkt, auch app.diagrams.net)."""
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Als draw.io-Datei speichern",
                                                  str(Path.home() / "diagramm.drawio"), "draw.io (*.drawio)")
            if not path:
                return False
        return write_drawio_file(self, path, self.result_diagram()[0])

    def fit_area(self) -> None:
        self.canvas.checkpoint()
        self.canvas.model.fit()
        self.canvas._resize_widget()
        self.canvas._changed()

    def result_diagram(self) -> tuple[M.Diagram, tuple[float, float]]:
        """Fertiges Diagramm und Versatz der linken oberen Ecke (falls Inhalt links/oberhalb der Fläche liegt)."""
        diagram = copy.deepcopy(self.canvas.model)
        box = diagram.bounds()
        dx = dy = 0.0
        if box is not None:
            if box[0] < 0 or box[1] < 0:
                dx, dy = min(0.0, box[0] - 2), min(0.0, box[1] - 2)
                diagram.translate(-dx, -dy)
                diagram.width -= dx
                diagram.height -= dy
                box = diagram.bounds()
            diagram.width = max(diagram.width, box[2] + 2)
            diagram.height = max(diagram.height, box[3] + 2)
        return diagram, (dx, dy)
