"""Weitere Formularfeld-Typen, Feld-Eigenschaften und Tab-Reihenfolge (Qt-frei, Block V5).

- Optionsfeld-Gruppe: ein Feld (`/FT /Btn`, Radio) mit einem Widget je Option; genau ein Wert (`/V`) oder keiner.
- Dropdown und Listenfeld (`/FT /Ch`, Dropdown mit `/Ff Combo`, optional mit eigener Eingabe).
- Schaltfläche (`/FT /Btn`, Pushbutton) – nur „Formular zurücksetzen“ (`/ResetForm`) und „Seite anspringen“
  (`/GoTo`). Kein JavaScript, nie.
- Eigenschaften: Pflicht, nur lesen, Standardwert (`/DV`), Optionen, Schriftgröße, Ausrichtung (`/Q`), Tooltip
  (`/TU`), Beschriftung der Schaltfläche.
- Tab-Reihenfolge: Viewer lesen sie aus `/Tabs` der Seite und der Reihenfolge der Widgets in `/Annots`. Beides wird
  passend gesetzt: eigene Reihenfolge = Widgets in `/Annots` umsortiert, `/Tabs` entfernt („wie Anmerkungen“);
  „nach Zeilen“/„nach Spalten“ = `/Tabs /R` bzw. `/C` und `/Annots` in derselben Reihenfolge.

Erscheinungsbilder zeichnen wir selbst (Helvetica wie die übrigen Felder, siehe pdfforms).
"""
from __future__ import annotations

from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from notex.core import pdfannot as A
from notex.core import pdfforms as F
from notex.core.pdfpages import PdfEditError

MAX_OPTIONS = 500
BUTTON_FILL = "#e6e6e6"


# ---- Erscheinungsbilder ----------------------------------------------------------------------------------------
def radio_appearance(width: float, height: float, on: bool, border: str | None = F.BORDER) -> str:
    """Kreis (Rand) und bei „an“ ein Punkt in der Mitte."""
    d = min(width, height)
    cx, cy, r = width / 2, height / 2, d / 2 - 0.5
    k = 0.5523

    def circle(radius: float) -> str:
        c = radius * k
        n = A._num
        return (f"{n(cx + radius)} {n(cy)} m {n(cx + radius)} {n(cy + c)} {n(cx + c)} {n(cy + radius)} {n(cx)} "
                f"{n(cy + radius)} c {n(cx - c)} {n(cy + radius)} {n(cx - radius)} {n(cy + c)} {n(cx - radius)} "
                f"{n(cy)} c {n(cx - radius)} {n(cy - c)} {n(cx - c)} {n(cy - radius)} {n(cx)} {n(cy - radius)} c "
                f"{n(cx + c)} {n(cy - radius)} {n(cx + radius)} {n(cy - c)} {n(cx + radius)} {n(cy)} c h")
    ops = []
    if border:
        ops.append(f"q {A._rgb_op(border, 'RG')} 1 w {circle(r)} S Q")
    if on:
        ops.append(f"q 0 g {circle(max(1.0, r * 0.45))} f Q")
    return "\n".join(ops) or " "


def button_appearance(label: str, width: float, height: float, size: float = 0.0) -> str:
    size = size or max(6.0, min(12.0, height * 0.55))
    while size > 5 and A.text_width(label, size) > width - 6:
        size -= 0.5
    x = (width - A.text_width(label, size)) / 2
    y = (height - size * 0.72) / 2
    return (f"q {A._rgb_op(BUTTON_FILL, 'rg')} 0 0 {A._num(width)} {A._num(height)} re f Q\n"
            f"q {A._rgb_op(F.BORDER, 'RG')} 1 w 0.5 0.5 {A._num(width - 1)} {A._num(height - 1)} re S Q\n"
            f"BT /Helv {A._num(size)} Tf 0 g {A._num(x)} {A._num(y)} Td {A.pdf_string(label)} Tj ET")


def _set_radio_appearance(writer, widget, state: str) -> None:
    w, h, rotate = F._widget_box(widget)
    matrix = F._matrix(rotate)
    widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): DictionaryObject({
        NameObject("/" + state): A._form(writer, radio_appearance(w, h, True), [0, 0, w, h], None, matrix),
        NameObject("/Off"): A._form(writer, radio_appearance(w, h, False), [0, 0, w, h], None, matrix),
    })})


def _set_button_appearance(writer, widget, label: str) -> None:
    w, h, rotate = F._widget_box(widget)
    content = button_appearance(label, w, h)
    widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): A._form(
        writer, content, [0, 0, w, h], A.helvetica_resources(writer), F._matrix(rotate))})


