"""Bilder in PDFs (Qt-frei, Block V7).

Zwei Arten von Bildern:

1. **Eingefügte Bilder** (PNG, JPG, WebP …) sind Objekte wie die Unterschrift: eine Stempel-Anmerkung mit eigenem
   Erscheinungsbild und der Kennung `/fckNotesImage` (Winkel, Größe). Sie lassen sich verschieben, skalieren (mit oder
   ohne Seitenverhältnis), drehen, durchsichtig machen, ersetzen, exportieren und – wie alle Objekte – einbrennen.
   JPEGs ohne EXIF-Drehung werden unverändert eingebettet (/DCTDecode), alles andere verlustfrei (/FlateDecode,
   Transparenz als /SMask). Fotos mit EXIF-Drehung werden einmal aufgerichtet und als JPEG (Qualität 95) neu kodiert.

2. **Bilder im Seiteninhalt** findet `page_images` im Content-Stream (`Do` auf Bild-XObjects, Inline-Bilder
   `BI … EI`, auch in Form-XObjects verschachtelt) samt Lage über die Transformationsmatrix. Exportieren geht immer
   (JPEG/JPEG 2000 unverändert, sonst PNG). Löschen und Zuschneiden ändern nur die gewählte Stelle (Content-Stream der
   Seite); Ersetzen wahlweise nur hier oder überall, wo dasselbe Bild-Objekt vorkommt. Inline-Bilder lassen sich nur
   exportieren und löschen, Bilder in Form-XObjects nur exportieren und (überall) ersetzen.

Zuschneiden schneidet die Pixel wirklich ab (neues Bild-Objekt), es legt keinen Clip über das alte – der weggeschnittene
Teil steht danach nicht mehr in der Datei, sofern das Bild nicht noch an anderer Stelle benutzt wird.
"""
from __future__ import annotations

import io
import math
import zlib
from dataclasses import dataclass

from pypdf.generic import (ArrayObject, ContentStream, DictionaryObject, FloatObject, NameObject, NumberObject,
                           StreamObject)

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError, open_reader

