"""Diagramm-Editor mit echter Maus: Formen setzen, an Andockpunkten verbinden, verschieben (Verbinder folgen),
Text, Rückgängig, Kopieren – und Einbindung in den PDF-Tab (Einfügen, Doppelklick zum Bearbeiten)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from notex.core import pdfdiagram, pdfobjects  # noqa: E402
from notex.core.diagram.model import Diagram, End, route  # noqa: E402
from notex.ui.diagram_editor import MARGIN, DiagramCanvas, DiagramEditor  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def canvas(qapp_window):
    c = DiagramCanvas(Diagram(400, 300))
    c.set_zoom(1.0)
    c.show()
    QApplication.processEvents()
    yield c
    c.close()


@pytest.fixture
def qapp_window(win):
    return win


def _pt(c, x, y) -> QPoint:
    return QPoint(round(MARGIN + x * c.zoom), round(MARGIN + y * c.zoom))


def _drag(c, a, b, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mousePress(c, Qt.MouseButton.LeftButton, modifiers, _pt(c, *a))
    for t in (0.25, 0.5, 0.75, 1.0):
        p = QPointF(_pt(c, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        c.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p, p, Qt.MouseButton.NoButton,
                                     Qt.MouseButton.LeftButton, modifiers))
    QTest.mouseRelease(c, Qt.MouseButton.LeftButton, modifiers, _pt(c, *b))
    QApplication.processEvents()


def _click(c, x, y, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, modifiers, _pt(c, x, y))
    QApplication.processEvents()


def _hover(c, x, y):
    p = QPointF(_pt(c, x, y))
    c.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p, p, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                 Qt.KeyboardModifier.NoModifier))


def test_place_shape_by_click_and_by_drag(canvas):
    canvas.set_tool("rect")
    _click(canvas, 100, 100)
    shape = canvas.model.shapes[0]
    assert (shape.kind, shape.w, shape.h) == ("rect", 120, 60)
    assert shape.center == pytest.approx((100, 100))
    assert canvas.tool == "select" and canvas.selection == [shape.id]
    canvas.set_tool("ellipse")
    _drag(canvas, (200, 200), (300, 250))
    ellipse = canvas.model.shapes[1]
    assert (ellipse.x, ellipse.y, ellipse.w, ellipse.h) == (200, 200, 100, 50)


def test_drag_from_port_connects_to_other_port(canvas):
    a = canvas.model.add_shape("rect", 20, 20)
    b = canvas.model.add_shape("rect", 220, 160)
    _hover(canvas, 60, 40)
    assert canvas.hover == a.id
    _drag(canvas, a.port("e2"), b.port("n2"))
    assert len(canvas.model.connectors) == 1
    conn = canvas.model.connectors[0]
    assert (conn.source.shape, conn.source.port, conn.target.shape, conn.target.port) == (a.id, "e2", b.id, "n2")
    assert conn.end_arrow == "arrow" and canvas.selection == [conn.id]


def test_drag_up_to_free_point_is_exactly_vertical(canvas):
    box = canvas.model.add_shape("rounded", 102, 200, 120, 60)              # Andockpunkt oben bei x = 162
    canvas.update()
    _hover(canvas, 162, 230)
    _drag(canvas, (162, 200), (166, 60))                                    # leicht schief gezogen
    conn = canvas.model.connectors[-1]
    assert conn.source.shape == box.id and conn.target.shape is None
    assert conn.target.x == pytest.approx(162)
    points = route(canvas.model, conn)
    assert all(abs(p[0] - 162) < 0.01 for p in points)


def test_chained_lines_join_at_free_end(canvas):
    box = canvas.model.add_shape("rounded", 102, 200, 120, 60)
    target = canvas.model.add_shape("rect", 0, 30, 60, 60)                   # Andockpunkt rechts bei (60, 60)
    canvas.update()
    _hover(canvas, 162, 230)
    _drag(canvas, (162, 200), (163, 62))                                    # hoch bis auf Höhe des Ziels
    first = canvas.model.connectors[-1]
    assert (first.target.x, first.target.y) == pytest.approx((162, 60))
    canvas.set_tool("connect")
    _drag(canvas, (164, 63), (60, 60))                                      # zweite Linie am Ende ansetzen
    second = canvas.model.connectors[-1]
    assert second.target.shape == target.id
    assert (second.source.x, second.source.y) == pytest.approx((162, 60))   # stößt genau an
    assert box.id != target.id


def test_shape_icons_follow_ink_color():
    from notex.ui.diagram_paint import shape_icon
    image = shape_icon("rect", 28, ink="#ff0000").pixmap(56, 56).toImage()
    colors = {image.pixelColor(x, y).name() for x in range(56) for y in range(56) if image.pixelColor(x, y).alpha() > 200}
    assert "#ff0000" in colors and "#ffffff" not in colors and "#1a1a1a" not in colors


def test_connect_tool_snaps_to_nearest_port_and_free_end(canvas):
    a = canvas.model.add_shape("rect", 20, 20)
    b = canvas.model.add_shape("ellipse", 220, 160)
    canvas.relation = "Vererbung"
    canvas.set_tool("connect")
    _drag(canvas, (75, 75), (283, 180))
    conn = canvas.model.connectors[-1]
    assert conn.source.shape == a.id and conn.target.shape == b.id and conn.end_arrow == "triangle"
    canvas.set_tool("connect")
    _drag(canvas, (75, 75), (380, 40))
    free = canvas.model.connectors[-1]
    assert free.target.shape is None and (free.target.x, free.target.y) == (380, 40)


def test_moving_shape_moves_attached_connector(canvas):
    a = canvas.model.add_shape("rect", 20, 20)
    b = canvas.model.add_shape("rect", 220, 160)
    conn = canvas.model.connect(End(a.id, "e2"), End(b.id, "n2"))
    start = route(canvas.model, conn)[-1]
    _drag(canvas, (280, 190), (330, 230))
    assert (b.x, b.y) == (270, 200)
    end = route(canvas.model, conn)[-1]
    assert end == (start[0] + 50, start[1] + 40)
    canvas.undo()                                           # Rückgängig baut die Formen neu auf → per ID holen
    restored = canvas.model.shape(b.id)
    assert (restored.x, restored.y) == (220, 160)
    canvas.redo()
    assert (canvas.model.shape(b.id).x, canvas.model.shape(b.id).y) == (270, 200)


def test_resize_handle_and_area_handle(canvas):
    shape = canvas.model.add_shape("rect", 20, 20)
    _click(canvas, 60, 40)
    _drag(canvas, (140, 80), (200, 120))
    assert (shape.w, shape.h) == (180, 100)
    _drag(canvas, (400, 300), (500, 360))
    assert (canvas.model.width, canvas.model.height) == (500, 360)


def test_band_select_delete_copy_paste_duplicate(canvas):
    a = canvas.model.add_shape("rect", 20, 20)
    b = canvas.model.add_shape("rect", 220, 160)
    canvas.model.connect(End(a.id), End(b.id))
    _drag(canvas, (5, 5), (395, 295))
    assert set(canvas.selection) == {a.id, b.id, canvas.model.connectors[0].id}
    canvas.copy()
    new = canvas.paste()
    assert len(canvas.model.shapes) == 4 and len(canvas.model.connectors) == 2 and len(new) == 3
    pasted = canvas.model.connectors[1]
    assert pasted.source.shape in new and pasted.target.shape in new
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Delete)
    assert len(canvas.model.shapes) == 2 and len(canvas.model.connectors) == 1
    canvas.select([a.id])
    QTest.keyClick(canvas, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert len(canvas.model.shapes) == 3
    QTest.keyClick(canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(canvas.model.shapes) == 2


def test_edit_texts(canvas):
    shape = canvas.model.add_shape("class", 20, 20, 150, 40)
    assert canvas.edit_shape_text(shape, "Auto\n--\n- ps: int\n- farbe: String\n- marke: String\n--\n+ fahren()")
    assert shape.h > 40                                     # Klasse wächst mit dem Inhalt
    conn = canvas.model.connect(End(shape.id), End(None, None, 300, 200))
    assert canvas.edit_connector_labels(conn, ("fährt", "1", "0..*"))
    assert (conn.label, conn.start_label, conn.end_label) == ("fährt", "1", "0..*")


def test_class_dialog_roundtrip(canvas):
    from notex.ui.diagram_editor import ClassDialog
    dialog = ClassDialog(canvas, "{abstract}\nTier\n--\n- name\n--\n+ laut()")
    assert dialog.abstract.isChecked() and dialog.name.text() == "Tier"
    assert dialog.text() == "{abstract}\nTier\n--\n- name\n--\n+ laut()"


def test_editor_controls_apply_to_selection(win):
    editor = DiagramEditor(win, Diagram(300, 200))
    c = editor.canvas
    a = c.model.add_shape("rect", 10, 10)
    b = c.model.add_shape("rect", 150, 120)
    conn = c.model.connect(End(a.id), End(b.id))
    c.select([conn.id])
    editor.relation.setCurrentText("Komposition")
    editor._relation_chosen(0)
    assert conn.start_arrow == "diamond_filled" and conn.end_arrow == "none"
    editor.route.setCurrentIndex(editor.route.findData("straight"))
    editor._route_chosen(0)
    assert conn.route == "straight"
    c.select([a.id])
    editor._choose_color("fill", "#ffcc00")
    assert a.fill == "#ffcc00"
    editor.font_size.setValue(16)
    assert a.font_size == 16
    ids = c.insert_template("usecase")
    assert ids and c.model.width >= 300
    editor.close()


def test_result_shifts_content_left_of_area(win):
    editor = DiagramEditor(win, Diagram(200, 100))
    editor.canvas.model.add_shape("rect", -30, -10, 50, 30)
    diagram, offset = editor.result_diagram()
    assert offset == (-32, -12)
    assert diagram.shapes[0].x == pytest.approx(2) and diagram.shapes[0].y == pytest.approx(2)
    assert diagram.width == pytest.approx(232)
    editor.close()


# ---- im PDF-Tab ------------------------------------------------------------------------------------------
@pytest.fixture
def tab(win):
    path = win.root / "uml.pdf"
    path.write_bytes(make_pdf(["Aufgabe 3: Zeichne ein Klassendiagramm"], outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    return page


def _uml():
    d = Diagram(260, 160)
    a = d.add_shape("class", 10, 10, 120, 60, "Person")
    b = d.add_shape("class", 10, 100, 120, 50, "Schüler")
    d.connect(End(b.id, "n2"), End(a.id, "s2"), "Vererbung")
    return d


def test_insert_and_edit_diagram_in_pdf(tab):
    assert tab.insert_diagram(0, (72, 200), _uml())
    obj = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert obj.rect == pytest.approx((72, 200, 332, 360))
    changed = pdfdiagram.read_diagram(tab.data, 0, obj.index)
    changed.shapes[0].text = "Mensch"
    assert tab.edit_diagram(obj, changed)
    again = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert pdfdiagram.read_diagram(tab.data, 0, again.index).shapes[0].text == "Mensch"
    assert again.rect[:2] == pytest.approx((72, 200))
    tab.undo()
    assert pdfdiagram.read_diagram(tab.data, 0, again.index).shapes[0].text == "Person"


def test_region_tool_opens_editor_and_inserts(tab, monkeypatch):
    seen = {}

    def fake_exec(editor):
        seen["size"] = (editor.canvas.model.width, editor.canvas.model.height)
        seen["background"] = editor.canvas.page_image is not None
        editor.canvas.insert_template("flow")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(DiagramEditor, "exec", fake_exec)
    tab.set_editing(True)
    tab.set_tool("diagram")
    from PySide6.QtCore import QRectF
    tab.canvas.region_chosen.emit(0, QRectF(72, 300, 300, 240))
    assert seen["size"] == (300, 240) and seen["background"]
    assert [o.kind for o in tab.objects.objects(0)] == ["diagram"]


def test_double_click_on_diagram_opens_editor(tab, monkeypatch):
    tab.insert_diagram(0, (72, 200), _uml())
    calls = []
    monkeypatch.setattr(tab, "edit_diagram", lambda obj, diagram=None: calls.append(obj) or True)
    tab.set_editing(True)
    obj = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert tab.objects.double_click(0, obj.rect[0] + 20, obj.rect[1] + 20)
    assert calls and calls[0].kind == "diagram"


def test_empty_diagram_is_not_inserted_and_delete_via_edit(tab):
    assert not tab.insert_diagram(0, (72, 200), Diagram(100, 100))
    tab.insert_diagram(0, (72, 200), _uml())
    obj = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert tab.edit_diagram(obj, Diagram(100, 100))                  # alles gelöscht → Diagramm weg
    assert [o for o in tab.objects.objects(0) if o.kind == "diagram"] == []


def test_page_background_without_annotation(tab):
    tab.insert_diagram(0, (72, 200), _uml())
    obj = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    image, scale = tab.page_background(0, without=obj.index)
    assert scale == 2.0 and image.width() == 1190
    x, y = round((72 + 70) * scale), round((200 + 40) * scale)
    assert image.pixelColor(x, y).lightness() > 240                     # Diagramm nicht im Hintergrund


def test_palette_and_moving_diagram(win, tab):
    assert win.registry.get("pdf:tool_diagram") is not None
    win.registry.get("pdf:tool_diagram").callback()
    assert tab.canvas.tool == "diagram"
    tab.insert_diagram(0, (72, 200), _uml())
    obj = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert obj.keep_aspect
    assert tab.set_object_rect(obj, (100, 220, 360, 380))
    moved = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert moved.rect[:2] == pytest.approx((100, 220))
    assert pdfdiagram.read_diagram(tab.data, 0, moved.index).shapes[0].text == "Person"
    assert pdfobjects.list_objects(tab.data, 0)[0].kind == "diagram"


# ---- draw.io ---------------------------------------------------------------------------------------------
def test_editor_imports_and_exports_drawio(win, tmp_path):
    from notex.core.diagram import drawio
    source = tmp_path / "uml.drawio"
    source.write_text(drawio.write(_uml()), encoding="utf-8")
    editor = DiagramEditor(win, Diagram(200, 100))
    editor.canvas.model.add_shape("rect", 10, 10, 60, 30, "schon da")
    ids = editor.import_drawio(str(source))
    assert len(ids) == 3 and set(editor.canvas.selection) == set(ids)
    imported = [s for s in editor.canvas.model.shapes if s.id in ids]
    assert {s.text for s in imported} == {"Person", "Schüler"}
    assert min(s.y for s in imported) > 40                                  # unter den vorhandenen Inhalt
    assert editor.canvas.model.height > 100
    target = tmp_path / "raus.drawio"
    assert editor.export_drawio(str(target))
    back = drawio.read(target.read_text(encoding="utf-8"))
    assert {s.text for s in back.shapes} == {"schon da", "Person", "Schüler"}
    editor.close()


def test_editor_import_error_shows_message(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    bad = tmp_path / "kaputt.drawio"
    bad.write_text("<html/>", encoding="utf-8")
    editor = DiagramEditor(win, Diagram(200, 100))
    assert editor.import_drawio(str(bad)) == []
    assert shown and "mxfile" in shown[0]
    editor.close()


def test_pdf_import_and_export_drawio(win, tab, tmp_path, monkeypatch):
    from notex.core.diagram import drawio
    source = tmp_path / "uml.drawio"
    source.write_text(drawio.write(_uml(), compressed=True), encoding="utf-8")
    assert tab.import_drawio(str(source), page=0, edit=False)
    obj = next(o for o in tab.objects.objects(0) if o.kind == "diagram")
    assert obj.rect[:2] == pytest.approx((36, 36))
    assert {s.text for s in pdfdiagram.read_diagram(tab.data, 0, obj.index).shapes} == {"Person", "Schüler"}
    seen = {}

    def fake_exec(editor):
        seen["texts"] = {s.text for s in editor.canvas.model.shapes}
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(DiagramEditor, "exec", fake_exec)
    assert tab.import_drawio(str(source), page=0)                            # mit Editor dazwischen
    assert seen["texts"] == {"Person", "Schüler"}
    assert len([o for o in tab.objects.objects(0) if o.kind == "diagram"]) == 2
    target = tmp_path / "aus_pdf.drawio"
    assert tab.export_drawio(obj, str(target))
    assert len(drawio.read(target.read_text(encoding="utf-8")).connectors) == 1
    tab.objects.select(None)
    assert not tab.export_drawio(path=str(target))                          # nichts ausgewählt
    assert win.registry.get("pdf:import_drawio") is not None
    assert win.registry.get("pdf:export_drawio") is not None
