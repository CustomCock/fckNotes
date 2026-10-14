"""V5 (A11): neue Feldtypen, Eigenschaften und Tab-Reihenfolge – jedes Feld in pypdf gelesen wie geschrieben,
Optionsgruppe hat genau einen Wert, /Tabs und /Annots passen zusammen."""
import io

import pytest
from pypdf import PdfReader

from notex.core import pdfannot as A, pdfforms as F, pdffieldtypes as T
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def _doc(pages=2):
    return make_pdf([f"Seite {i + 1}" for i in range(pages)], size=(600, 800), outline=False)


def _form():
    d = T.add_radio_group(_doc(), 0, T.layout_radios((60, 100, 200, 160), 3), "Note",
                          ["Sehr gut", "Gut", "Möglich"], "Gut", required=True)
    d = T.add_choice(d, 0, (250, 100, 400, 120), "Fach", ["Mathe", "Deutsch"], True, "Deutsch")
    d = T.add_choice(d, 0, (250, 140, 400, 200), "Kurs", ["A", "B", "C"], False, "B")
    d = T.add_choice(d, 0, (250, 220, 400, 240), "Ort", ["Berlin"], True, "Bonn", editable=True)
    d = T.add_button(d, 0, (60, 300, 200, 325), "Reset", "Zurücksetzen")
    d = T.add_button(d, 0, (220, 300, 360, 325), "Weiter", "Weiter", "goto", 1)
    return d


def _fields(data):
    return PdfReader(io.BytesIO(data)).get_fields()


def test_every_field_reads_back_in_pypdf_as_written():
    data = _form()
    assert_valid_pdf(data)
    fields = _fields(data)
    assert fields["Note"]["/FT"] == "/Btn" and fields["Note"]["/V"] == "/Gut"
    assert int(fields["Note"]["/Ff"]) & F.FF_RADIO and int(fields["Note"]["/Ff"]) & F.FF_REQUIRED
    assert fields["Fach"]["/FT"] == "/Ch" and fields["Fach"]["/V"] == "Deutsch"
    assert list(fields["Fach"]["/Opt"]) == ["Mathe", "Deutsch"] and int(fields["Fach"]["/Ff"]) & F.FF_COMBO
    assert not int(fields["Kurs"].get("/Ff", 0)) & F.FF_COMBO and fields["Kurs"]["/V"] == "B"
    assert fields["Ort"]["/V"] == "Bonn" and int(fields["Ort"]["/Ff"]) & F.FF_EDIT
    assert int(fields["Reset"]["/Ff"]) & F.FF_PUSHBUTTON
    infos = {f.name: f for f in F.list_fields(data)}
    assert infos["Note"].options == ["Sehr gut", "Gut", "Möglich"] and infos["Note"].value == "Gut"
    assert infos["Fach"].combo and not infos["Kurs"].combo and infos["Ort"].editable
    assert infos["Reset"].action == "reset" and infos["Weiter"].action == "goto:1"
    raw = data.decode("latin-1")
    assert "/JavaScript" not in raw and "/JS" not in raw and "/Launch" not in raw


def test_radio_group_has_exactly_one_value():
    data = F.fill(_form(), {"Note": "Sehr gut"})
    reader = PdfReader(io.BytesIO(data))
    states = [str(a.get_object()["/AS"]) for a in reader.pages[0]["/Annots"]
              if a.get_object().get("/Parent") is not None]
    assert states.count("/Off") == 2 and sum(s != "/Off" for s in states) == 1
    assert {f.name: f.value for f in F.list_fields(data)}["Note"] == "Sehr gut"
    with pytest.raises(PdfEditError):
        F.fill(data, {"Note": "Ungenügend"})


@pytest.mark.parametrize("call", [
    lambda d: T.add_radio_group(d, 0, T.layout_radios((0, 0, 100, 100), 2), "R", ["a", "b"], "c"),
    lambda d: T.add_radio_group(d, 0, T.layout_radios((0, 0, 100, 100), 1), "R", ["a", "b"]),
    lambda d: T.add_radio_group(d, 0, [], "R", []),
    lambda d: T.add_radio_group(d, 0, T.layout_radios((0, 0, 100, 100), 1), "a.b", ["x"]),
    lambda d: T.add_choice(d, 0, (0, 0, 100, 20), "C", ["a"], True, "z"),
    lambda d: T.add_choice(d, 0, (0, 0, 4, 4), "C", ["a"]),
    lambda d: T.add_button(d, 0, (0, 0, 100, 20), "B", "   "),
    lambda d: T.add_button(d, 0, (0, 0, 100, 20), "B", "Los", "javascript"),
    lambda d: T.add_button(d, 0, (0, 0, 100, 20), "B", "Los", "goto", 9),
    lambda d: T.add_choice(d, 0, (0, 0, 100, 20), "Note", ["a"]),            # Name gibt es schon
])
def test_invalid_fields_rejected(call):
    with pytest.raises(PdfEditError):
        call(_form())