IMAGE_KEY = "/fckNotesImage"
FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF")
MAX_FILE = 60 * 1024 * 1024
MAX_PIXELS = 40_000_000
JPEG_QUALITY = 95
MAX_DEPTH = 8                                             # verschachtelte Form-XObjects
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ---- Matrizen (PDF-Konvention: Zeilenvektor, CTM' = M × CTM) ----------------------------------------------------
def multiply(m1: Matrix, m2: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def invert(m: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise PdfEditError("Das Bild ist auf eine Linie zusammengedrückt")
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_)


def _matrix_op(m: Matrix) -> list:
    return [FloatObject(round(v, 6)) for v in m]


# ---- Bilddateien einlesen und als XObject einbetten ------------------------------------------------------------
@dataclass(frozen=True)
class Encoded:
    width: int
    height: int
    data: bytes                     # kodierte Bilddaten (Stream-Inhalt)
    filter: str                     # /DCTDecode | /FlateDecode
    colorspace: str                 # /DeviceRGB | /DeviceGray
    alpha: bytes | None = None      # Flate-kodierte Transparenz (8 Bit), sonst None
    lossless: bool = True           # False = neu als JPEG kodiert

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as error:                          # pragma: no cover – Pillow fehlt im Build
        raise PdfEditError("Bilder brauchen das Paket Pillow – bitte installieren") from error
    return Image, ImageOps


def _open(content: bytes):
    Image, _ops = _pillow()
    if not content:
        raise PdfEditError("Leere Bilddatei")
    if len(content) > MAX_FILE:
        raise PdfEditError(f"Bild zu groß (höchstens {MAX_FILE // (1024 * 1024)} MB)")
    try:
        image = Image.open(io.BytesIO(content))
    except Exception as error:                            # noqa: BLE001 – Pillow wirft viele Arten
        raise PdfEditError("Kein lesbares Bild (PNG, JPG, WebP, GIF, BMP oder TIFF)") from error
    if image.format not in FORMATS:
        raise PdfEditError(f"Bildformat {image.format or 'unbekannt'} wird nicht unterstützt")
    width, height = image.size
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise PdfEditError(f"Bild zu groß ({width} × {height} Pixel, höchstens {MAX_PIXELS // 1_000_000} Megapixel)")
    return image


def picture_size(content: bytes) -> tuple[int, int]:
    """Pixelgröße wie angezeigt (EXIF-Drehung berücksichtigt)."""
    image = _open(content)
    width, height = image.size
    if _orientation(image) in (5, 6, 7, 8):
        width, height = height, width
    return width, height


def _orientation(image) -> int:
    try:
        return int(image.getexif().get(0x0112, 1) or 1)
    except Exception:                                     # noqa: BLE001 – kaputtes EXIF: wie gespeichert
        return 1


def encode_image(image, jpeg: bool = False) -> Encoded:
    """Pillow-Bild verlustfrei (Flate) oder – `jpeg` – als JPEG kodieren; Transparenz wird zur /SMask."""
    alpha = None
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    if has_alpha:
        image = image.convert("LA" if image.mode in ("LA",) else "RGBA")
        channel = image.getchannel("A")
        if channel.getextrema() != (255, 255):
            alpha = zlib.compress(channel.tobytes(), 6)
        image = image.convert("L" if image.mode == "LA" else "RGB")
    elif image.mode in ("1", "L", "I;16", "I", "F"):
        image = image.convert("L")
    elif image.mode != "RGB":
        image = image.convert("RGB")
    colorspace = "/DeviceGray" if image.mode == "L" else "/DeviceRGB"
    if jpeg:
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return Encoded(image.width, image.height, buffer.getvalue(), "/DCTDecode", colorspace, alpha, False)
    return Encoded(image.width, image.height, zlib.compress(image.tobytes(), 6), "/FlateDecode", colorspace, alpha)


def encode_picture(content: bytes) -> Encoded:
    """Bilddatei für das PDF: JPEG ohne Drehung unverändert, sonst aufgerichtet und kodiert."""
    _image, ImageOps = _pillow()
    image = _open(content)
    orientation = _orientation(image)
    try:
        if image.format == "JPEG" and orientation == 1 and image.mode in ("RGB", "L"):
            image.load()                                  # prüft, dass die Datei vollständig dekodierbar ist
            return Encoded(image.width, image.height, content, "/DCTDecode",
                           "/DeviceGray" if image.mode == "L" else "/DeviceRGB")
        if getattr(image, "n_frames", 1) > 1:
            image.seek(0)                                 # animiertes GIF/WebP, mehrseitiges TIFF: erstes Bild
        jpeg = image.format == "JPEG"
        image = ImageOps.exif_transpose(image) if orientation != 1 else image
        image.load()
        return encode_image(image, jpeg=jpeg)
    except PdfEditError:
        raise
    except Exception as error:                            # noqa: BLE001 – abgeschnittene/kaputte Dateien
        raise PdfEditError(f"Bild lässt sich nicht lesen ({error})") from error


def _stream(writer, encoded: Encoded):
    def make(data: bytes, colorspace: str, filter_: str):
        stream = StreamObject()
        stream._data = data
        stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                       NameObject("/Width"): NumberObject(encoded.width),
                       NameObject("/Height"): NumberObject(encoded.height),
                       NameObject("/ColorSpace"): NameObject(colorspace),
                       NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Filter"): NameObject(filter_)})
        return stream
    image = make(encoded.data, encoded.colorspace, encoded.filter)
    if encoded.alpha is not None:
        image[NameObject("/SMask")] = writer._add_object(make(encoded.alpha, "/DeviceGray", "/FlateDecode"))
    return image


def add_xobject(writer, encoded: Encoded):
    return writer._add_object(_stream(writer, encoded))


# ---- Exportieren --------------------------------------------------------------------------------------------------
def _filters(stream) -> list[str]:
    value = stream.get("/Filter")
    if value is None:
        return []
    value = value.get_object()
    if isinstance(value, ArrayObject):
        return [str(v) for v in value]
    return [str(value)]


def export_stream(stream) -> tuple[bytes, str]:
    """Bild-XObject als Datei: JPEG/JPEG 2000 unverändert, alles andere als PNG. Liefert (Bytes, Endung)."""
    filters = _filters(stream)
    if filters == ["/DCTDecode"] and "/SMask" not in stream and str(stream.get("/ColorSpace", "")) != "/DeviceCMYK":
        return bytes(stream._data), "jpg"
    if filters == ["/JPXDecode"]:
        return bytes(stream._data), "jp2"
    try:
        image = stream.decode_as_image()
    except Exception as error:                            # noqa: BLE001 – exotische Farbräume/Filter
        raise PdfEditError(f"Bild lässt sich nicht dekodieren ({error})") from error
    if image is None:
        raise PdfEditError("Bild lässt sich nicht dekodieren")
    return _png(image), "png"


def _png(image) -> bytes:
    if image.mode not in ("1", "L", "LA", "RGB", "RGBA", "P", "I;16"):
        image = image.convert("RGBA" if "A" in image.mode else "RGB")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=False)
    return buffer.getvalue()


# ---- Eingefügte Bilder (Anmerkung) ----------------------------------------------------------------------------
@dataclass(frozen=True)
class ImageMeta:
    angle: float                    # Grad, im Uhrzeigersinn (wie angezeigt)
    width: float                    # Größe des ungedrehten Bilds in Punkten
    height: float
    opacity: float = 1.0


def is_image(annot) -> bool:
    return IMAGE_KEY in annot and str(annot.get("/Subtype", "")) == "/Stamp"


def meta_of(annot) -> ImageMeta:
    info = annot[IMAGE_KEY].get_object()
    size = [float(v) for v in info.get("/Size", [1, 1])]
    try:
        opacity = float(annot.get("/CA", 1))
    except (TypeError, ValueError):
        opacity = 1.0
    return ImageMeta(float(info.get("/Angle", 0)), max(size[0], 0.1), max(size[1], 0.1), opacity)


def _norm_angle(angle: float) -> float:
    angle = round(float(angle), 2) % 360
    return 0.0 if abs(angle - 360) < 0.01 else angle


def rotated_size(width: float, height: float, angle: float) -> tuple[float, float]:
    rad = math.radians(angle)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    return width * c + height * s, width * s + height * c


def is_right_angle(angle: float) -> bool:
    return abs((angle + 0.005) % 90) < 0.01


def _draw_matrix(meta: ImageMeta) -> Matrix:
    """Einheitsquadrat des Bilds → Rahmen 0..W × 0..H (y nach oben), gedreht um `angle` im Uhrzeigersinn."""
    rad = -math.radians(meta.angle)                       # im Uhrzeigersinn = negativer Winkel bei y nach oben
    c, s = math.cos(rad), math.sin(rad)
    if is_right_angle(meta.angle):                        # exakte Werte: keine Haarlinien an den Rändern
        c, s = round(c), round(s)
    scale = (meta.width, 0.0, 0.0, meta.height, 0.0, 0.0)
    rotation = (c, s, -s, c, 0.0, 0.0)
    m = multiply(scale, rotation)
    corners = [apply(m, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
    min_x, min_y = min(p[0] for p in corners), min(p[1] for p in corners)
    return m[0], m[1], m[2], m[3], m[4] - min_x, m[5] - min_y


def _appearance(writer, geom: A.PageGeom, image_ref, meta: ImageMeta):
    w, h = rotated_size(meta.width, meta.height, meta.angle)
    resources = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image_ref})})
    ops = ["q"]
    if meta.opacity < 1:
        state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                                  NameObject("/CA"): FloatObject(round(meta.opacity, 3)),
                                  NameObject("/ca"): FloatObject(round(meta.opacity, 3))})
        resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS0"): state})
        ops.append("/GS0 gs")
    ops.append(" ".join(A._num(v) for v in _draw_matrix(meta)) + " cm /Im0 Do Q")
    return A._form(writer, " ".join(ops), [0, 0, max(w, 0.1), max(h, 0.1)], resources, geom.matrix())


