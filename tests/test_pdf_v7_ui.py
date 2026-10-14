"""V7 in der Oberfläche: Bild einfügen (Klick/Rahmen/Shift), drehen (R), Eigenschaften, ersetzen, exportieren;
Bilder im Inhalt auswählen (Maus), löschen (Entf), zuschneiden (Ränder und Rahmen), ersetzen, Liste und Export."""
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRectF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from notex.core import pdfimages, pdfobjects  # noqa: E402
from pdffixtures import assert_valid_pdf, images_doc, picture  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "bilder.pdf"
    path.write_bytes(images_doc())
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    page.resize(1000, 800)
    page.show()
    QApplication.processEvents()
    return page


@pytest.fixture
def png(win):
    path = win.root / "logo.png"
    path.write_bytes(picture("PNG", (40, 20)))
    return path


def _widget_pos(tab, page, x, y) -> QPoint:
    canvas = tab.canvas
    rect = canvas.layout_.pages[page]
    scale = canvas.layout_.scale
    return QPoint(round(rect.x + x * scale - canvas.horizontalScrollBar().value()),
                  round(rect.y + y * scale - canvas.verticalScrollBar().value()))


def images(tab, page=0):
    return [o for o in pdfobjects.list_objects(tab.data, page) if o.kind == "image"]


def test_toolbar_has_image_tools(tab):
    assert {"image", "pickimage"} <= set(tab.tool_buttons)
    tab.set_tool("image")
    assert tab.tool_buttons["image"].isChecked() and tab.canvas.tool == "image"


def test_insert_by_click_selects_and_undo(tab, png):
    assert tab.insert_image(0, (100, 700, 100, 700), png)
    obj = images(tab)[0]
    assert obj.rect == pytest.approx((100, 700, 130, 715), abs=0.05)
    assert tab.objects.selected is not None and tab.objects.selected.kind == "image"
    tab.undo()
    assert images(tab) == []
    tab.redo()
    assert len(images(tab)) == 1
    assert_valid_pdf(tab.data)


def test_insert_via_region_uses_file_dialog(tab, png, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(png), "")))
    tab.set_tool("image")
    tab.canvas.region_chosen.emit(0, QRectF(100, 650, 200, 200))
    assert images(tab)[0].rect == pytest.approx((100, 650, 300, 750), abs=0.05)


