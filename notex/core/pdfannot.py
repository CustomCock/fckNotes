"""PDF-Anmerkungen (Qt-frei, pypdf): Markieren, Unterstreichen, Durchstreichen, Haftnotiz, Text auf der Seite;
auflisten, Text ändern, löschen.

Koordinaten: Die Oberfläche denkt in „Ansichts-Punkten“ – Ursprung oben links, so wie die Seite angezeigt wird
(also nach /Rotate gedreht). PDF selbst rechnet in Benutzer-Koordinaten (Ursprung unten links der CropBox, ungedreht).
`PageGeom` rechnet hin und her; alle Formeln sind getestet.

Jede neue Anmerkung bekommt einen eigenen Appearance-Stream (/AP): so sieht sie in jedem Betrachter gleich aus
(Acrobat, Browser, PDFium in fckNotes), auch in solchen, die selbst nichts zeichnen. Text auf der Seite nutzt
Helvetica (eine der 14 Standardschriften, nichts wird eingebettet) mit WinAnsi-Kodierung – Zeichen außerhalb davon
(z. B. Emoji) erscheinen als „?“. Gelöschte Anmerkungen werden mitsamt ihren Objekten entfernt.
"""
from __future__ import annotations

import io
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject,
                           TextStringObject)

from notex.core.pdfpages import PdfEditError, open_reader

ID_PREFIX = "fcknotes-"
MARKUP_KINDS = ("highlight", "underline", "strikeout")
DEFAULT_COLORS = {"highlight": "#ffd400", "underline": "#2f6fdf", "strikeout": "#d03030", "note": "#ffd400",
                  "text": "#1a1a1a"}
SKIP_SUBTYPES = {"/Popup", "/Link", "/Widget"}        # Formularfelder: eigenes Modul
KIND_NAMES = {"/Highlight": "highlight", "/Underline": "underline", "/StrikeOut": "strikeout", "/Text": "note",
              "/FreeText": "text", "/Squiggly": "squiggly", "/Ink": "ink", "/Square": "square", "/Circle": "circle",
              "/Stamp": "stamp", "/Line": "line", "/Polygon": "polygon", "/PolyLine": "polyline",
              "/Caret": "caret", "/FileAttachment": "attachment", "/Redact": "redact"}
LABELS = {"highlight": "Markierung", "underline": "Unterstreichung", "strikeout": "Durchstreichung",
          "note": "Notiz", "text": "Text", "stamp": "Stempel"}

Rect = tuple[float, float, float, float]