def _write(writer, page, annot, image_ref, meta: ImageMeta, rect_view: A.Rect) -> None:
    geom = A.geometry_of(page)
    annot[NameObject("/Rect")] = A._floats(geom.rect_to_pdf(rect_view))
    annot[NameObject(IMAGE_KEY)] = DictionaryObject({NameObject("/Angle"): FloatObject(meta.angle),
                                         NameObject("/Size"): A._floats([meta.width, meta.height])})
    if meta.opacity < 1:
        annot[NameObject("/CA")] = FloatObject(round(meta.opacity, 3))
    elif "/CA" in annot:
        del annot["/CA"]
    annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): _appearance(writer, geom, image_ref, meta)})


def natural_rect(geom: A.PageGeom, rect_view: A.Rect, aspect: float, width_px: int) -> A.Rect:
    """Klick (kein Rahmen) → Bild in natürlicher Größe (96 dpi), höchstens 60 % der Seite; sonst einpassen."""
    x0, y0, x1, y1 = rect_view
    if abs(x1 - x0) >= 8 and abs(y1 - y0) >= 8:
        from notex.core.pdfforms import fit_rect
        return fit_rect((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), aspect)
    page_w, page_h = geom.size
    w = width_px * 0.75
    h = w / aspect
    factor = min(1.0, page_w * 0.6 / w, page_h * 0.6 / h)
    w, h = w * factor, h * factor
    x0 = min(max(0.0, x0), max(0.0, page_w - w))
    y0 = min(max(0.0, y0), max(0.0, page_h - h))
    return x0, y0, x0 + w, y0 + h


def insert_image(data: bytes, page_index: int, rect_view: A.Rect, content: bytes, keep_aspect: bool = True,
                 opacity: float = 1.0, label: str = "Bild") -> bytes:
    """Bild einfügen. Ein Klick setzt die natürliche Größe; ein aufgezogener Rahmen wird mit `keep_aspect` eingepasst,
    sonst gefüllt (verzerrt)."""
    encoded = encode_picture(content)
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    x0, y0, x1, y1 = rect_view
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if keep_aspect or x1 - x0 < 8 or y1 - y0 < 8:
        rect = natural_rect(geom, (x0, y0, x1, y1), encoded.aspect, encoded.width)
    else:
        rect = (x0, y0, x1, y1)
    meta = ImageMeta(0.0, rect[2] - rect[0], rect[3] - rect[1], max(0.05, min(float(opacity), 1.0)))
    annot = A._base("/Stamp", geom.rect_to_pdf(rect), "#000000", label)
    annot[NameObject("/Name")] = NameObject("/fckNotesImage")
    ref = add_xobject(writer, encoded)
    _write(writer, page, annot, ref, meta, rect)
    annot[NameObject("/P")] = page.indirect_reference
    A._annots(page).append(writer._add_object(annot))
    return A.finish(writer)


def _image_annot(writer, page_index: int, position: int):
    page = A._page(writer, page_index)
    annots = A._annots(page)
    if not 0 <= position < len(annots):
        raise PdfEditError("Dieses Objekt gibt es nicht mehr")
    annot = annots[position].get_object()
    if not is_image(annot):
        raise PdfEditError("Das ist kein eingefügtes Bild")
    return page, annot