def test_properties_roundtrip_and_reset():
    data = F.add_text_field(_form(), 1, (60, 60, 260, 80), "Name")
    data = T.set_properties(data, "Name", required=True, default="Max", tooltip="Vorname", quadding=2, size=10)
    data = T.set_properties(data, "Kurs", options=["A", "C", "D"], default="C")          # B fällt weg → leer
    data = T.set_properties(data, "Note", read_only=True, default="Möglich")
    data = T.set_properties(data, "Reset", label="Alles leeren")
    infos = {f.name: f for f in F.list_fields(data)}
    name = infos["Name"]
    assert (name.required, name.default, name.tooltip, name.quadding, name.size) == (True, "Max", "Vorname", 2, 10)
    assert infos["Kurs"].options == ["A", "C", "D"] and infos["Kurs"].value == "" and infos["Kurs"].default == "C"
    assert infos["Note"].read_only and infos["Note"].default == "Möglich"
    mk = next(a.get_object() for a in PdfReader(io.BytesIO(data)).pages[0]["/Annots"]
              if a.get_object().get("/T") == "Reset")["/MK"]
    assert mk["/CA"] == "Alles leeren"
    filled = F.fill(data, {"Name": "Erika", "Kurs": "D", "Fach": "Mathe"})
    reset = T.reset_values(filled)
    values = {f.name: f.value for f in F.list_fields(reset)}
    assert values["Name"] == "Max" and values["Kurs"] == "C" and values["Fach"] == "" and values["Note"] == "Gut"
    assert_valid_pdf(reset)
    with pytest.raises(PdfEditError):
        T.set_properties(data, "Name", options=["x"])
    with pytest.raises(PdfEditError):
        T.set_properties(data, "Note", default="Gibt es nicht")
    with pytest.raises(PdfEditError):
        T.set_properties(data, "Name", size=100)


def test_listbox_appearance_marks_selection():
    data = _form()
    widget = next(a.get_object() for a in PdfReader(io.BytesIO(data)).pages[0]["/Annots"]
                  if a.get_object().get("/T") == "Kurs")
    content = widget["/AP"]["/N"].get_object().get_data().decode("latin-1")
    assert content.count("Tj") == 3 and "0.6 0.75 0.95 rg" in content


def _annot_names(data, page=0):
    out = []
    for ref in PdfReader(io.BytesIO(data)).pages[page]["/Annots"]:
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Widget":
            out.append(F._qualified(F._field_of(obj)))
        else:
            out.append(str(obj["/Subtype"]))
    return out


def test_tab_order_custom_rows_columns_consistent():
    data = A.add_note(_form(), 0, 500, 500, "Notiz bleibt")
    assert T.tab_order(data, 0) == ["Note", "Fach", "Kurs", "Ort", "Reset", "Weiter"]
    custom = T.set_tab_order(data, 0, ["Weiter", "Fach", "Note"])
    assert T.tab_order(custom, 0) == ["Weiter", "Fach", "Note", "Kurs", "Ort", "Reset"]
    page = PdfReader(io.BytesIO(custom)).pages[0]
    assert "/Tabs" not in page and _annot_names(custom)[0] == "/Text"            # Notiz bleibt erhalten
    assert _annot_names(custom).count("Note") == 3                               # Optionsfelder beisammen
    rows = T.auto_tab_order(custom, 0, "rows")
    assert PdfReader(io.BytesIO(rows)).pages[0]["/Tabs"] == "/R"
    assert T.tab_order(rows, 0) == ["Note", "Fach", "Kurs", "Ort", "Reset", "Weiter"]
    columns = T.auto_tab_order(custom, 0, "columns")
    assert PdfReader(io.BytesIO(columns)).pages[0]["/Tabs"] == "/C"
    assert T.tab_order(columns, 0) == ["Note", "Reset", "Weiter", "Fach", "Kurs", "Ort"]   # x: 60, 60, 220, 250…
    assert_valid_pdf(columns)
    with pytest.raises(PdfEditError):
        T.auto_tab_order(data, 0, "diagonal")


def test_display_copy_and_flatten_handle_new_fields():
    data = F.fill(_form(), {"Note": "Möglich"})
    assert F.display_copy(data) is not None
    flat = F.flatten(data)
    assert F.list_fields(flat) == [] and _fields(flat) in (None, {})
    assert_valid_pdf(flat)
