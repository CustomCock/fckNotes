"""„Steht das Wort noch irgendwo?“ – die Prüfung nach dem Schwärzen (Qt-frei).

Ein Wort gilt erst als weg, wenn es an **keinem** Ort mehr steht, an dem ein PDF Text ablegen kann. Diese Orte stehen
im Register `LOCATIONS` (Schlüssel → Beschriftung). Jedes Feature, das einen neuen Ablageort für Text schafft
(z. B. Anhänge, Ebenen, Kommentar-Antworten), trägt ihn hier ein – `tests/test_pdfaudit.py` prüft, dass jeder Ort
eine eigene Fixture hat.

Gesucht wird ohne Groß/Klein und mit zusammengefassten Leerräumen. Erst die bestimmten Orte (Seite 3, Lesezeichen …);
nur wenn ein Wort dort nirgends auftaucht, sucht die **Restsuche** in allen Objekten, allen dekomprimierten Streams
und in den Rohbytes der Datei – dort stecken auch frühere Speicherstände (inkrementelle Updates), die ein normales
Lesen nicht mehr zeigt.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from typing import Callable, Iterable

from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from notex.core.pdfpages import open_reader

# Register der Textablageorte (Regel 8). Reihenfolge = Reihenfolge im Bericht.
LOCATIONS: dict[str, str] = {
    "page_text": "Seitentext",
    "appearance": "Erscheinungsbilder von Anmerkungen und Feldern",
    "annotation": "Anmerkungen (Text, Autor, Betreff, Antworten)",
    "link": "Link-Ziele (Webadressen, Dateinamen)",
    "form": "Formularfelder (Wert, Standardwert, Optionen, Tooltip, Name)",
    "outline": "Lesezeichen",
    "attachment": "Anhänge (Name, Beschreibung, Inhalt)",
    "layer": "Ebenen-Namen",
    "metadata": "Metadaten (Info)",
    "xmp": "XMP-Metadaten",
    "structure": "Struktur-Tags (Alt-Text, ActualText, Titel)",
    "names": "Benannte Ziele und Seitenbeschriftungen",
    "javascript": "Skripte",
    "objects": "Restsuche: alle Objekte und dekomprimierten Streams",
    "raw": "Restsuche: Rohbytes inkl. früherer Speicherstände",
}
GENERIC = ("objects", "raw")
MAX_RAW_STREAM = 64 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    needle: str
    location: str           # Schlüssel aus LOCATIONS
    where: str              # lesbar: „auf Seite 3“

    def text(self) -> str:
        return f"„{self.needle}“ {self.where}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).casefold()


def _str(value) -> str:
    try:
        value = value.get_object() if isinstance(value, IndirectObject) else value
    except Exception:                                     # noqa: BLE001
        return ""
    if isinstance(value, (list, ArrayObject)):
        return " ".join(_str(v) for v in value)
    return "" if value is None else str(value)


def _decode_bytes(data: bytes) -> str:
    parts = [data.decode("latin-1", "ignore")]
    try:
        parts.append(data.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    if data[:2] in (b"\xfe\xff", b"\xff\xfe") or b"\x00" in data[:200]:
        for codec in ("utf-16-be", "utf-16-le"):
            parts.append(data.decode(codec, "ignore"))
    return "\n".join(parts)


# ---- bestimmte Orte --------------------------------------------------------------------------------------------
def _page_text(reader, data: bytes) -> Iterable[tuple[str, str]]:
    for index, page in enumerate(reader.pages):
        try:
            yield page.extract_text() or "", f"auf Seite {index + 1}"
        except Exception:                                 # noqa: BLE001 – unlesbar zählt nicht als „sauber“
            yield "\x00UNREADABLE", f"Seite {index + 1} ließ sich nicht prüfen"


def _appearance(reader, data: bytes) -> Iterable[tuple[str, str]]:
    """Erscheinungsbilder (Freitext, Stempel, Feldwerte) über PDFium: Seite „einbrennen“ und Text lesen."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:                                   # pragma: no cover – ohne pypdfium2 bleibt die Restsuche
        return
    try:
        doc = pdfium.PdfDocument(data)
    except Exception:                                     # noqa: BLE001
        return
    try:
        for index in range(len(doc)):
            page = doc[index]
            if raw.FPDFPage_Flatten(page.raw, raw.FLAT_NORMALDISPLAY) == raw.FLATTEN_FAIL:
                continue
            page.close()
            page = doc[index]
            textpage = page.get_textpage()
            yield textpage.get_text_range(), f"in einem Erscheinungsbild auf Seite {index + 1}"
            textpage.close()
            page.close()
    finally:
        doc.close()


