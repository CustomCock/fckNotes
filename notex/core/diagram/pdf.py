"""Zeichen-Grundelemente → PDF-Inhalt (Qt-frei). Koordinaten werden gespiegelt (PDF: y nach oben), Text in der
mitgelieferten Noto Sans (vier Schnitte), eingebettet als Untermenge über `pdffont.FontUsage`."""
from __future__ import annotations

from notex.core import pdffont
from notex.core.diagram.model import Diagram
from notex.core.diagram.render import primitives, text_width

K = 0.5522847498            # Bézier-Näherung für Viertelkreise


def _n(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _rgb(color: str, op: str) -> str:
    from notex.core.pdfannot import rgb
    r, g, b = rgb(color)
    return f"{_n(r)} {_n(g)} {_n(b)} {op}"


def _paint(fill: str, stroke: str, width: float, dashed: bool) -> tuple[list[str], str]:
    """Farb-/Linien-Einstellungen und der passende Mal-Operator (f/S/B) – oder "" = nichts malen."""
    has_fill = bool(fill) and fill != "none"
    has_stroke = bool(stroke) and stroke != "none" and width > 0
    ops = []
    if has_fill:
        ops.append(_rgb(fill, "rg"))
    if has_stroke:
        ops.append(_rgb(stroke, "RG"))
        ops.append(f"{_n(width)} w")
        ops.append("[4 3] 0 d" if dashed else "[] 0 d")
    op = "B" if has_fill and has_stroke else "f" if has_fill else "S" if has_stroke else ""
    return ops, op


def content(diagram: Diagram, use: pdffont.FontUsage | None = None) -> str:
    """PDF-Content-Stream für ein Formular-XObject mit BBox [0 0 width height]. `use` sammelt die Zeichen für die
    eingebettete Schrift (danach `font_resources(writer, use)`)."""
    use = use if use is not None else pdffont.FontUsage()
    height = diagram.height

    def P(x, y):
        return f"{_n(x)} {_n(height - y)}"
    out = ["q 1 J 1 j"]
    for prim in primitives(diagram):
        kind = prim[0]
        if kind == "rect":
            _k, x, y, w, h, r, fill, stroke, width, dashed = prim
            ops, op = _paint(fill, stroke, width, dashed)
            if not op:
                continue
            out.append("q " + " ".join(ops))
            if r <= 0:
                out.append(f"{_n(x)} {_n(height - y - h)} {_n(w)} {_n(h)} re {op} Q")
                continue
            r = min(r, w / 2, h / 2)
            c = r * (1 - K)
            out.append(" ".join([
                f"{P(x + r, y)} m", f"{P(x + w - r, y)} l", f"{P(x + w - c, y)} {P(x + w, y + c)} {P(x + w, y + r)} c",
                f"{P(x + w, y + h - r)} l", f"{P(x + w, y + h - c)} {P(x + w - c, y + h)} {P(x + w - r, y + h)} c",
                f"{P(x + r, y + h)} l", f"{P(x + c, y + h)} {P(x, y + h - c)} {P(x, y + h - r)} c",
                f"{P(x, y + r)} l", f"{P(x, y + c)} {P(x + c, y)} {P(x + r, y)} c", f"h {op} Q"]))
        elif kind == "ellipse":
            _k, cx, cy, rx, ry, fill, stroke, width, dashed = prim
            ops, op = _paint(fill, stroke, width, dashed)
            if not op or rx <= 0 or ry <= 0:
                continue
            kx, ky = rx * K, ry * K
            out.append("q " + " ".join(ops))
            out.append(" ".join([
                f"{P(cx + rx, cy)} m",
                f"{P(cx + rx, cy + ky)} {P(cx + kx, cy + ry)} {P(cx, cy + ry)} c",
                f"{P(cx - kx, cy + ry)} {P(cx - rx, cy + ky)} {P(cx - rx, cy)} c",
                f"{P(cx - rx, cy - ky)} {P(cx - kx, cy - ry)} {P(cx, cy - ry)} c",
                f"{P(cx + kx, cy - ry)} {P(cx + rx, cy - ky)} {P(cx + rx, cy)} c", f"h {op} Q"]))
        elif kind == "path":
            _k, points, closed, fill, stroke, width, dashed = prim
            if not closed:
                fill = "none"
            ops, op = _paint(fill, stroke, width, dashed)
            if not op or len(points) < 2:
                continue
            path = [f"{P(*points[0])} m"] + [f"{P(*p)} l" for p in points[1:]]
            out.append("q " + " ".join(ops) + " " + " ".join(path) + (" h " if closed else " ") + f"{op} Q")
        elif kind == "text":
            _k, x, y, text, size, color, anchor, bold, italic = prim
            if not text:
                continue
            width = text_width(text, size, bold, italic)
            left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
            font = use.name(bold, italic)
            out.append(f"BT {font} {_n(size)} Tf {_rgb(color, 'rg')} {P(left, y)} Td {use.encode(text, bold, italic)} "
                       f"Tj ET")
    out.append("Q")
    return "\n".join(out)


def font_resources(writer, use: pdffont.FontUsage):
    """Ressourcen-Dictionary mit den benutzten Noto-Schnitten (Untermengen) für das Formular-XObject."""
    return use.resources(writer)