def _image_ref(annot):
    try:
        form = annot["/AP"].get_object()["/N"].get_object()
        return form["/Resources"].get_object()["/XObject"].get_object().raw_get("/Im0")
    except (KeyError, AttributeError) as error:
        raise PdfEditError("Das Bild hat kein Erscheinungsbild mehr") from error


def _view_rect(page, annot) -> A.Rect:
    x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
    return A.geometry_of(page).rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))


def reframe(writer, page, annot, rect_view: A.Rect) -> None:
    """Neuer Rahmen (Verschieben/Größe ändern). Bei schiefem Winkel bleibt das Seitenverhältnis erhalten."""
    meta = meta_of(annot)
    old = _view_rect(page, annot)
    x0, y0, x1, y1 = rect_view
    nw, nh = x1 - x0, y1 - y0
    if is_right_angle(meta.angle):
        width, height = (nh, nw) if round(meta.angle / 90) % 2 else (nw, nh)
    else:
        scale = min(nw / max(old[2] - old[0], 1e-6), nh / max(old[3] - old[1], 1e-6))
        width, height = meta.width * scale, meta.height * scale
    new = ImageMeta(meta.angle, max(width, 1.0), max(height, 1.0), meta.opacity)
    w, h = rotated_size(new.width, new.height, new.angle)
    _write(writer, page, annot, _image_ref(annot), new, (x0, y0, x0 + w, y0 + h))


def _centered(old: A.Rect, w: float, h: float) -> A.Rect:
    cx, cy = (old[0] + old[2]) / 2, (old[1] + old[3]) / 2
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def rotate_image(data: bytes, page_index: int, position: int, degrees: float, absolute: bool = False) -> bytes:
    """Bild um `degrees` im Uhrzeigersinn drehen (oder – `absolute` – auf diesen Winkel setzen); Mitte bleibt."""
    writer = A.open_writer(data)
    page, annot = _image_annot(writer, page_index, position)
    meta = meta_of(annot)
    angle = _norm_angle(degrees if absolute else meta.angle + degrees)
    new = ImageMeta(angle, meta.width, meta.height, meta.opacity)
    w, h = rotated_size(new.width, new.height, angle)
    _write(writer, page, annot, _image_ref(annot), new, _centered(_view_rect(page, annot), w, h))
    return A.finish(writer)


def set_image_opacity(data: bytes, page_index: int, position: int, opacity: float) -> bytes:
    writer = A.open_writer(data)
    page, annot = _image_annot(writer, page_index, position)
    meta = meta_of(annot)
    new = ImageMeta(meta.angle, meta.width, meta.height, max(0.05, min(float(opacity), 1.0)))
    _write(writer, page, annot, _image_ref(annot), new, _view_rect(page, annot))
    return A.finish(writer)


def replace_inserted(data: bytes, page_index: int, position: int, content: bytes) -> bytes:
    """Anderes Bild in denselben Rahmen: Breite und Winkel bleiben, die Höhe folgt dem neuen Seitenverhältnis."""
    encoded = encode_picture(content)
    writer = A.open_writer(data)
    page, annot = _image_annot(writer, page_index, position)
    meta = meta_of(annot)
    new = ImageMeta(meta.angle, meta.width, meta.width / encoded.aspect, meta.opacity)
    w, h = rotated_size(new.width, new.height, new.angle)
    _write(writer, page, annot, add_xobject(writer, encoded), new, _centered(_view_rect(page, annot), w, h))
    return A.finish(writer)


def export_inserted(data: bytes, page_index: int, position: int) -> tuple[bytes, str]:
    reader = open_reader(data)
    if not 0 <= page_index < len(reader.pages):
        raise PdfEditError(f"Seite {page_index + 1} gibt es nicht")
    annots = reader.pages[page_index].get("/Annots", None) or []
    if not 0 <= position < len(annots) or not is_image(annots[position].get_object()):
        raise PdfEditError("Das ist kein eingefügtes Bild")
    return export_stream(_image_ref(annots[position].get_object()).get_object())


def image_meta(data: bytes, page_index: int, position: int) -> ImageMeta:
    reader = open_reader(data)
    annots = reader.pages[page_index].get("/Annots", None) or [] if 0 <= page_index < len(reader.pages) else []
    if not 0 <= position < len(annots) or not is_image(annots[position].get_object()):
        raise PdfEditError("Das ist kein eingefügtes Bild")
    return meta_of(annots[position].get_object())


# ---- Bilder im Seiteninhalt ---------------------------------------------------------------------------------------
INLINE_KEYS = {"/W": "/Width", "/H": "/Height", "/BPC": "/BitsPerComponent", "/CS": "/ColorSpace", "/F": "/Filter",
               "/DP": "/DecodeParms", "/IM": "/ImageMask", "/D": "/Decode", "/I": "/Interpolate"}
INLINE_NAMES = {"/G": "/DeviceGray", "/RGB": "/DeviceRGB", "/CMYK": "/DeviceCMYK", "/I": "/Indexed",
                "/AHx": "/ASCIIHexDecode", "/A85": "/ASCII85Decode", "/LZW": "/LZWDecode", "/Fl": "/FlateDecode",
                "/RL": "/RunLengthDecode", "/CCF": "/CCITTFaxDecode", "/DCT": "/DCTDecode"}
