"""Lesezeichen (Outline) lesen, komplett neu schreiben und aus Überschriften vorschlagen (Qt-frei).

Die Oberfläche bearbeitet einen Baum aus `Bookmark` (anlegen, umbenennen, löschen, verschieben, verschachteln) und
schreibt ihn mit `write_outline` in einem Schritt zurück. Lesezeichen, die nicht auf eine Seite springen (z. B. auf
eine Webadresse), behalten ihre Original-Aktion unverändert – fckNotes führt sie nie aus.

`headings` schlägt Lesezeichen aus großen Textzeilen vor: Schriftgröße je Zeichen kommt von PDFium; was deutlich
größer ist als der Fließtext, wird je nach Größe Ebene 1, 2 oder 3.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, Fit, NameObject

from notex.core.pdfpages import PdfEditError, open_reader, to_bytes

MAX_ITEMS = 20000


@dataclass
class Bookmark:
    title: str
    page: int | None = 0            # 0-basiert; None = keine Seite (fremde Aktion)
    top: float | None = None        # y in PDF-Koordinaten der Seite (None = Seitenanfang/ganze Seite)
    children: list["Bookmark"] = field(default_factory=list)
    open: bool = False
    color: tuple[float, float, float] | None = None
    bold: bool = False
    italic: bool = False
    action: object | None = None    # Original-/A einer Nicht-Seiten-Aktion (bleibt erhalten)
    left: float | None = None

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


# ---- lesen -----------------------------------------------------------------------------------------------------
def _dest_array(reader, item):
    dest = item.get("/Dest")
    if dest is None:
        action = item.get("/A")
        if action is not None:
            action = action.get_object()
            if action.get("/S") == "/GoTo":
                dest = action.get("/D")
    if dest is None:
        return None
    dest = dest.get_object() if hasattr(dest, "get_object") else dest
    if isinstance(dest, (str, bytes)) or not hasattr(dest, "__len__") or isinstance(dest, DictionaryObject):
        name = str(dest.get("/D") if isinstance(dest, DictionaryObject) else dest)
        named = reader.named_destinations.get(name)
        if named is None:
            return None
        return named
    return dest


def _item_from(reader, item) -> Bookmark:
    title = str(item.get("/Title", "") or "")
    bookmark = Bookmark(title=title, page=None)
    flags = int(item.get("/F", 0) or 0)
    bookmark.italic, bookmark.bold = bool(flags & 1), bool(flags & 2)
    color = item.get("/C")
    if color is not None and len(color) == 3:
        bookmark.color = tuple(float(c) for c in color)
    count = item.get("/Count")
    bookmark.open = count is not None and int(count) > 0
    dest = _dest_array(reader, item)
    if dest is not None:
        try:
            if hasattr(dest, "page"):                  # pypdf-Destination (benanntes Ziel)
                bookmark.page = reader.get_page_number(dest.page) if dest.page is not None else None
                bookmark.top = float(dest.top) if getattr(dest, "top", None) is not None else None
                bookmark.left = float(dest.left) if getattr(dest, "left", None) is not None else None
            else:
                target = dest[0]
                bookmark.page = int(target) if isinstance(target, int) else reader.get_page_number(target)
                kind = str(dest[1]) if len(dest) > 1 else "/Fit"
                if kind == "/XYZ" and len(dest) > 3:
                    bookmark.left = _num(dest[2])
                    bookmark.top = _num(dest[3])
                elif kind in ("/FitH", "/FitBH") and len(dest) > 2:
                    bookmark.top = _num(dest[2])
        except Exception:                               # noqa: BLE001 – kaputtes Ziel: Seite 1
            bookmark.page = 0
    elif item.get("/A") is not None:
        bookmark.action = item["/A"]
    return bookmark


def _num(value) -> float | None:
    try:
        return None if value is None or str(value) == "null" else float(value)
    except (TypeError, ValueError):
        return None


def read_outline(data: bytes) -> list[Bookmark]:
    reader = open_reader(data)
    root = reader.trailer["/Root"].get("/Outlines")
    if root is None:
        return []
    seen: set[int] = set()

    def children(node) -> list[Bookmark]:
        out = []
        ref = node.get("/First")
        while ref is not None and len(seen) < MAX_ITEMS:
            key = getattr(ref, "idnum", id(ref))
            if key in seen:
                break                                   # Kreis im Baum: abbrechen statt hängen
            seen.add(key)
            item = ref.get_object()
            bookmark = _item_from(reader, item)
            bookmark.children = children(item)
            out.append(bookmark)
            ref = item.get("/Next")
        return out
    return children(root.get_object())


# ---- schreiben -------------------------------------------------------------------------------------------------
def write_outline(data: bytes, items: list[Bookmark]) -> bytes:
    """Alle Lesezeichen durch `items` ersetzen (leere Liste = alle entfernen)."""
    reader = open_reader(data)
    count = len(reader.pages)
    writer = PdfWriter(clone_from=reader)
    root = writer._root_object
    if "/Outlines" in root:
        del root["/Outlines"]
    if "/PageMode" in root and root["/PageMode"] == "/UseOutlines" and not items:
        del root["/PageMode"]

    def add(item: Bookmark, parent) -> None:
        if not item.title.strip():
            raise PdfEditError("Ein Lesezeichen braucht einen Titel")
        page = item.page if item.page is not None else 0
        if not 0 <= page < count:
            raise PdfEditError(f"Lesezeichen „{item.title}“: Seite {page + 1} gibt es nicht")
        fit = Fit.xyz(item.left, item.top, None) if item.top is not None else Fit.fit()
        ref = writer.add_outline_item(item.title.strip(), page, parent=parent, color=item.color, bold=item.bold,
                                      italic=item.italic, fit=fit, is_open=item.open)
        if item.page is None and item.action is not None:
            node = ref.get_object()
            if "/Dest" in node:
                del node["/Dest"]
            node[NameObject("/A")] = item.action.get_object().clone(writer)   # fremde Aktion unverändert
        for child in item.children:
            add(child, ref)
    for item in items:
        add(item, None)
    if items:
        root[NameObject("/PageMode")] = NameObject("/UseOutlines")
    return to_bytes(writer)


# ---- aus Überschriften vorschlagen -------------------------------------------------------------------------------
@dataclass
class Heading:
    text: str
    page: int
    top: float
    size: float
    level: int = 1


def find_headings(data: bytes, levels: int = 3, min_ratio: float = 1.15,
                  progress: Callable[[int, int], None] | None = None) -> list[Heading]:
    """Zeilen, deren Schrift deutlich größer ist als der Fließtext. `progress(i, n)` wirft bei Abbruch."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    try:
        doc = pdfium.PdfDocument(data)
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"PDF nicht lesbar ({error})") from error
    lines: list[tuple[str, int, float, float]] = []      # Text, Seite, top, Größe
    weights: Counter = Counter()
    try:
        total = len(doc)
        for index in range(total):
            if progress:
                progress(index, total)
            page = doc[index]
            textpage = page.get_textpage()
            n = textpage.count_chars()
            text = textpage.get_text_range(0, n) if n else ""
            current, sizes, top = [], [], None
            for i in range(n):
                ch = text[i] if i < len(text) else ""
                if ch in "\r\n":
                    if current:
                        _finish_line(lines, weights, "".join(current), index, top, sizes)
                    current, sizes, top = [], [], None
                    continue
                current.append(ch)
                if not ch.isspace():
                    sizes.append(round(raw.FPDFText_GetFontSize(textpage.raw, i) * 2) / 2)
                    box = textpage.get_charbox(i)
                    top = box[3] if top is None else max(top, box[3])
            if current:
                _finish_line(lines, weights, "".join(current), index, top, sizes)
            textpage.close()
            page.close()
    finally:
        doc.close()
    if not weights:
        return []
    body = weights.most_common(1)[0][0]
    candidates = [line for line in lines if line[3] >= body * min_ratio]
    sizes = sorted({line[3] for line in candidates}, reverse=True)
    groups: list[float] = []
    for size in sizes:                                  # Größen, die ≤ 0,5 pt auseinander liegen, sind eine Ebene
        if not groups or groups[-1] - size > 0.5:
            groups.append(size)
    groups = groups[:levels]
    out = []
    for text, page, top, size in candidates:
        level = next((i + 1 for i, g in enumerate(groups) if abs(size - g) <= 0.5 or size > g), None)
        if level is not None:
            out.append(Heading(text, page, top, size, level))
    return out


def _finish_line(lines, weights, text, page, top, sizes) -> None:
    text = re.sub(r"\s+", " ", text).strip()
    if not sizes:
        return
    size = Counter(sizes).most_common(1)[0][0]
    weights[size] += len(sizes)
    if 2 <= len(text) <= 120 and not re.fullmatch(r"[\d\s.,:;/()-]+", text):
        lines.append((text, page, top or 0.0, size))


def headings_to_outline(headings: list[Heading]) -> list[Bookmark]:
    """Ebenen-Liste → verschachtelter Baum (Ebene 2 unter die letzte Ebene 1 usw.)."""
    roots: list[Bookmark] = []
    stack: list[tuple[int, Bookmark]] = []
    for heading in headings:
        mark = Bookmark(heading.text, heading.page, heading.top + 2)
        while stack and stack[-1][0] >= heading.level:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(mark)
        stack.append((heading.level, mark))
    return roots
