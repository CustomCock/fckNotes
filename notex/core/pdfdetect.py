"""Formularfelder automatisch erkennen (Qt-frei) – wie „Formular vorbereiten“ in Acrobat, nur offline und einfach.

Eingaben: Wörter mit Rahmen (liefert die Oberfläche über PDFium) und gezeichnete Linien/Kästen (liest
`page_graphics` hier selbst aus dem Seiteninhalt). Alles in Ansichts-Punkten (oben links, wie angezeigt).

Erkannt wird:
- **Beschriftung + Linie**: „Name: ________“ (Unterstriche oder gezeichnete Linie rechts daneben),
- **Beschriftung + Kasten**: „Klasse: [      ]“ (gezeichnetes Rechteck rechts daneben),
- **Beschriftung + Leerraum**: „Thema:            “ bis zum nächsten Wort oder Seitenrand (nur bei bekannten
  Begriffen oder Doppelpunkt, damit Fließtext nicht zum Formular wird),
- **Linie über der Beschriftung**: „_____________ / Unterschrift“ – typisch für Unterschrift und „Ort, Datum“,
- **Kästchen**: gezeichnete kleine Quadrate oder ☐ □ → Kontrollkästchen, benannt nach dem Wort dahinter.
Vorhandene Felder werden nicht doppelt angelegt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from notex.core import pdfannot as A

Rect = tuple[float, float, float, float]

# Begriff (klein, ohne Doppelpunkt) → (Feldname, Art). Mehrwortbegriffe zuerst prüfen.
KEYWORDS: dict[str, tuple[str, str]] = {
    "ort, datum": ("Ort, Datum", "text"), "ort/datum": ("Ort, Datum", "text"), "datum, unterschrift":
    ("Unterschrift", "signature"), "unterschrift": ("Unterschrift", "signature"), "signature": ("Unterschrift", "signature"),
    "unterschrift des schülers": ("Unterschrift", "signature"), "unterschrift der schülerin": ("Unterschrift", "signature"),
    "unterschrift erziehungsberechtigte": ("Unterschrift Erziehungsberechtigte", "signature"),
    "unterschrift der erziehungsberechtigten": ("Unterschrift Erziehungsberechtigte", "signature"),
    "name": ("Name", "text"), "vorname": ("Vorname", "text"), "nachname": ("Nachname", "text"),
    "familienname": ("Nachname", "text"), "name, vorname": ("Name, Vorname", "text"),
    "vor- und nachname": ("Name", "text"), "datum": ("Datum", "text"), "date": ("Datum", "text"),
    "klasse": ("Klasse", "text"), "kurs": ("Kurs", "text"), "thema": ("Thema", "text"), "fach": ("Fach", "text"),
    "lehrer": ("Lehrkraft", "text"), "lehrerin": ("Lehrkraft", "text"), "lehrkraft": ("Lehrkraft", "text"),
    "schule": ("Schule", "text"), "gruppe": ("Gruppe", "text"), "note": ("Note", "text"), "punkte": ("Punkte", "text"),
    "ort": ("Ort", "text"), "straße": ("Straße", "text"), "strasse": ("Straße", "text"), "plz": ("PLZ", "text"),
    "plz, ort": ("PLZ, Ort", "text"), "anschrift": ("Anschrift", "text"), "adresse": ("Anschrift", "text"),
    "telefon": ("Telefon", "text"), "tel.": ("Telefon", "text"), "e-mail": ("E-Mail", "text"),
    "email": ("E-Mail", "text"), "geburtsdatum": ("Geburtsdatum", "text"), "betrieb": ("Betrieb", "text"),
    "ausbildungsberuf": ("Ausbildungsberuf", "text"), "ausbilder": ("Ausbilder", "text"), "woche": ("Woche", "text"),
    "kw": ("KW", "text"), "zeitraum": ("Zeitraum", "text"), "abteilung": ("Abteilung", "text"),
    "matrikelnummer": ("Matrikelnummer", "text"), "aktenzeichen": ("Aktenzeichen", "text"),
}
CHECK_GLYPHS = {"☐", "□", "❏", "❑", "▢", "◻"}
MIN_GAP = 60.0          # so viel freier Platz rechts neben einer Beschriftung ergibt ein Feld
MAX_WORDS_LABEL = 4


@dataclass(frozen=True)
class Word:
    text: str
    rect: Rect


@dataclass(frozen=True)
class Graphics:
    hlines: tuple[tuple[float, float, float], ...] = ()    # (x0, x1, y)
    boxes: tuple[Rect, ...] = ()                           # Rechtecke (Eingabekästen)
    squares: tuple[Rect, ...] = ()                         # kleine Quadrate (Kontrollkästchen)


@dataclass
class Suggestion:
    name: str
    kind: str                 # text | signature | checkbox
    rect: Rect
    multiline: bool = False
    label: str = ""           # gefundene Beschriftung (für die Meldung)
    page: int = 0
    reasons: list[str] = field(default_factory=list)
    base: str = ""            # Name vor dem Durchnummerieren („Name“ → „Name 2“)


# ---- Grafik aus dem Seiteninhalt -----------------------------------------------------------------------------
def _mul(m, n):
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + b * c2, a * b2 + b * d2, c * a2 + d * c2, c * b2 + d * d2, e * a2 + f * c2 + e2,
            e * b2 + f * d2 + f2)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def page_graphics(data: bytes, index: int) -> Graphics:
    """Linien, Kästen und kleine Quadrate einer Seite (auch in Form-XObjects, eine Ebene tief)."""
    from pypdf.generic import ContentStream
    from notex.core.pdfpages import open_reader
    reader = open_reader(data)
    page = reader.pages[index]
    geom = A.geometry_of(page)
    hlines: list = []
    boxes: list = []
    squares: list = []

    def to_view_rect(x0, y0, x1, y1):
        return geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def run(contents, resources, base, depth=0):
        try:
            ops = ContentStream(contents, reader).operations
        except Exception:                                   # noqa: BLE001 – unlesbarer Inhalt: nichts erkennen
            return
        ctm, stack = base, []
        path: list = []
        current = None
        for operands, op in ops:
            op = op.decode("latin-1") if isinstance(op, bytes) else op
            try:
                if op == "q":
                    stack.append(ctm)
                elif op == "Q":
                    ctm = stack.pop() if stack else base
                elif op == "cm":
                    ctm = _mul(tuple(float(v) for v in operands), ctm)
                elif op == "m":
                    current = _apply(ctm, float(operands[0]), float(operands[1]))
                elif op == "l" and current is not None:
                    point = _apply(ctm, float(operands[0]), float(operands[1]))
                    path.append(("line", current, point))
                    current = point
                elif op == "re":
                    x, y, w, h = (float(v) for v in operands)
                    corners = [_apply(ctm, px, py) for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
                    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
                    path.append(("rect", (min(xs), min(ys), max(xs), max(ys))))
                elif op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
                    for item in path:
                        if item[0] == "line":
                            (x0, y0), (x1, y1) = item[1], item[2]
                            if abs(y1 - y0) < 0.8 and abs(x1 - x0) >= 20:
                                vx0, vy0, vx1, _vy1 = to_view_rect(x0, y0, x1, y1)
                                hlines.append((vx0, vx1, vy0))
                        else:
                            x0, y0, x1, y1 = item[1]
                            w, h = x1 - x0, y1 - y0
                            view = to_view_rect(x0, y0, x1, y1)
                            vw, vh = view[2] - view[0], view[3] - view[1]
                            if vh <= 2.5 and vw >= 20:
                                hlines.append((view[0], view[2], (view[1] + view[3]) / 2))
                            elif 5 <= vw <= 18 and 5 <= vh <= 18 and abs(vw - vh) <= 2.5:
                                squares.append(view)
                            elif vw >= 30 and 10 <= vh <= 160 and op in ("S", "s", "B", "B*", "b", "b*"):
                                boxes.append(view)
                    path = []
                    current = None
                elif op == "n":
                    path = []
                    current = None
                elif op == "Do" and depth == 0 and resources is not None:
                    name = operands[0]
                    xobjects = resources.get("/XObject")
                    xobject = xobjects.get_object().get(name) if xobjects is not None else None
                    xobject = xobject.get_object() if xobject is not None else None
                    if xobject is not None and xobject.get("/Subtype") == "/Form":
                        matrix = tuple(float(v) for v in xobject.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                        run(xobject, xobject.get("/Resources"), _mul(matrix, ctm), depth + 1)
            except (ValueError, TypeError, IndexError, KeyError):
                continue

    contents = page.get_contents()
    if contents is not None:
        resources = page.get("/Resources")
        run(contents, resources.get_object() if resources is not None else None, (1, 0, 0, 1, 0, 0))
    return Graphics(tuple(hlines), tuple(boxes), tuple(squares))


# ---- Erkennung ---------------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(":").rstrip(".").strip()


def _lines(words: list[Word]) -> list[list[Word]]:
    """Wörter zu Textzeilen (gleiche Höhe), je Zeile von links nach rechts."""
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: ((w.rect[1] + w.rect[3]) / 2, w.rect[0])):
        cy = (word.rect[1] + word.rect[3]) / 2
        for row in rows:
            ref = row[0].rect
            if abs(cy - (ref[1] + ref[3]) / 2) <= max(3.0, (ref[3] - ref[1]) * 0.45):
                row.append(word)
                break
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w.rect[0])
    return rows


def _is_underscores(text: str) -> bool:
    return len(text) >= 3 and set(text) <= {"_", "."} and text.count("_") >= 3 or set(text) == {"…"}


def _match_label(row: list[Word], start: int) -> tuple[int, str, str, str] | None:
    """Beschriftung ab Wort `start`: (Anzahl Wörter, Feldname, Art, Text) – längste passende zuerst."""
    for count in range(min(MAX_WORDS_LABEL, len(row) - start), 0, -1):
        words = row[start:start + count]
        if any(_is_underscores(w.text) for w in words):
            continue
        text = " ".join(w.text for w in words)
        key = _norm(text)
        if key in KEYWORDS:
            name, kind = KEYWORDS[key]
            return count, name, kind, text
        if count == 1 or not text.endswith(":"):
            continue
    word = row[start]
    if word.text.endswith(":") and len(word.text) > 2 and not _is_underscores(word.text):
        name = word.text.rstrip(":").strip()
        if name and name[0].isalpha() and len(name) <= 30:
            return 1, name[0].upper() + name[1:], "text", word.text
    return None


def _overlaps(a: Rect, b: Rect, threshold: float = 0.25) -> bool:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1.0
    return inter / smaller > threshold


def detect(words: list[Word], graphics: Graphics, page_size: tuple[float, float],
           existing: list[Rect] | None = None, page: int = 0) -> list[Suggestion]:
    width, height = page_size
    margin = 36.0
    existing = list(existing or [])
    out: list[Suggestion] = []
    hlines = list(graphics.hlines)
    for word in words:                                   # Unterstrich-Wörter sind auch Linien
        if _is_underscores(word.text):
            hlines.append((word.rect[0], word.rect[2], word.rect[3] - 1))
    used_lines: set[int] = set()

    def add(s: Suggestion) -> None:
        x0, y0, x1, y1 = s.rect
        min_w, min_h = (4, 4) if s.kind == "checkbox" else (12, 6)
        if x1 - x0 < min_w or y1 - y0 < min_h:
            return
        if any(_overlaps(s.rect, r) for r in existing) or any(_overlaps(s.rect, o.rect) for o in out):
            return
        s.page = page
        out.append(s)

    rows = _lines([w for w in words if w.text.strip()])
    for row in rows:
        i = 0
        while i < len(row):
            match = _match_label(row, i)
            if match is None:
                i += 1
                continue
            count, name, kind, label = match
            words_label = row[i:i + count]
            lx0 = words_label[0].rect[0]
            lx1 = words_label[-1].rect[2]
            ly0 = min(w.rect[1] for w in words_label)
            ly1 = max(w.rect[3] for w in words_label)
            lh = max(6.0, ly1 - ly0)
            nxt = row[i + count] if i + count < len(row) else None
            first_on_line = i == 0 or row[i - 1].rect[2] < lx0 - 40
            known = _norm(label) in KEYWORDS
            field_height = max(lh * 1.35, 14.0) if kind != "signature" else 30.0
            # 1. Linie rechts daneben (gezeichnet oder Unterstriche)
            right = None
            for n, (x0, x1, y) in enumerate(hlines):
                if n in used_lines:
                    continue
                if ly0 - 2 <= y <= ly1 + lh * 0.8 and lx1 - 4 <= x0 <= lx1 + 80 and x1 - x0 >= 25:
                    if right is None or x0 < hlines[right][0]:
                        right = n
            if right is not None:
                x0, x1, y = hlines[right]
                used_lines.add(right)
                add(Suggestion(name, kind, (max(x0, lx1 + 2), y - field_height, x1, y - 0.8), label=label,
                               reasons=["Linie rechts"]))
                i += count
                continue
            # 2. Kasten rechts daneben
            box = next((b for b in graphics.boxes if b[0] >= lx1 - 4 and b[0] <= lx1 + 80
                        and b[1] <= ly1 and b[3] >= ly0), None)
            if box is not None:
                x0, y0, x1, y1 = box
                add(Suggestion(name, kind, (x0 + 1.5, y0 + 1.5, x1 - 1.5, y1 - 1.5), y1 - y0 > 40, label,
                               reasons=["Kasten rechts"]))
                i += count
                continue
            # 3. Linie darüber (Unterschrift, Ort/Datum unter der Linie)
            above = None
            for n, (x0, x1, y) in enumerate(hlines):
                if n in used_lines:
                    continue
                if ly0 - lh * 2.2 <= y <= ly0 + 1 and x0 - 10 <= lx0 <= x1 and x1 - x0 >= 40:
                    if above is None or y > hlines[above][2]:
                        above = n
            if above is not None and (known or kind == "signature"):
                x0, x1, y = hlines[above]
                used_lines.add(above)
                add(Suggestion(name, kind, (x0, y - field_height, x1, y - 0.8), label=label,
                               reasons=["Linie darüber"]))
                i += count
                continue
            # 4. freier Platz rechts daneben
            limit = nxt.rect[0] - 6 if nxt is not None else width - margin
            if (known or label.endswith(":")) and first_on_line and limit - (lx1 + 4) >= MIN_GAP:
                cy = (ly0 + ly1) / 2
                add(Suggestion(name, kind, (lx1 + 4, cy - field_height / 2 - 1, limit, cy + field_height / 2 - 1),
                               label=label, reasons=["Leerraum rechts"]))
            i += count
    # Kontrollkästchen: gezeichnete Quadrate und Kästchen-Zeichen, benannt nach dem Text rechts daneben
    squares = list(graphics.squares) + [w.rect for w in words if w.text.strip() in CHECK_GLYPHS]
    numbered = 0
    for square in squares:
        x0, y0, x1, y1 = square
        side = max(x1 - x0, y1 - y0)
        cy = (y0 + y1) / 2
        neighbour = [w for w in words if w.rect[0] >= x1 - 1 and w.rect[0] <= x1 + 20
                     and abs((w.rect[1] + w.rect[3]) / 2 - cy) <= side and w.text.strip() not in CHECK_GLYPHS]
        if neighbour:
            row = sorted((w for w in words if abs((w.rect[1] + w.rect[3]) / 2 - cy) <= side
                          and neighbour[0].rect[0] <= w.rect[0] <= neighbour[0].rect[0] + 260),
                         key=lambda w: w.rect[0])
            label_words = [row[0]]
            for w in row[1:]:                             # bis zur nächsten großen Lücke (nächstes Kästchen o. Ä.)
                if w.rect[0] - label_words[-1].rect[2] > 18 or w.text.strip() in CHECK_GLYPHS:
                    break
                label_words.append(w)
            name = " ".join(w.text for w in label_words).strip(" :.,")[:40]
        else:
            numbered += 1
            name = f"Kästchen {numbered}"
        add(Suggestion(name or "Kästchen", "checkbox", (x0, y0, x0 + side, y0 + side), label=name,
                       reasons=["Kästchen"]))
    _unique_names(out, set())
    return out


def _unique_names(suggestions: list[Suggestion], taken: set[str]) -> None:
    for s in suggestions:
        s.base = s.base or s.name
        base, number, name = s.base, 1, s.base
        while name in taken:
            number += 1
            name = f"{base} {number}"
        s.name = name
        taken.add(name)


def unique_against(suggestions: list[Suggestion], existing_names: set[str]) -> list[Suggestion]:
    _unique_names(suggestions, set(existing_names))
    return suggestions


TOKEN_RE = re.compile(r"_{3,}|\.{6,}|…+|[☐□❏❑▢◻]|[^\s_☐□❏❑▢◻]+")