KIND_LABELS = {"xobject": "Bild", "inline": "Inline-Bild", "form": "Bild in Gruppe"}


@dataclass(frozen=True)
class PageImage:
    page: int
    index: int                      # n-tes Bild der Seite in Zeichenreihenfolge
    kind: str                       # xobject | inline | form (in einem Form-XObject verschachtelt)
    name: str                       # Ressourcenname (/Im0), "" bei Inline-Bildern
    path: tuple[str, ...]           # Form-XObjects auf dem Weg zum Bild (nur kind == "form")
    op: int                         # Position im Content-Stream der Seite; -1 = verschachtelt
    rect: A.Rect                    # umschließendes Rechteck in Ansichts-Punkten
    matrix: Matrix                  # Transformationsmatrix (Einheitsquadrat → Benutzer-Koordinaten)
    width_px: int
    height_px: int
    bits: int = 8
    colorspace: str = ""
    filters: tuple[str, ...] = ()
    smask: bool = False
    stencil: bool = False           # /ImageMask: einfarbige Maske
    objnum: int = 0                 # Objektnummer (0 = inline)
    uses: int = 1                   # so oft wird dasselbe Bild-Objekt im Dokument gezeichnet

    @property
    def can_delete(self) -> bool:
        return self.op >= 0

    @property
    def can_crop(self) -> bool:
        return self.kind == "xobject" and self.op >= 0 and not self.stencil

    @property
    def can_replace_here(self) -> bool:
        return self.kind == "xobject" and self.op >= 0

    @property
    def can_replace_all(self) -> bool:
        return self.kind in ("xobject", "form") and self.objnum > 0

    @property
    def export_ext(self) -> str:
        if self.filters == ("/DCTDecode",) and not self.smask and self.colorspace != "/DeviceCMYK":
            return "jpg"
        if self.filters == ("/JPXDecode",):
            return "jp2"
        return "png"

    @property
    def format_label(self) -> str:
        return {"jpg": "JPEG", "jp2": "JPEG 2000"}.get(self.export_ext, "Pixel (PNG)")

    @property
    def dpi(self) -> float:
        """Effektive Auflösung auf der Seite (kleinere Achse)."""
        a, b, c, d, _e, _f = self.matrix
        width_pt, height_pt = math.hypot(a, b), math.hypot(c, d)
        if width_pt < 1e-6 or height_pt < 1e-6:
            return 0.0
        return min(self.width_px / width_pt, self.height_px / height_pt) * 72

    def describe(self) -> str:
        bits = f", {self.uses}× im Dokument" if self.uses > 1 else ""
        return (f"{KIND_LABELS.get(self.kind, self.kind)} {self.width_px} × {self.height_px} px, "
                f"{self.format_label}, ≈ {round(self.dpi)} dpi{bits}")


def _resources(node):
    """Ressourcen einer Seite oder eines Form-XObjects (bei Seiten auch geerbt)."""
    seen = 0
    while node is not None and seen < 32:
        value = node.get("/Resources")
        if value is not None:
            return value.get_object()
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
        seen += 1
    return DictionaryObject()


def _xobjects(resources) -> DictionaryObject:
    value = resources.get("/XObject") if resources is not None else None
    return value.get_object() if value is not None else DictionaryObject()


def _inline_stream(settings, resources) -> StreamObject:
    """Inline-Bild (BI … ID … EI) als gewöhnliches Bild-XObject, damit es sich dekodieren lässt."""
    stream = StreamObject()
    for key, value in settings.items():
        key = INLINE_KEYS.get(str(key), str(key))
        if isinstance(value, NameObject):
            name = INLINE_NAMES.get(str(value), str(value))
            if key == "/ColorSpace" and name not in INLINE_NAMES.values():
                found = (resources.get("/ColorSpace") or DictionaryObject()).get_object().get(name)
                value = found if found is not None else NameObject(name)
            else:
                value = NameObject(name)
        elif isinstance(value, ArrayObject):
            value = ArrayObject([NameObject(INLINE_NAMES.get(str(v), str(v))) if isinstance(v, NameObject) else v
                                 for v in value])
        stream[NameObject(key)] = value
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Image")
    return stream


def _int(value, default: int = 0) -> int:
    try:
        return int(value.get_object() if hasattr(value, "get_object") else value)
    except (TypeError, ValueError):
        return default


