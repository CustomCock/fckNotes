"""PDF-Seiten organisieren (notex/core/pdfpages.py) – ohne Qt."""
import logging

import pytest

from notex.core import pdfpages
from notex.core.pdfpages import PdfEditError
from pdfhelp import contains_anywhere, make_pdf, reader, rotations, texts

logging.getLogger("pypdf").setLevel(logging.ERROR)

DOC = make_pdf(["Eins", "Zwei", "Drei", "Vier"])


def test_rearrange_reorders_and_keeps_outline_and_metadata():
    out = pdfpages.rearrange(DOC, [3, 0, 2, 1])
    assert texts(out) == ["Vier", "Eins", "Drei", "Zwei"]
    pdf = reader(out)
    assert {o.title for o in pdf.outline} == {"Eins", "Zwei", "Drei", "Vier"}
    assert pdf.metadata.title == "Test"


def test_rotate_adds_to_existing_rotation():
    out = pdfpages.rotate(DOC, [0, 2], 90)
    assert rotations(out) == [90, 0, 90, 0]
    out = pdfpages.rotate(out, [0], -90)
    assert rotations(out) == [0, 0, 90, 0]
    assert rotations(pdfpages.rotate(out, [2], 270)) == [0, 0, 0, 0]
    with pytest.raises(PdfEditError):
        pdfpages.rotate(DOC, [0], 45)


def test_delete_removes_content_from_file():
    secret = make_pdf(["Oeffentlich", "GEHEIMNIS", "Ende"])
    out = pdfpages.delete(secret, [1])
    assert texts(out) == ["Oeffentlich", "Ende"]
    assert contains_anywhere(secret, "GEHEIMNIS")
    assert not contains_anywhere(out, "GEHEIMNIS")          # auch nicht unsichtbar (Lesezeichen, Objekte)


def test_delete_all_pages_is_refused():
    with pytest.raises(PdfEditError, match="Mindestens"):
        pdfpages.delete(DOC, [0, 1, 2, 3])


def test_move_and_moved_order():
    assert pdfpages.moved_order(5, [3, 4], 0) == [3, 4, 0, 1, 2]
    assert pdfpages.moved_order(5, [0], 5) == [1, 2, 3, 4, 0]
    assert pdfpages.moved_order(5, [1, 3], 3) == [0, 2, 1, 3, 4]
    assert texts(pdfpages.move(DOC, [3], 1)) == ["Eins", "Vier", "Zwei", "Drei"]


def test_extract_insert_merge_split():
    assert texts(pdfpages.extract(DOC, [2, 0])) == ["Drei", "Eins"]
    other = make_pdf(["X", "Y"])
    assert texts(pdfpages.insert(DOC, other, 1)) == ["Eins", "X", "Y", "Zwei", "Drei", "Vier"]
    assert texts(pdfpages.insert(DOC, other, 0))[:2] == ["X", "Y"]
    assert texts(pdfpages.insert(DOC, other, 99))[-2:] == ["X", "Y"]
    merged = pdfpages.merge([DOC, other])
    assert texts(merged) == ["Eins", "Zwei", "Drei", "Vier", "X", "Y"]
    parts = pdfpages.split(DOC, [[0, 1], [2], [3]])
    assert [texts(p) for p in parts] == [["Eins", "Zwei"], ["Drei"], ["Vier"]]


def test_invalid_input_gives_friendly_errors():
    with pytest.raises(PdfEditError, match="Kein gültiges PDF"):
        pdfpages.page_count(b"kein pdf")
    with pytest.raises(PdfEditError, match="gibt es nicht"):
        pdfpages.delete(DOC, [9])
    with pytest.raises(PdfEditError, match="nur einmal"):
        pdfpages.rearrange(DOC, [0, 0, 1])
    with pytest.raises(PdfEditError):
        pdfpages.merge([])


def test_encrypted_pdf_is_refused():
    from pypdf import PdfWriter
    import io
    writer = PdfWriter(clone_from=reader(DOC))
    writer.encrypt("pw", algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(PdfEditError, match="Passwort"):
        pdfpages.rotate(buffer.getvalue(), [0], 90)


@pytest.mark.parametrize("text, expected", [
    ("1-3, 5", [0, 1, 2, 4]),
    ("8-", [7, 8, 9]),
    ("-2", [0, 1]),
    ("3-1", [2, 1, 0]),
    ("1 - 2 4", [0, 1, 3]),
    ("2, 2, 1–2", [1, 0]),
    ("", list(range(10))),
])
def test_parse_ranges(text, expected):
    assert pdfpages.parse_ranges(text, 10) == expected


@pytest.mark.parametrize("text", ["0", "11", "a", "1-x", "-"])
def test_parse_ranges_errors(text):
    with pytest.raises(PdfEditError):
        pdfpages.parse_ranges(text, 10)


def test_groups_every_names_describe():
    assert pdfpages.parse_groups("1-3; 4-5", 5) == [[0, 1, 2], [3, 4]]
    with pytest.raises(PdfEditError):
        pdfpages.parse_groups(" ; ", 5)
    assert pdfpages.every(5, 2) == [[0, 1], [2, 3], [4]]
    assert pdfpages.every(3, 1) == [[0], [1], [2]]
    assert pdfpages.part_names("bericht", 12)[:2] == ["bericht_teil01.pdf", "bericht_teil02.pdf"]
    assert pdfpages.describe([0, 1, 2, 4, 6, 7]) == "1–3, 5, 7–8"
