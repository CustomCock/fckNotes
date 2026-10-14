"""Links auf PDF-Seiten (Qt-frei): auflisten, anlegen, Ziel ändern, löschen.

Ein Link ist eine `/Link`-Anmerkung mit Rechteck und Ziel. Ziele, die fckNotes anlegt:

- **Seite** (auch „Lesezeichen“ – die Oberfläche übernimmt dann dessen Seite und Position): `/Dest [Seite /XYZ …]`
- **Webadresse**: `/A << /S /URI >>`, nur `http`, `https` und `mailto`
- **Datei**: `/A << /S /GoToR >>` auf ein anderes PDF (nur `.pdf` – kein `/Launch`, das Programme starten könnte)

Fremde Ziele (benannte Ziele werden aufgelöst, alles andere heißt „andere Aktion“) bleiben beim Verschieben
unverändert. fckNotes folgt einem Link nur innerhalb des Dokuments; Webadressen öffnet die Oberfläche nicht.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject, NullObject,
                           NumberObject, TextStringObject)

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError, open_reader

URL_SCHEMES = ("http", "https", "mailto")
DEFAULT_COLOR = "#2f6fdf"
KIND_LABELS = {"page": "Seite", "url": "Webadresse", "file": "Datei", "other": "andere Aktion"}


@dataclass(frozen=True)
class LinkTarget:
    kind: str                       # page | url | file | other
    page: int | None = None         # 0-basiert (kind == page)
    top: float | None = None        # y in PDF-Koordinaten der Zielseite (None = ganze Seite)
    url: str = ""
    file: str = ""
    file_page: int = 0              # Seite im anderen PDF

    def describe(self) -> str:
        if self.kind == "page":
            return f"Seite {self.page + 1}" if self.page is not None else "Seite ?"
        if self.kind == "url":
            return self.url
        if self.kind == "file":
            return f"{self.file} (Seite {self.file_page + 1})" if self.file_page else self.file
        return "andere Aktion"


@dataclass(frozen=True)
class Link:
    page: int
    index: int                      # Position in /Annots
    rect: A.Rect                    # Ansichts-Punkte
    target: LinkTarget
    border: bool = False
    color: str = DEFAULT_COLOR
    ours: bool = False


# ---- Prüfen ----------------------------------------------------------------------------------------------------
def clean_url(url: str) -> str:
    """Webadresse prüfen und vervollständigen („example.org“ → „https://example.org“); sonst PdfEditError."""
    url = (url or "").strip()
    if not url:
        raise PdfEditError("Bitte eine Webadresse eingeben")
    if re.search(r"[\x00-\x20\x7f]", url):
        raise PdfEditError("Die Webadresse enthält Leer- oder Steuerzeichen")
    if "@" in url and ":" not in url:
        url = "mailto:" + url
    elif not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url):
        url = "https://" + url
    scheme = urlsplit(url).scheme.lower()
    if scheme not in URL_SCHEMES:
        raise PdfEditError("Erlaubt sind nur http-, https- und mailto-Adressen")
    if scheme in ("http", "https") and not urlsplit(url).netloc:
        raise PdfEditError("Die Webadresse hat keinen Rechnernamen")
    return url


def clean_file(path: str) -> str:
    path = (path or "").strip().replace("\\", "/")
    if not path:
        raise PdfEditError("Bitte eine PDF-Datei wählen")
    if re.search(r"[\x00-\x1f\x7f]", path):
        raise PdfEditError("Der Dateiname enthält Steuerzeichen")
    if not path.lower().endswith(".pdf"):
        raise PdfEditError("Links auf Dateien gehen nur zu anderen PDFs")
    return path


# ---- Lesen -----------------------------------------------------------------------------------------------------
def _dest_target(reader, dest) -> LinkTarget:
    dest = dest.get_object() if hasattr(dest, "get_object") else dest
    if isinstance(dest, (str, bytes)) or isinstance(dest, DictionaryObject):
        name = str(dest.get("/D") if isinstance(dest, DictionaryObject) else dest)
        named = reader.named_destinations.get(name)
        if named is None or named.page is None:
            return LinkTarget("other")
        top = float(named.top) if getattr(named, "top", None) is not None else None
        return LinkTarget("page", reader.get_page_number(named.page), top)
    if not len(dest):
        return LinkTarget("other")
    first = dest[0]
    page = int(first) if isinstance(first, int) else reader.get_page_number(first)
    kind = str(dest[1]) if len(dest) > 1 else "/Fit"
    top = None
    if kind == "/XYZ" and len(dest) > 3:
        top = _num(dest[3])
    elif kind in ("/FitH", "/FitBH") and len(dest) > 2:
        top = _num(dest[2])
    return LinkTarget("page", page, top)


