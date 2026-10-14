"""V5: Formulardaten – JSON/XFDF/FDF-Rundlauf, sicheres Lesen, Antworten aus drei ausgefüllten Kopien sammeln."""
import csv
import io
import json

import pytest

from notex.core import pdfforms as F, pdffieldtypes as T, pdfformdata as D
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def _blank():
    d = make_pdf(["Anmeldung"], size=(600, 800), outline=False)
    d = F.add_text_field(d, 0, (60, 100, 260, 120), "Name")
    d = F.add_text_field(d, 0, (60, 130, 260, 200), "Bemerkung", multiline=True)
    d = F.add_checkbox(d, 0, (60, 210, 74, 224), "Zustimmung")
    d = T.add_radio_group(d, 0, T.layout_radios((300, 100, 320, 160), 3), "Kurs", ["A", "B", "Ü-Kurs"])
    d = T.add_choice(d, 0, (300, 200, 450, 220), "Fach", ["Mathe", "Deutsch"], True)
    d = T.add_button(d, 0, (60, 300, 200, 320), "Reset", "Zurücksetzen")
    return d


VALUES = {"Name": "Jörg (Müller)", "Bemerkung": "Zeile 1\nZeile 2 \\ Ende", "Zustimmung": True, "Kurs": "Ü-Kurs",
          "Fach": "Deutsch"}


def _filled(values=VALUES):
    return F.fill(_blank(), values)


@pytest.mark.parametrize("export, kind", [(D.export_json, "json"), (D.export_xfdf_fields, "xfdf"),
                                          (D.export_fdf, "fdf")])
def test_roundtrip(export, kind):
    raw = export(_filled(), "anmeldung.pdf")
    result = D.import_data(_blank(), raw)                         # Format am Inhalt erkannt
    assert result.skipped == [] and result.filled == 5
    assert D.values_of(result.data) == VALUES
    assert_valid_pdf(result.data)
    assert D.import_data(_blank(), raw, kind).filled == 5


def test_json_shape_and_flat_input():
    payload = json.loads(D.export_json(_filled(), "a.pdf"))
    assert payload["format"] == D.FORMAT and payload["fields"]["Zustimmung"] is True
    assert "Reset" not in payload["fields"]
    flat = json.dumps({"Name": "Erika", "Zustimmung": "nein", "Gibt es nicht": "x", "Fach": "Kunst",
                       "Liste": [1, 2]}).encode()
    result = D.import_data(_filled(), flat)
    values = D.values_of(result.data)
    assert values["Name"] == "Erika" and values["Zustimmung"] is False and values["Fach"] == "Deutsch"
    assert any("Gibt es nicht" in s for s in result.skipped) and any("Fach" in s for s in result.skipped)
    assert "übersprungen" in result.summary()


def test_xfdf_nested_names_and_acrobat_style():
    raw = b"""<?xml version="1.0" encoding="UTF-8"?>
    <xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve"><fields>
      <field name="Name"><value>Ana</value></field>
      <field name="Zustimmung"><value>Yes</value></field>
      <field name="Kurs"><value>B</value></field>
      <field name="Gruppe"><field name="Feld"><value>x</value></field></field>
    </fields><f href="Anmeldung.pdf"/></xfdf>"""
    assert D.parse_xfdf_fields(raw) == {"Name": "Ana", "Zustimmung": "Yes", "Kurs": "B", "Gruppe.Feld": "x"}
    result = D.import_data(_blank(), raw)
    assert result.filled == 3 and result.skipped == ["Gruppe.Feld: kein Feld"]
    with pytest.raises(D.PdfEditError):
        D.import_data(_blank(), b'<!DOCTYPE x [<!ENTITY a "a">]><xfdf/>')


def test_fdf_parser_handles_escapes_hex_kids_and_refs():
    raw = (b"%FDF-1.2\n%comment\n1 0 obj\n<< /FDF << /F (x.pdf) /Fields [ << /T (Name) /V (A \\(b\\) \\101\\n) >> "
           b"<< /T (Zustimmung) /V /Yes >> << /T <FEFF004B007500720073> /V /#C3#9C-Kurs >> "
           b"<< /T (Eltern) /Kids [ << /T (Kind) /V <FEFF00DC> >> ] >> << /T (Ref) /V 3 0 R >> ] >> >>\n"
           b"endobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF")
    assert D.parse_fdf(raw) == {"Name": "A (b) A\n", "Zustimmung": "Yes", "Kurs": "Ü-Kurs", "Eltern.Kind": "Ü",
                                "Ref": None}


@pytest.mark.parametrize("raw", [
    b"kein fdf",
    b"%FDF-1.2\n1 0 obj << /FDF << /Fields [ << /T (a) /V (offen",
    b"%FDF-1.2\n1 0 obj << /Length 5 >> stream\nhallo\nendstream endobj",
    b"%FDF-1.2\n1 0 obj " + b"[" * 100 + b"]" * 100,
    b"%FDF-1.2\n1 0 obj << /FDF 5 >>",
])
def test_fdf_rejects_broken_or_unsafe(raw):
    with pytest.raises(D.FormDataError):
        D.parse_fdf(raw)


def test_size_limit(monkeypatch):
    monkeypatch.setattr(D, "MAX_FILE", 10)
    with pytest.raises(D.FormDataError):
        D.parse_json(b'{"Name": "zu lang"}')


def test_read_only_fields_are_skipped():
    data = T.set_properties(_blank(), "Name", read_only=True)
    result = D.apply_values(data, {"Name": "x", "Fach": "Mathe"})
    assert result.filled == 1 and result.skipped == ["Name: schreibgeschützt"]


def test_collect_from_three_filled_copies():
    copies = [("anna.pdf", _filled({**VALUES, "Name": "Anna"})),
              ("ben.pdf", _filled({**VALUES, "Name": "=Ben", "Zustimmung": False, "Kurs": "A"})),
              ("cem.pdf", _filled({**VALUES, "Name": "Cem", "Fach": "Mathe"})),
              ("leer.pdf", make_pdf(["ohne Formular"], outline=False))]
    text, problems = D.collect(copies)
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0] == ["Datei", "Name", "Bemerkung", "Zustimmung", "Kurs", "Fach"]
    assert [r[0] for r in rows[1:]] == ["anna.pdf", "ben.pdf", "cem.pdf"]
    assert rows[2][1] == "'=Ben" and rows[2][3] == "Nein" and rows[2][4] == "A"
    assert rows[3][5] == "Mathe" and rows[1][3] == "Ja"
    assert problems == ["leer.pdf: keine Formularfelder"]
