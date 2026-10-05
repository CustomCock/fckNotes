"""Schwärzen in der Oberfläche: vormerken (Bereich, Markierung, Suchtreffer), anwenden, prüfen, nur „Speichern unter“."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF, QSize  # noqa: E402
from notex.core import pdfredact  # noqa: E402
from pdfhelp import contains_anywhere, make_pdf, texts  # noqa: E402

SECRET = "MUSTERMANN"


@pytest.fixture
def secret_tab(win):
    path = win.root / "akte.pdf"
    path.write_bytes(make_pdf(["Deckblatt", f"Herr {SECRET} zahlt", f"Kopie {SECRET}"], title="Akte", outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    return page


def _render(page, index, scale=1.0):
    from PySide6.QtPdf import QPdfDocumentRenderOptions
    size = page.doc.pagePointSize(index)
    options = QPdfDocumentRenderOptions()
    options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
    return page.doc.render(index, QSize(round(size.width() * scale), round(size.height() * scale)), options)


def _search(page, term):
    page.search_field.setText(term)                                         # Treffer kommen erst nach und nach


def test_region_marks_pending_overlay(secret_tab):
    secret_tab.set_tool("redact")
    secret_tab.canvas.region_chosen.emit(1, QRectF(70, 60, 200, 40))
    assert secret_tab.redactions == [(1, (70.0, 60.0, 270.0, 100.0))]
    assert [o[2] for o in secret_tab.canvas.overlays] == ["redact"]
    assert not secret_tab.redact_apply.isHidden() and "(1)" in secret_tab.redact_apply.text()
    secret_tab.canvas.region_chosen.emit(1, QRectF(10, 10, 0, 0))            # Klick ohne Fläche: nichts
    assert len(secret_tab.redactions) == 1
    secret_tab.remove_redaction(0)
    assert secret_tab.redactions == [] and secret_tab.redact_apply.isHidden()
    assert not secret_tab.is_dirty                                          # Vormerken ändert die Datei nicht


def test_selection_and_search_are_marked_with_terms(secret_tab):
    secret_tab.canvas.selection = secret_tab.doc.getAllText(1)
    secret_tab.canvas.selection_page = 1
    assert secret_tab.mark_redaction_from_selection()
    assert secret_tab.redactions and secret_tab.redact_terms == [f"Herr {SECRET} zahlt"]
    secret_tab.clear_redactions()
    _search(secret_tab, SECRET)
    assert secret_tab.mark_search_results() == 2
    assert {p for p, _r in secret_tab.redactions} == {1, 2} and secret_tab.redact_terms == [SECRET]


def test_apply_removes_text_for_real(secret_tab):
    _search(secret_tab, SECRET)
    secret_tab.mark_search_results()
    boxes = list(secret_tab.redactions)
    assert secret_tab.apply_redactions(dpi=100, confirm=False)
    assert secret_tab.redacted and secret_tab.is_dirty and secret_tab.redactions == []
    assert not contains_anywhere(secret_tab.data, SECRET)
    assert texts(secret_tab.data)[0] == "Deckblatt"                         # nicht betroffene Seite unverändert
    assert secret_tab.doc.getAllText(1).text() == ""                        # Seite ist jetzt ein Bild
    assert secret_tab.last_leftovers == []
    page, (x0, y0, x1, y1) = boxes[0]
    image = _render(secret_tab, page)
    color = image.pixelColor(round((x0 + x1) / 2), round((y0 + y1) / 2))
    assert color.lightness() < 30                                           # schwarzer Balken
    dark_left = sum(1 for x in range(72, round(x0) - 4) for y in range(round(y0), round(y1))
                    if image.pixelColor(x, y).lightness() < 120)
    assert dark_left > 20                                                   # „Herr“ links daneben noch zu sehen
    assert secret_tab.canvas.current_highlight is None


def test_leftovers_are_reported(secret_tab):
    secret_tab.canvas.selection = secret_tab.doc.getAllText(1)
    secret_tab.canvas.selection_page = 1
    secret_tab.mark_redaction_from_selection()
    secret_tab.redact_terms = [SECRET]
    assert secret_tab.apply_redactions(dpi=72, confirm=False)
    assert secret_tab.last_leftovers == [f"„{SECRET}“ auf Seite 3"]          # Seite 3 nicht geschwärzt


def test_save_after_redaction_goes_to_new_file(win, secret_tab, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    secret_tab.mark_redaction(1, (70, 60, 300, 100))
    secret_tab.apply_redactions(dpi=72, confirm=False)
    asked = []

    def fake_dialog(parent, title, start, filters):
        asked.append(start)
        return str(win.root / "akte_geschwärzt.pdf"), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_dialog)
    win.tabs.save_current()
    assert asked and asked[0].endswith("akte_geschwärzt.pdf")
    assert secret_tab.path.name == "akte_geschwärzt.pdf" and not secret_tab.is_dirty and not secret_tab.redacted
    original = (win.root / "akte.pdf").read_bytes()
    assert contains_anywhere(original, SECRET)                               # Original unangetastet
    assert not contains_anywhere(secret_tab.path.read_bytes(), "zahlt")


def test_context_menu_and_palette(win, secret_tab):
    from PySide6.QtWidgets import QMenu
    secret_tab.mark_redaction(1, (70, 60, 300, 100))
    menu = QMenu()
    secret_tab._extend_menu(menu, 1, 100, 80)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Vorgemerkte Schwärzung entfernen" in labels
    secret_tab.canvas.selection = secret_tab.doc.getAllText(0)
    secret_tab.canvas.selection_page = 0
    menu = QMenu()
    secret_tab._extend_menu(menu, 0, 500, 700)
    assert "Markierung schwärzen (vormerken)" in [a.text() for a in menu.actions()]
    for key in ("pdf:tool_redact", "pdf:redact_selection", "pdf:redact_search", "pdf:redact_apply"):
        assert win.registry.get(key) is not None
    win.registry.get("pdf:tool_redact").callback()
    assert secret_tab.canvas.tool == "redact"
    secret_tab.set_editing(False)
    assert secret_tab.redactions == []                                      # Bearbeiten aus → Vormerkungen weg


def test_bookmark_titles_are_reported(win):
    path = win.root / "mit_lesezeichen.pdf"
    path.write_bytes(make_pdf(["Deckblatt", f"Herr {SECRET}"], outline=True))
    page = win.tabs.open_viewer(path, "pdf")
    page.mark_redaction(1, (60, 50, 400, 110), SECRET)
    assert page.apply_redactions(dpi=72, confirm=False)
    assert page.last_leftovers == [f"„{SECRET}“ in den Lesezeichen"]
    page.mark_redaction(0, (0, 0, 10, 10), SECRET)
    assert page.apply_redactions(dpi=72, options=pdfredact.RedactOptions(strip_outline=True), confirm=False)
    assert page.last_leftovers == [] and not contains_anywhere(page.data, SECRET)


def test_render_redacted_paints_box():
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from notex.ui.pdf_edit import render_redacted
    buffer = QBuffer()
    buffer.setData(QByteArray(make_pdf(["Text"])))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument()
    doc.load(buffer)
    image = render_redacted(doc, 0, [(100, 100, 200, 150)], 72)
    assert (image.width_px, image.height_px) == (595, 842)
    index = (125 * 595 + 150) * 3
    assert image.data[index:index + 3] == b"\x00\x00\x00"
    assert image.data[0:3] == b"\xff\xff\xff"
    assert isinstance(image, pdfredact.PageImage)
