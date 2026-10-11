"""Eingebettete Schrift (Regel 6): Noto Sans als Untermenge, Unicode, wieder extrahierbar, deterministisch."""
import io

import pypdfium2 as pdfium
from pypdf import PdfWriter

from notex.core import pdfannot as A, pdffont, pdfforms
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf


def test_widths_and_wrap():
    assert pdffont.width("WWW", 10) > pdffont.width("iii", 10)
    assert pdffont.width("Text", 12, bold=True) > pdffont.width("Text", 12)
    lines = pdffont.wrap("eins zwei drei vier fünf sechs", 12, 60)
    assert len(lines) > 1 and all(pdffont.width(line, 12) <= 60 for line in lines)
    assert pdffont.wrap("A\nB", 12, 500) == ["A", "B"]


def test_normalize_replaces_missing_glyphs_and_controls():
    face = pdffont.face()
    assert face.normalize("Łódź Ω Ж") == "Łódź Ω Ж"
    assert face.normalize("a😀b\x07c\tz") == "a?bc    z"
    assert face.normalize("é") == "é"                        # NFC
    assert face.encode("A") == f"<{face.glyph('A'):04X}>"


def test_embedded_subset_font_is_valid_and_text_extractable():
    data = A.add_text(make_pdf(["Seite"], outline=False, title=""), 0, (72, 100, 400, 110), "Grüße aus Łódź – Ωmega",
                      16)
    assert_valid_pdf(data)
    flat = pdfforms.flatten(data)                                   # Erscheinungsbild wird Seiteninhalt
    text = pdfium.PdfDocument(flat)[0].get_textpage().get_text_range()
    assert "Grüße aus Łódź – Ωmega" in text                          # ToUnicode stimmt
    writer = PdfWriter(clone_from=io.BytesIO(data))
    ap = writer.pages[0]["/Annots"][0].get_object()["/AP"]["/N"].get_object()
    font = ap["/Resources"]["/Font"]["/FnR"].get_object()
    descendant = font["/DescendantFonts"][0].get_object()
    font_file = descendant["/FontDescriptor"]["/FontFile2"].get_object().get_data()
    assert len(font_file) < 60_000                                  # Untermenge, nicht die ganze Schrift (≈ 600 KB)


def test_subset_is_deterministic():
    first = A.add_text(make_pdf(["X"], outline=False, title=""), 0, (72, 100, 300, 110), "Gleich", 12)
    second = A.add_text(make_pdf(["X"], outline=False, title=""), 0, (72, 100, 300, 110), "Gleich", 12)
    pick = lambda d: PdfWriter(clone_from=io.BytesIO(d)).pages[0]["/Annots"][0].get_object()["/AP"]["/N"].get_object()[  # noqa: E731
        "/Resources"]["/Font"]["/FnR"].get_object()["/BaseFont"]
    assert pick(first) == pick(second)
