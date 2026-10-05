"""PDF-Anmerkungen (notex/core/pdfannot.py) – Geometrie, Erscheinungsbilder, Auflisten/Ändern/Löschen, ohne Qt."""
import logging

import pytest

from notex.core import pdfannot as A
from notex.core import pdfpages
from notex.core.pdfpages import PdfEditError
from pdfhelp import contains_anywhere, make_pdf, reader

logging.getLogger("pypdf").setLevel(logging.ERROR)

DOC = make_pdf(["Hallo Welt", "Zwei"])


# ---- Geometrie ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_geometry_roundtrip(rotate):
    geom = A.PageGeom((10.0, 20.0, 610.0, 820.0), rotate)
    for x, y in ((0, 0), (12.5, 40), (300, 10), (geom.size[0], geom.size[1])):
        px, py = geom.to_pdf(x, y)
        assert geom.to_view(px, py) == pytest.approx((x, y))


def test_geometry_known_points():
    g0 = A.PageGeom((0, 0, 600, 800), 0)
    assert g0.to_pdf(0, 0) == (0, 800)                       # oben links → oben links in PDF (y oben)
    assert g0.size == (600, 800)
    g90 = A.PageGeom((0, 0, 600, 800), 90)
    assert g90.size == (800, 600)
    assert g90.to_pdf(0, 0) == (0, 0)                        # 90° im Uhrzeigersinn: unten links wird oben links
    assert g90.to_pdf(800, 0) == (0, 800)
    g180 = A.PageGeom((0, 0, 600, 800), 180)
    assert g180.to_pdf(0, 0) == (600, 0)
    g270 = A.PageGeom((0, 0, 600, 800), 270)
    assert g270.to_pdf(0, 0) == (600, 800)
    assert A.PageGeom((5, 5, 105, 205), 0).rect_to_pdf((0, 0, 10, 20)) == [5, 185, 15, 205]   # CropBox-Versatz
    assert g90.matrix() == [0, 1, -1, 0, 0, 0]


def test_page_geometry_reads_rotation():
    rotated = pdfpages.rotate(DOC, [1], 90)
    assert A.page_geometry(rotated, 1).rotate == 90 and A.page_geometry(rotated, 1).size == (842, 595)


# ---- Text --------------------------------------------------------------------------------------------------------
def test_text_width_and_wrap():
    assert A.text_width("WWW", 10) > A.text_width("iii", 10)
    lines = A.wrap("ein zwei drei vier fünf sechs", 12, 60)
    assert len(lines) > 1 and all(A.text_width(line, 12) <= 60 for line in lines)
    assert A.wrap("Absatz eins\nAbsatz zwei", 12, 500) == ["Absatz eins", "Absatz zwei"]
    assert all(A.text_width(line, 12) <= 30 for line in A.wrap("Donaudampfschifffahrt", 12, 30))


def test_pdf_string_escapes_and_encodes():
    assert A.pdf_string("a(b)c\\") == "(a\\(b\\)c\\\\)"
    assert A.pdf_string("ä") == "(\\344)"
    assert A.pdf_string("😀") == "(?)"


# ---- Anmerkungen anlegen -----------------------------------------------------------------------------------------
def _annots(data, page=0):
    return [ref.get_object() for ref in reader(data).pages[page].get("/Annots", [])]


def test_markup_has_quadpoints_and_appearance():
    out = A.add_markup(DOC, 0, "highlight", [(72, 80, 200, 110), (72, 112, 150, 130)], "#00ff00", "wichtig")
    annot = _annots(out)[0]
    assert annot["/Subtype"] == "/Highlight"
    assert len(annot["/QuadPoints"]) == 16
    assert annot["/Contents"] == "wichtig"
    assert str(annot["/NM"]).startswith(A.ID_PREFIX)
    ap = annot["/AP"]["/N"].get_object()
    assert b" f" in ap.get_data() and ap["/Resources"]["/ExtGState"]["/GS0"]["/BM"] == "/Multiply"
    x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
    assert x0 <= 72 and x1 >= 200 and y0 <= 842 - 130 and y1 >= 842 - 80     # Ansicht oben → PDF unten gespiegelt


def test_underline_and_strikeout_draw_lines():
    for kind in ("underline", "strikeout"):
        out = A.add_markup(DOC, 0, kind, [(72, 80, 200, 110)])
        data = _annots(out)[0]["/AP"]["/N"].get_object().get_data()
        assert b" S" in data and b" re f" not in data