def test_insert_cancelled_dialog_changes_nothing(tab, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    before = tab.data
    assert not tab.insert_image(0, (10, 10, 10, 10))
    assert tab.data is before


def test_insert_garbage_warns(tab, win):
    bad = win.root / "kaputt.png"
    bad.write_bytes(b"nope")
    assert not tab.insert_image(0, (10, 10, 10, 10), bad)


def test_rotate_with_r_key_and_shift_r(tab, png):
    tab.insert_image(0, (100, 650, 300, 850), png)
    tab.canvas.setFocus()
    QTest.keyClick(tab.canvas, Qt.Key.Key_R)
    assert images(tab)[0].angle == 90
    QTest.keyClick(tab.canvas, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
    assert images(tab)[0].angle == 0


def test_properties_dialog_values_and_direct(tab, png, monkeypatch):
    tab.insert_image(0, (100, 650, 300, 850), png)
    obj = images(tab)[0]
    assert tab.image_properties(obj, angle=45, opacity=0.4)
    meta = pdfimages.image_meta(tab.data, 0, obj.index)
    assert (meta.angle, meta.opacity) == (45, 0.4)
    from notex.ui.pdf_images_ui import ImagePropertiesDialog
    monkeypatch.setattr(ImagePropertiesDialog, "exec", lambda self: (self.angle.setValue(-90),
                                                                     QDialog.DialogCode.Accepted)[1])
    assert tab.image_properties(images(tab)[0])
    assert pdfimages.image_meta(tab.data, 0, obj.index).angle == 270
    assert not tab.image_properties(images(tab)[0], angle=270, opacity=0.4)   # nichts geändert


def test_double_click_opens_properties(tab, png, monkeypatch):
    tab.insert_image(0, (100, 650, 300, 850), png)
    called = []
    monkeypatch.setattr(tab, "image_properties", lambda obj=None, **k: called.append(obj) or False)
    tab.edit_object(images(tab)[0])
    assert called and called[0].kind == "image"


def test_replace_and_export_inserted(tab, png, win):
    tab.insert_image(0, (100, 650, 300, 850), png)
    jpeg = win.root / "foto.jpg"
    jpeg.write_bytes(picture("JPEG", (20, 20)))
    assert tab.replace_image(images(tab)[0], jpeg)
    target = tab.export_image(images(tab)[0], win.root / "raus")
    assert target.name == "raus.jpg" and target.read_bytes() == jpeg.read_bytes()


def test_shift_corner_drag_distorts_only_straight_images(tab, png, monkeypatch):
    tab.insert_image(0, (100, 600, 300, 800), png)
    obj = images(tab)[0]
    controller = tab.objects
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QGuiApplication
    monkeypatch.setattr(QGuiApplication, "keyboardModifiers",
                        staticmethod(lambda: Qt.KeyboardModifier.ShiftModifier))
    controller._press = (0, 300, 700, obj, "br", QPointF(0, 0))
    controller.move(0, 300, 760, QPointF(50, 50))
    assert controller._ghost == pytest.approx((100, 600, 300, 760))
    controller.release(0, 300, 760)
    assert images(tab)[0].rect == pytest.approx((100, 600, 300, 760), abs=0.05)
    monkeypatch.setattr(QGuiApplication, "keyboardModifiers", staticmethod(lambda: Qt.KeyboardModifier.NoModifier))
    obj = images(tab)[0]
    controller._press = (0, 300, 760, obj, "br", QPointF(0, 0))
    controller.move(0, 400, 760, QPointF(50, 50))
    x0, y0, x1, y1 = controller._ghost
    assert (x1 - x0) / (y1 - y0) == pytest.approx(200 / 160, abs=0.01)   # Ecke ohne Shift: Verhältnis bleibt
    controller._press = controller._ghost = None


def test_context_menu_for_inserted_image(tab, png):
    from PySide6.QtWidgets import QMenu
    tab.insert_image(0, (100, 650, 300, 850), png)
    menu = QMenu()
    tab._extend_menu(menu, 0, 150, 680)
    texts = [a.text() for a in menu.actions()]
    assert "Bild exportieren …" in texts and "Bild löschen" in texts and "Bild nach rechts drehen  R" in texts


# ---- Bilder im Seiteninhalt ---------------------------------------------------------------------------------------
def test_pick_by_mouse_and_delete_with_key(tab, monkeypatch):
    tab.set_tool("pickimage")
    viewport = tab.canvas.viewport()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _widget_pos(tab, 0, 350, 200))
    assert tab.selected_image is not None and tab.selected_image.name == "/ImP"
    assert any(chosen for _rect, chosen in tab._image_overlay(0))
    from notex.ui import dialogs
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    tab.canvas.setFocus()
    QTest.keyClick(tab.canvas, Qt.Key.Key_Delete)
    assert [i.name for i in pdfimages.page_images(tab.data, 0)] == ["/ImJ", "", "/ImJ", "/ImF"]
    assert tab.selected_image is None
    tab.undo()
    assert len(pdfimages.page_images(tab.data, 0)) == 5


def test_escape_clears_image_selection(tab):
    tab.set_tool("pickimage")
    tab.select_content_image(0, 0)
    tab.canvas.setFocus()
    QTest.keyClick(tab.canvas, Qt.Key.Key_Escape)
    assert tab.selected_image is None


def test_overlay_only_in_image_tool(tab):
    assert tab._image_overlay(0) == []
    tab.set_tool("pickimage")
    assert len(tab._image_overlay(0)) == 5
    tab.set_tool("select")
    assert tab.selected_image is None


def test_delete_needs_confirmation(tab):
    before = tab.data
    assert not tab.delete_content_image(0, 0)                       # Dialog abgelehnt (Tests)
    assert tab.data is before
    assert not tab.delete_content_image(0, 4, confirmed=True)       # in einer Gruppe: nicht löschbar


def test_crop_by_margins_and_dialog(tab, monkeypatch):
    assert tab.crop_content_image(0, 0, margins=(0, 0, 0.5, 0))
    assert pdfimages.page_images(tab.data, 0)[0].width_px == 20
    assert tab.selected_image is not None and tab.selected_image.width_px == 20
    from notex.ui.pdf_images_ui import CropImageDialog

    def accept(self):
        self.boxes["bottom"].setValue(50)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CropImageDialog, "exec", accept)
    assert tab.crop_content_image(0, 1)
    assert pdfimages.page_images(tab.data, 0)[1].height_px == 10
    assert_valid_pdf(tab.data)