def _annotation(reader, data: bytes) -> Iterable[tuple[str, str]]:
    for index, page in enumerate(reader.pages):
        for ref in page.get("/Annots", None) or []:
            try:
                annot = ref.get_object()
            except Exception:                             # noqa: BLE001
                continue
            if annot.get("/Subtype") == "/Widget":
                continue
            texts = [_str(annot.get(key)) for key in ("/Contents", "/RC", "/T", "/Subj", "/TU")]
            yield " ".join(texts), f"in einer Anmerkung auf Seite {index + 1}"


def _link(reader, data: bytes) -> Iterable[tuple[str, str]]:
    for index, page in enumerate(reader.pages):
        for ref in page.get("/Annots", None) or []:
            try:
                annot = ref.get_object()
                if annot.get("/Subtype") != "/Link":
                    continue
                action = annot.get("/A")
                action = action.get_object() if action is not None else {}
                texts = [_str(action.get("/URI")), _str(annot.get("/Dest")), _str(action.get("/D"))]
                spec = action.get("/F")
                if spec is not None:
                    spec = spec.get_object()
                    texts += [_str(spec.get("/UF")), _str(spec.get("/F"))] if hasattr(spec, "get") else [_str(spec)]
            except Exception:                             # noqa: BLE001
                yield "\x00UNREADABLE", f"Ein Link auf Seite {index + 1} ließ sich nicht prüfen"
                continue
            yield " ".join(texts), f"in einem Link auf Seite {index + 1}"


def _fields(reader) -> Iterable[DictionaryObject]:
    acro = reader.trailer["/Root"].get("/AcroForm")
    if acro is None:
        return
    stack = list(acro.get_object().get("/Fields", []) or [])
    seen: set[int] = set()
    while stack:
        ref = stack.pop()
        try:
            field = ref.get_object()
        except Exception:                                 # noqa: BLE001
            continue
        if id(field) in seen:
            continue
        seen.add(id(field))
        yield field
        stack.extend(field.get("/Kids", []) or [])


def _form(reader, data: bytes) -> Iterable[tuple[str, str]]:
    for field in _fields(reader):
        name = _str(field.get("/T"))
        texts = [_str(field.get(key)) for key in ("/V", "/DV", "/Opt", "/TU", "/T", "/TM")]
        yield " ".join(texts), f"im Formularfeld „{name}“" if name else "in einem Formularfeld"


def _outline(reader, data: bytes) -> Iterable[tuple[str, str]]:
    def walk(items):
        for item in items:
            if isinstance(item, list):
                yield from walk(item)
            else:
                yield str(getattr(item, "title", "") or "")
    try:
        yield " ".join(walk(reader.outline)), "in den Lesezeichen"
    except Exception:                                     # noqa: BLE001
        return


def _name_tree(node) -> Iterable[tuple[str, object]]:
    """Alle (Name, Wert) eines PDF-Namensbaums."""
    stack = [node]
    while stack:
        current = stack.pop()
        try:
            current = current.get_object()
        except Exception:                                 # noqa: BLE001
            continue
        names = current.get("/Names", []) or []
        for i in range(0, len(names) - 1, 2):
            yield _str(names[i]), names[i + 1]
        stack.extend(current.get("/Kids", []) or [])


def _attachment(reader, data: bytes) -> Iterable[tuple[str, str]]:
    specs = []
    names = reader.trailer["/Root"].get("/Names")
    if names is not None and names.get_object().get("/EmbeddedFiles") is not None:
        specs += [(name, value) for name, value in _name_tree(names.get_object()["/EmbeddedFiles"])]
    for index, page in enumerate(reader.pages):
        for ref in page.get("/Annots", None) or []:
            try:
                annot = ref.get_object()
                if annot.get("/Subtype") == "/FileAttachment" and annot.get("/FS") is not None:
                    specs.append((f"Seite {index + 1}", annot["/FS"]))
            except Exception:                             # noqa: BLE001
                continue
    for name, spec in specs:
        try:
            spec = spec.get_object()
            label = _str(spec.get("/UF")) or _str(spec.get("/F")) or name
            texts = [name, label, _str(spec.get("/Desc"))]
            embedded = (spec.get("/EF") or {}).get("/F") if hasattr(spec, "get") else None
            if embedded is not None:
                content = embedded.get_object().get_data()
                if len(content) <= MAX_RAW_STREAM:
                    texts.append(_decode_bytes(content))
            yield " ".join(texts), f"im Anhang „{label}“"
        except Exception:                                 # noqa: BLE001
            yield "\x00UNREADABLE", f"Anhang „{name}“ ließ sich nicht prüfen"