class _Scanner:
    def __init__(self, pdf, page, page_index: int) -> None:
        self.pdf = pdf
        self.page = page
        self.page_index = page_index
        self.geom = A.geometry_of(page)
        self.out: list[PageImage] = []
        self.forms: dict[int, list] = {}

    def run(self) -> list[PageImage]:
        try:
            content = self.page.get_contents()
            ops = content.operations if content is not None else []
        except Exception as error:                        # noqa: BLE001 – kaputter Content-Stream
            raise PdfEditError(f"Seite {self.page_index + 1}: Inhalt nicht lesbar ({error})") from error
        self.walk(ops, _resources(self.page), IDENTITY, (), True, 0, frozenset())
        return self.out

    def view_rect(self, ctm: Matrix) -> A.Rect:
        points = [self.geom.to_view(*apply(ctm, x, y)) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)

    def add(self, kind: str, name: str, path, op: int, ctm: Matrix, stream, objnum: int) -> None:
        cs = stream.get("/ColorSpace")
        cs = cs.get_object() if cs is not None else None
        colorspace = str(cs[0]) if isinstance(cs, ArrayObject) and cs else str(cs or "")
        self.out.append(PageImage(
            self.page_index, len(self.out), kind, name, tuple(path), op, self.view_rect(ctm), ctm,
            _int(stream.get("/Width"), 1), _int(stream.get("/Height"), 1), _int(stream.get("/BitsPerComponent"), 1),
            colorspace, tuple(_filters(stream)), "/SMask" in stream, bool(stream.get("/ImageMask", False)), objnum))

    def walk(self, ops, resources, start: Matrix, path, top: bool, depth: int, seen: frozenset) -> None:
        ctm, stack = start, []
        xobjects = _xobjects(resources)
        for position, (operands, operator) in enumerate(ops):
            if operator == b"q":
                stack.append(ctm)
            elif operator == b"Q":
                ctm = stack.pop() if stack else start
            elif operator == b"cm" and len(operands) >= 6:
                try:
                    ctm = multiply(tuple(float(v) for v in operands[:6]), ctm)
                except (TypeError, ValueError):
                    continue
            elif operator == b"INLINE IMAGE":
                settings = operands.get("settings", DictionaryObject()) if isinstance(operands, dict) else {}
                stream = _inline_stream(settings, resources)
                self.add("inline", "", path, position if top else -1, ctm, stream, 0)
            elif operator == b"Do" and operands:
                name = str(operands[0])
                ref = xobjects.raw_get(name) if name in xobjects else None
                if ref is None:
                    continue
                xobject = ref.get_object()
                objnum = int(getattr(ref, "idnum", 0) or 0)
                subtype = str(xobject.get("/Subtype", ""))
                if subtype == "/Image":
                    self.add("xobject" if top else "form", name, path, position if top else -1, ctm, xobject, objnum)
                elif subtype == "/Form" and depth < MAX_DEPTH and (objnum == 0 or objnum not in seen):
                    try:
                        if objnum not in self.forms or objnum == 0:
                            self.forms[objnum] = ContentStream(xobject, self.pdf).operations
                        form_ops = self.forms[objnum]
                        matrix = tuple(float(v) for v in xobject.get("/Matrix", IDENTITY))
                    except Exception:                     # noqa: BLE001 – kaputte Gruppe überspringen
                        continue
                    inner = xobject.get("/Resources")
                    self.walk(form_ops, inner.get_object() if inner is not None else resources,
                              multiply(matrix, ctm), (*path, name), False, depth + 1, seen | {objnum})


def _scan(pdf, page, page_index: int) -> list[PageImage]:
    return _Scanner(pdf, page, page_index).run()


def page_images(data: bytes, page_index: int) -> list[PageImage]:
    """Alle Bilder, die auf der Seite gezeichnet werden (ohne Anmerkungen), in Zeichenreihenfolge."""
    reader = open_reader(data)
    if not 0 <= page_index < len(reader.pages):
        return []
    return _scan(reader, reader.pages[page_index], page_index)


def all_images(data: bytes, progress=None) -> list[PageImage]:
    """Bilder aller Seiten; `uses` zählt, wie oft dasselbe Bild-Objekt im Dokument vorkommt.
    `progress(done, total)` darf False liefern, um abzubrechen."""
    reader = open_reader(data)
    out: list[PageImage] = []
    total = len(reader.pages)
    for index, page in enumerate(reader.pages):
        try:
            out.extend(_scan(reader, page, index))
        except PdfEditError:
            continue
        if progress is not None and progress(index + 1, total) is False:
            break
    counts: dict[int, int] = {}
    for image in out:
        if image.objnum:
            counts[image.objnum] = counts.get(image.objnum, 0) + 1
    from dataclasses import replace
    return [replace(image, uses=counts.get(image.objnum, 1)) if image.objnum else image for image in out]


def uses_of(data: bytes, page_index: int, index: int) -> int:
    image = _find(page_images(data, page_index), index)
    if not image.objnum:
        return 1
    return sum(1 for other in all_images(data) if other.objnum == image.objnum)


def hit(images: list[PageImage], x: float, y: float, tolerance: float = 1.0) -> PageImage | None:
    """Kleinstes Bild unter dem Punkt (kleine liegen oft auf großen Hintergrundbildern)."""
    candidates = [image for image in images
                  if image.rect[0] - tolerance <= x <= image.rect[2] + tolerance
                  and image.rect[1] - tolerance <= y <= image.rect[3] + tolerance]
    if not candidates:
        return None
    return min(reversed(candidates), key=lambda i: (i.rect[2] - i.rect[0]) * (i.rect[3] - i.rect[1]))


def _find(images: list[PageImage], index: int) -> PageImage:
    if not 0 <= index < len(images):
        raise PdfEditError("Dieses Bild gibt es nicht mehr – bitte neu auswählen")
    return images[index]