# ---- Prüfen ----------------------------------------------------------------------------------------------------
def _clean_options(options: list[str]) -> list[str]:
    out = []
    for option in options:
        option = " ".join(str(option).split())
        if option and option not in out:
            out.append(option[:200])
    if not out:
        raise PdfEditError("Bitte mindestens eine Option angeben")
    if len(out) > MAX_OPTIONS:
        raise PdfEditError(f"Höchstens {MAX_OPTIONS} Optionen")
    return out


def _check_rect(rect_view: A.Rect, min_size: float = 8) -> None:
    x0, y0, x1, y1 = rect_view
    if x1 - x0 < min_size or y1 - y0 < min_size:
        raise PdfEditError("Das Feld ist zu klein – Bereich aufziehen")


# ---- Optionsfeld-Gruppe ----------------------------------------------------------------------------------------
def layout_radios(rect_view: A.Rect, count: int, vertical: bool = True, size: float = 14.0) -> list[A.Rect]:
    """`count` Optionsfelder gleichmäßig im aufgezogenen Bereich verteilen (Kreise der Größe `size`)."""
    x0, y0, x1, y1 = rect_view
    count = max(1, count)
    if vertical:
        step = max(size + 4, (y1 - y0) / count)
        return [(x0, y0 + i * step, x0 + size, y0 + i * step + size) for i in range(count)]
    step = max(size + 4, (x1 - x0) / count)
    return [(x0 + i * step, y0, x0 + i * step + size, y0 + size) for i in range(count)]


def add_radio_group(data: bytes, page_index: int, rects_view: list[A.Rect], name: str, options: list[str],
                    value: str = "", required: bool = False) -> bytes:
    """Optionsfeld-Gruppe: je Option ein Kreis; genau eine Option (oder keine) ist gewählt."""
    options = _clean_options(options)
    if len(rects_view) != len(options):
        raise PdfEditError("Für jede Option braucht es ein Kästchen")
    if value and value not in options:
        raise PdfEditError(f"„{value}“ ist keine der Optionen")
    writer = A.open_writer(data)
    existing = {f.name for f in F.list_fields(data)}
    name = name.strip()
    if not name or "." in name:
        raise PdfEditError("Feldname darf nicht leer sein und keinen Punkt enthalten")
    if name in existing:
        raise PdfEditError(f"Ein Feld „{name}“ gibt es schon")
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    flags = F.FF_RADIO | F.FF_NOTOGGLETOOFF | (F.FF_REQUIRED if required else 0)
    parent = DictionaryObject({
        NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject(name),
        NameObject("/Ff"): NumberObject(flags),
        NameObject("/V"): NameObject("/" + value) if value else NameObject("/Off"),
        NameObject("/Kids"): ArrayObject(),
    })
    parent_ref = writer._add_object(parent)
    for option, rect in zip(options, rects_view):
        x0, y0, x1, y1 = rect
        side = max(8.0, min(x1 - x0, y1 - y0))
        widget = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Parent"): parent_ref,
            NameObject("/Rect"): A._floats(geom.rect_to_pdf((x0, y0, x0 + side, y0 + side))),
            NameObject("/F"): NumberObject(4), NameObject("/P"): page.indirect_reference,
            NameObject("/AS"): NameObject("/" + option) if option == value else NameObject("/Off"),
            NameObject("/MK"): DictionaryObject({NameObject("/BC"): A._floats(A.rgb(F.BORDER)),
                                                 NameObject("/R"): NumberObject(geom.rotate),
                                                 NameObject("/CA"): TextStringObject("l")}),
            NameObject("/DA"): TextStringObject("/ZaDb 0 Tf 0 g"),
            NameObject("/TU"): TextStringObject(option),
        })
        _set_radio_appearance(writer, widget, option)
        ref = writer._add_object(widget)
        parent["/Kids"].append(ref)
        A._annots(page).append(ref)
    F._acroform(writer)["/Fields"].get_object().append(parent_ref)
    return A.finish(writer)


