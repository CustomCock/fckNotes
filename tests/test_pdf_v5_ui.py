"""V5 in der Oberfläche: neue Feldtypen per Werkzeug und Dialog, erweiterte Feld-Eigenschaften, Tab-Reihenfolge,
Formulardaten exportieren/importieren, Antworten sammeln."""
import csv
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from notex.core import pdfforms, pdffieldtypes, pdfformdata  # noqa: E402
from pdffixtures import assert_valid_pdf  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


@pytest.fixture
def tab(win):
    path = win.root / "anmeldung.pdf"
    data = make_pdf(["Anmeldung", "Seite zwei"], size=(600, 800), outline=False)
    data = pdfforms.add_text_field(data, 0, (60, 300, 260, 320), "Name")
    path.write_bytes(data)
    page = win.tabs.open_viewer(path, "pdf")
    page.set_editing(True)
    return page


def _accept(monkeypatch, cls, setup):
    monkeypatch.setattr(cls, "exec", lambda self: (setup(self), QDialog.DialogCode.Accepted)[-1])


def infos(tab):
    return {f.name: f for f in pdfforms.list_fields(tab.data)}


def test_new_field_tools_with_dialog(tab, monkeypatch):
    from notex.ui.pdf_forms_ui import NewFieldDialog

    def radio(dialog):
        dialog.name.setText("Kurs")
        dialog.options.setPlainText("A\nB\nC")
    _accept(monkeypatch, NewFieldDialog, radio)
    tab.set_tool("radio")
    assert tab.tool_buttons["formfield"].isChecked()
    tab.canvas.region_chosen.emit(0, QRectF(60, 100, 100, 80))
    assert infos(tab)["Kurs"].options == ["A", "B", "C"] and tab.canvas.tool == "select"

    def combo(dialog):
        dialog.name.setText("Fach")
        dialog.options.setPlainText("Mathe\nDeutsch")
        dialog.editable.setChecked(True)
    _accept(monkeypatch, NewFieldDialog, combo)
    tab.set_tool("combo")
    tab.canvas.region_chosen.emit(0, QRectF(250, 100, 0, 0))                # Klick: Standardgröße
    fach = infos(tab)["Fach"]
    assert fach.combo and fach.editable and fach.rect[2] - fach.rect[0] == pytest.approx(180)

    def button(dialog):
        dialog.action.setCurrentIndex(1)
        dialog.target.setValue(2)
    _accept(monkeypatch, NewFieldDialog, button)
    tab.set_tool("button")
    tab.canvas.region_chosen.emit(0, QRectF(60, 400, 120, 24))
    assert infos(tab)["Schaltfläche"].action == "goto:1"
    tab.undo()
    assert "Schaltfläche" not in infos(tab)
    assert_valid_pdf(tab.data)


def test_new_field_dialog_validates(tab):
    from notex.ui.pdf_forms_ui import NewFieldDialog
    dialog = NewFieldDialog(tab, "listbox", "Liste")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "Eintrag" in dialog.error.text()
    dialog.name.setText("a.b")
    dialog.options.setPlainText("x")
    dialog._accept()
    assert "Punkt" in dialog.error.text()


def test_field_properties_dialog_extended(tab, monkeypatch):
    from notex.ui.pdf_edit import FieldDialog

    def setup(dialog):
        assert dialog.size.value() == 0
        dialog.required.setChecked(True)
        dialog.default.setText("Max")
        dialog.tooltip.setText("Vorname")
        dialog.align.setCurrentIndex(1)
        dialog.size.setValue(11)
    _accept(monkeypatch, FieldDialog, setup)
    obj = next(o for o in tab.objects.objects(0) if o.kind == "field")
    assert tab.field_properties(obj)
    name = infos(tab)["Name"]
    assert (name.required, name.default, name.tooltip, name.quadding, name.size) == (True, "Max", "Vorname", 1, 11)


def test_tab_order_dialog_and_numbers(tab, monkeypatch):
    data = pdfforms.add_text_field(tab.data, 0, (60, 100, 260, 120), "Oben")
    tab.apply(data, 0)
    from notex.ui.pdf_forms_ui import TabOrderDialog
    seen = {}

    def reorder(dialog):
        seen["names"] = dialog.order()
        dialog.list.setCurrentRow(0)
        dialog._move(1)
    _accept(monkeypatch, TabOrderDialog, reorder)
    assert tab.edit_tab_order()
    assert seen["names"] == ["Name", "Oben"]
    assert pdffieldtypes.tab_order(tab.data, 0) == ["Oben", "Name"] and tab.show_tab_numbers
    assert tab.edit_tab_order(mode="rows")
    assert pdffieldtypes.tab_order(tab.data, 0) == ["Oben", "Name"]
    tab.canvas.viewport().repaint()                                   # Nummern zeichnen ohne Fehler
    tab.set_tab_numbers(False)
    assert not tab.show_tab_numbers


def test_form_data_export_import_and_collect(tab, tmp_path, win):
    tab.apply(pdfforms.fill(tab.data, {"Name": "Erika"}), 0)
    for kind in ("json", "xfdf", "fdf"):
        assert tab.export_form_data(kind, tmp_path / f"d.{kind}").exists()
    tab.apply(pdfforms.fill(tab.data, {"Name": ""}), 0)
    notices = []
    tab.notice.connect(notices.append)
    assert tab.import_form_data(tmp_path / "d.fdf")
    assert infos(tab)["Name"].value == "Erika" and notices[-1].startswith("1 Felder übernommen")
    copies = []
    for who in ("Anna", "Ben"):
        path = tmp_path / f"{who}.pdf"
        path.write_bytes(pdfforms.fill(tab.data, {"Name": who}))
        copies.append(path)
    out = tab.collect_responses(copies, tmp_path / "antworten.csv")
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8-sig")), delimiter=";"))
    assert rows == [["Datei", "Name"], ["Anna.pdf", "Anna"], ["Ben.pdf", "Ben"]]
    assert notices[-1] == "2 Antworten gesammelt"


def test_reset_form(tab):
    data = pdffieldtypes.set_properties(tab.data, "Name", default="Max")
    tab.apply(pdfforms.fill(data, {"Name": "Erika"}), 0)
    assert tab.reset_form() and infos(tab)["Name"].value == "Max"


def test_palette_entries(win):
    for key in ("pdf:tool_radio", "pdf:tool_combo", "pdf:tool_listbox", "pdf:tool_button", "pdf:tab_order",
                "pdf:tab_numbers", "pdf:form_export_json", "pdf:form_export_xfdf", "pdf:form_export_fdf",
                "pdf:form_import", "pdf:form_collect", "pdf:form_reset"):
        assert win.registry.get(key) is not None, key


def test_bad_import_warns(tab, tmp_path, monkeypatch):
    from notex.ui import dialogs
    warned = []
    monkeypatch.setattr(dialogs, "warn", lambda *a, **k: warned.append(a))
    bad = tmp_path / "x.fdf"
    bad.write_bytes(b"%FDF-1.2\n1 0 obj << /FDF << /Fields [ << /T (a")
    assert not tab.import_form_data(bad) and warned
    QApplication.processEvents()
    assert pdfformdata.values_of(tab.data) == {"Name": ""}
