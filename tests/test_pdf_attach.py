"""V2 (A3): Anhänge auflisten, extrahieren, hinzufügen, entfernen; Programme erkennen; sichere Dateinamen."""
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
                           TextStringObject)

from notex.core import pdfannot, pdfattach as T, pdfaudit
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf

BINARY = bytes(range(256)) * 40


def _doc():
    return make_pdf(["Seite eins", "Seite zwei"], outline=False)


def test_roundtrip_bytes_identical_with_description_and_type():
    data = T.add_attachment(_doc(), "messwerte.bin", BINARY, "Rohdaten")
    data = T.add_attachment(data, "notiz.txt", "Grüße".encode("utf-8"))
    assert_valid_pdf(data)
    items = {a.name: a for a in T.list_attachments(data)}
    assert set(items) == {"messwerte.bin", "notiz.txt"}
    assert items["messwerte.bin"].description == "Rohdaten" and items["messwerte.bin"].size == len(BINARY)
    assert items["notiz.txt"].mime == "text/plain" and items["notiz.txt"].where == "Dokument"
    assert T.extract(data, "doc:messwerte.bin") == ("messwerte.bin", BINARY)
    assert T.extract(data, "doc:notiz.txt")[1].decode("utf-8") == "Grüße"


def test_same_name_gets_a_number_and_path_parts_are_dropped():
    data = T.add_attachment(_doc(), "../../geheim/bericht.txt", b"eins")
    data = T.add_attachment(data, "bericht.txt", b"zwei")
    assert sorted(a.name for a in T.list_attachments(data)) == ["bericht (2).txt", "bericht.txt"]


def test_remove_deletes_name_and_content():
    data = T.add_attachment(_doc(), "a.txt", b"Inhalt Alpha")
    data = T.add_attachment(data, "b.txt", b"Inhalt Beta")
    once = T.remove_attachment(data, "doc:a.txt")
    assert [a.name for a in T.list_attachments(once)] == ["b.txt"]
    assert pdfaudit.report(once, ["Inhalt Alpha"]) == []         # nirgends mehr, auch nicht als Objekt
    empty = T.remove_attachment(once, "doc:b.txt")
    assert T.list_attachments(empty) == []
    assert "/EmbeddedFiles" not in empty.decode("latin-1")
    assert_valid_pdf(empty)
    with pytest.raises(PdfEditError):
        T.remove_attachment(empty, "doc:b.txt")


def _with_kids_tree() -> bytes:
    """Namensbaum mit /Kids (wie von anderen Programmen) und /AF-Verweis."""
    writer = PdfWriter(clone_from=io.BytesIO(_doc()))
    specs = []
    for name, content in (("x.txt", b"Xaver"), ("y.txt", b"Ypsilon")):
        stream = DecodedStreamObject()
        stream.set_data(content)
        stream[NameObject("/Type")] = NameObject("/EmbeddedFile")
        spec = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Filespec"), NameObject("/F"): TextStringObject(name),
            NameObject("/EF"): DictionaryObject({NameObject("/F"): writer._add_object(stream)})}))
        specs.append((name, spec))
    kids = ArrayObject([writer._add_object(DictionaryObject({
        NameObject("/Names"): ArrayObject([TextStringObject(n), s]),
        NameObject("/Limits"): ArrayObject([TextStringObject(n), TextStringObject(n)])})) for n, s in specs])
    root = writer._root_object
    root[NameObject("/Names")] = DictionaryObject({NameObject("/EmbeddedFiles"): DictionaryObject({
        NameObject("/Kids"): kids})})
    root[NameObject("/AF")] = ArrayObject([specs[0][1]])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_kids_tree_listed_and_removed_with_af():
    data = _with_kids_tree()
    assert [a.name for a in T.list_attachments(data)] == ["x.txt", "y.txt"]
    out = T.remove_attachment(data, "doc:x.txt")
    assert [a.name for a in T.list_attachments(out)] == ["y.txt"]
    assert "/AF" not in out.decode("latin-1")
    assert pdfaudit.report(out, ["Xaver"]) == []
    assert T.extract(out, "doc:y.txt")[1] == b"Ypsilon"


def _file_annotation() -> bytes:
    writer = PdfWriter(clone_from=io.BytesIO(_doc()))
    stream = DecodedStreamObject()
    stream.set_data(b"Beilage auf Seite zwei")
    stream[NameObject("/Type")] = NameObject("/EmbeddedFile")
    spec = DictionaryObject({NameObject("/Type"): NameObject("/Filespec"),
                             NameObject("/UF"): TextStringObject("beilage.txt"),
                             NameObject("/EF"): DictionaryObject({NameObject("/F"): writer._add_object(stream)})})
    annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                              NameObject("/Subtype"): NameObject("/FileAttachment"),
                              NameObject("/Rect"): pdfannot._floats([50, 50, 70, 70]),
                              NameObject("/F"): NumberObject(4), NameObject("/FS"): spec})
    writer.pages[1][NameObject("/Annots")] = ArrayObject([writer._add_object(annot)])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_file_attachment_annotation_listed_extracted_removed():
    data = _file_annotation()
    (item,) = T.list_attachments(data)
    assert item.key == "annot:1:0" and item.page == 1 and item.where == "Seite 2" and item.name == "beilage.txt"
    assert T.extract(data, item.key)[1] == b"Beilage auf Seite zwei"
    out = T.remove_attachment(data, item.key)
    assert T.list_attachments(out) == []
    assert pdfaudit.report(out, ["Beilage auf Seite"]) == []


@pytest.mark.parametrize("name, head, expected", [
    ("tool.exe", b"", True), ("setup.MSI", b"", True), ("bild.png.lnk", b"", True), ("makro.docm", b"", True),
    ("harmlos.txt", b"MZ\x90\x00", True), ("daten.bin", b"\x7fELF", True), ("skript", b"#!/bin/sh", True),
    ("brief.pdf", b"%PDF-1.7", False), ("tabelle.xlsx", b"PK\x03\x04", False), ("foto.jpg", b"\xff\xd8", False),
])
def test_risky_detects_programs_by_name_and_header(name, head, expected):
    assert T.risky(name, head) is expected


def test_list_marks_programs_as_risky():
    data = T.add_attachment(_doc(), "rechnung.pdf.exe", b"MZ\x90\x00rest")
    assert T.list_attachments(data)[0].risky


@pytest.mark.parametrize("raw, safe", [
    ("../../etc/passwd", "passwd"), ("C:\\Windows\\system32\\evil.dll", "evil.dll"), ("..", "anhang"),
    ("", "anhang"), ("CON.txt", "_CON.txt"), ("a\x00b?.pdf", "a_b_.pdf"), (" punkt. ", "punkt"),
])
def test_safe_filename(raw, safe):
    assert T.safe_filename(raw) == safe


def test_too_large_attachment_rejected(monkeypatch):
    monkeypatch.setattr(T, "MAX_ATTACHMENT", 10)
    with pytest.raises(PdfEditError):
        T.add_attachment(_doc(), "gross.bin", b"x" * 11)
