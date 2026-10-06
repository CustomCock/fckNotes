"""Eingefügtes als Objekte (notex/core/pdfobjects.py, update_field, Textstil) – ohne Qt."""
import logging

import pytest

from notex.core import pdfannot as A, pdfforms as F, pdfobjects as O, pdfpages
from notex.core.pdfpages import PdfEditError
from pdfhelp import contains_anywhere, make_form_pdf, make_pdf, reader

logging.getLogger("pypdf").setLevel(logging.ERROR)


def _doc():
    d = make_pdf(["Seite"], outline=False)
    d = A.add_text(d, 0, (72, 300, 300, 300), "Hallo Text", 14, "#2f6fdf", True)        # 0
    d = A.add_note(d, 0, 400, 50, "Notiz")                                              # 1
    d = F.add_strokes(d, 0, (72, 400, 232, 460), [[(0, 0), (1, 1)]])                    # 2
    d = F.add_text_field(d, 0, (72, 500, 300, 522), "Name", "Ada")                      # 3
    d = F.add_checkbox(d, 0, (72, 540, 72, 540), "OK", checked=True)                    # 4
    d = A.add_markup(d, 0, "highlight", [(72, 80, 200, 110)])                           # 5
    return d


def _by_index(data, index):
    return next(o for o in O.list_objects(data, 0) if o.index == index)


def test_list_objects_kinds_and_flags():
    objs = O.list_objects(_doc(), 0)
    assert [(o.kind, o.field_kind) for o in objs] == [("text", ""), ("note", ""), ("signature", ""), ("field", "text"),
                                                      ("field", "checkbox"), ("highlight", "")]
    movable = [o.movable for o in objs]
    assert movable == [True, True, True, True, True, False]
    assert [o.keep_aspect for o in objs] == [False, False, True, False, True, False]
    assert objs[3].field == "Name" and objs[4].state == "Yes"
    assert all(o.ours for o in objs)


def test_hit_prefers_smaller_object():
    big = O.PdfObject(0, 0, "diagram", (0, 0, 300, 300), "Diagramm")
    small = O.PdfObject(0, 1, "field", (10, 10, 50, 30), "Feld", field="A", field_kind="text")
    assert O.hit([big, small], 20, 20) is small
    assert O.hit([big, small], 200, 200) is big
    assert O.hit([big, small], 400, 400) is None


def test_move_and_resize_each_kind():
    d = _doc()
    d = O.set_rect(d, 0, 0, (100, 320, 400, 330))                  # Text: neu umbrochen, Höhe passt sich an
    t = _by_index(d, 0)
    assert t.rect[:3] == pytest.approx((100, 320, 400)) and t.rect[3] > 330
    assert A.freetext_style(reader(d).pages[0]["/Annots"][0].get_object()) == (14.0, "#2f6fdf", True)
    d = O.set_rect(d, 0, 1, (450, 80, 520, 200))                   # Notiz: nur verschoben, Größe bleibt
    assert _by_index(d, 1).rect == pytest.approx((450, 80, 470, 100))
    d = O.set_rect(d, 0, 2, (72, 400, 392, 520))                   # Unterschrift: Rahmen neu
    assert _by_index(d, 2).rect == pytest.approx((72, 400, 392, 520))
    d = O.set_rect(d, 0, 3, (72, 600, 400, 640))                   # Textfeld: Bild neu gezeichnet, Wert bleibt
    widget = reader(d).pages[0]["/Annots"][3].get_object()
    assert [float(v) for v in widget["/AP"]["/N"].get_object()["/BBox"]] == [0, 0, 328, 40]
    assert {f.name: f.value for f in F.list_fields(d)}["Name"] == "Ada"
    d = O.set_rect(d, 0, 4, (72, 660, 100, 688))                   # Kästchen: beide Zustände neu
    states = reader(d).pages[0]["/Annots"][4].get_object()["/AP"]["/N"]
    assert set(states.keys()) == {"/Yes", "/Off"}
    assert [float(v) for v in states["/Yes"].get_object()["/BBox"]] == [0, 0, 28, 28]


def test_markups_and_tiny_rects_are_refused():
    d = _doc()
    with pytest.raises(PdfEditError, match="Markierungen"):
        O.set_rect(d, 0, 5, (0, 0, 100, 100))
    with pytest.raises(PdfEditError, match="klein"):
        O.set_rect(d, 0, 0, (0, 0, 2, 2))
    with pytest.raises(PdfEditError):
        O.set_rect(d, 0, 9, (0, 0, 100, 100))


def test_move_by_and_rotated_page():
    d = O.move_by(_doc(), 0, 2, 10, -5)
    assert _by_index(d, 2).rect == pytest.approx((82, 395, 242, 455))
    rotated = pdfpages.rotate(make_pdf(["Quer"], outline=False), [0], 90)
    rotated = A.add_text(rotated, 0, (50, 50, 250, 50), "Quer", 12)
    moved = O.set_rect(rotated, 0, 0, (300, 100, 500, 110))
    obj = _by_index(moved, 0)
    assert obj.rect[0] == pytest.approx(300) and obj.rect[1] == pytest.approx(100)


def test_delete_object_removes_whole_field_and_leaves_no_trace():
    d = _doc()
    gone = O.delete_object(d, 0, 3)
    assert "Name" not in {f.name for f in F.list_fields(gone)}
    assert not contains_anywhere(gone, "Ada")
    gone = O.delete_object(gone, 0, 0)
    assert "text" not in [o.kind for o in O.list_objects(gone, 0)]
    assert not contains_anywhere(gone, "Hallo Text")


def test_text_style_update():
    d = A.update_text(_doc(), 0, 0, "Neu", size=20, color="#d03030", border=False)
    annot = reader(d).pages[0]["/Annots"][0].get_object()
    assert A.freetext_style(annot) == (20.0, "#d03030", False) and annot["/Contents"] == "Neu"
    kept = A.update_text(_doc(), 0, 0, "Nur Text")
    assert A.freetext_style(reader(kept).pages[0]["/Annots"][0].get_object()) == (14.0, "#2f6fdf", True)
    with pytest.raises(PdfEditError):
        A.update_text(_doc(), 0, 0, "x", size=200)


def test_update_field_properties():
    d = F.update_field(_doc(), "Name", new_name="Vorname", multiline=True, size=9)
    info = {f.name: f for f in F.list_fields(d)}["Vorname"]
    assert info.multiline and info.value == "Ada"
    assert reader(d).pages[0]["/Annots"][3].get_object()["/DA"] == "/Helv 9 Tf 0 g"
    with pytest.raises(PdfEditError, match="gibt es schon"):
        F.update_field(d, "Vorname", new_name="OK")
    with pytest.raises(PdfEditError):
        F.update_field(d, "Vorname", new_name="a.b")
    with pytest.raises(PdfEditError):
        F.update_field(d, "Fehlt", new_name="x")
    foreign = F.update_field(make_form_pdf(), "Ort", size=14)
    assert {f.name: f.value for f in F.list_fields(foreign)}["Ort"] == "Berlin"


def test_fit_aspect_keeps_ratio_and_anchor():
    old = (0, 0, 200, 100)
    assert O.fit_aspect(old, (0, 0, 400, 400), "br") == (0, 0, 400, 200)
    assert O.fit_aspect(old, (-100, -50, 200, 100), "tl") == pytest.approx((-100, -50, 200, 100))
    x0, y0, x1, y1 = O.fit_aspect(old, (50, 0, 200, 300), "bl")
    assert (x1 - x0) / (y1 - y0) == pytest.approx(2.0) and x1 == 200
