"""PDF-Formulare und Stempel (Qt-frei, pypdf): Felder auflisten und ausfüllen, Textfelder und Kontrollkästchen
anlegen, Felder löschen, sichtbare Unterschrift (gezeichnet oder als Bild), alles „fest einbrennen“.

Anzeige: PDFium zeichnet Formularfelder in QtPdf nicht (dafür bräuchte es die Formular-Umgebung). `display_copy`
erzeugt deshalb nur für die Anzeige eine Kopie, in der Felder als normale Stempel-Anmerkungen gelten – die echte
Datei bleibt ein Formular. Fehlende Erscheinungsbilder von Textfeldern werden dabei nachgezeichnet.

Erscheinungsbilder für Textfelder zeichnen wir selbst (Helvetica/WinAnsi, Größe aus /DA, 0 = passend, Ausrichtung
/Q, mehrzeilig, Rahmen/Hintergrund aus /MK, Drehung /MK /R). XFA-Formulare (Adobe LiveCycle) werden beim Ändern auf
AcroForm zurückgeführt (/XFA entfernt) – sonst zeigte Acrobat die neuen Werte nicht.

Unterschrift heißt hier: sichtbares Bild bzw. Linien auf der Seite – keine digitale (kryptografische) Signatur.
"""
from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field

from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
                           StreamObject, TextStringObject)

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError, open_reader

FF_READONLY = 1
FF_MULTILINE = 1 << 12
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
FF_COMBO = 1 << 17
BORDER = "#808080"


@dataclass
class FieldInfo:
    name: str                       # voll qualifiziert (Eltern.Kind)
    kind: str                       # text | checkbox | radio | choice | button | signature
    value: str = ""                 # Text, Auswahl, Export-Name (Kontrollkästchen/Optionsfeld: "" = aus)
    options: list[str] = field(default_factory=list)   # Auswahl-Einträge bzw. Export-Namen der Optionsfelder
    page: int = -1
    rect: A.Rect = (0, 0, 0, 0)     # erstes Widget, Ansichts-Punkte
    multiline: bool = False
    read_only: bool = False
    on_state: str = ""              # Kontrollkästchen: Name des „an“-Zustands (z. B. Yes)
    widgets: list[tuple[int, int]] = field(default_factory=list)   # (Seite, Position in /Annots)

    @property
    def checked(self) -> bool:
        return self.kind == "checkbox" and bool(self.value)


# ---- Hilfen ------------------------------------------------------------------------------------------------------
def _inherited(obj, key: str):
    seen = 0
    while obj is not None and seen < 32:
        if key in obj:
            return obj[key]
        parent = obj.get("/Parent")
        obj = parent.get_object() if parent is not None else None
        seen += 1
    return None


def _field_of(widget):
    """Feld-Dictionary zu einem Widget: das Widget selbst (zusammengelegt) oder sein Eltern-Feld."""
    if "/T" in widget or "/Parent" not in widget:
        return widget
    return widget["/Parent"].get_object()


def _qualified(field_obj) -> str:
    parts, obj, seen = [], field_obj, 0
    while obj is not None and seen < 32:
        if "/T" in obj:
            parts.append(str(obj["/T"]))
        parent = obj.get("/Parent")
        obj = parent.get_object() if parent is not None else None
        seen += 1
    return ".".join(reversed(parts))


def _states(widget) -> list[str]:
    ap = widget.get("/AP")
    normal = ap.get_object().get("/N") if ap is not None else None
    normal = normal.get_object() if normal is not None else None
    if isinstance(normal, DictionaryObject) and not isinstance(normal, StreamObject):
        return [str(k).lstrip("/") for k in normal.keys() if str(k) != "/Off"]
    return []


