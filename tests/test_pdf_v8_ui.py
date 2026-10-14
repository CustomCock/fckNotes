"""V8 in der Oberfläche: Ebenen-Dialog (Felder, Vorschau, Vorlagen), Anwenden im Hintergrund mit Rückgängig,
Aktualisieren, Entfernen, fremde Wasserzeichen, Palette."""
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from notex.core import pdfstamp_layers as L  # noqa: E402
from notex.core.pdfpages import insert_blank  # noqa: E402
from pdffixtures import assert_valid_pdf, picture  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "bericht.pdf"
    path.write_bytes(make_pdf(["Eins", "Zwei", "Drei"], outline=False, title="Bericht"))
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    page.resize(1000, 800)
    page.show()
    QApplication.processEvents()
    return page


def text(data: bytes, page: int) -> str:
    return PdfReader(io.BytesIO(data)).pages[page].extract_text()


def kinds(tab) -> list[str]:
    return sorted(layer.kind for layer in L.list_layers(tab.data))


def test_watermark_applied_in_background_with_undo(tab):
    assert tab.page_layer("watermark", L.Watermark(text="ENTWURF"), wait=True)
    assert "ENTWURF" in text(tab.data, 2)
    assert_valid_pdf(tab.data)
    tab.undo()
    assert kinds(tab) == []
    tab.redo()
    assert kinds(tab) == ["watermark"]


def test_header_uses_file_name_and_update_after_new_page(tab):
    assert tab.page_layer("header", L.HeaderFooter(header_left="{dateiname}", footer_center="{seite}/{seiten}"),
                          wait=True)
    assert "bericht.pdf" in text(tab.data, 0) and "3/3" in text(tab.data, 2)
    tab.apply(insert_blank(tab.data, 3))
    assert tab.update_page_layers(wait=True)
    assert "4/4" in text(tab.data, 3)


def test_error_in_background_is_reported_not_raised(tab, monkeypatch):
    from notex.ui import dialogs
    warned = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warned.append(a))
    before = tab.data
    assert not tab.page_layer("watermark", L.Watermark(text="X", pages="9"), wait=True)
    assert tab.data is before and warned


def test_remove_by_kind_and_all(tab):
    tab.page_layer("watermark", L.Watermark(text="W"), wait=True)
    tab.page_layer("background", L.Background(color="#eeeeee"), wait=True)
    assert kinds(tab) == ["background", "watermark"]
    assert tab.remove_page_layers("background")
    assert kinds(tab) == ["watermark"]
    assert tab.remove_page_layers()
    assert kinds(tab) == []
    assert not tab.remove_page_layers()                                # nichts mehr da: Meldung, keine Änderung


def test_dialog_builds_spec_and_preview(tab):
    from notex.ui.pdf_layers_ui import PageLayerDialog
    dialog = PageLayerDialog(tab, "watermark", 3, 0, preview=tab.layer_preview)
    dialog.fields["text"].setPlainText("GEHEIM")
    dialog.fields["opacity"].setValue(50)
    dialog.fields["rotation"].setValue(-30)
    dialog.pages.setText("2-3")
    spec = dialog.spec()
    assert (spec.text, spec.opacity, spec.rotation, spec.pages) == ("GEHEIM", 0.5, 330, "2-3")
    dialog.preview_page.setValue(2)
    dialog.update_preview()
    assert dialog.preview_label.pixmap() is not None and not dialog.preview_label.pixmap().isNull()
    dialog.fields["text"].setPlainText("")
    dialog.update_preview()
    assert "Text" in dialog.error.text()


def test_dialog_header_preset_and_image_requirement(tab, win):
    from notex.ui.pdf_layers_ui import PageLayerDialog
    dialog = PageLayerDialog(tab, "header", 3, preview=tab.layer_preview)
    dialog.fields["preset"].setCurrentIndex(3)
    dialog._preset(3)
    assert dialog.fields["footer_center"].text() == L.NUMBER_PRESETS[2]
    background = PageLayerDialog(tab, "background", 3, preview=tab.layer_preview)
    background.fields["image"].setChecked(True)
    background._accept()
    assert "Bild" in background.error.text() and background.result() != QDialog.DialogCode.Accepted
    path = win.root / "papier.png"
    path.write_bytes(picture("PNG", (40, 20)))
    assert background.choose_image(str(path))
    assert background.image_label.text() == "papier.png"
    background._accept()
    assert background.result() == QDialog.DialogCode.Accepted