def test_crop_by_dragging_frame(tab, monkeypatch):
    from notex.ui.pdf_images_ui import CropImageDialog

    def drag(self):
        self._drag()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CropImageDialog, "exec", drag)
    assert not tab.crop_content_image(0, 0)
    assert tab.canvas.tool == "imagecrop" and tab.tool_buttons["pickimage"].isChecked()
    viewport = tab.canvas.viewport()
    start, end = _widget_pos(tab, 0, 60, 130), _widget_pos(tab, 0, 170, 260)   # mitten im 20. Pixel (167–172)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    image = pdfimages.page_images(tab.data, 0)[0]
    assert image.width_px == 20 and tab.canvas.tool == "pickimage"


def test_crop_refused_for_inline(tab):
    before = tab.data
    assert not tab.crop_content_image(0, 2, margins=(0.1, 0, 0, 0))
    assert tab.data is before


def test_replace_here_and_everywhere(tab, png, monkeypatch):
    assert tab.replace_content_image(0, 0, png, everywhere=False)
    assert [i.name for i in pdfimages.all_images(tab.data)].count("/ImJ") == 2
    before = tab.data
    assert not tab.replace_content_image(0, 2, png)                     # Inline-Bild: nur exportieren/löschen
    assert tab.data is before
    from notex.ui.pdf_images_ui import ReplaceImageDialog
    seen = []

    def everywhere(self):
        seen.append(self.everywhere.text())
        self.everywhere.setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ReplaceImageDialog, "exec", everywhere)
    jpeg_index = next(i.index for i in pdfimages.page_images(tab.data, 0) if i.name == "/ImJ")
    assert tab.replace_content_image(0, jpeg_index, png)
    assert "(2×)" in seen[0]
    assert all(i.filters == ("/FlateDecode",) for i in pdfimages.all_images(tab.data) if i.name == "/ImJ")


def test_export_single_and_all(tab, win):
    target = tab.export_content_image(0, 0, win.root / "erstes")
    assert target.name == "erstes.jpg" and target.read_bytes() == picture("JPEG", (40, 20))
    folder = win.root / "export"
    folder.mkdir()
    (folder / "bilder-S1-Bild1.jpg").write_bytes(b"alt")
    paths = tab.export_all_images(folder, wait=True)
    assert len(paths) == 6
    assert (folder / "bilder-S1-Bild1.jpg").read_bytes() == b"alt"         # nie überschreiben
    assert (folder / "bilder-S1-Bild1 (2).jpg").exists()


def test_images_dialog_lists_and_actions(tab, monkeypatch):
    dialog = tab.show_images()
    assert dialog.table.rowCount() == 6
    dialog.table.selectRow(2)                                            # Inline-Bild
    assert not dialog.crop_button.isEnabled() and not dialog.replace_button.isEnabled()
    assert dialog.delete_button.isEnabled()
    dialog.table.selectRow(4)                                            # in einer Gruppe
    assert dialog.replace_button.isEnabled() and not dialog.delete_button.isEnabled()
    assert tab.canvas.tool == "pickimage" and tab.selected_image.index == 4
    from notex.ui import dialogs
    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    dialog.table.selectRow(1)
    dialog.delete_button.click()
    assert dialog.table.rowCount() == 5
    dialog.close()


def test_images_dialog_read_only_still_exports(win):
    path = win.root / "nur.pdf"
    path.write_bytes(images_doc())
    page = win.tabs.open_viewer(path, "pdf")
    page.show()
    QApplication.processEvents()
    dialog = page.show_images()
    dialog.table.selectRow(0)
    assert dialog.export_button.isEnabled()
    dialog.close()


def test_content_image_context_menu(tab):
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    tab._extend_menu(menu, 0, 100, 160)
    sub = next(a.menu() for a in menu.actions() if a.menu() is not None and "Bild auf der Seite" in a.text())
    texts = [a.text() for a in sub.actions()]
    assert texts[:2] == ["Exportieren …", "Ersetzen …"] and "Löschen  Entf" in texts


def test_palette_has_image_commands(win):
    ids = {c.id for c in win.registry.all()}
    assert {"pdf:tool_image", "pdf:tool_pickimage", "pdf:images", "pdf:images_export_all", "pdf:image_rotate_right",
            "pdf:image_rotate_left", "pdf:image_properties"} <= ids


def test_page_without_images_has_empty_dialog(win):
    path = win.root / "leer.pdf"
    path.write_bytes(make_pdf(["Nur Text"], outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    dialog = page.show_images()
    assert dialog.table.rowCount() == 0 and not dialog.export_all.isEnabled()
    dialog.close()


def test_png_export_is_real_png(tab, win):
    target = tab.export_content_image(0, 1, win.root / "maske.png")
    from PIL import Image
    assert Image.open(io.BytesIO(target.read_bytes())).mode == "RGBA"