def test_markup_rejects_empty_and_unknown():
    with pytest.raises(PdfEditError):
        A.add_markup(DOC, 0, "highlight", [])
    with pytest.raises(PdfEditError):
        A.add_markup(DOC, 0, "squiggle", [(0, 0, 10, 10)])
    with pytest.raises(PdfEditError):
        A.add_markup(DOC, 5, "highlight", [(0, 0, 10, 10)])


def test_note_and_text():
    out = A.add_note(DOC, 0, 300, 50, "Notiz (mit Klammern)")
    note = _annots(out)[0]
    assert note["/Subtype"] == "/Text" and note["/Contents"] == "Notiz (mit Klammern)"
    out = A.add_text(out, 0, (72, 300, 300, 310), "Zeile eins und noch viel mehr Text zum Umbrechen", 14, "#d03030")
    text = _annots(out)[1]
    assert text["/Subtype"] == "/FreeText"
    assert text["/DA"].startswith("/Helv 14 Tf")
    ap = text["/AP"]["/N"].get_object()
    assert "/Helv" in ap["/Resources"]["/Font"]
    content = ap.get_data()
    assert b"BT /Helv 14 Tf" in content and content.count(b"Tj") >= 2      # umbrochen
    with pytest.raises(PdfEditError):
        A.add_text(DOC, 0, (0, 0, 100, 10), "   ")
    with pytest.raises(PdfEditError):
        A.add_note(DOC, 0, 0, 0, "")


def test_text_on_rotated_page_gets_rotation_matrix():
    rotated = pdfpages.rotate(DOC, [0], 90)
    out = A.add_text(rotated, 0, (50, 50, 250, 60), "Quer", 20)
    ap = _annots(out)[0]["/AP"]["/N"].get_object()
    assert [float(v) for v in ap["/Matrix"]] == [0, 1, -1, 0, 0, 0]
    info = A.list_annotations(out, 0)[0]
    assert info.rect[0] == pytest.approx(50) and info.rect[1] == pytest.approx(50)


# ---- Auflisten, ändern, löschen ----------------------------------------------------------------------------------
def test_list_hit_update_delete():
    out = A.add_markup(DOC, 0, "highlight", [(72, 80, 200, 110)])
    out = A.add_note(out, 0, 300, 50, "Erste Notiz GEHEIMWORT")
    out = A.add_text(out, 0, (72, 300, 300, 310), "Text", 12)
    infos = A.list_annotations(out, 0)
    assert [i.kind for i in infos] == ["highlight", "note", "text"]
    assert all(i.ours for i in infos)
    assert A.hit(infos, 305, 55).kind == "note"
    assert A.hit(infos, 100, 90).kind == "highlight"
    assert A.hit(infos, 500, 700) is None
    changed = A.update_text(out, 0, 1, "Geänderte Notiz")
    assert A.list_annotations(changed, 0)[1].contents == "Geänderte Notiz"
    retext = A.update_text(out, 0, 2, "Neuer längerer Text auf der Seite")
    assert A.list_annotations(retext, 0)[2].contents == "Neuer längerer Text auf der Seite"
    assert b"Neuer" in _annots(retext)[2]["/AP"]["/N"].get_object().get_data()
    gone = A.delete_annotation(out, 0, 1)
    assert [i.kind for i in A.list_annotations(gone, 0)] == ["highlight", "text"]
    assert contains_anywhere(out, "GEHEIMWORT") and not contains_anywhere(gone, "GEHEIMWORT")
    with pytest.raises(PdfEditError):
        A.delete_annotation(gone, 0, 9)


def test_foreign_annotations_listed_links_skipped():
    from pypdf import PdfWriter
    from pypdf.annotations import Link, Text
    import io
    writer = PdfWriter(clone_from=reader(DOC))
    writer.add_annotation(0, Text(rect=(10, 10, 30, 30), text="fremd"))
    writer.add_annotation(0, Link(rect=(50, 50, 90, 60), url="https://example.org"))
    buffer = io.BytesIO()
    writer.write(buffer)
    infos = A.list_annotations(buffer.getvalue(), 0)
    assert [(i.kind, i.ours, i.contents) for i in infos] == [("note", False, "fremd")]


def test_annotations_survive_page_operations():
    out = A.add_note(DOC, 1, 10, 10, "auf Seite zwei")
    moved = pdfpages.rearrange(out, [1, 0])
    assert A.list_annotations(moved, 0)[0].contents == "auf Seite zwei"
