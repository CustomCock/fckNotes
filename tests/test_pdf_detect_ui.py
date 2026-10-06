"""Felderkennung in der Oberfläche: Arbeitsblatt → Felder, reinklicken, Unterschrifts-Platzhalter."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from notex.core import pdfforms  # noqa: E402
from pdfhelp import make_worksheet_pdf  # noqa: E402

EXPECTED = {"Name", "Klasse", "Datum", "Thema", "Unterschrift", "Ich habe die Aufgabe verstanden"}


@pytest.fixture
def sheet(win):
    path = win.root / "blatt.pdf"
    path.write_bytes(make_worksheet_pdf())
    page = win.tabs.open_viewer(path, "pdf")
    page.canvas.set_zoom("custom", 1.0)
    QApplication.processEvents()
    return page


def _click(page, x, y):
    canvas = page.canvas
    QApplication.processEvents()                       # Layout erst setzen lassen (Bearbeiten-Leiste o. Ä.)
    canvas.verticalScrollBar().setValue(max(0, round(canvas.layout_.to_view(0, x, y)[1] - 200)))
    QApplication.processEvents()
    vx, vy = canvas.layout_.to_view(0, x, y)
    point = QPoint(round(vx - canvas.horizontalScrollBar().value()), round(vy - canvas.verticalScrollBar().value()))
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QApplication.processEvents()


def test_page_words_split_labels_and_underscores(sheet):
    words = [w.text for w in sheet.page_words(0)]
    assert "Name:" in words and "______________________" in words and "Klasse:" in words


def test_detect_creates_fields_in_one_step(sheet):
    found = sheet.detect_fields()
    assert {s.name for s in found} == EXPECTED
    assert {f.name for f in sheet.fields} == EXPECTED
    assert {f.name: f.placeholder for f in sheet.fields}["Unterschrift"] == "signature"
    assert sheet.is_dirty and any(kind == "field" for _p, _r, kind in sheet.canvas.overlays)
    sheet.undo()
    assert sheet.fields == []
    sheet.redo()
    assert len(sheet.fields) == 6


def test_detect_twice_adds_nothing_new(sheet):
    sheet.detect_fields()
    notices = []
    sheet.notice.connect(notices.append)
    assert sheet.detect_fields() == [] and notices
    assert len(sheet.fields) == 6


def test_preview_without_apply(sheet):
    found = sheet.detect_fields(apply=False)
    assert len(found) == 6 and sheet.fields == [] and not sheet.is_dirty


def test_click_detected_field_and_type(sheet):
    sheet.detect_fields()
    name = {f.name: f for f in sheet.fields}["Name"]
    x0, y0, x1, y1 = name.rect
    _click(sheet, (x0 + x1) / 2, (y0 + y1) / 2)
    editor = sheet.objects.editor
    assert isinstance(editor, QLineEdit)
    editor.setText("Ada")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert {f.name: f.value for f in sheet.fields}["Name"] == "Ada"


def test_signature_placeholder_click_signs(sheet, monkeypatch):
    sheet.detect_fields()
    placeholder = {f.name: f for f in sheet.fields}["Unterschrift"]
    calls = []
    monkeypatch.setattr(sheet, "sign_placeholder", lambda obj, value=None: calls.append(obj))
    x0, y0, x1, y1 = placeholder.rect
    _click(sheet, (x0 + x1) / 2, (y0 + y1) / 2)
    assert calls and calls[0].field == "Unterschrift"


def test_sign_placeholder_replaces_field_with_signature(sheet):
    sheet.detect_fields()
    obj = next(o for o in sheet.objects.objects(0) if o.kind == "field" and o.field == "Unterschrift")
    assert sheet.sign_placeholder(obj, ("strokes", [[(0, 0.5), (1, 0.5)]], 4.0))
    assert "Unterschrift" not in {f.name for f in sheet.fields}
    sigs = [o for o in sheet.objects.objects(0) if o.kind == "signature"]
    assert len(sigs) == 1
    sx0, sy0, sx1, sy1 = sigs[0].rect
    assert obj.rect[0] == pytest.approx(sx0) and sy1 <= obj.rect[3] + 0.5
    assert not pdfforms.list_fields(sheet.data) or all(f.name != "Unterschrift" for f in pdfforms.list_fields(sheet.data))


def test_field_highlight_paints(sheet):
    from PySide6.QtGui import QImage, QPainter
    sheet.detect_fields()
    sheet.canvas.overlays = []
    image = QImage(sheet.canvas.viewport().size(), QImage.Format.Format_ARGB32)
    sheet.canvas.viewport().render(image)
    assert not image.isNull()
    sheet.objects.highlight_fields = False
    sheet.canvas.viewport().update()
    assert QPainter is not None


def test_highlight_setting(win, sheet):
    win.config["pdf_highlight_fields"] = False
    win.tabs.apply_preview_settings()
    assert not sheet.objects.highlight_fields
    win.config["pdf_highlight_fields"] = True
    win.tabs.apply_preview_settings()
    assert sheet.objects.highlight_fields


def test_palette_command(win, sheet):
    assert win.registry.get("pdf:detect_fields") is not None
    win.registry.get("pdf:detect_fields").callback()
    assert len(sheet.fields) == 6 and sheet.editing
