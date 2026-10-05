"""PDF bearbeiten in der Oberfläche: Bearbeiten-Modus, Seitenleiste, Rückgängig, Speichern, Aufteilen, Zusammenfügen."""
import pytest

pytest.importorskip("PySide6")

import uihelp  # noqa: E402
from pdfhelp import make_pdf, rotations, texts  # noqa: E402


@pytest.fixture
def pdf_tab(win):
    path = win.root / "bericht.pdf"
    path.write_bytes(make_pdf(["Eins", "Zwei", "Drei", "Vier"]))
    page = win.tabs.open_viewer(path, "pdf")
    assert page is not None and page.doc.pageCount() == 4
    return page


def test_edit_mode_shows_bar_and_pages(pdf_tab):
    assert pdf_tab.edit_bar.isHidden() and pdf_tab.strip.isHidden()
    pdf_tab.set_editing(True)
    assert not pdf_tab.edit_bar.isHidden() and not pdf_tab.strip.isHidden()
    assert pdf_tab.strip.count() == 4
    assert uihelp.wait_for(lambda: not pdf_tab.strip._timer.isActive())
    pdf_tab.set_editing(False)
    assert pdf_tab.edit_bar.isHidden()


def test_rotate_delete_undo_redo_and_dirty(win, pdf_tab):
    pdf_tab.set_editing(True)
    dirty = []
    pdf_tab.dirty_changed.connect(dirty.append)
    assert pdf_tab.rotate_pages(90, [0])
    assert rotations(pdf_tab.data) == [90, 0, 0, 0] and pdf_tab.is_dirty and dirty == [True]
    assert pdf_tab.delete_pages([1, 2])
    assert texts(pdf_tab.data) == ["Eins", "Vier"] and pdf_tab.doc.pageCount() == 2
    pdf_tab.undo()
    assert texts(pdf_tab.data) == ["Eins", "Zwei", "Drei", "Vier"]
    pdf_tab.undo()
    assert rotations(pdf_tab.data) == [0, 0, 0, 0]
    pdf_tab.redo()
    assert rotations(pdf_tab.data) == [90, 0, 0, 0]
    assert texts(pdf_tab.path.read_bytes()) == ["Eins", "Zwei", "Drei", "Vier"]   # Datei unberührt bis Ctrl+S
    assert "geändert" in " ".join(pdf_tab.status_parts())


def test_reorder_from_strip_order(pdf_tab):
    pdf_tab.set_editing(True)
    assert pdf_tab.reorder([3, 2, 1, 0])
    assert texts(pdf_tab.data) == ["Vier", "Drei", "Zwei", "Eins"]
    assert pdf_tab.move_pages([0], 4)
    assert texts(pdf_tab.data)[-1] == "Vier"


def test_save_overwrites_with_trash_backup_once(win, pdf_tab, monkeypatch):
    import send2trash
    trashed = []
    monkeypatch.setattr(send2trash, "send2trash", lambda p: trashed.append(p) or __import__("os").remove(p))
    pdf_tab.set_editing(True)
    pdf_tab.delete_pages([0])
    win.tabs.save_current()                                   # Ctrl+S geht an den PDF-Tab
    assert not pdf_tab.is_dirty
    assert texts(pdf_tab.path.read_bytes()) == ["Zwei", "Drei", "Vier"]
    assert len(trashed) == 1
    pdf_tab.delete_pages([0])
    assert pdf_tab.save()
    assert len(trashed) == 1                                  # nur einmal je Tab
    assert texts(pdf_tab.path.read_bytes()) == ["Drei", "Vier"]


def test_save_without_backup_setting(win, pdf_tab, monkeypatch):
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda p: pytest.fail("kein Papierkorb erwartet"))
    win.config["pdf_backup_trash"] = False
    win.tabs.apply_preview_settings()
    pdf_tab.set_editing(True)
    pdf_tab.rotate_pages(180, [0])
    assert pdf_tab.save() and rotations(pdf_tab.path.read_bytes())[0] == 180
    win.config["pdf_backup_trash"] = True
    win.tabs.apply_preview_settings()


