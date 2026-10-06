"""Diagramm-Modell (Qt-frei): Formen, Verbinder, Andockpunkte, Routing – Einheit Punkt, y nach unten.

Formen haben Andockpunkte (Ports) als Anteile ihres Rahmens; ein Verbinder hängt mit jedem Ende entweder an einem
Port einer Form, „schwebend“ an einer Form (dann am Rand Richtung Gegenstück) oder frei an einem Punkt. Verschiebt
man eine Form, wandern die angehängten Verbinder mit – gerechnet wird immer aus dem Modell.
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field

FORMAT = "fcknotes-diagram"
VERSION = 1

SHAPE_KINDS = ("rect", "rounded", "ellipse", "diamond", "circle", "endstate", "note", "text", "actor", "class",
               "package", "database", "parallelogram")
ARROWS = ("none", "arrow", "open", "triangle", "diamond", "diamond_filled", "circle")
ROUTES = ("straight", "orthogonal")
DEFAULT_SIZES = {"rect": (120, 60), "rounded": (120, 60), "ellipse": (130, 60), "diamond": (100, 70),
                 "circle": (20, 20), "endstate": (22, 22), "note": (120, 70), "text": (100, 24), "actor": (36, 70),
                 "class": (150, 90), "package": (140, 90), "database": (80, 70), "parallelogram": (120, 60)}
LABELS = {"rect": "Rechteck", "rounded": "Abgerundet", "ellipse": "Ellipse / Use Case", "diamond": "Raute",
          "circle": "Startknoten", "endstate": "Endknoten", "note": "Notiz", "text": "Text", "actor": "Akteur",
          "class": "UML-Klasse", "package": "Paket", "database": "Datenbank", "parallelogram": "Ein-/Ausgabe"}

# Ports als (Name, Anteil x, Anteil y); Randformen bekommen je Seite drei Punkte, runde Formen acht.
_SIDE = [(f"n{i}", f, 0.0) for i, f in ((1, 0.25), (2, 0.5), (3, 0.75))] + \
        [(f"e{i}", 1.0, f) for i, f in ((1, 0.25), (2, 0.5), (3, 0.75))] + \
        [(f"s{i}", f, 1.0) for i, f in ((1, 0.25), (2, 0.5), (3, 0.75))] + \
        [(f"w{i}", 0.0, f) for i, f in ((1, 0.25), (2, 0.5), (3, 0.75))]
_ROUND = [("n2", 0.5, 0.0), ("e2", 1.0, 0.5), ("s2", 0.5, 1.0), ("w2", 0.0, 0.5)] + \
         [(name, 0.5 + 0.5 * math.cos(a), 0.5 + 0.5 * math.sin(a))
          for name, a in (("se", math.pi / 4), ("sw", 3 * math.pi / 4), ("nw", 5 * math.pi / 4), ("ne", 7 * math.pi / 4))]
_FOUR = [("n2", 0.5, 0.0), ("e2", 1.0, 0.5), ("s2", 0.5, 1.0), ("w2", 0.0, 0.5)]
PORTS = {"ellipse": _ROUND, "circle": _ROUND, "endstate": _ROUND, "diamond": _FOUR, "actor": _FOUR}


def new_id(prefix: str = "s") -> str:
    return prefix + uuid.uuid4().hex[:8]


@dataclass
class Shape:
    id: str
    kind: str
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    fill: str = "#ffffff"
    stroke: str = "#1a1a1a"
    text_color: str = "#1a1a1a"
    font_size: float = 11.0
    bold: bool = False
    dashed: bool = False

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.w, self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def contains(self, px: float, py: float, tolerance: float = 0.0) -> bool:
        return self.x - tolerance <= px <= self.x + self.w + tolerance and \
            self.y - tolerance <= py <= self.y + self.h + tolerance

    def ports(self) -> list[tuple[str, float, float]]:
        """(Name, x, y) aller Andockpunkte."""
        return [(name, self.x + fx * self.w, self.y + fy * self.h) for name, fx, fy in PORTS.get(self.kind, _SIDE)]

    def port(self, name: str) -> tuple[float, float] | None:
        for port, px, py in self.ports():
            if port == name:
                return px, py
        return None

    def class_sections(self) -> list[str]:
        """UML-Klasse: Text in Abschnitte (Name / Attribute / Methoden), getrennt durch eine Zeile „--“."""
        sections, current = [], []
        for line in self.text.split("\n"):
            if line.strip() == "--":
                sections.append("\n".join(current))
                current = []
            else:
                current.append(line)
        sections.append("\n".join(current))
        return sections


@dataclass
class End:
    shape: str | None = None      # angehängte Form
    port: str | None = None       # Andockpunkt; None = schwebend (Rand Richtung Gegenstück)
    x: float = 0.0                # freier Punkt, wenn an keiner Form
    y: float = 0.0


@dataclass
class Connector:
    id: str
    source: End
    target: End
    route: str = "orthogonal"
    start_arrow: str = "none"
    end_arrow: str = "arrow"
    dashed: bool = False
    label: str = ""
    start_label: str = ""         # z. B. Multiplizität „1“
    end_label: str = ""           # z. B. „*“
    stroke: str = "#1a1a1a"
    width: float = 1.2


# UML-Beziehungen als Vorlagen für Verbinder
RELATIONS = {
    "Linie": dict(start_arrow="none", end_arrow="none", dashed=False),
    "Pfeil": dict(start_arrow="none", end_arrow="arrow", dashed=False),
    "Assoziation": dict(start_arrow="none", end_arrow="none", dashed=False),
    "Gerichtete Assoziation": dict(start_arrow="none", end_arrow="open", dashed=False),
    "Vererbung": dict(start_arrow="none", end_arrow="triangle", dashed=False),
    "Realisierung": dict(start_arrow="none", end_arrow="triangle", dashed=True),
    "Abhängigkeit": dict(start_arrow="none", end_arrow="open", dashed=True),
    "Aggregation": dict(start_arrow="diamond", end_arrow="none", dashed=False),
    "Komposition": dict(start_arrow="diamond_filled", end_arrow="none", dashed=False),
    "Nachricht (asynchron)": dict(start_arrow="none", end_arrow="open", dashed=False),
    "Antwort": dict(start_arrow="none", end_arrow="open", dashed=True),
}


@dataclass
class Diagram:
    width: float = 300.0
    height: float = 200.0
    shapes: list[Shape] = field(default_factory=list)
    connectors: list[Connector] = field(default_factory=list)

    # ---- Zugriff ---------------------------------------------------------------------------------------
    def shape(self, shape_id: str | None) -> Shape | None:
        return next((s for s in self.shapes if s.id == shape_id), None)

    def connector(self, connector_id: str) -> Connector | None:
        return next((c for c in self.connectors if c.id == connector_id), None)

    def add_shape(self, kind: str, x: float, y: float, w: float | None = None, h: float | None = None,
                  text: str | None = None) -> Shape:
        if kind not in SHAPE_KINDS:
            raise ValueError(f"Unbekannte Form „{kind}“")
        dw, dh = DEFAULT_SIZES[kind]
        default_text = {"class": "Klasse\n--\n- attribut: Typ\n--\n+ methode(): void", "actor": "Akteur",
                        "text": "Text", "note": "Notiz", "package": "Paket", "database": "Datenbank"}.get(kind, "")
        shape = Shape(new_id("s"), kind, x, y, w or dw, h or dh, default_text if text is None else text)
        if kind in ("circle", "endstate"):
            shape.fill = "#1a1a1a"
        if kind == "note":
            shape.fill = "#fff6c2"
        if kind == "text":
            shape.stroke = "none"
            shape.fill = "none"
        self.shapes.append(shape)
        return shape

    def connect(self, source: End, target: End, relation: str = "Pfeil", **style) -> Connector:
        conn = Connector(new_id("c"), source, target, **{**RELATIONS.get(relation, {}), **style})
        self.connectors.append(conn)
        return conn

    def remove(self, ids: set[str]) -> None:
        """Formen/Verbinder löschen; Verbinder an gelöschten Formen fallen mit weg."""
        self.shapes = [s for s in self.shapes if s.id not in ids]
        alive = {s.id for s in self.shapes}
        self.connectors = [c for c in self.connectors if c.id not in ids and
                           (c.source.shape is None or c.source.shape in alive) and
                           (c.target.shape is None or c.target.shape in alive)]

    def bounds(self) -> tuple[float, float, float, float] | None:
        """Rahmen um alle Inhalte (Formen und Verbinder), None bei leerem Diagramm."""
        xs, ys = [], []
        for s in self.shapes:
            xs += [s.x, s.x + s.w]
            ys += [s.y, s.y + s.h + (14 if s.kind == "actor" else 0)]
        for c in self.connectors:
            for px, py in route(self, c):
                xs.append(px)
                ys.append(py)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def fit(self, margin: float = 6.0) -> None:
        """Inhalt nach oben links schieben und Größe an den Inhalt anpassen (nie kleiner als 40×30)."""
        box = self.bounds()
        if box is None:
            return
        dx, dy = margin - box[0], margin - box[1]
        self.translate(dx, dy)
        self.width = max(40.0, box[2] - box[0] + 2 * margin)
        self.height = max(30.0, box[3] - box[1] + 2 * margin)

    def translate(self, dx: float, dy: float) -> None:
        for s in self.shapes:
            s.x += dx
            s.y += dy
        for c in self.connectors:
            for end in (c.source, c.target):
                if end.shape is None:
                    end.x += dx
                    end.y += dy

    # ---- Speichern -----------------------------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps({"format": FORMAT, "version": VERSION, "width": self.width, "height": self.height,
                           "shapes": [asdict(s) for s in self.shapes],
                           "connectors": [asdict(c) for c in self.connectors]}, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "Diagram":
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("Kein fckNotes-Diagramm")
        shape_fields = set(Shape.__dataclass_fields__)
        conn_fields = set(Connector.__dataclass_fields__) - {"source", "target"}
        end_fields = set(End.__dataclass_fields__)
        shapes = [Shape(**{k: v for k, v in s.items() if k in shape_fields}) for s in data.get("shapes", [])]
        connectors = []
        for c in data.get("connectors", []):
            source = End(**{k: v for k, v in (c.get("source") or {}).items() if k in end_fields})
            target = End(**{k: v for k, v in (c.get("target") or {}).items() if k in end_fields})
            connectors.append(Connector(source=source, target=target,
                                        **{k: v for k, v in c.items() if k in conn_fields}))
        return cls(float(data.get("width", 300)), float(data.get("height", 200)), shapes, connectors)


# ---- Geometrie -------------------------------------------------------------------------------------------------
def boundary_point(shape: Shape, toward: tuple[float, float]) -> tuple[float, float]:
    """Punkt auf dem Rand der Form in Richtung `toward` (vom Mittelpunkt aus)."""
    cx, cy = shape.center
    dx, dy = toward[0] - cx, toward[1] - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return cx, cy - shape.h / 2
    hw, hh = shape.w / 2, shape.h / 2
    if shape.kind in ("ellipse", "circle", "endstate"):
        t = 1.0 / math.sqrt((dx / hw) ** 2 + (dy / hh) ** 2)
    elif shape.kind == "diamond":
        t = 1.0 / (abs(dx) / hw + abs(dy) / hh)
    else:
        t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def _direction(shape: Shape | None, point: tuple[float, float]) -> tuple[float, float]:
    """Richtung, in die ein Verbinder an diesem Randpunkt die Form verlässt (Einheitsvektor, achsparallel)."""
    if shape is None:
        return 0.0, 0.0
    x0, y0, x1, y1 = shape.rect
    px, py = point
    distances = {(0.0, -1.0): abs(py - y0), (0.0, 1.0): abs(py - y1), (-1.0, 0.0): abs(px - x0),
                 (1.0, 0.0): abs(px - x1)}
    return min(distances, key=distances.get)


def end_point(diagram: Diagram, end: End, other: tuple[float, float]) -> tuple[float, float]:
    shape = diagram.shape(end.shape)
    if shape is None:
        return end.x, end.y
    if end.port:
        port = shape.port(end.port)
        if port is not None:
            return port
    return boundary_point(shape, other)


def _anchor_guess(diagram: Diagram, end: End) -> tuple[float, float]:
    shape = diagram.shape(end.shape)
    if shape is None:
        return end.x, end.y
    if end.port and shape.port(end.port) is not None:
        return shape.port(end.port)
    return shape.center


def anchor(diagram: Diagram, end: End) -> tuple[float, float]:
    """Bezugspunkt eines Endes: Andockpunkt, sonst Formmitte, sonst der freie Punkt."""
    return _anchor_guess(diagram, end)


def align_point(point: tuple[float, float], ref: tuple[float, float], tolerance: float,
                constrain: bool = False) -> tuple[float, float]:
    """Freien Punkt auf eine Linie mit `ref` ziehen: fast senkrecht/waagerecht → exakt (wie Hilfslinien in draw.io).
    `constrain` (Shift) erzwingt die überwiegende Achse."""
    x, y = point
    dx, dy = abs(x - ref[0]), abs(y - ref[1])
    if constrain:
        return (ref[0], y) if dy >= dx else (x, ref[1])
    if dx <= tolerance:
        x = ref[0]
    if dy <= tolerance:
        y = ref[1]
    return x, y


def align_free_ends(diagram: Diagram, conn: Connector, tolerance: float, constrain: bool = False) -> None:
    """Freie Enden eines Verbinders am anderen Ende ausrichten, damit „gerade gezogen“ auch exakt gerade ist."""
    source, target = conn.source, conn.target
    if target.shape is None:
        target.x, target.y = align_point((target.x, target.y), anchor(diagram, source), tolerance, constrain)
    elif source.shape is None:
        source.x, source.y = align_point((source.x, source.y), anchor(diagram, target), tolerance, constrain)


def orthogonal(p0, d0, p1, d1, stub: float = 14.0) -> list[tuple[float, float]]:
    """Rechtwinkliger Weg von p0 (Austritt d0) nach p1 (Eintritt aus Richtung d1) – höchstens wenige Knicke."""
    s0 = (p0[0] + d0[0] * stub, p0[1] + d0[1] * stub) if d0 != (0.0, 0.0) else p0
    s1 = (p1[0] + d1[0] * stub, p1[1] + d1[1] * stub) if d1 != (0.0, 0.0) else p1
    horizontal0 = d0[0] != 0 if d0 != (0.0, 0.0) else abs(s1[0] - s0[0]) >= abs(s1[1] - s0[1])
    if horizontal0:
        mid_x = (s0[0] + s1[0]) / 2
        middle = [(mid_x, s0[1]), (mid_x, s1[1])]
        if d1 != (0.0, 0.0) and d1[1] != 0:                 # Ziel kommt von oben/unten: ein Knick reicht
            middle = [(s1[0], s0[1])]
    else:
        mid_y = (s0[1] + s1[1]) / 2
        middle = [(s0[0], mid_y), (s1[0], mid_y)]
        if d1 != (0.0, 0.0) and d1[0] != 0:
            middle = [(s0[0], s1[1])]
    points = [p0, s0, *middle, s1, p1]
    return _simplify(points)


def _simplify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        p = (round(p[0], 3), round(p[1], 3))
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 0.01 and abs(b[0] - p[0]) < 0.01) or (abs(a[1] - b[1]) < 0.01 and abs(b[1] - p[1]) < 0.01):
                out[-1] = p                                  # gerade weiter: Zwischenpunkt weg
                continue
        out.append(p)
    return out


def route(diagram: Diagram, conn: Connector) -> list[tuple[float, float]]:
    """Punkte des Verbinders vom Start- zum Endpunkt."""
    start = end_point(diagram, conn.source, _anchor_guess(diagram, conn.target))
    finish = end_point(diagram, conn.target, start)
    start = end_point(diagram, conn.source, finish)
    if conn.route != "orthogonal":
        return [start, finish]
    d0 = _direction(diagram.shape(conn.source.shape), start)
    d1 = _direction(diagram.shape(conn.target.shape), finish)
    return orthogonal(start, d0, finish, d1)


def midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Punkt auf halber Länge des Weges (für die Beschriftung)."""
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return points[0]
    half = total / 2
    for (a, b), length in zip(zip(points, points[1:]), lengths):
        if half <= length:
            t = half / length if length else 0
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        half -= length
    return points[-1]


def nearest_port(shape: Shape, x: float, y: float) -> tuple[str, float]:
    """Nächster Andockpunkt und Abstand."""
    name, px, py = min(shape.ports(), key=lambda p: math.dist((p[1], p[2]), (x, y)))
    return name, math.dist((px, py), (x, y))


def distance_to_route(points: list[tuple[float, float]], x: float, y: float) -> float:
    best = math.inf
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
        best = min(best, math.dist((x, y), (ax + dx * t, ay + dy * t)))
    return best
