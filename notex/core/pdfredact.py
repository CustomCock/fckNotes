"""Echtes Schwärzen (Qt-frei, pypdf): Seiten mit geschwärzten Bereichen werden durch ein Bild der Seite ersetzt,
in das die Balken schon eingemalt sind (das Rendern macht die Oberfläche mit PDFium). Danach steckt auf diesen Seiten
kein Text mehr – auch nicht unter dem Balken, nicht in Schriften, nicht in alten Objekten.

Warum nicht einfach ein schwarzes Rechteck darüberlegen? Weil der Text darunter weiter in der Datei steht: Kopieren,
Suchen oder ein Texteditor holen ihn wieder heraus. Genau das passiert in der Praxis immer wieder.

Was zusätzlich bereinigt wird:
- Anmerkungen und Formularfelder der geschwärzten Seiten (stecken im Bild; die Feld-Objekte samt Werten fliegen raus),
- die Struktur für Barrierefreiheit (/StructTreeRoot kann Alternativtexte enthalten),
- unbenutzte Objekte (alte Seiteninhalte, Schriften, Bilder),
- auf Wunsch: Metadaten (Titel, Autor, XMP), Anhänge und Skripte, Lesezeichen.
`leftovers` sucht danach die geschwärzten Wörter im ganzen Dokument und meldet Fundstellen.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject,
                           StreamObject)

from notex.core.pdfannot import Rect, finish
from notex.core.pdfpages import PdfEditError, copy_metadata, open_reader

PAD = 1.0          # Balken etwas größer als die Auswahl (Glyphen-Ränder)


@dataclass
class PageImage:
    """Gerenderte, bereits geschwärzte Seite in Anzeige-Ausrichtung."""
    width_pt: float
    height_pt: float
    width_px: int
    height_px: int
    data: bytes             # kind "rgb": 3 Byte je Pixel; kind "jpeg": fertige JPEG-Datei
    kind: str = "rgb"


@dataclass
class RedactOptions:
    strip_metadata: bool = True
    strip_attachments: bool = True        # eingebettete Dateien, JavaScript, Aktionen beim Öffnen
    strip_outline: bool = False


def normalize_boxes(boxes: list[tuple[int, Rect]], pad: float = PAD) -> dict[int, list[Rect]]:
    """(Seite, Rechteck) → je Seite die Balken, leicht vergrößert, winzige verworfen."""
    out: dict[int, list[Rect]] = {}
    for page, (x0, y0, x1, y1) in boxes:
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            continue
        out.setdefault(page, []).append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    return out


def _image_stream(writer: PdfWriter, image: PageImage):
    stream = StreamObject()
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                   NameObject("/Width"): NumberObject(image.width_px), NameObject("/Height"): NumberObject(image.height_px),
                   NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
    if image.kind == "jpeg":
        stream._data = image.data
        stream[NameObject("/Filter")] = NameObject("/DCTDecode")
    else:
        if len(image.data) != image.width_px * image.height_px * 3:
            raise PdfEditError("Seitenbild hat die falsche Größe")
        stream._data = zlib.compress(image.data, 6)
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    return writer._add_object(stream)


def _field_tree_remove(node_array, targets: set[int]) -> None:
    """Widgets/Felder mit IDs in `targets` aus einem /Fields- bzw. /Kids-Array entfernen (rekursiv); leere Eltern
    fallen mit weg."""
    if node_array is None:
        return
    array = node_array.get_object()
    for i in range(len(array) - 1, -1, -1):
        obj = array[i].get_object()
        if id(obj) in targets:
            del array[i]
            continue
        kids = obj.get("/Kids")
        if kids is not None:
            _field_tree_remove(kids, targets)
            if not kids.get_object():
                del array[i]


def redact_pages(data: bytes, images: dict[int, PageImage], options: RedactOptions | None = None) -> bytes:
    """Seiten `images` (Index → geschwärztes Seitenbild) ersetzen und das Dokument bereinigen."""
    options = options or RedactOptions()
    reader = open_reader(data)
    count = len(reader.pages)
    if not images:
        raise PdfEditError("Keine Bereiche zum Schwärzen")
    for index in images:
        if not 0 <= index < count:
            raise PdfEditError(f"Seite {index + 1} gibt es nicht")
    writer = PdfWriter(clone_from=reader)
    removed: set[int] = set()
    for index, image in images.items():
        page = writer.pages[index]
        for ref in page.get("/Annots", None) or []:
            removed.add(id(ref.get_object()))
        for key in ("/Annots", "/Thumb", "/PieceInfo", "/Metadata", "/StructParents", "/Group", "/AA", "/B",
                    "/Tabs", "/VP", "/SeparationInfo", "/UserUnit", "/BoxColorInfo"):
            if key in page:
                del page[key]
        box = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(round(image.width_pt, 3)),
                           FloatObject(round(image.height_pt, 3))])
        page[NameObject("/MediaBox")] = box
        for key in ("/CropBox", "/BleedBox", "/TrimBox", "/ArtBox"):
            if key in page:
                del page[key]
        page[NameObject("/Rotate")] = NumberObject(0)
        content = DecodedStreamObject()
        content.set_data(f"q {image.width_pt:.3f} 0 0 {image.height_pt:.3f} 0 0 cm /Im0 Do Q".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): _image_stream(writer, image)})})
    root = writer._root_object
    form = root.get("/AcroForm")
    if form is not None and removed:
        _field_tree_remove(form.get_object().get("/Fields"), removed)
        if not form.get_object().get("/Fields"):
            del root["/AcroForm"]
    for key in ("/StructTreeRoot", "/MarkInfo"):
        if key in root:
            del root[key]
    if options.strip_attachments:
        names = root.get("/Names")
        if names is not None:
            names = names.get_object()
            for key in ("/EmbeddedFiles", "/JavaScript"):
                if key in names:
                    del names[key]
        for key in ("/OpenAction", "/AA"):
            if key in root:
                del root[key]
        for page in writer.pages:
            for ref in page.get("/Annots", None) or []:
                annot = ref.get_object()
                if annot.get("/Subtype") == "/FileAttachment":
                    removed.add(id(annot))
            annots = page.get("/Annots")
            if annots is not None:
                annots = annots.get_object()
                for i in range(len(annots) - 1, -1, -1):
                    if id(annots[i].get_object()) in removed:
                        del annots[i]
    if options.strip_outline:
        for key in ("/Outlines", "/PageMode"):
            if key in root:
                del root[key]
    if options.strip_metadata:
        if "/Metadata" in root:
            del root["/Metadata"]
        writer.metadata = None
    else:
        copy_metadata(reader, writer)
    return finish(writer)


def leftovers(data: bytes, needles: list[str]) -> list[str]:
    """Wo stehen die Wörter noch? Durchsucht Seitentext, Anmerkungen, Formularwerte, Lesezeichen und Metadaten.
    Liefert lesbare Fundstellen („„Meier“ auf Seite 3“); leer = nichts gefunden."""
    needles = [n.strip() for n in needles if n and n.strip()]
    if not needles:
        return []
    reader = open_reader(data)
    lowered = [(n, n.casefold()) for n in dict.fromkeys(needles)]
    hits: list[str] = []

    def check(text: str, where: str) -> None:
        text = (text or "").casefold()
        for original, needle in lowered:
            if needle in text:
                hits.append(f"„{original}“ {where}")

    for index, page in enumerate(reader.pages):
        try:
            check(page.extract_text() or "", f"auf Seite {index + 1}")
        except Exception:                                # noqa: BLE001 – kaputte Inhalte nicht als „sauber“ werten
            hits.append(f"Seite {index + 1} ließ sich nicht prüfen")
        for ref in page.get("/Annots", None) or []:
            try:
                annot = ref.get_object()
                check(str(annot.get("/Contents", "")) + " " + str(annot.get("/V", "")),
                      f"in einer Anmerkung/einem Feld auf Seite {index + 1}")
            except Exception:                            # noqa: BLE001
                continue
    try:
        def walk(items):
            for item in items:
                if isinstance(item, list):
                    walk(item)
                else:
                    check(str(getattr(item, "title", "") or ""), "in den Lesezeichen")
        walk(reader.outline)
    except Exception:                                    # noqa: BLE001
        pass
    meta = reader.metadata or {}
    check(" ".join(str(v) for v in meta.values()), "in den Metadaten")
    xmp = reader.trailer["/Root"].get("/Metadata")
    if xmp is not None:
        try:
            check(xmp.get_object().get_data().decode("utf-8", "replace"), "in den XMP-Metadaten")
        except Exception:                                # noqa: BLE001
            pass
    return list(dict.fromkeys(hits))
