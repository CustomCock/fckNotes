"""PDF-Anmerkungen in der Oberfläche: Werkzeuge, Textauswahl → Markierung, Notiz, Text, Kontextmenü, Darstellung."""
import pytest

pytest.importorskip("PySide6")

import uihelp  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, QSize  # noqa: E402
from notex.core import pdfannot, pdfpages  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def pdf_tab(win):
    path = win.root / "notizen.pdf"
    path.write_bytes(make_pdf(["Hallo Welt", "Zweite Seite"]))
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    return page


def _render(page, index, scale=1.0):
    size = page.doc.pagePointSize(index)
    from PySide6.QtPdf import QPdfDocumentRenderOptions
    options = QPdfDocumentRenderOptions()
    options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
    return page.doc.render(index, QSize(round(size.width() * scale), round(size.height() * scale)), options)


def _yellow_share(image, rect) -> float:
    """Anteil gelblicher Pixel im Rechteck (Ansichts-Punkte, Bild im Maßstab 1)."""
    x0, y0, x1, y1 = (round(v) for v in rect)
    samples = [image.pixelColor(x, y) for x in range(x0, x1, 2) for y in range(y0, y1, 2)]
    hits = [c for c in samples if c.alpha() > 200 and c.red() > 200 and c.green() > 160 and c.blue() < 120]
    return len(hits) / max(1, len(samples))


def _select_all(page, index=0):
    page.canvas.selection = page.doc.getAllText(index)
    page.canvas.selection_page = index


def test_tools_switch_cursor_and_reset(pdf_tab):
    pdf_tab.set_tool("text")
    assert pdf_tab.canvas.tool == "text" and pdf_tab.tool_buttons["text"].isChecked()
    pdf_tab.canvas.tool_cancelled.emit()
    assert pdf_tab.canvas.tool == "select" and pdf_tab.tool_buttons["select"].isChecked()
    pdf_tab.set_tool("note")
    pdf_tab.set_editing(False)
    assert pdf_tab.canvas.tool == "select"


def test_highlight_selection_is_drawn_yellow(pdf_tab):
    _select_all(pdf_tab)
    rect = pdf_tab.canvas.selection_rects()[0]
    assert pdf_tab.add_markup("highlight")
    assert pdf_tab.canvas.selection is None and pdf_tab.is_dirty
    assert [a.kind for a in pdf_tab.annotations(0)] == ["highlight"]
    image = _render(pdf_tab, 0)
    assert _yellow_share(image, rect) > 0.4                              # die Zeile ist überwiegend gelb
    assert _yellow_share(image, (rect[0], rect[3] + 40, rect[2], rect[3] + 80)) == 0   # darunter nicht


def test_markup_tool_applies_on_selection_release(pdf_tab):
    pdf_tab.set_tool("underline")
    _select_all(pdf_tab)
    pdf_tab.canvas.markup_chosen.emit()
    assert [a.kind for a in pdf_tab.annotations(0)] == ["underline"]


def test_markup_without_selection_gives_notice(pdf_tab):
    notices = []
    pdf_tab.notice.connect(notices.append)
    assert not pdf_tab.add_markup("highlight")
    assert notices


def test_note_and_text_from_region(pdf_tab, monkeypatch):
    assert pdf_tab.add_note(0, 300, 60, "Prüfen!")
    assert pdf_tab.add_text(0, (72, 300, 72, 300), "Text auf der Seite", 16, "#d03030", border=True)
    kinds = [a.kind for a in pdf_tab.annotations(0)]
    assert kinds == ["note", "text"]
    text = pdf_tab.annotations(0)[1]
    assert text.rect[2] - text.rect[0] == pytest.approx(220, abs=1)       # Klick → Standardbreite
    image = _render(pdf_tab, 0)
    assert _yellow_share(image, (301, 61, 319, 79)) > 0.3                # Notiz-Symbol (gelb)
    # Region-Signal mit Werkzeug „Notiz“ öffnet den Dialog (im Test abgelehnt → nichts passiert)
    pdf_tab.set_tool("note")
    pdf_tab.canvas.region_chosen.emit(0, QRectF(100, 100, 0, 0))
    assert len(pdf_tab.annotations(0)) == 2


def test_text_on_rotated_page_renders_where_placed(pdf_tab):
    pdf_tab.rotate_pages(90, [1])
    assert pdf_tab.add_text(1, (40, 40, 400, 40), "XXXXXXXX", 30)
    image = _render(pdf_tab, 1)
    assert image.width() > image.height()                                  # quer
    dark = [image.pixelColor(x, y).lightness() for x in range(42, 200, 3) for y in range(44, 70, 3)]
    assert min(dark) < 100                                                # Text oben links, wo aufgezogen


def test_edit_and_delete_annotation(pdf_tab):
    pdf_tab.add_note(0, 300, 60, "alt")
    assert pdf_tab.edit_annotation(0, 0, "neu")
    assert pdf_tab.annotations(0)[0].contents == "neu"
    assert pdf_tab.delete_annotation(0, 0)
    assert pdf_tab.annotations(0) == []
    pdf_tab.undo()
    assert pdf_tab.annotations(0)[0].contents == "neu"


def test_context_menu_offers_annotation_actions(pdf_tab):
    from PySide6.QtWidgets import QMenu
    pdf_tab.add_note(0, 300, 60, "Kontext")
    menu = QMenu()
    pdf_tab._extend_menu(menu, 0, 305, 65)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert "Notiz löschen" in labels and any("Kommentar bearbeiten" in t for t in labels)
    menu = QMenu()
    _select_all(pdf_tab)
    pdf_tab._extend_menu(menu, 0, 500, 700)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert {"Markieren", "Unterstreichen", "Durchstreichen", "Notiz hier …", "Text hier …"} <= set(labels)


def test_saved_file_contains_annotations(pdf_tab, monkeypatch):
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda p: __import__("os").remove(p))
    pdf_tab.add_note(0, 10, 10, "bleibt")
    assert pdf_tab.save()
    assert pdfannot.list_annotations(pdf_tab.path.read_bytes(), 0)[0].contents == "bleibt"
    assert pdfpages.page_count(pdf_tab.path.read_bytes()) == 2


def test_canvas_region_drag_emits_rect(pdf_tab):
    regions = []
    pdf_tab.canvas.region_chosen.disconnect()
    pdf_tab.canvas.region_chosen.connect(lambda page, rect: regions.append((page, rect)))
    pdf_tab.canvas.set_tool("text")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    layout = pdf_tab.canvas.layout_
    start = layout.to_view(0, 100, 100)
    end = layout.to_view(0, 200, 160)
    bar = pdf_tab.canvas.verticalScrollBar().value()
    vp = pdf_tab.canvas.viewport()
    p0 = QPoint(round(start[0]), round(start[1] - bar))
    p1 = QPoint(round(end[0]), round(end[1] - bar))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0)
    QTest.mouseMove(vp, p1)
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(p1), QPointF(vp.mapToGlobal(p1)), Qt.MouseButton.NoButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pdf_tab.canvas.mouseMoveEvent(move)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p1)
    assert regions and regions[0][0] == 0
    rect = regions[0][1]
    assert rect.left() == pytest.approx(100, abs=2) and rect.width() == pytest.approx(100, abs=3)
    uihelp.wait_for(lambda: True, 0.05)
