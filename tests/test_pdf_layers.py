"""Block V8: Wasserzeichen, Kopf-/Fußzeilen mit Seitenzahlen, Hintergrund als eigene Ebenen (OCG) –
hinzufügen, entfernen (Seiteninhalt danach byte-gleich), aktualisieren, Vorschau, fremde Wasserzeichen."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
                           TextStringObject)

from notex import APP_NAME
from notex.core import pdfaudit
from notex.core import pdfstamp_layers as L
from notex.core.pdfpages import PdfEditError, insert_blank, rotate
from pdffixtures import assert_valid_pdf, picture
from pdfhelp import make_pdf


def doc(pages: int = 3) -> bytes:
    return make_pdf([f"Seite {i + 1} Inhalt" for i in range(pages)], outline=False, title="Bericht")


def text(data: bytes, page: int) -> str:
    return PdfReader(io.BytesIO(data)).pages[page].extract_text()


def contents(data: bytes) -> list[bytes]:
    return [p.get_contents().get_data() if p.get_contents() else b"" for p in PdfReader(io.BytesIO(data)).pages]


def render(data: bytes, page: int = 0) -> Image.Image:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(data)
    try:
        return document[page].render(scale=1).to_pil().convert("RGB")
    finally:
        document.close()


def ocg_names(data: bytes) -> list[str]:
    root = PdfReader(io.BytesIO(data)).trailer["/Root"]
    if "/OCProperties" not in root:
        return []
    return [str(ref.get_object()["/Name"]) for ref in root["/OCProperties"]["/OCGs"]]


def pixels(image: Image.Image, box=None) -> list[tuple[int, int, int]]:
    raw = (image.crop(box) if box else image).tobytes()
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]


def dark_pixels(image: Image.Image, box) -> int:
    return sum(1 for p in pixels(image, box) if sum(p) < 600)


# ---- Hinzufügen -------------------------------------------------------------------------------------------------
def test_watermark_text_is_searchable_in_its_own_layer():
    out = L.add_layer(doc(), L.Watermark(text="VERTRAULICH"))
    assert_valid_pdf(out)
    assert all("VERTRAULICH" in text(out, i) for i in range(3))
    assert ocg_names(out) == [f"{APP_NAME}: Wasserzeichen"]
    layers = L.list_layers(out)
    assert len(layers) == 1 and layers[0].kind == "watermark" and layers[0].pages == (0, 1, 2)
    assert "VERTRAULICH" in layers[0].describe()
    image = render(out)
    assert dark_pixels(image, (200, 300, 400, 550)) == 0               # hell (Deckkraft 25 %)
    reddish = [p for p in pixels(image, (150, 250, 450, 600)) if p[0] - p[2] > 25]
    assert len(reddish) > 500


def test_watermark_marked_as_artifact_and_optional_content():
    out = L.add_layer(doc(1), L.Watermark(text="ENTWURF"))
    page = PdfReader(io.BytesIO(out)).pages[0]
    data = page.get_contents().get_data()
    assert b"/Artifact << /Type /Pagination /Subtype /Watermark >> BDC" in data
    form = next(r.get_object() for r in page["/Resources"]["/XObject"].values() if L.LAYER_KEY in r.get_object())
    assert form["/OC"].get_object()["/Type"] == "/OCG"


def test_page_range_and_positions():
    out = L.add_layer(doc(), L.Watermark(text="KOPIE", position="tl", rotation=0, font_size=30, opacity=1, pages="2"))
    assert "KOPIE" not in text(out, 0) and "KOPIE" in text(out, 1) and "KOPIE" not in text(out, 2)
    image = render(out, 1)
    assert dark_pixels(image, (30, 30, 200, 80)) > 20                  # oben links
    assert dark_pixels(image, (300, 700, 595, 842)) == 0


def test_watermark_behind_content():
    out = L.add_layer(doc(1), L.Watermark(text="X", behind=True))
    array = PdfReader(io.BytesIO(out)).pages[0]["/Contents"]
    assert array[0].get_object()[L.LAYER_KEY]["/Role"] == "/Behind"


def test_original_with_open_graphics_state_does_not_shift_layer():
    writer = PdfWriter(clone_from=io.BytesIO(doc(1)))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(b"0.5 0 0 0.5 0 0 cm 2 Tr")                        # Original lässt CTM und Textmodus offen
    page[NameObject("/Contents")] = ArrayObject([page.raw_get("/Contents"), writer._add_object(stream)])
    buffer = io.BytesIO()
    writer.write(buffer)
    out = L.add_layer(buffer.getvalue(), L.HeaderFooter(footer_center="", footer_right="Seite {seite}", font_size=12))
    image = render(out)
    assert dark_pixels(image, (480, 780, 595, 842)) > 10               # unten rechts, nicht verkleinert


def test_header_footer_placeholders_and_numbering():
    out = L.add_layer(doc(), L.HeaderFooter(header_left="{dateiname}", header_right="{titel}",
                                            footer_center="Seite {seite} von {seiten}", pages="2-", start=1),
                      filename="bericht.pdf")
    assert "Seite 1 von 2" in text(out, 1) and "Seite 2 von 2" in text(out, 2)
    assert "von" not in text(out, 0)
    assert "bericht.pdf" in text(out, 1) and "Bericht" in text(out, 1)
    image = render(out, 1)
    assert dark_pixels(image, (0, 0, 595, 50)) > 10 and dark_pixels(image, (0, 800, 595, 842)) > 10


def test_header_date_placeholder():
    from datetime import datetime
    out = L.add_layer(doc(1), L.HeaderFooter(footer_center="{datum}"))
    assert datetime.now().strftime("%d.%m.%Y") in text(out, 0)


def test_header_on_rotated_page_sits_at_displayed_top():
    data = rotate(doc(1), [0], 90)
    out = L.add_layer(data, L.HeaderFooter(header_center="OBEN", footer_center=""))
    image = render(out)                                                # PDFium rendert gedreht wie angezeigt
    w, h = image.size
    assert w > h
    assert dark_pixels(image, (0, 0, w, 45)) > 10
    assert dark_pixels(image, (0, h - 30, w, h)) == 0


def test_background_color_and_image():
    out = L.add_layer(doc(1), L.Background(color="#00ff00"))
    image = render(out)
    assert image.getpixel((300, 500))[1] > 200 and image.getpixel((300, 500))[0] < 60
    assert dark_pixels(image, (60, 80, 250, 110)) > 20                 # Text bleibt sichtbar (Hintergrund dahinter)
    out = L.add_layer(doc(1), L.Background(image=True, fit="stretch"), image=picture("PNG", (40, 20)))
    image = render(out)
    assert image.getpixel((20, 20))[0] > 180                          # rotes Viertel oben links
    assert_valid_pdf(out)


def test_image_watermark_shares_one_image_object():
    out = L.add_layer(doc(), L.Watermark(image=True, scale=0.4, opacity=0.5), image=picture("PNG", (40, 20)))
    reader = PdfReader(io.BytesIO(out))
    images = set()
    for page in reader.pages:
        for ref in page["/Resources"]["/XObject"].values():
            form = ref.get_object()
            if L.LAYER_KEY in form:
                images.add(form["/Resources"]["/XObject"].raw_get("/Im0").idnum)
    assert len(images) == 1
    with pytest.raises(PdfEditError, match="Bild"):
        L.add_layer(doc(), L.Watermark(image=True))


def test_adding_same_kind_replaces_it():
    out = L.add_layer(doc(), L.Watermark(text="ALT"))
    out = L.add_layer(out, L.Watermark(text="NEU"))
    assert "ALT" not in text(out, 0) and "NEU" in text(out, 0)
    assert [layer.kind for layer in L.list_layers(out)] == ["watermark"]


# ---- Entfernen und Aktualisieren --------------------------------------------------------------------------------
def test_add_then_remove_restores_page_content_exactly():
    original = doc()
    out = L.add_layer(original, L.Background(color="#eeeeee"))
    out = L.add_layer(out, L.Watermark(text="X"))
    out = L.add_layer(out, L.HeaderFooter())
    clean = L.remove_layers(out)
    assert_valid_pdf(clean)
    assert contents(clean) == contents(original)
    assert ocg_names(clean) == []
    reader = PdfReader(io.BytesIO(clean))
    assert all("/fckLayer" not in str(list(p["/Resources"].get("/XObject", {}).keys())) for p in reader.pages)
    with pytest.raises(PdfEditError, match="Keine"):
        L.remove_layers(clean)


def test_remove_one_kind_keeps_the_others():
    out = L.add_layer(doc(), L.Watermark(text="WM"))
    out = L.add_layer(out, L.HeaderFooter(footer_center="Fuss {seite}"))
    out = L.remove_layers(out, "watermark")
    assert "WM" not in text(out, 0) and "Fuss 1" in text(out, 0)
    assert ocg_names(out) == [f"{APP_NAME}: Kopf-/Fußzeile"]
    assert_valid_pdf(out)


def test_remove_after_streams_were_merged_by_another_program():
    out = L.add_layer(doc(1), L.Watermark(text="WEG"))
    writer = PdfWriter(clone_from=io.BytesIO(out))
    page = writer.pages[0]
    merged = DecodedStreamObject()
    merged.set_data(page.get_contents().get_data())                  # alle Streams in einem (ohne Markierung)
    page[NameObject("/Contents")] = writer._add_object(merged)
    buffer = io.BytesIO()
    writer.write(buffer)
    clean = L.remove_layers(buffer.getvalue())
    assert "WEG" not in text(clean, 0) and "Seite 1 Inhalt" in text(clean, 0)
    assert b"Do" not in contents(clean)[0]
    assert_valid_pdf(clean)


def test_update_after_inserting_pages_renumbers():
    out = L.add_layer(doc(2), L.HeaderFooter(footer_center="Seite {seite} von {seiten}"))
    out = insert_blank(out, 2)
    assert "Seite 3" not in text(out, 2)
    updated = L.update_layers(out)
    assert "Seite 3 von 3" in text(updated, 2) and "Seite 1 von 3" in text(updated, 0)
    assert len(L.list_layers(updated)) == 1
    assert_valid_pdf(updated)


def test_update_keeps_image_and_id():
    out = L.add_layer(doc(2), L.Watermark(image=True), image=picture("PNG", (40, 20)))
    before = L.list_layers(out)[0]
    updated = L.update_layers(out)
    after = L.list_layers(updated)[0]
    assert after.layer_id == before.layer_id and after.spec["image"]
    with pytest.raises(PdfEditError, match="Keine"):
        L.update_layers(doc())


def test_progress_reports_and_can_cancel():
    seen = []
    L.add_layer(doc(), L.Watermark(text="X"), progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (6, 6)

    class Stop(Exception):
        pass

    def stop(done, total):
        raise Stop()
    with pytest.raises(Stop):
        L.add_layer(doc(), L.Watermark(text="X"), progress=stop)


# ---- Vorlagen, Vorschau, Prüfung --------------------------------------------------------------------------------
def test_spec_roundtrip_and_validation():
    spec = L.HeaderFooter(header_left="A", start=5, pages="2-3")
    assert L.from_dict(L.to_dict(spec)) == spec
    assert L.from_dict({"kind": "watermark", "text": "Y", "opacity": 9, "unbekannt": 1}).opacity == 1.0
    with pytest.raises(PdfEditError, match="Text"):
        L.checked(L.Watermark(text="  "))
    with pytest.raises(PdfEditError, match="Farbe"):
        L.checked(L.Background(color="grün"))
    with pytest.raises(PdfEditError, match="mindestens ein Feld"):
        L.checked(L.HeaderFooter(footer_center=""))
    with pytest.raises(PdfEditError, match="Ebenen-Art"):
        L.from_dict({"kind": "zauber"})
    with pytest.raises(PdfEditError, match="nur 3 Seiten"):
        L.add_layer(doc(), L.Watermark(text="X", pages="7"))


def test_preview_matches_numbering_of_real_result():
    data = doc()
    single = L.preview(data, L.HeaderFooter(footer_center="Seite {seite} von {seiten}", pages="2-"), 2)
    assert len(PdfReader(io.BytesIO(single)).pages) == 1
    assert "Seite 2 von 2" in text(single, 0)
    outside = L.preview(data, L.HeaderFooter(pages="2-"), 0)
    assert "von" not in text(outside, 0)


def test_audit_finds_text_in_layer_and_its_template():
    out = L.add_layer(doc(1), L.Watermark(text="Geheimprojekt"))
    found = {f.location for f in pdfaudit.audit(out, ["geheimprojekt"])}
    assert "page_text" in found and "page_layers" in found


# ---- Fremde Wasserzeichen ---------------------------------------------------------------------------------------
def foreign_doc() -> bytes:
    writer = PdfWriter(clone_from=io.BytesIO(doc(2)))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(b"/Artifact << /Type /Pagination /Subtype /Watermark >> BDC q BT /F1 40 Tf 100 400 Td "
                    b"(FREMD) Tj ET Q EMC")
    page[NameObject("/Contents")] = ArrayObject([page.raw_get("/Contents"), writer._add_object(stream)])
    annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Watermark"),
                              NameObject("/Rect"): ArrayObject([NumberObject(v) for v in (0, 0, 10, 10)]),
                              NameObject("/Contents"): TextStringObject("wm")})
    writer.pages[1][NameObject("/Annots")] = ArrayObject([writer._add_object(annot)])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_foreign_watermarks_are_detected_and_removed_but_ours_stay():
    data = L.add_layer(foreign_doc(), L.Watermark(text="UNSER"))
    assert L.foreign_watermarks(data) == {0: 1, 1: 1}
    clean = L.remove_foreign_watermarks(data)
    assert "FREMD" not in text(clean, 0) and "Seite 1 Inhalt" in text(clean, 0) and "UNSER" in text(clean, 0)
    assert L.foreign_watermarks(clean) == {}
    assert "/Annots" not in PdfReader(io.BytesIO(clean)).pages[1]
    assert_valid_pdf(clean)
    with pytest.raises(PdfEditError, match="Keine"):
        L.remove_foreign_watermarks(clean)
    assert L.remove_layers(clean)                                    # unsere Ebene lässt sich weiter entfernen
