"""V3 in der Oberfläche: Stift, Radierer, Formen (Ziehen, Polygon per Klicks), Stil, Stempel (Standard, eigener,
Datum/Name)."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from notex.core import pdfannot, pdfdraw, pdfstamps  # noqa: E402
from notex.paths import stamps_dir  # noqa: E402
from pdffixtures import assert_valid_pdf  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "blatt.pdf"
    path.write_bytes(make_pdf(["Eins", "Zwei"], size=(600, 800), outline=False))
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    page.resize(1000, 800)
    page.show()
    QApplication.processEvents()
    return page


def kinds(tab, page=0):
    return [a.kind for a in pdfannot.list_annotations(tab.data, page)]


def _widget_pos(tab, page, x, y) -> QPoint:
    canvas = tab.canvas
    rect = canvas.layout_.pages[page]
    scale = canvas.layout_.scale
    return QPoint(round(rect.x + x * scale - canvas.horizontalScrollBar().value()),
                  round(rect.y + y * scale - canvas.verticalScrollBar().value()))


def test_ink_stroke_and_eraser_with_undo(tab):
    tab.set_tool("ink")
    tab.canvas.path_chosen.emit(0, [(50 + i * 4, 100 + (i % 3) * 3) for i in range(30)])
    assert kinds(tab) == ["ink"]
    tab.set_tool("eraser")
    assert tab.erase(0, [(500, 700)]) == 0 and kinds(tab) == ["ink"]
    tab.canvas.path_chosen.emit(0, [(90, 103)])
    assert kinds(tab) == []
    tab.undo()
    assert kinds(tab) == ["ink"]


def test_shapes_by_drag_and_path(tab):
    for tool in ("rect", "ellipse", "cloud"):
        tab.set_tool(tool)
        assert tab.tool_buttons["shape"].isChecked() and not tab.tool_buttons["select"].isChecked()
        tab.canvas.region_chosen.emit(0, QRectF(60, 60, 120, 80))
    tab.set_tool("arrow")
    tab.canvas.path_chosen.emit(0, [(50, 300), (200, 360)])
    tab.set_tool("line")
    tab.canvas.path_chosen.emit(0, [(50, 400)])                    # nur Klick: nichts
    assert kinds(tab) == ["square", "circle", "square", "line"]
    assert_valid_pdf(tab.data)


def test_polygon_by_clicks_and_enter(tab):
    tab.set_tool("polygon")
    viewport = tab.canvas.viewport()
    for x, y in ((100, 100), (250, 120), (180, 260)):
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         _widget_pos(tab, 0, x, y))
    assert len(tab.canvas._path[1]) == 3
    QTest.keyClick(tab.canvas, Qt.Key.Key_Backspace)
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _widget_pos(tab, 0, 160, 300))
    QTest.keyClick(tab.canvas, Qt.Key.Key_Return)
    assert kinds(tab) == ["polygon"] and tab.canvas._path is None
    (info,) = pdfannot.list_annotations(tab.data, 0)
    assert info.rect[3] == pytest.approx(300 + 2, abs=2.5)       # letzter Punkt (160, 300), nicht der gelöschte


def test_escape_drops_polygon_points_before_leaving_tool(tab):
    tab.set_tool("polygon")
    QTest.mouseClick(tab.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     _widget_pos(tab, 0, 10, 10))                    # Klick gibt der Seite den Fokus
    tab.canvas.setFocus()
    tab.canvas._path = (0, [(10, 10), (50, 10)])
    QTest.keyClick(tab.canvas, Qt.Key.Key_Escape)
    assert tab.canvas._path is None and tab.canvas.tool == "polygon"
    QTest.keyClick(tab.canvas, Qt.Key.Key_Escape)
    assert tab.canvas.tool == "select"


def test_style_dialog_sets_style_and_restyles_selection(tab, monkeypatch):
    from notex.ui.pdf_draw_ui import DrawStyleDialog
    tab.set_tool("rect")
    tab.canvas.region_chosen.emit(0, QRectF(60, 60, 120, 80))
    tab.set_tool("select")
    tab.objects.select(tab.objects.objects(0)[0])

    def setup(dialog):
        dialog.color.set_color("#0000ff")
        dialog.width.setValue(5)
        dialog.dash.setCurrentIndex(2)
        dialog.fill_on.setChecked(True)
        dialog.opacity.setValue(50)
    monkeypatch.setattr(DrawStyleDialog, "exec", lambda self: (setup(self), QDialog.DialogCode.Accepted)[-1])
    assert tab.edit_draw_style()
    assert tab.shape_style.color == "#0000ff" and tab.shape_style.dash == "dotted"
    from pypdf import PdfReader
    import io
    annot = PdfReader(io.BytesIO(tab.data)).pages[0]["/Annots"][0].get_object()
    style = pdfdraw.style_of(annot)
    assert (style.color, style.width, style.dash, style.opacity) == ("#0000ff", 5, "dotted", 0.5) and style.fill


def test_pen_style_without_fill(tab):
    tab.set_tool("ink")
    assert tab.edit_draw_style(pdfdraw.Style("#00aa00", 4, "#ffffff", 0.7, "dashed", False))
    assert tab.ink_style.color == "#00aa00" and tab.canvas.path_color == "#00aa00"


def test_builtin_stamp_with_date_and_author(tab):
    tab.author = "Mia"
    tab.choose_stamp(pdfstamps.builtin("confidential"))
    assert tab.canvas.tool == "stamp" and tab.tool_buttons["stamp"].isChecked()
    tab.stamp_with_date = tab.stamp_with_user = True
    tab.canvas.region_chosen.emit(0, QRectF(100, 100, 0, 0))
    (info,) = pdfannot.list_annotations(tab.data, 0)
    assert info.kind == "stamp" and info.contents.startswith("VERTRAULICH (") and "Mia" in info.contents
    obj = tab.objects.objects(0)[0]
    assert obj.movable and obj.keep_aspect


def _png(color="#ff0000") -> bytes:
    image = QImage(8, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_custom_text_and_image_stamps(tab):
    text = tab.create_custom_stamp({"text": "KOPIE", "color": "#555555", "image": None,
                                    "with_date": False, "with_user": False})
    assert text is not None and tab.current_stamp.key == text.key
    assert (stamps_dir() / "stamps.json").exists()
    assert tab.add_stamp(0, (50, 50, 50, 50))
    image = tab.create_custom_stamp({"text": "", "color": "#000000", "image": _png(),
                                     "with_date": True, "with_user": False})
    assert tab.add_stamp(0, (50, 200, 250, 400))
    assert kinds(tab) == ["stamp", "stamp"]
    assert [s.key for s in tab.custom_stamps()] == [text.key, image.key]
    assert tab.delete_custom_stamp(image.key, confirmed=True)
    assert tab.current_stamp.key == "approved" and [s.key for s in tab.custom_stamps()] == [text.key]
    assert_valid_pdf(tab.data)


def test_custom_stamp_dialog_validates(tab, tmp_path):
    from notex.ui.pdf_draw_ui import CustomStampDialog
    dialog = CustomStampDialog(tab, tmp_path)
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.error.text()
    bad = tmp_path / "x.png"
    bad.write_bytes(b"GIF89a")
    assert not dialog.choose_image(bad)
    good = tmp_path / "y.png"
    good.write_bytes(_png())
    assert dialog.choose_image(good)
    assert dialog.values()["image"] == good.read_bytes()


def test_palette_entries(win):
    for key in ("pdf:tool_ink", "pdf:tool_eraser", "pdf:tool_rect", "pdf:tool_ellipse", "pdf:tool_line",
                "pdf:tool_arrow", "pdf:tool_polygon", "pdf:tool_cloud", "pdf:draw_style", "pdf:tool_stamp",
                "pdf:stamp_custom"):
        assert win.registry.get(key) is not None, key


def test_author_setting_reaches_open_tabs(win, tab):
    win.config["pdf_author"] = "Jo"
    win.tabs.apply_preview_settings()
    assert tab.author == "Jo" and tab.stamp_user() == "Jo"


def test_edit_bar_wraps_instead_of_forcing_a_wide_window(win, tab):
    """Die Bearbeiten-Leiste bricht gruppenweise um – sie darf das Fenster nicht breiter erzwingen (vorher 1766 px)."""
    assert tab.edit_bar.minimumSizeHint().width() < 600
    win.resize(1000, 800)
    QApplication.processEvents()
    assert win.width() < 1200                     # andere Teile (Seitenleiste) setzen ~1000–1020 px; vorher 1766
    bar = tab.edit_bar
    one_row = bar.layout().heightForWidth(4000)
    assert bar.layout().heightForWidth(500) > one_row                   # schmal: mehrere Zeilen
    for button in tab.tool_buttons.values():
        assert button.parent() is not None and button.isVisibleTo(tab)
