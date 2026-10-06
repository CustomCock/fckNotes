"""Diagramme im PDF (Qt-frei): als Stempel-Anmerkung mit eigenem Erscheinungsbild (Vektor) einfügen – und das Modell
(JSON, Flate-komprimiert) direkt an der Anmerkung mitspeichern, damit es später wieder bearbeitet werden kann.

Andere Programme zeigen das Diagramm als normalen Stempel an; nur fckNotes kennt den Schlüssel /fckNotesDiagram.
Beim „Einbrennen“ wird nur das Bild übernommen, das Modell fällt weg.
"""
from __future__ import annotations

import zlib

from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

from notex.core import pdfannot as A
from notex.core.diagram import pdf as dpdf
from notex.core.diagram.model import Diagram
from notex.core.pdfobjects import DIAGRAM_KEY
from notex.core.pdfpages import PdfEditError, open_reader


def _model_stream(writer, diagram: Diagram):
    stream = StreamObject()
    stream._data = zlib.compress(diagram.to_json().encode("utf-8"), 9)
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    stream[NameObject("/Length")] = NumberObject(len(stream._data))
    return writer._add_object(stream)


def _summary(diagram: Diagram) -> str:
    texts = [s.text.replace("\n--\n", " ").replace("\n", " ").strip() for s in diagram.shapes if s.text.strip()]
    return ("Diagramm: " + ", ".join(texts))[:500] if texts else "Diagramm"


MIN_SCALE = 0.25
PAGE_MARGIN = 12.0


def fit_scale(diagram: Diagram, top_left: tuple[float, float], page_size: tuple[float, float],
              scale: float = 1.0) -> float:
    """Maßstab, damit das Diagramm ab `top_left` auf die Seite passt (nie größer als `scale`)."""
    x0, y0 = top_left
    room_w = page_size[0] - max(0.0, x0) - PAGE_MARGIN
    room_h = page_size[1] - max(0.0, y0) - PAGE_MARGIN
    fit = min(scale, room_w / max(diagram.width, 1.0), room_h / max(diagram.height, 1.0))
    return max(MIN_SCALE, min(scale, fit))


def _annotation(writer, page_index: int, top_left: tuple[float, float], diagram: Diagram,
                scale: float = 1.0) -> DictionaryObject:
    page = A._page(writer, page_index)
    geom = A.geometry_of(page)
    x0, y0 = top_left
    rect = geom.rect_to_pdf((x0, y0, x0 + diagram.width * scale, y0 + diagram.height * scale))
    annot = A._base("/Stamp", rect, "#000000", _summary(diagram))
    annot[NameObject("/Name")] = NameObject("/fckNotesDiagram")
    appearance = A._form(writer, dpdf.content(diagram), [0, 0, diagram.width, diagram.height],
                         dpdf.font_resources(writer), geom.matrix())
    annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): appearance})
    annot[NameObject(DIAGRAM_KEY)] = _model_stream(writer, diagram)
    annot[NameObject("/P")] = page.indirect_reference
    return annot


def add_diagram(data: bytes, page_index: int, top_left: tuple[float, float], diagram: Diagram,
                scale: float | None = None) -> bytes:
    """Diagramm mit der linken oberen Ecke bei `top_left` (Ansichts-Punkte) einfügen; Größe = Diagrammgröße,
    bei Bedarf verkleinert, damit es auf die Seite passt (Vektor – bleibt scharf)."""
    if diagram.width < 4 or diagram.height < 4:
        raise PdfEditError("Das Diagramm ist zu klein")
    writer = A.open_writer(data)
    if scale is None:
        scale = fit_scale(diagram, top_left, A.geometry_of(A._page(writer, page_index)).size)
    annot = _annotation(writer, page_index, top_left, diagram, scale)
    A._annots(A._page(writer, page_index)).append(writer._add_object(annot))
    return A.finish(writer)


def read_diagram(data: bytes, page_index: int, position: int) -> Diagram:
    reader = open_reader(data)
    if not 0 <= page_index < len(reader.pages):
        raise PdfEditError("Diese Seite gibt es nicht")
    annots = reader.pages[page_index].get("/Annots") or []
    if not 0 <= position < len(annots):
        raise PdfEditError("Dieses Diagramm gibt es nicht mehr")
    annot = annots[position].get_object()
    stream = annot.get(DIAGRAM_KEY)
    if stream is None:
        raise PdfEditError("Das ist kein fckNotes-Diagramm (nur Bild)")
    try:
        return Diagram.from_json(stream.get_object().get_data().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise PdfEditError(f"Diagramm beschädigt ({error})") from error


def update_diagram(data: bytes, page_index: int, position: int, diagram: Diagram,
                   top_left: tuple[float, float] | None = None) -> bytes:
    """Diagramm an `position` ersetzen; ohne `top_left` bleibt die linke obere Ecke, die Größe folgt dem Modell."""
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    annots = A._annots(page)
    if not 0 <= position < len(annots):
        raise PdfEditError("Dieses Diagramm gibt es nicht mehr")
    old = annots[position].get_object()
    geom = A.geometry_of(page)
    x0, y0, x1, y1 = (float(v) for v in old["/Rect"])
    view = geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    if top_left is None:
        top_left = (view[0], view[1])
    scale = 1.0                                          # vom Nutzer gewählte Größe (Griffe) beibehalten
    stream = old.get(DIAGRAM_KEY)
    if stream is not None:
        try:
            previous = Diagram.from_json(stream.get_object().get_data().decode("utf-8"))
            scale = (view[2] - view[0]) / max(previous.width, 1.0)
        except (ValueError, UnicodeDecodeError):
            pass
    scale = fit_scale(diagram, top_left, geom.size, scale)
    annots[position] = writer._add_object(_annotation(writer, page_index, top_left, diagram, scale))
    return A.finish(writer)
