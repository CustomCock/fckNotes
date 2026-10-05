"""Echtes Schwärzen (notex/core/pdfredact.py) – ohne Qt. Prüft vor allem: nichts bleibt in der Datei."""
import io
import logging

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

from notex.core import pdfannot, pdfforms
from notex.core import pdfredact as R
from notex.core.pdfpages import PdfEditError
from pdfhelp import contains_anywhere, make_form_pdf, make_pdf, reader, texts

logging.getLogger("pypdf").setLevel(logging.ERROR)

SECRET = "GEHEIMNAME"


def _image(w_pt=595, h_pt=842, w=12, h=17, kind="rgb"):
    data = bytes([255] * (w * h * 3)) if kind == "rgb" else b"\xff\xd8 fake jpeg \xff\xd9"
    return R.PageImage(w_pt, h_pt, w, h, data, kind)


def _secret_doc():
    data = make_pdf(["Oeffentlich", f"Herr {SECRET} wohnt hier", "Ende"], title=f"Akte {SECRET}")
    data = pdfannot.add_note(data, 1, 10, 10, f"Notiz zu {SECRET}")
    writer = PdfWriter(clone_from=reader(data))
    root = writer._root_object
    struct = DictionaryObject({NameObject("/Type"): NameObject("/StructTreeRoot"),
                               NameObject("/Alt"): TextStringObject(f"Alternativtext {SECRET}")})
    root[NameObject("/StructTreeRoot")] = writer._add_object(struct)
    xmp = DecodedStreamObject()
    xmp.set_data(f"<x:xmpmeta><dc:title>{SECRET}</dc:title></x:xmpmeta>".encode())
    xmp[NameObject("/Type")] = NameObject("/Metadata")
    root[NameObject("/Metadata")] = writer._add_object(xmp)
    writer.add_attachment("geheim.txt", SECRET.encode())
    action = writer._add_object(DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                                                  NameObject("/JS"): TextStringObject("app.alert('hi');")}))
    root["/Names"].get_object()[NameObject("/JavaScript")] = DictionaryObject(
        {NameObject("/Names"): ArrayObject([TextStringObject("start"), action])})
    root[NameObject("/OpenAction")] = action
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_redaction_removes_secret_everywhere():
    data = _secret_doc()
    assert contains_anywhere(data, SECRET)
    out = R.redact_pages(data, {1: _image()}, R.RedactOptions(strip_outline=True))
    assert texts(out) == ["Oeffentlich", "", "Ende"]
    assert not contains_anywhere(out, SECRET)
    pdf = reader(out)
    root = pdf.trailer["/Root"]
    for key in ("/StructTreeRoot", "/Metadata", "/Outlines", "/OpenAction"):
        assert key not in root
    assert "/EmbeddedFiles" not in root.get("/Names", {}) and "/JavaScript" not in root.get("/Names", {})
    assert "/Annots" not in pdf.pages[1]
    page = pdf.pages[1]
    assert [float(v) for v in page.mediabox] == [0, 0, 595, 842] and page.rotation == 0
    assert R.leftovers(out, [SECRET.lower()]) == []


def test_options_keep_what_was_asked():
    data = _secret_doc()
    out = R.redact_pages(data, {1: _image()}, R.RedactOptions(strip_metadata=False, strip_attachments=False))
    found = R.leftovers(out, [SECRET])
    assert any("Lesezeichen" in f for f in found) and any("Metadaten" in f for f in found)
    assert not any("Seite 2" in f for f in found)
    assert "/EmbeddedFiles" in reader(out).trailer["/Root"]["/Names"]


def test_leftovers_lists_places():
    found = R.leftovers(_secret_doc(), ["geheimname", "", "  "])
    assert "„geheimname“ auf Seite 2" in found
    assert any("Anmerkung" in f for f in found) and any("Metadaten" in f for f in found)
    assert R.leftovers(_secret_doc(), []) == []


def test_form_fields_on_redacted_page_disappear():
    form = pdfforms.fill(make_form_pdf(), {"Ort": "Geheimstadt"})
    out = R.redact_pages(form, {0: _image()})
    assert pdfforms.list_fields(out) == []
    assert "/AcroForm" not in reader(out).trailer["/Root"]
    assert not contains_anywhere(out, "Geheimstadt")


def test_rotated_page_becomes_upright_image():
    from notex.core import pdfpages
    data = pdfpages.rotate(make_pdf(["Quer"]), [0], 90)
    out = R.redact_pages(data, {0: _image(842, 595)})
    page = reader(out).pages[0]
    assert page.rotation == 0 and [float(v) for v in page.mediabox] == [0, 0, 842, 595]


def test_image_kinds_and_validation():
    out = R.redact_pages(make_pdf(["A"]), {0: _image(kind="jpeg")})
    image = reader(out).pages[0]["/Resources"]["/XObject"]["/Im0"].get_object()
    assert image["/Filter"] == "/DCTDecode"
    with pytest.raises(PdfEditError, match="falsche Größe"):
        R.redact_pages(make_pdf(["A"]), {0: R.PageImage(595, 842, 10, 10, b"zu kurz")})
    with pytest.raises(PdfEditError):
        R.redact_pages(make_pdf(["A"]), {})
    with pytest.raises(PdfEditError):
        R.redact_pages(make_pdf(["A"]), {3: _image()})


def test_normalize_boxes():
    boxes = R.normalize_boxes([(0, (10, 10, 20, 20)), (0, (30, 40, 25, 35)), (1, (5, 5, 5.2, 9)), (2, (0, 0, 4, 4))])
    assert boxes[0] == [(9, 9, 21, 21), (24, 34, 31, 41)]                 # vertauschte Ecken, Rand 1 pt
    assert 1 not in boxes and boxes[2] == [(-1, -1, 5, 5)]


def test_other_pages_keep_text_and_annotations():
    data = pdfannot.add_note(make_pdf(["Eins", "Zwei"]), 0, 10, 10, "bleibt")
    out = R.redact_pages(data, {1: _image()})
    assert texts(out)[0] == "Eins"
    assert pdfannot.list_annotations(out, 0)[0].contents == "bleibt"