def _kind(field_obj) -> str:
    ft = str(_inherited(field_obj, "/FT") or "")
    flags = int(_inherited(field_obj, "/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Ch":
        return "choice"
    if ft == "/Sig":
        return "signature"
    if ft == "/Btn":
        if flags & FF_PUSHBUTTON:
            return "button"
        return "radio" if flags & FF_RADIO else "checkbox"
    return "unknown"


def _options(field_obj) -> list[str]:
    out = []
    for entry in _inherited(field_obj, "/Opt") or []:
        entry = entry.get_object() if hasattr(entry, "get_object") else entry
        if isinstance(entry, (list, ArrayObject)) and entry:
            out.append(str(entry[-1]))
        else:
            out.append(str(entry))
    return out


def _value_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, ArrayObject)):
        return ", ".join(str(v) for v in value)
    text = str(value)
    return text.lstrip("/") if text.startswith("/") else text


# ---- Auflisten ---------------------------------------------------------------------------------------------------
def list_fields(data: bytes) -> list[FieldInfo]:
    reader = open_reader(data)
    fields: dict[str, FieldInfo] = {}
    for page_index, page in enumerate(reader.pages):
        geom = A.geometry_of(page)
        for position, ref in enumerate(page.get("/Annots", None) or []):
            try:
                widget = ref.get_object()
                if widget.get("/Subtype") != "/Widget":
                    continue
                field_obj = _field_of(widget)
                name = _qualified(field_obj) or f"Feld{len(fields) + 1}"
                x0, y0, x1, y1 = (float(v) for v in widget["/Rect"])
            except Exception:                              # noqa: BLE001 – kaputte Widgets überspringen
                continue
            info = fields.get(name)
            if info is None:
                kind = _kind(field_obj)
                flags = int(_inherited(field_obj, "/Ff") or 0)
                value = _value_text(_inherited(field_obj, "/V"))
                if kind in ("checkbox", "radio") and value == "Off":
                    value = ""
                info = FieldInfo(name, kind, value, _options(field_obj) if kind == "choice" else [], page_index,
                                 geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))),
                                 bool(flags & FF_MULTILINE), bool(flags & FF_READONLY))
                fields[name] = info
            info.widgets.append((page_index, position))
            states = _states(widget)
            if info.kind == "checkbox" and states and not info.on_state:
                info.on_state = states[0]
            if info.kind == "radio":
                info.options += [s for s in states if s not in info.options]
    return list(fields.values())


# ---- Erscheinungsbilder ------------------------------------------------------------------------------------------
def _color_from(array) -> str | None:
    try:
        values = [float(v) for v in array]
    except (TypeError, ValueError):
        return None
    if len(values) == 1:
        values *= 3
    if len(values) != 3:
        return None
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in values)


def _mk(widget) -> dict:
    mk = widget.get("/MK")
    mk = mk.get_object() if mk is not None else {}
    return {"border": _color_from(mk.get("/BC", [])) if mk.get("/BC") else None,
            "background": _color_from(mk.get("/BG", [])) if mk.get("/BG") else None,
            "rotate": int(mk.get("/R", 0) or 0) % 360}


def text_appearance(value: str, width: float, height: float, size: float = 0.0, quadding: int = 0,
                    multiline: bool = False, border: str | None = BORDER, background: str | None = None) -> str:
    """Inhalt eines Textfeld-Erscheinungsbilds im Rechteck 0..width × 0..height."""
    ops = []
    if background:
        ops.append(f"q {A._rgb_op(background, 'rg')} 0 0 {A._num(width)} {A._num(height)} re f Q")
    if border:
        ops.append(f"q {A._rgb_op(border, 'RG')} 1 w 0.5 0.5 {A._num(width - 1)} {A._num(height - 1)} re S Q")
    ops.append("/Tx BMC q")
    ops.append(f"1 1 {A._num(max(0, width - 2))} {A._num(max(0, height - 2))} re W n")
    pad = 2.0
    if size <= 0:                                            # automatisch: passend zur Höhe, max. 12 pt
        size = 12.0 if multiline else max(4.0, min(12.0, (height - 2 * pad) * 0.72))
        if not multiline:
            while size > 4 and A.text_width(value, size) > width - 2 * pad:
                size -= 0.5
    lines = A.wrap(value, size, width - 2 * pad) if multiline else [value.replace("\n", " ")]
    leading = size * 1.15
    ops.append(f"BT /Helv {A._num(size)} Tf 0 g {A._num(leading)} TL")
    if multiline:
        y = height - pad - size * 0.93
    else:
        y = (height - size * 0.72) / 2
    for line in lines:
        line_width = A.text_width(line, size)
        x = pad if quadding == 0 else (width - line_width) / 2 if quadding == 1 else width - pad - line_width
        ops.append(f"1 0 0 1 {A._num(x)} {A._num(y)} Tm {A.pdf_string(line)} Tj")
        y -= leading
    ops.append("ET Q EMC")
    return "\n".join(ops)


