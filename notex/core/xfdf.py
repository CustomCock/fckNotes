"""XFDF (XML-Austauschformat für PDF-Kommentare, ISO 19444-1) exportieren und importieren (Qt-frei).

Export: alle Kommentare samt Antworten und Status – Markierungen (`coords`), Notizen, Text auf der Seite, Freihand
(`inklist`), Linien/Pfeile, Rechtecke, Ellipsen, Polygone, Stempel. Ohne `/NM` bekommt ein Kommentar einen stabilen
Namen, damit Antworten (`inreplyto`) zugeordnet bleiben.

Import: XFDF ist XML von außen – gleiche Regeln wie beim draw.io-Import: keine eigenen DTD-Teile/ENTITY (kein XXE,
kein „Billion Laughs“), Größen- und Anzahl-Grenzen, Zahlen und Farben geprüft. Ein Kommentar, dessen Name im PDF
schon existiert, wird nicht verdoppelt, sondern aktualisiert (Text). Jeder neue Kommentar bekommt ein eigenes
Erscheinungsbild (wie alles, was fckNotes anlegt). Stempel werden als Text-Stempel neu gezeichnet; Datei-Anhänge,
Klänge und Schwärz-Vormerkungen werden nicht übernommen und im Ergebnis genannt.
"""
from __future__ import annotations

import math
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

from notex.core import pdfannot as A
from notex.core import pdfcomments as C
from notex.core import pdfdraw as D
from notex.core.pdfpages import PdfEditError, open_reader

NS = "http://ns.adobe.com/xfdf/"
MAX_FILE = 20 * 1024 * 1024
MAX_ANNOTS = 20000
MAX_POINTS = 20000
FLAGS = {"invisible": 1, "hidden": 2, "print": 4, "nozoom": 8, "norotate": 16, "noview": 32, "readonly": 64,
         "locked": 128, "togglenoview": 256}
ELEMENTS = {"/Text": "text", "/Highlight": "highlight", "/Underline": "underline", "/StrikeOut": "strikeout",
            "/Squiggly": "squiggly", "/FreeText": "freetext", "/Square": "square", "/Circle": "circle",
            "/Line": "line", "/Polygon": "polygon", "/PolyLine": "polyline", "/Ink": "ink", "/Stamp": "stamp",
            "/Caret": "caret", "/FileAttachment": "fileattachment", "/Sound": "sound", "/Redact": "redact"}
SUBTYPES = {v: k for k, v in ELEMENTS.items()}
NOT_IMPORTED = {"fileattachment": "Datei-Anhang", "sound": "Klang", "redact": "Schwärz-Vormerkung",
                "caret": "Einfügemarke", "link": "Link"}


class XfdfError(PdfEditError):
    pass


# ---- Export ----------------------------------------------------------------------------------------------------
def _num(value: float) -> str:
    return A._num(float(value))


def _pts(values) -> str:
    nums = [float(v) for v in values]
    return ",".join(_num(v) for v in nums)


def _pairs(values) -> str:
    nums = [float(v) for v in values]
    return ";".join(f"{_num(nums[i])},{_num(nums[i + 1])}" for i in range(0, len(nums) - 1, 2))