def _layer(reader, data: bytes) -> Iterable[tuple[str, str]]:
    props = reader.trailer["/Root"].get("/OCProperties")
    if props is None:
        return
    for ref in props.get_object().get("/OCGs", []) or []:
        try:
            yield _str(ref.get_object().get("/Name")), "in einem Ebenen-Namen"
        except Exception:                                 # noqa: BLE001
            continue


def _metadata(reader, data: bytes) -> Iterable[tuple[str, str]]:
    meta = reader.metadata or {}
    yield " ".join(str(v) for v in meta.values()), "in den Metadaten"


def _xmp(reader, data: bytes) -> Iterable[tuple[str, str]]:
    xmp = reader.trailer["/Root"].get("/Metadata")
    if xmp is not None:
        try:
            yield xmp.get_object().get_data().decode("utf-8", "replace"), "in den XMP-Metadaten"
        except Exception:                                 # noqa: BLE001
            return


def _structure(reader, data: bytes) -> Iterable[tuple[str, str]]:
    root = reader.trailer["/Root"].get("/StructTreeRoot")
    if root is None:
        return
    stack, seen, texts = [root], set(), []
    while stack and len(seen) < 200000:
        node = stack.pop()
        if isinstance(node, IndirectObject):
            if node.idnum in seen:
                continue
            seen.add(node.idnum)
        try:
            node = node.get_object()
        except Exception:                                 # noqa: BLE001
            continue
        if isinstance(node, DictionaryObject):
            texts += [_str(node.get(key)) for key in ("/Alt", "/ActualText", "/E", "/T")]
            kids = node.get("/K")
            if kids is not None:
                stack.extend(kids if isinstance(kids, (list, ArrayObject)) else [kids])
        elif isinstance(node, (list, ArrayObject)):
            stack.extend(node)
    yield " ".join(texts), "in den Struktur-Tags (Alt-Text/Titel)"


def _names(reader, data: bytes) -> Iterable[tuple[str, str]]:
    root = reader.trailer["/Root"]
    texts = []
    names = root.get("/Names")
    if names is not None and names.get_object().get("/Dests") is not None:
        texts += [name for name, _v in _name_tree(names.get_object()["/Dests"])]
    dests = root.get("/Dests")
    if dests is not None:
        texts += [str(k) for k in dests.get_object().keys()]
    labels = root.get("/PageLabels")
    if labels is not None:
        nums = labels.get_object().get("/Nums", []) or []
        for i in range(1, len(nums), 2):
            try:
                texts.append(_str(nums[i].get_object().get("/P")))
            except Exception:                             # noqa: BLE001
                continue
    yield " ".join(texts), "in benannten Zielen/Seitenbeschriftungen"


def _javascript(reader, data: bytes) -> Iterable[tuple[str, str]]:
    root = reader.trailer["/Root"]
    texts = []
    names = root.get("/Names")
    if names is not None and names.get_object().get("/JavaScript") is not None:
        for name, action in _name_tree(names.get_object()["/JavaScript"]):
            texts.append(name)
            try:
                js = action.get_object().get("/JS")
                js = js.get_object() if js is not None else None
                texts.append(js.get_data().decode("latin-1", "ignore") if isinstance(js, StreamObject) else _str(js))
            except Exception:                             # noqa: BLE001
                continue
    yield " ".join(texts), "in einem Skript"


# ---- Restsuche -------------------------------------------------------------------------------------------------
def _objects(reader, data: bytes) -> Iterable[tuple[str, str]]:
    """Alle Strings aller erreichbaren und nicht erreichbaren Objekte der aktuellen Fassung, dazu alle Streams."""
    texts = []
    ids: set[tuple[int, int]] = set()
    for generation, table in (getattr(reader, "xref", {}) or {}).items():
        ids.update((idnum, generation) for idnum in table)
    ids.update((idnum, 0) for idnum in (getattr(reader, "xref_objStm", {}) or {}))
    for idnum, generation in sorted(ids):
        try:
            obj = IndirectObject(idnum, generation, reader).get_object()
        except Exception:                                 # noqa: BLE001
            continue
        if obj is None:
            continue
        texts.append(_flatten_strings(obj))
        if isinstance(obj, StreamObject):
            try:
                content = obj.get_data()
                if len(content) <= MAX_RAW_STREAM:
                    texts.append(_decode_bytes(content))
            except Exception:                             # noqa: BLE001
                continue
    yield "\n".join(texts), "irgendwo in der Datei (Objekte/Streams)"


