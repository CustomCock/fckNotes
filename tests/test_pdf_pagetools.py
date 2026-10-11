"""V1 (A6): leere Seite, Seiten ersetzen, Zuschneiden, weiße Ränder, Seitenbeschriftungen – Qt-frei."""
import io

import pytest
from pypdf import PdfReader

from notex.core import pdfpages as P
from notex.core.pdfpages import LabelRange, PdfEditError
from pdffixtures import assert_valid_pdf, big
from pdfhelp import make_pdf, texts


def _labels(data):
    reader = PdfReader(io.BytesIO(data))
    return P.label_list(P.labels_of(reader), len(reader.pages))


def _labelled(texts_, ranges):
    return P.set_labels(make_pdf(texts_, outline=False), ranges)


# ---- leere Seite ---------------------------------------------------------------------------------------------
def test_insert_blank_sizes_and_position():
    doc = make_pdf(["A", "B"], size=(400, 300), outline=False)
    out = P.insert_blank(doc, 1)
    reader = PdfReader(io.BytesIO(out))
    assert texts(out) == ["A", "", "B"]
    assert [float(v) for v in reader.pages[1].mediabox[2:]] == [400, 300]       # wie die Nachbarseite
    a4 = PdfReader(io.BytesIO(P.insert_blank(doc, 0, "A4"))).pages[0]
    assert round(float(a4.mediabox[2])) == 595 and round(float(a4.mediabox[3])) == 842
    end = P.insert_blank(doc, 99, "Letter")
    assert texts(end)[-1] == "" and float(PdfReader(io.BytesIO(end)).pages[-1].mediabox[2]) == 612
    assert_valid_pdf(out)
    with pytest.raises(PdfEditError):
        P.insert_blank(doc, 0, "A9")


def test_insert_blank_neighbor_size_follows_rotation():
    rotated = P.rotate(make_pdf(["Quer"], size=(400, 300), outline=False), [0], 90)
    page = PdfReader(io.BytesIO(P.insert_blank(rotated, 1))).pages[1]
    assert [float(v) for v in page.mediabox[2:]] == [300, 400]                 # so groß, wie sie angezeigt wird


# ---- ersetzen ------------------------------------------------------------------------------------------------
def test_replace_pages_in_one_step():
    doc = make_pdf(["A", "B", "C", "D"], outline=False)
    other = make_pdf(["X", "Y", "Z"], outline=False)
    assert texts(P.replace_pages(doc, 1, 2, other)) == ["A", "X", "Y", "Z", "D"]
    assert texts(P.replace_pages(doc, 3, 3, other, [2])) == ["A", "B", "C", "Z"]
    assert texts(P.replace_pages(doc, 2, 0, other, [0])) == ["X", "D"]       # Reihenfolge egal
    with pytest.raises(PdfEditError):
        P.replace_pages(doc, 0, 9, other)
    with pytest.raises(PdfEditError):
        P.replace_pages(doc, 0, 0, other, [])


# ---- zuschneiden ---------------------------------------------------------------------------------------------
def test_crop_sets_only_cropbox_and_keeps_content():
    doc = make_pdf(["Inhalt bleibt", "Zwei"], size=(600, 800), outline=False)
    out = P.crop(doc, [0], margins=(50, 100, 50, 100))
    page = PdfReader(io.BytesIO(out)).pages[0]
    assert [float(v) for v in page.cropbox] == [50, 100, 550, 700]
    assert [float(v) for v in page.mediabox] == [0, 0, 600, 800]               # MediaBox unverändert
    assert "Inhalt bleibt" in texts(out)[0]                                   # kein Schwärzen: Text noch da
    assert [float(v) for v in PdfReader(io.BytesIO(out)).pages[1].cropbox] == [0, 0, 600, 800]
    assert_valid_pdf(out)
    back = P.reset_crop(out, [0])
    assert [float(v) for v in PdfReader(io.BytesIO(back)).pages[0].cropbox] == [0, 0, 600, 800]


def test_crop_rect_in_view_coordinates_on_rotated_page():
    rotated = P.rotate(make_pdf(["X"], size=(600, 800), outline=False), [0], 90)   # angezeigt 800 × 600
    out = P.crop(rotated, [0], rect=(100, 50, 700, 550))
    page = PdfReader(io.BytesIO(out)).pages[0]
    from notex.core.pdfannot import geometry_of
    assert geometry_of(page).size == pytest.approx((600, 500))
    with pytest.raises(PdfEditError, match="10 pt"):
        P.crop(rotated, [0], margins=(400, 0, 400, 0))


def test_pages_for_modes():
    assert P.pages_for("even", 5) == [1, 3] and P.pages_for("odd", 5) == [0, 2, 4]
    assert P.pages_for("all", 3) == [0, 1, 2] and P.pages_for("current", 3, 2) == [2]
    assert P.pages_for("range", 9, text="2-3, 7") == [1, 2, 6]


def test_detect_white_margins():
    doc = make_pdf(["Text oben links"], size=(600, 800), outline=False)    # Text bei x=72, y≈700 (pdf)
    left, top, right, bottom = P.detect_margins(doc, 0)
    assert 60 < left < 75 and 70 < top < 100                                  # Text beginnt bei 72 / Grundlinie 100
    assert right > 300 and bottom > 650
    blank = P.insert_blank(doc, 1)
    assert P.detect_margins(blank, 1) is None
    out = P.crop(doc, [0], margins=(left, top, right, bottom))
    assert "Text oben links" in texts(out)[0]


# ---- Seitenbeschriftungen ------------------------------------------------------------------------------------
def test_labels_set_read_and_format():
    doc = _labelled(list("abcdef"), [LabelRange(0, "r"), LabelRange(2, "D", "S-", 5), LabelRange(5, "A")])
    assert _labels(doc) == ["i", "ii", "S-5", "S-6", "S-7", "A"]
    assert P.read_labels(doc)[1] == LabelRange(2, "D", "S-", 5)
    assert P.format_label("a", "", 28) == "bb" and P.format_label("R", "Anh. ", 14) == "Anh. XIV"
    cleared = P.set_labels(doc, [])
    assert _labels(cleared) == ["1", "2", "3", "4", "5", "6"]
    assert_valid_pdf(doc)
    with pytest.raises(PdfEditError):
        P.set_labels(doc, [LabelRange(9)])
    with pytest.raises(PdfEditError):
        P.set_labels(doc, [LabelRange(0, "", "")])


def test_labels_survive_rotate_delete_insert():
    doc = _labelled(list("abcde"), [LabelRange(0, "r"), LabelRange(2, "D")])   # i ii 1 2 3
    assert _labels(P.rotate(doc, [0], 90)) == ["i", "ii", "1", "2", "3"]
    assert _labels(P.delete(doc, [2])) == ["i", "ii", "1", "2"]                  # Folgeseiten zählen weiter
    assert _labels(P.insert_blank(doc, 3)) == ["i", "ii", "1", "2", "3", "4"]
    assert _labels(P.move(doc, [0], 5)) == ["i", "1", "2", "3", "ii"]           # Bereich zählt in neuer Reihenfolge
    foreign = P.insert(doc, make_pdf(["X"], outline=False), 3)
    assert _labels(foreign) == ["i", "ii", "1", "1", "2", "3"]                  # fremde Seite eigene Zählung


def test_big_document_page_tools():
    data = big(520)
    out = P.insert_blank(data, 260)
    assert P.page_count(out) == 521
    cropped = P.crop(out, P.pages_for("odd", 521), margins=(10, 10, 10, 10))
    assert_valid_pdf(cropped)
