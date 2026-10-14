"""Seiten-Ebenen (Qt-frei, Block V8): Wasserzeichen, Kopf-/Fußzeilen mit Seitenzahlen, Hintergrund.

Aufbau – damit „Entfernen“ und „Aktualisieren“ sauber gehen:
- Je Seite ein **Form-XObject** mit dem Inhalt der Ebene (Text in eingebetteter Noto Sans, Bild, Farbfläche). Es trägt
  `/OC` → eine **Ebene (OCG)** je Art („fckNotes: Wasserzeichen“ …), die Betrachter im Ebenen-Bedienfeld zeigen, und
  `/fckNotesLayer` mit Art, Kennung und der **Vorlage** (JSON), aus der es entstand.
- Gezeichnet wird es aus einem **eigenen Content-Stream** der Seite (markiert mit `/fckNotesLayer`), eingefasst als
  `/Artifact` (Pagination/Watermark bzw. Background) – Bildschirmleser lesen es nicht vor.
- Ebenen vor dem Inhalt stehen hinter dem Original; das Original wird dafür in eigene `q`/`Q`-Streams gefasst, damit
  ein offener Grafikzustand des Originals nicht auf die Ebene durchschlägt. Ebenen hinter dem Inhalt (Hintergrund,
  Wasserzeichen „dahinter“) stehen davor.

Entfernen nimmt genau diese Streams, Ressourcen und Ebenen wieder heraus – die Original-Streams bleiben unberührt, der
Seiteninhalt ist danach Byte für Byte wie vorher. Hat ein anderes Programm die Streams zusammengelegt, werden die
Zeichenbefehle einzeln gesucht und entfernt.

Fremde Wasserzeichen (z. B. von Acrobat) erkennt `foreign_watermarks`, wenn sie als `/Artifact` mit
`/Subtype /Watermark` markiert oder Wasserzeichen-Anmerkungen sind – andere nicht.
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime

from pypdf.generic import (ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject, FloatObject,
                           NameObject, TextStringObject)

from notex import APP_NAME
from notex.core import pdfannot as A
from notex.core import pdffont
from notex.core import pdfimages as I
from notex.core.pdfpages import PdfEditError, extract, open_reader, page_count, parse_ranges

KINDS = ("background", "watermark", "header")
LABELS = {"background": "Hintergrund", "watermark": "Wasserzeichen", "header": "Kopf-/Fußzeile"}
LAYER_KEY = "/fckNotesLayer"
OCG_KEY = "/fckNotesLayerKind"
POSITIONS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
POSITION_LABELS = {"tl": "oben links", "tc": "oben Mitte", "tr": "oben rechts", "ml": "Mitte links", "mc": "Mitte",
                   "mr": "Mitte rechts", "bl": "unten links", "bc": "unten Mitte", "br": "unten rechts"}
PLACEHOLDERS = {"{seite}": "Seitenzahl", "{seiten}": "letzte Seitenzahl", "{datum}": "Datum",
                "{dateiname}": "Dateiname", "{titel}": "Titel (Metadaten)"}
NUMBER_PRESETS = ("{seite}", "Seite {seite}", "Seite {seite} von {seiten}", "{seite} / {seiten}", "– {seite} –")
MARGIN = 36.0                                             # Abstand von Wasserzeichen zum Rand (Punkte)
MM = 72 / 25.4
MAX_TEXT = 500
ARTIFACT = {"background": "/Artifact << /Type /Background >> BDC",
            "watermark": "/Artifact << /Type /Pagination /Subtype /Watermark >> BDC",
            "header": "/Artifact << /Type /Pagination >> BDC"}


# ---- Vorlagen ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Watermark:
    text: str = "ENTWURF"
    font_size: float = 72.0
    bold: bool = True
    color: str = "#c02020"
    opacity: float = 0.25
    rotation: float = 45.0                                # Grad gegen den Uhrzeigersinn (steigende Diagonale)
    position: str = "mc"
    scale: float = 0.5                                    # Bild: Anteil der Seitenbreite
    behind: bool = False
    image: bool = False                                   # True: Bild statt Text (Bytes kommen getrennt)
    pages: str = ""                                       # „1-3, 5“; leer = alle

    kind = "watermark"


@dataclass(frozen=True)
class HeaderFooter:
    header_left: str = ""
    header_center: str = ""
    header_right: str = ""
    footer_left: str = ""
    footer_center: str = "Seite {seite} von {seiten}"
    footer_right: str = ""
    font_size: float = 10.0
    bold: bool = False
    color: str = "#333333"
    margin_mm: float = 10.0                               # Abstand der Grundlinie zum oberen/unteren Rand
    side_mm: float = 15.0                                 # Abstand links/rechts
    start: int = 1                                        # Nummer der ersten Seite im Bereich
    pages: str = ""

    kind = "header"

    @property
    def slots(self) -> dict[str, str]:
        return {"header_left": self.header_left, "header_center": self.header_center,
                "header_right": self.header_right, "footer_left": self.footer_left,
                "footer_center": self.footer_center, "footer_right": self.footer_right}


@dataclass(frozen=True)
class Background:
    color: str = "#fff6d5"
    opacity: float = 1.0
    image: bool = False
    fit: str = "stretch"                                  # stretch (ganze Seite) | fit (einpassen, mittig)
    pages: str = ""

    kind = "background"


SPECS = {"watermark": Watermark, "header": HeaderFooter, "background": Background}
Spec = Watermark | HeaderFooter | Background


def _color(value: str) -> str:
    if not A.valid_color(value):
        raise PdfEditError(f"Ungültige Farbe „{value}“ (erwartet #rrggbb)")
    return value


def checked(spec: Spec) -> Spec:
    """Werte in gültige Bereiche bringen; unsinnige Eingaben als PdfEditError."""
    if isinstance(spec, Watermark):
        if not spec.image and not spec.text.strip():
            raise PdfEditError("Bitte einen Text für das Wasserzeichen eingeben")
        if spec.position not in POSITIONS:
            raise PdfEditError(f"Unbekannte Position „{spec.position}“")
        return replace(spec, text=spec.text[:MAX_TEXT], font_size=max(4.0, min(float(spec.font_size), 400.0)),
                       color=_color(spec.color), opacity=max(0.05, min(float(spec.opacity), 1.0)),
                       rotation=float(spec.rotation) % 360, scale=max(0.02, min(float(spec.scale), 1.0)))
    if isinstance(spec, HeaderFooter):
        if not any(v.strip() for v in spec.slots.values()):
            raise PdfEditError("Bitte mindestens ein Feld für Kopf- oder Fußzeile ausfüllen")
        return replace(spec, **{k: v[:MAX_TEXT] for k, v in spec.slots.items()},
                       font_size=max(4.0, min(float(spec.font_size), 72.0)), color=_color(spec.color),
                       margin_mm=max(0.0, min(float(spec.margin_mm), 100.0)),
                       side_mm=max(0.0, min(float(spec.side_mm), 100.0)), start=max(0, int(spec.start)))
    if isinstance(spec, Background):
        if spec.fit not in ("stretch", "fit"):
            raise PdfEditError(f"Unbekannte Einpassung „{spec.fit}“")
        return replace(spec, color=_color(spec.color), opacity=max(0.05, min(float(spec.opacity), 1.0)))
    raise PdfEditError("Unbekannte Ebenen-Art")


def to_dict(spec: Spec) -> dict:
    return {"kind": spec.kind, **asdict(spec)}


def from_dict(data: dict) -> Spec:
    """Vorlage aus JSON (Config oder PDF) – fremde Schlüssel werden ignoriert, Typen geprüft."""
    kind = str(data.get("kind", ""))
    cls = SPECS.get(kind)
    if cls is None:
        raise PdfEditError(f"Unbekannte Ebenen-Art „{kind}“")
    values = {}
    for item in fields(cls):
        if item.name not in data:
            continue
        value, default = data[item.name], item.default
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            continue
        values[item.name] = value
    return checked(cls(**values))


# ---- Geometrie und Zeichnen --------------------------------------------------------------------------------------
def view_matrix(geom: A.PageGeom) -> list[float]:
    """Form-Matrix: aufrechte Ansicht (Ursprung unten links, y nach oben) → PDF-Benutzerkoordinaten."""
    _w, h = geom.size
    ox, oy = geom.to_pdf(0, h)
    ax, ay = geom.to_pdf(1, h)
    bx, by = geom.to_pdf(0, h - 1)
    return [ax - ox, ay - oy, bx - ox, by - oy, ox, oy]


def _anchor(position: str, width: float, height: float, page_w: float, page_h: float) -> tuple[float, float]:
    """Mittelpunkt eines Blocks (umschließende Größe) für eine der neun Positionen."""
    row, col = position[0], position[1]
    cx = {"l": MARGIN + width / 2, "c": page_w / 2, "r": page_w - MARGIN - width / 2}[col]
    cy = {"t": page_h - MARGIN - height / 2, "m": page_h / 2, "b": MARGIN + height / 2}[row]
    return cx, cy


def _rotation(angle: float) -> str:
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)
    return f"{A._num(c)} {A._num(s)} {A._num(-s)} {A._num(c)} 0 0 cm"


def _gs(opacity: float) -> str:
    return "/GS0 gs " if opacity < 1 else ""


def _watermark_ops(spec: Watermark, font: pdffont.FontUsage, page_w: float, page_h: float,
                   image_size: tuple[int, int] | None) -> str:
    if spec.image:
        iw, ih = image_size or (1, 1)
        w = spec.scale * page_w
        h = w * ih / max(iw, 1)
        if h > page_h * 0.95:
            w, h = w * page_h * 0.95 / h, page_h * 0.95
        rw, rh = I.rotated_size(w, h, spec.rotation)
        cx, cy = _anchor(spec.position, rw, rh, page_w, page_h)
        return (f"q {_gs(spec.opacity)}1 0 0 1 {A._num(cx)} {A._num(cy)} cm {_rotation(spec.rotation)} "
                f"{A._num(w)} 0 0 {A._num(h)} {A._num(-w / 2)} {A._num(-h / 2)} cm /Im0 Do Q")
    lines = spec.text.replace("\r", "").split("\n")
    size = spec.font_size
    widths = [pdffont.width(line, size, spec.bold) for line in lines]
    line_h = size * 1.15
    block_w, block_h = max(widths + [1.0]), line_h * len(lines)
    rw, rh = I.rotated_size(block_w, block_h, spec.rotation)
    cx, cy = _anchor(spec.position, rw, rh, page_w, page_h)
    name = font.name(spec.bold)
    ops = [f"q {_gs(spec.opacity)}1 0 0 1 {A._num(cx)} {A._num(cy)} cm {_rotation(spec.rotation)}",
           f"BT {name} {A._num(size)} Tf {A._rgb_op(spec.color, 'rg')}"]
    face = pdffont.face(spec.bold)
    first_baseline = block_h / 2 - (line_h - size) / 2 - size * face.ascent / 1000
    for index, (line, width) in enumerate(zip(lines, widths)):
        y = first_baseline - index * line_h
        ops.append(f"1 0 0 1 {A._num(-width / 2)} {A._num(y)} Tm {font.encode(line, spec.bold)} Tj")
    ops.append("ET Q")
    return "\n".join(ops)


def fill_placeholders(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def _header_ops(spec: HeaderFooter, font: pdffont.FontUsage, page_w: float, page_h: float,
                values: dict[str, str]) -> str:
    size, name = spec.font_size, font.name(spec.bold)
    face = pdffont.face(spec.bold)
    side = spec.side_mm * MM
    top = page_h - spec.margin_mm * MM - size * face.ascent / 1000     # Oberkante der Schrift auf dem Abstand
    bottom = spec.margin_mm * MM                                         # Grundlinie auf dem Abstand
    ops = []
    for slot, template in spec.slots.items():
        text = fill_placeholders(template, values).replace("\r", " ").replace("\n", " ").strip()
        if not text:
            continue
        width = pdffont.width(text, size, spec.bold)
        where = slot.split("_")[1]
        x = {"left": side, "center": (page_w - width) / 2, "right": page_w - side - width}[where]
        y = top if slot.startswith("header") else bottom
        ops.append(f"1 0 0 1 {A._num(x)} {A._num(y)} Tm {font.encode(text, spec.bold)} Tj")
    if not ops:
        return ""
    return f"q BT {name} {A._num(size)} Tf {A._rgb_op(spec.color, 'rg')}\n" + "\n".join(ops) + "\nET Q"


def _background_ops(spec: Background, page_w: float, page_h: float, image_size: tuple[int, int] | None) -> str:
    if not spec.image:
        return (f"q {_gs(spec.opacity)}{A._rgb_op(spec.color, 'rg')} 0 0 {A._num(page_w)} {A._num(page_h)} re f Q")
    iw, ih = image_size or (1, 1)
    if spec.fit == "fit":
        factor = min(page_w / max(iw, 1), page_h / max(ih, 1))
        w, h = iw * factor, ih * factor
        x, y = (page_w - w) / 2, (page_h - h) / 2
    else:
        w, h, x, y = page_w, page_h, 0.0, 0.0
    return f"q {_gs(spec.opacity)}{A._num(w)} 0 0 {A._num(h)} {A._num(x)} {A._num(y)} cm /Im0 Do Q"


# ---- Ebenen (OCG) -----------------------------------------------------------------------------------------------
def _oc_properties(writer, create: bool):
    root = writer._root_object
    props = root.get("/OCProperties")
    if props is None:
        if not create:
            return None
        props = DictionaryObject({NameObject("/OCGs"): ArrayObject(),
                                  NameObject("/D"): DictionaryObject({NameObject("/Order"): ArrayObject()})})
        root[NameObject("/OCProperties")] = props
    return props.get_object()


def _ocgs_of(writer, kind: str | None = None) -> list:
    props = _oc_properties(writer, False)
    if props is None:
        return []
    out = []
    for ref in props.get("/OCGs", []) or []:
        try:
            ocg = ref.get_object()
        except Exception:                                 # noqa: BLE001
            continue
        if OCG_KEY in ocg and (kind is None or str(ocg[OCG_KEY]) == f"/{kind}"):
            out.append(ref)
    return out


def _ocg(writer, kind: str):
    found = _ocgs_of(writer, kind)
    if found:
        return found[0]
    ocg = DictionaryObject({
        NameObject("/Type"): NameObject("/OCG"),
        NameObject("/Name"): TextStringObject(f"{APP_NAME}: {LABELS[kind]}"),
        NameObject(OCG_KEY): NameObject(f"/{kind}"),
        NameObject("/Usage"): DictionaryObject({
            NameObject("/Print"): DictionaryObject({NameObject("/PrintState"): NameObject("/ON")}),
            NameObject("/View"): DictionaryObject({NameObject("/ViewState"): NameObject("/ON")})}),
    })
    ref = writer._add_object(ocg)
    props = _oc_properties(writer, True)
    ocgs = props.get("/OCGs")
    if ocgs is None:
        ocgs = ArrayObject()
        props[NameObject("/OCGs")] = ocgs
    ocgs.get_object().append(ref)
    default = props.get("/D")
    if default is None:
        default = DictionaryObject()
        props[NameObject("/D")] = default
    default = default.get_object()
    order = default.get("/Order")
    if order is None:
        order = ArrayObject()
        default[NameObject("/Order")] = order
    order.get_object().append(ref)
    return ref


def _drop_ocg(writer, ref) -> None:
    props = _oc_properties(writer, False)
    if props is None:
        return
    target = ref.idnum

    def keep(array) -> ArrayObject:
        out = ArrayObject()
        for item in array:
            if hasattr(item, "idnum") and item.idnum == target:
                continue
            out.append(item)
        return out
    props[NameObject("/OCGs")] = keep(props.get("/OCGs", ArrayObject()).get_object())
    configs = [props.get("/D")] + list((props.get("/Configs") or ArrayObject()).get_object())
    for config in configs:
        if config is None:
            continue
        config = config.get_object()
        for key in ("/Order", "/ON", "/OFF", "/Locked"):
            if key in config:
                config[NameObject(key)] = keep(config[key].get_object())
    if not props["/OCGs"]:
        del writer._root_object["/OCProperties"]


# ---- Content-Streams der Seite ----------------------------------------------------------------------------------
def _stream(writer, data: bytes, marker: DictionaryObject):
    stream = DecodedStreamObject()
    stream.set_data(data)
    stream = stream.flate_encode()
    stream[NameObject(LAYER_KEY)] = marker
    return writer._add_object(stream)


def _marker(kind: str, layer_id: str, role: str) -> DictionaryObject:
    return DictionaryObject({NameObject("/Kind"): NameObject(f"/{kind}"), NameObject("/Id"): TextStringObject(layer_id),
                             NameObject("/Role"): NameObject(f"/{role}")})


def _contents(page) -> ArrayObject:
    """/Contents als direktes Array der Seite (dasselbe Objekt bei jedem Aufruf)."""
    contents = page.raw_get("/Contents") if "/Contents" in page else None
    if isinstance(contents, ArrayObject):
        return contents
    if contents is None:
        array = ArrayObject()
    else:
        resolved = contents.get_object()
        array = ArrayObject(resolved) if isinstance(resolved, ArrayObject) else ArrayObject([contents])
    page[NameObject("/Contents")] = array
    return array


def _role(ref) -> tuple[str, str]:
    """(Rolle, Art) eines Content-Streams; ("", "") für fremde Streams."""
    try:
        marker = ref.get_object().get(LAYER_KEY)
    except Exception:                                     # noqa: BLE001
        return "", ""
    if marker is None:
        return "", ""
    marker = marker.get_object()
    return str(marker.get("/Role", "")).lstrip("/"), str(marker.get("/Kind", "")).lstrip("/")


def _insert(writer, page, ref, behind: bool) -> None:
    array = _contents(page)
    roles = [_role(r)[0] for r in array]
    if behind:
        array.insert(0, ref)
        return
    if "WrapStart" not in roles:                          # Original einfassen: offener Grafikzustand bleibt drinnen
        first = 0
        while first < len(array) and roles[first] == "Behind":
            first += 1
        last = max((i for i, r in enumerate(roles) if r not in ("Front",)), default=first - 1)
        array.insert(last + 1, _stream(writer, b"\nQ\n", _marker("wrap", "", "WrapEnd")))
        array.insert(first, _stream(writer, b"q\n", _marker("wrap", "", "WrapStart")))
    array.append(ref)


def _finish_contents(page) -> None:
    array = page.get("/Contents")
    if array is None:
        return
    array = array.get_object()
    if isinstance(array, ArrayObject):
        if not array:
            del page["/Contents"]
        elif len(array) == 1:
            page[NameObject("/Contents")] = array[0]


# ---- Hinzufügen -------------------------------------------------------------------------------------------------
def _title(writer) -> str:
    info = writer._info.get_object() if getattr(writer, "_info", None) is not None else {}
    try:
        return str(info.get("/Title", "") or "")
    except Exception:                                     # noqa: BLE001
        return ""


def _add(writer, spec: Spec, image_ref=None, filename: str = "", progress=None, layer_id: str = "",
         last_number: int | None = None) -> str:
    kind = spec.kind
    count = len(writer.pages)
    pages = parse_ranges(spec.pages, count)
    if not pages:
        raise PdfEditError("Keine Seiten im Bereich")
    layer_id = layer_id or uuid.uuid4().hex[:8]
    ocg = _ocg(writer, kind)
    font = pdffont.FontUsage()
    image_size = None
    if getattr(spec, "image", False):
        if image_ref is None:
            raise PdfEditError("Bild fehlt")
        image = image_ref.get_object()
        image_size = (int(image.get("/Width", 1)), int(image.get("/Height", 1)))
    last = last_number if last_number is not None else getattr(spec, "start", 1) + len(pages) - 1
    values_base = {"{seiten}": str(last),
                   "{datum}": datetime.now().strftime("%d.%m.%Y"), "{dateiname}": filename,
                   "{titel}": _title(writer)}
    drawings: list[tuple[int, str, float, float]] = []
    for number, page_index in enumerate(pages):
        page = writer.pages[page_index]
        page_w, page_h = A.geometry_of(page).size
        if isinstance(spec, Watermark):
            ops = _watermark_ops(spec, font, page_w, page_h, image_size)
        elif isinstance(spec, HeaderFooter):
            ops = _header_ops(spec, font, page_w, page_h, {**values_base, "{seite}": str(spec.start + number)})
        else:
            ops = _background_ops(spec, page_w, page_h, image_size)
        if ops:
            drawings.append((page_index, ops, page_w, page_h))
        if progress is not None:
            progress(number + 1, len(pages) * 2)
    extra = DictionaryObject()
    if image_ref is not None and image_size is not None:
        extra[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): image_ref})
    opacity = getattr(spec, "opacity", 1.0)
    if opacity < 1:
        state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                                  NameObject("/CA"): FloatObject(round(opacity, 3)),
                                  NameObject("/ca"): FloatObject(round(opacity, 3))})
        extra[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS0"): state})
    resources = writer._add_object(font.resources(writer, extra))
    info = DictionaryObject({NameObject("/Kind"): NameObject(f"/{kind}"), NameObject("/Id"): TextStringObject(layer_id),
                             NameObject("/Spec"): TextStringObject(json.dumps(to_dict(spec), ensure_ascii=False))})
    info_ref = writer._add_object(info)
    behind = kind == "background" or bool(getattr(spec, "behind", False))
    for number, (page_index, ops, page_w, page_h) in enumerate(drawings):
        page = writer.pages[page_index]
        form = DecodedStreamObject()
        form.set_data(ops.encode("latin-1"))
        form = form.flate_encode()
        form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                     NameObject("/BBox"): A._floats([0, 0, page_w, page_h]),
                     NameObject("/Matrix"): A._floats(view_matrix(A.geometry_of(page))),
                     NameObject("/Resources"): resources, NameObject("/OC"): ocg, NameObject(LAYER_KEY): info_ref})
        xobjects = I._own_xobjects(page)
        name = I._free_name(xobjects, "/fckLayer")
        xobjects[NameObject(name)] = writer._add_object(form)
        draw = f"q {ARTIFACT[kind]} {name} Do EMC Q\n".encode("latin-1")
        _insert(writer, page, _stream(writer, draw, _marker(kind, layer_id, "Behind" if behind else "Front")), behind)
        if progress is not None:
            progress(len(pages) + number + 1, len(pages) * 2)
    return layer_id


def _encoded_image(writer, image: bytes | None):
    return I.add_xobject(writer, I.encode_picture(image)) if image else None


def add_layer(data: bytes, spec: Spec, image: bytes | None = None, filename: str = "", progress=None) -> bytes:
    """Ebene hinzufügen; eine vorhandene Ebene derselben Art wird ersetzt. `image` = Bilddatei (PNG/JPG …), wenn
    die Vorlage ein Bild verlangt. `progress(done, total)` meldet den Fortschritt (darf zum Abbrechen werfen)."""
    spec = checked(spec)
    if getattr(spec, "image", False) and not image:
        raise PdfEditError("Bitte ein Bild wählen")
    writer = A.open_writer(data)
    _remove(writer, spec.kind)
    _add(writer, spec, _encoded_image(writer, image) if getattr(spec, "image", False) else None, filename, progress)
    return A.finish(writer)


# ---- Auflisten, Entfernen, Aktualisieren ------------------------------------------------------------------------
@dataclass(frozen=True)
class LayerInfo:
    kind: str
    layer_id: str
    pages: tuple[int, ...]
    spec: dict

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind)

    def describe(self) -> str:
        from notex.core.pdfpages import describe
        spec = self.spec
        what = ""
        if self.kind == "watermark":
            what = "Bild" if spec.get("image") else f"„{str(spec.get('text', ''))[:40]}“"
        elif self.kind == "header":
            parts = [str(spec.get(k, "")) for k in ("header_left", "header_center", "header_right", "footer_left",
                                                    "footer_center", "footer_right") if spec.get(k)]
            what = " · ".join(parts)[:60]
        elif self.kind == "background":
            what = "Bild" if spec.get("image") else str(spec.get("color", ""))
        return f"{self.label}: {what} – {describe(list(self.pages))}"


def _form_info(xobject):
    info = xobject.get(LAYER_KEY)
    return info.get_object() if info is not None else None


def _layers_on(page):
    """(Ressourcenname, Form, Info) der eigenen Ebenen einer Seite."""
    out = []
    for name, ref in I._xobjects(I._resources(page)).items():
        try:
            form = ref.get_object()
            info = _form_info(form)
        except Exception:                                 # noqa: BLE001
            continue
        if info is not None:
            out.append((str(name), form, info))
    return out


def list_layers(data: bytes) -> list[LayerInfo]:
    reader = open_reader(data)
    found: dict[tuple[str, str], tuple[list[int], dict]] = {}
    for index, page in enumerate(reader.pages):
        for _name, _form, info in _layers_on(page):
            kind, layer_id = str(info.get("/Kind", "")).lstrip("/"), str(info.get("/Id", ""))
            try:
                spec = json.loads(str(info.get("/Spec", "{}")))
            except ValueError:
                spec = {}
            entry = found.setdefault((kind, layer_id), ([], spec))
            entry[0].append(index)
    return [LayerInfo(kind, layer_id, tuple(pages), spec) for (kind, layer_id), (pages, spec) in found.items()]


def _strip_ops(writer, page, predicate) -> int:
    """Zeichenbefehle aus den Content-Streams entfernen: jeden `Do`, für den `predicate(name)` gilt, samt dem
    markierten Inhalt (BDC … EMC), der ihn umschließt. Liefert die Zahl der entfernten Stellen."""
    removed = 0
    array = _contents(page)
    for position, ref in enumerate(list(array)):
        try:
            ops = list(ContentStream(ref.get_object(), writer).operations)
        except Exception:                                 # noqa: BLE001
            continue
        drop: set[int] = set()
        stack: list[int] = []
        for index, (operands, operator) in enumerate(ops):
            if operator in (b"BDC", b"BMC"):
                stack.append(index)
            elif operator == b"EMC" and stack:
                stack.pop()
            elif operator == b"Do" and operands and predicate(str(operands[0]), stack, ops):
                if stack:
                    start, depth = stack[-1], 0
                    for end in range(start, len(ops)):
                        op = ops[end][1]
                        depth += op in (b"BDC", b"BMC")
                        depth -= op == b"EMC"
                        if depth == 0:
                            drop.update(range(start, end + 1))
                            break
                    else:
                        drop.add(index)
                else:
                    drop.add(index)
                removed += 1
        if drop:
            content = ContentStream(None, writer)
            content.operations = [op for i, op in enumerate(ops) if i not in drop]
            stream = DecodedStreamObject()
            stream.set_data(content.get_data())
            array[position] = writer._add_object(stream.flate_encode())
    return removed


def _remove(writer, kind: str | None = None, layer_id: str | None = None) -> int:
    """Eigene Ebenen entfernen (alle, eine Art oder eine Kennung). Liefert die Zahl der betroffenen Seiten."""
    def ours(info) -> bool:
        return ((kind is None or str(info.get("/Kind", "")) == f"/{kind}")
                and (layer_id is None or str(info.get("/Id", "")) == layer_id))
    touched = 0
    for page in writer.pages:
        layers = [(name, form, info) for name, form, info in _layers_on(page) if ours(info)]
        contents = page.get("/Contents")
        marked = []
        if contents is not None:
            for ref in _contents(page):
                role, stream_kind = _role(ref)
                if role in ("Front", "Behind") and (kind is None or stream_kind == kind):
                    if layer_id is None or str(ref.get_object()[LAYER_KEY].get_object().get("/Id", "")) == layer_id:
                        marked.append(ref)
        if not layers and not marked:
            _finish_contents(page)
            continue
        touched += 1
        array = _contents(page)
        for ref in marked:
            array.remove(ref)
        names = {name for name, _form, _info in layers}
        if names:                                         # zusammengelegte Streams: Befehle einzeln suchen
            _strip_ops(writer, page, lambda name, _stack, _ops: name in names)
            xobjects = I._own_xobjects(page)
            for name in names:
                if name in xobjects:
                    del xobjects[name]
        if not any(_role(ref)[0] == "Front" for ref in array):
            for ref in [r for r in array if _role(r)[0] in ("WrapStart", "WrapEnd")]:
                array.remove(ref)
        _finish_contents(page)
    remaining = {str(info.get("/Kind", "")).lstrip("/") for page in writer.pages for _n, _f, info in _layers_on(page)}
    for ref in _ocgs_of(writer):
        if str(ref.get_object()[OCG_KEY]).lstrip("/") not in remaining:
            _drop_ocg(writer, ref)
    return touched


def remove_layers(data: bytes, kind: str | None = None, layer_id: str | None = None) -> bytes:
    writer = A.open_writer(data)
    if not _remove(writer, kind, layer_id):
        raise PdfEditError("Keine passende Ebene gefunden" if kind or layer_id else
                           f"Keine {APP_NAME}-Ebenen in diesem PDF")
    return A.finish(writer)


def update_layers(data: bytes, filename: str = "", progress=None) -> bytes:
    """Alle eigenen Ebenen aus ihrer gespeicherten Vorlage neu erzeugen – z. B. nach Einfügen oder Löschen von
    Seiten (Seitenzahlen, „alle Seiten“) oder für ein neues Datum."""
    writer = A.open_writer(data)
    jobs = []
    for kind in KINDS:
        for page in writer.pages:
            hit = next(((form, info) for _n, form, info in _layers_on(page)
                        if str(info.get("/Kind", "")) == f"/{kind}"), None)
            if hit is None:
                continue
            form, info = hit
            try:
                spec = from_dict(json.loads(str(info.get("/Spec", "{}"))))
            except (ValueError, PdfEditError) as error:
                raise PdfEditError(f"{LABELS[kind]}: Vorlage nicht lesbar ({error})") from error
            image_ref = None
            if getattr(spec, "image", False):
                res = form.get("/Resources")
                image_ref = I._xobjects(res.get_object() if res is not None else DictionaryObject()).raw_get("/Im0")
            jobs.append((spec, image_ref, str(info.get("/Id", ""))))
            break
    if not jobs:
        raise PdfEditError(f"Keine {APP_NAME}-Ebenen in diesem PDF")
    for spec, image_ref, layer_id in jobs:
        _remove(writer, spec.kind)
        _add(writer, spec, image_ref, filename, progress, layer_id)
    return A.finish(writer)


# ---- Fremde Wasserzeichen ---------------------------------------------------------------------------------------
def _is_watermark_mark(operands) -> bool:
    if len(operands) < 2 or str(operands[0]) != "/Artifact":
        return False
    props = operands[1]
    try:
        props = props.get_object()
        return str(props.get("/Subtype", "")) == "/Watermark"
    except AttributeError:
        return False


def _foreign_blocks(writer, page, remove: bool) -> int:
    """Als Wasserzeichen markierte Inhalte (BDC /Artifact <</Subtype /Watermark>> … EMC), die nicht von uns sind."""
    ours = {name for name, _f, _i in _layers_on(page)}
    count = 0
    array = _contents(page)
    for position, ref in enumerate(list(array)):
        if _role(ref)[0]:
            continue
        try:
            ops = list(ContentStream(ref.get_object(), writer).operations)
        except Exception:                                 # noqa: BLE001
            continue
        drop: set[int] = set()
        index = 0
        while index < len(ops):
            operands, operator = ops[index]
            if operator == b"BDC" and _is_watermark_mark(operands):
                depth, end = 0, None
                for j in range(index, len(ops)):
                    op = ops[j][1]
                    depth += op in (b"BDC", b"BMC")
                    depth -= op == b"EMC"
                    if depth == 0:
                        end = j
                        break
                if end is None:
                    break
                inner = {str(o[0]) for o, op in ops[index:end] if op == b"Do" and o}
                if not inner & ours:
                    count += 1
                    drop.update(range(index, end + 1))
                index = end + 1
                continue
            index += 1
        if remove and drop:
            content = ContentStream(None, writer)
            content.operations = [op for i, op in enumerate(ops) if i not in drop]
            stream = DecodedStreamObject()
            stream.set_data(content.get_data())
            array[position] = writer._add_object(stream.flate_encode())
    _finish_contents(page)
    annots = page.get("/Annots")
    if annots is not None:
        keep = ArrayObject()
        for ref in annots.get_object():
            try:
                if ref.get_object().get("/Subtype") == "/Watermark":
                    count += 1
                    continue
            except Exception:                             # noqa: BLE001
                pass
            keep.append(ref)
        if remove:
            if keep:
                page[NameObject("/Annots")] = keep
            else:
                del page["/Annots"]
    return count


def foreign_watermarks(data: bytes) -> dict[int, int]:
    """Seite → Zahl erkannter fremder Wasserzeichen (markierte Inhalte und Wasserzeichen-Anmerkungen)."""
    writer = A.open_writer(data)
    out = {}
    for index, page in enumerate(writer.pages):
        found = _foreign_blocks(writer, page, remove=False)
        if found:
            out[index] = found
    return out


def remove_foreign_watermarks(data: bytes) -> bytes:
    writer = A.open_writer(data)
    if not sum(_foreign_blocks(writer, page, remove=True) for page in writer.pages):
        raise PdfEditError("Keine als Wasserzeichen markierten Inhalte gefunden")
    return A.finish(writer)


def preview(data: bytes, spec: Spec, page_index: int = 0, image: bytes | None = None, filename: str = "") -> bytes:
    """Eine Seite mit der Ebene als eigenes kleines PDF – für die Vorschau im Dialog, schnell auch bei großen
    Dokumenten. Seitenzahlen stimmen mit dem späteren Ergebnis überein."""
    spec = checked(spec)
    count = page_count(data)
    pages = parse_ranges(spec.pages, count)
    single = extract(data, [page_index])
    if page_index not in pages:
        return single                                     # Seite liegt nicht im Bereich: ohne Ebene
    number = pages.index(page_index)
    one = replace(spec, pages="")
    last = None
    if isinstance(one, HeaderFooter):
        last = one.start + len(pages) - 1
        one = replace(one, start=one.start + number)
    writer = A.open_writer(single)
    image_ref = _encoded_image(writer, image) if getattr(one, "image", False) else None
    _add(writer, one, image_ref, filename, last_number=last)
    return A.finish(writer)