def checkbox_appearance(width: float, height: float, on: bool, border: str | None = BORDER) -> str:
    ops = []
    if border:
        ops.append(f"q {A._rgb_op(border, 'RG')} 1 w 0.5 0.5 {A._num(width - 1)} {A._num(height - 1)} re S Q")
    if on:
        w, h = width, height
        ops.append(f"q 0 g 0 G {A._num(max(1.0, min(w, h) / 9))} w 1 J 1 j "
                   f"{A._num(w * 0.2)} {A._num(h * 0.52)} m {A._num(w * 0.42)} {A._num(h * 0.28)} l "
                   f"{A._num(w * 0.8)} {A._num(h * 0.76)} l S Q")
    return "\n".join(ops) or " "


def _widget_box(widget) -> tuple[float, float, int]:
    x0, y0, x1, y1 = (float(v) for v in widget["/Rect"])
    w, h = abs(x1 - x0), abs(y1 - y0)
    rotate = _mk(widget)["rotate"]
    return (h, w, rotate) if rotate in (90, 270) else (w, h, rotate)


def _matrix(rotate: int) -> list[float]:
    return A.PageGeom((0, 0, 1, 1), rotate).matrix()


def _set_text_appearance(writer, widget, field_obj, value: str) -> None:
    w, h, rotate = _widget_box(widget)
    da = str(_inherited(widget, "/DA") or _inherited(field_obj, "/DA") or "/Helv 0 Tf 0 g")
    match = re.search(r"([\d.]+)\s+Tf", da)
    size = float(match.group(1)) if match else 0.0
    flags = int(_inherited(field_obj, "/Ff") or 0)
    quadding = int(_inherited(field_obj, "/Q") or 0)
    mk = _mk(widget)
    content = text_appearance(value, w, h, size, quadding, bool(flags & FF_MULTILINE), mk["border"],
                              mk["background"])
    appearance = A._form(writer, content, [0, 0, w, h], A.helvetica_resources(writer), _matrix(rotate))
    widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance})


# ---- AcroForm ----------------------------------------------------------------------------------------------------
def _acroform(writer) -> DictionaryObject:
    root = writer._root_object
    form = root.get("/AcroForm")
    if form is None:
        form = DictionaryObject()
        root[NameObject("/AcroForm")] = writer._add_object(form)
    form = form.get_object()
    if "/Fields" not in form:
        form[NameObject("/Fields")] = ArrayObject()
    if "/XFA" in form:
        del form["/XFA"]                                     # sonst zeigte Acrobat die XFA-Daten statt unserer
    dr = form.get("/DR")
    if dr is None:
        dr = DictionaryObject()
        form[NameObject("/DR")] = dr
    dr = dr.get_object()
    fonts = dr.get("/Font")
    if fonts is None:
        fonts = DictionaryObject()
        dr[NameObject("/Font")] = fonts
    fonts = fonts.get_object()
    if "/Helv" not in fonts:
        fonts[NameObject("/Helv")] = A.helvetica_resources(writer)["/Font"]["/Helv"]
    if "/DA" not in form:
        form[NameObject("/DA")] = TextStringObject("/Helv 0 Tf 0 g")
    if form.get("/NeedAppearances"):
        del form["/NeedAppearances"]                         # wir liefern Erscheinungsbilder mit
    return form


def _find_widgets(writer, name: str) -> list[tuple[int, DictionaryObject]]:
    out = []
    for page_index, page in enumerate(writer.pages):
        for ref in page.get("/Annots", None) or []:
            widget = ref.get_object()
            if widget.get("/Subtype") == "/Widget" and _qualified(_field_of(widget)) == name:
                out.append((page_index, widget))
    return out


