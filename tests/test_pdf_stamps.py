"""V3 (A10, Teil 1): Stempel – Standard und eigene (Text/Bild, portabel gespeichert), Datum und Name, aufrecht auf
gedrehten Seiten, Schrift eingebettet, verschiebbar wie andere Objekte."""
import io
import json

import pytest
from pypdf import PdfReader
from pypdf.generic import ContentStream

from notex.core import pdfannot as A, pdfaudit, pdfobjects, pdfpages, pdfstamps as S
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
IMAGE = (2, 1, bytes([255, 0, 0, 0, 0, 255]), bytes([255, 128]))


def _doc():
    return make_pdf(["Seite"], size=(600, 800), outline=False)


def _stamp(data, position=-1):
    reader = PdfReader(io.BytesIO(data))
    return reader, reader.pages[0]["/Annots"][position].get_object()


@pytest.mark.parametrize("spec", S.BUILTIN, ids=[s.key for s in S.BUILTIN])
def test_builtin_stamps(spec):
    data = S.add_stamp(_doc(), 0, (100, 100, 100, 100), spec)
    assert_valid_pdf(data)
    reader, annot = _stamp(data)
    assert annot["/Subtype"] == "/Stamp" and annot["/Contents"] == spec.text
    appearance = annot["/AP"]["/N"].get_object()
    ops = [op.decode() for _a, op in ContentStream(appearance, reader).operations]
    assert {"Tj", "Tf", "c", "S"} <= set(ops)
    font = appearance["/Resources"]["/Font"]["/FnB"].get_object()
    assert font["/Subtype"] == "/Type0" and "NotoSans" in font["/BaseFont"]
    (obj,) = pdfobjects.list_objects(data, 0)
    assert obj.kind == "stamp" and obj.movable and obj.keep_aspect
    w, h = S.natural_size(spec)
    assert obj.rect == pytest.approx((100, 100, 100 + w, 100 + h), abs=0.01)    # Klick = natürliche Größe


def test_date_and_user_line_and_audit():
    spec = S.StampSpec("approved", "GENEHMIGT", "#1f7a3a", with_date=True, with_user=True)
    data = S.add_stamp(_doc(), 0, (50, 50, 50, 50), spec, "11.10.2026 18:00", "Mia")
    _r, annot = _stamp(data)
    assert annot["/Contents"] == "GENEHMIGT (11.10.2026 18:00  ·  Mia)" and annot["/T"] == "Mia"
    assert S.natural_size(spec, "11.10.2026 18:00  ·  Mia")[1] > S.natural_size(S.BUILTIN[0])[1]
    assert pdfaudit.report(data, ["Mia"])                                          # Name ist ein Textort
    plain = S.add_stamp(_doc(), 0, (50, 50, 50, 50), S.BUILTIN[0], "11.10.2026", "Mia")
    assert "/T" not in _stamp(plain)[1] and _stamp(plain)[1]["/Contents"] == "GENEHMIGT"


def test_dragged_rect_keeps_aspect_and_upright_on_rotated_page():
    w, h = S.natural_size(S.BUILTIN[1])
    assert S.fit((10, 10, 10 + w * 3, 10 + h * 10), (w, h)) == pytest.approx((10, 10, 10 + w * 3, 10 + h * 3))
    rotated = pdfpages.rotate(_doc(), [0], 90)
    data = S.add_stamp(rotated, 0, (100, 100, 100, 100), S.BUILTIN[1])
    _r, annot = _stamp(data)
    assert [float(v) for v in annot["/AP"]["/N"]["/Matrix"]] == [0, 1, -1, 0, 0, 0]
    (info,) = A.list_annotations(data, 0)
    assert info.rect == pytest.approx((100, 100, 100 + w, 100 + h), abs=0.01)


def test_image_stamp_with_alpha_and_opacity():
    spec = S.StampSpec("custom-a", "", "#000000", "a.png", with_date=True, custom=True)
    data = S.add_stamp(_doc(), 0, (10, 10, 210, 300), spec, "11.10.2026", image=IMAGE, opacity=0.7)
    assert_valid_pdf(data)
    _r, annot = _stamp(data)
    res = annot["/AP"]["/N"]["/Resources"]
    image = res["/XObject"]["/Im0"].get_object()
    assert (image["/Width"], image["/Height"]) == (2, 1) and "/SMask" in image
    assert float(res["/ExtGState"]["/GS0"]["/ca"]) == 0.7 and float(annot["/CA"]) == 0.7
    with pytest.raises(PdfEditError):
        S.add_stamp(_doc(), 0, (10, 10, 10, 10), spec)                            # Bild fehlt
    with pytest.raises(PdfEditError):
        S.add_stamp(_doc(), 0, (10, 10, 10, 10), spec, image=(2, 2, b"\x00" * 3, None))


def test_custom_stamps_are_stored_portably(tmp_path):
    folder = tmp_path / "stamps"
    text = S.save_custom(folder, "  Zur   Ablage ", "#123456", with_date=True)
    image = S.save_custom(folder, "", "#000000", PNG)
    specs = S.load_custom(folder)
    assert [s.text for s in specs] == ["Zur Ablage", ""] and all(s.custom for s in specs)
    assert specs[0].with_date and specs[0].color == "#123456"
    assert specs[1].image.endswith(".png") and (folder / specs[1].image).read_bytes() == PNG
    S.delete_custom(folder, image.key)
    assert [s.key for s in S.load_custom(folder)] == [text.key] and not (folder / image.image).exists()
    with pytest.raises(PdfEditError):
        S.delete_custom(folder, image.key)


@pytest.mark.parametrize("case", ["leer", "gif", "zu-gross"])
def test_invalid_custom_stamps(tmp_path, case):
    # Bytes erst hier erzeugen: lange Parameter landen sonst im Testnamen (PYTEST_CURRENT_TEST, unter Windows max. 32 767)
    text, image = {"leer": ("", None), "gif": ("", b"GIF89a....."),
                   "zu-gross": ("x", b"\x89PNG\r\n\x1a\n" + b"0" * (6 << 20))}[case]
    with pytest.raises(PdfEditError):
        S.save_custom(tmp_path, text, "#000000", image)


def test_broken_index_and_foreign_paths_are_ignored(tmp_path):
    (tmp_path / "stamps.json").write_text(json.dumps([
        {"key": "a", "text": "Gut", "color": "kein-farbwert"},
        {"key": "b", "text": "", "image": "../../geheim.png"},
        {"key": "c", "text": "", "image": "fehlt.png"},
        "unsinn", {"text": "ohne key"}]), encoding="utf-8")
    specs = S.load_custom(tmp_path)
    assert [(s.key, s.color) for s in specs] == [("a", "#c02020")]
    (tmp_path / "stamps.json").write_text("{kaputt", encoding="utf-8")
    assert S.load_custom(tmp_path) == []
    assert S.load_custom(tmp_path / "gibt-es-nicht") == []


def test_sub_line():
    spec = S.StampSpec("x", "X", with_date=True, with_user=True)
    assert S.sub_line(spec, "1.1.2027", "") == "1.1.2027"
    assert S.sub_line(S.StampSpec("x", "X"), "1.1.2027", "Mia") == ""
    with pytest.raises(PdfEditError):
        S.builtin("gibt-es-nicht")
    assert S.builtin("draft").text == "ENTWURF"


def test_invalid_color_rejected(tmp_path):
    with pytest.raises(PdfEditError):
        S.save_custom(tmp_path, "Text", "rot")
