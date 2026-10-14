"""Ordnerweite Suche in PDF-Inhalten (Qt-frei, Block V10) mit einem Index in SQLite.

Der Index (`<portable>/cache/pdf-index.sqlite`) hält den Text jeder Seite. Aktualisiert wird inkrementell:
- Änderungsdatum und Größe unverändert → nichts tun;
- sonst Hash (SHA-256) bilden – gleich geblieben (nur „berührt“, kopiert, umbenannt) → Text übernehmen;
- erst dann neu auslesen (PDFium).
Passwortgeschützte PDFs werden nicht ausgelesen, sondern als „nicht durchsucht“ gemeldet; PDFs ohne Text (Scans)
ebenso – sie werden erst mit OCR durchsuchbar. Gelöschter Text wird mit `secure_delete` überschrieben, damit z. B.
eine geschwärzte Fassung keine alten Wörter im Index hinterlässt.

Gesucht wird mit derselben Abfragesprache wie in Textdateien (`notex.core.search`): mehrere Begriffe in einer Zeile,
„Phrase“, ext:/path:, ganze Wörter, Regex mit Zeitlimit, Groß/Klein egal (Unicode).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from notex.core import search as S

SCHEMA = 1
MAX_PDF_BYTES = 300 * 1024 * 1024
STATUS_LABELS = {"encrypted": "Passwortschutz", "notext": "kein Text (Scan?)", "error": "nicht lesbar",
                 "large": "zu groß"}


@dataclass
class PdfHit:
    page: int                        # 0-basiert
    line_no: int                     # Zeile auf der Seite (1-basiert)
    snippet: str
    start: int
    end: int
    spans: list[tuple[int, int]] = field(default_factory=list)
    text: str = ""                   # der gefundene Text (für die Markierung im PDF)


@dataclass
class PdfFileMatch:
    path: Path
    relative: str
    hits: list[PdfHit] = field(default_factory=list)


@dataclass
class IndexStats:
    checked: int = 0
    extracted: int = 0
    reused: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (relativer Pfad, Grund)
    cancelled: bool = False


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pages(data: bytes) -> tuple[str, list[str]]:
    """(Status, Text je Seite). Status: ok | encrypted | notext | error."""
    import pypdfium2 as pdfium
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        return ("encrypted" if "password" in str(error).lower() else "error"), []
    except Exception:                                     # noqa: BLE001
        return "error", []
    pages: list[str] = []
    try:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            count = textpage.count_chars()
            text = textpage.get_text_range(0, count) if count else ""
            pages.append(text.replace("\r\n", "\n").replace("\r", "\n").replace("￾", "").replace("\x00", ""))
            textpage.close()
            page.close()
    except Exception:                                     # noqa: BLE001
        return "error", []
    finally:
        document.close()
    if not any(p.strip() for p in pages):
        return "notext", pages
    return "ok", pages


class PdfIndex:
    """Ein Index je Datenbankdatei. Jede Methode öffnet ihre eigene Verbindung (Threads der Suche)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA:
            connection.executescript("""
                DROP TABLE IF EXISTS docs; DROP TABLE IF EXISTS pages;
                CREATE TABLE docs (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, hash TEXT, status TEXT,
                                   pages INTEGER);
                CREATE TABLE pages (path TEXT, page INTEGER, text TEXT, PRIMARY KEY (path, page));
                CREATE INDEX docs_hash ON docs (hash);
            """)
            connection.execute(f"PRAGMA user_version = {SCHEMA}")
            connection.commit()
        return connection

    # ---- Aktualisieren -------------------------------------------------------------------------------------
    def update(self, files: list[Path], root: Path | None = None, cancel: threading.Event | None = None,
               progress: Callable[[int, int, str], None] | None = None, max_bytes: int = MAX_PDF_BYTES) -> IndexStats:
        """Index für diese Dateien auf Stand bringen. Fehlende Dateien werden aus dem Index entfernt (`prune`)."""
        stats = IndexStats()
        with self._lock:
            connection = self._connect()
            try:
                known = {row[0]: row[1:] for row in connection.execute(
                    "SELECT path, mtime, size, hash, status FROM docs")}
                for number, path in enumerate(files):
                    if cancel is not None and cancel.is_set():
                        stats.cancelled = True
                        break
                    key = str(path)
                    relative = _relative(path, root)
                    if progress is not None:
                        progress(number, len(files), relative)
                    stats.checked += 1
                    try:
                        info = path.stat()
                    except OSError:
                        continue
                    old = known.get(key)
                    if info.st_size > max_bytes:
                        self._store(connection, key, info, "", "large", [])
                        stats.skipped.append((relative, STATUS_LABELS["large"]))
                        continue
                    if old is not None and old[0] == info.st_mtime and old[1] == info.st_size:
                        if old[3] != "ok":
                            stats.skipped.append((relative, STATUS_LABELS.get(old[3], old[3])))
                        continue
                    try:
                        digest = _hash(path)
                    except OSError:
                        continue
                    source = self._same_content(connection, digest, key)
                    if source is not None:                       # gleicher Inhalt: Text übernehmen
                        status = source[1]
                        texts = [r[0] for r in connection.execute(
                            "SELECT text FROM pages WHERE path = ? ORDER BY page", (source[0],))]
                        self._store(connection, key, info, digest, status, texts)
                        stats.reused += 1
                    else:
                        try:
                            data = path.read_bytes()
                        except OSError:
                            continue
                        status, texts = extract_pages(data)
                        self._store(connection, key, info, digest, status, texts if status == "ok" else [])
                        stats.extracted += 1
                    if status != "ok":
                        stats.skipped.append((relative, STATUS_LABELS.get(status, status)))
                    if number % 20 == 19:
                        connection.commit()
                connection.commit()
                _checkpoint(connection)
            finally:
                connection.close()
        if progress is not None and not stats.cancelled:
            progress(len(files), len(files), "")
        return stats

    def _same_content(self, connection, digest: str, key: str):
        row = connection.execute("SELECT path, status FROM docs WHERE hash = ? AND path != ? LIMIT 1",
                                 (digest, key)).fetchone()
        if row is not None:
            return row
        own = connection.execute("SELECT path, status FROM docs WHERE hash = ? AND path = ?", (digest, key)).fetchone()
        return own

    def _store(self, connection, key: str, info, digest: str, status: str, texts: list[str]) -> None:
        connection.execute("DELETE FROM pages WHERE path = ?", (key,))
        connection.executemany("INSERT INTO pages (path, page, text) VALUES (?, ?, ?)",
                               [(key, index, text) for index, text in enumerate(texts)])
        connection.execute("INSERT OR REPLACE INTO docs (path, mtime, size, hash, status, pages) VALUES (?, ?, ?, ?, "
                           "?, ?)", (key, info.st_mtime, info.st_size, digest, status, len(texts)))

    def forget(self, path: Path | str) -> None:
        """Einen Eintrag sofort entfernen (z. B. nach dem Speichern eines geschwärzten PDFs)."""
        if not self.path.exists():
            return
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("DELETE FROM pages WHERE path = ?", (str(path),))
                connection.execute("DELETE FROM docs WHERE path = ?", (str(path),))
                connection.commit()
                _checkpoint(connection)
            finally:
                connection.close()

    def prune(self, existing: set[str], under: Path | None = None) -> int:
        """Einträge entfernen, deren Datei es (unter `under`) nicht mehr gibt."""
        with self._lock:
            connection = self._connect()
            try:
                paths = [row[0] for row in connection.execute("SELECT path FROM docs")]
                prefix = str(under) + os.sep if under is not None else ""
                gone = [p for p in paths if (not prefix or p.startswith(prefix)) and p not in existing]
                for path in gone:
                    connection.execute("DELETE FROM pages WHERE path = ?", (path,))
                    connection.execute("DELETE FROM docs WHERE path = ?", (path,))
                connection.commit()
                if gone:
                    _checkpoint(connection)
            finally:
                connection.close()
        return len(gone)

    def clear(self) -> None:
        """Index komplett löschen (Datei samt WAL)."""
        with self._lock:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(str(self.path) + suffix).unlink()
                except FileNotFoundError:
                    pass

    def status(self, path: Path | str) -> str | None:
        if not self.path.exists():
            return None
        connection = self._connect()
        try:
            row = connection.execute("SELECT status FROM docs WHERE path = ?", (str(path),)).fetchone()
        finally:
            connection.close()
        return row[0] if row else None

    # ---- Suchen --------------------------------------------------------------------------------------------
    def search(self, files: list[Path], query: S.Query, root: Path | None = None,
               cancel: threading.Event | None = None,
               on_file: Callable[[PdfFileMatch], None] | None = None) -> list[PdfFileMatch]:
        """Treffer in den (aktuell indexierten) Dateien, je Zeile einer Seite. Kann S.RegexTimeout werfen."""
        out: list[PdfFileMatch] = []
        connection = self._connect()
        try:
            for path in files:
                if cancel is not None and cancel.is_set():
                    break
                rows = connection.execute("SELECT page, text FROM pages WHERE path = ? ORDER BY page", (str(path),))
                match = PdfFileMatch(path, _relative(path, root))
                for page, text in rows:
                    for line_no, line in enumerate(text.split("\n"), start=1):
                        spans = S.match_spans(query, line)
                        if not spans:
                            continue
                        found = S._line_match(line_no, line, spans)
                        match.hits.append(PdfHit(page, line_no, found.snippet, found.start, found.end, found.spans,
                                                 line[spans[0][0]:spans[0][1]]))
                        if len(match.hits) >= 500:
                            break
                if match.hits:
                    out.append(match)
                    if on_file is not None:
                        on_file(match)
        finally:
            connection.close()
        return out


def _checkpoint(connection) -> None:
    """WAL in die Datenbank zurückschreiben und leeren – sonst stünde gelöschter Text noch im -wal."""
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def _relative(path: Path, root: Path | None) -> str:
    if root is None:
        return path.name
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def pdf_files(root: Path) -> list[Path]:
    """Alle PDFs unter `root` (rekursiv, Endung ohne Groß/Klein, versteckte Ordner übersprungen)."""
    return list(S.iter_files(root, (".pdf",)))
