"""Komprimieren, Reparieren und Konvertieren (Qt-frei, Block V9).

- **Komprimieren** (pikepdf/qpdf): Bilder über der Ziel-Auflösung verkleinern und – bei Fotos – als JPEG neu kodieren,
  Ungenutztes entfernen, Objekt-Streams erzeugen, Streams neu packen. Profile „Hohe Qualität“, „Standard“, „Klein“,
  „Nur verlustfrei“ oder eigene Werte. Neu kodierte Bilder sind verlustbehaftet – das Ergebnis sagt es. Frühere
  Speicherstände (inkrementelle Updates) und nicht erreichbare Objekte fallen dabei weg; Inhalte kommen nie hinzu.
- **Reparieren**: qpdf baut die Querverweistabelle neu auf (abgeschnittene oder beschädigte Dateien).
- **Bilder → PDF**: eine Seite je Bild, „wie Bild“ (Größe aus der Bildauflösung) oder A4/Letter mit Rand.
- **PDF → PNG/JPG**: Seiten mit PDFium rendern (DPI, Seitenbereich), nie eine vorhandene Datei überschreiben.
- **PDF → Text/Markdown**: Text je Seite; Markdown erkennt Überschriften an der Schriftgröße, fügt Zeilen zu Absätzen
  zusammen, trennt getrennte Wörter und macht Aufzählungen zu Listen.
"""
from __future__ import annotations

import io
import math
import re
import zlib
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from notex.core import pdfimages as I
from notex.core.pdfpages import PdfEditError

MAX_RENDER_PIXELS = 120_000_000                           # je Seite (z. B. A4 bei 1200 dpi ≈ 140 MP)
MAX_PAGE = 14400.0                                        # größte Seitenlänge laut PDF-Norm (200 Zoll)
PAGE_SIZES = {"a4": (595.28, 841.89), "letter": (612.0, 792.0)}
MM = 72 / 25.4


def _pikepdf():
    try:
        import pikepdf
    except ImportError as error:                          # pragma: no cover – fehlt nur in kaputten Builds
        raise PdfEditError("Komprimieren braucht das Paket pikepdf – bitte installieren") from error
    return pikepdf


def _open(data: bytes):
    pikepdf = _pikepdf()
    try:
        return pikepdf.Pdf.open(io.BytesIO(data))
    except pikepdf.PasswordError as error:
        raise PdfEditError("Das PDF ist verschlüsselt – erst entsperren") from error
    except Exception as error:                            # noqa: BLE001 – qpdf wirft eigene Fehlerarten
        raise PdfEditError(f"PDF nicht lesbar ({error})") from error


# ---- Komprimieren -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    dpi: int = 150                                        # 0 = Bilder nicht verkleinern
    jpeg_quality: int = 80                                # 0 = Bilder nicht neu kodieren
    remove_unused: bool = True
    object_streams: bool = True
    recompress_flate: bool = False                        # Streams stärker packen (langsamer)

    @property
    def lossy(self) -> bool:
        return self.dpi > 0 or self.jpeg_quality > 0

    def checked(self) -> "Profile":
        dpi = int(self.dpi)
        quality = int(self.jpeg_quality)
        if dpi and not 36 <= dpi <= 1200:
            raise PdfEditError("Bild-Auflösung: 36 bis 1200 dpi (0 = nicht verkleinern)")
        if quality and not 10 <= quality <= 100:
            raise PdfEditError("JPEG-Qualität: 10 bis 100 (0 = nicht neu kodieren)")
        return replace(self, dpi=dpi, jpeg_quality=quality)


PROFILES = {
    "high": Profile("high", "Hohe Qualität (300 dpi, JPEG 90)", 300, 90),
    "standard": Profile("standard", "Standard (150 dpi, JPEG 80)", 150, 80),
    "small": Profile("small", "Klein (96 dpi, JPEG 60)", 96, 60, recompress_flate=True),
    "lossless": Profile("lossless", "Nur verlustfrei (Bilder bleiben, Struktur wird gepackt)", 0, 0,
                        recompress_flate=True),
}