def _num(value) -> float | None:
    try:
        return None if value is None or isinstance(value, NullObject) else float(value)
    except (TypeError, ValueError):
        return None


def _target_of(reader, annot) -> LinkTarget:
    try:
        if annot.get("/Dest") is not None:
            return _dest_target(reader, annot["/Dest"])
        action = annot.get("/A")
        if action is None:
            return LinkTarget("other")
        action = action.get_object()
        kind = str(action.get("/S", ""))
        if kind == "/URI":
            return LinkTarget("url", url=str(action.get("/URI", "") or ""))
        if kind == "/GoTo" and action.get("/D") is not None:
            return _dest_target(reader, action["/D"])
        if kind == "/GoToR":
            spec = action.get("/F")
            spec = spec.get_object() if spec is not None else ""
            name = str(spec.get("/UF") or spec.get("/F") or "") if isinstance(spec, DictionaryObject) else str(spec)
            dest = action.get("/D")
            file_page = 0
            if dest is not None and hasattr(dest, "__len__") and len(dest) and isinstance(dest[0], int):
                file_page = int(dest[0])
            return LinkTarget("file", file=name, file_page=file_page)
    except Exception:                                     # noqa: BLE001 – kaputtes Ziel: „andere Aktion“
        pass
    return LinkTarget("other")


def list_links(data: bytes, page_index: int) -> list[Link]:
    reader = open_reader(data)
    if not 0 <= page_index < len(reader.pages):
        return []
    page = reader.pages[page_index]
    geom = A.geometry_of(page)
    out = []
    for position, ref in enumerate(page.get("/Annots", None) or []):
        try:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Link" or "/Rect" not in annot:
                continue
            x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        except Exception:                                 # noqa: BLE001
            continue
        rect = geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        out.append(Link(page_index, position, rect, _target_of(reader, annot), _has_border(annot),
                        A._hex(annot.get("/C", [])) or DEFAULT_COLOR,
                        str(annot.get("/NM", "")).startswith(A.ID_PREFIX)))
    return out


def all_links(data: bytes) -> list[Link]:
    count = len(open_reader(data).pages)
    return [link for index in range(count) for link in list_links(data, index)]


def _has_border(annot) -> bool:
    bs = annot.get("/BS")
    if bs is not None:
        try:
            return float(bs.get_object().get("/W", 1)) > 0
        except (TypeError, ValueError):
            return False
    border = annot.get("/Border")
    if border is not None and len(border) >= 3:
        try:
            return float(border[2]) > 0
        except (TypeError, ValueError):
            return False
    return True                                           # PDF-Standard: Rahmen 1 pt


# ---- Schreiben -------------------------------------------------------------------------------------------------
def _set_target(writer, annot: DictionaryObject, target: LinkTarget) -> None:
    for key in ("/Dest", "/A"):
        if key in annot:
            del annot[key]
    if target.kind == "page":
        if target.page is None or not 0 <= target.page < len(writer.pages):
            raise PdfEditError("Diese Zielseite gibt es nicht")
        ref = writer.pages[target.page].indirect_reference
        if target.top is None:
            annot[NameObject("/Dest")] = ArrayObject([ref, NameObject("/Fit")])
        else:
            annot[NameObject("/Dest")] = ArrayObject([ref, NameObject("/XYZ"), NullObject(),
                                                      FloatObject(round(target.top, 2)), NullObject()])
    elif target.kind == "url":
        annot[NameObject("/A")] = DictionaryObject({NameObject("/S"): NameObject("/URI"),
                                                    NameObject("/URI"): TextStringObject(clean_url(target.url))})
    elif target.kind == "file":
        name = clean_file(target.file)
        spec = DictionaryObject({NameObject("/Type"): NameObject("/Filespec"), NameObject("/F"): TextStringObject(name),
                                 NameObject("/UF"): TextStringObject(name)})
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/S"): NameObject("/GoToR"), NameObject("/F"): spec,
            NameObject("/D"): ArrayObject([NumberObject(max(0, int(target.file_page))), NameObject("/Fit")]),
            NameObject("/NewWindow"): BooleanObject(True)})
    else:
        raise PdfEditError("Bitte ein Ziel wählen: Seite, Webadresse oder Datei")


