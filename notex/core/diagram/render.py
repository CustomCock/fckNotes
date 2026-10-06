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

from notex.core.diagram.model import (CLASS_KINDS, CONTAINER_KINDS, Connector, Diagram, Shape, lifeline_head,
                                      midpoint, route)

ARROW_LEN = 10.0
ARROW_HALF = 5.0
DIAMOND_LEN = 15.0
PAD = 5.0
STEREOTYPES = {"interface": "«interface»", "enum": "«enumeration»"}


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
    elif s.kind in CLASS_KINDS:
        return out + _class_primitives(s)
    elif s.kind in UML_SHAPES:
        return out + UML_SHAPES[s.kind](s)
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


def _section_heights(s: Shape) -> list[float]:
    sections = _sections(s)
    size = s.font_size
    heights = []
    for i, text in enumerate(sections):
        lines = max(1, len(wrap(text, size, s.w - 2 * PAD, i == 0 and s.kind != "object"))) \
            if text.strip() or i == 0 else 0
        if i == 0 and s.kind in STEREOTYPES:
            lines += 1
        heights.append(max(size * 1.2 * lines + 2 * PAD - 2, 10.0 if i else size * 1.2 + 2 * PAD))
    return heights


def class_layout(s: Shape) -> list[tuple[float, float]]:
    """(oben, unten) je Abschnitt einer UML-Klasse – Höhe aus dem Text, Rest bekommt der letzte Abschnitt."""
    heights = _section_heights(s)
    out, top = [], s.y
    for i, height in enumerate(heights):
        bottom = s.y + s.h if i == len(heights) - 1 else min(s.y + s.h, top + height)
        out.append((top, max(top, bottom)))
        top = bottom
    return out


def min_class_height(s: Shape) -> float:
    return sum(_section_heights(s))