def _stream_of(page, image: PageImage):
    resources = _resources(page)
    for name in image.path:
        form = _xobjects(resources)[name].get_object()
        inner = form.get("/Resources")
        resources = inner.get_object() if inner is not None else resources
    ref = _xobjects(resources).raw_get(image.name)
    return ref, ref.get_object()


def export_content_image(data: bytes, page_index: int, index: int) -> tuple[bytes, str]:
    """Bild aus dem Seiteninhalt als Datei (JPEG/JPEG 2000 unverändert, sonst PNG). Liefert (Bytes, Endung)."""
    reader = open_reader(data)
    if not 0 <= page_index < len(reader.pages):
        raise PdfEditError(f"Seite {page_index + 1} gibt es nicht")
    page = reader.pages[page_index]
    image = _find(_scan(reader, page, page_index), index)
    if image.kind == "inline":
        return export_stream(_inline_by_order(reader, page, image))
    return export_stream(_stream_of(page, image)[1])


def _inline_by_order(pdf, page, image: PageImage) -> StreamObject:
    """Daten des Inline-Bilds: erneut durch den Inhalt gehen und das passende `BI … EI` nehmen."""
    found: list[StreamObject] = []

    def collect(ops, resources, depth: int) -> None:
        xobjects = _xobjects(resources)
        for operands, operator in ops:
            if operator == b"INLINE IMAGE":
                stream = _inline_stream(operands.get("settings", DictionaryObject()), resources)
                stream._data = bytes(operands.get("data", b""))
                found.append(stream)
            elif operator == b"Do" and operands and depth < MAX_DEPTH and str(operands[0]) in xobjects:
                xobject = xobjects[str(operands[0])].get_object()
                if str(xobject.get("/Subtype", "")) == "/Image":
                    found.append(None)                    # Platzhalter: zählt in derselben Reihenfolge mit
                elif str(xobject.get("/Subtype", "")) == "/Form":
                    inner = xobject.get("/Resources")
                    collect(ContentStream(xobject, pdf).operations,
                            inner.get_object() if inner is not None else resources, depth + 1)

    content = page.get_contents()
    collect(content.operations if content is not None else [], _resources(page), 0)
    if image.index >= len(found) or found[image.index] is None:
        raise PdfEditError("Inline-Bild nicht gefunden")
    return found[image.index]


# ---- Inhalt ändern ------------------------------------------------------------------------------------------------
def _own_xobjects(page) -> DictionaryObject:
    """Eigene Kopie der Seiten-Ressourcen (andere Seiten können dasselbe Wörterbuch teilen)."""
    resources = DictionaryObject(_resources(page))
    xobjects = DictionaryObject(_xobjects(resources))
    resources[NameObject("/XObject")] = xobjects
    page[NameObject("/Resources")] = resources
    return xobjects


def _free_name(xobjects, stem: str = "/fckImg") -> str:
    number = 1
    while f"{stem}{number}" in xobjects:
        number += 1
    return f"{stem}{number}"


def _set_contents(writer, page, ops) -> None:
    content = ContentStream(None, writer)
    content.operations = ops
    from pypdf.generic import DecodedStreamObject
    stream = DecodedStreamObject()
    stream.set_data(content.get_data())
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())


def _check_op(ops, image: PageImage) -> None:
    if not 0 <= image.op < len(ops):
        raise PdfEditError("Seiteninhalt hat sich geändert – bitte neu auswählen")
    operands, operator = ops[image.op]
    expected = b"INLINE IMAGE" if image.kind == "inline" else b"Do"
    if operator != expected or (image.kind != "inline" and str(operands[0]) != image.name):
        raise PdfEditError("Seiteninhalt hat sich geändert – bitte neu auswählen")


def _drop_unused(page, ops, name: str) -> None:
    """Ressourcennamen entfernen, wenn die Seite das Bild nicht mehr zeichnet (Gruppen ohne eigene Ressourcen
    könnten es noch brauchen – dann bleibt er)."""
    if not name or any(op == b"Do" and operands and str(operands[0]) == name for operands, op in ops):
        return
    for ref in _xobjects(_resources(page)).values():
        other = ref.get_object()
        if str(other.get("/Subtype", "")) == "/Form" and "/Resources" not in other:
            return
    xobjects = _own_xobjects(page)
    if name in xobjects:
        del xobjects[name]


def _open_page(data: bytes, page_index: int):
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    content = page.get_contents()
    ops = list(content.operations) if content is not None else []
    return writer, page, ops


def delete_content_image(data: bytes, page_index: int, index: int) -> bytes:
    """Bild an dieser Stelle aus dem Seiteninhalt entfernen. Wird das Bild-Objekt sonst nirgends gezeichnet, verschwindet
    es ganz aus der Datei."""
    writer, page, ops = _open_page(data, page_index)
    image = _find(_scan(writer, page, page_index), index)
    if not image.can_delete:
        raise PdfEditError("Bilder in Gruppen (Form-XObjects) lassen sich nur exportieren oder überall ersetzen")
    _check_op(ops, image)
    del ops[image.op]
    _drop_unused(page, ops, image.name)
    _set_contents(writer, page, ops)
    return A.finish(writer)