# ---- Geometrie -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PageGeom:
    box: Rect               # CropBox (x0, y0, x1, y1) in Benutzer-Koordinaten
    rotate: int = 0         # 0 / 90 / 180 / 270, im Uhrzeigersinn

    @property
    def raw_size(self) -> tuple[float, float]:
        return self.box[2] - self.box[0], self.box[3] - self.box[1]

    @property
    def size(self) -> tuple[float, float]:
        """Größe wie angezeigt (nach Drehung)."""
        w, h = self.raw_size
        return (h, w) if self.rotate in (90, 270) else (w, h)

    def to_pdf(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.raw_size
        r = self.rotate
        if r == 90:
            u, v = y, h - x
        elif r == 180:
            u, v = w - x, h - y
        elif r == 270:
            u, v = w - y, x
        else:
            u, v = x, y
        return self.box[0] + u, self.box[3] - v

    def to_view(self, px: float, py: float) -> tuple[float, float]:
        w, h = self.raw_size
        u, v = px - self.box[0], self.box[3] - py
        r = self.rotate
        if r == 90:
            return h - v, u
        if r == 180:
            return w - u, h - v
        if r == 270:
            return v, w - u
        return u, v

    def rect_to_pdf(self, rect: Rect) -> list[float]:
        x0, y0, x1, y1 = rect
        points = [self.to_pdf(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return [min(xs), min(ys), max(xs), max(ys)]

    def rect_to_view(self, rect: Rect) -> Rect:
        x0, y0, x1, y1 = rect
        points = [self.to_view(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)

    def matrix(self) -> list[float]:
        """Form-Matrix, damit ein Erscheinungsbild auf einer gedrehten Seite aufrecht steht."""
        angle = math.radians(self.rotate)
        c, s = round(math.cos(angle)), round(math.sin(angle))
        return [c, s, -s, c, 0, 0]


def _box(page) -> Rect:
    box = page.cropbox
    x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def geometry_of(page) -> PageGeom:
    return PageGeom(_box(page), int(page.rotation or 0) % 360)


def page_geometry(data: bytes, index: int) -> PageGeom:
    reader = open_reader(data)
    if not 0 <= index < len(reader.pages):
        raise PdfEditError(f"Seite {index + 1} gibt es nicht")
    return geometry_of(reader.pages[index])


# ---- Text für Helvetica (WinAnsi) --------------------------------------------------------------------------------
def _widths() -> dict[str, int]:
    try:
        from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS
        return CORE_FONT_METRICS["Helvetica"].character_widths
    except Exception:                                     # noqa: BLE001 – andere pypdf-Version: Näherung
        return {"default": 556, " ": 278}


_WIDTHS = _widths()


def text_width(text: str, size: float) -> float:
    default = _WIDTHS.get("default", 556)
    return sum(_WIDTHS.get(ch, default) for ch in text) * size / 1000


def wrap(text: str, size: float, width: float) -> list[str]:
    """Zeilenumbruch nach Wörtern (überlange Wörter werden hart getrennt); Absätze aus dem Text bleiben."""
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if text_width(candidate, size) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
            while text_width(current, size) > width and len(current) > 1:
                cut = len(current)
                while cut > 1 and text_width(current[:cut], size) > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines


def pdf_string(text: str) -> str:
    """Literal-String für Content-Streams in WinAnsi (cp1252); nicht darstellbare Zeichen → „?“."""
    raw = text.encode("cp1252", "replace")
    out = []
    for byte in raw:
        ch = chr(byte)
        if ch in "\\()":
            out.append("\\" + ch)
        elif byte < 32 or byte > 126:
            out.append(f"\\{byte:03o}")
        else:
            out.append(ch)
    return "(" + "".join(out) + ")"


def rgb(color: str) -> tuple[float, float, float]:
    color = (color or "#000000").lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return 0.0, 0.0, 0.0


def _num(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _rgb_op(color: str, op: str) -> str:
    r, g, b = rgb(color)
    return f"{_num(r)} {_num(g)} {_num(b)} {op}"


# ---- Schreiben ---------------------------------------------------------------------------------------------------
def open_writer(data: bytes) -> PdfWriter:
    return PdfWriter(clone_from=open_reader(data))


def prune_unreachable(writer: PdfWriter) -> int:
    """Alle Objekte verwerfen, die vom Katalog (bzw. /Info) aus nicht erreichbar sind; liefert ihre Anzahl.

    pypdfs eigenes „remove_unreferenced“ zählt jeden Verweis – auch den von einem verwaisten Objekt auf ein anderes.
    Ketten wie alte Lesezeichen (Prev/Next/Parent) oder eine gelöschte Seite samt Schriften blieben so in der Datei.
    Hier zählt nur, was man vom Dokument aus wirklich erreicht."""
    from pypdf.generic import IndirectObject
    seen: set[int] = set()
    stack: list = [writer._root_object]
    info = getattr(writer, "_info_obj", None)
    if info is not None:
        stack.append(info)
    encrypt = getattr(writer, "_encrypt_entry", None)
    if encrypt is not None:
        stack.append(encrypt)
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            if obj.pdf is not writer or obj.idnum in seen:
                continue
            seen.add(obj.idnum)
            try:
                obj = obj.get_object()
            except Exception:                            # noqa: BLE001 – kaputter Verweis
                continue
        else:
            ref = getattr(obj, "indirect_reference", None)
            if ref is not None and getattr(ref, "pdf", None) is writer:
                seen.add(ref.idnum)
        if isinstance(obj, DictionaryObject):
            stack.extend(obj.values())
        elif isinstance(obj, ArrayObject):
            stack.extend(obj)
    dropped = 0
    for index, obj in enumerate(writer._objects):
        if obj is not None and index + 1 not in seen:
            writer._objects[index] = None
            dropped += 1
    return dropped


def finish(writer: PdfWriter) -> bytes:
    """Unerreichbare Objekte (gelöschte Anmerkungen, alte Erscheinungsbilder, Seiten) entfernen und schreiben."""
    prune_unreachable(writer)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _page(writer: PdfWriter, index: int):
    if not 0 <= index < len(writer.pages):
        raise PdfEditError(f"Seite {index + 1} gibt es nicht")
    return writer.pages[index]


def _annots(page) -> ArrayObject:
    annots = page.get("/Annots")
    if annots is None:
        annots = ArrayObject()
        page[NameObject("/Annots")] = annots
        return annots
    annots = annots.get_object()
    return annots


def _now() -> str:
    return datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")


def _floats(values) -> ArrayObject:
    return ArrayObject([FloatObject(round(float(v), 3)) for v in values])


def _form(writer: PdfWriter, content: str, bbox, resources: DictionaryObject | None = None,
          matrix: list[float] | None = None):
    stream = DecodedStreamObject()
    stream.set_data(content.encode("latin-1"))
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Form")
    stream[NameObject("/BBox")] = _floats(bbox)
    if matrix and matrix != [1, 0, 0, 1, 0, 0]:
        stream[NameObject("/Matrix")] = _floats(matrix)
    stream[NameObject("/Resources")] = resources if resources is not None else DictionaryObject()
    return writer._add_object(stream)


def helvetica_resources(writer: PdfWriter) -> DictionaryObject:
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica"),
                             NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
    return DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): writer._add_object(font)})})