@dataclass(frozen=True)
class CompressResult:
    data: bytes
    before: int
    after: int
    images_changed: int
    images_skipped: int
    lossy: bool

    @property
    def saved_percent(self) -> float:
        return 100.0 * (self.before - self.after) / max(self.before, 1)

    def summary(self) -> str:
        percent = self.saved_percent
        shown = f"{percent:.0f}" if percent < 99.5 else f"{percent:.1f}".replace(".", ",")
        change = (f"{human(self.before)} → {human(self.after)} (−{shown} %)"
                  if self.after < self.before else f"{human(self.before)} → {human(self.after)} (nicht kleiner)")
        images = f", {self.images_changed} Bild(er) neu kodiert" if self.images_changed else ""
        return change + images + (" – verlustbehaftet" if self.images_changed and self.lossy else "")


def human(size: int) -> str:
    for unit, factor in (("MB", 1024 * 1024), ("KB", 1024)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}".replace(".", ",")
    return f"{size} B"


def _display_sizes(data: bytes) -> dict[int, tuple[float, float]]:
    """Objektnummer → größte gezeichnete Breite/Höhe in Punkten (über alle Stellen im Dokument)."""
    out: dict[int, tuple[float, float]] = {}
    for image in I.all_images(data):
        if not image.objnum:
            continue
        a, b, c, d, _e, _f = image.matrix
        width, height = math.hypot(a, b), math.hypot(c, d)
        old = out.get(image.objnum, (0.0, 0.0))
        out[image.objnum] = (max(old[0], width), max(old[1], height))
    return out


def _names(value) -> list[str]:
    if value is None:
        return []
    try:
        return [str(v) for v in value] if hasattr(value, "__iter__") and not isinstance(value, str) else [str(value)]
    except TypeError:
        return [str(value)]


def _recompress(pikepdf, obj, size_pt: tuple[float, float], profile: Profile) -> bool:
    """Ein Bild verkleinern/neu kodieren; False = unverändert gelassen (Masken, exotische Farbräume …)."""
    from PIL import Image
    if bool(obj.get("/ImageMask", False)) or int(obj.get("/BitsPerComponent", 8)) != 8 or "/Decode" in obj:
        return False
    filters = _names(obj.get("/Filter"))
    if any(f in ("/JPXDecode", "/JBIG2Decode", "/CCITTFaxDecode") for f in filters):
        return False
    colorspace = obj.get("/ColorSpace")
    space = str(colorspace[0]) if isinstance(colorspace, pikepdf.Array) and len(colorspace) else str(colorspace)
    if space not in ("/DeviceRGB", "/DeviceGray", "/ICCBased", "/Indexed", "/DeviceCMYK"):
        return False
    try:
        picture = pikepdf.PdfImage(obj).as_pil_image()
    except Exception:                                     # noqa: BLE001 – nicht dekodierbar: lassen
        return False
    width, height = picture.size
    resized = False
    if profile.dpi and size_pt[0] > 0 and size_pt[1] > 0:
        target = (size_pt[0] / 72 * profile.dpi, size_pt[1] / 72 * profile.dpi)
        scale = min(1.0, max(target[0] / width, target[1] / height))
        if scale < 1 / 1.15:                                  # erst ab deutlich zu viel Auflösung
            picture = picture.convert("RGB" if picture.mode not in ("L", "RGB") else picture.mode)
            picture = picture.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                                     Image.Resampling.LANCZOS)
            resized = True
    if picture.mode not in ("L", "RGB"):
        picture = picture.convert("RGB")
    photo = "/DCTDecode" in filters or picture.getcolors(256) is None
    old_size = len(obj.read_raw_bytes())
    if photo and profile.jpeg_quality:
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=profile.jpeg_quality, optimize=True)
        raw, filter_ = buffer.getvalue(), pikepdf.Name.DCTDecode
    elif resized:
        raw, filter_ = zlib.compress(picture.tobytes(), 9), pikepdf.Name.FlateDecode
    else:
        return False
    if len(raw) >= old_size * 0.95 and not resized:
        return False
    obj.write(raw, filter=filter_)
    obj.Width, obj.Height = picture.width, picture.height
    obj.ColorSpace = pikepdf.Name.DeviceGray if picture.mode == "L" else pikepdf.Name.DeviceRGB
    obj.BitsPerComponent = 8
    for key in ("/DecodeParms", "/Intent"):
        if key in obj:
            del obj[key]
    return True


