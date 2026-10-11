"""Test-PDFs für den PDF-Ausbau (Blöcke V/W/X): reproduzierbar erzeugt, ohne Zusatzwerkzeug.

- `audit_doc(ort, geheim)`: ein PDF, in dem das Wort **nur** an diesem Ablageort steht (Regel 8, `pdfaudit.LOCATIONS`).
- `encrypted(...)`, `broken()`, `scanned()`, `big(...)`: Sonderfälle aus der Definition of Done.
- `assert_valid_pdf(data)`: dieselbe Prüfung wie `qpdf --check` (pikepdf/libqpdf).

`tests/fixtures/make_fixtures.py` schreibt die kleinen Dateien nach `tests/fixtures/pdf/`; große entstehen nur im Test.
"""
from __future__ import annotations

import io
import zlib

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, StreamObject,
                           TextStringObject)

from pdfhelp import make_pdf

PASSWORD = "geheim-123"


def _out(writer: PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _writer(texts=("Neutraler Text",)) -> PdfWriter:
    return PdfWriter(clone_from=io.BytesIO(make_pdf(list(texts), outline=False, title="")))


def assert_valid_pdf(data: bytes) -> None:
    """Wie `qpdf --check`: Syntax, xref, Streams. Schlägt mit der Fehlerliste fehl."""
    import pikepdf
    with pikepdf.Pdf.open(io.BytesIO(data)) as pdf:
        problems = pdf.check_pdf_syntax()
    assert not problems, problems


# ---- ein Geheimnis an genau einem Ort -----------------------------------------------------------------------------
def audit_doc(location: str, secret: str) -> bytes:
    if location == "page_text":
        return make_pdf([secret], outline=False, title="")
    if location == "appearance":
        from notex.core import pdfannot as A
        data = A.add_text(make_pdf(["Neutraler Text"], outline=False, title=""), 0, (72, 200, 300, 210), secret, 14)
        writer = PdfWriter(clone_from=io.BytesIO(data))
        annot = writer.pages[0]["/Annots"][0].get_object()
        del annot["/Contents"]                                # nur noch im Erscheinungsbild
        return _out(writer)
    if location == "annotation":
        from notex.core import pdfannot as A
        return A.add_note(make_pdf(["Neutraler Text"], outline=False, title=""), 0, 100, 100, secret)
    writer = _writer()
    root = writer._root_object
    if location == "form":
        field = DictionaryObject({NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("feld1"),
                                  NameObject("/V"): TextStringObject(secret)})
        root[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([writer._add_object(field)])})
    elif location == "outline":
        writer.add_outline_item(secret, 0)
    elif location == "attachment":
        writer.add_attachment("bericht.txt", f"Bericht über {secret}".encode("utf-8"))
    elif location == "layer":
        ocg = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/OCG"),
                                                   NameObject("/Name"): TextStringObject(secret)}))
        root[NameObject("/OCProperties")] = DictionaryObject({
            NameObject("/OCGs"): ArrayObject([ocg]),
            NameObject("/D"): DictionaryObject({NameObject("/ON"): ArrayObject([ocg])})})
    elif location == "metadata":
        writer.add_metadata({"/Author": secret})
    elif location == "xmp":
        xmp = StreamObject()
        xmp._data = (f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>{secret}</dc:creator></x:xmpmeta>').encode()
        xmp[NameObject("/Type")] = NameObject("/Metadata")
        xmp[NameObject("/Subtype")] = NameObject("/XML")
        root[NameObject("/Metadata")] = writer._add_object(xmp)
    elif location == "structure":
        element = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/StructElem"),
                                                       NameObject("/S"): NameObject("/Figure"),
                                                       NameObject("/Alt"): TextStringObject(secret)}))
        root[NameObject("/StructTreeRoot")] = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/StructTreeRoot"), NameObject("/K"): ArrayObject([element])}))
    elif location == "names":
        labels = DictionaryObject({NameObject("/Nums"): ArrayObject([
            NumberObject(0), DictionaryObject({NameObject("/S"): NameObject("/D"),
                                               NameObject("/P"): TextStringObject(secret + "-")})])})
        root[NameObject("/PageLabels")] = labels
    elif location == "javascript":
        action = writer._add_object(DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                                                      NameObject("/JS"): TextStringObject(f"app.alert('{secret}')")}))
        root[NameObject("/Names")] = DictionaryObject({NameObject("/JavaScript"): DictionaryObject({
            NameObject("/Names"): ArrayObject([TextStringObject("hallo"), action])})})
    elif location == "objects":
        page = writer.pages[0]
        page[NameObject("/PieceInfo")] = DictionaryObject({NameObject("/Notiz"): TextStringObject(secret)})
    elif location == "raw":
        writer.add_metadata({"/Subject": secret})
        first = _out(writer)
        later = PdfWriter(io.BytesIO(first), incremental=True)  # früherer Speicherstand bleibt in den Bytes
        later.add_metadata({"/Subject": "harmlos"})
        return _out(later)
    else:
        raise ValueError(location)
    return _out(writer)


# ---- Sonderfälle -----------------------------------------------------------------------------------------------
def encrypted(algorithm: str = "AES-256", user: str = PASSWORD, owner: str | None = None) -> bytes:
    """AES-256 (R6) wie fckNotes schreibt; „RC4-128“/„AES-128“ nur als Lese-Fixture."""
    writer = _writer(["Verschlüsselter Inhalt"])
    writer.encrypt(user, owner or user + "-owner", algorithm=algorithm)
    return _out(writer)


def broken() -> bytes:
    """Abgeschnitten und mit kaputter xref – muss sauber abgelehnt oder repariert werden, nie abstürzen."""
    data = make_pdf(["Seite eins", "Seite zwei"], outline=False)
    cut = data[: int(len(data) * 0.7)]
    return cut.replace(b"xref", b"xrfe", 1)


def scanned(pages: int = 1, width: int = 400, height: int = 560) -> bytes:
    """Nur ein Graustufenbild pro Seite, kein Text (wie ein Scan). Muster: dunkle „Zeilen“ auf hellem Grund."""
    rows = []
    for y in range(height):
        dark = (y // 12) % 3 == 0 and 40 <= y <= height - 40
        rows.append(bytes((40 if dark and 30 <= x <= width - 30 and (x // 9) % 4 else 245) for x in range(width)))
    raw = b"".join(rows)
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(595, 842)
        image = StreamObject()
        image._data = zlib.compress(raw, 6)
        for key, value in (("/Type", NameObject("/XObject")), ("/Subtype", NameObject("/Image")),
                           ("/Width", NumberObject(width)), ("/Height", NumberObject(height)),
                           ("/ColorSpace", NameObject("/DeviceGray")), ("/BitsPerComponent", NumberObject(8)),
                           ("/Filter", NameObject("/FlateDecode"))):
            image[NameObject(key)] = value
        content = DecodedStreamObject()
        content.set_data(b"q 515 0 0 721 40 60 cm /Im1 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
            NameObject("/Im1"): writer._add_object(image)})})
    return _out(writer)


def big(pages: int = 520) -> bytes:
    """Über 500 Seiten, schnell erzeugt (ein gemeinsamer Font, kurze Inhalte)."""
    return make_pdf([f"Seite {i + 1}" for i in range(pages)], outline=False, title="Groß")


def is_encrypted(data: bytes) -> bool:
    return PdfReader(io.BytesIO(data)).is_encrypted
