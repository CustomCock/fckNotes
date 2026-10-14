"""V2 (A8): Links anlegen (Seite, Webadresse, Datei), lesen, ändern, verschieben, löschen; unsichere Ziele ablehnen."""
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from notex.core import pdfannot, pdfaudit, pdflinks as L, pdfobjects, pdfpages
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def _doc(pages=3):
    return make_pdf([f"Seite {i + 1}" for i in range(pages)], outline=False)


def test_all_target_kinds_roundtrip():
    data = _doc()
    data = L.add_link(data, 0, (50, 50, 200, 80), L.LinkTarget("page", 2, 500.0), border=True, color="#ff0000")
    data = L.add_link(data, 0, (50, 100, 200, 130), L.LinkTarget("page", 1))
    data = L.add_link(data, 1, (50, 100, 200, 130), L.LinkTarget("url", url="example.org/pfad?q=1"))
    data = L.add_link(data, 1, (50, 150, 200, 180), L.LinkTarget("url", url="max@example.org"))
    data = L.add_link(data, 2, (50, 150, 200, 180), L.LinkTarget("file", file="anhang\\zwei.pdf", file_page=3))
    assert_valid_pdf(data)
    first, second = L.list_links(data, 0)
    assert first.target == L.LinkTarget("page", 2, 500.0) and first.border and first.color == "#ff0000"
    assert first.rect == pytest.approx((50, 50, 200, 80)) and first.ours
    assert second.target == L.LinkTarget("page", 1) and not second.border
    urls = [link.target.url for link in L.list_links(data, 1)]
    assert urls == ["https://example.org/pfad?q=1", "mailto:max@example.org"]
    (file_link,) = L.list_links(data, 2)
    assert file_link.target.kind == "file" and file_link.target.file == "anhang/zwei.pdf"
    assert file_link.target.file_page == 3 and "Seite 4" in file_link.target.describe()
    raw = data.decode("latin-1")
    assert "/Launch" not in raw and "/JavaScript" not in raw
    assert len(L.all_links(data)) == 5


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://", "a b", "",
                                 "data:text/html,x", "smb://server/freigabe"])
def test_unsafe_or_broken_urls_rejected(url):
    with pytest.raises(PdfEditError):
        L.add_link(_doc(), 0, (50, 50, 200, 80), L.LinkTarget("url", url=url))


@pytest.mark.parametrize("target", [L.LinkTarget("file", file="programm.exe"), L.LinkTarget("file", file=""),
                                    L.LinkTarget("page", 7), L.LinkTarget("other")])
def test_invalid_targets_rejected(target):
    with pytest.raises(PdfEditError):
        L.add_link(_doc(), 0, (50, 50, 200, 80), target)


def test_too_small_rejected():
    with pytest.raises(PdfEditError):
        L.add_link(_doc(), 0, (50, 50, 52, 80), L.LinkTarget("page", 1))


def test_rotated_page_rect_roundtrip():
    rotated = pdfpages.rotate(_doc(), [1], 90)
    data = L.add_link(rotated, 1, (30, 40, 130, 90), L.LinkTarget("page", 0))
    assert L.list_links(data, 1)[0].rect == pytest.approx((30, 40, 130, 90))


def test_update_target_and_border():
    data = L.add_link(_doc(), 0, (50, 50, 200, 80), L.LinkTarget("page", 2), border=True)
    data = L.update_link(data, 0, 0, L.LinkTarget("url", url="https://example.org"), border=False)
    link = L.list_links(data, 0)[0]
    assert link.target.url == "https://example.org" and not link.border
    assert "/Dest" not in PdfReader(io.BytesIO(data)).pages[0]["/Annots"][0].get_object()


def _foreign_link() -> bytes:
    """Link eines anderen Programms: benanntes Ziel und eine fremde Aktion."""
    writer = PdfWriter(clone_from=io.BytesIO(_doc()))
    writer.add_named_destination("kapitel3", 2)
    named = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                              NameObject("/Rect"): pdfannot._floats([10, 10, 60, 40]),
                              NameObject("/Dest"): TextStringObject("kapitel3")})
    launch = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                               NameObject("/Rect"): pdfannot._floats([10, 100, 60, 140]),
                               NameObject("/Border"): ArrayObject([NumberObject(0)] * 3),
                               NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/Launch"),
                                                                   NameObject("/F"): TextStringObject("calc.exe")})})
    writer.pages[0][NameObject("/Annots")] = ArrayObject([writer._add_object(named), writer._add_object(launch)])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_foreign_links_read_and_kept_when_moved():
    data = _foreign_link()
    named, launch = L.list_links(data, 0)
    assert named.target.kind == "page" and named.target.page == 2
    assert launch.target.kind == "other" and not launch.border and launch.target.describe() == "andere Aktion"
    view_rect = launch.rect
    moved = pdfobjects.set_rect(data, 0, launch.index, (view_rect[0] + 20, view_rect[1], view_rect[2] + 20,
                                                        view_rect[3]))
    after = L.list_links(moved, 0)[1]
    assert after.rect[0] == pytest.approx(view_rect[0] + 20) and after.target.kind == "other"
    annot = PdfReader(io.BytesIO(moved)).pages[0]["/Annots"][1].get_object()
    assert annot["/A"]["/S"] == "/Launch"                       # unverändert, nie ausgeführt
    kept = L.update_link(moved, 0, 1, None, border=True)       # nur Rahmen: Aktion bleibt
    assert PdfReader(io.BytesIO(kept)).pages[0]["/Annots"][1].get_object()["/A"]["/S"] == "/Launch"


def test_links_are_objects_movable_and_deletable():
    data = L.add_link(_doc(), 0, (50, 50, 200, 80), L.LinkTarget("page", 2), border=True)
    (obj,) = [o for o in pdfobjects.list_objects(data, 0) if o.kind == "link"]
    assert obj.movable and obj.label == "Link → Seite 3"
    moved = pdfobjects.set_rect(data, 0, obj.index, (60, 60, 260, 100))
    link = L.list_links(moved, 0)[0]
    assert link.rect == pytest.approx((60, 60, 260, 100)) and link.target.page == 2 and link.border
    gone = pdfobjects.delete_object(moved, 0, obj.index)
    assert L.list_links(gone, 0) == []
    assert_valid_pdf(gone)


def test_audit_finds_secret_in_link_url():
    data = L.add_link(_doc(), 1, (50, 50, 200, 80), L.LinkTarget("url", url="https://example.org/Mustermann"))
    assert pdfaudit.report(data, ["Mustermann"]) == ["„Mustermann“ in einem Link auf Seite 2"]


def test_delete_requires_a_link():
    data = pdfannot.add_note(_doc(), 0, 100, 100, "Notiz")
    with pytest.raises(PdfEditError):
        L.delete_link(data, 0, 0)
