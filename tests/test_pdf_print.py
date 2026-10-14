"""Block V11: Drucken vorbereiten (Seiten, Anmerkungen/Formular, Platzierung, Kopien) und „In PDF drucken“."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from notex.core import pdfannot as A
from notex.core import pdfforms as F
from notex.core import pdfprint as P
from notex.core.pdfpages import PdfEditError, rotate
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def doc(pages: int = 3, size=(595, 842)) -> bytes:
    return make_pdf([f"Seite {i + 1}" for i in range(pages)], size=size, outline=False, title="")


def texts(data: bytes) -> list[str]:
    return [p.extract_text() for p in PdfReader(io.BytesIO(data)).pages]


def sizes(data: bytes) -> list[tuple[float, float]]:
    return [(round(float(p.mediabox.width), 1), round(float(p.mediabox.height), 1))
            for p in PdfReader(io.BytesIO(data)).pages]


def test_placement_fit_shrink_actual_and_rotation():
    fit = P.placement(842, 595, 595.28, 841.89, "fit")
    assert fit.landscape and fit.paper_width > fit.paper_height and fit.scale == pytest.approx(1.0, abs=0.01)
    small = P.placement(300, 400, 595.28, 841.89, "fit")
    assert small.scale > 1.9 and small.x == pytest.approx(0, abs=0.5) or small.y == pytest.approx(0, abs=0.5)
    shrink = P.placement(300, 400, 595.28, 841.89, "shrink")
    assert shrink.scale == 1.0 and shrink.x == pytest.approx((595.28 - 300) / 2)
    big = P.placement(1190, 1684, 595.28, 841.89, "shrink")
    assert big.scale == pytest.approx(0.5, abs=0.01)
    actual = P.placement(1190, 1684, 595.28, 841.89, "actual")
    assert actual.scale == 1.0 and actual.x < 0                                    # ragt über (Originalgröße)
    fixed = P.placement(842, 595, 595.28, 841.89, "fit", auto_rotate=False)
    assert not fixed.landscape and fixed.paper_width < fixed.paper_height
    same = P.placement(400, 300, None, None)
    assert (same.paper_width, same.paper_height, same.scale) == (400, 300, 1.0)


def test_page_order_collate():
    assert P.page_order(3, 2, True) == [0, 1, 2, 0, 1, 2]
    assert P.page_order(3, 2, False) == [0, 0, 1, 1, 2, 2]
    assert P.page_order(2, 0, True) == [0, 1]


def test_print_to_pdf_range_copies_and_paper():
    out = P.print_to_pdf(doc(5), P.PrintOptions(pages="2-3", copies=2, collate=True))
    assert_valid_pdf(out)
    assert [t.strip() for t in texts(out)] == ["Seite 2", "Seite 3", "Seite 2", "Seite 3"]
    assert set(sizes(out)) == {(595.3, 841.9)}
    letter = P.print_to_pdf(doc(1), P.PrintOptions(paper="letter"))
    assert sizes(letter) == [(612.0, 792.0)]
    source = P.print_to_pdf(doc(1, size=(300, 200)), P.PrintOptions(paper="source"))
    assert sizes(source) == [(300.0, 200.0)]


def test_print_to_pdf_landscape_page_on_a4():
    out = P.print_to_pdf(doc(1, size=(842, 595)), P.PrintOptions())
    assert sizes(out) == [(841.9, 595.3)]
    out = P.print_to_pdf(doc(1, size=(842, 595)), P.PrintOptions(auto_rotate=False))
    assert sizes(out) == [(595.3, 841.9)]


def test_rotated_page_prints_upright():
    data = rotate(doc(1), [0], 90)
    out = P.print_to_pdf(data, P.PrintOptions())
    assert sizes(out) == [(841.9, 595.3)]                                          # gedreht = quer
    assert "Seite 1" in texts(out)[0]


def test_annotations_and_forms_options():
    data = A.add_text(doc(1), 0, (72, 300, 300, 330), "KOMMENTARTEXT", 12)
    data = F.add_text_field(data, 0, (72, 400, 300, 420), "name")
    data = F.fill(data, {"name": "FELDWERT"})
    both = P.print_to_pdf(data, P.PrintOptions())
    assert "KOMMENTARTEXT" in texts(both)[0] and "FELDWERT" in texts(both)[0]
    no_annots = P.print_to_pdf(data, P.PrintOptions(annotations=False))
    assert "KOMMENTARTEXT" not in texts(no_annots)[0] and "FELDWERT" in texts(no_annots)[0]
    no_forms = P.print_to_pdf(data, P.PrintOptions(forms=False))
    assert "KOMMENTARTEXT" in texts(no_forms)[0] and "FELDWERT" not in texts(no_forms)[0]
    plain = P.prepare(data, P.PrintOptions(annotations=False, forms=False))
    assert "/Annots" not in PdfReader(io.BytesIO(plain)).pages[0]


def test_non_printing_and_hidden_annotations_are_left_out():
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, NumberObject
    data = A.add_text(doc(1), 0, (72, 300, 300, 330), "NURBILDSCHIRM", 12)
    writer = PdfWriter(clone_from=io.BytesIO(data))
    writer.pages[0]["/Annots"][0].get_object()[NameObject("/F")] = NumberObject(0)
    buffer = io.BytesIO()
    writer.write(buffer)
    out = P.print_to_pdf(buffer.getvalue(), P.PrintOptions())
    assert "NURBILDSCHIRM" not in texts(out)[0]


def test_progress_and_validation():
    seen = []
    P.print_to_pdf(doc(2), P.PrintOptions(copies=2), progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (4, 4)
    with pytest.raises(PdfEditError, match="Kopien"):
        P.print_to_pdf(doc(1), P.PrintOptions(copies=0))
    with pytest.raises(PdfEditError, match="Papier"):
        P.print_to_pdf(doc(1), P.PrintOptions(paper="a0"))
    with pytest.raises(PdfEditError, match="nur 3 Seiten"):
        P.print_to_pdf(doc(3), P.PrintOptions(pages="9"))