def _attach(writer: PdfWriter, index: int, annot: DictionaryObject, appearance=None) -> int:
    page = _page(writer, index)
    if appearance is not None:
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance})
    annot[NameObject("/P")] = page.indirect_reference
    ref = writer._add_object(annot)
    annots = _annots(page)
    annots.append(ref)
    return len(annots) - 1


def _base(subtype: str, rect: list[float], color: str, contents: str = "") -> DictionaryObject:
    r, g, b = rgb(color)
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject(subtype),
        NameObject("/Rect"): _floats(rect),
        NameObject("/C"): _floats([r, g, b]),
        NameObject("/F"): NumberObject(4),                   # drucken
        NameObject("/NM"): TextStringObject(ID_PREFIX + uuid.uuid4().hex[:12]),
        NameObject("/M"): TextStringObject(_now()),
        NameObject("/CreationDate"): TextStringObject(_now()),
    })
    if contents:
        annot[NameObject("/Contents")] = TextStringObject(contents)
    return annot


# ---- Markieren / Unterstreichen / Durchstreichen -----------------------------------------------------------------
def _quads(geom: PageGeom, rects: list[Rect]) -> list[list[tuple[float, float]]]:
    """Je Zeilen-Rechteck (Ansicht) die vier Ecken in PDF-Koordinaten: oben links, oben rechts, unten links,
    unten rechts. Gerechnet im ungedrehten Seiteninhalt – dort steht Text (fast immer) aufrecht, also stimmen
    Unter- und Durchstreichen auch auf Seiten, die per /Rotate gedreht angezeigt werden."""
    out = []
    for rect in rects:
        if rect[2] - rect[0] < 0.1 or rect[3] - rect[1] < 0.1:
            continue
        x0, y0, x1, y1 = geom.rect_to_pdf(rect)
        out.append([(x0, y1), (x1, y1), (x0, y0), (x1, y0)])
    return out