def compress(data: bytes, profile: Profile, progress=None) -> CompressResult:
    """Komprimieren nach Profil. `progress(done, total)` meldet den Fortschritt (darf zum Abbrechen werfen)."""
    pikepdf = _pikepdf()
    profile = profile.checked()
    sizes = _display_sizes(data) if profile.lossy else {}
    pdf = _open(data)
    changed = skipped = 0
    try:
        images = [obj for obj in pdf.objects
                  if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == pikepdf.Name.Image] if sizes else []
        total = len(images) + 1
        for index, obj in enumerate(images):
            if progress is not None:
                progress(index, total)
            size = sizes.get(obj.objgen[0])
            if size is None:                                  # nur in Anmerkungen o. Ä.: Größe unbekannt → lassen
                skipped += 1
                continue
            try:
                if _recompress(pikepdf, obj, size, profile):
                    changed += 1
                else:
                    skipped += 1
            except Exception:                                 # noqa: BLE001 – ein kaputtes Bild bricht nichts ab
                skipped += 1
        if profile.remove_unused:
            pdf.remove_unreferenced_resources()
        buffer = io.BytesIO()
        mode = pikepdf.ObjectStreamMode.generate if profile.object_streams else pikepdf.ObjectStreamMode.disable
        pdf.save(buffer, compress_streams=True, object_stream_mode=mode, recompress_flate=profile.recompress_flate)
        if progress is not None:
            progress(total, total)
    finally:
        pdf.close()
    out = buffer.getvalue()
    return CompressResult(out, len(data), len(out), changed, skipped, profile.lossy)


def repair(data: bytes) -> bytes:
    """Beschädigtes PDF von qpdf neu schreiben lassen (Querverweise rekonstruiert)."""
    pdf = _open(data)
    try:
        if not len(pdf.pages):
            raise PdfEditError("Keine Seiten zu retten")
        if "/Size" not in pdf.trailer:                    # sonst übernimmt qpdf den kaputten Trailer ohne /Size;
            pdf.trailer.Size = len(pdf.objects) + 1       # mit Platzhalter rechnet es den richtigen Wert neu
        buffer = io.BytesIO()
        pdf.save(buffer)
    finally:
        pdf.close()
    return buffer.getvalue()


# ---- Bilder → PDF -----------------------------------------------------------------------------------------------
def _image_dpi(content: bytes) -> float:
    try:
        from PIL import Image
        dpi = Image.open(io.BytesIO(content)).info.get("dpi")
        value = float(dpi[0]) if dpi else 0.0
    except Exception:                                     # noqa: BLE001
        value = 0.0
    return value if 50 <= value <= 2400 else 150.0


