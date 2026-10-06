"""Eingefügtes im PDF anfassen (echte Mausereignisse): auswählen, ziehen, Größe ändern, löschen, Felder direkt
ausfüllen, Doppelklick/Kontextmenü bearbeiten."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit  # noqa: E402

from notex.core import pdfannot, pdfforms, pdfobjects  # noqa: E402
from pdfhelp import make_form_pdf, make_pdf  # noqa: E402


def _build():
    d = make_pdf(["Arbeitsblatt"], outline=False)
    d = pdfannot.add_text(d, 0, (72, 300, 300, 300), "Antwort", 14)                # 0
    d = pdfforms.add_strokes(d, 0, (72, 400, 232, 460), [[(0, 0), (1, 1)]])        # 1
    d = pdfforms.add_text_field(d, 0, (72, 500, 300, 522), "Name")                  # 2
    d = pdfforms.add_text_field(d, 0, (72, 560, 300, 620), "Bemerkung", multiline=True)   # 3
    d = pdfforms.add_checkbox(d, 0, (72, 640, 72, 640), "OK")                       # 4
    return d


@pytest.fixture
def tab(win):
    path = win.root / "blatt.pdf"
    path.write_bytes(_build())
    page = win.tabs.open_viewer(path, "pdf")
    page.canvas.set_zoom("custom", 1.0)
    QApplication.processEvents()
    return page


def _pixel(page, x, y) -> QPoint:
    canvas = page.canvas
    vx, vy = canvas.layout_.to_view(0, x, y)
    return QPoint(round(vx - canvas.horizontalScrollBar().value()), round(vy - canvas.verticalScrollBar().value()))


def _scroll_to(page, y):
    canvas = page.canvas
    vx, vy = canvas.layout_.to_view(0, 0, y)
    canvas.verticalScrollBar().setValue(max(0, round(vy - 200)))
    QApplication.processEvents()


def _drag(page, start, end):
    vp = page.canvas.viewport()
    a, b = _pixel(page, *start), _pixel(page, *end)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for t in (0.3, 0.7, 1.0):
        p = QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
        page.canvas.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p, QPointF(vp.mapToGlobal(p.toPoint())),
                                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                               Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    QApplication.processEvents()


def _click(page, x, y):
    QTest.mouseClick(page.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _pixel(page, x, y))
    QApplication.processEvents()


def _obj(page, index):
    return next(o for o in page.objects.objects(0) if o.index == index)


def test_click_selects_and_drag_moves_signature(tab):
    tab.set_editing(True)
    _scroll_to(tab, 400)
    _click(tab, 150, 430)
    assert tab.objects.selected is not None and tab.objects.selected.kind == "signature"
    _drag(tab, (150, 430), (250, 480))
    rect = _obj(tab, 1).rect
    assert rect[0] == pytest.approx(172, abs=2) and rect[1] == pytest.approx(450, abs=2)
    assert tab.is_dirty
    tab.undo()
    assert _obj(tab, 1).rect[0] == pytest.approx(72, abs=0.5)


def test_corner_handle_resizes_with_aspect(tab):
    tab.set_editing(True)
    _scroll_to(tab, 400)
    _click(tab, 150, 430)
    _drag(tab, (232, 460), (392, 600))                       # rechte untere Ecke
    x0, y0, x1, y1 = _obj(tab, 1).rect
    assert (x0, y0) == pytest.approx((72, 400), abs=1)
    assert (x1 - x0) / (y1 - y0) == pytest.approx(160 / 60, rel=0.02)
    assert x1 - x0 > 250


def test_edge_handle_resizes_text_box(tab):
    tab.set_editing(True)
    _scroll_to(tab, 300)
    _click(tab, 150, 310)
    before = _obj(tab, 0).rect
    _drag(tab, (before[2], (before[1] + before[3]) / 2), (before[2] + 100, (before[1] + before[3]) / 2))
    after = _obj(tab, 0).rect
    assert after[2] - after[0] == pytest.approx(before[2] - before[0] + 100, abs=2)
    assert after[0] == pytest.approx(before[0], abs=0.5)


def test_keys_move_and_delete(tab):
    tab.set_editing(True)
    _scroll_to(tab, 400)
    _click(tab, 150, 430)
    tab.canvas.setFocus()
    QTest.keyClick(tab.canvas, Qt.Key.Key_Right)
    QTest.keyClick(tab.canvas, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert _obj(tab, 1).rect[:2] == pytest.approx((73, 410), abs=0.5)
    QTest.keyClick(tab.canvas, Qt.Key.Key_Delete)
    assert "signature" not in [o.kind for o in tab.objects.objects(0)]
    assert tab.objects.selected is None


def test_click_into_text_field_types_value(tab):
    _scroll_to(tab, 500)
    _click(tab, 150, 511)
    editor = tab.objects.editor
    assert isinstance(editor, QLineEdit) and editor.isVisible()
    assert tab.editing                                        # Ausfüllen schaltet Bearbeiten ein (Speichern/Undo)
    editor.setText("Ada Lovelace")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert tab.objects.editor is None
    assert {f.name: f.value for f in tab.fields}["Name"] == "Ada Lovelace"
    _click(tab, 150, 511)
    QTest.keyClick(tab.objects.editor, Qt.Key.Key_Escape)    # Esc verwirft
    QApplication.processEvents()
    assert tab.objects.editor is None and {f.name: f.value for f in tab.fields}["Name"] == "Ada Lovelace"


def test_multiline_field_and_checkbox_click(tab):
    _scroll_to(tab, 560)
    _click(tab, 150, 590)
    editor = tab.objects.editor
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("Zeile 1\nZeile 2")
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert {f.name: f.value for f in tab.fields}["Bemerkung"] == "Zeile 1\nZeile 2"
    _scroll_to(tab, 640)
    _click(tab, 79, 647)
    assert {f.name: f for f in tab.fields}["OK"].checked
    _click(tab, 79, 647)
    assert not {f.name: f for f in tab.fields}["OK"].checked


def test_choice_and_radio_in_foreign_form(win):
    path = win.root / "antrag.pdf"
    path.write_bytes(make_form_pdf())
    page = win.tabs.open_viewer(path, "pdf")
    page.canvas.set_zoom("custom", 1.0)
    QApplication.processEvents()
    _scroll_to(page, 230)
    _click(page, 150, 232)                                    # Land (Auswahlliste)
    assert isinstance(page.objects.editor, QComboBox)
    page.objects.editor.setCurrentIndex(page.objects.editor.findData("Österreich"))
    page.objects.close_editor(commit=True)
    assert {f.name: f.value for f in page.fields}["Land"] == "Österreich"
    _click(page, 106, 196)                                    # Optionsfeld B
    assert {f.name: f.value for f in page.fields}["Farbe"] == "B"
    _click(page, 150, 262)                                    # schreibgeschützt: keine Eingabe
    assert page.objects.editor is None


def test_drag_field_moves_instead_of_editing(tab):
    tab.set_editing(True)
    _scroll_to(tab, 500)
    _drag(tab, (150, 511), (150, 451))
    assert tab.objects.editor is None
    assert _obj(tab, 2).rect[1] == pytest.approx(440, abs=2)


def test_double_click_edits_text_with_style(tab, monkeypatch):
    from notex.ui import pdf_edit
    from PySide6.QtWidgets import QDialog
    tab.set_editing(True)
    seen = {}

    def fake_exec(dialog):
        seen["values"] = (dialog.edit.toPlainText(), dialog.size.value(), dialog.color.currentData())
        dialog.edit.setPlainText("Neue Antwort")
        dialog.size.setValue(20)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(pdf_edit.TextDialog, "exec", fake_exec)
    _scroll_to(tab, 300)
    QTest.mouseDClick(tab.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      _pixel(tab, 150, 310))
    QApplication.processEvents()
    assert seen["values"] == ("Antwort", 14, "#1a1a1a")
    obj = _obj(tab, 0)
    assert obj.contents == "Neue Antwort"
    annot = pdfannot.open_reader(tab.data).pages[0]["/Annots"][0].get_object()
    assert pdfannot.freetext_style(annot)[0] == 20


def test_field_properties_and_context_menu(tab):
    from PySide6.QtWidgets import QMenu
    obj = _obj(tab, 2)
    assert tab.field_properties(obj, new_name="Vorname", multiline=False, size=10)
    assert "Vorname" in {f.name for f in tab.fields}
    menu = QMenu()
    tab._extend_menu(menu, 0, 150, 511)
    labels = [a.text() for a in menu.actions()]
    assert "Feld-Eigenschaften …" in labels and any("ausfüllen" in t for t in labels)
    menu = QMenu()
    tab._extend_menu(menu, 0, 150, 430)
    assert "Unterschrift ersetzen …" in [a.text() for a in menu.actions()]


def test_replace_signature_keeps_place(tab):
    obj = _obj(tab, 1)
    assert tab.replace_signature(obj, ("strokes", [[(0, 0.5), (1, 0.5)]], 2.0))
    sigs = [o for o in tab.objects.objects(0) if o.kind == "signature"]
    assert len(sigs) == 1 and sigs[0].rect[0] == pytest.approx(72) and sigs[0].rect[1] == pytest.approx(400)


def test_markups_not_grabbed_while_reading(win):
    path = win.root / "m.pdf"
    path.write_bytes(pdfannot.add_markup(make_pdf(["Hallo Welt"], outline=False), 0, "highlight",
                                         [(70, 75, 220, 110)]))
    page = win.tabs.open_viewer(path, "pdf")
    page.canvas.set_zoom("custom", 1.0)
    QApplication.processEvents()
    _scroll_to(page, 80)
    assert not page.objects.press(0, 100, 90, QPointF(0, 0))   # Lesemodus: Textauswahl bleibt möglich
    page.set_editing(True)
    assert page.objects.press(0, 100, 90, QPointF(0, 0))
    page.objects._press = None
    assert pdfobjects.list_objects(page.data, 0)[0].kind == "highlight"


def test_palette_edit_and_delete_selected(win, tab):
    assert win.registry.get("pdf:edit_selected") and win.registry.get("pdf:delete_selected")
    tab.set_editing(True)
    notices = []
    tab.notice.connect(notices.append)
    win.registry.get("pdf:delete_selected").callback()
    assert notices
    tab.objects.select(_obj(tab, 1))
    win.registry.get("pdf:delete_selected").callback()
    assert "signature" not in [o.kind for o in tab.objects.objects(0)]