def markup_content(kind: str, quads, color: str) -> str:
    ops = ["q"]
    if kind == "highlight":
        ops += ["/GS0 gs", _rgb_op(color, "rg")]
        for tl, tr, bl, br in quads:
            ops.append(f"{_num(tl[0])} {_num(tl[1])} m {_num(tr[0])} {_num(tr[1])} l {_num(br[0])} {_num(br[1])} l "
                       f"{_num(bl[0])} {_num(bl[1])} l h f")
    else:
        ops.append(_rgb_op(color, "RG"))
        for tl, tr, bl, br in quads:
            height = math.dist(tl, bl)
            ops.append(f"{_num(max(0.6, height / 14))} w")
            if kind == "underline":
                f = 0.08                                       # knapp über der Unterkante
                a = (bl[0] + (tl[0] - bl[0]) * f, bl[1] + (tl[1] - bl[1]) * f)
                b = (br[0] + (tr[0] - br[0]) * f, br[1] + (tr[1] - br[1]) * f)
            else:
                a = ((tl[0] + bl[0]) / 2, (tl[1] + bl[1]) / 2)
                b = ((tr[0] + br[0]) / 2, (tr[1] + br[1]) / 2)
            ops.append(f"{_num(a[0])} {_num(a[1])} m {_num(b[0])} {_num(b[1])} l S")
    ops.append("Q")
    return "\n".join(ops)


def add_markup(data: bytes, index: int, kind: str, rects: list[Rect], color: str | None = None,
               contents: str = "") -> bytes:
    """Markierung über Textzeilen; `rects` = Zeilen-Rechtecke in Ansichts-Punkten (aus der Textauswahl)."""
    if kind not in MARKUP_KINDS:
        raise PdfEditError(f"Unbekannte Art „{kind}“")
    writer = open_writer(data)
    geom = geometry_of(_page(writer, index))
    quads = _quads(geom, rects)
    if not quads:
        raise PdfEditError("Nichts markiert")
    color = color or DEFAULT_COLORS[kind]
    xs = [p[0] for q in quads for p in q]
    ys = [p[1] for q in quads for p in q]
    pad = 1.0
    rect = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]
    subtype = {"highlight": "/Highlight", "underline": "/Underline", "strikeout": "/StrikeOut"}[kind]
    annot = _base(subtype, rect, color, contents)
    annot[NameObject("/QuadPoints")] = _floats([c for q in quads for p in q for c in p])
    resources = DictionaryObject()
    if kind == "highlight":
        state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                                  NameObject("/BM"): NameObject("/Multiply")})
        resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS0"): state})
    appearance = _form(writer, markup_content(kind, quads, color), rect, resources)
    _attach(writer, index, annot, appearance)
    return finish(writer)


# ---- Haftnotiz ---------------------------------------------------------------------------------------------------
NOTE_SIZE = 20.0


def note_content(rect: list[float], color: str) -> str:
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    lines = "\n".join(f"{_num(x0 + w * 0.22)} {_num(y0 + h * f)} m {_num(x0 + w * 0.78)} {_num(y0 + h * f)} l S"
                      for f in (0.68, 0.5, 0.32))
    return (f"q {_rgb_op(color, 'rg')} 0.25 0.25 0.25 RG 0.8 w {_num(x0 + 0.5)} {_num(y0 + 0.5)} "
            f"{_num(w - 1)} {_num(h - 1)} re B\n0.25 0.25 0.25 RG 1.1 w\n{lines}\nQ")


def add_note(data: bytes, index: int, x: float, y: float, text: str, color: str | None = None) -> bytes:
    """Haftnotiz (Symbol, Text erscheint beim Anklicken/Überfahren) mit der linken oberen Ecke bei (x, y)."""
    if not text.strip():
        raise PdfEditError("Die Notiz ist leer")
    writer = open_writer(data)
    geom = geometry_of(_page(writer, index))
    rect = geom.rect_to_pdf((x, y, x + NOTE_SIZE, y + NOTE_SIZE))
    color = color or DEFAULT_COLORS["note"]
    annot = _base("/Text", rect, color, text)
    annot[NameObject("/Name")] = NameObject("/Comment")
    annot[NameObject("/Open")] = BooleanObject(False)
    appearance = _form(writer, note_content(rect, color), rect)
    _attach(writer, index, annot, appearance)
    return finish(writer)