def test_templates_saved_with_image_and_reloaded(tab, win):
    from notex.paths import stamps_dir
    from notex.ui.pdf_layers_ui import PageLayerDialog
    dialog = PageLayerDialog(tab, "watermark", 3, preview=None, image_folder=stamps_dir())
    path = win.root / "logo.png"
    path.write_bytes(picture("PNG", (40, 20)))
    dialog.choose_image(str(path))
    dialog.fields["scale"].setValue(30)
    assert dialog.save_template("Logo klein")
    entry = dialog.saved_templates[0]
    assert entry["name"] == "Logo klein" and (stamps_dir() / entry["image"]).is_file()
    tab._store_layer_templates("watermark", dialog.saved_templates)
    again = PageLayerDialog(tab, "watermark", 3, preview=None, templates=tab._layer_templates("watermark"),
                            image_folder=stamps_dir())
    again.template_box.setCurrentIndex(1)
    again._template_chosen(1)
    spec = again.spec()
    assert spec.image and spec.scale == 0.3 and again.image == path.read_bytes()
    again._delete_template()
    assert again.saved_templates == []


def test_page_layer_via_dialog(tab, monkeypatch):
    from notex.ui.pdf_layers_ui import PageLayerDialog

    def accept(self):
        self.fields["text"].setPlainText("PER DIALOG")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PageLayerDialog, "exec", accept)
    monkeypatch.setattr(tab, "_background_change",
                        lambda title, change, wait=False: tab._run(lambda d: change(d, None)))
    assert tab.page_layer("watermark")
    assert "PER DIALOG" in text(tab.data, 0)


def test_dialog_opens_with_existing_layer(tab, monkeypatch):
    from notex.ui.pdf_layers_ui import PageLayerDialog
    tab.page_layer("watermark", L.Watermark(text="VORHANDEN", position="tl"), wait=True)
    seen = []

    def look(self):
        seen.append((self.fields["text"].toPlainText(), self.fields["position"].currentData()))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(PageLayerDialog, "exec", look)
    assert not tab.page_layer("watermark")
    assert seen == [("VORHANDEN", "tl")]


def test_foreign_watermarks_need_confirmation(tab):
    from test_pdf_layers import foreign_doc
    tab.apply(foreign_doc())
    before = tab.data
    assert not tab.remove_foreign_watermarks()                       # Rückfrage abgelehnt
    assert tab.data is before
    assert tab.remove_foreign_watermarks(confirmed=True)
    assert L.foreign_watermarks(tab.data) == {}
    assert not tab.remove_foreign_watermarks(confirmed=True)        # nichts mehr da


def test_layers_menu_lists_present_layers(tab):
    tab.page_layer("watermark", L.Watermark(text="MENÜ"), wait=True)
    texts = [action.text() for action in tab.build_layers_menu().actions()]
    assert any("MENÜ" in t for t in texts) and any(t.startswith("Ebenen aktualisieren") for t in texts)


def test_palette_has_layer_commands(win):
    ids = {c.id for c in win.registry.all()}
    assert {"pdf:watermark", "pdf:header_footer", "pdf:background", "pdf:layers_update", "pdf:layers_remove",
            "pdf:foreign_watermarks"} <= ids


def test_config_keeps_templates(tmp_path):
    from notex.core import config
    path = tmp_path / "config.json"
    data = config.default_config()
    data["pdf_layer_templates"]["header"] = [{"name": "Std", "spec": {"kind": "header"}, "image": ""}]
    config.save_config(path, data) if hasattr(config, "save_config") else path.write_text(__import__("json").dumps(data))
    loaded = config.load_config(path)
    assert loaded["pdf_layer_templates"]["header"][0]["name"] == "Std"