def _class_primitives(s: Shape) -> list:
    out: list = [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    sections = s.class_sections()
    for i, ((top, bottom), text) in enumerate(zip(class_layout(s), sections)):
        if i:
            out.append(("path", [(s.x, top), (s.x + s.w, top)], False, "none", s.stroke, 1.0, False))
        if i == 0 and s.kind in STEREOTYPES:
            out.append(("text", s.x + s.w / 2, top + PAD - 1 + s.font_size * 0.93, STEREOTYPES[s.kind],
                        s.font_size, s.text_color, "middle", False, False))
            top += s.font_size * 1.2
        if not text.strip():
            continue
        if i == 0:
            name, italic = class_name(text)
            first = len(out)
            _text_block(out, name, s.x + s.w / 2, top + PAD - 1, s.w - 2 * PAD, s.font_size, s.text_color,
                        bold=s.kind != "object", italic=italic)
            if s.kind == "object":                           # Objektname unterstrichen
                for prim in out[first:]:
                    half = text_width(prim[3], prim[4]) / 2
                    out.append(("path", [(prim[1] - half, prim[2] + 1.5), (prim[1] + half, prim[2] + 1.5)], False,
                                "none", s.text_color, 0.8, False))
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
    if kind in ("circle", "dot"):
        c = at(4.0)
        return [("ellipse", c[0], c[1], 4.0, 4.0, color if kind == "dot" else "#ffffff", color, width, False)], 8.0
    if kind == "cross":                                     # nicht navigierbar: × kurz vor dem Ende
        return [("path", [at(5, 4), at(13, -4)], False, "none", color, width, False),
                ("path", [at(5, -4), at(13, 4)], False, "none", color, width, False)], 0.0
    if kind == "containment":                               # Kreis mit Plus
        c, r = at(6.0), 6.0
        return [("ellipse", c[0], c[1], r, r, "#ffffff", color, width, False),
                ("path", [at(0.0), at(12.0)], False, "none", color, width, False),
                ("path", [at(6.0, r), at(6.0, -r)], False, "none", color, width, False)], 12.0
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
    for s in diagram.shapes:                                # Rahmen, Bereiche, Lebenslinien ganz hinten
        if s.kind in CONTAINER_KINDS:
            out += shape_primitives(s)
    for c in diagram.connectors:
        out += connector_primitives(diagram, c)
    for s in diagram.shapes:
        if s.kind not in CONTAINER_KINDS:
            out += shape_primitives(s)
    return out


# ---- weitere UML-Symbole ---------------------------------------------------------------------------------------
def _below(out: list, s: Shape) -> list:
    """Beschriftung unter kleinen Symbolen (Akteur-Stil)."""
    if s.text:
        _text_block(out, s.text, s.x + s.w / 2, s.y + s.h + 3, max(s.w * 4, 80.0), s.font_size, s.text_color,
                    bold=s.bold)
    return out


def _centered(out: list, s: Shape, left: float, top: float, width: float, height: float) -> list:
    if s.text:
        inner = max(10.0, width - 2 * PAD)
        y = top + height / 2 - _text_height(s.text, s.font_size, inner, s.bold) / 2
        _text_block(out, s.text, left + width / 2, y, inner, s.font_size, s.text_color, bold=s.bold)
    return out


def _x_lines(cx: float, cy: float, r: float, color: str, width: float) -> list:
    return [("path", [(cx - r, cy - r), (cx + r, cy + r)], False, "none", color, width, False),
            ("path", [(cx - r, cy + r), (cx + r, cy - r)], False, "none", color, width, False)]


def _lollipop(s: Shape) -> list:
    r = min(s.w, s.h) / 2
    return _below([("ellipse", s.x + s.w / 2, s.y + s.h / 2, r, r, s.fill, s.stroke, 1.2, s.dashed)], s)


def _socket(s: Shape) -> list:
    r = min(s.w, s.h / 2)                                  # „(“ – linker Rand liegt am Andockpunkt w2
    arc = _arc(s.x + r, s.y + s.h / 2, r, r, 90, 270)
    return _below([("path", arc, False, "none", s.stroke, 1.2, s.dashed)], s)


def _port(s: Shape) -> list:
    return _below([("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)], s)


def _component(s: Shape) -> list:
    out = [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    ix, iy = s.x + s.w - 22, s.y + 6                        # Komponenten-Symbol oben rechts
    out.append(("rect", ix, iy, 14, 16, 0.0, s.fill, s.stroke, 1.0, False))
    for dy in (3, 9):
        out.append(("rect", ix - 4, iy + dy, 8, 4, 0.0, s.fill, s.stroke, 1.0, False))
    return _centered(out, s, s.x, s.y, s.w - 16, s.h)


def _node(s: Shape) -> list:
    d = min(12.0, s.w / 6, s.h / 6)
    x, y, w, h = s.x, s.y, s.w, s.h
    out = [("path", [(x, y + d), (x + d, y), (x + w, y), (x + w - d, y + d)], True, s.fill, s.stroke, 1.2, s.dashed),
           ("path", [(x + w - d, y + d), (x + w, y), (x + w, y + h - d), (x + w - d, y + h)], True, s.fill,
            s.stroke, 1.2, s.dashed),
           ("rect", x, y + d, w - d, h - d, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    if s.text:
        _text_block(out, s.text, x + (w - d) / 2, y + d + PAD, w - d - 2 * PAD, s.font_size, s.text_color,
                    bold=True)
    return out


def _artifact(s: Shape) -> list:
    out = [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    ix, iy, iw, ih, f = s.x + s.w - 18, s.y + 5, 11.0, 14.0, 4.0     # Dokument-Symbol oben rechts
    out.append(("path", [(ix, iy), (ix + iw - f, iy), (ix + iw, iy + f), (ix + iw, iy + ih), (ix, iy + ih)], True,
                s.fill, s.stroke, 0.9, False))
    out.append(("path", [(ix + iw - f, iy), (ix + iw - f, iy + f), (ix + iw, iy + f)], False, "none", s.stroke, 0.9,
                False))
    return _centered(out, s, s.x, s.y, s.w - 14, s.h)


def _fork(s: Shape) -> list:
    return [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.fill if s.fill != "none" else s.stroke, 0.5, False)]


def _flowfinal(s: Shape) -> list:
    cx, cy = s.center
    r = min(s.w, s.h) / 2
    out = [("ellipse", cx, cy, r, r, "#ffffff" if s.fill == "none" else s.fill, s.stroke, 1.2, s.dashed)]
    return _below(out + _x_lines(cx, cy, r * 0.707, s.stroke, 1.2), s)


def _send(s: Shape) -> list:
    tip = min(s.h / 2, s.w / 4)
    x, y, w, h = s.x, s.y, s.w, s.h
    out = [("path", [(x, y), (x + w - tip, y), (x + w, y + h / 2), (x + w - tip, y + h), (x, y + h)], True, s.fill,
            s.stroke, 1.2, s.dashed)]
    return _centered(out, s, x, y, w - tip, h)


def _receive(s: Shape) -> list:
    notch = min(s.h / 2, s.w / 4)
    x, y, w, h = s.x, s.y, s.w, s.h
    out = [("path", [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + notch, y + h / 2)], True, s.fill,
            s.stroke, 1.2, s.dashed)]
    return _centered(out, s, x + notch, y, w - notch, h)


def _timeevent(s: Shape) -> list:
    x, y, w, h = s.x, s.y, s.w, s.h
    return _below([("path", [(x, y), (x + w, y), (x, y + h), (x + w, y + h)], True, s.fill, s.stroke, 1.2,
                    s.dashed)], s)


def _header_height(s: Shape) -> float:
    return s.font_size * 1.2 + 2 * PAD


def _partition(s: Shape) -> list:
    head = _header_height(s)
    out = [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed),
           ("path", [(s.x, s.y + head), (s.x + s.w, s.y + head)], False, "none", s.stroke, 1.2, False)]
    if s.text:
        _text_block(out, s.text.split("\n")[0], s.x + s.w / 2, s.y + PAD, s.w - 2 * PAD, s.font_size,
                    s.text_color, bold=True)
    return out


def _state(s: Shape) -> list:
    out = [("rect", s.x, s.y, s.w, s.h, min(12.0, s.h / 3, s.w / 3), s.fill, s.stroke, 1.2, s.dashed)]
    sections = s.class_sections()
    if len(sections) == 1:
        return _centered(out, s, s.x, s.y, s.w, s.h)
    name = sections[0]
    bottom = _text_block(out, name, s.x + s.w / 2, s.y + PAD, s.w - 2 * PAD, s.font_size, s.text_color,
                         bold=s.bold) + PAD - 2
    out.append(("path", [(s.x, bottom), (s.x + s.w, bottom)], False, "none", s.stroke, 1.0, False))
    rest = "\n".join(sections[1:]).strip("\n")
    if rest:
        _text_block(out, rest, s.x + s.w / 2, bottom + PAD - 2, s.w - 2 * PAD, s.font_size, s.text_color, "start",
                    left=s.x + PAD)
    return out


def _history(s: Shape) -> list:
    cx, cy = s.center
    r = min(s.w, s.h) / 2
    letter = "H*" if s.kind == "deephistory" else "H"
    size = r * 1.05
    out = [("ellipse", cx, cy, r, r, "#ffffff" if s.fill == "none" else s.fill, s.stroke, 1.2, s.dashed),
           ("text", cx, cy + size * 0.36, letter, size, s.text_color, "middle", False, False)]
    return _below(out, s)


def _entrypoint(s: Shape) -> list:
    cx, cy = s.center
    r = min(s.w, s.h) / 2
    out = [("ellipse", cx, cy, r, r, "#ffffff" if s.fill == "none" else s.fill, s.stroke, 1.2, s.dashed)]
    if s.kind == "exitpoint":
        out += _x_lines(cx, cy, r * 0.707, s.stroke, 1.2)
    return _below(out, s)


def _cross(s: Shape) -> list:
    cx, cy = s.center
    return _below(_x_lines(cx, cy, min(s.w, s.h) / 2, s.stroke, 1.6), s)


def _lifeline(s: Shape) -> list:
    head = lifeline_head(s)
    cx = s.x + s.w / 2
    out = [("path", [(cx, s.y + head), (cx, s.y + s.h)], False, "none", s.stroke, 1.0, True),
           ("rect", s.x, s.y, s.w, head, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    return _centered(out, s, s.x, s.y, s.w, head)


def _activation(s: Shape) -> list:
    return [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)]


def _frame(s: Shape) -> list:
    sections = s.class_sections()
    label = sections[0].strip() or " "
    size = s.font_size
    lw = min(s.w, text_width(label, size, True) + 2 * PAD + 8)
    lh = min(s.h, size * 1.2 + 6)
    x, y = s.x, s.y
    out = [("rect", x, y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed),
           ("path", [(x, y), (x + lw, y), (x + lw, y + lh - 5), (x + lw - 6, y + lh), (x, y + lh)], True,
            "#ffffff" if s.fill == "none" else s.fill, s.stroke, 1.0, False),
           ("text", x + PAD, y + 3 + size * 0.93, label, size, s.text_color, "start", True, False)]
    guards = sections[1:]
    if guards:                                              # Operanden (alt/par …): gleich hohe Streifen
        band = (s.h - lh) / len(guards)
        for i, guard in enumerate(guards):
            top = y + lh + i * band
            if i:
                out.append(("path", [(x, top), (x + s.w, top)], False, "none", s.stroke, 1.0, True))
            if guard.strip():
                _text_block(out, guard.strip(), x + s.w / 2, top + 3, s.w - 2 * PAD, size, s.text_color, "start",
                            left=x + PAD)
    return out


def _system(s: Shape) -> list:
    out = [("rect", s.x, s.y, s.w, s.h, 0.0, s.fill, s.stroke, 1.2, s.dashed)]
    if s.text:
        _text_block(out, s.text, s.x + s.w / 2, s.y + PAD, s.w - 2 * PAD, s.font_size, s.text_color, bold=True)
    return out


def _robustness(s: Shape) -> list:
    fill = "#ffffff" if s.fill == "none" else s.fill
    out: list = []
    if s.kind == "boundary":                                # |—○
        r = min(s.h / 2, (s.w - 10) / 2)
        cx, cy = s.x + s.w - r, s.y + s.h / 2
        out += [("path", [(s.x, cy - r), (s.x, cy + r)], False, "none", s.stroke, 1.2, False),
                ("path", [(s.x, cy), (cx - r, cy)], False, "none", s.stroke, 1.2, False),
                ("ellipse", cx, cy, r, r, fill, s.stroke, 1.2, s.dashed)]
    elif s.kind == "control":                               # ○ mit Pfeil oben
        r = min(s.w, s.h - 4) / 2
        cx, cy = s.x + s.w / 2, s.y + s.h - r
        top = cy - r
        out += [("ellipse", cx, cy, r, r, fill, s.stroke, 1.2, s.dashed),
                ("path", [(cx + 5, top - 4), (cx, top), (cx + 5, top + 4)], False, "none", s.stroke, 1.2, False)]
    else:                                                   # entity: ○ mit Strich darunter
        r = min(s.w, s.h - 4) / 2
        cx, cy = s.x + s.w / 2, s.y + r
        out += [("ellipse", cx, cy, r, r, fill, s.stroke, 1.2, s.dashed),
                ("path", [(cx - r, s.y + s.h), (cx + r, s.y + s.h)], False, "none", s.stroke, 1.2, False)]
    return _below(out, s)


UML_SHAPES = {
    "lollipop": _lollipop, "socket": _socket, "port": _port, "component": _component, "node": _node,
    "artifact": _artifact, "fork": _fork, "flowfinal": _flowfinal, "send": _send, "receive": _receive,
    "timeevent": _timeevent, "partition": _partition, "state": _state, "history": _history,
    "deephistory": _history, "entrypoint": _entrypoint, "exitpoint": _entrypoint, "cross": _cross,
    "lifeline": _lifeline, "activation": _activation, "frame": _frame, "system": _system,
    "boundary": _robustness, "control": _robustness, "entity": _robustness,
}