def _replace_op(writer, page, ops, image: PageImage, encoded: Encoded, inner: Matrix | None) -> None:
    xobjects = _own_xobjects(page)
    name = _free_name(xobjects)
    xobjects[NameObject(name)] = add_xobject(writer, encoded)
    draw = [([NameObject(name)], b"Do")]
    if inner is not None and inner != IDENTITY:
        draw = [([], b"q"), (_matrix_op(inner), b"cm"), *draw, ([], b"Q")]
    ops[image.op:image.op + 1] = draw
    _drop_unused(page, ops, image.name)
    _set_contents(writer, page, ops)


def crop_box(image: PageImage, geom: A.PageGeom, rect_view: A.Rect) -> tuple[int, int, int, int]:
    """Pixel-Ausschnitt (links, oben, rechts, unten) des Bilds, der im Ansichts-Rechteck liegt."""
    inverse = invert(image.matrix)
    x0, y0, x1, y1 = rect_view
    units = [apply(inverse, *geom.to_pdf(x, y)) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    u0 = max(0.0, min(u for u, _v in units))
    u1 = min(1.0, max(u for u, _v in units))
    v0 = max(0.0, min(v for _u, v in units))
    v1 = min(1.0, max(v for _u, v in units))
    w, h = image.width_px, image.height_px
    left, right = math.floor(u0 * w + 1e-6), math.ceil(u1 * w - 1e-6)
    top, bottom = math.floor((1 - v1) * h + 1e-6), math.ceil((1 - v0) * h - 1e-6)
    if right - left < 1 or bottom - top < 1:
        raise PdfEditError("Der Rahmen liegt nicht auf dem Bild")
    return left, top, right, bottom


def crop_content_image(data: bytes, page_index: int, index: int, rect_view: A.Rect) -> bytes:
    """Bild auf den Rahmen zuschneiden: die Pixel außerhalb werden entfernt (neues Bild-Objekt nur für diese Stelle),
    der sichtbare Teil bleibt genau an seinem Platz."""
    writer, page, ops = _open_page(data, page_index)
    image = _find(_scan(writer, page, page_index), index)
    if not image.can_crop:
        raise PdfEditError("Dieses Bild lässt sich nicht zuschneiden (Inline-Bild, Maske oder in einer Gruppe)")
    _check_op(ops, image)
    left, top, right, bottom = crop_box(image, A.geometry_of(page), rect_view)
    if (left, top, right, bottom) == (0, 0, image.width_px, image.height_px):
        raise PdfEditError("Der Rahmen umfasst das ganze Bild – nichts zuzuschneiden")
    stream = _stream_of(page, image)[1]
    try:
        picture = stream.decode_as_image()
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"Bild lässt sich nicht dekodieren ({error})") from error
    if picture is None or picture.size != (image.width_px, image.height_px):
        raise PdfEditError("Bild lässt sich nicht dekodieren")
    encoded = encode_image(picture.crop((left, top, right, bottom)),
                           jpeg=bool({"/DCTDecode", "/JPXDecode"} & set(image.filters)))
    w, h = image.width_px, image.height_px
    inner = ((right - left) / w, 0.0, 0.0, (bottom - top) / h, left / w, 1 - bottom / h)
    _replace_op(writer, page, ops, image, encoded, inner)
    return A.finish(writer)


def fit_matrix(image: PageImage, aspect: float) -> Matrix:
    """Neues Bild mit Seitenverhältnis `aspect` mittig in den Rahmen des alten einpassen."""
    a, b, c, d, _e, _f = image.matrix
    frame = math.hypot(a, b) / max(math.hypot(c, d), 1e-9)
    if aspect > frame:
        sv = frame / aspect
        return 1.0, 0.0, 0.0, sv, 0.0, (1 - sv) / 2
    su = aspect / frame
    return su, 0.0, 0.0, 1.0, (1 - su) / 2, 0.0


def replace_content_image(data: bytes, page_index: int, index: int, content: bytes, everywhere: bool = False,
                          keep_aspect: bool = True) -> bytes:
    """Bild ersetzen – nur an dieser Stelle (eingepasst oder gestreckt) oder überall, wo dasselbe Bild-Objekt
    gezeichnet wird (dort füllt das neue Bild jeweils den alten Rahmen)."""
    encoded = encode_picture(content)
    writer, page, ops = _open_page(data, page_index)
    image = _find(_scan(writer, page, page_index), index)
    if everywhere:
        if not image.can_replace_all:
            raise PdfEditError("Inline-Bilder lassen sich nicht ersetzen – nur exportieren oder löschen")
        ref, _old = _stream_of(page, image)
        if not hasattr(ref, "idnum"):
            raise PdfEditError("Bild ist kein eigenes Objekt – bitte „nur hier“ ersetzen")
        writer._replace_object(ref, _stream(writer, encoded))
        return A.finish(writer)
    if not image.can_replace_here:
        raise PdfEditError("Hier geht nur „überall ersetzen“ (Bild in einer Gruppe) – Inline-Bilder gar nicht")
    _check_op(ops, image)
    _replace_op(writer, page, ops, image, encoded, fit_matrix(image, encoded.aspect) if keep_aspect else None)
    return A.finish(writer)