# ---- Dropdown / Listenfeld -------------------------------------------------------------------------------------
def add_choice(data: bytes, page_index: int, rect_view: A.Rect, name: str, options: list[str], combo: bool = True,
               value: str = "", editable: bool = False, size: float = 0.0) -> bytes:
    """Dropdown (`combo`) oder Listenfeld mit festen Einträgen; Dropdown auf Wunsch mit eigener Eingabe."""
    options = _clean_options(options)
    _check_rect(rect_view)
    if value and value not in options and not (combo and editable):
        raise PdfEditError(f"„{value}“ ist keiner der Einträge")
    writer = A.open_writer(data)
    widget, _rotate = F._new_widget(writer, page_index, rect_view, name, "/Ch")
    flags = (F.FF_COMBO | (F.FF_EDIT if editable else 0)) if combo else 0
    if flags:
        widget[NameObject("/Ff")] = NumberObject(flags)
    widget[NameObject("/Opt")] = ArrayObject([TextStringObject(o) for o in options])
    widget[NameObject("/V")] = TextStringObject(value)
    widget[NameObject("/DA")] = TextStringObject(f"/Helv {A._num(size)} Tf 0 g")
    F._set_text_appearance(writer, widget, widget, value)
    F._register(writer, page_index, widget)
    return A.finish(writer)


# ---- Schaltfläche ----------------------------------------------------------------------------------------------
def add_button(data: bytes, page_index: int, rect_view: A.Rect, name: str, label: str, action: str = "reset",
               target_page: int = 0) -> bytes:
    """Schaltfläche: `action` "reset" (Formular zurücksetzen) oder "goto" (Seite `target_page` anspringen)."""
    _check_rect(rect_view)
    label = " ".join((label or "").split())[:80]
    if not label:
        raise PdfEditError("Die Schaltfläche braucht eine Beschriftung")
    writer = A.open_writer(data)
    widget, _rotate = F._new_widget(writer, page_index, rect_view, name, "/Btn")
    widget[NameObject("/Ff")] = NumberObject(F.FF_PUSHBUTTON)
    widget[NameObject("/H")] = NameObject("/P")
    widget["/MK"][NameObject("/CA")] = TextStringObject(label)
    widget["/MK"][NameObject("/BG")] = A._floats(A.rgb(BUTTON_FILL))
    widget[NameObject("/DA")] = TextStringObject("/Helv 0 Tf 0 g")
    widget[NameObject("/A")] = _action(writer, action, target_page)
    _set_button_appearance(writer, widget, label)
    F._register(writer, page_index, widget)
    return A.finish(writer)


def _action(writer, action: str, target_page: int) -> DictionaryObject:
    if action == "reset":
        return DictionaryObject({NameObject("/S"): NameObject("/ResetForm")})
    if action == "goto":
        if not 0 <= target_page < len(writer.pages):
            raise PdfEditError(f"Seite {target_page + 1} gibt es nicht")
        return DictionaryObject({NameObject("/S"): NameObject("/GoTo"), NameObject("/D"): ArrayObject([
            writer.pages[target_page].indirect_reference, NameObject("/Fit")])})
    raise PdfEditError("Schaltflächen können nur zurücksetzen oder eine Seite anspringen")


# ---- Eigenschaften ---------------------------------------------------------------------------------------------
def set_properties(data: bytes, name: str, *, required: bool | None = None, read_only: bool | None = None,
                   default: str | bool | None = None, options: list[str] | None = None,
                   size: float | None = None, quadding: int | None = None, tooltip: str | None = None,
                   label: str | None = None, action: str | None = None, target_page: int = 0) -> bytes:
    """Eigenschaften eines Felds ändern; Erscheinungsbilder werden neu gezeichnet."""
    writer = A.open_writer(data)
    widgets = F._find_widgets(writer, name)
    if not widgets:
        raise PdfEditError(f"Feld „{name}“ gibt es nicht")
    field_obj = F._field_of(widgets[0][1])
    kind = F._kind(field_obj)
    flags = int(field_obj.get("/Ff", F._inherited(field_obj, "/Ff") or 0) or 0)
    if required is not None:
        flags = flags | F.FF_REQUIRED if required else flags & ~F.FF_REQUIRED
    if read_only is not None:
        flags = flags | F.FF_READONLY if read_only else flags & ~F.FF_READONLY
    field_obj[NameObject("/Ff")] = NumberObject(flags)
    if options is not None:
        if kind != "choice":
            raise PdfEditError("Einträge gibt es nur bei Dropdown und Listenfeld")
        options = _clean_options(options)
        field_obj[NameObject("/Opt")] = ArrayObject([TextStringObject(o) for o in options])
        value = F._value_text(F._inherited(field_obj, "/V"))
        if value and value not in options and not flags & F.FF_EDIT:
            field_obj[NameObject("/V")] = TextStringObject("")
    if default is not None:
        if kind in ("text", "choice"):
            field_obj[NameObject("/DV")] = TextStringObject(str(default))
        elif kind == "checkbox":
            on = next((s for _p, w in widgets for s in F._states(w)), "Yes")
            field_obj[NameObject("/DV")] = NameObject("/" + on) if default else NameObject("/Off")
        elif kind == "radio":
            states = {s for _p, w in widgets for s in F._states(w)}
            if default and str(default) not in states:
                raise PdfEditError(f"„{default}“ ist keine Option")
            field_obj[NameObject("/DV")] = NameObject("/" + str(default)) if default else NameObject("/Off")
    if tooltip is not None:
        if tooltip.strip():
            field_obj[NameObject("/TU")] = TextStringObject(tooltip.strip()[:500])
        elif "/TU" in field_obj:
            del field_obj["/TU"]
    if quadding is not None:
        if quadding not in (0, 1, 2):
            raise PdfEditError("Ausrichtung: 0 links, 1 Mitte, 2 rechts")
        field_obj[NameObject("/Q")] = NumberObject(quadding)
    if size is not None:
        if not (size == 0 or 4 <= size <= 72):
            raise PdfEditError("Schriftgröße 0 (passend) oder 4–72 pt")
        for _page, widget in widgets:
            widget[NameObject("/DA")] = TextStringObject(f"/Helv {A._num(size)} Tf 0 g")
    if kind == "button":
        widget = widgets[0][1]
        if action is not None:
            widget[NameObject("/A")] = _action(writer, action, target_page)
        if label is not None:
            label = " ".join(label.split())[:80]
            if not label:
                raise PdfEditError("Die Schaltfläche braucht eine Beschriftung")
            widget["/MK"].get_object()[NameObject("/CA")] = TextStringObject(label)
            _set_button_appearance(writer, widget, label)
    if kind in ("text", "choice"):
        value = F._value_text(F._inherited(field_obj, "/V"))
        for _page, widget in widgets:
            F._set_text_appearance(writer, widget, field_obj, value)
    return A.finish(writer)