def _flatten_strings(obj, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(obj, DictionaryObject):
        return " ".join(_flatten_strings(v, depth + 1) for k, v in obj.items() if not isinstance(v, IndirectObject))
    if isinstance(obj, (list, ArrayObject)):
        return " ".join(_flatten_strings(v, depth + 1) for v in obj if not isinstance(v, IndirectObject))
    if isinstance(obj, (str, bytes)):
        return obj.decode("latin-1", "ignore") if isinstance(obj, bytes) else str(obj)
    return ""


_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)


def _raw(reader, data: bytes) -> Iterable[tuple[str, str]]:
    """Rohbytes und jeder Flate-Stream darin – auch aus früheren Speicherständen, die pypdf nicht mehr liest."""
    texts = [_decode_bytes(data)]
    total = 0
    for match in _STREAM_RE.finditer(data):
        chunk = match.group(1)
        try:
            inflated = zlib.decompressobj().decompress(chunk, MAX_RAW_STREAM)
        except zlib.error:
            continue
        total += len(inflated)
        if total > 8 * MAX_RAW_STREAM:
            break
        texts.append(_decode_bytes(inflated))
    yield "\n".join(texts), "in den Rohdaten der Datei (auch frühere Speicherstände)"


SCANNERS: dict[str, Callable] = {
    "page_text": _page_text, "appearance": _appearance, "annotation": _annotation, "link": _link, "form": _form,
    "outline": _outline, "attachment": _attachment, "layer": _layer, "metadata": _metadata, "xmp": _xmp,
    "structure": _structure, "names": _names, "javascript": _javascript, "objects": _objects, "raw": _raw,
}
assert set(SCANNERS) == set(LOCATIONS), "Jeder Ablageort braucht einen Scanner"


def audit(data: bytes, needles: list[str], cancel: Callable[[], bool] | None = None) -> list[Finding]:
    """Alle Fundstellen der Wörter. Leer = nirgends gefunden."""
    needles = [n.strip() for n in dict.fromkeys(needles or []) if n and n.strip()]
    if not needles:
        return []
    wanted = [(n, _norm(n)) for n in needles]
    reader = open_reader(data)
    findings: list[Finding] = []
    found_specific: set[str] = set()
    seen_where: set[tuple[str, str]] = set()
    for key in LOCATIONS:
        if cancel and cancel():
            break
        if key in GENERIC and all(n in found_specific for n, _x in wanted):
            continue
        try:
            items = list(SCANNERS[key](reader, data))
        except Exception:                                 # noqa: BLE001 – ein kaputter Ort darf nicht „sauber“ heißen
            items = [("\x00UNREADABLE", f"{LOCATIONS[key]} ließen sich nicht prüfen")]
        for text, where in items:
            if text == "\x00UNREADABLE":
                findings.append(Finding("?", key, where))
                continue
            haystack = _norm(text)
            for original, needle in wanted:
                if key in GENERIC and original in found_specific:
                    continue
                if needle in haystack and (original, where) not in seen_where:
                    seen_where.add((original, where))
                    if key not in GENERIC:
                        found_specific.add(original)
                    if key == "appearance" and any(f.needle == original and f.location == "page_text"
                                                   and _page_of(f.where) == _page_of(where) for f in findings):
                        continue                          # Seitentext steht schon da – nicht doppelt melden
                    findings.append(Finding(original, key, where))
    objects = {f.needle for f in findings if f.location == "objects"}   # Rohsuche nur, wo die Objektsuche nichts fand
    return [f for f in findings if not (f.location == "raw" and f.needle in objects)]


def _page_of(where: str) -> int | None:
    match = re.search(r"Seite (\d+)$", where)
    return int(match.group(1)) if match else None


def report(data: bytes, needles: list[str]) -> list[str]:
    """Lesbare Fundstellen („„Meier“ auf Seite 3“)."""
    out = []
    for finding in audit(data, needles):
        out.append(finding.where if finding.needle == "?" else finding.text())
    return list(dict.fromkeys(out))
