"""PDF-Formulare, Unterschrift und Einbrennen (notex/core/pdfforms.py) – ohne Qt."""
import logging

import pytest

from notex.core import pdfannot, pdfforms as F, pdfpages
from notex.core.pdfpages import PdfEditError
from pdfhelp import contains_anywhere, make_form_pdf, make_pdf, reader

logging.getLogger("pypdf").setLevel(logging.ERROR)

FORM = make_form_pdf()
BLANK = make_pdf(["Leer"], outline=False)


def _fields(data):
    return {f.name: f for f in F.list_fields(data)}


def _widget(data, name):
    for ref in reader(data).pages[0].get("/Annots", []):
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Widget" and F._qualified(F._field_of(obj)) == name:
            return obj
    raise AssertionError(name)


def test_list_foreign_form():
    fields = _fields(FORM)
    assert fields["Ort"].kind == "text" and fields["Ort"].value == "Berlin"
    assert fields["Zustimmung"].kind == "checkbox" and fields["Zustimmung"].on_state == "Ja"
    assert not fields["Zustimmung"].checked
    assert fields["Farbe"].kind == "radio" and fields["Farbe"].options == ["A", "B"] and fields["Farbe"].value == "A"
    assert len(fields["Farbe"].widgets) == 2
    assert fields["Land"].options == ["Deutschland", "Österreich"]
    assert fields["Aktenzeichen"].read_only
    assert fields["Ort"].rect == pytest.approx((72, 842 - 720, 272, 842 - 700))


def test_fill_all_kinds():
    out = F.fill(FORM, {"Ort": "Köln (Dom)", "Zustimmung": True, "Farbe": "B", "Land": "Österreich"})
    fields = _fields(out)
    assert fields["Ort"].value == "Köln (Dom)"
    assert fields["Zustimmung"].checked and fields["Zustimmung"].value == "Ja"
    assert fields["Farbe"].value == "B"
    assert fields["Land"].value == "Österreich"
    ap = _widget(out, "Ort")["/AP"]["/N"].get_object().get_data()
    assert b"K\\366ln \\(Dom\\)" in ap                                    # WinAnsi, Klammern escaped
    kids = [r.get_object() for r in reader(out).pages[0]["/Annots"]][2:4]
    assert [str(k["/AS"]) for k in kids] == ["/Off", "/B"]
    assert _widget(out, "Zustimmung")["/AS"] == "/Ja"
    acro = reader(out).trailer["/Root"]["/AcroForm"]
    assert "/NeedAppearances" not in acro and "/Helv" in acro["/DR"]["/Font"]
    unchecked = F.fill(out, {"Zustimmung": False, "Farbe": ""})
    assert not _fields(unchecked)["Zustimmung"].checked and _fields(unchecked)["Farbe"].value == ""


def test_fill_errors():
    with pytest.raises(PdfEditError, match="schreibgeschützt"):
        F.fill(FORM, {"Aktenzeichen": "x"})
    with pytest.raises(PdfEditError, match="gibt es nicht"):
        F.fill(FORM, {"Fehlt": "x"})
    with pytest.raises(PdfEditError, match="keine Option"):
        F.fill(FORM, {"Farbe": "Z"})
    with pytest.raises(PdfEditError, match="keine Auswahl"):
        F.fill(FORM, {"Land": "Mars"})


def test_create_text_field_and_checkbox():
    out = F.add_text_field(BLANK, 0, (72, 100, 300, 122), "Name", "Ada")
    out = F.add_text_field(out, 0, (72, 140, 300, 200), "Notiz", multiline=True)
    out = F.add_checkbox(out, 0, (72, 220, 72, 220), "OK", checked=True)
    fields = _fields(out)
    assert fields["Name"].value == "Ada" and not fields["Name"].multiline
    assert fields["Notiz"].multiline
    assert fields["OK"].checked and fields["OK"].rect[2] - fields["OK"].rect[0] == pytest.approx(14)
    acro = reader(out).trailer["/Root"]["/AcroForm"]
    assert len(acro["/Fields"]) == 3
    with pytest.raises(PdfEditError, match="gibt es schon"):
        F.add_text_field(out, 0, (72, 300, 300, 322), "Name")
    with pytest.raises(PdfEditError):
        F.add_text_field(out, 0, (72, 300, 300, 322), "a.b")
    with pytest.raises(PdfEditError, match="zu klein"):
        F.add_text_field(out, 0, (72, 300, 74, 302), "Mini")
    filled = F.fill(out, {"Notiz": "Zeile eins und eine lange Fortsetzung, die umbrechen muss"})
    assert _widget(filled, "Notiz")["/AP"]["/N"].get_object().get_data().count(b"Tj") >= 2


