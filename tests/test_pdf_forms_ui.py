"""PDF-Formulare in der Oberfläche: Felder sichtbar, Formular-Panel, Felder anlegen, Unterschrift, Einbrennen."""
import pytest

pytest.importorskip("PySide6")

import uihelp  # noqa: E402
from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from notex.core import pdfforms  # noqa: E402
from pdfhelp import make_form_pdf, make_pdf  # noqa: E402


@pytest.fixture
def form_tab(win):
    path = win.root / "antrag.pdf"
    path.write_bytes(make_form_pdf())
    page = win.tabs.open_viewer(path, "pdf")
    return page


def _render(page, index=0):
    from PySide6.QtPdf import QPdfDocumentRenderOptions
    size = page.doc.pagePointSize(index)
    options = QPdfDocumentRenderOptions()
    options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
    return page.doc.render(index, QSize(round(size.width()), round(size.height())), options)


def _dark_pixels(image, rect) -> int:
    x0, y0, x1, y1 = (round(v) for v in rect)
    return sum(1 for x in range(x0, x1) for y in range(y0, y1)
               if image.pixelColor(x, y).alpha() > 200 and image.pixelColor(x, y).lightness() < 160)


def test_field_values_are_visible_in_viewer(form_tab):
    assert {f.name for f in form_tab.fields} == {"Ort", "Zustimmung", "Farbe", "Land", "Aktenzeichen"}
    ort = next(f for f in form_tab.fields if f.name == "Ort")
    assert _dark_pixels(_render(form_tab), ort.rect) >= 5            # „Berlin“ wird gezeichnet
    assert "Formular (5 Felder)" in " ".join(form_tab.status_parts())


def test_form_panel_fill_in_one_step(form_tab):
    form_tab.set_form_visible(True)
    assert form_tab.editing and not form_tab.form_panel.isHidden()
    panel = form_tab.form_panel
    assert not panel.editors["Aktenzeichen"].isEnabled()
    panel.set_value("Ort", "München")
    panel.set_value("Zustimmung", True)
    panel.set_value("Farbe", "B")
    assert panel.changed_values() == {"Ort": "München", "Zustimmung": True, "Farbe": "B"}
    panel.apply_button.click()
    values = {f.name: f.value for f in form_tab.fields}
    assert values["Ort"] == "München" and values["Zustimmung"] == "Ja" and values["Farbe"] == "B"
    assert form_tab.is_dirty
    form_tab.undo()
    assert {f.name: f.value for f in form_tab.fields}["Ort"] == "Berlin"
    assert panel.editors["Ort"].text() == "Berlin"                   # Panel folgt dem Stand


def test_show_field_jumps_and_highlights(form_tab):
    info = form_tab.fields[0]
    form_tab.show_field(info)
    assert any(kind == "field" for _p, _r, kind in form_tab.canvas.overlays)
    form_tab._clear_field_overlay()
    assert not form_tab.canvas.overlays


def test_create_fields_and_remove(win):
    path = win.root / "leer.pdf"
    path.write_bytes(make_pdf(["Leer"], outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    assert page.add_field(0, (72, 100, 300, 122), "Name")
    assert page.add_checkbox(0, (72, 140, 72, 140), "OK")
    assert [f.name for f in page.fields] == ["Name", "OK"]
    assert page._free_name("Feld") == "Feld1"
    assert not page.add_field(0, (72, 200, 300, 222), "Name")          # doppelt → Meldung, kein Schritt
    assert page.fill_form({"Name": "Ada", "OK": True})
    assert _dark_pixels(_render(page), page.fields[0].rect) > 5
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    page._extend_menu(menu, 0, 80, 110)
    assert any("löschen" in a.text() for a in menu.actions())
    assert page.remove_field("OK")
    assert [f.name for f in page.fields] == ["Name"]


def test_signature_strokes_and_image(win):
    path = win.root / "vertrag.pdf"
    path.write_bytes(make_pdf(["Vertrag"], outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    assert page.add_signature(0, (72, 400, 72, 400), ("strokes", [[(0, 0.5), (0.5, 0.0), (1, 1)]], 3.0))
    stamps = page.annotations(0)
    assert stamps[0].kind == "stamp" and stamps[0].rect[2] - stamps[0].rect[0] == pytest.approx(160)
    image = QImage(60, 20, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for x in range(60):
        image.setPixelColor(x, 10, QColor("#102050"))
    assert page.add_signature(0, (300, 400, 420, 500), ("image", image))
    second = page.annotations(0)[1]
    assert second.rect[2] - second.rect[0] == pytest.approx(120) and second.rect[3] - second.rect[1] == pytest.approx(40)


def test_image_to_bytes_and_strokes_normalize():
    from notex.ui.pdf_edit import image_to_bytes, normalize_strokes
    image = QImage(3, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor(10, 20, 30, 255))
    w, h, rgb, alpha = image_to_bytes(image)
    assert (w, h) == (3, 2) and rgb == bytes([10, 20, 30] * 6) and alpha is None
    image.setPixelColor(0, 0, QColor(10, 20, 30, 0))
    assert image_to_bytes(image)[3][0] == 0
    strokes, aspect = normalize_strokes([[(10, 10), (110, 60)]], margin=0)
    assert strokes == [[(0.0, 0.0), (1.0, 1.0)]] and aspect == pytest.approx(2.0)


def test_signature_pad_records_strokes(qtbot=None):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from notex.ui.pdf_edit import _SignaturePad
    pad = _SignaturePad()
    pad.resize(480, 180)
    pad.show()
    QTest.mousePress(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    for x in (40, 80, 120):
        pad.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(x, 60), QPointF(x, 60),
                                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier))
    assert len(pad.strokes) == 1 and len(pad.strokes[0]) == 4
    pad.clear()
    assert pad.strokes == []
    pad.close()


def test_flatten_from_ui(form_tab):
    form_tab.set_editing(True)
    assert form_tab.flatten(confirm=False)
    assert form_tab.fields == [] and not pdfforms.has_widgets(form_tab.data)
    ort_rect = (72, 842 - 720, 272, 842 - 700)
    assert _dark_pixels(_render(form_tab), ort_rect) >= 5              # Wert steht jetzt fest auf der Seite


def test_palette_commands(win, form_tab):
    for key in ("pdf:form", "pdf:tool_field", "pdf:tool_checkbox", "pdf:tool_signature", "pdf:flatten"):
        assert win.registry.get(key) is not None
    win.registry.get("pdf:tool_signature").callback()
    assert form_tab.canvas.tool == "signature"
    win.registry.get("pdf:form").callback()
    assert not form_tab.form_panel.isHidden()
    uihelp.wait_for(lambda: True, 0.01)