# ---- Text auf der Seite (FreeText) -------------------------------------------------------------------------------
TEXT_PADDING = 3.0


def freetext_content(text: str, width: float, height: float, size: float, color: str, border: bool = False,
                     background: str | None = None) -> str:
    """Erscheinungsbild im eigenen Rechteck 0..width × 0..height (aufrecht, Matrix dreht es auf die Seite)."""
    ops = ["q"]
    if background:
        ops.append(f"{_rgb_op(background, 'rg')} 0 0 {_num(width)} {_num(height)} re f")
    if border:
        ops.append(f"{_rgb_op(color, 'RG')} 0.8 w 0.4 0.4 {_num(width - 0.8)} {_num(height - 0.8)} re S")
    ops.append(f"0 0 {_num(width)} {_num(height)} re W n")
    leading = size * 1.2
    lines = wrap(text, size, max(1.0, width - 2 * TEXT_PADDING))
    ops.append(f"BT /Helv {_num(size)} Tf {_rgb_op(color, 'rg')} {_num(leading)} TL")
    ops.append(f"{_num(TEXT_PADDING)} {_num(height - TEXT_PADDING - size * 0.93)} Td")
    for i, line in enumerate(lines):
        ops.append(("T* " if i else "") + f"{pdf_string(line)} Tj")
    ops.append("ET Q")
    return "\n".join(ops)


def text_height(text: str, size: float, width: float) -> float:
    return len(wrap(text, size, max(1.0, width - 2 * TEXT_PADDING))) * size * 1.2 + 2 * TEXT_PADDING


def add_text(data: bytes, index: int, rect: Rect, text: str, size: float = 12.0, color: str | None = None,
             border: bool = False, grow: bool = True) -> bytes:
    """Text direkt auf die Seite (FreeText-Anmerkung). `rect` in Ansichts-Punkten; `grow`: Höhe an den Text anpassen."""
    if not text.strip():
        raise PdfEditError("Der Text ist leer")
    if not 4 <= size <= 144:
        raise PdfEditError("Schriftgröße zwischen 4 und 144 pt")
    writer = open_writer(data)
    _add_text(writer, index, rect, text, size, color or DEFAULT_COLORS["text"], border, grow)
    return finish(writer)


def _add_text(writer, index, rect, text, size, color, border, grow, replace: int | None = None) -> None:
    page = _page(writer, index)
    geom = geometry_of(page)
    x0, y0, x1, y1 = rect
    width = max(x1 - x0, size * 2)
    height = max(y1 - y0, size * 1.2 + 2 * TEXT_PADDING)
    if grow:
        height = max(height, text_height(text, size, width))
    view = (x0, y0, x0 + width, y0 + height)
    pdf_rect = geom.rect_to_pdf(view)
    annot = _base("/FreeText", pdf_rect, color, text)
    r, g, b = rgb(color)
    annot[NameObject("/DA")] = TextStringObject(f"/Helv {_num(size)} Tf {_num(r)} {_num(g)} {_num(b)} rg")
    annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): NumberObject(1 if border else 0)})
    if not border:
        annot[NameObject("/C")] = ArrayObject()                 # kein Rahmen in Betrachtern, die selbst zeichnen
    annot[NameObject("/Rotate")] = NumberObject(geom.rotate)
    appearance = _form(writer, freetext_content(text, width, height, size, color, border), [0, 0, width, height],
                       helvetica_resources(writer), geom.matrix())
    if replace is None:
        _attach(writer, index, annot, appearance)
    else:
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance})
        annot[NameObject("/P")] = page.indirect_reference
        _annots(page)[replace] = writer._add_object(annot)


# ---- Auflisten, ändern, löschen ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class AnnotInfo:
    page: int
    index: int              # Position in /Annots
    kind: str               # highlight | underline | strikeout | note | text | … (siehe KIND_NAMES)
    rect: Rect              # Ansichts-Punkte
    contents: str
    color: str
    ours: bool              # von fckNotes angelegt

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind.capitalize())

    @property
    def text_editable(self) -> bool:
        return self.kind in ("note", "text") or self.kind in MARKUP_KINDS