def _flags(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    return ",".join(name for name, bit in FLAGS.items() if number & bit)


def _hexcolor(value) -> str:
    color = A._hex(value or [])
    return color.upper() if color else ""


def export_xfdf(data: bytes, filename: str = "") -> bytes:
    """Alle Kommentare (ohne Links und Formularfelder) als XFDF-Datei (UTF-8)."""
    reader = open_reader(data)
    names: dict[int, str] = {}
    entries = []
    for page_index, page in enumerate(reader.pages):
        for position, ref in enumerate(page.get("/Annots", None) or []):
            try:
                annot = ref.get_object()
            except Exception:                             # noqa: BLE001
                continue
            element = ELEMENTS.get(str(annot.get("/Subtype", "")))
            if element is None or "/Rect" not in annot:
                continue
            name = str(annot.get("/NM", "") or "") or f"{A.ID_PREFIX}x{page_index}-{position}"
            names[getattr(ref, "idnum", id(annot))] = name
            entries.append((page_index, element, annot, name))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<xfdf xmlns="{NS}" xml:space="preserve">']
    if filename:
        out.append(f"<f href={quoteattr(filename)}/>")
    out.append("<annots>")
    for page_index, element, annot, name in entries:
        attrs = {"page": str(page_index), "rect": _pts(annot["/Rect"]), "name": name}
        for key, attr in (("/T", "title"), ("/Subj", "subject"), ("/M", "date"), ("/CreationDate", "creationdate")):
            if annot.get(key) is not None:
                attrs[attr] = str(annot[key])
        if _hexcolor(annot.get("/C")):
            attrs["color"] = _hexcolor(annot.get("/C"))
        if annot.get("/F") is not None and _flags(annot["/F"]):
            attrs["flags"] = _flags(annot["/F"])
        if annot.get("/CA") is not None and float(annot["/CA"]) < 1:
            attrs["opacity"] = _num(annot["/CA"])
        irt = annot.get("/IRT")
        if irt is not None:
            attrs["inreplyto"] = names.get(getattr(irt, "idnum", None), "")
            attrs["replyType"] = "group" if str(annot.get("/RT", "")) == "/Group" else "reply"
            if annot.get("/State") is not None:
                attrs["state"] = str(annot["/State"]).lstrip("/")
                attrs["statemodel"] = str(annot.get("/StateModel", "/Review")).lstrip("/")
        bs = annot.get("/BS")
        if bs is not None and element in ("square", "circle", "line", "polygon", "polyline", "ink", "freetext"):
            bs = bs.get_object()
            attrs["width"] = _num(bs.get("/W", 1))
            if str(bs.get("/S", "")) == "/D":
                attrs["style"] = "dash"
                attrs["dashes"] = _pts(bs.get("/D") or [3])
        if element in ("square", "circle", "polygon") and annot.get("/BE") is not None and \
                str(annot["/BE"].get_object().get("/S", "")) == "/C":
            attrs["style"] = "cloudy"
            attrs["intensity"] = _num(annot["/BE"].get_object().get("/I", 1))
        if annot.get("/IC") is not None and _hexcolor(annot["/IC"]):
            attrs["interior-color"] = _hexcolor(annot["/IC"])
        if annot.get("/RD") is not None:
            attrs["fringe"] = _pts(annot["/RD"])
        children = []
        if element in ("highlight", "underline", "strikeout", "squiggly") and annot.get("/QuadPoints") is not None:
            attrs["coords"] = _pts(annot["/QuadPoints"])
        elif element in ("text", "stamp") and annot.get("/Name") is not None:
            attrs["icon"] = str(annot["/Name"]).lstrip("/")
        elif element == "line" and annot.get("/L") is not None:
            line = [float(v) for v in annot["/L"]]
            attrs["start"], attrs["end"] = f"{_num(line[0])},{_num(line[1])}", f"{_num(line[2])},{_num(line[3])}"
            ends = [str(e).lstrip("/") for e in annot.get("/LE", [])] or ["None", "None"]
            attrs["head"], attrs["tail"] = ends[0], ends[-1]
        elif element in ("polygon", "polyline") and annot.get("/Vertices") is not None:
            children.append(f"<vertices>{_pairs(annot['/Vertices'])}</vertices>")
        elif element == "ink" and annot.get("/InkList") is not None:
            gestures = "".join(f"<gesture>{_pairs(s)}</gesture>" for s in annot["/InkList"])
            children.append(f"<inklist>{gestures}</inklist>")
        elif element == "freetext" and annot.get("/DA") is not None:
            children.append(f"<defaultappearance>{escape(str(annot['/DA']))}</defaultappearance>")
        if annot.get("/Contents") is not None:
            children.insert(0, f"<contents>{escape(str(annot['/Contents']))}</contents>")
        attr_text = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items())
        out.append(f"<{element} {attr_text}>{''.join(children)}</{element}>")
    out += ["</annots>", "</xfdf>", ""]
    return "\n".join(out).encode("utf-8")


# ---- Import ----------------------------------------------------------------------------------------------------
@dataclass
class ImportResult:
    data: bytes
    added: int = 0
    updated: int = 0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.added} Kommentare übernommen"]
        if self.updated:
            parts.append(f"{self.updated} aktualisiert")
        if self.skipped:
            parts.append(f"{len(self.skipped)} nicht übernommen ({', '.join(sorted(set(self.skipped)))})")
        return ", ".join(parts)


_PLAIN_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s[^\[\]<>]*>", re.I)


