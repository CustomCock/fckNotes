"""Diagramm → Zeichen-Grundelemente (Qt-frei). PDF-Ausgabe (`pdf.py`) und Editor (QPainter) zeichnen dieselbe Liste,
damit das Diagramm im Editor genauso aussieht wie später im PDF.

Grundelemente (Koordinaten in Punkt, y nach unten):
  ("rect", x, y, w, h, radius, fill, stroke, width, dashed)
  ("ellipse", cx, cy, rx, ry, fill, stroke, width, dashed)
  ("path", points, closed, fill, stroke, width, dashed)
  ("text", x, baseline, text, size, color, anchor, bold, italic)      anchor: start | middle | end
Farbe "none" = nicht zeichnen. Text: Helvetica (Breiten aus den Adobe-Metriken), WinAnsi.
"""
from __future__ import annotations

import math

from notex.core.diagram.model import Connector, Diagram, Shape, midpoint, route

ARROW_LEN = 10.0
ARROW_HALF = 5.0
DIAMOND_LEN = 15.0
PAD = 5.0


def _metrics(bold: bool) -> dict[str, int]:
    try:
        from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS
        return CORE_FONT_METRICS["Helvetica-Bold" if bold else "Helvetica"].character_widths
    except Exception:                                      # noqa: BLE001
        return {"default": 556 if not bold else 611, " ": 278}


_WIDTHS = {False: _metrics(False), True: _metrics(True)}


def text_width(text: str, size: float, bold: bool = False) -> float:
    widths = _WIDTHS[bold]
    default = widths.get("default", 556)
    return sum(widths.get(ch, default) for ch in text) * size / 1000