def reset_values(data: bytes) -> bytes:
    """Wie die Schaltfläche „Formular zurücksetzen“: jedes Feld auf seinen Standardwert (oder leer)."""
    values = {}
    for info in F.list_fields(data):
        if info.read_only or info.kind not in ("text", "choice", "checkbox", "radio"):
            continue
        values[info.name] = bool(info.default) if info.kind == "checkbox" else info.default
    return F.fill(data, values) if values else data


# ---- Tab-Reihenfolge -------------------------------------------------------------------------------------------
def tab_order(data: bytes, page_index: int) -> list[str]:
    """Feldnamen der Seite in Tab-Reihenfolge (Reihenfolge in /Annots, jedes Feld einmal)."""
    names = []
    for info in sorted(F.list_fields(data), key=lambda f: min((p for pg, p in f.widgets if pg == page_index),
                                                                default=10 ** 9)):
        if any(pg == page_index for pg, _p in info.widgets):
            names.append(info.name)
    return names


def _reorder(writer, page_index: int, order: list[str], tabs: str | None) -> None:
    page = A._page(writer, page_index)
    annots = A._annots(page)
    widgets: dict[str, list] = {}
    others = []
    for ref in list(annots):
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Widget":
            widgets.setdefault(F._qualified(F._field_of(obj)), []).append(ref)
        else:
            others.append(ref)
    known = [n for n in order if n in widgets] + [n for n in widgets if n not in order]
    new = ArrayObject(others + [ref for n in known for ref in widgets[n]])
    annots.clear()
    annots.extend(new)
    if tabs:
        page[NameObject("/Tabs")] = NameObject(tabs)
    elif "/Tabs" in page:
        del page["/Tabs"]


def set_tab_order(data: bytes, page_index: int, names: list[str]) -> bytes:
    """Eigene Tab-Reihenfolge für die Seite (Felder, die fehlen, kommen danach in alter Reihenfolge)."""
    writer = A.open_writer(data)
    _reorder(writer, page_index, list(names), None)
    return A.finish(writer)


def auto_tab_order(data: bytes, page_index: int, mode: str = "rows") -> bytes:
    """Tab-Reihenfolge „nach Zeilen“ (`rows`) oder „nach Spalten“ (`columns`) – /Tabs und /Annots passend."""
    if mode not in ("rows", "columns"):
        raise PdfEditError("Reihenfolge: nach Zeilen oder nach Spalten")
    infos = [f for f in F.list_fields(data) if f.page == page_index]
    band = 8.0                                            # Felder, die bis 8 pt versetzt sind, zählen als eine Zeile

    def key(info):
        x0, y0 = info.rect[0], info.rect[1]
        return (round(y0 / band), x0) if mode == "rows" else (round(x0 / band), y0)
    order = [f.name for f in sorted(infos, key=key)]
    writer = A.open_writer(data)
    _reorder(writer, page_index, order, "/R" if mode == "rows" else "/C")
    return A.finish(writer)
