"""Anhänge eines PDFs (Qt-frei): auflisten, Inhalt holen, hinzufügen, entfernen.

Zwei Ablageorte: Dokument-Anhänge im Namensbaum `/Names /EmbeddedFiles` und Datei-Anmerkungen (`/FileAttachment`)
auf einer Seite. Beide erscheinen in einer Liste; ein Anhang wird über `key` adressiert („doc:<Name>“ bzw.
„annot:<Seite>:<Position>“).

fckNotes öffnet nichts von selbst. `risky` erkennt Programme, Skripte und Makro-Dokumente an Endung und Dateikopf –
solche Anhänge lassen sich nur speichern. `safe_filename` macht aus dem Namen im PDF einen harmlosen Dateinamen
(ohne Ordner, „..“ oder Steuerzeichen), bevor irgendetwas auf die Platte geht.
"""
from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, TextStringObject

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError, open_reader

MAX_ATTACHMENT = 200 * 1024 * 1024          # größere Dateien nicht in den Speicher/ins PDF
MAX_NAME_NODES = 100000

# Gleiche Liste wie notex.platform.external (Programme, Skripte, Verknüpfungen, Makro-Dokumente)
EXECUTABLE_SUFFIXES = {
    ".exe", ".com", ".bat", ".cmd", ".msi", ".msp", ".scr", ".pif", ".cpl", ".dll", ".sys", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".jar", ".appref-ms",
    ".msc", ".gadget", ".application", ".sh", ".run", ".appimage", ".desktop", ".py", ".pyw", ".pl", ".rb",
    ".deb", ".rpm", ".apk", ".app", ".command", ".iso", ".img", ".vhd", ".vhdx", ".chm", ".xll", ".docm", ".xlsm",
    ".pptm", ".dotm", ".xlam", ".ppam",
}
EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"#!", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")


@dataclass(frozen=True)
class Attachment:
    key: str                # „doc:<Name>“ oder „annot:<Seite>:<Position>“
    name: str               # Dateiname wie im PDF
    size: int
    description: str = ""
    page: int | None = None  # nur Datei-Anmerkungen
    mime: str = ""
    risky: bool = False
    modified: str = ""

    @property
    def where(self) -> str:
        return "Dokument" if self.page is None else f"Seite {self.page + 1}"


def risky(name: str, content: bytes = b"") -> bool:
    """Programm, Skript, Verknüpfung oder Makro-Dokument? Dann nie öffnen, nur speichern."""
    suffixes = [s.lower() for s in PurePosixPath(safe_filename(name)).suffixes]
    if any(s in EXECUTABLE_SUFFIXES for s in suffixes):
        return True
    return any(content.startswith(magic) for magic in EXECUTABLE_MAGIC)


def safe_filename(name: str) -> str:
    """Nur der letzte Namensteil, ohne Steuer- und Sonderzeichen; nie leer, nie „.“/„..“."""
    name = str(name or "")
    name = PureWindowsPath(PurePosixPath(name.replace("\\", "/")).name).name
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name).strip(" .")
    if name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                      *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name[:200] or "anhang"


def _text(value) -> str:
    try:
        value = value.get_object() if isinstance(value, IndirectObject) else value
    except Exception:                                     # noqa: BLE001
        return ""
    return "" if value is None else str(value)


def _name_tree(node) -> list[tuple[str, object]]:
    """Alle (Name, Wert) eines Namensbaums – auch über /Kids, mit Kreis-Schutz."""
    out: list[tuple[str, object]] = []
    stack, seen = [node], set()
    while stack and len(seen) < MAX_NAME_NODES:
        current = stack.pop()
        key = getattr(current, "idnum", None)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        try:
            current = current.get_object()
        except Exception:                                 # noqa: BLE001
            continue
        names = current.get("/Names", []) or []
        for i in range(0, len(names) - 1, 2):
            out.append((_text(names[i]), names[i + 1]))
        stack.extend(reversed(list(current.get("/Kids", []) or [])))
    return out


def _embedded(spec):
    """Eingebetteter Datei-Stream einer Dateispezifikation (oder None)."""
    spec = spec.get_object()
    ef = spec.get("/EF")
    if ef is None:
        return None
    ef = ef.get_object()
    stream = ef.get("/UF") or ef.get("/F")
    return stream.get_object() if stream is not None else None


def _spec_name(spec, fallback: str) -> str:
    spec = spec.get_object()
    return _text(spec.get("/UF")) or _text(spec.get("/F")) or fallback or "anhang"


def _info(key: str, spec, fallback: str, page: int | None) -> Attachment:
    spec = spec.get_object()
    name = _spec_name(spec, fallback)
    stream = _embedded(spec)
    size, head, modified, mime = 0, b"", "", ""
    if stream is not None:
        params = stream.get("/Params")
        params = params.get_object() if params is not None else {}
        size = int(params.get("/Size", 0) or 0)
        modified = _text(params.get("/ModDate"))
        mime = _text(stream.get("/Subtype")).lstrip("/").replace("#2F", "/")
        if not size or size <= 4 * 1024 * 1024:
            try:
                content = stream.get_data()
                size = size or len(content)
                head = content[:8]
            except Exception:                             # noqa: BLE001 – kaputter Stream: Größe unbekannt
                pass
    mime = mime or (mimetypes.guess_type(safe_filename(name))[0] or "")
    return Attachment(key, name, size, _text(spec.get("/Desc")), page, mime, risky(name, head), modified)


