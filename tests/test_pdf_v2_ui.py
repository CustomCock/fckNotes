"""V2 in der Oberfläche: Link-Werkzeug, Links bearbeiten/folgen, Anhänge-Liste (speichern, öffnen, hinzufügen,
entfernen) – Webadressen und Programme werden nie geöffnet."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from notex.core import pdfattach, pdflinks, pdfoutline  # noqa: E402
from notex.core.pdflinks import LinkTarget  # noqa: E402
from notex.core.pdfoutline import Bookmark  # noqa: E402
from notex.core.pdfpages import PdfEditError  # noqa: E402
from notex.platform import external  # noqa: E402
from pdffixtures import assert_valid_pdf  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "skript.pdf"
    path.write_bytes(make_pdf(["Eins", "Zwei", "Drei"], size=(600, 800), outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    return page


@pytest.fixture
def no_open(monkeypatch):
    """Nichts darf wirklich ein System-Programm starten; Aufrufe werden mitgeschrieben."""
    calls = []
    monkeypatch.setattr(external, "available", lambda: external.Availability(True))
    monkeypatch.setattr(external, "open_path", lambda path: calls.append(path) or True)
    return calls


def _accept(monkeypatch, cls, setup):
    monkeypatch.setattr(cls, "exec", lambda self: (setup(self), QDialog.DialogCode.Accepted)[-1])


def test_link_tool_with_dialog_and_undo(tab, monkeypatch):
    from notex.ui.pdf_links_ui import LinkDialog
    _accept(monkeypatch, LinkDialog, lambda d: (d.page.setValue(3), d.border.setChecked(True)))
    tab.set_tool("link")
    tab.canvas.region_chosen.emit(0, QRectF(60, 80, 200, 30))
    (link,) = pdflinks.list_links(tab.data, 0)
    assert link.target == LinkTarget("page", 2) and link.border
    assert link.rect == pytest.approx((60, 80, 260, 110))
    assert tab.canvas.tool == "link"                                    # Werkzeug bleibt für weitere Links
    tab.undo()
    assert pdflinks.list_links(tab.data, 0) == []


def test_link_dialog_validates_url_and_offers_bookmarks(tab):
    from notex.ui.pdf_links_ui import LinkDialog
    marks = [Bookmark("Kapitel", 1, 500.0, children=[Bookmark("Unterpunkt", 2)]), Bookmark("Web", None)]
    dialog = LinkDialog(tab, 3, marks)
    assert [dialog.kind.itemData(i) for i in range(dialog.kind.count())] == ["page", "bookmark", "url", "file"]
    assert dialog.bookmark.count() == 2                                 # nur Lesezeichen mit Seite
    dialog.kind.setCurrentIndex(1)
    dialog.bookmark.setCurrentIndex(0)
    assert dialog.target() == LinkTarget("page", 1, 500.0)
    dialog.kind.setCurrentIndex(2)
    dialog.url.setText("javascript:alert(1)")
    with pytest.raises(PdfEditError):
        dialog.values()
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "http" in dialog.error.text()
    dialog.url.setText("example.org")
    assert dialog.values() == (LinkTarget("url", url="https://example.org"), False)


def test_edit_link_by_double_click_keeps_rect(tab, monkeypatch):
    from notex.ui.pdf_links_ui import LinkDialog
    assert tab.add_link(0, (50, 50, 200, 80), LinkTarget("page", 1), False)
    (obj,) = [o for o in tab.objects.objects(0) if o.kind == "link"]
    seen = {}

    def edit(dialog):
        seen["page"] = dialog.page.value()
        dialog.kind.setCurrentIndex(dialog.kind.findData("url"))
        dialog.url.setText("https://example.org/neu")
    _accept(monkeypatch, LinkDialog, edit)
    assert tab.objects.double_click(0, 100, 60)
    (link,) = pdflinks.list_links(tab.data, 0)
    assert seen["page"] == 2 and link.target.url == "https://example.org/neu"
    assert link.rect == pytest.approx((50, 50, 200, 80))


def test_links_move_like_objects(tab):
    tab.add_link(0, (50, 50, 200, 80), LinkTarget("page", 2), True)
    obj = next(o for o in tab.objects.objects(0) if o.kind == "link")
    assert tab.set_object_rect(obj, (70, 90, 220, 120))
    assert pdflinks.list_links(tab.data, 0)[0].rect == pytest.approx((70, 90, 220, 120))
    assert pdflinks.list_links(tab.data, 0)[0].target.page == 2


def test_read_mode_click_follows_page_link_and_never_opens_urls(tab, no_open):
    tab.add_link(0, (50, 50, 200, 80), LinkTarget("page", 2), False)
    tab.add_link(0, (50, 150, 200, 180), LinkTarget("url", url="https://example.org"), False)
    tab.set_editing(False)
    notices = []
    tab.notice.connect(notices.append)
    assert tab.objects.press(0, 100, 60, QPointF(0, 0))
    assert tab.objects.release(0, 100, 60)
    QApplication.processEvents()
    assert tab.canvas.current_page == 2
    assert tab.objects.press(0, 100, 160, QPointF(0, 0)) and tab.objects.release(0, 100, 160)
    QApplication.processEvents()
    assert any("example.org" in n and "nicht geöffnet" in n for n in notices)
    assert no_open == [] and not tab.editing                           # Klick schaltet nicht in den Bearbeiten-Modus
    obj = next(o for o in tab.objects.objects(0) if "example.org" in o.label)
    tab.copy_link_target(tab.link_target(obj))
    assert QApplication.clipboard().text() == "https://example.org"


def test_attachments_add_save_open_remove(tab, tmp_path, no_open):
    source = tmp_path / "messwerte.csv"
    source.write_bytes(b"a;b\n1;2\n")
    dialog = tab.show_attachments()
    assert dialog.table.rowCount() == 0 and not dialog.empty.isHidden()
    assert tab.add_attachment(source, "Rohdaten")
    assert [a.name for a in dialog.items] == ["messwerte.csv"] and dialog.open_button.isEnabled()
    key = dialog.items[0].key
    saved = tab.save_attachment(key, tmp_path / "kopie.csv")
    assert saved.read_bytes() == b"a;b\n1;2\n"
    assert tab.open_attachment(key, confirmed=True)
    (opened,) = no_open
    assert opened.read_bytes() == b"a;b\n1;2\n" and opened.parent.parent.name == "anhaenge"
    assert not tab.open_attachment(key, confirmed=False) and len(no_open) == 1
    assert tab.remove_attachment(key, confirmed=True)
    assert dialog.items == [] and pdfattach.list_attachments(tab.data) == []
    tab.undo()
    assert [a.name for a in dialog.items] == ["messwerte.csv"]
    assert_valid_pdf(tab.data)


def test_programs_are_never_opened(tab, tmp_path, no_open, monkeypatch):
    from notex.ui import dialogs
    warned = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warned.append(a))
    program = tmp_path / "rechnung.pdf.exe"
    program.write_bytes(b"MZ\x90\x00programm")
    tab.add_attachment(program)
    dialog = tab.show_attachments()
    assert dialog.items[0].risky and not dialog.open_button.isEnabled()
    assert not tab.open_attachment(dialog.items[0].key, confirmed=True)
    assert warned and no_open == []
    disguised = tmp_path / "foto.jpg"
    disguised.write_bytes(b"\x7fELF\x02\x01")
    tab.add_attachment(disguised)
    key = next(a.key for a in tab.attachments() if a.name == "foto.jpg")
    assert not tab.open_attachment(key, confirmed=True) and no_open == []


def test_palette_entries(win):
    for key in ("pdf:tool_link", "pdf:attachments", "pdf:attachment_add"):
        assert win.registry.get(key) is not None


def test_outline_targets_feed_link_dialog(tab):
    tab.set_outline([Bookmark("Kapitel 2", 1, 400.0)])
    marks = tab._outline_for_links()
    assert marks[0].title == "Kapitel 2" and pdfoutline.read_outline(tab.data)[0].page == 1


def test_without_system_opener_open_is_greyed_out(tab, tmp_path, monkeypatch):
    monkeypatch.setattr(external, "available", lambda: external.Availability(False, "xdg-open fehlt"))
    monkeypatch.setattr(external, "open_path", lambda path: pytest.fail("darf nicht öffnen"))
    source = tmp_path / "notiz.txt"
    source.write_text("hallo")
    tab.add_attachment(source)
    dialog = tab.show_attachments()
    assert not dialog.open_button.isEnabled() and dialog.save_button.isEnabled()
    assert dialog.open_button.toolTip() == "xdg-open fehlt"
    assert not tab.open_attachment(dialog.items[0].key, confirmed=True)
