"""Test-PDFs ohne Zusatzwerkzeug (pypdf) und Prüfhelfer – Qt-frei, für Kern- und UI-Tests."""
import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def make_pdf(texts, size=(595, 842), outline=True, title="Test") -> bytes:
    """Ein PDF mit je einer Seite pro Text (Helvetica 24 pt oben links), optional Lesezeichen je Seite."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    for text in texts:
        page = writer.add_blank_page(*size)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 24 Tf 72 {size[1] - 100} Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    if outline:
        for index, text in enumerate(texts):
            writer.add_outline_item(text, index)
    if title:
        writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def reader(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


def texts(data: bytes) -> list[str]:
    return [page.extract_text().strip() for page in reader(data).pages]


def rotations(data: bytes) -> list[int]:
    return [page.rotation for page in reader(data).pages]


def contains_anywhere(data: bytes, needle: str) -> bool:
    """Steht `needle` irgendwo in der Datei – roh, in entpackten Streams oder in Objekten (auch unbenutzten)?"""
    raw = needle.encode("latin-1", "replace")
    if raw in data:
        return True
    pdf = reader(data)
    size = int(pdf.trailer.get("/Size", 0))
    for number in range(1, size):
        try:
            obj = pdf.get_object(number)
        except Exception:                      # noqa: BLE001 – freie Nummern
            continue
        if obj is None:
            continue
        try:
            if hasattr(obj, "get_data") and raw in obj.get_data():
                return True
        except Exception:                      # noqa: BLE001
            pass
        if needle in str(obj):
            return True
    return False