# ---- Ausfüllen ---------------------------------------------------------------------------------------------------
def fill(data: bytes, values: dict[str, object]) -> bytes:
    """Werte setzen: Text/Auswahl als Text, Kontrollkästchen als bool, Optionsfeld als Export-Name ("" = keins)."""
    writer = A.open_writer(data)
    infos = {f.name: f for f in list_fields(data)}
    _acroform(writer)
    for name, value in values.items():
        info = infos.get(name)
        if info is None:
            raise PdfEditError(f"Feld „{name}“ gibt es nicht")
        if info.read_only:
            raise PdfEditError(f"Feld „{name}“ ist schreibgeschützt")
        widgets = _find_widgets(writer, name)
        if not widgets:
            continue
        field_obj = _field_of(widgets[0][1])
        if info.kind in ("text", "choice"):
            text = "" if value is None else str(value)
            if info.kind == "choice" and info.options and text and text not in info.options and \
                    not int(_inherited(field_obj, "/Ff") or 0) & (1 << 18):     # nicht editierbare Liste
                raise PdfEditError(f"„{text}“ ist keine Auswahl in „{name}“")
            field_obj[NameObject("/V")] = TextStringObject(text)
            for _page, widget in widgets:
                _set_text_appearance(writer, widget, field_obj, text)
        elif info.kind == "checkbox":
            on = info.on_state or "Yes"
            state = NameObject("/" + on) if value else NameObject("/Off")
            field_obj[NameObject("/V")] = state
            for _page, widget in widgets:
                widget[NameObject("/AS")] = state if (not value or on in _states(widget)) else NameObject("/Off")
        elif info.kind == "radio":
            choice = str(value or "")
            if choice and choice not in info.options:
                raise PdfEditError(f"„{choice}“ ist keine Option von „{name}“")
            field_obj[NameObject("/V")] = NameObject("/" + choice) if choice else NameObject("/Off")
            for _page, widget in widgets:
                widget[NameObject("/AS")] = NameObject("/" + choice) if choice in _states(widget) else NameObject("/Off")
        else:
            raise PdfEditError(f"Feld „{name}“ ({info.kind}) lässt sich hier nicht ausfüllen")
    return A.finish(writer)


# ---- Anlegen / Löschen -------------------------------------------------------------------------------------------
def _new_widget(writer, page_index: int, rect_view: A.Rect, name: str, ft: str) -> tuple[DictionaryObject, int]:
    existing = {f.name for f in list_fields(_bytes(writer))}
    name = name.strip()
    if not name or "." in name:
        raise PdfEditError("Feldname darf nicht leer sein und keinen Punkt enthalten")
    if name in existing:
        raise PdfEditError(f"Ein Feld „{name}“ gibt es schon")
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    widget = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject(ft), NameObject("/T"): TextStringObject(name),
        NameObject("/Rect"): A._floats(geom.rect_to_pdf(rect_view)), NameObject("/F"): NumberObject(4),
        NameObject("/NM"): TextStringObject(A.ID_PREFIX + name),
        NameObject("/MK"): DictionaryObject({NameObject("/BC"): A._floats(A.rgb(BORDER)),
                                             NameObject("/R"): NumberObject(geom.rotate)}),
        NameObject("/P"): page.indirect_reference,
    })
    return widget, geom.rotate


def _bytes(writer) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _register(writer, page_index: int, widget: DictionaryObject) -> None:
    form = _acroform(writer)
    ref = writer._add_object(widget)
    A._annots(A._page(writer, page_index)).append(ref)
    form["/Fields"].get_object().append(ref)


