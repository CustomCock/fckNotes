"""V1 in der Oberfläche: leere Seite, ersetzen, zuschneiden (Werkzeug + Dialog), Seitenbeschriftungen, Lesezeichen."""
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from notex.core import pdfoutline, pdfpages  # noqa: E402
from notex.core.pdfpages import LabelRange  # noqa: E402
from pdffixtures import assert_valid_pdf  # noqa: E402
from pdfhelp import make_pdf, texts  # noqa: E402
from test_pdf_outline import _heading_pdf  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "skript.pdf"
    path.write_bytes(make_pdf(["Eins", "Zwei", "Drei"], size=(600, 800), outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    return page


def test_blank_page_and_undo(tab):
    assert tab.insert_blank(1, "A4")
    assert texts(tab.data) == ["Eins", "", "Zwei", "Drei"] and tab.doc.pageCount() == 4
    tab.undo()
    assert tab.doc.pageCount() == 3


def test_blank_page_dialog_values(tab, monkeypatch):
    from notex.ui.pdf_pages_ui import BlankPageDialog
    monkeypatch.setattr(BlankPageDialog, "exec", lambda self: (self.position.setCurrentIndex(3),
                                                               self.size.setCurrentIndex(2),
                                                               QDialog.DialogCode.Accepted)[-1])
    assert tab.insert_blank()
    last = PdfReader(io.BytesIO(tab.data)).pages[-1]
    assert texts(tab.data)[-1] == "" and float(last.mediabox[2]) == 612            # Letter, am Ende


def test_replace_pages(tab, tmp_path):
    other = tmp_path / "neu.pdf"
    other.write_bytes(make_pdf(["X", "Y"], outline=False))
    assert tab.replace_pages(1, 2, other)
    assert texts(tab.data) == ["Eins", "X", "Y"]
    assert_valid_pdf(tab.data)


def test_replace_dialog_validates(tab, tmp_path):
    from notex.ui.pdf_pages_ui import ReplaceDialog
    dialog = ReplaceDialog(tab, 3, 0, 1, tmp_path)
    with pytest.raises(pdfpages.PdfEditError):
        dialog.values()
    other = tmp_path / "x.pdf"
    other.write_bytes(make_pdf(["X", "Y", "Z"], outline=False))
    assert dialog.choose(str(other))
    dialog.other_pages.setText("2-3")
    assert dialog.values() == (0, 1, other, [1, 2])


def test_crop_tool_opens_dialog_with_rect_margins(tab, monkeypatch):
    from notex.ui.pdf_pages_ui import CropDialog
    seen = {}

    def fake_exec(dialog):
        seen["mm"] = [box.value() for box in dialog.boxes]
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CropDialog, "exec", fake_exec)
    tab.set_tool("crop")
    tab.canvas.region_chosen.emit(0, QRectF(60, 80, 480, 640))            # 60..540 × 80..720
    page = PdfReader(io.BytesIO(tab.data)).pages[0]
    assert [round(float(v)) for v in page.cropbox] == [60, 80, 540, 720]
    assert seen["mm"] == pytest.approx([21.2, 28.2, 21.2, 28.2], abs=0.1)
    assert tab.canvas.tool == "select"
    assert "Eins" in texts(tab.data)[0]                                        # kein Schwärzen


def test_crop_dialog_scope_reset_and_detect(tab):
    assert tab.crop_pages(pages=[0, 2], margins=(10, 10, 10, 10))
    reader = PdfReader(io.BytesIO(tab.data))
    assert [float(v) for v in reader.pages[1].cropbox] == [0, 0, 600, 800]
    assert tab.crop_pages(pages=[0], reset=True)
    assert [float(v) for v in PdfReader(io.BytesIO(tab.data)).pages[0].cropbox] == [0, 0, 600, 800]
    from notex.ui.pdf_pages_ui import CropDialog
    dialog = CropDialog(tab, 3, 0, detect=tab.detect_margins)
    dialog.detect_white()
    assert dialog.boxes[0].value() > 20 and dialog.boxes[2].value() > 100      # Text nur oben links
    dialog.scope.setCurrentIndex(4)
    pages, margins = dialog.values()
    assert pages == [0, 1, 2] and len(margins) == 4


def test_page_labels_dialog_and_apply(tab):
    from notex.ui.pdf_pages_ui import LabelsDialog
    dialog = LabelsDialog(tab, 3, [LabelRange(0, "r"), LabelRange(1, "D", "K-", 1)])
    assert "i, K-1, K-2" in dialog.preview.text()
    assert tab.edit_page_labels(dialog.ranges())
    assert tab.page_label(0) == "i" and tab.page_label(2) == "K-2"
    assert tab.rotate_pages(90, [0])
    assert tab.page_label(1) == "K-1"                                           # bleibt nach dem Drehen
    assert tab.edit_page_labels([])
    assert tab.page_label(0) == "1"


def test_bookmarks_add_edit_and_jump(tab):
    tab.canvas.go_to(1)
    assert tab.add_bookmark("Zweite Seite")
    items = pdfoutline.read_outline(tab.data)
    assert [b.title for b in items] == ["Zweite Seite"] and items[0].page == 1
    assert not tab.outline_editor.isHidden() and tab.outline_editor.count() == 1
    assert tab.add_bookmark("Unterpunkt", page=2, top=400.0)
    editor = tab.outline_editor
    second = editor.tree.topLevelItem(1)
    editor.tree.setCurrentItem(second)
    editor.indent()                                                             # unter „Zweite Seite“
    tree = pdfoutline.read_outline(tab.data)
    assert [c.title for c in tree[0].children] == ["Unterpunkt"]
    child = editor.tree.topLevelItem(0).child(0)
    child.setText(0, "Umbenannt")
    assert pdfoutline.read_outline(tab.data)[0].children[0].title == "Umbenannt"
    editor.tree.setCurrentItem(editor.tree.topLevelItem(0).child(0))
    editor.delete_selected()
    assert pdfoutline.read_outline(tab.data)[0].children == []
    tab.undo()
    assert pdfoutline.read_outline(tab.data)[0].children[0].title == "Umbenannt"
    tab.go_to_pdf_point(2, 400.0)
    assert tab.canvas.current_page == 2


def test_bookmarks_from_headings(win, tmp_path):
    path = win.root / "arbeit.pdf"
    path.write_bytes(_heading_pdf())
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    assert page.bookmarks_from_headings(choose=lambda headings: (pdfoutline.headings_to_outline(headings), "replace"))
    tree = pdfoutline.read_outline(page.data)
    assert [b.title for b in tree] == ["Einleitung", "Methoden"]
    assert [c.title for c in tree[0].children] == ["Ziel der Arbeit"]


def test_palette_entries(win):
    for key in ("pdf:insert_blank", "pdf:replace_pages", "pdf:crop", "pdf:tool_crop", "pdf:page_labels",
                "pdf:bookmark_add", "pdf:bookmarks_edit", "pdf:bookmarks_headings"):
        assert win.registry.get(key) is not None, key