def images_to_pdf(images: list[tuple[str, bytes]], page_size: str = "image", margin_mm: float = 0.0,
                  progress=None) -> bytes:
    """Eine Seite je Bild in der gegebenen Reihenfolge. `page_size`: image (Größe aus der Bildauflösung, sonst
    150 dpi) | a4 | letter (Hoch/Quer nach Bild, Bild eingepasst und mittig)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    if not images:
        raise PdfEditError("Keine Bilder gewählt")
    if page_size not in ("image", *PAGE_SIZES):
        raise PdfEditError(f"Unbekannte Seitengröße „{page_size}“")
    margin = max(0.0, min(float(margin_mm), 100.0)) * MM
    writer = PdfWriter()
    for index, (name, content) in enumerate(images):
        try:
            encoded = I.encode_picture(content)
        except PdfEditError as error:
            raise PdfEditError(f"{name}: {error}") from error
        if page_size == "image":
            dpi = _image_dpi(content)
            w, h = encoded.width * 72 / dpi, encoded.height * 72 / dpi
            factor = min(1.0, MAX_PAGE / max(w + 2 * margin, h + 2 * margin))
            w, h = w * factor, h * factor
            page_w, page_h = w + 2 * margin, h + 2 * margin
            x, y = margin, margin
        else:
            page_w, page_h = PAGE_SIZES[page_size]
            if encoded.width > encoded.height:
                page_w, page_h = page_h, page_w
            factor = min((page_w - 2 * margin) / encoded.width, (page_h - 2 * margin) / encoded.height)
            w, h = encoded.width * factor, encoded.height * factor
            x, y = (page_w - w) / 2, (page_h - h) / 2
        page = writer.add_blank_page(page_w, page_h)
        ref = I.add_xobject(writer, encoded)
        content_stream = DecodedStreamObject()
        content_stream.set_data(f"q {w:.3f} 0 0 {h:.3f} {x:.3f} {y:.3f} cm /Im0 Do Q".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(content_stream.flate_encode())
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
            NameObject("/Im0"): ref})})
        if progress is not None:
            progress(index + 1, len(images))
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---- PDF → Bilder -----------------------------------------------------------------------------------------------
def unique_path(folder: Path, name: str) -> Path:
    """Freier Dateiname im Ordner: „name.png“, sonst „name (2).png“ … – nie überschreiben."""
    target = Path(folder) / name
    number = 2
    while target.exists():
        target = Path(folder) / f"{Path(name).stem} ({number}){Path(name).suffix}"
        number += 1
    return target


def render_page(document, index: int, dpi: int, fmt: str = "png", quality: int = 90) -> bytes:
    page = document[index]
    try:
        width, height = page.get_size()
        scale = dpi / 72
        if width * scale * height * scale > MAX_RENDER_PIXELS:
            raise PdfEditError(f"Seite {index + 1} wäre bei {dpi} dpi zu groß – bitte weniger dpi wählen")
        picture = page.render(scale=scale, draw_annots=True, may_draw_forms=True).to_pil()
    finally:
        page.close()
    buffer = io.BytesIO()
    if fmt == "jpg":
        picture.convert("RGB").save(buffer, "JPEG", quality=max(10, min(int(quality), 100)), optimize=True,
                                    dpi=(dpi, dpi))
    else:
        picture.save(buffer, "PNG", dpi=(dpi, dpi))
    return buffer.getvalue()


def export_page_images(data: bytes, pages: list[int], folder: Path, stem: str, dpi: int = 150, fmt: str = "png",
                       quality: int = 90, progress=None) -> list[Path]:
    """Seiten als PNG/JPG in einen Ordner („stem-S3.png“, nie überschreiben)."""
    import pypdfium2 as pdfium
    if fmt not in ("png", "jpg"):
        raise PdfEditError(f"Unbekanntes Format „{fmt}“")
    if not 36 <= int(dpi) <= 1200:
        raise PdfEditError("Auflösung: 36 bis 1200 dpi")
    folder = Path(folder)
    if not folder.is_dir():
        raise PdfEditError(f"Ordner nicht gefunden: {folder}")
    try:
        document = pdfium.PdfDocument(data)
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"PDF nicht lesbar ({error})") from error
    written: list[Path] = []
    try:
        document.init_forms()
        for number, index in enumerate(pages):
            if not 0 <= index < len(document):
                raise PdfEditError(f"Seite {index + 1} gibt es nicht")
            raw = render_page(document, index, int(dpi), fmt, quality)
            target = unique_path(folder, f"{stem}-S{index + 1}.{fmt}")
            target.write_bytes(raw)
            written.append(target)
            if progress is not None:
                progress(number + 1, len(pages))
    finally:
        document.close()
    return written


# ---- PDF → Text / Markdown --------------------------------------------------------------------------------------
@dataclass
class Line:
    text: str
    page: int
    top: float
    bottom: float
    size: float


def _lines(data: bytes, progress=None) -> tuple[list[list[Line]], float]:
    """Zeilen je Seite mit Schriftgröße (PDFium) und die Fließtext-Größe."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    try:
        document = pdfium.PdfDocument(data)
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"PDF nicht lesbar ({error})") from error
    pages: list[list[Line]] = []
    weights: Counter = Counter()
    try:
        total = len(document)
        for index in range(total):
            page = document[index]
            textpage = page.get_textpage()
            count = textpage.count_chars()
            text = textpage.get_text_range(0, count) if count else ""
            lines: list[Line] = []
            current, sizes, top, bottom = [], [], None, None
            for i in range(count):
                ch = text[i] if i < len(text) else ""
                if ch in "\r\n":
                    if current:
                        _close(lines, weights, current, sizes, index, top, bottom)
                    current, sizes, top, bottom = [], [], None, None
                    continue
                current.append(ch)
                if not ch.isspace():
                    sizes.append(round(raw.FPDFText_GetFontSize(textpage.raw, i) * 2) / 2)
                    box = textpage.get_charbox(i)
                    top = box[3] if top is None else max(top, box[3])
                    bottom = box[1] if bottom is None else min(bottom, box[1])
            if current:
                _close(lines, weights, current, sizes, index, top, bottom)
            textpage.close()
            page.close()
            pages.append(lines)
            if progress is not None:
                progress(index + 1, total)
    finally:
        document.close()
    body = weights.most_common(1)[0][0] if weights else 10.0
    return pages, body