def add_text_field(data: bytes, page_index: int, rect_view: A.Rect, name: str, value: str = "",
                   multiline: bool = False, size: float = 0.0) -> bytes:
    """Neues ausfüllbares Textfeld. `size` 0 = Schriftgröße passt sich an."""
    writer = A.open_writer(data)
    x0, y0, x1, y1 = rect_view
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise PdfEditError("Das Feld ist zu klein – Bereich aufziehen")
    widget, _rotate = _new_widget(writer, page_index, rect_view, name, "/Tx")
    widget[NameObject("/DA")] = TextStringObject(f"/Helv {A._num(size)} Tf 0 g")
    widget[NameObject("/V")] = TextStringObject(value)
    if multiline:
        widget[NameObject("/Ff")] = NumberObject(FF_MULTILINE)
    _set_text_appearance(writer, widget, widget, value)
    _register(writer, page_index, widget)
    return A.finish(writer)


def add_checkbox(data: bytes, page_index: int, rect_view: A.Rect, name: str, checked: bool = False) -> bytes:
    writer = A.open_writer(data)
    x0, y0, x1, y1 = rect_view
    side = max(8.0, min(x1 - x0, y1 - y0)) if x1 - x0 >= 4 and y1 - y0 >= 4 else 14.0
    widget, rotate = _new_widget(writer, page_index, (x0, y0, x0 + side, y0 + side), name, "/Btn")
    state = NameObject("/Yes") if checked else NameObject("/Off")
    widget[NameObject("/V")] = state
    widget[NameObject("/AS")] = state
    widget[NameObject("/DA")] = TextStringObject("/ZaDb 0 Tf 0 g")
    widget["/MK"][NameObject("/CA")] = TextStringObject("4")
    matrix = _matrix(rotate)
    widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): DictionaryObject({
        NameObject("/Yes"): A._form(writer, checkbox_appearance(side, side, True), [0, 0, side, side], None, matrix),
        NameObject("/Off"): A._form(writer, checkbox_appearance(side, side, False), [0, 0, side, side], None, matrix),
    })})
    _register(writer, page_index, widget)
    return A.finish(writer)


def remove_field(data: bytes, name: str) -> bytes:
    writer = A.open_writer(data)
    widgets = _find_widgets(writer, name)
    if not widgets:
        raise PdfEditError(f"Feld „{name}“ gibt es nicht")
    targets = {id(w) for _p, w in widgets}
    field_obj = _field_of(widgets[0][1])
    for page in writer.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        annots = annots.get_object()
        for i in range(len(annots) - 1, -1, -1):
            if id(annots[i].get_object()) in targets:
                del annots[i]
    form = writer._root_object.get("/AcroForm")
    if form is not None:
        _remove_from(form.get_object().get("/Fields"), field_obj, targets)
    parent = field_obj.get("/Parent")
    if parent is not None:
        _remove_from(parent.get_object().get("/Kids"), field_obj, targets)
    return A.finish(writer)


def _remove_from(array, field_obj, targets: set[int]) -> None:
    if array is None:
        return
    array = array.get_object()
    for i in range(len(array) - 1, -1, -1):
        obj = array[i].get_object()
        if obj is field_obj or id(obj) in targets:
            del array[i]


# ---- Unterschrift / Bild -----------------------------------------------------------------------------------------
def _stamp(writer, page_index: int, rect_view: A.Rect, content: str, resources, box: tuple[float, float],
           label: str) -> None:
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    annot = A._base("/Stamp", geom.rect_to_pdf(rect_view), "#000000", label)
    annot[NameObject("/Name")] = NameObject("/fckNotesSignature")
    appearance = A._form(writer, content, [0, 0, box[0], box[1]], resources, geom.matrix())
    A._attach(writer, page_index, annot, appearance)


def fit_rect(rect_view: A.Rect, aspect: float, default_width: float = 160.0) -> A.Rect:
    """Rechteck mit Seitenverhältnis `aspect` (Breite/Höhe) in den aufgezogenen Bereich einpassen (Klick = Standard)."""
    x0, y0, x1, y1 = rect_view
    w, h = x1 - x0, y1 - y0
    if w < 8 or h < 8:
        return x0, y0, x0 + default_width, y0 + default_width / max(aspect, 0.01)
    if w / h > aspect:
        w = h * aspect
    else:
        h = w / aspect
    return x0, y0, x0 + w, y0 + h