def _style(writer, annot: DictionaryObject, rect_pdf: list[float], border: bool, color: str) -> None:
    r, g, b = A.rgb(color)
    annot[NameObject("/C")] = A._floats([r, g, b])
    annot[NameObject("/Border")] = ArrayObject([NumberObject(0), NumberObject(0), NumberObject(1 if border else 0)])
    annot[NameObject("/BS")] = DictionaryObject({NameObject("/W"): NumberObject(1 if border else 0),
                                                 NameObject("/S"): NameObject("/S")})
    if "/QuadPoints" in annot:
        del annot["/QuadPoints"]                          # sonst gilt das alte Rechteck weiter
    if border:
        x0, y0, x1, y1 = rect_pdf
        w, h = x1 - x0, y1 - y0
        content = f"{A._rgb_op(color, 'RG')} 1 w 0.5 0.5 {A._num(w - 1)} {A._num(h - 1)} re S"
        annot[NameObject("/AP")] = DictionaryObject({NameObject("/N"): A._form(writer, content, [0, 0, w, h])})
    elif "/AP" in annot:
        del annot["/AP"]


def add_link(data: bytes, page_index: int, rect_view: A.Rect, target: LinkTarget, border: bool = False,
             color: str = DEFAULT_COLOR) -> bytes:
    x0, y0, x1, y1 = rect_view
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise PdfEditError("Der Link-Bereich ist zu klein")
    writer = A.open_writer(data)
    page = A._page(writer, page_index)
    rect = A.geometry_of(page).rect_to_pdf((x0, y0, x1, y1))
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): A._floats(rect), NameObject("/H"): NameObject("/I"),
        NameObject("/F"): NumberObject(4),
        NameObject("/NM"): TextStringObject(A.ID_PREFIX + uuid.uuid4().hex[:12]),
        NameObject("/M"): TextStringObject(A._now()),
    })
    _set_target(writer, annot, target)
    _style(writer, annot, rect, border, color)
    A._attach(writer, page_index, annot)
    return A.finish(writer)


def _link_annot(writer, page_index: int, position: int):
    page = A._page(writer, page_index)
    annots = A._annots(page)
    if not 0 <= position < len(annots):
        raise PdfEditError("Diesen Link gibt es nicht mehr")
    annot = annots[position].get_object()
    if annot.get("/Subtype") != "/Link":
        raise PdfEditError("Das ist kein Link")
    return page, annot


def update_link(data: bytes, page_index: int, position: int, target: LinkTarget | None = None,
                border: bool | None = None, color: str | None = None) -> bytes:
    """Ziel und/oder Rahmen ändern; `target=None` lässt das Ziel (auch eine fremde Aktion) unverändert."""
    writer = A.open_writer(data)
    _page, annot = _link_annot(writer, page_index, position)
    if target is not None and target.kind != "other":
        _set_target(writer, annot, target)
    rect = [float(v) for v in annot["/Rect"]]
    current_color = A._hex(annot.get("/C", [])) or DEFAULT_COLOR
    _style(writer, annot, rect, _has_border(annot) if border is None else border, color or current_color)
    return A.finish(writer)


def set_link_rect(writer, page, annot, rect_view: A.Rect) -> None:
    """Rahmen eines Links setzen (für pdfobjects.set_rect); Ziel bleibt, Erscheinungsbild wird neu gezeichnet."""
    rect = A.geometry_of(page).rect_to_pdf(rect_view)
    annot[NameObject("/Rect")] = A._floats(rect)
    _style(writer, annot, rect, _has_border(annot), A._hex(annot.get("/C", [])) or DEFAULT_COLOR)


def delete_link(data: bytes, page_index: int, position: int) -> bytes:
    writer = A.open_writer(data)
    _link_annot(writer, page_index, position)
    return A.delete_annotation(data, page_index, position)
