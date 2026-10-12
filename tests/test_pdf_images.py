"""Block V7: Bilder einfügen, drehen, skalieren; Bilder im Inhalt finden, exportieren, ersetzen, zuschneiden, löschen.

Pixelprüfungen rendern die Seite mit PDFium (1 Pixel = 1 Ansichts-Punkt) und vergleichen Farben an festen Stellen.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader

from notex.core import pdfannot as A
from notex.core import pdfforms as F
from notex.core import pdfimages as I
from notex.core import pdfobjects as O
from notex.core.pdfpages import PdfEditError, rotate
from pdffixtures import QUADRANTS, assert_valid_pdf, images_doc, picture
from pdfhelp import make_pdf

RED, GREEN, BLUE, WHITE = QUADRANTS


def render(data: bytes, page: int = 0) -> Image.Image:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(data)
    try:
        return document[page].render(scale=1, draw_annots=True).to_pil().convert("RGB")
    finally:
        document.close()


def close(color, expected, tolerance: int = 40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(color, expected))


def blank() -> bytes:
    return make_pdf(["Bilder"], outline=False, title="")


def object_count(data: bytes, subtype: str = "/Image") -> int:
    reader = PdfReader(io.BytesIO(data))
    count = 0
    for number in range(1, len(reader.xref_objStm) + sum(len(v) for v in reader.xref.values()) + 2):
        try:
            obj = reader.get_object(number)
        except Exception:                                  # noqa: BLE001
            continue
        if hasattr(obj, "get") and obj.get("/Subtype") == subtype:
            count += 1
    return count


# ---- Bilder im Inhalt: finden und exportieren -------------------------------------------------------------------
def test_page_images_lists_all_kinds_in_order():
    data = images_doc()
    images = I.page_images(data, 0)
    assert [(i.kind, i.name) for i in images] == [("xobject", "/ImJ"), ("xobject", "/ImP"), ("inline", ""),
                                                  ("xobject", "/ImJ"), ("form", "/ImF")]
    assert images[0].rect == pytest.approx((72, 142, 272, 242))
    assert images[4].rect == pytest.approx((72, 532, 192, 592))       # Form-Matrix verschiebt um (72, 250)
    assert images[4].path == ("/Fm1",) and images[4].op == -1
    assert images[1].smask and not images[0].smask
    assert (images[2].width_px, images[2].height_px) == (4, 2)


def test_all_images_counts_uses_across_pages():
    images = I.all_images(images_doc())
    jpegs = [i for i in images if i.name == "/ImJ"]
    assert len(jpegs) == 3 and all(i.uses == 3 for i in jpegs)
    assert "3× im Dokument" in jpegs[0].describe()
    assert I.uses_of(images_doc(), 0, 1) == 1


def test_all_images_progress_can_cancel():
    seen = []
    I.all_images(images_doc(), lambda done, total: seen.append((done, total)) or False)
    assert seen == [(1, 2)]


def test_export_jpeg_unchanged_and_png_with_alpha():
    data = images_doc()
    raw, ext = I.export_content_image(data, 0, 0)
    assert ext == "jpg" and raw == picture("JPEG", (40, 20))
    raw, ext = I.export_content_image(data, 0, 1)
    image = Image.open(io.BytesIO(raw))
    assert ext == "png" and image.mode == "RGBA" and image.size == (40, 20)
    assert image.getpixel((5, 5))[3] == 255 and image.getpixel((35, 5))[3] == 96   # /SMask als Alpha
    raw, ext = I.export_content_image(data, 0, 2)                     # Inline-Bild
    image = Image.open(io.BytesIO(raw))
    assert ext == "png" and image.size == (4, 2) and image.getpixel((1, 0)) == 0x55
    raw, ext = I.export_content_image(data, 0, 4)                     # in einer Gruppe
    assert ext == "png" and Image.open(io.BytesIO(raw)).convert("RGB").getpixel((0, 0)) == RED


def test_hit_prefers_smallest_image():
    images = I.page_images(images_doc(), 0)
    assert I.hit(images, 100, 160).index == 0
    assert I.hit(images, 10, 10) is None


# ---- Löschen ----------------------------------------------------------------------------------------------------
def test_delete_unique_image_removes_it_from_file():
    data = images_doc()
    before = object_count(data)
    out = I.delete_content_image(data, 0, 1)                          # /ImP mit /SMask
    assert_valid_pdf(out)
    assert [i.name for i in I.page_images(out, 0)] == ["/ImJ", "", "/ImJ", "/ImF"]
    assert object_count(out) == before - 2                            # Bild und Maske sind aus der Datei
    assert close(render(out).getpixel((320, 180)), (255, 255, 255), 5)
    assert close(render(out).getpixel((80, 150)), RED)                # andere Bilder bleiben
    assert "Bilder" in PdfReader(io.BytesIO(out)).pages[0].extract_text()


def test_delete_one_use_of_shared_image_keeps_the_others():
    out = I.delete_content_image(images_doc(), 0, 0)
    assert_valid_pdf(out)
    jpegs = [i for i in I.all_images(out) if i.name == "/ImJ"]
    assert len(jpegs) == 2 and all(i.uses == 2 for i in jpegs)
    assert close(render(out).getpixel((80, 150)), (255, 255, 255), 5)
    assert close(render(out).getpixel((305, 345)), RED)


def test_delete_inline_image_and_refuse_nested():
    out = I.delete_content_image(images_doc(), 0, 2)
    assert [i.kind for i in I.page_images(out, 0)] == ["xobject", "xobject", "xobject", "form"]
    with pytest.raises(PdfEditError, match="Gruppen"):
        I.delete_content_image(images_doc(), 0, 4)
    with pytest.raises(PdfEditError, match="neu auswählen"):
        I.delete_content_image(images_doc(), 0, 9)


def test_delete_does_not_touch_other_page_sharing_resources():
    data = images_doc()
    out = I.delete_content_image(data, 1, 0)
    assert I.page_images(out, 1) == []
    assert len([i for i in I.page_images(out, 0) if i.name == "/ImJ"]) == 2
    assert_valid_pdf(out)


# ---- Zuschneiden ------------------------------------------------------------------------------------------------
def test_crop_removes_pixels_and_keeps_visible_part_in_place():
    data = images_doc()
    before = render(data)
    out = I.crop_content_image(data, 0, 0, (60, 130, 172, 260))       # linke Hälfte des JPEGs (72..172)
    assert_valid_pdf(out)
    after = render(out)
    images = I.page_images(out, 0)
    assert (images[0].width_px, images[0].height_px) == (20, 20)
    assert images[0].rect == pytest.approx((72, 142, 172, 242), abs=0.01)
    for x, y in ((80, 150), (160, 150), (80, 235), (160, 235)):
        assert close(after.getpixel((x, y)), before.getpixel((x, y)), 30)
    assert close(after.getpixel((230, 150)), (255, 255, 255), 5)      # rechter Teil ist weg
    assert I.page_images(out, 0)[3].name == "/ImJ"                    # andere Verwendung unverändert
    raw, _ext = I.export_content_image(out, 0, 3)
    assert raw == picture("JPEG", (40, 20))


def test_crop_png_keeps_alpha_and_lossless():
    out = I.crop_content_image(images_doc(), 0, 1, (300, 162, 460, 202))   # obere Hälfte (160 × 80 → 160 × 40)
    raw, ext = I.export_content_image(out, 0, 1)
    image = Image.open(io.BytesIO(raw))
    assert ext == "png" and image.size == (40, 10) and image.mode == "RGBA"
    assert image.getpixel((3, 3)) == (*RED, 255) and image.getpixel((35, 3)) == (*GREEN, 96)
    out = I.crop_content_image(images_doc(), 0, 1, (300, 162, 380, 202))   # nur der deckende Teil: keine Maske
    assert not I.page_images(out, 0)[1].smask


def test_crop_on_rotated_page_uses_view_coordinates():
    data = rotate(images_doc(), [0], 90)
    image = I.page_images(data, 0)[0]
    x0, y0, x1, y1 = image.rect
    before = render(data)
    out = I.crop_content_image(data, 0, 0, (x0, y0, x1, (y0 + y1) / 2))
    after = render(out)
    probe = (round(x0 + 10), round(y0 + 10))
    assert close(after.getpixel(probe), before.getpixel(probe), 30)
    assert close(after.getpixel((round(x0 + 10), round(y1 - 10))), (255, 255, 255), 5)
    assert_valid_pdf(out)


def test_crop_errors():
    data = images_doc()
    with pytest.raises(PdfEditError, match="nicht auf dem Bild"):
        I.crop_content_image(data, 0, 0, (400, 700, 450, 750))
    with pytest.raises(PdfEditError, match="ganze Bild"):
        I.crop_content_image(data, 0, 0, (0, 0, 595, 842))
    with pytest.raises(PdfEditError, match="nicht zuschneiden"):
        I.crop_content_image(data, 0, 2, (72, 352, 100, 392))


# ---- Ersetzen ---------------------------------------------------------------------------------------------------
def test_replace_here_fits_with_aspect_and_leaves_other_uses():
    data = images_doc()
    square = picture("PNG", (20, 20))
    out = I.replace_content_image(data, 0, 0, square)
    assert_valid_pdf(out)
    image = render(out)
    assert close(image.getpixel((80, 190)), (255, 255, 255), 5)       # Rand links: eingepasst (100 × 100 mittig)
    assert close(image.getpixel((130, 150)), RED)
    raw, _ext = I.export_content_image(out, 0, 3)
    assert raw == picture("JPEG", (40, 20))                           # zweite Verwendung unverändert
    assert len([i for i in I.all_images(out) if i.name == "/ImJ"]) == 2


def test_replace_here_stretch():
    out = I.replace_content_image(images_doc(), 0, 0, picture("PNG", (20, 20)), keep_aspect=False)
    assert close(render(out).getpixel((80, 150)), RED)
    assert I.page_images(out, 0)[0].rect == pytest.approx((72, 142, 272, 242), abs=0.01)


def test_replace_everywhere_changes_all_uses():
    data = images_doc()
    new = picture("PNG", (40, 20), mode="L")
    out = I.replace_content_image(data, 0, 3, new, everywhere=True)
    assert_valid_pdf(out)
    exports = [I.export_content_image(out, i.page, i.index) for i in I.all_images(out) if i.name == "/ImJ"]
    assert len(exports) == 3 and all(ext == "png" for _raw, ext in exports)
    assert len({raw for raw, _ext in exports}) == 1
    assert object_count(out) == object_count(data)                    # altes JPEG ist weg, neues da


def test_replace_nested_only_everywhere_and_inline_never():
    data = images_doc()
    out = I.replace_content_image(data, 0, 4, picture("PNG", (10, 10)), everywhere=True)
    assert I.page_images(out, 0)[4].width_px == 10
    with pytest.raises(PdfEditError, match="überall ersetzen"):
        I.replace_content_image(data, 0, 4, picture("PNG", (10, 10)))
    with pytest.raises(PdfEditError, match="Inline"):
        I.replace_content_image(data, 0, 2, picture("PNG", (10, 10)), everywhere=True)


def test_replace_rejects_garbage():
    with pytest.raises(PdfEditError, match="Kein lesbares Bild"):
        I.replace_content_image(images_doc(), 0, 0, b"kein bild")


# ---- Bilddateien einlesen ---------------------------------------------------------------------------------------
def test_encode_jpeg_unchanged_png_lossless_alpha_as_smask():
    jpeg = picture("JPEG")
    encoded = I.encode_picture(jpeg)
    assert encoded.filter == "/DCTDecode" and encoded.data == jpeg and encoded.lossless
    encoded = I.encode_picture(picture("PNG", alpha=True))
    assert encoded.filter == "/FlateDecode" and encoded.alpha is not None and encoded.colorspace == "/DeviceRGB"
    encoded = I.encode_picture(picture("PNG"))
    assert encoded.alpha is None                                      # voll deckend: keine Maske
    encoded = I.encode_picture(picture("WEBP", alpha=True))
    assert encoded.alpha is not None
    assert I.encode_picture(picture("PNG", mode="L")).colorspace == "/DeviceGray"


def test_encode_exif_rotation_and_cmyk_jpeg():
    encoded = I.encode_picture(picture("JPEG", (40, 20), orientation=6))
    assert (encoded.width, encoded.height) == (20, 40) and encoded.filter == "/DCTDecode" and not encoded.lossless
    assert I.picture_size(picture("JPEG", (40, 20), orientation=6)) == (20, 40)
    encoded = I.encode_picture(picture("JPEG", mode="CMYK"))
    assert encoded.colorspace == "/DeviceRGB" and not encoded.lossless


def test_encode_palette_gif_with_transparency():
    image = Image.new("P", (8, 8), 0)
    image.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    image.putpixel((1, 1), 1)
    buffer = io.BytesIO()
    image.save(buffer, "GIF", transparency=0)
    encoded = I.encode_picture(buffer.getvalue())
    assert encoded.alpha is not None and (encoded.width, encoded.height) == (8, 8)


def test_encode_limits(monkeypatch):
    monkeypatch.setattr(I, "MAX_PIXELS", 100)
    with pytest.raises(PdfEditError, match="Megapixel"):
        I.encode_picture(picture("PNG", (40, 20)))
    monkeypatch.setattr(I, "MAX_FILE", 10)
    with pytest.raises(PdfEditError, match="zu groß"):
        I.encode_picture(picture("PNG", (40, 20)))
    with pytest.raises(PdfEditError, match="Leere"):
        I.encode_picture(b"")


# ---- Eingefügte Bilder ------------------------------------------------------------------------------------------
def inserted(data: bytes, page: int = 0):
    return [o for o in O.list_objects(data, page) if o.kind == "image"]


def test_insert_click_uses_natural_size_and_renders():
    out = I.insert_image(blank(), 0, (100, 300, 100, 300), picture("PNG", (40, 20)))
    assert_valid_pdf(out)
    obj = inserted(out)[0]
    assert obj.rect == pytest.approx((100, 300, 130, 315), abs=0.05)  # 96 dpi: 40 px = 30 pt
    assert obj.label == "Bild" and obj.movable and obj.keep_aspect and obj.free_aspect
    image = render(out)
    assert close(image.getpixel((105, 303)), RED) and close(image.getpixel((125, 312)), WHITE)


def test_insert_large_image_is_capped_to_page():
    out = I.insert_image(blank(), 0, (0, 0, 0, 0), picture("PNG", (4000, 200)))
    x0, _y0, x1, _y1 = inserted(out)[0].rect
    assert x1 - x0 == pytest.approx(595 * 0.6, abs=0.5)


def test_insert_drag_fits_or_stretches():
    out = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    assert inserted(out)[0].rect == pytest.approx((100, 100, 300, 200), abs=0.05)
    out = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)), keep_aspect=False)
    assert inserted(out)[0].rect == pytest.approx((100, 100, 300, 300), abs=0.05)
    assert close(render(out).getpixel((110, 280)), BLUE)


def test_inserted_jpeg_exports_unchanged():
    jpeg = picture("JPEG", (40, 20))
    out = I.insert_image(blank(), 0, (100, 100, 300, 300), jpeg)
    assert I.export_inserted(out, 0, inserted(out)[0].index) == (jpeg, "jpg")


def test_exif_rotated_photo_appears_upright():
    out = I.insert_image(blank(), 0, (100, 100, 100, 100), picture("JPEG", (40, 20), orientation=6))
    x0, y0, x1, y1 = inserted(out)[0].rect
    assert (x1 - x0, y1 - y0) == pytest.approx((15, 30), abs=0.05)
    expected = Image.open(io.BytesIO(picture("JPEG", (40, 20), orientation=6)))
    from PIL import ImageOps
    upright = ImageOps.exif_transpose(expected).convert("RGB")
    assert close(render(out).getpixel((round(x0) + 3, round(y0) + 3)), upright.getpixel((2, 2)))


def test_rotate_by_90_swaps_frame_around_center():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    obj = inserted(data)[0]
    out = I.rotate_image(data, 0, obj.index, 90)
    assert_valid_pdf(out)
    new = inserted(out)[0]
    assert new.rect == pytest.approx((150, 50, 250, 250), abs=0.05) and new.angle == 90
    image = render(out)
    assert close(image.getpixel((245, 55)), RED)                     # im Uhrzeigersinn: oben links → oben rechts
    assert close(image.getpixel((155, 245)), WHITE)


def test_rotate_free_angle_and_absolute():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    out = I.rotate_image(data, 0, inserted(data)[0].index, 30)
    obj = inserted(out)[0]
    w, h = I.rotated_size(200, 100, 30)
    assert (obj.rect[2] - obj.rect[0], obj.rect[3] - obj.rect[1]) == pytest.approx((w, h), abs=0.05)
    assert not obj.free_aspect
    out = I.rotate_image(out, 0, obj.index, 0, absolute=True)
    assert inserted(out)[0].rect == pytest.approx((100, 100, 300, 200), abs=0.05)
    out = I.rotate_image(out, 0, obj.index, -90)
    assert inserted(out)[0].angle == 270


def test_resize_free_and_aspect_for_skewed():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    obj = inserted(data)[0]
    out = O.set_rect(data, 0, obj.index, (100, 100, 200, 300))       # gerade: verzerren erlaubt
    assert inserted(out)[0].rect == pytest.approx((100, 100, 200, 300), abs=0.05)
    assert I.image_meta(out, 0, obj.index).width == pytest.approx(100, abs=0.01)
    skewed = I.rotate_image(data, 0, obj.index, 30)
    old = inserted(skewed)[0].rect
    out = O.set_rect(skewed, 0, obj.index, (old[0], old[1], old[0] + (old[2] - old[0]) / 2, old[3]))
    meta = I.image_meta(out, 0, obj.index)
    assert meta.width / meta.height == pytest.approx(2.0, abs=0.01)  # schief: Seitenverhältnis bleibt
    moved = O.move_by(data, 0, obj.index, 10, 5)
    assert inserted(moved)[0].rect == pytest.approx((110, 105, 310, 205), abs=0.05)


def test_rotated_90_frame_resize_maps_axes():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    rotated = I.rotate_image(data, 0, 0, 90)
    out = O.set_rect(rotated, 0, 0, (150, 50, 200, 250))
    meta = I.image_meta(out, 0, 0)
    assert (meta.width, meta.height) == pytest.approx((200, 50), abs=0.05)


def test_opacity_and_replace_inserted():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    out = I.set_image_opacity(data, 0, 0, 0.5)
    assert I.image_meta(out, 0, 0).opacity == 0.5
    pixel = render(out).getpixel((110, 110))
    assert 120 < pixel[1] < 200                                     # halb durchsichtiges Rot auf Weiß
    out = I.replace_inserted(out, 0, 0, picture("PNG", (20, 20)))
    obj = inserted(out)[0]
    assert obj.rect == pytest.approx((100, 50, 300, 250), abs=0.05)  # Breite bleibt, Mitte bleibt
    assert I.image_meta(out, 0, 0).opacity == 0.5
    assert_valid_pdf(out)


def test_inserted_image_on_rotated_page_stands_upright():
    data = rotate(blank(), [0], 90)
    out = I.insert_image(data, 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    obj = inserted(out)[0]
    assert obj.rect == pytest.approx((100, 100, 300, 200), abs=0.05)
    image = render(out)
    assert close(image.getpixel((105, 105)), RED) and close(image.getpixel((295, 195)), WHITE)


def test_flatten_burns_image_in_and_it_becomes_content():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    before = render(data)
    out = F.flatten(data)
    assert_valid_pdf(out)
    assert inserted(out) == []
    assert close(render(out).getpixel((110, 110)), before.getpixel((110, 110)), 10)
    assert any(i.kind == "form" for i in I.page_images(out, 0))       # liegt jetzt im Inhalt (über eine Gruppe)


def test_delete_inserted_image_removes_pixels():
    data = I.insert_image(blank(), 0, (100, 100, 300, 300), picture("PNG", (40, 20)))
    out = A.delete_annotation(data, 0, 0)
    assert object_count(out) == 0 and inserted(out) == []


def test_not_an_image_errors():
    data = A.add_note(blank(), 0, 100, 100, "Notiz")
    with pytest.raises(PdfEditError, match="kein eingefügtes Bild"):
        I.rotate_image(data, 0, 0, 90)
    with pytest.raises(PdfEditError, match="kein eingefügtes Bild"):
        I.export_inserted(data, 0, 0)