def add_strokes(data: bytes, page_index: int, rect_view: A.Rect, strokes: list[list[tuple[float, float]]],
                color: str = "#1a2a6c", width: float = 1.6, label: str = "Unterschrift") -> bytes:
    """Gezeichnete Unterschrift als Linien (Vektor). `strokes`: Punkte normiert 0..1, y nach unten."""
    strokes = [s for s in strokes if len(s) >= 2]
    if not strokes:
        raise PdfEditError("Nichts gezeichnet")
    writer = A.open_writer(data)
    x0, y0, x1, y1 = rect_view
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    ops = [f"q {A._rgb_op(color, 'RG')} {A._num(width)} w 1 J 1 j"]
    for stroke in strokes:
        px = [(max(0.0, min(1.0, x)) * w, (1 - max(0.0, min(1.0, y))) * h) for x, y in stroke]
        ops.append(f"{A._num(px[0][0])} {A._num(px[0][1])} m " + " ".join(f"{A._num(x)} {A._num(y)} l" for x, y in px[1:])
                   + " S")
    ops.append("Q")
    _stamp(writer, page_index, rect_view, "\n".join(ops), DictionaryObject(), (w, h), label)
    return A.finish(writer)


def add_image(data: bytes, page_index: int, rect_view: A.Rect, width_px: int, height_px: int, rgb: bytes,
              alpha: bytes | None = None, label: str = "Unterschrift") -> bytes:
    """Bild (z. B. eingescannte Unterschrift) als Stempel; `rgb` = 3 Byte je Pixel, `alpha` = 1 Byte je Pixel."""
    if width_px < 1 or height_px < 1 or len(rgb) != width_px * height_px * 3:
        raise PdfEditError("Bilddaten passen nicht zur Größe")
    if alpha is not None and len(alpha) != width_px * height_px:
        raise PdfEditError("Transparenz passt nicht zur Größe")
    writer = A.open_writer(data)
    image = StreamObject()
    image._data = zlib.compress(rgb, 9)
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(width_px), NameObject("/Height"): NumberObject(height_px),
                  NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
                  NameObject("/Filter"): NameObject("/FlateDecode")})
    if alpha is not None:
        mask = StreamObject()
        mask._data = zlib.compress(alpha, 9)
        mask.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                     NameObject("/Width"): NumberObject(width_px), NameObject("/Height"): NumberObject(height_px),
                     NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                     NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Filter"): NameObject("/FlateDecode")})
        image[NameObject("/SMask")] = writer._add_object(mask)
    x0, y0, x1, y1 = rect_view
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    resources = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)})})
    _stamp(writer, page_index, rect_view, f"q {A._num(w)} 0 0 {A._num(h)} 0 0 cm /Im0 Do Q", resources, (w, h), label)
    return A.finish(writer)


# ---- Anzeige-Kopie und Einbrennen ------------------------------------------------------------------------------
def has_widgets(data: bytes) -> bool:
    try:
        reader = open_reader(data)
    except PdfEditError:
        return False
    for page in reader.pages:
        for ref in page.get("/Annots", None) or []:
            try:
                if ref.get_object().get("/Subtype") == "/Widget":
                    return True
            except Exception:                              # noqa: BLE001
                continue
    return False


def display_copy(data: bytes) -> bytes | None:
    """Kopie nur für die Anzeige: Formularfelder als Stempel (PDFium zeichnet sie dann), fehlende Text-
    Erscheinungsbilder nachgezeichnet. None = nichts zu tun (keine Felder)."""
    if not has_widgets(data):
        return None
    writer = A.open_writer(data)
    for page in writer.pages:
        for ref in page.get("/Annots", None) or []:
            widget = ref.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            field_obj = _field_of(widget)
            if "/AP" not in widget and _kind(field_obj) in ("text", "choice"):
                _set_text_appearance(writer, widget, field_obj, _value_text(_inherited(field_obj, "/V")))
            widget[NameObject("/Subtype")] = NameObject("/Stamp")
    if "/AcroForm" in writer._root_object:
        del writer._root_object["/AcroForm"]
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _appearance_of(annot):
    ap = annot.get("/AP")
    if ap is None:
        return None
    normal = ap.get_object().get("/N")
    if normal is None:
        return None
    normal = normal.get_object()
    if isinstance(normal, StreamObject):
        return normal
    state = annot.get("/AS")
    if state is not None and state in normal:
        return normal[state].get_object()
    return None


