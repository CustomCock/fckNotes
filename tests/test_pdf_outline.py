"""V1 (A2): Lesezeichen lesen, schreiben (verschachtelt, Position, Stil, fremde Aktionen) und aus Überschriften."""
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

from notex.core import pdfoutline as O, pdfpages
from notex.core.pdfoutline import Bookmark
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def _doc(pages=3):
    return make_pdf([f"Seite {i + 1}" for i in range(pages)], outline=False)


def test_write_and_read_nested_outline():
    tree = [Bookmark("Kapitel 1", 0, children=[Bookmark("Abschnitt 1.1", 1, top=500.0, bold=True),
                                               Bookmark("Abschnitt 1.2", 1, top=300.0)], open=True),
            Bookmark("Kapitel 2", 2, color=(1.0, 0.0, 0.0), italic=True)]
    out = O.write_outline(_doc(), tree)
    assert_valid_pdf(out)
    back = O.read_outline(out)
    assert [b.title for b in back] == ["Kapitel 1", "Kapitel 2"]
    assert [c.title for c in back[0].children] == ["Abschnitt 1.1", "Abschnitt 1.2"]
    first = back[0].children[0]
    assert first.page == 1 and first.top == pytest.approx(500) and first.bold
    assert back[1].color == (1.0, 0.0, 0.0) and back[1].italic and back[0].open
    assert PdfReader(io.BytesIO(out)).outline[0].title == "Kapitel 1"           # pypdf liest es auch


def test_replace_and_clear_outline():
    out = O.write_outline(_doc(), [Bookmark("Alt", 0)])
    out = O.write_outline(out, [Bookmark("Neu", 2)])
    assert [b.title for b in O.read_outline(out)] == ["Neu"]
    empty = O.write_outline(out, [])
    assert O.read_outline(empty) == []
    with pytest.raises(PdfEditError):
        O.write_outline(_doc(), [Bookmark("Zu weit", 9)])
    with pytest.raises(PdfEditError):
        O.write_outline(_doc(), [Bookmark("   ", 0)])


def test_foreign_action_is_kept_unchanged():
    writer = PdfWriter(clone_from=io.BytesIO(_doc(1)))
    ref = writer.add_outline_item("Webseite", 0)
    item = ref.get_object()
    if "/Dest" in item:
        del item["/Dest"]
    item[NameObject("/A")] = DictionaryObject({NameObject("/S"): NameObject("/URI"),
                                               NameObject("/URI"): TextStringObject("https://example.org")})
    buffer = io.BytesIO()
    writer.write(buffer)
    marks = O.read_outline(buffer.getvalue())
    assert marks[0].page is None and marks[0].action is not None
    marks.append(Bookmark("Anfang", 0))
    out = O.write_outline(buffer.getvalue(), marks)
    again = O.read_outline(out)
    assert again[0].page is None
    assert again[0].action.get_object()["/URI"] == "https://example.org"


def test_existing_outline_from_pages_module_survives_reorder():
    doc = make_pdf(["A", "B", "C"])                        # pdfhelp legt Lesezeichen je Seite an
    assert [b.title for b in O.read_outline(doc)] == ["A", "B", "C"]
    moved = pdfpages.move(doc, [2], 0)
    assert {b.title: b.page for b in O.read_outline(moved)} == {"C": 0, "A": 1, "B": 2}


def _heading_pdf() -> bytes:
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    pages = [[(24, "Einleitung"), (10, "Fliesstext " * 8), (10, "noch mehr Text " * 6), (16, "Ziel der Arbeit"),
              (10, "Text " * 20)],
             [(24, "Methoden"), (10, "Beschreibung " * 9), (16, "Aufbau"), (10, "Text " * 30), (10, "12")]]
    for lines in pages:
        page = writer.add_blank_page(595, 842)
        y, ops = 780, []
        for size, text in lines:
            ops.append(f"BT /F1 {size} Tf 72 {y} Td ({text.strip()}) Tj ET")
            y -= size * 2
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_headings_become_nested_proposals():
    found = O.find_headings(_heading_pdf())
    assert [(h.text, h.level, h.page) for h in found] == [("Einleitung", 1, 0), ("Ziel der Arbeit", 2, 0),
                                                         ("Methoden", 1, 1), ("Aufbau", 2, 1)]
    tree = O.headings_to_outline(found)
    assert [b.title for b in tree] == ["Einleitung", "Methoden"]
    assert [c.title for c in tree[1].children] == ["Aufbau"]
    assert tree[0].top > 780                                                   # springt an die Überschrift
    out = O.write_outline(_heading_pdf(), tree)
    assert [b.title for b in O.read_outline(out)] == ["Einleitung", "Methoden"]


def test_headings_progress_can_cancel():
    from notex.core.tasks import Cancelled, TaskContext
    ctx = TaskContext()
    ctx.cancel()
    with pytest.raises(Cancelled):
        O.find_headings(_heading_pdf(), progress=lambda i, n: ctx.step(i, n))