def test_field_on_rotated_page_is_upright():
    rotated = pdfpages.rotate(BLANK, [0], 90)
    out = F.add_text_field(rotated, 0, (50, 50, 300, 75), "Quer", "x")
    widget = reader(out).pages[0]["/Annots"][0].get_object()
    assert widget["/MK"]["/R"] == 90
    assert [float(v) for v in widget["/AP"]["/N"].get_object()["/Matrix"]] == [0, 1, -1, 0, 0, 0]
    assert _fields(out)["Quer"].rect == pytest.approx((50, 50, 300, 75))


def test_remove_field():
    out = F.remove_field(FORM, "Farbe")
    assert "Farbe" not in _fields(out)
    assert len(reader(out).trailer["/Root"]["/AcroForm"]["/Fields"]) == 4
    with pytest.raises(PdfEditError):
        F.remove_field(out, "Farbe")


def test_signature_strokes_and_image():
    out = F.add_strokes(BLANK, 0, (72, 300, 232, 360), [[(0, 0.5), (0.5, 0.1), (1, 0.9)], [(0.2, 0.2)]])
    stamp = reader(out).pages[0]["/Annots"][0].get_object()
    assert stamp["/Subtype"] == "/Stamp" and stamp["/Contents"] == "Unterschrift"
    assert stamp["/AP"]["/N"].get_object().get_data().count(b" S") == 1          # Einzelpunkt ignoriert
    with pytest.raises(PdfEditError):
        F.add_strokes(BLANK, 0, (0, 0, 10, 10), [[(0, 0)]])
    rgb = bytes([10, 20, 30] * 6)
    out = F.add_image(BLANK, 0, (72, 400, 132, 420), 3, 2, rgb, bytes([0, 255, 255, 255, 255, 0]))
    form = reader(out).pages[0]["/Annots"][0].get_object()["/AP"]["/N"].get_object()
    image = form["/Resources"]["/XObject"]["/Im0"].get_object()
    assert image["/Width"] == 3 and image.get_data() == rgb and "/SMask" in image
    with pytest.raises(PdfEditError):
        F.add_image(BLANK, 0, (0, 0, 10, 10), 3, 2, b"zu kurz")


def test_fit_rect():
    assert F.fit_rect((10, 10, 10, 10), 2.0) == (10, 10, 170, 90)                  # Klick → Standardbreite
    assert F.fit_rect((0, 0, 400, 100), 2.0) == (0, 0, 200, 100)                  # Höhe begrenzt
    assert F.fit_rect((0, 0, 100, 400), 2.0) == (0, 0, 100, 50)                   # Breite begrenzt


def test_display_copy_turns_widgets_into_stamps():
    assert F.display_copy(BLANK) is None
    copy = F.display_copy(FORM)
    pdf = reader(copy)
    subtypes = {str(r.get_object()["/Subtype"]) for r in pdf.pages[0]["/Annots"]}
    assert subtypes == {"/Stamp"} and "/AcroForm" not in pdf.trailer["/Root"]
    ort = pdf.pages[0]["/Annots"][0].get_object()
    assert b"Berlin" in ort["/AP"]["/N"].get_object().get_data()                  # fehlendes Bild nachgezeichnet
    assert F.has_widgets(FORM) and not F.has_widgets(copy)


def test_flatten_burns_in_and_removes_fields():
    out = pdfannot.add_note(F.fill(FORM, {"Ort": "Hamburg"}), 0, 300, 50, "Notiz")
    flat = F.flatten(out)
    pdf = reader(flat)
    assert "/Annots" not in pdf.pages[0] and "/AcroForm" not in pdf.trailer["/Root"]
    assert F.list_fields(flat) == [] and pdfannot.list_annotations(flat, 0) == []
    resources = pdf.pages[0]["/Resources"]["/XObject"]
    assert len(resources) >= 4
    contents = b"".join(c.get_object().get_data() for c in pdf.pages[0]["/Contents"])
    assert contents.startswith(b"q\n") and b"/FxA0 Do" in contents
    assert contains_anywhere(flat, "Hamburg")                                      # Wert jetzt im Seiteninhalt
    untouched = F.flatten(FORM)                                     # „Ort“ hatte kein Bild: wird erst gezeichnet
    assert contains_anywhere(untouched, "Berlin") and b"(Berlin) Tj" in b"".join(
        x.get_object().get_data() for x in reader(untouched).pages[0]["/Resources"]["/XObject"].values())


def test_flatten_keeps_links():
    from pypdf import PdfWriter
    from pypdf.annotations import Link
    import io
    writer = PdfWriter(clone_from=reader(pdfannot.add_note(BLANK, 0, 10, 10, "n")))
    writer.add_annotation(0, Link(rect=(50, 50, 90, 60), url="https://example.org"))
    buffer = io.BytesIO()
    writer.write(buffer)
    flat = F.flatten(buffer.getvalue())
    kinds = [r.get_object()["/Subtype"] for r in reader(flat).pages[0]["/Annots"]]
    assert kinds == ["/Link"]
