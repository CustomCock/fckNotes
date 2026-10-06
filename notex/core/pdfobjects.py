"""Alles Eingefügte als „Objekt“ (Qt-frei): auflisten, verschieben, Größe ändern, löschen.

Ein Objekt ist eine Anmerkung oder ein Formularfeld-Widget auf einer Seite, adressiert über seine Position im
/Annots-Array der Seite. Markierungen (Markieren/Unterstreichen/Durchstreichen) hängen am Text: sie lassen sich
löschen und kommentieren, aber nicht verschieben. Links und Popups sind keine Objekte.

Verschieben/Größe ändern zeichnet das Erscheinungsbild neu, wo eine Verzerrung stören würde (Text auf der Seite,
Textfelder, Kontrollkästchen); Stempel (Unterschrift, Diagramm, Bild) werden einfach in den neuen Rahmen gesetzt –
die Oberfläche behält dafür das Seitenverhältnis bei.
"""
from __future__ import annotations

from dataclasses import dataclass

from pypdf.generic import DictionaryObject, NameObject

from notex.core import pdfannot as A
from notex.core import pdfforms as F
from notex.core.pdfpages import PdfEditError, open_reader

DIAGRAM_KEY = "/fckNotesDiagram"
MOVABLE = {"text", "note", "stamp", "signature", "diagram", "field"}


@dataclass(frozen=True)
class PdfObject:
    page: int
    index: int              # Position in /Annots
    kind: str               # text | note | stamp | signature | diagram | field | highlight | underline | …
    rect: A.Rect            # Ansichts-Punkte
    label: str
    contents: str = ""
    ours: bool = False
    field: str = ""         # voll qualifizierter Feldname (nur kind == "field")
    field_kind: str = ""    # text | checkbox | radio | choice | signature | button
    state: str = ""         # „an“-Zustand dieses Widgets (Kontrollkästchen/Optionsfeld)

    @property
    def movable(self) -> bool:
        return self.kind in MOVABLE

    @property
    def keep_aspect(self) -> bool:
        return self.kind in ("signature", "diagram", "stamp") or self.field_kind in ("checkbox", "radio")

    def contains(self, x: float, y: float, tolerance: float = 2.0) -> bool:
        x0, y0, x1, y1 = self.rect
        return x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance


def _kind_of(annot) -> str:
    subtype = str(annot.get("/Subtype", ""))
    if subtype == "/Widget":
        return "field"
    if subtype == "/Stamp":
        if DIAGRAM_KEY in annot:
            return "diagram"
        if str(annot.get("/Name", "")) == "/fckNotesSignature":
            return "signature"
        return "stamp"
    return A.KIND_NAMES.get(subtype, subtype.lstrip("/").lower())


LABELS = {"text": "Text", "note": "Notiz", "stamp": "Stempel", "signature": "Unterschrift", "diagram": "Diagramm",
          "highlight": "Markierung", "underline": "Unterstreichung", "strikeout": "Durchstreichung"}


def list_objects(data: bytes, page_index: int) -> list[PdfObject]:
    reader = open_reader(data)
    if not 0 <= page_index < len(reader.pages):
        return []
    page = reader.pages[page_index]
    geom = A.geometry_of(page)
    out = []
    for position, ref in enumerate(page.get("/Annots", None) or []):
        try:
            annot = ref.get_object()
            subtype = str(annot.get("/Subtype", ""))
            if subtype in ("/Link", "/Popup") or "/Rect" not in annot:
                continue
            x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        except Exception:                                  # noqa: BLE001 – kaputte Einträge überspringen
            continue
        rect = geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        kind = _kind_of(annot)
        ours = str(annot.get("/NM", "")).startswith(A.ID_PREFIX)
        if kind == "field":
            field_obj = F._field_of(annot)
            name = F._qualified(field_obj)
            field_kind = F._kind(field_obj)
            states = F._states(annot)
            out.append(PdfObject(page_index, position, "field", rect, f"Feld „{name}“", "", ours, name, field_kind,
                                 states[0] if states else ""))
        else:
            out.append(PdfObject(page_index, position, kind, rect, LABELS.get(kind, kind.capitalize()),
                                 str(annot.get("/Contents", "") or ""), ours))
    return out


