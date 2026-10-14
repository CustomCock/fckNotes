"""Drucken vorbereiten (Qt-frei, Block V11).

- `prepare`: Seitenbereich wählen und Anmerkungen/Formularinhalte nach Wunsch einbrennen oder weglassen. Gedruckt
  wird nur, was das PDF zum Drucken markiert (/F „Drucken“); ausgeblendete Anmerkungen nie.
- `placement`: wie eine Seite aufs Papier kommt – „An Seite anpassen“, „Originalgröße“ oder „Nur große verkleinern“,
  Hoch/Quer automatisch nach der Seite.
- `print_to_pdf`: „In PDF drucken“ als **Vektor-PDF** (Text bleibt durchsuchbar) auf dem gewählten Papier, mit Kopien
  (sortiert oder nicht). Der echte Drucker bekommt dieselbe Platzierung über QtPrintSupport (Oberfläche).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfWriter, Transformation
from pypdf.generic import ArrayObject, NameObject

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError, extract, open_reader, page_count, parse_ranges

PAPERS = {"a4": (595.28, 841.89), "letter": (612.0, 792.0), "a3": (841.89, 1190.55), "source": None}
PAPER_LABELS = {"a4": "A4", "letter": "Letter", "a3": "A3", "source": "Wie die PDF-Seite"}
FITS = {"fit": "An Seite anpassen", "shrink": "Nur zu große verkleinern", "actual": "Originalgröße"}
PRINT_FLAG, HIDDEN_FLAG = 4, 2
MAX_COPIES = 99


@dataclass(frozen=True)
class PrintOptions:
    pages: str = ""                 # „1-3, 5“; leer = alle
    copies: int = 1
    collate: bool = True            # sortiert: 1 2 3, 1 2 3 – sonst 1 1, 2 2, 3 3
    fit: str = "fit"                # fit | shrink | actual
    auto_rotate: bool = True        # Hoch/Quer nach der Seite
    annotations: bool = True        # Kommentare, Stempel, Zeichnungen …
    forms: bool = True              # Formularfelder mit ihren Werten
    paper: str = "a4"               # a4 | letter | a3 | source

    def checked(self) -> "PrintOptions":
        if self.fit not in FITS:
            raise PdfEditError(f"Unbekannte Einpassung „{self.fit}“")
        if self.paper not in PAPERS:
            raise PdfEditError(f"Unbekanntes Papier „{self.paper}“")
        if not 1 <= int(self.copies) <= MAX_COPIES:
            raise PdfEditError(f"Kopien: 1 bis {MAX_COPIES}")
        return self


@dataclass(frozen=True)
class Placement:
    landscape: bool
    scale: float
    x: float                        # Lage auf dem Papier (Punkte, Ursprung unten links)
    y: float
    width: float
    height: float
    paper_width: float
    paper_height: float


def placement(page_w: float, page_h: float, paper_w: float | None, paper_h: float | None, fit: str = "fit",
              auto_rotate: bool = True) -> Placement:
    """Seite (Größe wie angezeigt) aufs Papier: Ausrichtung, Maßstab, mittig. `paper_w` None = Papier wie Seite."""
    if paper_w is None or paper_h is None:
        return Placement(page_w > page_h, 1.0, 0.0, 0.0, page_w, page_h, page_w, page_h)
    landscape = auto_rotate and page_w > page_h
    if landscape:
        paper_w, paper_h = max(paper_w, paper_h), min(paper_w, paper_h)
    else:
        paper_w, paper_h = min(paper_w, paper_h), max(paper_w, paper_h)
    fitted = min(paper_w / page_w, paper_h / page_h)
    scale = {"fit": fitted, "shrink": min(1.0, fitted), "actual": 1.0}[fit]
    width, height = page_w * scale, page_h * scale
    return Placement(landscape, scale, (paper_w - width) / 2, (paper_h - height) / 2, width, height, paper_w, paper_h)


def page_order(count: int, copies: int, collate: bool) -> list[int]:
    """Reihenfolge der Seiten (Indizes in der vorbereiteten Datei) über alle Kopien."""
    copies = max(1, min(int(copies), MAX_COPIES))
    if collate:
        return [page for _copy in range(copies) for page in range(count)]
    return [page for page in range(count) for _copy in range(copies)]


def prepare(data: bytes, options: PrintOptions) -> bytes:
    """Nur die gewählten Seiten; Anmerkungen/Formular nach Wunsch eingebrannt, alles andere weggelassen."""
    from notex.core import pdfforms as F
    options = options.checked()
    pages = parse_ranges(options.pages, page_count(data))
    if not pages:
        raise PdfEditError("Keine Seiten zum Drucken")
    writer = A.open_writer(data)
    for page in writer.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        keep = ArrayObject()
        for ref in annots.get_object():
            try:
                annot = ref.get_object()
                subtype = str(annot.get("/Subtype", ""))
                flags = int(annot.get("/F", 0) or 0)
            except Exception:                             # noqa: BLE001 – kaputte Einträge nicht drucken
                continue
            if subtype in ("/Popup", "/Link") or flags & HIDDEN_FLAG or not flags & PRINT_FLAG:
                continue
            wanted = options.forms if subtype == "/Widget" else options.annotations
            if wanted:
                keep.append(ref)
        if keep:
            page[NameObject("/Annots")] = keep
        else:
            del page["/Annots"]
    flattened = F.flatten(A.finish(writer))
    return extract(flattened, pages)


def print_to_pdf(data: bytes, options: PrintOptions, progress=None) -> bytes:
    """„In PDF drucken“: vorbereitete Seiten als Vektor-PDF auf dem Papier, mit Kopien."""
    options = options.checked()
    prepared = prepare(data, options)
    reader = PdfWriter(clone_from=open_reader(prepared))     # Seiten an einem Writer: Drehung übertragbar
    paper = PAPERS[options.paper]
    order = page_order(len(reader.pages), options.copies, options.collate)
    writer = PdfWriter()
    for number, index in enumerate(order):
        source = reader.pages[index]
        if source.rotation:
            source.transfer_rotation_to_content()
        box = source.cropbox
        left, bottom = float(box.left), float(box.bottom)
        page_w, page_h = float(box.width), float(box.height)
        place = placement(page_w, page_h, *(paper or (None, None)), options.fit, options.auto_rotate)
        target = writer.add_blank_page(place.paper_width, place.paper_height)
        transform = (Transformation().translate(-left, -bottom).scale(place.scale, place.scale)
                     .translate(place.x, place.y))
        target.merge_transformed_page(source, transform)
        if progress is not None:
            progress(number + 1, len(order))
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