def wrap(text: str, size: float, width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if text_width(candidate, size, bold) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _text_block(out: list, text: str, cx: float, top: float, width: float, size: float, color: str,
                anchor: str = "middle", bold: bool = False, italic: bool = False, left: float | None = None) -> float:
    """Text umbrechen und ab `top` setzen; liefert die Unterkante."""
    leading = size * 1.2
    y = top + size * 0.93
    for line in wrap(text, size, max(10.0, width), bold):
        x = cx if anchor == "middle" else (left if left is not None else cx)
        out.append(("text", x, y, line, size, color, anchor, bold, italic))
        y += leading
    return y - size * 0.93 + size * 0.27


def _text_height(text: str, size: float, width: float, bold: bool = False) -> float:
    return len(wrap(text, size, max(10.0, width), bold)) * size * 1.2


def shape_primitives(s: Shape) -> list:
    out: list = []
    fill, stroke, width = s.fill, s.stroke, 1.2
    x, y, w, h = s.x, s.y, s.w, s.h
    cx, cy = s.center
    dashed = s.dashed
    size = s.font_size
    centered_text = True
    if s.kind in ("rect", "text"):
        out.append(("rect", x, y, w, h, 0.0, fill, stroke, width, dashed))
    elif s.kind == "rounded":
        out.append(("rect", x, y, w, h, min(10.0, h / 3, w / 3), fill, stroke, width, dashed))
    elif s.kind == "ellipse":
        out.append(("ellipse", cx, cy, w / 2, h / 2, fill, stroke, width, dashed))
    elif s.kind == "circle":
        out.append(("ellipse", cx, cy, w / 2, h / 2, fill, stroke, width, dashed))
        centered_text = False
    elif s.kind == "endstate":
        out.append(("ellipse", cx, cy, w / 2, h / 2, "#ffffff", stroke, width, dashed))
        out.append(("ellipse", cx, cy, w / 2 - 4, h / 2 - 4, fill, "none", width, False))
        centered_text = False
    elif s.kind == "diamond":
        out.append(("path", [(cx, y), (x + w, cy), (cx, y + h), (x, cy)], True, fill, stroke, width, dashed))
    elif s.kind == "parallelogram":
        skew = min(w * 0.18, 18.0)
        out.append(("path", [(x + skew, y), (x + w, y), (x + w - skew, y + h), (x, y + h)], True, fill, stroke,
                    width, dashed))
    elif s.kind == "note":
        fold = min(12.0, w / 4, h / 4)
        out.append(("path", [(x, y), (x + w - fold, y), (x + w, y + fold), (x + w, y + h), (x, y + h)], True,
                    fill, stroke, width, dashed))
        out.append(("path", [(x + w - fold, y), (x + w - fold, y + fold), (x + w, y + fold)], False, "none",
                    stroke, width, False))
    elif s.kind == "package":
        tab_w, tab_h = min(w * 0.45, 70.0), min(14.0, h / 4)
        out.append(("rect", x, y, tab_w, tab_h, 0.0, fill, stroke, width, dashed))
        out.append(("rect", x, y + tab_h, w, h - tab_h, 0.0, fill, stroke, width, dashed))
        _text_block(out, s.text, cx, y + tab_h + PAD, w - 2 * PAD, size, s.text_color, "start", True, False,
                    left=x + PAD)
        return out
    elif s.kind == "database":
        ry = min(h * 0.15, 10.0)
        arc_top = _arc(cx, y + ry, w / 2, ry, 0, 360)
        body = [(x, y + ry)] + _arc(cx, y + h - ry, w / 2, ry, 180, 0) + [(x + w, y + ry)]
        out.append(("path", body + _arc(cx, y + ry, w / 2, ry, 0, -180)[1:], True, fill, stroke, width, dashed))
        out.append(("path", arc_top, True, fill, stroke, width, dashed))
        _text_block(out, s.text, cx, cy - _text_height(s.text, size, w - 2 * PAD, s.bold) / 2 + ry / 2,
                    w - 2 * PAD, size, s.text_color, bold=s.bold)
        return out
    elif s.kind == "actor":
        head = min(w, h) * 0.22
        hx, hy = cx, y + head
        out.append(("ellipse", hx, hy, head, head, fill, stroke, width, dashed))
        neck, hip = y + 2 * head, y + h * 0.62
        out.append(("path", [(cx, neck), (cx, hip)], False, "none", stroke, width, False))
        out.append(("path", [(x, y + h * 0.4), (x + w, y + h * 0.4)], False, "none", stroke, width, False))
        out.append(("path", [(x + w * 0.1, y + h), (cx, hip), (x + w * 0.9, y + h)], False, "none", stroke, width,
                    False))
        if s.text:
            _text_block(out, s.text, cx, y + h + 3, max(w * 3, 80.0), size, s.text_color, bold=s.bold)
        return out
    elif s.kind == "class":
        return out + _class_primitives(s)
    if centered_text and s.text:
        inner = w - 2 * PAD if s.kind not in ("diamond", "ellipse") else w * 0.72
        top = cy - _text_height(s.text, size, inner, s.bold) / 2
        _text_block(out, s.text, cx, top, inner, size, s.text_color, bold=s.bold)
    elif s.text:
        _text_block(out, s.text, cx, y + h + 3, max(w * 4, 80.0), size, s.text_color, bold=s.bold)
    return out


def _arc(cx, cy, rx, ry, start_deg, end_deg, steps: int = 16) -> list[tuple[float, float]]:
    out = []
    for i in range(steps + 1):
        a = math.radians(start_deg + (end_deg - start_deg) * i / steps)
        out.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return out


def class_name(text: str) -> tuple[str, bool]:
    """Kopf einer UML-Klasse ohne die Markierung „{abstract}“ (→ kursiv)."""
    lines = [ln for ln in text.split("\n")]
    italic = any(ln.strip().startswith("{abstract}") for ln in lines)
    name = "\n".join(ln.replace("{abstract}", "").strip() for ln in lines if ln.replace("{abstract}", "").strip())
    return name, italic


def _sections(s: Shape) -> list[str]:
    sections = s.class_sections()
    sections[0] = class_name(sections[0])[0]
    return sections


def class_layout(s: Shape) -> list[tuple[float, float]]:
    """(oben, unten) je Abschnitt einer UML-Klasse – Höhe aus dem Text, Rest bekommt der letzte Abschnitt."""
    sections = _sections(s)
    size = s.font_size
    heights = []
    for i, text in enumerate(sections):
        lines = max(1, len(wrap(text, size, s.w - 2 * PAD, i == 0))) if text.strip() or i == 0 else 0
        heights.append(max(size * 1.2 * lines + 2 * PAD - 2, 10.0 if i else size * 1.2 + 2 * PAD))
    out, top = [], s.y
    for i, height in enumerate(heights):
        bottom = s.y + s.h if i == len(heights) - 1 else min(s.y + s.h, top + height)
        out.append((top, max(top, bottom)))
        top = bottom
    return out


def min_class_height(s: Shape) -> float:
    sections = _sections(s)
    total = 0.0
    for i, text in enumerate(sections):
        lines = max(1, len(wrap(text, s.font_size, s.w - 2 * PAD, i == 0))) if text.strip() or i == 0 else 0
        total += max(s.font_size * 1.2 * lines + 2 * PAD - 2, 10.0 if i else s.font_size * 1.2 + 2 * PAD)
    return total


def _class_primitives(s: Shape) -> list:
    out: list = [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    sections = s.class_sections()
    for i, ((top, bottom), text) in enumerate(zip(class_layout(s), sections)):
        if i:
            out.append(("path", [(s.x, top), (s.x + s.w, top)], False, "none", s.stroke, 1.0, False))
        if not text.strip():
            continue
        if i == 0:
            name, italic = class_name(text)
            _text_block(out, name, s.x + s.w / 2, top + PAD - 1, s.w - 2 * PAD, s.font_size, s.text_color,
                        bold=True, italic=italic)
        else:
            _text_block(out, text, s.x + s.w / 2, top + PAD - 2, s.w - 2 * PAD, s.font_size, s.text_color,
                        "start", left=s.x + PAD)
    return out


# ---- Verbinder -----------------------------------------------------------------------------------------------
def _head(kind: str, tip, before, color: str, width: float) -> tuple[list, float]:
    """Pfeilspitze an `tip` (Richtung von `before`); liefert Grundelemente und wie weit die Linie kürzer wird."""
    dx, dy = tip[0] - before[0], tip[1] - before[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy, px, py = dx / d, dy / d, -dy / d, dx / d

    def at(dist, side=0.0):
        return tip[0] - ux * dist + px * side, tip[1] - uy * dist + py * side
    if kind == "arrow":
        return [("path", [tip, at(ARROW_LEN, ARROW_HALF), at(ARROW_LEN, -ARROW_HALF)], True, color, color, width,
                 False)], ARROW_LEN
    if kind == "open":
        return [("path", [at(ARROW_LEN, ARROW_HALF), tip, at(ARROW_LEN, -ARROW_HALF)], False, "none", color, width,
                 False)], 0.0
    if kind == "triangle":
        return [("path", [tip, at(ARROW_LEN + 2, ARROW_HALF + 1.5), at(ARROW_LEN + 2, -ARROW_HALF - 1.5)], True,
                 "#ffffff", color, width, False)], ARROW_LEN + 2
    if kind in ("diamond", "diamond_filled"):
        fill = color if kind == "diamond_filled" else "#ffffff"
        return [("path", [tip, at(DIAMOND_LEN / 2, ARROW_HALF), at(DIAMOND_LEN), at(DIAMOND_LEN / 2, -ARROW_HALF)],
                 True, fill, color, width, False)], DIAMOND_LEN
    if kind == "circle":
        c = at(4.0)
        return [("ellipse", c[0], c[1], 4.0, 4.0, "#ffffff", color, width, False)], 8.0
    return [], 0.0


def _shorten(points, amount: float, at_end: bool):
    if amount <= 0 or len(points) < 2:
        return points
    points = list(points)
    i, j = (-1, -2) if at_end else (0, 1)
    tip, before = points[i], points[j]
    d = math.dist(tip, before)
    if d <= amount:
        return points
    t = amount / d
    points[i] = (tip[0] + (before[0] - tip[0]) * t, tip[1] + (before[1] - tip[1]) * t)
    return points


def connector_primitives(diagram: Diagram, c: Connector) -> list:
    points = route(diagram, c)
    if len(points) < 2:
        return []
    out: list = []
    heads: list = []
    start_prims, start_cut = _head(c.start_arrow, points[0], points[1], c.stroke, c.width)
    end_prims, end_cut = _head(c.end_arrow, points[-1], points[-2], c.stroke, c.width)
    line = _shorten(_shorten(points, start_cut, False), end_cut, True)
    out.append(("path", line, False, "none", c.stroke, c.width, c.dashed))
    heads += start_prims + end_prims
    out += heads
    size = 9.5
    if c.label:
        mx, my = midpoint(points)
        lines = c.label.split("\n")
        width = max(text_width(ln, size) for ln in lines) + 6
        height = len(lines) * size * 1.2 + 2
        out.append(("rect", mx - width / 2, my - height / 2, width, height, 2.0, "#ffffff", "none", 0, False))
        _text_block(out, c.label, mx, my - height / 2 + 1, width, size, c.stroke)
    for text, tip, before in ((c.start_label, points[0], points[1]), (c.end_label, points[-1], points[-2])):
        if not text:
            continue
        dx, dy = before[0] - tip[0], before[1] - tip[1]
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d
        px, py = -uy, ux
        lx, ly = tip[0] + ux * 16 + px * 8, tip[1] + uy * 16 + py * 8
        out.append(("text", lx, ly + size * 0.35, text, size, c.stroke, "middle", False, False))
    return out


def primitives(diagram: Diagram) -> list:
    """Alles in Zeichenreihenfolge: erst Verbinder, dann Formen (Formen liegen über Linien)."""
    out: list = []
    for c in diagram.connectors:
        out += connector_primitives(diagram, c)
    for s in diagram.shapes:
        out += shape_primitives(s)
    return out
