"""V4 in der Oberfläche: Kommentar-Seitenleiste (Filter, Sortierung, Sprung), Antworten, Status, Löschen,
Zusammenfassung (Markdown, CSV, PDF) und XFDF."""
import csv
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from notex.core import pdfannot, pdfcomments  # noqa: E402
from pdffixtures import assert_valid_pdf  # noqa: E402
from pdfhelp import make_pdf, texts  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "aufsatz.pdf"
    data = make_pdf(["Die Biene sammelt Nektar", "Seite zwei"], size=(600, 800), outline=False)
    data = pdfannot.add_markup(data, 0, "highlight", [(73, 82, 351, 101)], contents="Wichtig")
    data = pdfannot.add_note(data, 1, 100, 100, "Bitte prüfen")
    path.write_bytes(data)
    page = win.tabs.open_viewer(path, "pdf")
    page.author = "Mia"
    return page


def test_panel_lists_filters_and_jumps(tab):
    tab.set_comments_visible(True)
    panel = tab.comments_panel
    assert not panel.isHidden() and tab.comments_button.isChecked()
    assert panel.tree.topLevelItemCount() == 2 and panel.count.text() == "2 Kommentare"
    assert panel.tree.topLevelItem(0).text(0) == "Markierung · S. 1 · offen\nWichtig"
    panel.kind.setCurrentIndex(panel.kind.findData("note"))
    assert panel.tree.topLevelItemCount() == 1 and panel.count.text() == "1 von 2 Kommentaren"
    panel.kind.setCurrentIndex(0)
    panel.search.setText("biene")                                  # sucht im markierten Text
    assert panel.tree.topLevelItemCount() == 1
    panel.search.clear()
    panel.sort.setCurrentIndex(panel.sort.findData("page"))
    panel.descending.setChecked(True)
    assert panel.tree.topLevelItem(0).data(0, 256).kind == "note"
    panel._clicked(panel.tree.topLevelItem(0))
    QApplication.processEvents()
    assert tab.canvas.current_page == 1


def test_reply_status_delete_with_undo(tab):
    assert tab.reply_comment(0, 0, "Sehe ich auch so")
    assert tab.set_comment_status(0, 0, "accepted")
    tab.set_comments_visible(True)
    panel = tab.comments_panel
    top = panel.tree.topLevelItem(0)
    assert top.childCount() == 1 and top.child(0).text(0).startswith("Mia · ") and "angenommen" in top.text(0)
    panel.status.setCurrentIndex(panel.status.findData("accepted"))
    assert panel.tree.topLevelItemCount() == 1
    panel.status.setCurrentIndex(0)
    assert tab.delete_comment(0, 0, confirmed=True)
    assert [c.kind for c in pdfcomments.list_comments(tab.data)] == ["note"]
    tab.undo()
    comment = pdfcomments.list_comments(tab.data)[0]
    assert comment.status == "accepted" and comment.replies[0].contents == "Sehe ich auch so"


def test_reply_dialog_and_panel_signals(tab, monkeypatch):
    from notex.ui.pdf_edit import TextDialog
    monkeypatch.setattr(TextDialog, "exec", lambda self: (self.edit.setPlainText("Per Dialog"),
                                                          QDialog.DialogCode.Accepted)[-1])
    tab.set_comments_visible(True)
    panel = tab.comments_panel
    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
    panel._reply()
    assert pdfcomments.list_comments(tab.data)[1].replies[0].contents == "Per Dialog"
    panel.status_requested.emit(1, 0, "done")
    assert pdfcomments.list_comments(tab.data)[1].status == "done"


def test_exports(tab, tmp_path):
    tab.reply_comment(0, 0, "=Antwort")
    md = tab.export_comments("md", tmp_path / "k.md")
    assert md.read_text(encoding="utf-8").startswith("# Kommentare – aufsatz.pdf")
    csv_path = tab.export_comments("csv", tmp_path / "k.csv")
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                         # BOM für Excel
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    assert rows[2][6] == "'=Antwort"
    pdf = tab.export_comments("pdf", tmp_path / "k.pdf")
    assert "Wichtig" in " ".join(texts(pdf.read_bytes()))
    x = tab.export_comments("xfdf", tmp_path / "k.xfdf")
    assert b"<xfdf" in x.read_bytes()
    assert tab.export_comments("pdf", tab.path) is None             # nie das PDF selbst überschreiben


def test_xfdf_import_roundtrip_into_clean_copy(win, tab, tmp_path):
    exported = tab.export_comments("xfdf", tmp_path / "k.xfdf")
    clean = win.root / "sauber.pdf"
    clean.write_bytes(make_pdf(["Die Biene sammelt Nektar", "Seite zwei"], size=(600, 800), outline=False))
    other = win.tabs.open_viewer(clean, "pdf")
    notices = []
    other.notice.connect(notices.append)
    assert other.import_xfdf(exported)
    assert [c.contents for c in pdfcomments.list_comments(other.data)] == ["Wichtig", "Bitte prüfen"]
    assert notices and notices[-1].startswith("2 Kommentare übernommen")
    assert other.import_xfdf(exported) and notices[-1].startswith("0 Kommentare übernommen")   # keine Doppelten
    assert_valid_pdf(other.data)


def test_bad_xfdf_shows_warning_not_crash(tab, tmp_path, monkeypatch):
    from notex.ui import dialogs
    warned = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warned.append(a))
    bad = tmp_path / "boese.xfdf"
    bad.write_bytes(b'<!DOCTYPE x [<!ENTITY a "a">]><xfdf/>')
    assert not tab.import_xfdf(bad) and warned


def test_palette_entries(win):
    for key in ("pdf:comments", "pdf:comments_md", "pdf:comments_csv", "pdf:comments_pdf", "pdf:xfdf_export",
                "pdf:xfdf_import"):
        assert win.registry.get(key) is not None, key


def test_palette_toggle_does_not_switch_to_edit_mode(win, tab):
    win.tabs.setCurrentWidget(tab) if hasattr(win.tabs, "setCurrentWidget") else None
    tab.set_comments_visible()
    assert not tab.comments_panel.isHidden() and not tab.editing
    tab.set_comments_visible()
    assert tab.comments_panel.isHidden()