def _hex(color) -> str:
    try:
        values = [float(v) for v in color][:3]
    except (TypeError, ValueError):
        return ""
    if len(values) != 3:
        return ""
    return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02x}" for v in values)


def list_annotations(data: bytes, index: int) -> list[AnnotInfo]:
    reader = open_reader(data)
    if not 0 <= index < len(reader.pages):
        return []
    page = reader.pages[index]
    geom = geometry_of(page)
    out = []
    for position, ref in enumerate(page.get("/Annots", None) or []):
        try:
            annot = ref.get_object()
            subtype = str(annot.get("/Subtype", ""))
            if subtype in SKIP_SUBTYPES or "/Rect" not in annot:
                continue
            x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        except Exception:                                 # noqa: BLE001 – kaputte Einträge überspringen
            continue
        rect = geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        out.append(AnnotInfo(index, position, KIND_NAMES.get(subtype, subtype.lstrip("/").lower()), rect,
                             str(annot.get("/Contents", "") or ""), _hex(annot.get("/C", [])),
                             str(annot.get("/NM", "")).startswith(ID_PREFIX)))
    return out


def hit(annotations: list[AnnotInfo], x: float, y: float, tolerance: float = 2.0) -> AnnotInfo | None:
    """Oberste Anmerkung unter dem Punkt (Ansichts-Punkte)."""
    for info in reversed(annotations):
        x0, y0, x1, y1 = info.rect
        if x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance:
            return info
    return None


def delete_annotation(data: bytes, index: int, position: int) -> bytes:
    writer = open_writer(data)
    page = _page(writer, index)
    annots = _annots(page)
    if not 0 <= position < len(annots):
        raise PdfEditError("Diese Anmerkung gibt es nicht mehr")
    target = annots[position].get_object()
    popup = target.get("/Popup")
    del annots[position]
    if popup is not None:
        popup_obj = popup.get_object()
        for i in range(len(annots) - 1, -1, -1):
            if annots[i].get_object() is popup_obj:
                del annots[i]
    if not annots:
        del page["/Annots"]
    return finish(writer)


def update_text(data: bytes, index: int, position: int, text: str) -> bytes:
    """Text einer Anmerkung ändern: Notiz/Markierung → Kommentar (/Contents); eigener Text auf der Seite wird neu
    gezeichnet (gleiche Position, Schriftgröße und Farbe)."""
    writer = open_writer(data)
    page = _page(writer, index)
    annots = _annots(page)
    if not 0 <= position < len(annots):
        raise PdfEditError("Diese Anmerkung gibt es nicht mehr")
    annot = annots[position].get_object()
    subtype = str(annot.get("/Subtype", ""))
    if subtype == "/FreeText":
        if not text.strip():
            raise PdfEditError("Der Text ist leer")
        size, color = _parse_da(str(annot.get("/DA", "")))
        geom = geometry_of(page)
        x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        view = geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        bs = annot.get("/BS")
        border = bool(bs and float(bs.get_object().get("/W", 0)) > 0)
        _add_text(writer, index, (view[0], view[1], view[2], view[1] + size * 1.2), text, size, color, border,
                  True, replace=position)
    else:
        annot[NameObject("/Contents")] = TextStringObject(text)
        annot[NameObject("/M")] = TextStringObject(_now())
    return finish(writer)


def _parse_da(da: str) -> tuple[float, str]:
    parts = da.split()
    size, color = 12.0, "#000000"
    for i, part in enumerate(parts):
        if part == "Tf" and i >= 1:
            try:
                size = float(parts[i - 1]) or 12.0
            except ValueError:
                pass
        if part == "rg" and i >= 3:
            try:
                color = _hex(parts[i - 3:i])
            except ValueError:
                pass
    return size, color or "#000000"
