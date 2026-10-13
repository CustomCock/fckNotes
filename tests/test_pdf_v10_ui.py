"""V10 in der Oberfläche: Chip „PDF“ in der Seitenleiste, Treffer mit Seite, Klick öffnet die Seite mit Markierung,
nicht durchsuchte PDFs, Index vergessen beim Speichern, Palette."""
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from notex.core.pdfindex import PdfIndex  # noqa: E402
from pdffixtures import encrypted  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def folder(win):
    (win.root / "akten").mkdir(exist_ok=True)
    (win.root / "akten" / "vertrag.pdf").write_bytes(
        make_pdf(["Deckblatt", "Inhalt", "Kuendigungsfrist drei Monate"], outline=False))
    (win.root / "geheim.pdf").write_bytes(encrypted())
    (win.root / "notiz.md").write_text("Kuendigungsfrist auch hier\n", encoding="utf-8")
    return win.root


def search(win, text: str, pdf: bool = True, full_text: bool = False):
    sidebar = win.sidebar
    sidebar.by_name.setChecked(False)
    sidebar.full_text.setChecked(full_text)
    sidebar.pdf.setChecked(pdf)
    sidebar.search_field.setText(text)
    sidebar._start_search()
    deadline = time.time() + 20
    while sidebar._workers and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    for _ in range(20):
        QApplication.processEvents()
    return sidebar.results


def texts(results) -> list[str]:
    out = []

    def walk(item):
        out.append(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))
    for i in range(results.topLevelItemCount()):
        walk(results.topLevelItem(i))
    return out


def test_pdf_chip_finds_page_and_lists_skipped(win, folder):
    results = search(win, "kuendigungsfrist")
    lines = texts(results)
    assert any("akten/vertrag.pdf" in t for t in lines)
    assert any("S. 3:" in t for t in lines)
    assert any("1 PDF(s) nicht durchsucht" in t for t in lines)
    assert any("geheim.pdf" in t and "Passwortschutz" in t for t in lines)
    assert "1 Treffer" in win.sidebar.results_info.text()
    assert win.config["search"]["pdf"] is True
    assert win.sidebar.pdf_index_path().exists()


def test_headers_when_text_and_pdf_both_on(win, folder):
    lines = texts(search(win, "kuendigungsfrist", full_text=True))
    assert any("Volltext" in t for t in lines) and any("PDF-Inhalte" in t for t in lines)


def test_no_search_kind_message(win, folder):
    lines = texts(search(win, "x", pdf=False))
    assert any("Keine Suchart" in t for t in lines)


def test_click_opens_page_with_highlight(win, folder):
    results = search(win, "kuendigungsfrist")
    item = next(results.topLevelItem(i) for i in range(results.topLevelItemCount())
                if "vertrag.pdf" in results.topLevelItem(i).text(0))
    child = item.child(0)
    results._on_item_clicked(child, 0)
    for _ in range(50):
        QApplication.processEvents()
    viewer = win.tabs.current_viewer() if hasattr(win.tabs, "current_viewer") else win.tabs.currentWidget()
    assert viewer.path.name == "vertrag.pdf"
    deadline = time.time() + 10
    while viewer.canvas.current_highlight is None and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert viewer.search_field.text().lower() == "kuendigungsfrist"
    assert viewer.canvas.current_highlight is not None and viewer.canvas.current_highlight[0] == 2
    assert viewer.canvas.current_page == 2


def test_saving_pdf_forgets_index_entry(win, folder):
    search(win, "kuendigungsfrist")
    index = PdfIndex(win.sidebar.pdf_index_path())
    path = folder / "akten" / "vertrag.pdf"
    assert index.status(path) == "ok"
    win.tabs.file_saved.emit(path)
    assert index.status(path) is None


def test_palette_commands(win, folder):
    ids = {c.id for c in win.registry.all()}
    assert {"search:pdf_toggle", "search:pdf_index_clear"} <= ids
    before = win.sidebar.pdf.isChecked()
    win.registry.get("search:pdf_toggle").callback()
    assert win.sidebar.pdf.isChecked() != before
    search(win, "kuendigungsfrist")
    win.registry.get("search:pdf_index_clear").callback()
    assert not win.sidebar.pdf_index_path().exists()
