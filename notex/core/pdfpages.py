"""PDF-Seiten organisieren (Qt-frei, pypdf): umsortieren, drehen, löschen, herauslösen, einfügen, zusammenfügen,
aufteilen.

Alle Funktionen nehmen und liefern PDF-Bytes – die Oberfläche legt jede Änderung als Schritt auf einen
Rückgängig-Stapel. Neu aufgebaut wird immer über einen frischen PdfWriter mit `append`: so landen nur die behaltenen
Seiten samt dem, was sie brauchen (Schriften, Bilder, Anmerkungen, Lesezeichen auf diese Seiten), in der Datei.
Eine gelöschte Seite steckt danach nicht mehr unsichtbar im PDF – wichtig, wenn man es weitergibt.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, RectangleObject, TextStringObject


class PdfEditError(Exception):
    """Verständliche Meldung für die Oberfläche (kaputtes PDF, Passwortschutz, unsinnige Eingabe)."""


def open_reader(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except (PdfReadError, ValueError, KeyError, TypeError) as error:
        raise PdfEditError(f"Kein gültiges PDF ({error})") from error
    if encrypted:
        raise PdfEditError("Passwortgeschützte PDFs lassen sich nur ansehen, nicht bearbeiten")
    return reader


def to_bytes(writer: PdfWriter) -> bytes:
    from notex.core.pdfannot import prune_unreachable
    prune_unreachable(writer)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def copy_metadata(reader: PdfReader, writer: PdfWriter) -> None:
    meta = reader.metadata or {}
    values = {key: str(value) for key, value in meta.items() if isinstance(key, str) and key != "/Producer"}
    if values:
        writer.add_metadata(values)


def page_count(data: bytes) -> int:
    return len(open_reader(data).pages)


def _check_pages(pages, count: int) -> list[int]:
    out = []
    for page in pages:
        if not isinstance(page, int) or not 0 <= page < count:
            raise PdfEditError(f"Seite {page + 1 if isinstance(page, int) else page} gibt es nicht (1–{count})")
        out.append(page)
    return out


def rearrange(data: bytes, order: list[int], rotate: dict[int, int] | None = None) -> bytes:
    """Neue Seitenfolge aus Quellseiten (0-basiert); fehlende Seiten fallen weg. `rotate`: Quellseite → Grad
    (Vielfache von 90, im Uhrzeigersinn, zusätzlich zur bisherigen Drehung)."""
    reader = open_reader(data)
    count = len(reader.pages)
    order = _check_pages(order, count)
    if not order:
        raise PdfEditError("Mindestens eine Seite muss übrig bleiben")
    if len(set(order)) != len(order):
        raise PdfEditError("Jede Seite darf nur einmal vorkommen")
    rotate = rotate or {}
    for degrees in rotate.values():
        if degrees % 90:
            raise PdfEditError("Drehen geht nur in 90°-Schritten")
    writer = PdfWriter()
    writer.append(reader, pages=list(order))
    for position, source in enumerate(order):
        degrees = rotate.get(source, 0) % 360
        if degrees:
            page = writer.pages[position]
            page[NameObject("/Rotate")] = NumberObject((page.rotation + degrees) % 360)   # 0/90/180/270, nie 360
    copy_metadata(reader, writer)
    _carry_labels(writer, [(0, p) for p in order], {0: reader})
    return to_bytes(writer)


def rotate(data: bytes, pages: list[int], degrees: int) -> bytes:
    count = page_count(data)
    return rearrange(data, list(range(count)), {p: degrees for p in _check_pages(pages, count)})


def delete(data: bytes, pages: list[int]) -> bytes:
    count = page_count(data)
    gone = set(_check_pages(pages, count))
    return rearrange(data, [p for p in range(count) if p not in gone])


def move(data: bytes, pages: list[int], before: int) -> bytes:
    """Seiten `pages` (in ihrer Reihenfolge) vor die Seite `before` verschieben (`before` = Seitenzahl, 0-basiert,
    im alten Dokument; = Seitenanzahl → ans Ende)."""
    count = page_count(data)
    return rearrange(data, moved_order(count, pages, before))


def moved_order(count: int, pages: list[int], before: int) -> list[int]:
    picked = sorted(set(_check_pages(pages, count)))
    rest = [p for p in range(count) if p not in picked]
    anchor = len([p for p in rest if p < before])
    return rest[:anchor] + picked + rest[anchor:]


def extract(data: bytes, pages: list[int]) -> bytes:
    """Nur diese Seiten als neues PDF (in der angegebenen Reihenfolge)."""
    return rearrange(data, list(dict.fromkeys(pages)))


def insert(data: bytes, other: bytes, at: int) -> bytes:
    """Alle Seiten von `other` vor Seite `at` einfügen (`at` = Seitenanzahl → ans Ende)."""
    reader, extra = open_reader(data), open_reader(other)
    count = len(reader.pages)
    at = max(0, min(at, count))
    writer = PdfWriter()
    if at:
        writer.append(reader, pages=list(range(at)))
    writer.append(extra)
    if at < count:
        writer.append(reader, pages=list(range(at, count)))
    copy_metadata(reader, writer)
    layout = [(0, p) for p in range(at)] + [(1, p) for p in range(len(extra.pages))] + \
        [(0, p) for p in range(at, count)]
    _carry_labels(writer, layout, {0: reader, 1: extra})
    return to_bytes(writer)


def merge(documents: list[bytes]) -> bytes:
    """Mehrere PDFs hintereinander zu einem (Metadaten vom ersten)."""
    if not documents:
        raise PdfEditError("Keine PDFs zum Zusammenfügen")
    readers = [open_reader(d) for d in documents]
    writer = PdfWriter()
    for reader in readers:
        writer.append(reader)
    copy_metadata(readers[0], writer)
    _carry_labels(writer, [(d, p) for d, reader in enumerate(readers) for p in range(len(reader.pages))],
                  dict(enumerate(readers)))
    return to_bytes(writer)


def split(data: bytes, groups: list[list[int]]) -> list[bytes]:
    """Ein PDF je Gruppe (Gruppen = Listen 0-basierter Seiten)."""
    groups = [g for g in groups if g]
    if not groups:
        raise PdfEditError("Keine Seiten zum Aufteilen angegeben")
    return [extract(data, group) for group in groups]


# ---- Leere Seite, Seiten ersetzen, Zuschneiden ---------------------------------------------------------------
PAPER = {"A4": (595.28, 841.89), "Letter": (612.0, 792.0)}
MM = 72 / 25.4                     # Punkt je Millimeter


def insert_blank(data: bytes, at: int, size: str | tuple[float, float] = "neighbor") -> bytes:
    """Leere Seite vor Seite `at` (= Seitenanzahl → ans Ende). Größe: „neighbor“ (wie die Nachbarseite, so wie sie
    angezeigt wird), „A4“, „Letter“ oder (Breite, Höhe) in Punkt."""
    reader = open_reader(data)
    count = len(reader.pages)
    at = max(0, min(at, count))
    if size == "neighbor":
        from notex.core.pdfannot import geometry_of
        width, height = geometry_of(reader.pages[max(0, at - 1)]).size
    elif isinstance(size, str):
        if size not in PAPER:
            raise PdfEditError(f"Unbekannte Seitengröße „{size}“")
        width, height = PAPER[size]
    else:
        width, height = size
    if not (36 <= width <= 14400 and 36 <= height <= 14400):
        raise PdfEditError("Seitengröße zwischen 1,3 cm und 5 m")
    writer = PdfWriter()
    if at:
        writer.append(reader, pages=list(range(at)))
    writer.add_blank_page(width, height)
    if at < count:
        writer.append(reader, pages=list(range(at, count)))
    copy_metadata(reader, writer)
    _carry_labels(writer, [(0, p) for p in range(at)] + [None] + [(0, p) for p in range(at, count)], {0: reader})
    return to_bytes(writer)


def replace_pages(data: bytes, first: int, last: int, other: bytes, other_pages: list[int] | None = None) -> bytes:
    """Seiten first…last (0-basiert, einschließlich) in einem Schritt durch Seiten aus `other` ersetzen
    (`other_pages` = welche, Standard: alle)."""
    reader, extra = open_reader(data), open_reader(other)
    count = len(reader.pages)
    first, last = sorted((first, last))
    _check_pages([first, last], count)
    picked = _check_pages(other_pages if other_pages is not None else list(range(len(extra.pages))), len(extra.pages))
    if not picked:
        raise PdfEditError("Keine Ersatzseiten gewählt")
    writer = PdfWriter()
    if first:
        writer.append(reader, pages=list(range(first)))
    writer.append(extra, pages=picked)
    if last + 1 < count:
        writer.append(reader, pages=list(range(last + 1, count)))
    copy_metadata(reader, writer)
    layout = [(0, p) for p in range(first)] + [(1, p) for p in picked] + [(0, p) for p in range(last + 1, count)]
    _carry_labels(writer, layout, {0: reader, 1: extra})
    return to_bytes(writer)


def pages_for(mode: str, count: int, current: int = 0, text: str = "") -> list[int]:
    """Seitenauswahl aus der Oberfläche: „current“, „range“ (Text wie „1-3, 5“), „even“/„odd“ (Seitenzahlen 2, 4 … bzw.
    1, 3 …), „all“."""
    if mode == "current":
        return _check_pages([current], count)
    if mode == "range":
        return parse_ranges(text, count)
    if mode == "even":
        return list(range(1, count, 2))
    if mode == "odd":
        return list(range(0, count, 2))
    if mode == "all":
        return list(range(count))
    raise PdfEditError(f"Unbekannte Auswahl „{mode}“")


def crop(data: bytes, pages: list[int], rect: tuple[float, float, float, float] | None = None,
         margins: tuple[float, float, float, float] | None = None) -> bytes:
    """Sichtbaren Bereich (CropBox) setzen – Inhalt bleibt vollständig in der Datei, das ist **kein** Schwärzen.

    `rect` (x0, y0, x1, y1) in Ansichts-Punkten der jeweiligen Seite, oder `margins` (links, oben, rechts, unten) in
    Punkt, gemessen am bisher sichtbaren Bereich so, wie die Seite angezeigt wird (gedrehte Seiten richtig)."""
    from notex.core.pdfannot import geometry_of
    if (rect is None) == (margins is None):
        raise PdfEditError("Zuschneiden braucht entweder ein Rechteck oder Ränder")
    reader = open_reader(data)
    pages = _check_pages(pages, len(reader.pages))
    if not pages:
        raise PdfEditError("Keine Seiten gewählt")
    writer = PdfWriter(clone_from=reader)
    for index in pages:
        page = writer.pages[index]
        geom = geometry_of(page)
        width, height = geom.size
        if margins is not None:
            left, top, right, bottom = (max(0.0, float(v)) for v in margins)
            view = (left, top, width - right, height - bottom)
        else:
            x0, y0, x1, y1 = rect
            view = (max(0.0, min(x0, x1)), max(0.0, min(y0, y1)), min(width, max(x0, x1)), min(height, max(y0, y1)))
        if view[2] - view[0] < 10 or view[3] - view[1] < 10:
            raise PdfEditError(f"Auf Seite {index + 1} bliebe weniger als 10 pt sichtbar")
        box = geom.rect_to_pdf(view)
        page.cropbox = RectangleObject(_clamp(box, page.mediabox))
    return to_bytes(writer)


def reset_crop(data: bytes, pages: list[int]) -> bytes:
    """Zuschnitt aufheben: wieder die ganze Seite (MediaBox) zeigen."""
    reader = open_reader(data)
    pages = _check_pages(pages, len(reader.pages))
    writer = PdfWriter(clone_from=reader)
    for index in pages:
        page = writer.pages[index]
        if "/CropBox" in page:
            del page["/CropBox"]
    return to_bytes(writer)


def _clamp(box, media) -> list[float]:
    mx0, my0, mx1, my1 = (float(v) for v in media)
    x0, y0, x1, y1 = box
    return [max(x0, mx0), max(y0, my0), min(x1, mx1), min(y1, my1)]


def detect_margins(data: bytes, index: int, threshold: int = 245, pad: float = 2.0
                   ) -> tuple[float, float, float, float] | None:
    """Weiße Ränder der Seite (links, oben, rechts, unten) in Punkt, so wie die Seite angezeigt wird – aus einem
    Graustufen-Bild (PDFium, Anmerkungen eingeschlossen). None = Seite ist leer."""
    import pypdfium2 as pdfium
    scale = 2.0
    try:
        doc = pdfium.PdfDocument(data)
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"Seite nicht lesbar ({error})") from error
    try:
        if not 0 <= index < len(doc):
            raise PdfEditError(f"Seite {index + 1} gibt es nicht")
        bitmap = doc[index].render(scale=scale, grayscale=True, draw_annots=True)
        width, height, stride = bitmap.width, bitmap.height, bitmap.stride
        raw = bytes(bitmap.buffer)
    finally:
        doc.close()
    table = bytes(255 if v >= threshold else 0 for v in range(256))       # hell → 255, Inhalt → 0
    top = bottom = None
    left, right = width, -1
    for y in range(height):
        row = raw[y * stride: y * stride + width].translate(table)
        first = len(row) - len(row.lstrip(b"\xff"))
        if first >= width:
            continue
        last = len(row.rstrip(b"\xff")) - 1
        top = y if top is None else top
        bottom = y
        left, right = min(left, first), max(right, last)
    if top is None:
        return None
    to_pt = 1 / scale
    return (max(0.0, left * to_pt - pad), max(0.0, top * to_pt - pad),
            max(0.0, (width - 1 - right) * to_pt - pad), max(0.0, (height - 1 - bottom) * to_pt - pad))


# ---- Seitenbeschriftungen (1, i, I, a, A + Präfix) ----------------------------------------------------------------
LABEL_STYLES = {"D": "1, 2, 3", "r": "i, ii, iii", "R": "I, II, III", "a": "a, b, c", "A": "A, B, C",
                "": "nur Präfix"}


@dataclass
class LabelRange:
    start: int              # erste Seite (0-basiert)
    style: str = "D"        # D, r, R, a, A oder "" (ohne Zahl)
    prefix: str = ""
    first: int = 1          # Zahl der ersten Seite


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
                       (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out


def _letters(n: int) -> str:
    """1 → a, 26 → z, 27 → aa (PDF-Regel: Buchstabe wiederholt)."""
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def format_label(style: str, prefix: str, number: int) -> str:
    if style == "D":
        core = str(number)
    elif style in ("r", "R"):
        core = _roman(number)
    elif style in ("a", "A"):
        core = _letters(number)
    else:
        core = ""
    return prefix + (core.upper() if style in ("R", "A") else core)


def labels_of(reader: PdfReader) -> list[LabelRange]:
    """Bereiche aus /PageLabels (leer = keine Beschriftungen, Anzeige 1, 2, 3 …)."""
    root = reader.trailer["/Root"]
    tree = root.get("/PageLabels")
    if tree is None:
        return []
    nums = []
    stack = [tree.get_object()]
    while stack:                                          # Zahlenbaum: /Nums oder /Kids
        node = stack.pop()
        if "/Nums" in node:
            nums.extend(node["/Nums"])
        stack.extend(kid.get_object() for kid in node.get("/Kids", []) or [])
    out = []
    for i in range(0, len(nums) - 1, 2):
        try:
            start = int(nums[i])
            spec = nums[i + 1].get_object()
            style = str(spec.get("/S", ""))[1:] if spec.get("/S") is not None else ""
            out.append(LabelRange(start, style if style in LABEL_STYLES else "D", str(spec.get("/P", "") or ""),
                                  max(1, int(spec.get("/St", 1)))))
        except (TypeError, ValueError, AttributeError):
            continue
    return sorted({r.start: r for r in out}.values(), key=lambda r: r.start)


def read_labels(data: bytes) -> list[LabelRange]:
    return labels_of(open_reader(data))


def label_list(ranges: list[LabelRange], count: int) -> list[str]:
    """Beschriftung jeder Seite (für Vorschau und Tests)."""
    if not ranges:
        return [str(i + 1) for i in range(count)]
    ordered = sorted(ranges, key=lambda r: r.start)
    out = []
    for index in range(count):
        current = None
        for r in ordered:
            if r.start <= index:
                current = r
        if current is None:
            out.append(str(index + 1))
        else:
            out.append(format_label(current.style, current.prefix, current.first + index - current.start))
    return out


def _write_labels(writer: PdfWriter, ranges: list[LabelRange]) -> None:
    root = writer._root_object
    if not ranges:
        if "/PageLabels" in root:
            del root["/PageLabels"]
        return
    ordered = sorted({r.start: r for r in ranges}.values(), key=lambda r: r.start)
    if ordered[0].start != 0:
        ordered.insert(0, LabelRange(0))
    nums = ArrayObject()
    for r in ordered:
        spec = DictionaryObject({NameObject("/Type"): NameObject("/PageLabel")})
        if r.style:
            spec[NameObject("/S")] = NameObject("/" + r.style)
        if r.prefix:
            spec[NameObject("/P")] = TextStringObject(r.prefix)
        if r.first != 1:
            spec[NameObject("/St")] = NumberObject(r.first)
        nums += [NumberObject(r.start), spec]
    root[NameObject("/PageLabels")] = DictionaryObject({NameObject("/Nums"): nums})


def set_labels(data: bytes, ranges: list[LabelRange]) -> bytes:
    """Seitenbeschriftungen komplett ersetzen (leere Liste = entfernen)."""
    reader = open_reader(data)
    count = len(reader.pages)
    for r in ranges:
        if not 0 <= r.start < count:
            raise PdfEditError(f"Bereich ab Seite {r.start + 1}: das PDF hat nur {count} Seiten")
        if r.style not in LABEL_STYLES:
            raise PdfEditError(f"Unbekannter Stil „{r.style}“")
        if r.first < 1:
            raise PdfEditError("Startwert muss mindestens 1 sein")
        if not r.style and not r.prefix:
            raise PdfEditError("Ohne Zahl braucht der Bereich ein Präfix")
    writer = PdfWriter(clone_from=reader)
    _write_labels(writer, ranges)
    return to_bytes(writer)


def _carry_labels(writer: PdfWriter, layout: list, readers: dict[int, PdfReader]) -> None:
    """Seitenbeschriftungen nach dem Neuaufbau übertragen (pypdf's `append` lässt sie fallen).

    `layout`: je neue Seite (Dokument, Quellseite) oder None (neue leere Seite). Ein Bereich bleibt an seinen Seiten:
    Wird darin gelöscht oder eingefügt, zählen die Folgeseiten weiter (wie in Acrobat); wird er durch fremde Seiten
    unterbrochen, setzt er danach mit der nächsten Zahl fort."""
    labels = {d: labels_of(r) for d, r in readers.items()}
    if not any(labels.values()):
        return
    counts = {d: len(r.pages) for d, r in readers.items()}

    def range_of(d: int, p: int) -> tuple:
        ranges = labels[d] or [LabelRange(0)]
        current = ranges[0]
        for r in ranges:
            if r.start <= p:
                current = r
        return (d, current.start), current

    consumed: dict[tuple, int] = {}
    anchors: list[LabelRange] = []
    run_key = None
    last_source: dict[tuple, int] = {}
    for position, entry in enumerate(layout):
        if entry is None:                                  # leere Seite gehört zum laufenden Bereich
            if run_key is None:
                anchors.append(LabelRange(position))
                run_key = ("blank", position)
            consumed[run_key] = consumed.get(run_key, 0) + 1
            continue
        d, p = entry
        key, spec = range_of(d, p)
        continues = run_key == key and last_source.get(key, -1) < p
        if not continues:
            anchors.append(LabelRange(position, spec.style, spec.prefix, spec.first + consumed.get(key, 0)))
            run_key = key
        consumed[key] = consumed.get(key, 0) + 1
        last_source[key] = p
    del counts
    # Anker weglassen, die nur fortsetzen, was ohnehin gezählt würde
    compact: list[LabelRange] = []
    for anchor in anchors:
        if compact:
            prev = compact[-1]
            if (prev.style, prev.prefix) == (anchor.style, anchor.prefix) and \
                    prev.first + anchor.start - prev.start == anchor.first:
                continue
        compact.append(anchor)
    _write_labels(writer, compact)


# ---- Eingaben aus der Oberfläche ---------------------------------------------------------------------------------
_PART_RE = re.compile(r"^\s*(\d*)\s*(-?)\s*(\d*)\s*$")


def parse_ranges(text: str, count: int) -> list[int]:
    """„1-3, 5, 8-“ → [0, 1, 2, 4, 7, …] (0-basiert, Reihenfolge wie angegeben, ohne Doppelte).
    Erlaubt: Zahl, a-b, a- (bis Ende), -b (ab Anfang). Leer = alle Seiten."""
    text = (text or "").replace("–", "-").replace("—", "-")
    if not text.strip():
        return list(range(count))
    text = re.sub(r"\s*-\s*", "-", text)
    out: list[int] = []
    for part in re.split(r"[,\s]+", text):
        if not part.strip():
            continue
        m = _PART_RE.match(part)
        if not m or (not m.group(1) and not m.group(3)):
            raise PdfEditError(f"„{part.strip()}“ verstehe ich nicht – Beispiel: 1-3, 5, 8-")
        a = int(m.group(1)) if m.group(1) else 1
        b = int(m.group(3)) if m.group(3) else (count if m.group(2) else a)
        if a < 1 or b < 1 or a > count or b > count:
            raise PdfEditError(f"„{part.strip()}“: das PDF hat nur {count} Seiten")
        step = 1 if b >= a else -1
        for page in range(a, b + step, step):
            if page - 1 not in out:
                out.append(page - 1)
    return out


def parse_groups(text: str, count: int) -> list[list[int]]:
    """Aufteilen nach Bereichen: Gruppen durch „;“ getrennt, z. B. „1-3; 4-6; 7-“."""
    groups = [parse_ranges(chunk, count) for chunk in (text or "").split(";") if chunk.strip()]
    if not groups:
        raise PdfEditError("Bitte Bereiche angeben, z. B. 1-3; 4-6")
    return groups


def every(count: int, size: int) -> list[list[int]]:
    """Aufteilen alle `size` Seiten (1 = jede Seite einzeln)."""
    if size < 1:
        raise PdfEditError("Teilgröße muss mindestens 1 Seite sein")
    return [list(range(start, min(start + size, count))) for start in range(0, count, size)]


def part_names(stem: str, count: int) -> list[str]:
    width = len(str(count))
    return [f"{stem}_teil{str(i + 1).zfill(width)}.pdf" for i in range(count)]


def describe(pages: list[int]) -> str:
    """[0, 1, 2, 4] → „1–3, 5“ (für Meldungen und Dateinamen-Vorschläge)."""
    out, run = [], []
    for page in sorted(set(pages)):
        if run and page == run[-1] + 1:
            run.append(page)
            continue
        if run:
            out.append(f"{run[0] + 1}–{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
        run = [page]
    if run:
        out.append(f"{run[0] + 1}–{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
    return ", ".join(out)