def _transformed_bbox(form) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in form.get("/BBox", [0, 0, 1, 1]))
    a, b, c, d, e, f = (float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0]))
    points = [(a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def flatten(data: bytes, pages: list[int] | None = None) -> bytes:
    """Anmerkungen und Formularfelder fest in den Seiteninhalt einbrennen (danach nicht mehr änderbar).
    Links, Popups und ausgeblendete Anmerkungen bleiben bzw. fallen weg."""
    writer = A.open_writer(data)
    flattened_widgets = False
    for page_index, page in enumerate(writer.pages):
        if pages is not None and page_index not in pages:
            continue
        annots = page.get("/Annots")
        if annots is None:
            continue
        annots = annots.get_object()
        ops, keep = ["Q"], ArrayObject()
        xobjects = DictionaryObject()
        for ref in annots:
            annot = ref.get_object()
            subtype = annot.get("/Subtype")
            if subtype in ("/Link", "/Popup"):
                keep.append(ref)
                continue
            if subtype == "/Widget" and "/AP" not in annot and _kind(_field_of(annot)) in ("text", "choice"):
                field_obj = _field_of(annot)          # fremde Formulare ohne Bild: Wert sonst verloren
                _set_text_appearance(writer, annot, field_obj, _value_text(_inherited(field_obj, "/V")))
            flags = int(annot.get("/F", 0) or 0)
            form = _appearance_of(annot)
            if subtype == "/Widget":
                flattened_widgets = True
            if form is None or flags & 2:                        # unsichtbar/ohne Bild: einfach weg
                continue
            bx0, by0, bx1, by1 = _transformed_bbox(form)
            rx0, ry0, rx1, ry1 = (float(v) for v in annot["/Rect"])
            rx0, rx1, ry0, ry1 = min(rx0, rx1), max(rx0, rx1), min(ry0, ry1), max(ry0, ry1)
            sx = (rx1 - rx0) / max(bx1 - bx0, 1e-6)
            sy = (ry1 - ry0) / max(by1 - by0, 1e-6)
            name = f"/FxA{len(xobjects)}"
            xobjects[NameObject(name)] = form.indirect_reference or writer._add_object(form)
            ops.append(f"q {A._num(sx)} 0 0 {A._num(sy)} {A._num(rx0 - bx0 * sx)} {A._num(ry0 - by0 * sy)} cm "
                       f"{name} Do Q")
        if keep:
            page[NameObject("/Annots")] = keep
        elif "/Annots" in page:
            del page["/Annots"]
        if not xobjects:
            continue
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else DictionaryObject()
        page[NameObject("/Resources")] = resources
        existing = resources.get("/XObject")
        existing = existing.get_object() if existing is not None else DictionaryObject()
        existing.update(xobjects)
        resources[NameObject("/XObject")] = existing
        before, after = DecodedStreamObject(), DecodedStreamObject()
        before.set_data(b"q\n")
        after.set_data(("\n".join(ops) + "\n").encode("latin-1"))
        contents = page.get("/Contents")
        parts = ArrayObject([writer._add_object(before)])
        if contents is not None:
            contents_obj = contents.get_object()
            if isinstance(contents_obj, ArrayObject):
                parts.extend(contents_obj)
            else:
                parts.append(contents if hasattr(contents, "idnum") else writer._add_object(contents_obj))
        parts.append(writer._add_object(after))
        page[NameObject("/Contents")] = parts
    if flattened_widgets and "/AcroForm" in writer._root_object and \
            not any(_page_has_widgets(p) for p in writer.pages):
        del writer._root_object["/AcroForm"]
    return A.finish(writer)


def _page_has_widgets(page) -> bool:
    return any(ref.get_object().get("/Subtype") == "/Widget" for ref in page.get("/Annots", None) or [])
