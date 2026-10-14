"""Formulardaten austauschen (Qt-frei, Block V5): JSON, XFDF und FDF exportieren/importieren, Antworten sammeln.

- JSON: `{"format": "fckNotes-Formular", "version": 1, "document": "...", "fields": {"Name": "Erika", "OK": true}}`
  – Kontrollkästchen als true/false, alles andere als Text. Beim Import genügt auch ein flaches `{"Name": …}`.
- XFDF: `<fields><field name="…"><value>…</value></field></fields>`; Namen mit Punkt als verschachtelte `<field>`.
  Gelesen wird über `xfdf.parse` (kein DOCTYPE/ENTITY, Größengrenze).
- FDF: PDF-Syntax (`/FDF << /Fields [ << /T (Name) /V (Wert) >> … ] >>`). Gelesen wird mit einem eigenen, kleinen
  Leser, der nur Daten kennt (Text, Namen, Zahlen, Felder, Listen) – keine Streams, keine Aktionen, keine Verweise –
  mit Grenzen für Größe, Tiefe und Anzahl.
- Antworten sammeln: aus mehreren ausgefüllten Kopien eines Formulars eine CSV-Tabelle (eine Zeile je Datei).

Import füllt über `pdfforms.fill`: unbekannte und schreibgeschützte Felder sowie ungültige Werte werden übersprungen
und im Ergebnis genannt; ein Rückgängig-Schritt in der Oberfläche.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

from notex.core import pdfforms as F
from notex.core import xfdf as X
from notex.core.pdfcomments import csv_cell
from notex.core.pdfpages import PdfEditError

FORMAT = "fckNotes-Formular"
MAX_FILE = 10 * 1024 * 1024
MAX_FIELDS = 20000
MAX_DEPTH = 32
DATA_KINDS = ("text", "choice", "checkbox", "radio")


class FormDataError(PdfEditError):
    pass


@dataclass
class FillResult:
    data: bytes
    filled: int = 0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"{self.filled} Felder übernommen"
        if self.skipped:
            shown = ", ".join(self.skipped[:5]) + (" …" if len(self.skipped) > 5 else "")
            text += f", {len(self.skipped)} übersprungen ({shown})"
        return text


# ---- Werte lesen/setzen ------------------------------------------------------------------------------------------
def values_of(data: bytes) -> dict[str, object]:
    """Feldwerte: Kontrollkästchen → bool, Rest → Text (Schaltflächen und Signaturfelder ohne Wert fehlen)."""
    out: dict[str, object] = {}
    for info in F.list_fields(data):
        if info.kind == "checkbox":
            out[info.name] = info.checked
        elif info.kind in DATA_KINDS:
            out[info.name] = info.value
    return out


def apply_values(data: bytes, values: dict[str, object]) -> FillResult:
    """Werte übernehmen; Unbekanntes, Schreibgeschütztes und Ungültiges wird übersprungen und genannt."""
    infos = {f.name: f for f in F.list_fields(data)}
    result = FillResult(data)
    accepted: dict[str, object] = {}
    for name, value in values.items():
        info = infos.get(name)
        if info is None or info.kind not in DATA_KINDS:
            result.skipped.append(f"{name}: kein Feld")
            continue
        if info.read_only:
            result.skipped.append(f"{name}: schreibgeschützt")
            continue
        if info.kind == "checkbox":
            value = value if isinstance(value, bool) else str(value).strip().lower() not in (
                "", "off", "false", "0", "nein", "no", "aus")
        else:
            value = "" if value is None else str(value)
            if value in ("Off",) and info.kind == "radio":
                value = ""
        if info.kind == "radio" and value and value not in info.options:
            result.skipped.append(f"{name}: „{value}“ ist keine Option")
            continue
        if info.kind == "choice" and value and info.options and value not in info.options and not info.editable:
            result.skipped.append(f"{name}: „{value}“ ist kein Eintrag")
            continue
        accepted[name] = value
    if accepted:
        result.data = F.fill(data, accepted)
        result.filled = len(accepted)
    return result


# ---- JSON ------------------------------------------------------------------------------------------------------
def export_json(data: bytes, document: str = "") -> bytes:
    payload = {"format": FORMAT, "version": 1, "document": document, "fields": values_of(data)}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def parse_json(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_FILE:
        raise FormDataError("Die Datei ist zu groß")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise FormDataError(f"Keine gültige JSON-Datei ({error})") from error
    if isinstance(payload, dict) and isinstance(payload.get("fields"), dict):
        payload = payload["fields"]
    if not isinstance(payload, dict):
        raise FormDataError("Erwartet: {\"Feldname\": Wert, …}")
    out = {}
    for name, value in list(payload.items())[:MAX_FIELDS]:
        if isinstance(value, (str, bool, int, float)) or value is None:
            out[str(name)] = value
    return out


# ---- XFDF ------------------------------------------------------------------------------------------------------
def export_xfdf_fields(data: bytes, document: str = "") -> bytes:
    tree: dict = {}
    for info in F.list_fields(data):
        if info.kind not in DATA_KINDS:
            continue
        if info.kind == "checkbox":
            value = (info.on_state or "Yes") if info.checked else "Off"
        elif info.kind == "radio":
            value = info.value or "Off"
        else:
            value = info.value
        node = tree
        parts = info.name.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def render(node: dict, indent: str) -> list[str]:
        lines = []
        for name, value in node.items():
            if isinstance(value, dict):
                lines.append(f"{indent}<field name={quoteattr(name)}>")
                lines += render(value, indent + "  ")
                lines.append(f"{indent}</field>")
            else:
                lines.append(f"{indent}<field name={quoteattr(name)}><value>{escape(str(value))}</value></field>")
        return lines
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<xfdf xmlns="{X.NS}" xml:space="preserve">']
    if document:
        out.append(f"  <f href={quoteattr(document)}/>")
    out += ["  <fields>", *render(tree, "    "), "  </fields>", "</xfdf>", ""]
    return "\n".join(out).encode("utf-8")


def parse_xfdf_fields(raw: bytes) -> dict[str, object]:
    root = X.parse(raw)
    fields = X._child(root, "fields")
    out: dict[str, object] = {}
    if fields is None:
        return out

    def walk(node: ET.Element, prefix: str, depth: int) -> None:
        if depth > MAX_DEPTH or len(out) >= MAX_FIELDS:
            return
        for child in node:
            if X._local(child.tag) != "field":
                continue
            name = (prefix + "." if prefix else "") + (child.get("name") or "")
            value = X._child(child, "value")
            if value is not None:
                out[name] = "".join(value.itertext())
            walk(child, name, depth + 1)
    walk(fields, "", 0)
    return out


# ---- FDF -------------------------------------------------------------------------------------------------------
def export_fdf(data: bytes, document: str = "") -> bytes:
    entries = ArrayObject()
    for info in F.list_fields(data):
        if info.kind not in DATA_KINDS:
            continue
        if info.kind == "checkbox":
            value = NameObject("/" + (info.on_state or "Yes")) if info.checked else NameObject("/Off")
        elif info.kind == "radio":
            value = NameObject("/" + info.value) if info.value else NameObject("/Off")
        else:
            value = TextStringObject(info.value)
        entries.append(DictionaryObject({NameObject("/T"): TextStringObject(info.name), NameObject("/V"): value}))
    fdf = DictionaryObject({NameObject("/Fields"): entries})
    if document:
        fdf[NameObject("/F")] = TextStringObject(document)
    body = io.BytesIO()
    DictionaryObject({NameObject("/FDF"): fdf}).write_to_stream(body)
    return (b"%FDF-1.2\n%\xe2\xe3\xcf\xd3\n1 0 obj\n" + body.getvalue() + b"\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")


class _Reader:
    """Kleiner, strenger Leser für PDF-Daten-Objekte (nur was FDF-Felder brauchen)."""
    DELIM = b"()<>[]{}/%"
    WHITE = b" \t\r\n\x0c\x00"

    def __init__(self, raw: bytes) -> None:
        self.raw, self.pos, self.count = raw, 0, 0

    def skip(self) -> None:
        raw = self.raw
        while self.pos < len(raw):
            c = raw[self.pos:self.pos + 1]
            if c in (b"",) or c not in self.WHITE and c != b"%":
                return
            if c == b"%":
                end = raw.find(b"\n", self.pos)
                self.pos = len(raw) if end < 0 else end + 1
            else:
                self.pos += 1

    def read(self, depth: int = 0):
        if depth > MAX_DEPTH:
            raise FormDataError("FDF zu tief verschachtelt")
        self.count += 1
        if self.count > MAX_FIELDS * 20:
            raise FormDataError("FDF hat zu viele Einträge")
        self.skip()
        raw, pos = self.raw, self.pos
        if raw.startswith(b"<<", pos):
            self.pos += 2
            out = {}
            while True:
                self.skip()
                if raw.startswith(b">>", self.pos):
                    self.pos += 2
                    return out
                key = self.read(depth + 1)
                if not isinstance(key, _Name):
                    raise FormDataError("FDF: Schlüssel erwartet")
                out[str(key)] = self.read(depth + 1)
        if raw.startswith(b"[", pos):
            self.pos += 1
            items = []
            while True:
                self.skip()
                if raw.startswith(b"]", self.pos):
                    self.pos += 1
                    return items
                items.append(self.read(depth + 1))
        if raw.startswith(b"(", pos):
            return self._literal()
        if raw.startswith(b"<", pos):
            end = raw.find(b">", pos)
            if end < 0:
                raise FormDataError("FDF: Hex-Text ohne Ende")
            hexdigits = re.sub(rb"\s", b"", raw[pos + 1:end])
            self.pos = end + 1
            try:
                return _decode(bytes.fromhex(hexdigits.decode("ascii") + ("0" if len(hexdigits) % 2 else "")))
            except ValueError as error:
                raise FormDataError("FDF: ungültiger Hex-Text") from error
        if raw.startswith(b"/", pos):
            end = pos + 1
            while end < len(raw) and raw[end:end + 1] not in self.WHITE and raw[end:end + 1] not in self.DELIM:
                end += 1
            self.pos = end
            name = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw[pos + 1:end])
            return _Name("/" + name.decode("utf-8", "replace"))
        match = re.match(rb"[+-]?\d*\.?\d+", raw[pos:pos + 40])
        if match:
            self.pos += len(match.group(0))
            ref = re.match(rb"\s+\d+\s+R\b", raw[self.pos:self.pos + 20])
            if ref:                                        # indirekter Verweis: in FDF-Feldern nicht unterstützt
                self.pos += len(ref.group(0))
                return None
            text = match.group(0).decode()
            return float(text) if "." in text else int(text)
        for word, value in ((b"true", True), (b"false", False), (b"null", None)):
            if raw.startswith(word, pos):
                self.pos += len(word)
                return value
        raise FormDataError(f"FDF: unerwartetes Zeichen bei Byte {pos}")

    def _literal(self) -> str:
        raw = self.raw
        self.pos += 1
        depth, out = 1, bytearray()
        escapes = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f",
                   ord("("): b"(", ord(")"): b")", ord("\\"): b"\\"}
        while self.pos < len(raw):
            c = raw[self.pos]
            self.pos += 1
            if c == ord("\\") and self.pos < len(raw):
                nxt = raw[self.pos]
                if nxt in escapes:
                    out += escapes[nxt]
                    self.pos += 1
                elif 48 <= nxt <= 55:
                    digits = re.match(rb"[0-7]{1,3}", raw[self.pos:self.pos + 3]).group(0)
                    out.append(int(digits, 8) & 0xFF)
                    self.pos += len(digits)
                elif nxt in (10, 13):
                    self.pos += 1
                continue
            if c == ord("("):
                depth += 1
            elif c == ord(")"):
                depth -= 1
                if depth == 0:
                    return _decode(bytes(out))
            out.append(c)
        raise FormDataError("FDF: Text ohne Ende")


class _Name(str):
    pass


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    try:
        from pypdf.generic._utils import PDFDocEncoding  # type: ignore
        return "".join(PDFDocEncoding[b] if b < len(PDFDocEncoding) else chr(b) for b in raw)
    except Exception:                                     # noqa: BLE001
        return raw.decode("latin-1")


def parse_fdf(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_FILE:
        raise FormDataError("Die Datei ist zu groß")
    if not raw.lstrip().startswith(b"%FDF"):
        raise FormDataError("Das ist keine FDF-Datei")
    if b"stream" in raw and re.search(rb"\bstream\b", raw):
        raise FormDataError("FDF mit eingebetteten Daten-Streams wird nicht gelesen")
    start = raw.find(b"obj")
    if start < 0:
        raise FormDataError("FDF ohne Objekt")
    reader = _Reader(raw)
    reader.pos = start + 3
    root = reader.read()
    fdf = root.get("/FDF") if isinstance(root, dict) else None
    if not isinstance(fdf, dict) or not isinstance(fdf.get("/Fields", []), list):
        raise FormDataError("FDF ohne Feldliste")
    out: dict[str, object] = {}

    def walk(items, prefix: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        for item in items:
            if not isinstance(item, dict) or len(out) >= MAX_FIELDS:
                continue
            title = item.get("/T", "")
            name = (prefix + "." if prefix else "") + (title[1:] if isinstance(title, _Name) else str(title))
            if "/V" in item:
                value = item["/V"]
                if isinstance(value, list):
                    value = ", ".join(str(v)[1:] if isinstance(v, _Name) else str(v) for v in value)
                elif isinstance(value, _Name):
                    value = value[1:]
                out[name] = None if value is None else str(value)
            if isinstance(item.get("/Kids"), list):
                walk(item["/Kids"], name, depth + 1)
    walk(fdf.get("/Fields", []), "", 0)
    return out


# ---- Import (alle Formate) -------------------------------------------------------------------------------------
def import_data(data: bytes, raw: bytes, kind: str | None = None) -> FillResult:
    """Werte aus JSON, XFDF oder FDF übernehmen; `kind` None = am Inhalt erkennen."""
    head = raw.lstrip()[:200]
    kind = kind or ("fdf" if head.startswith(b"%FDF") else "json" if head[:1] in (b"{", b"[") or
                    head.startswith(b"\xef\xbb\xbf{") else "xfdf")
    if kind == "json":
        values = parse_json(raw)
    elif kind == "fdf":
        values = parse_fdf(raw)
    elif kind == "xfdf":
        values = parse_xfdf_fields(raw)
    else:
        raise FormDataError(f"Unbekanntes Format „{kind}“")
    if not values:
        raise FormDataError("Die Datei enthält keine Feldwerte")
    return apply_values(data, values)


# ---- Antworten sammeln -----------------------------------------------------------------------------------------
def collect(files: list[tuple[str, bytes]]) -> tuple[str, list[str]]:
    """Mehrere ausgefüllte Kopien → CSV (Semikolon; je Datei eine Zeile). Liefert (CSV, Probleme)."""
    columns: list[str] = []
    rows: list[tuple[str, dict]] = []
    problems: list[str] = []
    for name, content in files:
        try:
            values = values_of(content)
        except PdfEditError as error:
            problems.append(f"{name}: {error}")
            continue
        if not values:
            problems.append(f"{name}: keine Formularfelder")
            continue
        for key in values:
            if key not in columns:
                columns.append(key)
        rows.append((name, values))
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Datei", *[csv_cell(c) for c in columns]])
    for name, values in rows:
        cells = []
        for column in columns:
            value = values.get(column, "")
            cells.append(("Ja" if value else "Nein") if isinstance(value, bool) else csv_cell(value))
        writer.writerow([csv_cell(name), *cells])
    return buffer.getvalue(), problems
