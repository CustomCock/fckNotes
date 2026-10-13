"""Block V9: Komprimieren (Profile, Bilder, Objekt-Streams), Reparieren, Bilder → PDF, PDF → PNG/JPG,
PDF → Text und Markdown."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader

from notex.core import pdfaudit
from notex.core import pdfconvert as C
from notex.core import pdfimages as I
from notex.core import pdfstamp_layers as L
from notex.core.pdfpages import PdfEditError
from pdffixtures import (QUADRANTS, article_doc, assert_valid_pdf, big, broken, encrypted, images_doc, photo_doc,
                         picture)
from pdfhelp import make_pdf


def text(data: bytes, page: int = 0) -> str:
    return PdfReader(io.BytesIO(data)).pages[page].extract_text()


# ---- Komprimieren -----------------------------------------------------------------------------------------------
def test_standard_profile_shrinks_photo_and_keeps_text():
    data = photo_doc()
    result = C.compress(data, C.PROFILES["standard"])
    assert_valid_pdf(result.data)
    assert result.after < result.before / 10 and result.images_changed == 1 and result.lossy
    assert "Fotoseite 2" in text(result.data, 1)
    image = I.page_images(result.data, 0)[0]
    assert image.filters == ("/DCTDecode",) and 140 <= image.dpi <= 160          # auf 150 dpi verkleinert
    assert "verlustbehaftet" in result.summary() and "→" in result.summary()


def test_profiles_order_by_size():
    data = photo_doc(1)
    sizes = {key: C.compress(data, profile).after for key, profile in C.PROFILES.items()}
    assert sizes["small"] < sizes["standard"] < sizes["high"] < len(data)
    lossless = C.compress(data, C.PROFILES["lossless"])
    assert lossless.images_changed == 0 and not lossless.lossy
    assert I.page_images(lossless.data, 0)[0].width_px == 1600                 # Bild unverändert


def test_shared_image_is_sized_for_its_largest_use():
    data = images_doc()                                                          # /ImJ 3× (größte: 200 × 100 pt)
    result = C.compress(data, C.Profile("custom", "eigen", dpi=72, jpeg_quality=0))
    assert_valid_pdf(result.data)
    assert all(i.width_px == 40 for i in I.all_images(result.data) if i.uses == 3)  # 40 px < 200 pt: bleibt


def test_object_streams_and_unused_removed():
    data = photo_doc(1)
    result = C.compress(data, C.PROFILES["lossless"])
    assert b"/ObjStm" in result.data
    result = C.compress(data, C.Profile("x", "x", 0, 0, object_streams=False))
    assert b"/ObjStm" not in result.data


def test_line_art_stays_lossless():
    data = images_doc()                                                          # kleine 4-Farben-Bilder
    result = C.compress(data, C.Profile("x", "x", dpi=0, jpeg_quality=50))
    raw, ext = I.export_content_image(result.data, 0, 1)
    assert ext == "png" and Image.open(io.BytesIO(raw)).convert("RGB").getpixel((2, 2)) == QUADRANTS[0]


def test_compress_keeps_layers_and_redaction_audit_stays_clean():
    data = L.add_layer(make_pdf(["Inhalt"], outline=False, title=""), L.Watermark(text="ENTWURF"))
    result = C.compress(data, C.PROFILES["small"])
    assert [layer.kind for layer in L.list_layers(result.data)] == ["watermark"]
    assert pdfaudit.audit(result.data, ["geheimnis"]) == []


def test_compress_drops_old_revisions():
    data = make_pdf(["Neu"], outline=False, title="")
    old = data + b"\n% alte Fassung: Geheimwort\n"
    result = C.compress(old, C.PROFILES["lossless"])
    assert b"Geheimwort" not in result.data


def test_compress_progress_and_cancel():
    seen = []
    C.compress(photo_doc(1), C.PROFILES["standard"], progress=lambda d, t: seen.append((d, t)))
    assert seen[-1][0] == seen[-1][1]

    class Stop(Exception):
        pass

    def stop(done, total):
        raise Stop()
    with pytest.raises(Stop):
        C.compress(photo_doc(1), C.PROFILES["standard"], progress=stop)


def test_compress_validation_and_encrypted():
    with pytest.raises(PdfEditError, match="dpi"):
        C.compress(photo_doc(1), C.Profile("x", "x", dpi=5))
    with pytest.raises(PdfEditError, match="JPEG"):
        C.compress(photo_doc(1), C.Profile("x", "x", jpeg_quality=500))
    with pytest.raises(PdfEditError, match="verschlüsselt"):
        C.compress(encrypted(), C.PROFILES["lossless"])


def test_big_document_compresses():
    data = big(520)
    result = C.compress(data, C.PROFILES["lossless"])
    assert len(PdfReader(io.BytesIO(result.data)).pages) == 520
    assert result.after < result.before


# ---- Reparieren -------------------------------------------------------------------------------------------------
def test_repair_broken_file():
    repaired = C.repair(broken())
    assert_valid_pdf(repaired)
    assert len(PdfReader(io.BytesIO(repaired)).pages) >= 1
    with pytest.raises(PdfEditError, match="nicht lesbar"):
        C.repair(b"kein pdf")


# ---- Bilder → PDF -----------------------------------------------------------------------------------------------
def test_images_to_pdf_image_size_uses_dpi():
    png = picture("PNG", (300, 150))
    with Image.open(io.BytesIO(png)) as img:
        buffer = io.BytesIO()
        img.save(buffer, "PNG", dpi=(300, 300))
    out = C.images_to_pdf([("a.png", buffer.getvalue()), ("b.jpg", picture("JPEG", (40, 20)))])
    assert_valid_pdf(out)
    pages = PdfReader(io.BytesIO(out)).pages
    assert (float(pages[0].mediabox.width), float(pages[0].mediabox.height)) == pytest.approx((72, 36), abs=0.1)
    assert float(pages[1].mediabox.width) == pytest.approx(40 * 72 / 150, abs=0.1)   # ohne Angabe: 150 dpi
    raw, ext = I.export_content_image(out, 1, 0)
    assert ext == "jpg" and raw == picture("JPEG", (40, 20))                      # JPEG unverändert


def test_images_to_pdf_a4_orientation_and_margin():
    out = C.images_to_pdf([("quer.png", picture("PNG", (400, 200))), ("hoch.png", picture("PNG", (200, 400)))],
                          page_size="a4", margin_mm=10)
    pages = PdfReader(io.BytesIO(out)).pages
    assert float(pages[0].mediabox.width) > float(pages[0].mediabox.height)       # quer
    assert float(pages[1].mediabox.width) < float(pages[1].mediabox.height)       # hoch
    image = I.page_images(out, 0)[0]
    assert image.rect[0] == pytest.approx(10 * 72 / 25.4, abs=0.2)               # Rand links


def test_images_to_pdf_errors():
    with pytest.raises(PdfEditError, match="Keine Bilder"):
        C.images_to_pdf([])
    with pytest.raises(PdfEditError, match="kaputt.png"):
        C.images_to_pdf([("kaputt.png", b"nope")])
    with pytest.raises(PdfEditError, match="Seitengröße"):
        C.images_to_pdf([("a.png", picture())], page_size="a0")


# ---- PDF → Bilder -----------------------------------------------------------------------------------------------
def test_export_page_images_png_and_jpg_without_overwrite(tmp_path):
    data = make_pdf(["Eins", "Zwei", "Drei"], outline=False)
    (tmp_path / "doc-S1.png").write_bytes(b"alt")
    paths = C.export_page_images(data, [0, 2], tmp_path, "doc", dpi=72)
    assert [p.name for p in paths] == ["doc-S1 (2).png", "doc-S3.png"]
    assert (tmp_path / "doc-S1.png").read_bytes() == b"alt"
    with Image.open(paths[1]) as img:
        assert img.size == (595, 842)
    paths = C.export_page_images(data, [1], tmp_path, "doc", dpi=144, fmt="jpg", quality=70)
    with Image.open(paths[0]) as img:
        assert img.format == "JPEG" and img.size == (1190, 1684)


def test_export_page_images_errors(tmp_path):
    data = make_pdf(["Eins"], outline=False)
    with pytest.raises(PdfEditError, match="dpi"):
        C.export_page_images(data, [0], tmp_path, "x", dpi=5000)
    with pytest.raises(PdfEditError, match="Format"):
        C.export_page_images(data, [0], tmp_path, "x", fmt="gif")
    with pytest.raises(PdfEditError, match="Ordner"):
        C.export_page_images(data, [0], tmp_path / "fehlt", "x")
    with pytest.raises(PdfEditError, match="Seite 5"):
        C.export_page_images(data, [4], tmp_path, "x")


# ---- PDF → Text / Markdown --------------------------------------------------------------------------------------
def test_to_text_with_page_markers():
    out = C.to_text(make_pdf(["Eins", "Zwei"], outline=False))
    assert out == "--- Seite 1 ---\nEins\n\n--- Seite 2 ---\nZwei\n"
    assert C.to_text(make_pdf(["Eins"], outline=False), page_markers=False) == "Eins\n"


def test_to_markdown_structure():
    md = C.to_markdown(article_doc())
    assert "# Jahresbericht\n" in md and "## Einleitung\n" in md and "## Ergebnis\n" in md
    assert "Dies ist der erste Absatz mit einem getrennten Wort und mehr Text.\n" in md
    assert "Zweiter Absatz." in md
    assert "- Punkt eins\n- Punkt zwei" in md and "1. Erstens\n2. Zweitens" in md
    assert "\\# kein Titel" in md                                              # kein falscher Titel
    assert md.startswith("<!-- Seite 1 -->")
    assert not C.to_markdown(article_doc(), page_markers=False).startswith("<!--")


def test_to_markdown_progress():
    seen = []
    C.to_markdown(make_pdf(["A", "B"], outline=False), progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 2), (2, 2)]


def test_human_sizes():
    assert C.human(512) == "512 B" and C.human(2048) == "2,0 KB" and C.human(5 * 1024 * 1024) == "5,0 MB"
