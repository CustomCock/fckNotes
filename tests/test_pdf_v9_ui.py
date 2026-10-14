"""V9 in der Oberfläche: Komprimieren (Dialog, Vorher/Nachher, Übernehmen, Rückgängig, Schwärz-Schutz),
Reparieren (beim Laden und per Befehl), Seiten als Bilder, Text/Markdown als Notiz, Bilder → PDF."""
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from notex.core import pdfconvert as C  # noqa: E402
from pdffixtures import assert_valid_pdf, broken, photo_doc, picture  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


def open_tab(win, name: str, data: bytes):
    path = win.root / name
    path.write_bytes(data)
    page = win.tabs.open_viewer(path, "pdf")
    page.resize(1000, 800)
    page.show()
    QApplication.processEvents()
    return page


@pytest.fixture
def tab(win):
    page = open_tab(win, "foto.pdf", photo_doc(2))
    page.set_editing(True)
    return page


def test_compress_accept_and_undo(tab):
    before = tab.data
    assert tab.compress(C.PROFILES["standard"], accept=True, wait=True)
    assert len(tab.data) < len(before) / 10 and tab.is_dirty
    assert_valid_pdf(tab.data)
    tab.undo()
    assert tab.data == before


def test_compress_declined_keeps_data(tab):
    before = tab.data
    assert not tab.compress(C.PROFILES["standard"], accept=False, wait=True)
    assert tab.data is before


def test_compress_not_smaller_changes_nothing(win):
    page = open_tab(win, "klein.pdf", make_pdf(["x"], outline=False, title=""))
    page.set_editing(True)
    small = C.compress(page.data, C.PROFILES["lossless"]).data
    page.apply(small)
    before = page.data
    assert not page.compress(C.PROFILES["lossless"], accept=True, wait=True)
    assert page.data is before


def test_compress_refuses_if_redacted_terms_would_return(tab, monkeypatch):
    from notex.core import pdfaudit
    from notex.ui import dialogs
    tab.redact_terms.append("geheim")
    monkeypatch.setattr(pdfaudit, "audit", lambda data, terms: ["Fund"])
    warned = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warned.append(a))
    before = tab.data
    assert not tab.compress(C.PROFILES["standard"], accept=True, wait=True)
    assert tab.data is before and warned


def test_compress_dialog_profiles_and_custom(tab):
    from notex.ui.pdf_convert_ui import CompressDialog
    dialog = CompressDialog(tab, len(tab.data))
    assert dialog.value() == C.PROFILES["standard"] and "Verlustbehaftet" in dialog.lossy.text()
    dialog.profile.setCurrentIndex(dialog.profile.findData("lossless"))
    assert not dialog.dpi.isEnabled() and "Verlustfrei" in dialog.lossy.text()
    dialog.profile.setCurrentIndex(dialog.profile.findData("custom"))
    dialog.dpi.setValue(20)
    dialog._accept()
    assert "dpi" in dialog.error.text()
    dialog.dpi.setValue(200)
    dialog.quality.setValue(70)
    assert (dialog.value().dpi, dialog.value().jpeg_quality) == (200, 70)


def test_compress_via_dialog(tab, monkeypatch):
    from notex.ui.pdf_convert_ui import CompressDialog
    monkeypatch.setattr(CompressDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    started = []
    monkeypatch.setattr(tab, "_task", lambda title, work, wait=False: started.append(title) or
                        type("W", (), {"done": type("S", (), {"connect": lambda self, f: f(work(None))})()})())
    before = len(tab.data)
    from notex.ui import dialogs
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    assert tab.compress()
    assert started == ["Komprimieren"] and len(tab.data) < before


def test_repair_on_load_and_command(win):
    page = open_tab(win, "kaputt.pdf", broken())
    QApplication.processEvents()
    assert page.data is not None and page.error == "" and page.repaired_on_load
    assert page.is_dirty                                               # repariert = ungespeicherte Änderung
    assert_valid_pdf(page.data)
    page.set_editing(True)
    assert page.repair_pdf()
    assert_valid_pdf(page.data)


def test_export_page_images(tab, win):
    folder = win.root / "bilder"
    folder.mkdir()
    paths = tab.export_page_images([0, 1], folder, dpi=72, fmt="jpg", wait=True)
    assert [p.name for p in paths] == ["foto-S1.jpg", "foto-S2.jpg"]


def test_export_page_images_dialog(tab, win, monkeypatch):
    from notex.ui.pdf_convert_ui import PageImagesDialog
    dialog = PageImagesDialog(tab, 2, win.root)
    dialog.pages.setText("5")
    dialog._accept()
    assert "nur 2 Seiten" in dialog.error.text()
    dialog.pages.setText("2")
    dialog.folder.setText(str(win.root / "fehlt"))
    dialog._accept()
    assert "Ordner" in dialog.error.text()
    dialog.folder.setText(str(win.root))
    assert dialog.values()["pages"] == [1]
    assert not dialog.quality.isEnabled()
    dialog.format.setCurrentIndex(1)
    assert dialog.quality.isEnabled()


def test_export_markdown_opens_as_note(win):
    from pdffixtures import article_doc
    page = open_tab(win, "artikel.pdf", article_doc())
    target = page.export_text("md", win.root / "artikel.md", wait=True)
    assert target.read_text(encoding="utf-8").count("# Jahresbericht") == 1
    QApplication.processEvents()
    assert win.tabs.current_editor() is not None and win.tabs.current_editor().path == target
    plain = page.export_text("txt", win.root / "artikel.txt", open_after=False, wait=True)
    assert "--- Seite 1 ---" in plain.read_text(encoding="utf-8")


def test_images_to_pdf_from_main_window(win):
    first, second = win.root / "a.png", win.root / "b.jpg"
    first.write_bytes(picture("PNG", (40, 20)))
    second.write_bytes(picture("JPEG", (20, 40)))
    target = win.images_to_pdf([first, second], win.root / "bilder.pdf", page_size="a4", wait=True)
    assert target is not None and len(PdfReader(io.BytesIO(target.read_bytes())).pages) == 2
    assert_valid_pdf(target.read_bytes())
    target.write_bytes(b"alt")                                          # vorhandene Datei: Papierkorb statt still
    import send2trash
    trashed = []
    send2trash_orig = send2trash.send2trash
    try:
        send2trash.send2trash = lambda path: trashed.append(path)
        assert win.images_to_pdf([first], target, wait=True) == target
    finally:
        send2trash.send2trash = send2trash_orig
    assert trashed


def test_images_to_pdf_dialog_order_and_options(win):
    from notex.ui.pdf_convert_ui import ImagesToPdfDialog
    dialog = ImagesToPdfDialog(win, [win.root / "1.png", win.root / "2.png"], win.root)
    dialog.list.setCurrentRow(1)
    dialog._shift(-1)
    assert [p.name for p in dialog.paths()] == ["2.png", "1.png"]
    dialog.size.setCurrentIndex(1)
    dialog.margin.setValue(12)
    assert dialog.options() == ("a4", 12)


def test_images_to_pdf_error_is_reported(win, monkeypatch):
    from notex.ui import dialogs
    bad = win.root / "kaputt.png"
    bad.write_bytes(b"nope")
    warned = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warned.append(a))
    assert win.images_to_pdf([bad], win.root / "x.pdf", wait=True) is None
    assert warned and not (win.root / "x.pdf").exists()


def test_palette_has_v9_commands(win):
    ids = {c.id for c in win.registry.all()}
    assert {"pdf:compress", "pdf:repair", "pdf:to_images", "pdf:to_text", "pdf:to_markdown",
            "pdf:images_to_pdf"} <= ids
