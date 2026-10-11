"""Zeichnen auf PDF-Seiten (Qt-frei): Freihand-Stift, Radierer und Formen als echte Anmerkungen.

- Stift: `/Ink` – ein Strich pro Anmerkung; die Punkte werden vereinfacht (Ramer-Douglas-Peucker) und auf Wunsch als
  Catmull-Rom-Kurve geglättet gezeichnet.
- Radierer: entfernt **ganze Striche** (aus `/InkList`; ist keiner mehr übrig, die ganze Anmerkung) – nie halbe.
- Formen: Rechteck (`/Square`), Ellipse (`/Circle`), Linie und Pfeil (`/Line`, `/LE`), Polygon (`/Polygon`), Wolke
  (Rechteck oder Polygon mit `/BE /S /C`).

Jede Form bekommt ein eigenes Erscheinungsbild (/AP) – Wolkenrand, Strichelung und Deckkraft sähen sonst in jedem
Betrachter anders aus. Deckkraft steht als `/CA` an der Anmerkung und als ExtGState (`/CA`, `/ca`) im Bild.
Geometrie (`/InkList`, `/L`, `/Vertices`, `/RD`) bleibt die Wahrheit: Verschieben/Größe ändern rechnet sie um und
zeichnet neu (`transform`), Stil ändern ebenso (`restyle`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError

Point = tuple[float, float]
DASHES = ("solid", "dashed", "dotted")
SHAPES = ("rect", "ellipse", "line", "arrow", "polygon", "cloud")
SUBTYPES = {"rect": "/Square", "cloud": "/Square", "ellipse": "/Circle", "line": "/Line", "arrow": "/Line",
            "polygon": "/Polygon"}
DRAW_KINDS = {"ink", "square", "circle", "line", "polygon", "polyline"}
KAPPA = 0.5522847498
MAX_POINTS = 5000


@dataclass(frozen=True)
class Style:
    color: str = "#d03030"
    width: float = 2.0
    fill: str | None = None          # Füllfarbe (nur Rechteck, Ellipse, Polygon, Wolke)
    opacity: float = 1.0
    dash: str = "solid"              # solid | dashed | dotted
    smooth: bool = True              # nur Stift

    def checked(self) -> "Style":
        if self.dash not in DASHES:
            raise PdfEditError(f"Unbekannter Linienstil „{self.dash}“")
        for color in (self.color, self.fill) if self.fill else (self.color,):
            if not A.valid_color(color):
                raise PdfEditError(f"Ungültige Farbe „{color}“")
        return replace(self, width=max(0.25, min(float(self.width), 50.0)),
                       opacity=max(0.05, min(float(self.opacity), 1.0)))


# ---- Geometrie ---------------------------------------------------------------------------------------------------
def simplify(points: list[Point], tolerance: float = 0.6) -> list[Point]:
    """Ramer-Douglas-Peucker: Punkte weglassen, die weniger als `tolerance` von der Linie abweichen."""
    points = [p for i, p in enumerate(points) if i == 0 or math.dist(p, points[i - 1]) > 1e-6]
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = 0.0, -1
        for i in range(first + 1, last):
            d = _segment_distance(points[i], points[first], points[last])
            if d > best:
                best, index = d, i
        if index >= 0 and best > tolerance:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, k in zip(points, keep) if k]


def _segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length = dx * dx + dy * dy
    if length < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def polyline_distance(p: Point, points: list[Point]) -> float:
    if not points:
        return math.inf
    if len(points) == 1:
        return math.dist(p, points[0])
    return min(_segment_distance(p, points[i], points[i + 1]) for i in range(len(points) - 1))


def _n(value: float) -> str:
    return A._num(value)


def _path(points: list[Point], smooth: bool) -> str:
    """PDF-Pfad durch die Punkte; geglättet als Catmull-Rom → kubische Bézier-Kurven."""
    if not points:
        return ""
    if len(points) == 1:                                   # Punkt: winziger Strich, damit die Kappe sichtbar ist
        x, y = points[0]
        return f"{_n(x)} {_n(y)} m {_n(x + 0.01)} {_n(y)} l"
    ops = [f"{_n(points[0][0])} {_n(points[0][1])} m"]
    if not smooth or len(points) < 3:
        ops += [f"{_n(x)} {_n(y)} l" for x, y in points[1:]]
        return " ".join(ops)
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        ops.append(f"{_n(c1[0])} {_n(c1[1])} {_n(c2[0])} {_n(c2[1])} {_n(p2[0])} {_n(p2[1])} c")
    return " ".join(ops)


def _ellipse(x0: float, y0: float, x1: float, y1: float) -> str:
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    kx, ky = rx * KAPPA, ry * KAPPA
    return (f"{_n(cx + rx)} {_n(cy)} m "
            f"{_n(cx + rx)} {_n(cy + ky)} {_n(cx + kx)} {_n(cy + ry)} {_n(cx)} {_n(cy + ry)} c "
            f"{_n(cx - kx)} {_n(cy + ry)} {_n(cx - rx)} {_n(cy + ky)} {_n(cx - rx)} {_n(cy)} c "
            f"{_n(cx - rx)} {_n(cy - ky)} {_n(cx - kx)} {_n(cy - ry)} {_n(cx)} {_n(cy - ry)} c "
            f"{_n(cx + kx)} {_n(cy - ry)} {_n(cx + rx)} {_n(cy - ky)} {_n(cx + rx)} {_n(cy)} c h")


def cloud_radius(width: float) -> float:
    return 4.0 + width


def _cloud(vertices: list[Point], radius: float) -> str:
    """Wolkenrand: Bögen (Halbkreise als je eine Bézier-Kurve) entlang jeder Kante, nach außen gewölbt."""
    pts = [p for i, p in enumerate(vertices) if math.dist(p, vertices[i - 1]) > 1e-6] if len(vertices) > 1 else []
    if len(pts) < 2:
        return ""
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts)))
    if area < 0:                                           # gegen den Uhrzeigersinn → Normale (dy, -dx) zeigt nach außen
        pts = list(reversed(pts))
    ops = [f"{_n(pts[0][0])} {_n(pts[0][1])} m"]
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        length = math.dist(a, b)
        count = max(1, round(length / (2 * radius)))
        dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        nx, ny = dy, -dx
        step = length / count
        k = 4 / 3 * step / 2
        for j in range(count):
            s = (a[0] + dx * step * j, a[1] + dy * step * j)
            e = (a[0] + dx * step * (j + 1), a[1] + dy * step * (j + 1))
            ops.append(f"{_n(s[0] + nx * k)} {_n(s[1] + ny * k)} {_n(e[0] + nx * k)} {_n(e[1] + ny * k)} "
                       f"{_n(e[0])} {_n(e[1])} c")
    ops.append("h")
    return " ".join(ops)


def _arrow_head(a: Point, b: Point, width: float) -> str:
    length = max(6.0, 3 * width + 4)
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    left = (b[0] - length * math.cos(angle - math.pi / 6), b[1] - length * math.sin(angle - math.pi / 6))
    right = (b[0] - length * math.cos(angle + math.pi / 6), b[1] - length * math.sin(angle + math.pi / 6))
    return f"{_n(left[0])} {_n(left[1])} m {_n(b[0])} {_n(b[1])} l {_n(right[0])} {_n(right[1])} l S"


def _dash_ops(dash: str, width: float) -> str:
    if dash == "dashed":
        return f"[{_n(max(3.0, 3 * width))} {_n(max(2.0, 2 * width))}] 0 d"
    if dash == "dotted":
        return f"[0 {_n(max(2.0, 2 * width))}] 0 d"
    return "[] 0 d"


# ---- Stil lesen/schreiben ------------------------------------------------------------------------------------------
def style_of(annot) -> Style:
    bs = annot.get("/BS")
    bs = bs.get_object() if bs is not None else {}
    width = float(bs.get("/W", 1) or 1) if bs else 1.0
    dash = "solid"
    if bs and str(bs.get("/S", "")) == "/D":
        pattern = [float(v) for v in (bs.get("/D") or [3])]
        dash = "dotted" if pattern and pattern[0] == 0 else "dashed"
    fill = A._hex(annot.get("/IC", [])) or None
    try:
        opacity = float(annot.get("/CA", 1))
    except (TypeError, ValueError):
        opacity = 1.0
    smooth = bool(annot.get("/fckNotesSmooth", True))
    return Style(A._hex(annot.get("/C", [])) or "#000000", width, fill, opacity, dash, smooth)


def _is_cloud(annot) -> bool:
    be = annot.get("/BE")
    return be is not None and str(be.get_object().get("/S", "")) == "/C"


def _write_style(annot: DictionaryObject, style: Style) -> None:
    r, g, b = A.rgb(style.color)
    annot[NameObject("/C")] = A._floats([r, g, b])
    bs = DictionaryObject({NameObject("/Type"): NameObject("/Border"), NameObject("/W"): FloatObject(style.width),
                           NameObject("/S"): NameObject("/S" if style.dash == "solid" else "/D")})
    if style.dash == "dashed":
        bs[NameObject("/D")] = A._floats([max(3.0, 3 * style.width), max(2.0, 2 * style.width)])
    elif style.dash == "dotted":
        bs[NameObject("/D")] = A._floats([0, max(2.0, 2 * style.width)])
    annot[NameObject("/BS")] = bs
    if style.fill and annot.get("/Subtype") in ("/Square", "/Circle", "/Polygon"):
        annot[NameObject("/IC")] = A._floats(A.rgb(style.fill))
    elif "/IC" in annot:
        del annot["/IC"]
    if style.opacity < 1:
        annot[NameObject("/CA")] = FloatObject(round(style.opacity, 3))
    elif "/CA" in annot:
        del annot["/CA"]


def _resources(style: Style) -> DictionaryObject:
    if style.opacity >= 1:
        return DictionaryObject()
    state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                              NameObject("/CA"): FloatObject(round(style.opacity, 3)),
                              NameObject("/ca"): FloatObject(round(style.opacity, 3))})
    return DictionaryObject({NameObject("/ExtGState"): DictionaryObject({NameObject("/GS0"): state})})


def _floats_list(array) -> list[float]:
    return [float(v) for v in (array.get_object() if hasattr(array, "get_object") else array)]


def _pairs(values: list[float]) -> list[Point]:
    return [(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]


# ---- Erscheinungsbild ------------------------------------------------------------------------------------------
def redraw(writer, annot: DictionaryObject, style: Style | None = None) -> None:
    """/Rect und /AP aus Geometrie und Stil neu berechnen (Stil `None` = aus der Anmerkung lesen)."""
    style = (style or style_of(annot)).checked()
    _write_style(annot, style)
    subtype = str(annot.get("/Subtype"))
    w = style.width
    head = ["q"]
    if style.opacity < 1:
        head.append("/GS0 gs")
    head += [A._rgb_op(style.color, "RG"), f"{_n(w)} w", "1 J 1 j", _dash_ops(style.dash, w)]
    if style.fill:
        head.append(A._rgb_op(style.fill, "rg"))
    paint = "B" if style.fill and subtype in ("/Square", "/Circle", "/Polygon") else "S"
    if subtype == "/Ink":
        strokes = [_pairs(_floats_list(s)) for s in annot.get("/InkList", [])]
        strokes = [s for s in strokes if s]
        if not strokes:
            raise PdfEditError("Der Strich ist leer")
        body = [(_path(s, style.smooth) + " S") for s in strokes]
        xs = [p[0] for s in strokes for p in s]
        ys = [p[1] for s in strokes for p in s]
        pad = w / 2 + 1
        rect = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]
        annot[NameObject("/fckNotesSmooth")] = NumberObject(1 if style.smooth else 0)
    elif subtype in ("/Square", "/Circle"):
        x0, y0, x1, y1 = _inner_rect(annot)
        cloud = subtype == "/Square" and _is_cloud(annot)
        pad = w / 2 + 1 + (cloud_radius(w) if cloud else 0)
        if subtype == "/Circle":
            body = [_ellipse(x0, y0, x1, y1) + f" {paint}"]
        elif cloud:
            body = [_cloud([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], cloud_radius(w)) + f" {paint}"]
        else:
            body = [f"{_n(x0)} {_n(y0)} {_n(x1 - x0)} {_n(y1 - y0)} re {paint}"]
        rect = [x0 - pad, y0 - pad, x1 + pad, y1 + pad]
        annot[NameObject("/RD")] = A._floats([pad] * 4)
    elif subtype == "/Line":
        x0, y0, x1, y1 = _floats_list(annot["/L"])[:4]
        body = [f"{_n(x0)} {_n(y0)} m {_n(x1)} {_n(y1)} l S"]
        ends = [str(e) for e in annot.get("/LE", [])] or ["/None", "/None"]
        pad = w / 2 + 1
        if any(e != "/None" for e in ends):
            body.append("[] 0 d")                         # Pfeilspitzen immer durchgezogen
            if ends[-1] != "/None":
                body.append(_arrow_head((x0, y0), (x1, y1), w))
            if ends[0] != "/None":
                body.append(_arrow_head((x1, y1), (x0, y0), w))
            pad += max(6.0, 3 * w + 4)
        rect = [min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad]
    elif subtype in ("/Polygon", "/PolyLine"):
        points = _pairs(_floats_list(annot["/Vertices"]))
        if len(points) < 2:
            raise PdfEditError("Ein Polygon braucht mindestens zwei Punkte")
        cloud = subtype == "/Polygon" and _is_cloud(annot)
        if cloud:
            body = [_cloud(points, cloud_radius(w)) + f" {paint}"]
        else:
            body = [_path(points, False) + (f" h {paint}" if subtype == "/Polygon" else " S")]
        pad = w / 2 + 1 + (cloud_radius(w) if cloud else 0)
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        rect = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]
    else:
        raise PdfEditError("Diese Anmerkung lässt sich hier nicht zeichnen")
    annot[NameObject("/Rect")] = A._floats(rect)
    content = "\n".join(head + body + ["Q"])
    annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): A._form(writer, content, rect, _resources(style))})


def _inner_rect(annot) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = _floats_list(annot["/Rect"])
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    rd = annot.get("/RD")
    if rd is not None:
        l, b, r, t = _floats_list(rd)[:4]
        if x1 - x0 > l + r and y1 - y0 > b + t:
            return x0 + l, y0 + b, x1 - r, y1 - t
    return x0, y0, x1, y1


# ---- Anlegen ---------------------------------------------------------------------------------------------------
def _new(subtype: str, style: Style) -> DictionaryObject:
    return A._base(subtype, [0, 0, 1, 1], style.color)


def add_ink(data: bytes, page_index: int, strokes: list[list[Point]], style: Style = Style()) -> bytes:
    """Freihand: `strokes` = Striche in Ansichts-Punkten; jeder Strich wird eine eigene /Ink-Anmerkung."""
    style = style.checked()
    writer = A.open_writer(data)
    geom = A.geometry_of(A._page(writer, page_index))
    added = 0
    for stroke in strokes:
        points = simplify([geom.to_pdf(x, y) for x, y in stroke[:MAX_POINTS]], 0.4)
        if not points:
            continue
        annot = _new("/Ink", replace(style, fill=None))
        annot[NameObject("/InkList")] = ArrayObject([A._floats([c for p in points for c in p])])
        redraw(writer, annot, replace(style, fill=None))
        A._attach(writer, page_index, annot)
        added += 1
    if not added:
        raise PdfEditError("Nichts gezeichnet")
    return A.finish(writer)


def add_shape(data: bytes, page_index: int, kind: str, points: list[Point], style: Style = Style()) -> bytes:
    """Form anlegen. `points` in Ansichts-Punkten: Rechteck/Ellipse/Wolke = zwei Ecken, Linie/Pfeil = Anfang und
    Ende, Polygon = alle Ecken (mind. drei)."""
    if kind not in SHAPES:
        raise PdfEditError(f"Unbekannte Form „{kind}“")
    style = style.checked()
    if kind in ("line", "arrow"):
        style = replace(style, fill=None)
    writer = A.open_writer(data)
    geom = A.geometry_of(A._page(writer, page_index))
    pdf = [geom.to_pdf(x, y) for x, y in points[:MAX_POINTS]]
    annot = _new(SUBTYPES[kind], style)
    if kind in ("rect", "ellipse", "cloud"):
        if len(pdf) < 2:
            raise PdfEditError("Bitte einen Bereich aufziehen")
        xs, ys = [p[0] for p in pdf], [p[1] for p in pdf]
        if max(xs) - min(xs) < 2 or max(ys) - min(ys) < 2:
            raise PdfEditError("Die Form ist zu klein")
        annot[NameObject("/Rect")] = A._floats([min(xs), min(ys), max(xs), max(ys)])
        if kind == "cloud":
            annot[NameObject("/BE")] = DictionaryObject({NameObject("/S"): NameObject("/C"),
                                                         NameObject("/I"): NumberObject(1)})
    elif kind in ("line", "arrow"):
        if len(pdf) < 2 or math.dist(pdf[0], pdf[-1]) < 2:
            raise PdfEditError("Die Linie ist zu kurz")
        annot[NameObject("/L")] = A._floats([*pdf[0], *pdf[-1]])
        end = "/OpenArrow" if kind == "arrow" else "/None"
        annot[NameObject("/LE")] = ArrayObject([NameObject("/None"), NameObject(end)])
    else:
        pdf = simplify(pdf, 0.01)
        if len(pdf) > 3 and math.dist(pdf[0], pdf[-1]) < 0.5:
            pdf = pdf[:-1]                                # doppelter Schlusspunkt
        if len(pdf) < 3:
            raise PdfEditError("Ein Polygon braucht mindestens drei Ecken")
        annot[NameObject("/Vertices")] = A._floats([c for p in pdf for c in p])
    redraw(writer, annot, style)
    A._attach(writer, page_index, annot)
    return A.finish(writer)


# ---- Radierer -------------------------------------------------------------------------------------------------
def erase_at(data: bytes, page_index: int, points: list[Point], tolerance: float = 4.0) -> tuple[bytes, int]:
    """Alle Freihand-Striche entfernen, die einer der Radierer-Positionen (Ansichts-Punkte) nahe kommen.
    Liefert (neue Daten, Anzahl entfernter Striche); andere Anmerkungen bleiben unberührt."""
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    annots = A._annots(page)
    removed = 0
    for position in range(len(annots) - 1, -1, -1):
        annot = annots[position].get_object()
        if annot.get("/Subtype") != "/Ink":
            continue
        width = style_of(annot).width
        strokes = [_pairs(_floats_list(s)) for s in annot.get("/InkList", [])]
        keep = []
        for stroke in strokes:
            view = [geom.to_view(x, y) for x, y in stroke]
            if any(polyline_distance(p, view) <= tolerance + width / 2 for p in points):
                removed += 1
            else:
                keep.append(stroke)
        if len(keep) == len(strokes):
            continue
        if not keep:
            popup = annot.get("/Popup")
            del annots[position]
            if popup is not None:
                for i in range(len(annots) - 1, -1, -1):
                    if annots[i].get_object() is popup.get_object():
                        del annots[i]
            continue
        annot[NameObject("/InkList")] = ArrayObject([A._floats([c for p in s for c in p]) for s in keep])
        redraw(writer, annot)
    if not removed:
        return data, 0
    if not annots:
        del page["/Annots"]
    return A.finish(writer), removed


# ---- Verschieben, Größe, Stil --------------------------------------------------------------------------------------
def _geometry_points(annot) -> list[Point]:
    subtype = str(annot.get("/Subtype"))
    if subtype == "/Ink":
        return [p for s in annot.get("/InkList", []) for p in _pairs(_floats_list(s))]
    if subtype in ("/Square", "/Circle"):
        x0, y0, x1, y1 = _inner_rect(annot)
        return [(x0, y0), (x1, y1)]
    if subtype == "/Line":
        return _pairs(_floats_list(annot["/L"])[:4])
    if subtype in ("/Polygon", "/PolyLine"):
        return _pairs(_floats_list(annot["/Vertices"]))
    raise PdfEditError("Diese Anmerkung lässt sich nicht verformen")


def transform(writer, page, annot, old_view: A.Rect, new_view: A.Rect) -> None:
    """Rahmen `old_view` → `new_view` (Ansichts-Punkte, /Rect der Anmerkung): die Geometrie so abbilden, dass der
    Rand (halbe Strichstärke, Pfeilspitzen, Wolkenbögen) gleich bleibt und nur die Form wächst; dann neu zeichnen."""
    geom = A.geometry_of(page)
    view = [geom.to_view(*p) for p in _geometry_points(annot)]
    gx0, gy0 = min(p[0] for p in view), min(p[1] for p in view)
    gx1, gy1 = max(p[0] for p in view), max(p[1] for p in view)
    ox0, oy0, ox1, oy1 = old_view
    nx0, ny0, nx1, ny1 = new_view
    left, top, right, bottom = gx0 - ox0, gy0 - oy0, ox1 - gx1, oy1 - gy1      # Rand je Seite bleibt
    tx0, ty0 = nx0 + left, ny0 + top
    tx1, ty1 = max(tx0, nx1 - right), max(ty0, ny1 - bottom)
    sx = (tx1 - tx0) / (gx1 - gx0) if gx1 - gx0 > 1e-6 else 1.0
    sy = (ty1 - ty0) / (gy1 - gy0) if gy1 - gy0 > 1e-6 else 1.0

    def move(p: Point) -> Point:
        vx, vy = geom.to_view(*p)
        return geom.to_pdf(tx0 + (vx - gx0) * sx, ty0 + (vy - gy0) * sy)

    subtype = str(annot.get("/Subtype"))
    if subtype == "/Ink":
        annot[NameObject("/InkList")] = ArrayObject([
            A._floats([c for p in _pairs(_floats_list(s)) for c in move(p)]) for s in annot.get("/InkList", [])])
    elif subtype in ("/Square", "/Circle"):
        x0, y0, x1, y1 = _inner_rect(annot)
        a, b = move((x0, y0)), move((x1, y1))
        annot[NameObject("/Rect")] = A._floats([min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])])
        if "/RD" in annot:
            del annot["/RD"]
    elif subtype == "/Line":
        x0, y0, x1, y1 = _floats_list(annot["/L"])[:4]
        annot[NameObject("/L")] = A._floats([*move((x0, y0)), *move((x1, y1))])
    else:
        annot[NameObject("/Vertices")] = A._floats([c for p in _pairs(_floats_list(annot["/Vertices"]))
                                                    for c in move(p)])
    redraw(writer, annot)


def restyle(data: bytes, page_index: int, position: int, style: Style) -> bytes:
    """Farbe, Stärke, Füllung, Deckkraft und Linienstil einer Zeichnung/Form ändern."""
    writer = A.open_writer(data)
    annots = A._annots(A._page(writer, page_index))
    if not 0 <= position < len(annots):
        raise PdfEditError("Diese Anmerkung gibt es nicht mehr")
    annot = annots[position].get_object()
    if A.KIND_NAMES.get(str(annot.get("/Subtype"))) not in DRAW_KINDS:
        raise PdfEditError("Nur Zeichnungen und Formen haben einen Linienstil")
    if annot.get("/Subtype") in ("/Line", "/Ink", "/PolyLine"):
        style = replace(style, fill=None)
    redraw(writer, annot, style)
    annot[NameObject("/M")] = TextStringObject(A._now())
    return A.finish(writer)