def _doc_specs(reader_or_writer) -> list[tuple[str, object]]:
    root = reader_or_writer.trailer["/Root"] if hasattr(reader_or_writer, "trailer") else reader_or_writer._root_object
    names = root.get_object().get("/Names")
    if names is None or names.get_object().get("/EmbeddedFiles") is None:
        return []
    return _name_tree(names.get_object()["/EmbeddedFiles"])


def list_attachments(data: bytes) -> list[Attachment]:
    reader = open_reader(data)
    out: list[Attachment] = []
    for name, spec in _doc_specs(reader):
        try:
            out.append(_info(f"doc:{name}", spec, name, None))
        except Exception:                                 # noqa: BLE001 – kaputter Eintrag: trotzdem zeigen
            out.append(Attachment(f"doc:{name}", name or "anhang", 0))
    for index, page in enumerate(reader.pages):
        for position, ref in enumerate(page.get("/Annots", None) or []):
            try:
                annot = ref.get_object()
                if annot.get("/Subtype") != "/FileAttachment" or annot.get("/FS") is None:
                    continue
                out.append(_info(f"annot:{index}:{position}", annot["/FS"], "", index))
            except Exception:                             # noqa: BLE001
                continue
    return out


def _find_spec(reader, key: str):
    if key.startswith("doc:"):
        wanted = key[4:]
        for name, spec in _doc_specs(reader):
            if name == wanted:
                return spec
    elif key.startswith("annot:"):
        try:
            _kind, page, position = key.split(":")
            annot = reader.pages[int(page)]["/Annots"][int(position)].get_object()
            if annot.get("/Subtype") == "/FileAttachment":
                return annot["/FS"]
        except (ValueError, IndexError, KeyError, TypeError):
            pass
    raise PdfEditError("Diesen Anhang gibt es nicht mehr")


def extract(data: bytes, key: str) -> tuple[str, bytes]:
    """(Dateiname wie im PDF, Inhalt) eines Anhangs."""
    reader = open_reader(data)
    spec = _find_spec(reader, key)
    stream = _embedded(spec)
    if stream is None:
        raise PdfEditError("Der Anhang verweist nur auf eine externe Datei – es ist nichts eingebettet")
    try:
        content = stream.get_data()
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"Anhang nicht lesbar ({error})") from error
    return _spec_name(spec, key[4:] if key.startswith("doc:") else ""), content


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    for n in range(2, 10000):
        candidate = f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
        if candidate not in taken:
            return candidate
    raise PdfEditError("Zu viele gleichnamige Anhänge")


def add_attachment(data: bytes, filename: str, content: bytes, description: str = "") -> bytes:
    """Datei als Dokument-Anhang einbetten; gleicher Name bekommt „(2)“."""
    if len(content) > MAX_ATTACHMENT:
        raise PdfEditError(f"Anhang zu groß (höchstens {MAX_ATTACHMENT // (1024 * 1024)} MB)")
    name = safe_filename(filename)
    writer = A.open_writer(data)
    name = _unique(name, {n for n, _s in _doc_specs(writer)})
    embedded = writer.add_attachment(name, content)
    spec = embedded.pdf_object                          # Dateispezifikation
    if description:
        spec[NameObject("/Desc")] = TextStringObject(description)
    mime = mimetypes.guess_type(name)[0]
    stream = _embedded(spec)
    if stream is not None and mime:
        stream[NameObject("/Subtype")] = NameObject("/" + mime.replace("/", "#2F"))
    return A.finish(writer)


def remove_attachment(data: bytes, key: str) -> bytes:
    """Anhang entfernen – aus dem Namensbaum (und /AF) bzw. die Datei-Anmerkung samt Popup. Der Inhalt bleibt nicht
    als verwaistes Objekt in der Datei."""
    if key.startswith("annot:"):
        try:
            _kind, page, position = key.split(":")
            page, position = int(page), int(position)
        except ValueError as error:
            raise PdfEditError("Diesen Anhang gibt es nicht mehr") from error
        reader = open_reader(data)
        _find_spec(reader, key)
        return A.delete_annotation(data, page, position)
    if not key.startswith("doc:"):
        raise PdfEditError("Diesen Anhang gibt es nicht mehr")
    wanted = key[4:]
    writer = A.open_writer(data)
    root = writer._root_object
    entries = _doc_specs(writer)
    keep = [(n, s) for n, s in entries if n != wanted]
    if len(keep) == len(entries):
        raise PdfEditError("Diesen Anhang gibt es nicht mehr")
    removed = [s for n, s in entries if n == wanted]
    names = root["/Names"].get_object()
    if keep:
        keep.sort(key=lambda item: item[0])
        flat = ArrayObject()
        for name, spec in keep:
            flat.extend([TextStringObject(name), spec])
        names[NameObject("/EmbeddedFiles")] = DictionaryObject({NameObject("/Names"): flat})
    else:
        del names[NameObject("/EmbeddedFiles")]
        if not [k for k in names.keys()]:
            del root[NameObject("/Names")]
    af = root.get("/AF")
    if af is not None:
        targets = {id(s.get_object()) for s in removed}
        array = af.get_object()
        for i in range(len(array) - 1, -1, -1):
            if id(array[i].get_object()) in targets:
                del array[i]
        if not len(array):
            del root[NameObject("/AF")]
    if "/PageMode" in root and root["/PageMode"] == "/UseAttachments" and not keep:
        del root[NameObject("/PageMode")]
    return A.finish(writer)
