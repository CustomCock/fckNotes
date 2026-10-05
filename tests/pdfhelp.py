"""Test-PDFs ohne Zusatzwerkzeug (pypdf) und Prüfhelfer – Qt-frei, für Kern- und UI-Tests."""
import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DecodedStreamObject, DictionaryObject, NameObject


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


def make_form_pdf() -> bytes:
    """Fremdes Formular wie aus anderen Programmen: Textfeld ohne Erscheinungsbild, Kontrollkästchen mit Zustand
    „Ja“, Optionsfeld-Gruppe (A/B) als Eltern mit Kindern, Auswahlliste, schreibgeschütztes Feld."""
    from pypdf.generic import ArrayObject, FloatObject, NumberObject, StreamObject, TextStringObject
    writer = PdfWriter(clone_from=reader(make_pdf(["Antrag"], outline=False)))
    page = writer.pages[0]

    def rect(x0, y0, x1, y1):
        return ArrayObject([FloatObject(v) for v in (x0, y0, x1, y1)])

    def form(content=b" "):
        stream = StreamObject()
        stream._data = content
        stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                       NameObject("/BBox"): rect(0, 0, 12, 12)})
        return writer._add_object(stream)

    def widget(extra):
        base = {NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/F"): NumberObject(4), NameObject("/P"): page.indirect_reference}
        base.update(extra)
        return DictionaryObject(base)

    text = writer._add_object(widget({NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("Ort"),
                                      NameObject("/Rect"): rect(72, 700, 272, 720),
                                      NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
                                      NameObject("/V"): TextStringObject("Berlin")}))
    box = writer._add_object(widget({
        NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("Zustimmung"),
        NameObject("/Rect"): rect(72, 670, 84, 682), NameObject("/V"): NameObject("/Off"),
        NameObject("/AS"): NameObject("/Off"),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
            NameObject("/Ja"): form(b"0 0 12 12 re f"), NameObject("/Off"): form()})})}))
    group = DictionaryObject({NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("Farbe"),
                              NameObject("/Ff"): NumberObject(1 << 15), NameObject("/V"): NameObject("/A"),
                              NameObject("/Kids"): ArrayObject()})
    group_ref = writer._add_object(group)
    kids = []
    for state, x in (("A", 72), ("B", 100)):
        kid = writer._add_object(widget({
            NameObject("/Parent"): group_ref, NameObject("/Rect"): rect(x, 640, x + 12, 652),
            NameObject("/AS"): NameObject("/A" if state == "A" else "/Off"),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
                NameObject("/" + state): form(b"0 0 12 12 re f"), NameObject("/Off"): form()})})}))
        group["/Kids"].append(kid)
        kids.append(kid)
    choice = writer._add_object(widget({
        NameObject("/FT"): NameObject("/Ch"), NameObject("/T"): TextStringObject("Land"),
        NameObject("/Ff"): NumberObject(1 << 17), NameObject("/Rect"): rect(72, 600, 272, 620),
        NameObject("/Opt"): ArrayObject([TextStringObject("Deutschland"), TextStringObject("Österreich")]),
        NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"), NameObject("/V"): TextStringObject("Deutschland")}))
    locked = writer._add_object(widget({
        NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("Aktenzeichen"),
        NameObject("/Ff"): NumberObject(1), NameObject("/Rect"): rect(72, 570, 272, 590),
        NameObject("/V"): TextStringObject("AZ-1")}))
    page[NameObject("/Annots")] = ArrayObject([text, box, *kids, choice, locked])
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): ArrayObject([text, box, group_ref, choice, locked]),
        NameObject("/NeedAppearances"): BooleanObject(True)})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
