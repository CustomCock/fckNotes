"""PDF-Seiten organisieren (Qt-frei, pypdf): umsortieren, drehen, löschen, herauslösen, einfügen, zusammenfügen,
aufteilen.

Alle Funktionen nehmen und liefern PDF-Bytes – die Oberfläche legt jede Änderung als Schritt auf einen
Rückgängig-Stapel. Neu aufgebaut wird immer über einen frischen PdfWriter mit `append`: so landen nur die behaltenen
Seiten samt dem, was sie brauchen (Schriften, Bilder, Anmerkungen, Lesezeichen auf diese Seiten), in der Datei.
Eine gelöschte Seite steckt danach nicht mehr unsichtbar im PDF – wichtig, wenn man es weitergibt.
"""
from __future__ import annotations

import io
import re

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import NameObject, NumberObject


class PdfEditError(Exception):
    """Verständliche Meldung für die Oberfläche (kaputtes PDF, Passwortschutz, unsinnige Eingabe)."""


def open_reader(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except (PdfReadError, ValueError, KeyError, TypeError) as error:
        raise PdfEditError(f"Kein gültiges PDF ({error})") from error
    if encrypted:
        raise PdfEditError("Passwortgeschützte PDFs lassen sich nur ansehen, nicht bearbeiten")
    return reader


def to_bytes(writer: PdfWriter) -> bytes:
    from notex.core.pdfannot import prune_unreachable
    prune_unreachable(writer)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def copy_metadata(reader: PdfReader, writer: PdfWriter) -> None:
    meta = reader.metadata or {}
    values = {key: str(value) for key, value in meta.items() if isinstance(key, str) and key != "/Producer"}
    if values:
        writer.add_metadata(values)


def page_count(data: bytes) -> int:
    return len(open_reader(data).pages)


def _check_pages(pages, count: int) -> list[int]:
    out = []
    for page in pages:
        if not isinstance(page, int) or not 0 <= page < count:
            raise PdfEditError(f"Seite {page + 1 if isinstance(page, int) else page} gibt es nicht (1–{count})")
        out.append(page)
    return out


def rearrange(data: bytes, order: list[int], rotate: dict[int, int] | None = None) -> bytes:
    """Neue Seitenfolge aus Quellseiten (0-basiert); fehlende Seiten fallen weg. `rotate`: Quellseite → Grad
    (Vielfache von 90, im Uhrzeigersinn, zusätzlich zur bisherigen Drehung)."""
    reader = open_reader(data)
    count = len(reader.pages)
    order = _check_pages(order, count)
    if not order:
        raise PdfEditError("Mindestens eine Seite muss übrig bleiben")
    if len(set(order)) != len(order):
        raise PdfEditError("Jede Seite darf nur einmal vorkommen")
    rotate = rotate or {}
    for degrees in rotate.values():
        if degrees % 90:
            raise PdfEditError("Drehen geht nur in 90°-Schritten")
    writer = PdfWriter()
    writer.append(reader, pages=list(order))
    for position, source in enumerate(order):
        degrees = rotate.get(source, 0) % 360
        if degrees:
            page = writer.pages[position]
            page[NameObject("/Rotate")] = NumberObject((page.rotation + degrees) % 360)   # 0/90/180/270, nie 360
    copy_metadata(reader, writer)
    return to_bytes(writer)


def rotate(data: bytes, pages: list[int], degrees: int) -> bytes:
    count = page_count(data)
    return rearrange(data, list(range(count)), {p: degrees for p in _check_pages(pages, count)})


def delete(data: bytes, pages: list[int]) -> bytes:
    count = page_count(data)
    gone = set(_check_pages(pages, count))
    return rearrange(data, [p for p in range(count) if p not in gone])


def move(data: bytes, pages: list[int], before: int) -> bytes:
    """Seiten `pages` (in ihrer Reihenfolge) vor die Seite `before` verschieben (`before` = Seitenzahl, 0-basiert,
    im alten Dokument; = Seitenanzahl → ans Ende)."""
    count = page_count(data)
    return rearrange(data, moved_order(count, pages, before))


def moved_order(count: int, pages: list[int], before: int) -> list[int]:
    picked = sorted(set(_check_pages(pages, count)))
    rest = [p for p in range(count) if p not in picked]
    anchor = len([p for p in rest if p < before])
    return rest[:anchor] + picked + rest[anchor:]


def extract(data: bytes, pages: list[int]) -> bytes:
    """Nur diese Seiten als neues PDF (in der angegebenen Reihenfolge)."""
    return rearrange(data, list(dict.fromkeys(pages)))


def insert(data: bytes, other: bytes, at: int) -> bytes:
    """Alle Seiten von `other` vor Seite `at` einfügen (`at` = Seitenanzahl → ans Ende)."""
    reader, extra = open_reader(data), open_reader(other)
    count = len(reader.pages)
    at = max(0, min(at, count))
    writer = PdfWriter()
    if at:
        writer.append(reader, pages=list(range(at)))
    writer.append(extra)
    if at < count:
        writer.append(reader, pages=list(range(at, count)))
    copy_metadata(reader, writer)
    return to_bytes(writer)


def merge(documents: list[bytes]) -> bytes:
    """Mehrere PDFs hintereinander zu einem (Metadaten vom ersten)."""
    if not documents:
        raise PdfEditError("Keine PDFs zum Zusammenfügen")
    readers = [open_reader(d) for d in documents]
    writer = PdfWriter()
    for reader in readers:
        writer.append(reader)
    copy_metadata(readers[0], writer)
    return to_bytes(writer)


def split(data: bytes, groups: list[list[int]]) -> list[bytes]:
    """Ein PDF je Gruppe (Gruppen = Listen 0-basierter Seiten)."""
    groups = [g for g in groups if g]
    if not groups:
        raise PdfEditError("Keine Seiten zum Aufteilen angegeben")
    return [extract(data, group) for group in groups]


# ---- Eingaben aus der Oberfläche ---------------------------------------------------------------------------------
_PART_RE = re.compile(r"^\s*(\d*)\s*(-?)\s*(\d*)\s*$")


def parse_ranges(text: str, count: int) -> list[int]:
    """„1-3, 5, 8-“ → [0, 1, 2, 4, 7, …] (0-basiert, Reihenfolge wie angegeben, ohne Doppelte).
    Erlaubt: Zahl, a-b, a- (bis Ende), -b (ab Anfang). Leer = alle Seiten."""
    text = (text or "").replace("–", "-").replace("—", "-")
    if not text.strip():
        return list(range(count))
    text = re.sub(r"\s*-\s*", "-", text)
    out: list[int] = []
    for part in re.split(r"[,\s]+", text):
        if not part.strip():
            continue
        m = _PART_RE.match(part)
        if not m or (not m.group(1) and not m.group(3)):
            raise PdfEditError(f"„{part.strip()}“ verstehe ich nicht – Beispiel: 1-3, 5, 8-")
        a = int(m.group(1)) if m.group(1) else 1
        b = int(m.group(3)) if m.group(3) else (count if m.group(2) else a)
        if a < 1 or b < 1 or a > count or b > count:
            raise PdfEditError(f"„{part.strip()}“: das PDF hat nur {count} Seiten")
        step = 1 if b >= a else -1
        for page in range(a, b + step, step):
            if page - 1 not in out:
                out.append(page - 1)
    return out


def parse_groups(text: str, count: int) -> list[list[int]]:
    """Aufteilen nach Bereichen: Gruppen durch „;“ getrennt, z. B. „1-3; 4-6; 7-“."""
    groups = [parse_ranges(chunk, count) for chunk in (text or "").split(";") if chunk.strip()]
    if not groups:
        raise PdfEditError("Bitte Bereiche angeben, z. B. 1-3; 4-6")
    return groups


def every(count: int, size: int) -> list[list[int]]:
    """Aufteilen alle `size` Seiten (1 = jede Seite einzeln)."""
    if size < 1:
        raise PdfEditError("Teilgröße muss mindestens 1 Seite sein")
    return [list(range(start, min(start + size, count))) for start in range(0, count, size)]


def part_names(stem: str, count: int) -> list[str]:
    width = len(str(count))
    return [f"{stem}_teil{str(i + 1).zfill(width)}.pdf" for i in range(count)]


def describe(pages: list[int]) -> str:
    """[0, 1, 2, 4] → „1–3, 5“ (für Meldungen und Dateinamen-Vorschläge)."""
    out, run = [], []
    for page in sorted(set(pages)):
        if run and page == run[-1] + 1:
            run.append(page)
            continue
        if run:
            out.append(f"{run[0] + 1}–{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
        run = [page]
    if run:
        out.append(f"{run[0] + 1}–{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
    return ", ".join(out)
