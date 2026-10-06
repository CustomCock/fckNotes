"""Zeichen-Grundelemente des Diagramm-Kerns mit QPainter malen (Editor, Symbole). Gleiche Liste wie im PDF,
gleiche Textbreiten (Helvetica-Metriken aus dem Kern) – so sitzt Text im Editor da, wo er im PDF landet."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from notex.core.diagram import render
from notex.core.diagram.model import Diagram

FONT_FAMILY = "Helvetica"
SUPERSAMPLE = 4.0           # Text in 4-facher Größe setzen und herunterskalieren → Bruchteil-Punktgrößen


def _pen(color: str, width: float, dashed: bool) -> QPen:
    if not color or color == "none" or width <= 0:
        return QPen(Qt.PenStyle.NoPen)
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dashed:
        pen.setDashPattern([4 / max(width, 0.1), 3 / max(width, 0.1)])
    return pen


def _brush(color: str) -> QBrush:
    return QBrush(Qt.BrushStyle.NoBrush) if not color or color == "none" else QBrush(QColor(color))


def paint_primitives(painter: QPainter, prims: list) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    for prim in prims:
        kind = prim[0]
        if kind == "rect":
            _k, x, y, w, h, r, fill, stroke, width, dashed = prim
            painter.setPen(_pen(stroke, width, dashed))
            painter.setBrush(_brush(fill))
            if r > 0:
                painter.drawRoundedRect(QRectF(x, y, w, h), r, r)
            else:
                painter.drawRect(QRectF(x, y, w, h))
        elif kind == "ellipse":
            _k, cx, cy, rx, ry, fill, stroke, width, dashed = prim
            painter.setPen(_pen(stroke, width, dashed))
            painter.setBrush(_brush(fill))
            painter.drawEllipse(QPointF(cx, cy), rx, ry)
        elif kind == "path":
            _k, points, closed, fill, stroke, width, dashed = prim
            if len(points) < 2:
                continue
            path = QPainterPath(QPointF(*points[0]))
            for p in points[1:]:
                path.lineTo(QPointF(*p))
            if closed:
                path.closeSubpath()
            painter.setPen(_pen(stroke, width, dashed))
            painter.setBrush(_brush(fill) if closed else QBrush(Qt.BrushStyle.NoBrush))
            painter.drawPath(path)
        elif kind == "text":
            _k, x, y, text, size, color, anchor, bold, italic = prim
            if not text:
                continue
            width = render.text_width(text, size, bold)
            left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
            font = QFont(FONT_FAMILY)
            font.setPixelSize(max(1, round(size * SUPERSAMPLE)))
            font.setBold(bool(bold))
            font.setItalic(bool(italic))
            painter.save()
            painter.translate(left, y)
            painter.scale(1 / SUPERSAMPLE, 1 / SUPERSAMPLE)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QPointF(0, 0), text)
            painter.restore()
    painter.restore()


def _recolor(prim: tuple, ink: str) -> tuple:
    """Symbol-Farben ans Theme anpassen: Schwarz → `ink`, Weiß → durchsichtig (Umriss-Stil wie die übrigen Icons)."""
    def swap(color):
        if color == "#1a1a1a":
            return ink
        if color == "#ffffff":
            return "none"
        return color
    kind = prim[0]
    if kind == "rect":
        return prim[:6] + (swap(prim[6]), swap(prim[7])) + prim[8:]
    if kind == "ellipse":
        return prim[:5] + (swap(prim[5]), swap(prim[6])) + prim[7:]
    if kind == "path":
        return prim[:3] + (swap(prim[3]), swap(prim[4])) + prim[5:]
    if kind == "text":
        return prim[:5] + (swap(prim[5]),) + prim[6:]
    return prim


def shape_icon(kind: str, size: int = 28, ink: str | None = None) -> QIcon:
    """Symbol einer Form – gezeichnet vom Diagramm-Renderer selbst; `ink` = Linienfarbe (Theme-Textfarbe)."""
    from notex.core.diagram.model import DEFAULT_SIZES
    w, h = DEFAULT_SIZES.get(kind, (60, 40))
    diagram = Diagram(w, h)
    shape = diagram.add_shape(kind, 0, 0, w, h, text="")
    if kind == "class":
        shape.text = "\n--\n\n--\n"
    if kind == "text":
        w, h = 30, 30
        shape.w, shape.h = w, h
        shape.text = "T"
        shape.bold = True
        shape.font_size = 26
    if kind == "rounded":
        w, h = 120, 70                                      # deutlichere Rundung im Symbol
        shape.w, shape.h = w, h
    if kind in ("circle", "endstate"):
        shape.x = shape.y = (34 - shape.w) / 2              # Knoten klein lassen, sonst wirken sie wuchtig
        w, h = 34, 34
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    margin = 4
    scale = min((size * 2 - 2 * margin) / w, (size * 2 - 2 * margin) / h)
    painter.translate((size * 2 - w * scale) / 2, (size * 2 - h * scale) / 2)
    painter.scale(scale, scale)
    prims = []
    line = 2.6 / scale                                    # Linien im kleinen Symbol kräftiger
    for p in render.shape_primitives(shape):
        if p[0] == "rect":
            p = p[:8] + (max(p[8], line),) + p[9:]
        elif p[0] == "ellipse":
            p = p[:7] + (max(p[7], line),) + p[8:]
        elif p[0] == "path":
            p = p[:5] + (max(p[5], line),) + p[6:]
        prims.append(_recolor(p, ink) if ink else p)
    paint_primitives(painter, prims)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)


def diagram_image(diagram: Diagram, scale: float = 2.0, background: str | None = "#ffffff"):
    """Diagramm als Bild (z. B. Vorschau, PNG-Export)."""
    from PySide6.QtGui import QImage
    image = QImage(max(1, round(diagram.width * scale)), max(1, round(diagram.height * scale)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(background) if background else QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.scale(scale, scale)
    paint_primitives(painter, render.primitives(diagram))
    painter.end()
    return image