def hit(objects: list[PdfObject], x: float, y: float) -> PdfObject | None:
    """Oberstes Objekt unter dem Punkt; kleine Objekte gewinnen gegen große, die sie überdecken."""
    candidates = [o for o in objects if o.contains(x, y)]
    if not candidates:
        return None
    return min(reversed(candidates), key=lambda o: (o.rect[2] - o.rect[0]) * (o.rect[3] - o.rect[1]))


def set_rect(data: bytes, page_index: int, position: int, rect_view: A.Rect) -> bytes:
    """Objekt an `position` in den Rahmen `rect_view` (Ansichts-Punkte) setzen."""
    x0, y0, x1, y1 = rect_view
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise PdfEditError("Zu klein")
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    annots = A._annots(page)
    if not 0 <= position < len(annots):
        raise PdfEditError("Dieses Objekt gibt es nicht mehr")
    annot = annots[position].get_object()
    kind = _kind_of(annot)
    if kind not in MOVABLE:
        raise PdfEditError("Markierungen hängen am Text – löschen und neu markieren")
    geom = A.geometry_of(page)
    if kind == "text":
        size, color, border = A.freetext_style(annot)
        text = str(annot.get("/Contents", "") or "")
        A._add_text(writer, page_index, (x0, y0, x1, y1), text, size, color, border, True, replace=position)
        return A.finish(writer)
    if kind == "note":
        rect = geom.rect_to_pdf((x0, y0, x0 + A.NOTE_SIZE, y0 + A.NOTE_SIZE))
        color = A._hex(annot.get("/C", [])) or A.DEFAULT_COLORS["note"]
        annot[NameObject("/Rect")] = A._floats(rect)
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): A._form(writer, A.note_content(rect, color), rect)})
        return A.finish(writer)
    annot[NameObject("/Rect")] = A._floats(geom.rect_to_pdf((x0, y0, x1, y1)))
    if kind == "field":
        field_obj = F._field_of(annot)
        field_kind = F._kind(field_obj)
        if field_kind in ("text", "choice"):
            F._set_text_appearance(writer, annot, field_obj, F._value_text(F._inherited(field_obj, "/V")))
        elif field_kind == "checkbox" and str(annot.get("/NM", "")).startswith(A.ID_PREFIX):
            states = F._states(annot)
            F._set_checkbox_appearance(writer, annot, states[0] if states else "Yes")
    return A.finish(writer)


def move_by(data: bytes, page_index: int, position: int, dx: float, dy: float) -> bytes:
    """Objekt um (dx, dy) Ansichts-Punkte verschieben (Pfeiltasten)."""
    obj = next((o for o in list_objects(data, page_index) if o.index == position), None)
    if obj is None:
        raise PdfEditError("Dieses Objekt gibt es nicht mehr")
    x0, y0, x1, y1 = obj.rect
    return set_rect(data, page_index, position, (x0 + dx, y0 + dy, x1 + dx, y1 + dy))


def delete_object(data: bytes, page_index: int, position: int) -> bytes:
    """Objekt löschen; bei einem Formularfeld das ganze Feld (alle Widgets)."""
    obj = next((o for o in list_objects(data, page_index) if o.index == position), None)
    if obj is None:
        raise PdfEditError("Dieses Objekt gibt es nicht mehr")
    if obj.kind == "field":
        return F.remove_field(data, obj.field)
    return A.delete_annotation(data, page_index, position)


def fit_aspect(old: A.Rect, new: A.Rect, anchor: str = "") -> A.Rect:
    """Neuen Rahmen auf das Seitenverhältnis des alten bringen (Ziehen an einer Ecke)."""
    ow, oh = max(old[2] - old[0], 1e-6), max(old[3] - old[1], 1e-6)
    x0, y0, x1, y1 = new
    w, h = abs(x1 - x0), abs(y1 - y0)
    aspect = ow / oh
    if w / max(h, 1e-6) > aspect:
        w = h * aspect
    else:
        h = w / aspect
    left = "l" in anchor
    top = "t" in anchor
    nx0 = x1 - w if left else x0
    ny0 = y1 - h if top else y0
    return nx0, ny0, nx0 + w, ny0 + h