def parse(raw: bytes) -> ET.Element:
    """XFDF sicher lesen: Größe begrenzt, keine eigenen DTD-Teile/ENTITY."""
    if len(raw) > MAX_FILE:
        raise XfdfError("Die XFDF-Datei ist zu groß (höchstens 20 MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    text = _PLAIN_DOCTYPE_RE.sub("", text, count=1)
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise XfdfError("Die Datei definiert eigene XML-Entities – aus Sicherheitsgründen nicht gelesen")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise XfdfError(f"Keine gültige XFDF-Datei ({error})") from error
    if _local(root.tag) != "xfdf":
        raise XfdfError("Das ist keine XFDF-Datei (Wurzel ist nicht <xfdf>)")
    return root


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _floats(text: str | None, limit: int = MAX_POINTS) -> list[float]:
    if not text:
        return []
    values = []
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        value = float(part)
        if not math.isfinite(value) or abs(value) > 1e6:
            raise ValueError("Zahl außerhalb des Bereichs")
        values.append(value)
        if len(values) > limit:
            raise ValueError("zu viele Punkte")
    return values


def _color(value: str | None, default: str = "") -> str:
    value = (value or "").strip()
    return value.lower() if A.valid_color(value) else default


def _flag_bits(text: str | None) -> int:
    return sum(FLAGS.get(part.strip().lower(), 0) for part in (text or "").split(","))


def _contents(element: ET.Element) -> str:
    node = _child(element, "contents")
    if node is not None:
        return "".join(node.itertext())
    rich = _child(element, "contents-richtext")
    return re.sub(r"\s+", " ", "".join(rich.itertext())).strip() if rich is not None else ""


def _existing(writer) -> dict[str, tuple[object, object]]:
    found = {}
    for page in writer.pages:
        for ref in page.get("/Annots", None) or []:
            annot = ref.get_object()
            name = str(annot.get("/NM", "") or "")
            if name:
                found[name] = (ref, annot)
    return found


def _common(annot: DictionaryObject, element: ET.Element, name: str) -> None:
    annot[NameObject("/NM")] = TextStringObject(name)
    for attr, key in (("title", "/T"), ("subject", "/Subj")):
        if element.get(attr):
            annot[NameObject(key)] = TextStringObject(element.get(attr)[:500])
    for attr, key in (("date", "/M"), ("creationdate", "/CreationDate")):
        if element.get(attr) and C.parse_date(element.get(attr)):
            annot[NameObject(key)] = TextStringObject(element.get(attr)[:40])
    if element.get("flags") is not None:
        annot[NameObject("/F")] = NumberObject(_flag_bits(element.get("flags")))
    text = _contents(element)
    if text:
        annot[NameObject("/Contents")] = TextStringObject(text[:100000])
    elif "/Contents" in annot:
        del annot["/Contents"]


def _style(element: ET.Element, kind: str) -> D.Style:
    dash = "solid"
    if (element.get("style") or "").lower() == "dash":
        pattern = _floats(element.get("dashes"), 8) or [3]
        dash = "dotted" if pattern[0] == 0 else "dashed"
    opacity = float(element.get("opacity") or 1)
    width = float(element.get("width") or 1)
    fill = _color(element.get("interior-color")) or None
    return D.Style(_color(element.get("color"), "#d03030"), max(0.25, min(width, 50)), fill,
                   max(0.05, min(opacity, 1.0)), dash)


def _page_of(element: ET.Element, count: int) -> int:
    page = int(element.get("page", "-1"))
    if not 0 <= page < count:
        raise ValueError(f"Seite {page + 1} gibt es nicht")
    return page


def import_xfdf(data: bytes, raw: bytes) -> ImportResult:
    """Kommentare aus XFDF übernehmen; vorhandene (gleicher Name) werden aktualisiert statt verdoppelt."""
    root = parse(raw)
    annots_node = _child(root, "annots")
    elements = list(annots_node) if annots_node is not None else []
    if len(elements) > MAX_ANNOTS:
        raise XfdfError(f"Zu viele Kommentare (höchstens {MAX_ANNOTS})")
    writer = A.open_writer(data)
    count = len(writer.pages)
    known = _existing(writer)
    result = ImportResult(data)
    stamps: list[tuple[int, A.Rect, str, ET.Element, str]] = []
    children: list[ET.Element] = []
    for element in elements:
        kind = _local(element.tag)
        if kind in NOT_IMPORTED or kind not in SUBTYPES:
            result.skipped.append(NOT_IMPORTED.get(kind, kind))
            continue
        if element.get("inreplyto"):
            children.append(element)
            continue
        try:
            name = (element.get("name") or "").strip()[:200] or A.ID_PREFIX + uuid.uuid4().hex[:12]
            if name in known:
                _ref, annot = known[name]
                text = _contents(element)
                if text and str(annot.get("/Contents", "")) != text:
                    annot[NameObject("/Contents")] = TextStringObject(text[:100000])
                    result.updated += 1
                continue
            page = _page_of(element, count)
            if kind == "stamp":
                stamps.append((page, tuple(_floats(element.get("rect"), 4)), name, element, kind))
                continue
            ref = _create(writer, page, kind, element, name)
            if ref is not None:
                known[name] = (ref, ref.get_object())
                result.added += 1
        except (ValueError, TypeError, KeyError, PdfEditError) as error:
            result.skipped.append(f"{kind} ({error})")
    for element in children:                              # Antworten und Status nach ihren Kommentaren
        try:
            parent = known.get(element.get("inreplyto", ""))
            name = (element.get("name") or "").strip()[:200] or A.ID_PREFIX + uuid.uuid4().hex[:12]
            if parent is None:
                result.skipped.append("Antwort ohne Kommentar")
                continue
            if name in known:
                continue
            page = _page_of(element, count)
            ref = _create_child(writer, page, parent, element, name)
            known[name] = (ref, ref.get_object())
            result.added += 1
        except (ValueError, TypeError, PdfEditError) as error:
            result.skipped.append(f"Antwort ({error})")
    data = A.finish(writer)
    for page, rect, name, element, _kind in stamps:       # Stempel über pdfstamps (eigene Schrift, Form-Matrix)
        try:
            data = _add_stamp(data, page, rect, name, element)
            result.added += 1
        except (ValueError, PdfEditError) as error:
            result.skipped.append(f"Stempel ({error})")
    result.data = data
    return result


def _rect(element: ET.Element) -> list[float]:
    values = _floats(element.get("rect"), 4)
    if len(values) != 4:
        raise ValueError("Rechteck fehlt")
    x0, y0, x1, y1 = values
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _create(writer, page: int, kind: str, element: ET.Element, name: str):
    subtype = SUBTYPES[kind]
    color = _color(element.get("color"), A.DEFAULT_COLORS.get(
        {"highlight": "highlight", "underline": "underline", "strikeout": "strikeout", "squiggly": "underline",
         "text": "note"}.get(kind, ""), "#d03030"))
    rect = _rect(element)
    if kind == "freetext":
        da = _child(element, "defaultappearance")
        size, text_color = A._parse_da("".join(da.itertext()) if da is not None else "")
        geom = A.geometry_of(A._page(writer, page))
        A._add_text(writer, page, geom.rect_to_view(rect), _contents(element) or " ", size, text_color,
                    float(element.get("width") or 0) > 0, False)
        ref = A._annots(A._page(writer, page))[-1]
        _common(ref.get_object(), element, name)
        return ref
    annot = A._base(subtype, rect, color)
    _common(annot, element, name)
    if kind in ("highlight", "underline", "strikeout", "squiggly"):
        coords = _floats(element.get("coords"))
        if len(coords) < 8:
            raise ValueError("Markierung ohne Koordinaten")
        quads = [[(coords[i], coords[i + 1]), (coords[i + 2], coords[i + 3]), (coords[i + 4], coords[i + 5]),
                  (coords[i + 6], coords[i + 7])] for i in range(0, len(coords) - 7, 8)]
        annot[NameObject("/QuadPoints")] = A._floats(coords[:len(quads) * 8])
        resources = DictionaryObject()
        if kind == "highlight":
            state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                                      NameObject("/BM"): NameObject("/Multiply")})
            resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS0"): state})
        draw_kind = "underline" if kind == "squiggly" else kind
        appearance = A._form(writer, A.markup_content(draw_kind, quads, color), rect, resources)
        A._attach(writer, page, annot, appearance)
    elif kind == "text":
        annot[NameObject("/Name")] = NameObject("/" + re.sub(r"[^A-Za-z0-9]", "", element.get("icon") or "Comment"))
        box = [rect[0], rect[3] - A.NOTE_SIZE, rect[0] + A.NOTE_SIZE, rect[3]]
        annot[NameObject("/Rect")] = A._floats(box)
        A._attach(writer, page, annot, A._form(writer, A.note_content(box, color), box))
    else:
        style = _style(element, kind)
        if kind in ("square", "circle"):
            fringe = _floats(element.get("fringe"), 4)
            if len(fringe) == 4:
                annot[NameObject("/RD")] = A._floats(fringe)
        if kind in ("square", "circle", "polygon") and (element.get("style") or "").lower() == "cloudy":
            annot[NameObject("/BE")] = DictionaryObject({NameObject("/S"): NameObject("/C"),
                                                         NameObject("/I"): NumberObject(1)})
        if kind == "line":
            start, end = _floats(element.get("start"), 2), _floats(element.get("end"), 2)
            if len(start) != 2 or len(end) != 2:
                raise ValueError("Linie ohne Anfang/Ende")
            annot[NameObject("/L")] = A._floats([*start, *end])
            head = re.sub(r"[^A-Za-z]", "", element.get("head") or "None") or "None"
            tail = re.sub(r"[^A-Za-z]", "", element.get("tail") or "None") or "None"
            annot[NameObject("/LE")] = ArrayObject([NameObject("/" + head), NameObject("/" + tail)])
        elif kind in ("polygon", "polyline"):
            node = _child(element, "vertices")
            points = _floats("".join(node.itertext()) if node is not None else "")
            if len(points) < 4:
                raise ValueError("Polygon ohne Ecken")
            annot[NameObject("/Vertices")] = A._floats(points[:len(points) // 2 * 2])
        elif kind == "ink":
            node = _child(element, "inklist")
            strokes = []
            for gesture in (node if node is not None else []):
                points = _floats("".join(gesture.itertext()))
                if len(points) >= 2:
                    strokes.append(A._floats(points[:len(points) // 2 * 2]))
            if not strokes:
                raise ValueError("Freihand ohne Striche")
            annot[NameObject("/InkList")] = ArrayObject(strokes)
        D.redraw(writer, annot, style)
        if style.opacity < 1:
            annot[NameObject("/CA")] = FloatObject(round(style.opacity, 3))
        A._attach(writer, page, annot)
    return A._annots(A._page(writer, page))[-1]


def _create_child(writer, page: int, parent, element: ET.Element, name: str):
    parent_ref, parent_annot = parent
    child = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): A._floats([float(v) for v in parent_annot["/Rect"]]),
        NameObject("/IRT"): parent_ref, NameObject("/P"): A._page(writer, page).indirect_reference,
    })
    _common(child, element, name)
    state = re.sub(r"[^A-Za-z]", "", element.get("state") or "")
    if state:
        child[NameObject("/State")] = NameObject("/" + state)
        model = re.sub(r"[^A-Za-z]", "", element.get("statemodel") or "Review") or "Review"
        child[NameObject("/StateModel")] = NameObject("/" + model)
        child.setdefault(NameObject("/F"), NumberObject(C.FLAG_HIDDEN | C.FLAG_NOVIEW))
    else:
        child[NameObject("/RT")] = NameObject("/Group" if element.get("replyType") == "group" else "/R")
        child.setdefault(NameObject("/F"), NumberObject(C.FLAG_NOVIEW | C.FLAG_NOZOOM | C.FLAG_NOROTATE))
        child[NameObject("/Name")] = NameObject("/Comment")
    ref = writer._add_object(child)
    A._annots(A._page(writer, page)).append(ref)
    return ref


STAMP_ICONS = {"approved": "approved", "draft": "draft", "confidential": "confidential", "final": "done",
               "fcknoteschecked": "checked"}


def _add_stamp(data: bytes, page: int, rect: tuple, name: str, element: ET.Element) -> bytes:
    from notex.core import pdfstamps as S
    if len(rect) != 4:
        raise ValueError("Rechteck fehlt")
    icon = (element.get("icon") or "").lower()
    if icon in STAMP_ICONS:
        spec = S.builtin(STAMP_ICONS[icon])
    else:
        label = (element.get("subject") or _contents(element) or element.get("icon") or "STEMPEL").strip()[:40]
        spec = S.StampSpec("custom-import", label.upper(), _color(element.get("color"), "#c02020"))
    geom = A.page_geometry(data, page)
    x0, y0, x1, y1 = rect
    view = geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    out = S.add_stamp(data, page, view, spec)
    writer = A.open_writer(out)
    annot = A._annots(A._page(writer, page))[-1].get_object()
    _common(annot, element, name)
    if not _contents(element):
        annot[NameObject("/Contents")] = TextStringObject(spec.text)
    return A.finish(writer)