def _close(lines, weights, chars, sizes, page, top, bottom) -> None:
    # PDFium fügt ein am Zeilenende getrenntes Wort selbst zusammen und markiert die Trennstelle mit U+FFFE
    # (weiches Trennzeichen) – das Zeichen fällt weg, das Wort bleibt ganz
    text = re.sub(r"[ \t\u00a0]+", " ", "".join(chars).replace("\ufffe", "").replace("\u00ad", "")).strip()
    if not text:
        return
    size = Counter(sizes).most_common(1)[0][0] if sizes else 0.0
    weights[size] += len(sizes)
    lines.append(Line(text, page, top or 0.0, bottom or 0.0, size))


def to_text(data: bytes, progress=None, page_markers: bool = True) -> str:
    pages, _body = _lines(data, progress)
    parts = []
    for index, lines in enumerate(pages):
        body = "\n".join(line.text for line in lines)
        parts.append((f"--- Seite {index + 1} ---\n" if page_markers else "") + body)
    return "\n\n".join(parts).strip() + "\n"


BULLETS = ("•", "◦", "▪", "‣", "●", "○", "■", "–", "—", "-", "*")
_LIST = re.compile(r"^(- |\d+\. )")
_MD_SPECIAL = re.compile(r"^(#{1,6}\s|>|[+*-]\s|\d+[.)]\s|`{3}|={3,}|-{3,})")


def _escape(text: str) -> str:
    text = text.replace("\\", "\\\\")
    return ("\\" + text) if _MD_SPECIAL.match(text) else text


def _heading_levels(pages: list[list[Line]], body: float, levels: int = 3) -> dict[float, int]:
    sizes = sorted({line.size for lines in pages for line in lines if line.size >= body * 1.15}, reverse=True)
    groups: list[float] = []
    for size in sizes:
        if not groups or groups[-1] - size > 0.5:
            groups.append(size)
    out = {}
    for line_size in sizes:
        level = next((i + 1 for i, g in enumerate(groups[:levels]) if abs(line_size - g) <= 0.5 or line_size > g),
                     None)
        if level is not None:
            out[line_size] = level
    return out


def to_markdown(data: bytes, progress=None, page_markers: bool = True) -> str:
    """Markdown: Überschriften (Schriftgröße), Absätze, Listen, Silbentrennung zusammengefügt; Seitenwechsel als
    unsichtbarer Kommentar `<!-- Seite n -->`."""
    pages, body = _lines(data, progress)
    levels = _heading_levels(pages, body)
    out: list[str] = []
    for index, lines in enumerate(pages):
        if page_markers:
            out.append(f"<!-- Seite {index + 1} -->")
        paragraph: list[str] = []
        previous: Line | None = None

        def flush() -> None:
            if paragraph:
                out.append(_join(paragraph))
                paragraph.clear()
        for line in lines:
            level = levels.get(line.size)
            if level is not None and len(line.text) <= 120:
                flush()
                out.append("#" * level + " " + line.text.lstrip("#").strip())
                previous = None
                continue
            bullet = next((b for b in BULLETS if line.text.startswith(b + " ") or
                           (b in "•◦▪‣●○■" and line.text.startswith(b))), None)
            numbered = re.match(r"^(\d{1,3})[.)]\s+(.*)$", line.text)
            if bullet is not None or numbered:
                if paragraph and not _LIST.match(paragraph[-1]):
                    flush()                                   # Liste beginnt: vorherigen Absatz abschließen
                item = (f"- {_escape(line.text[len(bullet):].strip())}" if bullet is not None
                        else f"{numbered.group(1)}. {_escape(numbered.group(2))}")
                paragraph.append(item)
                previous = line
                continue
            gap = (previous.bottom - line.top) if previous is not None else 0.0
            if previous is None or gap > max(line.size, body) * 0.9 or abs(line.size - previous.size) > 0.6:
                flush()
                paragraph.append(_escape(line.text))
            elif paragraph and _LIST.match(paragraph[-1]):
                paragraph[-1] = _merge(paragraph[-1], line.text)  # Fortsetzung eines Listenpunkts
            else:
                paragraph.append(_escape(line.text))
            previous = line
        flush()
    text = "\n\n".join(part for part in out if part.strip())
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _merge(first: str, second: str) -> str:
    if re.search(r"\w-$", first) and second[:1].islower():
        return first[:-1] + second
    return first + " " + second


def _join(lines: list[str]) -> str:
    """Zeilen eines Absatzes zusammenfügen; Listenpunkte bleiben eigene Zeilen."""
    if all(_LIST.match(line) for line in lines):
        return "\n".join(lines)
    text = lines[0]
    for line in lines[1:]:
        if _LIST.match(line):
            text += "\n" + line
        else:
            text = _merge(text, line)
    return text
