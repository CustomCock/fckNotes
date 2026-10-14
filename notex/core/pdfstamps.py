"""Stempel (Qt-frei): Genehmigt, Entwurf, Vertraulich, Erledigt, Geprüft und eigene Stempel aus Text oder Bild.

Ein Stempel ist eine `/Stamp`-Anmerkung mit eigenem Erscheinungsbild: Rahmen mit abgerundeten Ecken, fetter Text in
der eingebetteten Noto Sans (bzw. ein Bild) und auf Wunsch eine zweite Zeile mit Datum und/oder Namen. Gezeichnet wird
in der natürlichen Größe des Stempels; das Rechteck auf der Seite skaliert ihn (Seitenverhältnis bleibt, siehe
`fit`). Auf gedrehten Seiten steht er wie die Unterschrift aufrecht (Form-Matrix).

Eigene Stempel liegen portabel in `<App-Ordner>/stamps/`: `stamps.json` (Liste) und je Bildstempel eine PNG/JPEG-Datei.
Bilder dekodiert die Oberfläche (Qt); hier wird nur geprüft, dass es wirklich PNG oder JPEG ist.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf.generic import DictionaryObject, FloatObject, NameObject, NumberObject, StreamObject, TextStringObject

from notex.core import pdfannot as A
from notex.core import pdffont
from notex.core.pdfpages import PdfEditError

INDEX = "stamps.json"
MAX_IMAGE = 5 * 1024 * 1024
TEXT_SIZE = 20.0
SUB_SIZE = 8.5
PAD_X, PAD_Y = 12.0, 7.0
BORDER = 2.5
PDF_NAMES = {"approved": "/Approved", "draft": "/Draft", "confidential": "/Confidential", "done": "/Final",
             "checked": "/fckNotesChecked"}


@dataclass(frozen=True)
class StampSpec:
    key: str
    text: str
    color: str = "#c02020"
    image: str = ""                  # Dateiname im Stempelordner (nur Bildstempel)
    with_date: bool = False
    with_user: bool = False
    custom: bool = False

    @property
    def label(self) -> str:
        return self.text or "Bildstempel"


BUILTIN = [
    StampSpec("approved", "GENEHMIGT", "#1f7a3a"),
    StampSpec("draft", "ENTWURF", "#3a5a9a"),
    StampSpec("confidential", "VERTRAULICH", "#c02020"),
    StampSpec("done", "ERLEDIGT", "#1f7a3a"),
    StampSpec("checked", "GEPRÜFT", "#2f6fdf"),
]


def builtin(key: str) -> StampSpec:
    for spec in BUILTIN:
        if spec.key == key:
            return spec
    raise PdfEditError(f"Unbekannter Stempel „{key}“")


def sub_line(spec: StampSpec, date: str = "", user: str = "") -> str:
    parts = [date if spec.with_date else "", user if spec.with_user else ""]
    return "  ·  ".join(p.strip() for p in parts if p and p.strip())


# ---- Eigene Stempel (portabel) --------------------------------------------------------------------------------
def image_kind(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return ""


def load_custom(folder: Path) -> list[StampSpec]:
    """Eigene Stempel aus `stamps.json`; kaputte Einträge oder fehlende Bilder werden übersprungen."""
    try:
        raw = json.loads((Path(folder) / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        image = str(item.get("image", "") or "")
        if image and (Path(image).name != image or not (Path(folder) / image).is_file()):
            continue
        color = item.get("color", "#c02020")
        color = color if A.valid_color(color) else "#c02020"
        text = str(item.get("text", ""))[:80]
        if not text and not image:
            continue
        out.append(StampSpec(item["key"], text, color, image, bool(item.get("with_date")), bool(item.get("with_user")),
                             True))
    return out


def _write_index(folder: Path, specs: list[StampSpec]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    items = [{k: v for k, v in asdict(s).items() if k != "custom"} for s in specs]
    tmp = folder / (INDEX + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(folder / INDEX)


def save_custom(folder: Path, text: str, color: str = "#c02020", image: bytes | None = None,
                with_date: bool = False, with_user: bool = False) -> StampSpec:
    """Neuen eigenen Stempel anlegen (Text und/oder Bild)."""
    folder = Path(folder)
    text = re.sub(r"\s+", " ", text or "").strip()[:80]
    if not A.valid_color(color):
        raise PdfEditError(f"Ungültige Farbe „{color}“")
    if not text and not image:
        raise PdfEditError("Ein Stempel braucht Text oder ein Bild")
    key = "custom-" + uuid.uuid4().hex[:10]
    filename = ""
    if image:
        kind = image_kind(image)
        if not kind:
            raise PdfEditError("Bildstempel nur aus PNG oder JPEG")
        if len(image) > MAX_IMAGE:
            raise PdfEditError("Das Bild ist zu groß (höchstens 5 MB)")
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{key}.{kind}"
        (folder / filename).write_bytes(image)
    spec = StampSpec(key, text, color, filename, with_date, with_user, True)
    _write_index(folder, [*load_custom(folder), spec])
    return spec


def delete_custom(folder: Path, key: str) -> None:
    folder = Path(folder)
    specs = load_custom(folder)
    victim = next((s for s in specs if s.key == key), None)
    if victim is None:
        raise PdfEditError("Diesen Stempel gibt es nicht mehr")
    if victim.image:
        try:
            (folder / victim.image).unlink()
        except OSError:
            pass
    _write_index(folder, [s for s in specs if s.key != key])


# ---- Zeichnen -------------------------------------------------------------------------------------------------
def natural_size(spec: StampSpec, sub: str = "", image_size: tuple[int, int] | None = None) -> tuple[float, float]:
    """Breite/Höhe in Punkt, in der der Stempel gezeichnet wird."""
    sub_w = pdffont.width(sub, SUB_SIZE) if sub else 0.0
    sub_h = SUB_SIZE * 1.5 if sub else 0.0
    if spec.image and image_size:
        iw, ih = image_size
        width = 160.0
        height = width * ih / max(iw, 1)
        return max(width, sub_w + 2 * PAD_X), height + sub_h + (4 if sub else 0)
    text_w = pdffont.width(spec.text, TEXT_SIZE, bold=True)
    return max(text_w, sub_w) + 2 * PAD_X, TEXT_SIZE * 1.15 + sub_h + 2 * PAD_Y


def _rounded(x0: float, y0: float, x1: float, y1: float, r: float) -> str:
    k = r * (1 - 0.5523)
    n = A._num
    return (f"{n(x0 + r)} {n(y0)} m {n(x1 - r)} {n(y0)} l {n(x1 - k)} {n(y0)} {n(x1)} {n(y0 + k)} {n(x1)} {n(y0 + r)} c "
            f"{n(x1)} {n(y1 - r)} l {n(x1)} {n(y1 - k)} {n(x1 - k)} {n(y1)} {n(x1 - r)} {n(y1)} c "
            f"{n(x0 + r)} {n(y1)} l {n(x0 + k)} {n(y1)} {n(x0)} {n(y1 - k)} {n(x0)} {n(y1 - r)} c "
            f"{n(x0)} {n(y0 + r)} l {n(x0)} {n(y0 + k)} {n(x0 + k)} {n(y0)} {n(x0 + r)} {n(y0)} c h")


def _text_content(spec: StampSpec, sub: str, size: tuple[float, float], use: pdffont.FontUsage) -> str:
    w, h = size
    n = A._num
    ops = ["q", A._rgb_op(spec.color, "RG"), A._rgb_op(spec.color, "rg"), f"{n(BORDER)} w",
           _rounded(BORDER / 2, BORDER / 2, w - BORDER / 2, h - BORDER / 2, 6) + " S"]
    text_w = pdffont.width(spec.text, TEXT_SIZE, bold=True)
    base = h - PAD_Y - TEXT_SIZE * 0.88
    ops.append(f"BT {use.name(bold=True)} {n(TEXT_SIZE)} Tf {n((w - text_w) / 2)} {n(base)} Td "
               f"{use.encode(spec.text, bold=True)} Tj ET")
    if sub:
        sub_w = pdffont.width(sub, SUB_SIZE)
        ops.append(f"BT {use.name()} {n(SUB_SIZE)} Tf {n((w - sub_w) / 2)} {n(PAD_Y)} Td {use.encode(sub)} Tj ET")
    ops.append("Q")
    return "\n".join(ops)


def _image_xobject(writer, width_px: int, height_px: int, rgb: bytes, alpha: bytes | None):
    import zlib
    if width_px < 1 or height_px < 1 or len(rgb) != width_px * height_px * 3:
        raise PdfEditError("Bilddaten passen nicht zur Größe")
    if alpha is not None and len(alpha) != width_px * height_px:
        raise PdfEditError("Transparenz passt nicht zur Größe")

    def stream(data: bytes, space: str):
        obj = StreamObject()
        obj._data = zlib.compress(data, 9)
        obj.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(width_px), NameObject("/Height"): NumberObject(height_px),
                    NameObject("/ColorSpace"): NameObject(space), NameObject("/BitsPerComponent"): NumberObject(8),
                    NameObject("/Filter"): NameObject("/FlateDecode")})
        return obj
    image = stream(rgb, "/DeviceRGB")
    if alpha is not None:
        image[NameObject("/SMask")] = writer._add_object(stream(alpha, "/DeviceGray"))
    return writer._add_object(image)


def fit(rect_view: A.Rect, natural: tuple[float, float]) -> A.Rect:
    """Klick → natürliche Größe an der Stelle; aufgezogen → eingepasst mit gleichem Seitenverhältnis."""
    x0, y0, x1, y1 = rect_view
    w, h = abs(x1 - x0), abs(y1 - y0)
    nw, nh = natural
    x0, y0 = min(x0, x1), min(y0, y1)
    if w < 8 or h < 8:
        return x0, y0, x0 + nw, y0 + nh
    scale = min(w / nw, h / nh)
    return x0, y0, x0 + nw * scale, y0 + nh * scale


def add_stamp(data: bytes, page_index: int, rect_view: A.Rect, spec: StampSpec, date: str = "", user: str = "",
              image: tuple[int, int, bytes, bytes | None] | None = None, opacity: float = 1.0) -> bytes:
    """Stempel setzen. `image` = (Breite, Höhe, RGB, Alpha) für Bildstempel (von der Oberfläche dekodiert)."""
    sub = sub_line(spec, date, user)
    if spec.image and image is None:
        raise PdfEditError("Das Bild des Stempels fehlt")
    if not spec.image and not spec.text.strip():
        raise PdfEditError("Der Stempel hat keinen Text")
    natural = natural_size(spec, sub, image[:2] if image else None)
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    target = fit(rect_view, natural)
    use = pdffont.FontUsage()
    w, h = natural
    extra = DictionaryObject()
    if spec.image and image is not None:
        ref = _image_xobject(writer, *image)
        extra[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): ref})
        image_h = h - (SUB_SIZE * 1.5 + 4 if sub else 0)
        ops = [f"q {A._num(w)} 0 0 {A._num(image_h)} 0 {A._num(h - image_h)} cm /Im0 Do Q"]
        if sub:
            sub_w = pdffont.width(sub, SUB_SIZE)
            ops.append(f"q {A._rgb_op(spec.color, 'rg')} BT {use.name()} {A._num(SUB_SIZE)} Tf "
                       f"{A._num((w - sub_w) / 2)} 2 Td {use.encode(sub)} Tj ET Q")
        content = "\n".join(ops)
    else:
        content = _text_content(spec, sub, natural, use)
    if opacity < 1:
        state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                                  NameObject("/CA"): FloatObject(round(opacity, 3)),
                                  NameObject("/ca"): FloatObject(round(opacity, 3))})
        extra[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS0"): state})
        content = "/GS0 gs\n" + content
    resources = use.resources(writer, extra)
    label = spec.text or "Bildstempel"
    annot = A._base("/Stamp", geom.rect_to_pdf(target), spec.color, label + (f" ({sub})" if sub else ""))
    annot[NameObject("/Name")] = NameObject(PDF_NAMES.get(spec.key, "/fckNotesCustom"))
    if opacity < 1:
        annot[NameObject("/CA")] = FloatObject(round(opacity, 3))
    if user and spec.with_user:
        annot[NameObject("/T")] = TextStringObject(user)
    appearance = A._form(writer, content, [0, 0, w, h], resources, geom.matrix())
    A._attach(writer, page_index, annot, appearance)
    return A.finish(writer)
