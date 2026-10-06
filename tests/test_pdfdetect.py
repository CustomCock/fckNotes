"""Formularfelder automatisch erkennen (notex/core/pdfdetect.py) und gesammelt anlegen – ohne Qt."""
import io
import logging

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject, ArrayObject, FloatObject

from notex.core import pdfdetect as D, pdfforms as F
from pdfhelp import make_pdf, make_worksheet_pdf, reader

logging.getLogger("pypdf").setLevel(logging.ERROR)
PAGE = (595.0, 842.0)


def W(text, x0, y0, x1, y1):
    return D.Word(text, (x0, y0, x1, y1))


def _names(found):
    return {s.name: s for s in found}


def test_label_with_line_box_space_and_line_above():
    words = [W("Name:", 72, 100, 105, 112), W("Klasse:", 330, 100, 370, 112), W("Datum:", 72, 130, 108, 142),
             W("Thema:", 72, 160, 110, 172), W("Bruchrechnung", 400, 160, 480, 172),
             W("Unterschrift", 72, 712, 140, 724)]
    graphics = D.Graphics(hlines=((110, 300, 111.5), (372, 520, 111.5), (72, 250, 710)),
                          boxes=((112, 128, 300, 146),))
    found = _names(D.detect(words, graphics, PAGE))
    assert set(found) == {"Name", "Klasse", "Datum", "Thema", "Unterschrift"}
    assert found["Name"].rect[0] == pytest.approx(110) and found["Name"].rect[2] == 300
    assert found["Name"].rect[3] == pytest.approx(110.7) and found["Name"].reasons == ["Linie rechts"]
    assert found["Datum"].rect == pytest.approx((113.5, 129.5, 298.5, 144.5))
    assert found["Thema"].rect[2] == pytest.approx(394)                    # bis vor das nächste Wort
    assert found["Unterschrift"].kind == "signature" and found["Unterschrift"].reasons == ["Linie darüber"]
    assert found["Unterschrift"].rect[3] < 710


def test_underscore_words_count_as_lines():
    words = [W("Vorname:", 72, 100, 120, 112), W("_______________", 124, 111, 300, 111)]
    found = D.detect(words, D.Graphics(), PAGE)
    assert [s.name for s in found] == ["Vorname"] and found[0].rect[2] == 300


def test_running_text_is_not_a_form():
    words = [W("Der", 72, 100, 90, 112), W("Name", 93, 100, 120, 112), W("des", 123, 100, 140, 112),
             W("Autors", 143, 100, 180, 112), W("ist", 183, 100, 195, 112)]
    assert D.detect(words, D.Graphics(), PAGE) == []
    short = [W("Name:", 72, 100, 105, 112), W("Ada", 110, 100, 130, 112)]       # schon ausgefüllt, kein Platz
    assert D.detect(short, D.Graphics(), PAGE) == []


def test_generic_colon_label_and_multiword_keyword():
    words = [W("Projektnummer:", 72, 100, 150, 112), W("Ort,", 72, 130, 92, 142), W("Datum", 95, 130, 125, 142)]
    found = _names(D.detect(words, D.Graphics(hlines=((72, 260, 128),)), PAGE))
    assert "Projektnummer" in found and found["Projektnummer"].reasons == ["Leerraum rechts"]
    assert "Ort, Datum" in found and found["Ort, Datum"].reasons == ["Linie darüber"]


def test_checkboxes_from_squares_and_glyphs():
    words = [W("Ich", 90, 300, 100, 312), W("stimme", 102, 300, 140, 312), W("zu", 142, 300, 155, 312),
             W("☐", 72, 330, 82, 342), W("Ja", 86, 330, 96, 342), W("☐", 140, 330, 150, 342), W("Nein", 154, 330, 180, 342)]
    found = D.detect(words, D.Graphics(squares=((72, 300, 82, 310),)), PAGE)
    assert [(s.name, s.kind) for s in found] == [("Ich stimme zu", "checkbox"), ("Ja", "checkbox"),
                                                ("Nein", "checkbox")]
    alone = D.detect([], D.Graphics(squares=((72, 300, 82, 310), (72, 320, 82, 330))), PAGE)
    assert [s.name for s in alone] == ["Kästchen 1", "Kästchen 2"]


def test_existing_fields_and_duplicates():
    words = [W("Name:", 72, 100, 105, 112), W("Name:", 72, 200, 105, 212)]
    graphics = D.Graphics(hlines=((110, 300, 111), (110, 300, 211)))
    found = D.detect(words, graphics, PAGE, existing=[(110, 96, 300, 110)])
    assert len(found) == 1 and found[0].rect[1] > 190
    both = D.detect(words, graphics, PAGE)
    assert [s.name for s in both] == ["Name", "Name 2"]
    assert [s.name for s in D.unique_against(both, {"Name"})] == ["Name 2", "Name 3"]


def test_page_graphics_reads_lines_boxes_squares():
    graphics = D.page_graphics(make_worksheet_pdf(), 0)
    assert graphics.hlines == ((372.0, 520.0, 121.0), (72.0, 260.0, 747.0))
    assert graphics.boxes == ((112.0, 136.0, 312.0, 156.0),)
    assert graphics.squares == ((72.0, 291.0, 82.0, 301.0),)


def test_page_graphics_follows_form_xobjects_and_cm():
    writer = PdfWriter(clone_from=reader(make_pdf(["X"], outline=False)))
    form = DecodedStreamObject()
    form.set_data(b"0 0 m 100 0 l S")
    form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                 NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (0, 0, 200, 10)]),
                 NameObject("/Matrix"): ArrayObject([NumberObject(v) for v in (2, 0, 0, 1, 0, 0)])})
    page = writer.pages[0]
    page["/Resources"][NameObject("/XObject")] = DictionaryObject({NameObject("/L1"): writer._add_object(form)})
    content = DecodedStreamObject()
    content.set_data(b"q 1 0 0 1 50 700 cm /L1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert D.page_graphics(buffer.getvalue(), 0).hlines == ((50.0, 250.0, 142.0),)


def test_add_fields_in_one_step_with_placeholder_and_no_border():
    specs = [{"page": 0, "rect": (100, 100, 300, 116), "name": "Name", "kind": "text", "border": False},
             {"page": 0, "rect": (100, 700, 300, 730), "name": "Unterschrift", "kind": "signature", "border": False},
             {"page": 0, "rect": (72, 300, 82, 310), "name": "OK", "kind": "checkbox"}]
    out = F.add_fields(make_pdf(["X"], outline=False), specs)
    fields = {f.name: f for f in F.list_fields(out)}
    assert set(fields) == {"Name", "Unterschrift", "OK"}
    assert fields["Unterschrift"].placeholder == "signature" and fields["Name"].placeholder == ""
    assert fields["OK"].kind == "checkbox"
    name_widget = reader(out).pages[0]["/Annots"][0].get_object()
    assert "/BC" not in name_widget["/MK"]
    assert b" re S" not in name_widget["/AP"]["/N"].get_object().get_data()
    with pytest.raises(Exception):
        F.add_fields(out, [{"page": 0, "rect": (100, 400, 300, 416), "name": "Name"}])