def test_save_as_moves_tab_to_new_file(win, pdf_tab):
    pdf_tab.set_editing(True)
    pdf_tab.delete_pages([3])
    target = win.root / "kurz"
    assert pdf_tab.save_as(target)
    assert pdf_tab.path.name == "kurz.pdf" and texts(pdf_tab.path.read_bytes()) == ["Eins", "Zwei", "Drei"]
    assert texts((win.root / "bericht.pdf").read_bytes()) == ["Eins", "Zwei", "Drei", "Vier"]
    index = win.tabs.indexOf(pdf_tab)
    assert win.tabs.tabText(index) == "kurz.pdf"


def test_extract_insert_split(win, pdf_tab):
    pdf_tab.set_editing(True)
    out = pdf_tab.extract_pages([1, 2], win.root / "mitte.pdf")
    assert out is not None and texts(out.read_bytes()) == ["Zwei", "Drei"]
    extra = win.root / "extra.pdf"
    extra.write_bytes(make_pdf(["X"]))
    win.tabs.setCurrentWidget(pdf_tab)
    assert pdf_tab.insert_pdf(extra, 1)
    assert texts(pdf_tab.data) == ["Eins", "X", "Zwei", "Drei", "Vier"]
    folder = win.root / "teile"
    folder.mkdir()
    written = pdf_tab.split_pdf([[0, 1], [2, 3, 4]], folder)
    assert [p.name for p in written] == ["bericht_teil1.pdf", "bericht_teil2.pdf"]
    assert texts(written[1].read_bytes()) == ["Zwei", "Drei", "Vier"]


def test_close_dirty_tab_asks(win, pdf_tab, monkeypatch):
    pdf_tab.set_editing(True)
    pdf_tab.delete_pages([0])
    tabs = win.tabs.active if hasattr(win.tabs, "active") else win.tabs
    monkeypatch.setattr(tabs, "ask_save_viewer", lambda page: False)     # „Abbrechen“
    assert not tabs.close_tab(tabs.indexOf(pdf_tab))
    assert pdf_tab in tabs.viewers()
    assert not win.tabs.confirm_close_all()
    monkeypatch.setattr(tabs, "ask_save_viewer", lambda page: True)      # „Verwerfen“
    assert tabs.close_tab(tabs.indexOf(pdf_tab))


def test_external_change_does_not_drop_edits(win, pdf_tab):
    pdf_tab.set_editing(True)
    pdf_tab.delete_pages([0])
    pdf_tab.reload()
    assert texts(pdf_tab.data) == ["Zwei", "Drei", "Vier"] and pdf_tab.is_dirty


def test_encrypted_pdf_cannot_be_edited(win, pdf_tab):
    pdf_tab.encrypted = True
    pdf_tab.set_editing(True)
    assert pdf_tab.edit_bar.isHidden()
    assert not pdf_tab.rotate_pages(90, [0])


def test_merge_pdfs_command(win):
    a, b = win.root / "a.pdf", win.root / "b.pdf"
    a.write_bytes(make_pdf(["A1", "A2"]))
    b.write_bytes(make_pdf(["B1"]))
    target = win.merge_pdfs([b, a], win.root / "alle.pdf")
    assert target is not None and texts(target.read_bytes()) == ["B1", "A1", "A2"]
    assert win.tabs.current_viewer().path == target


def test_palette_commands_registered(win):
    for key in ("pdf:edit", "pdf:merge", "pdf:split", "pdf:rotate_right", "pdf:rotate_left", "pdf:delete_pages",
                "pdf:extract", "pdf:insert", "pdf:pages"):
        assert win.registry.get(key) is not None, key


def test_palette_rotate_switches_edit_mode_on(win, pdf_tab):
    win.registry.get("pdf:rotate_right").callback()
    assert pdf_tab.editing and rotations(pdf_tab.data)[0] == 90
