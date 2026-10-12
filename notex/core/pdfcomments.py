"""Kommentare eines PDFs als Threads (Qt-frei): auflisten, filtern, sortieren, antworten, Status setzen, zusammenfassen.

Ein „Kommentar“ ist jede Anmerkung außer Links, Formularfeldern und Popups. Antworten sind `/Text`-Anmerkungen mit
`/IRT` (in reply to) auf den Kommentar und `/RT /R`; der Prüfstatus ist – wie in Acrobat – eine eigene versteckte
`/Text`-Anmerkung mit `/IRT`, `/State` und `/StateModel /Review`; es gilt der jüngste Eintrag. Antworten und
Status-Einträge werden nicht gezeichnet (`/F` NoView bzw. Hidden) und erscheinen nicht als eigene Objekte auf der Seite.

Für Markierungen liefert `list_comments` den markierten Text (über PDFium, Bereich je QuadPoint) – für die
Zusammenfassung als Markdown, CSV oder (in der Oberfläche) PDF.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pypdf.generic import DictionaryObject, NameObject, NumberObject, TextStringObject

from notex.core import pdfannot as A
from notex.core.pdfpages import PdfEditError, open_reader

SKIP = {"/Popup", "/Link", "/Widget"}
STATES = {"open": "/None", "accepted": "/Accepted", "rejected": "/Rejected", "done": "/Completed",
          "cancelled": "/Cancelled"}
STATE_LABELS = {"open": "offen", "accepted": "angenommen", "rejected": "abgelehnt", "done": "erledigt",
                "cancelled": "abgebrochen"}
FLAG_HIDDEN, FLAG_NOZOOM, FLAG_NOROTATE, FLAG_NOVIEW, FLAG_PRINT = 2, 8, 16, 32, 4


# ---- Datum -------------------------------------------------------------------------------------------------------
def parse_date(value) -> datetime | None:
    """PDF-Datum „D:20261011183000+02'00'“ (auch ohne Zeitzone/Sekunden) → datetime mit Zeitzone."""
    text = str(value or "").strip()
    match = re.match(r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?'?", text)
    if not match:
        return None
    y, mo, d, h, mi, s, sign, oh, om = match.groups()
    try:
        moment = datetime(int(y), int(mo or 1), int(d or 1), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    offset = timedelta(0)
    if sign in ("+", "-") and oh:
        offset = timedelta(hours=int(oh), minutes=int(om or 0)) * (1 if sign == "+" else -1)
    return moment.replace(tzinfo=timezone(offset))


def format_date(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y %H:%M") if moment else ""


def pdf_date(moment: datetime | None = None) -> str:
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("D:%Y%m%d%H%M%SZ")


# ---- Lesen -------------------------------------------------------------------------------------------------------
@dataclass
class Comment:
    page: int
    index: int                      # Position in /Annots
    kind: str                       # highlight | note | text | ink | square | … (pdfannot.KIND_NAMES)
    contents: str = ""
    author: str = ""
    subject: str = ""
    created: datetime | None = None
    modified: datetime | None = None
    status: str = "open"
    status_by: str = ""
    rect: A.Rect = (0.0, 0.0, 0.0, 0.0)  # Ansichts-Punkte
    quoted: str = ""                 # markierter Text (nur Markierungen)
    nm: str = ""
    color: str = ""
    replies: list["Comment"] = field(default_factory=list)
    parent_nm: str = ""

    @property
    def label(self) -> str:
        return A.LABELS.get(self.kind, self.kind.capitalize())

    @property
    def date(self) -> datetime | None:
        return self.modified or self.created

    @property
    def status_label(self) -> str:
        return STATE_LABELS.get(self.status, self.status)


def _text(value) -> str:
    try:
        value = value.get_object() if hasattr(value, "get_object") else value
    except Exception:                                     # noqa: BLE001
        return ""
    return "" if value is None else str(value)


def _key(ref) -> object:
    return getattr(ref, "idnum", None) or id(ref.get_object())


def _quoted(textpages, page_index: int, annot) -> str:
    quads = annot.get("/QuadPoints")
    if quads is None or textpages is None:
        return ""
    values = [float(v) for v in quads]
    parts = []
    try:
        textpage = textpages(page_index)
        for i in range(0, len(values) - 7, 8):
            xs, ys = values[i:i + 8:2], values[i + 1:i + 8:2]
            text = textpage.get_text_bounded(min(xs), min(ys), max(xs), max(ys))
            if text.strip():
                parts.append(text.strip())
    except Exception:                                     # noqa: BLE001 – ohne Zitat weiter
        return ""
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def list_comments(data: bytes, with_quotes: bool = True) -> list[Comment]:
    """Alle Kommentare als Threads (Antworten unter `replies`, Status aus dem jüngsten Status-Eintrag)."""
    reader = open_reader(data)
    by_key: dict[object, Comment] = {}
    parent_of: dict[object, object] = {}
    replies: list[tuple[object, Comment]] = []
    states: list[tuple[object, str, str, datetime | None]] = []
    order: list[Comment] = []
    cache: dict[int, object] = {}
    pdfium_doc = None

    def textpages(index: int):
        nonlocal pdfium_doc
        if index not in cache:
            import pypdfium2 as pdfium
            if pdfium_doc is None:
                pdfium_doc = pdfium.PdfDocument(data)
            cache[index] = pdfium_doc[index].get_textpage()
        return cache[index]

    try:
        for page_index, page in enumerate(reader.pages):
            geom = A.geometry_of(page)
            for position, ref in enumerate(page.get("/Annots", None) or []):
                try:
                    annot = ref.get_object()
                    subtype = str(annot.get("/Subtype", ""))
                    if subtype in SKIP or "/Rect" not in annot:
                        continue
                    x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
                except Exception:                         # noqa: BLE001
                    continue
                comment = Comment(
                    page_index, position, A.KIND_NAMES.get(subtype, subtype.lstrip("/").lower()),
                    _text(annot.get("/Contents")), _text(annot.get("/T")), _text(annot.get("/Subj")),
                    parse_date(annot.get("/CreationDate")), parse_date(annot.get("/M")), "open", "",
                    geom.rect_to_view((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))), "",
                    _text(annot.get("/NM")), A._hex(annot.get("/C", [])))
                key = _key(ref)
                by_key[key] = comment
                parent = annot.get("/IRT")
                if parent is not None:
                    parent_of[key] = _key(parent)
                    if annot.get("/State") is not None:
                        state = str(annot.get("/State")).lstrip("/")
                        name = next((k for k, v in STATES.items() if v.lstrip("/") == state), state.lower())
                        states.append((key, name, comment.author, comment.date))
                    else:
                        replies.append((key, comment))
                    continue
                if with_quotes and comment.kind in A.MARKUP_KINDS + ("squiggly",):
                    comment.quoted = _quoted(textpages, page_index, annot)
                order.append(comment)
    finally:
        for textpage in cache.values():
            textpage.close()
        if pdfium_doc is not None:
            pdfium_doc.close()

    def root_of(key):
        """Ursprungs-Kommentar eines Threads (Antwort auf Antwort … → Kommentar), mit Kreis-Schutz."""
        seen = set()
        while key in parent_of and key not in seen:
            seen.add(key)
            key = parent_of[key]
        return by_key.get(key)

    roots = {id(c) for c in order}
    for key, reply in replies:
        root = root_of(key)
        direct = by_key.get(parent_of[key])
        reply.parent_nm = direct.nm if direct is not None else ""
        if root is not None and id(root) in roots:
            root.replies.append(reply)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    for key, name, author, _moment in sorted(states, key=lambda s: s[3] or epoch):
        root = root_of(key)
        if root is not None and id(root) in roots:
            root.status, root.status_by = name, author
    for comment in order:
        comment.replies.sort(key=lambda r: r.date or epoch)
    return order


# ---- Filtern / Sortieren -----------------------------------------------------------------------------------------
SORT_KEYS = {
    "page": lambda c: (c.page, c.rect[1], c.rect[0]),
    "date": lambda c: (c.date or datetime.min.replace(tzinfo=timezone.utc)),
    "author": lambda c: (c.author.casefold(), c.page),
    "kind": lambda c: (c.label, c.page),
    "status": lambda c: (list(STATES).index(c.status) if c.status in STATES else 99, c.page),
}


def filter_comments(comments: list[Comment], kind: str = "", page: int | None = None, author: str = "",
                    status: str = "", text: str = "", since: datetime | None = None,
                    sort: str = "page", descending: bool = False) -> list[Comment]:
    needle = text.casefold().strip()
    out = []
    for c in comments:
        if kind and c.kind != kind:
            continue
        if page is not None and c.page != page:
            continue
        if author and c.author != author:
            continue
        if status and c.status != status:
            continue
        if since and (c.date is None or c.date < since):
            continue
        if needle:
            haystack = " ".join([c.contents, c.quoted, c.author, c.subject, *(r.contents for r in c.replies)])
            if needle not in haystack.casefold():
                continue
        out.append(c)
    return sorted(out, key=SORT_KEYS.get(sort, SORT_KEYS["page"]), reverse=descending)


# ---- Schreiben ---------------------------------------------------------------------------------------------------
def _target(writer, page_index: int, position: int):
    annots = A._annots(A._page(writer, page_index))
    if not 0 <= position < len(annots):
        raise PdfEditError("Diesen Kommentar gibt es nicht mehr")
    ref = annots[position]
    annot = ref.get_object()
    if str(annot.get("/Subtype")) in SKIP:
        raise PdfEditError("Darauf kann man nicht antworten")
    while annot.get("/IRT") is not None and annot.get("/State") is None:   # Antwort auf Antwort → an den Ursprung
        ref = annot["/IRT"]
        annot = ref.get_object()
    return ref, annot


def _child(writer, page_index: int, parent_ref, parent, author: str) -> DictionaryObject:
    child = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): A._floats([float(v) for v in parent["/Rect"]]),
        NameObject("/IRT"): parent_ref if hasattr(parent_ref, "idnum") else writer._add_object(parent),
        NameObject("/NM"): TextStringObject(A.ID_PREFIX + uuid.uuid4().hex[:12]),
        NameObject("/M"): TextStringObject(pdf_date()), NameObject("/CreationDate"): TextStringObject(pdf_date()),
        NameObject("/P"): A._page(writer, page_index).indirect_reference,
    })
    if author.strip():
        child[NameObject("/T")] = TextStringObject(author.strip())
    return child


def add_reply(data: bytes, page_index: int, position: int, text: str, author: str = "") -> bytes:
    """Antwort auf einen Kommentar (Antworten auf Antworten hängen am Ursprungs-Kommentar)."""
    if not text.strip():
        raise PdfEditError("Die Antwort ist leer")
    writer = A.open_writer(data)
    parent_ref, parent = _target(writer, page_index, position)
    reply = _child(writer, page_index, parent_ref, parent, author)
    reply[NameObject("/RT")] = NameObject("/R")
    reply[NameObject("/Contents")] = TextStringObject(text.strip())
    reply[NameObject("/F")] = NumberObject(FLAG_NOVIEW | FLAG_NOZOOM | FLAG_NOROTATE)
    reply[NameObject("/Name")] = NameObject("/Comment")
    A._annots(A._page(writer, page_index)).append(writer._add_object(reply))
    return A.finish(writer)


def set_status(data: bytes, page_index: int, position: int, status: str, author: str = "") -> bytes:
    """Prüfstatus setzen (offen, angenommen, abgelehnt, erledigt, abgebrochen) – als neuer Status-Eintrag."""
    if status not in STATES:
        raise PdfEditError(f"Unbekannter Status „{status}“")
    writer = A.open_writer(data)
    parent_ref, parent = _target(writer, page_index, position)
    entry = _child(writer, page_index, parent_ref, parent, author)
    entry[NameObject("/State")] = NameObject(STATES[status])
    entry[NameObject("/StateModel")] = NameObject("/Review")
    entry[NameObject("/Contents")] = TextStringObject(f"Status: {STATE_LABELS[status]}"
                                                      + (f" ({author.strip()})" if author.strip() else ""))
    entry[NameObject("/F")] = NumberObject(FLAG_HIDDEN | FLAG_NOVIEW | FLAG_NOZOOM | FLAG_NOROTATE)
    A._annots(A._page(writer, page_index)).append(writer._add_object(entry))
    return A.finish(writer)


def find(comments: list[Comment], nm: str) -> Comment | None:
    for comment in comments:
        if comment.nm == nm:
            return comment
        for reply in comment.replies:
            if reply.nm == nm:
                return reply
    return None


# ---- Zusammenfassung ---------------------------------------------------------------------------------------------
def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def summary_markdown(comments: list[Comment], title: str = "") -> str:
    lines = [f"# Kommentare{(' – ' + title) if title else ''}", ""]
    if not comments:
        return "\n".join(lines + ["Keine Kommentare."]) + "\n"
    lines.append(f"{len(comments)} Kommentare, {sum(len(c.replies) for c in comments)} Antworten.")
    current = None
    for c in sorted(comments, key=SORT_KEYS["page"]):
        if c.page != current:
            current = c.page
            lines += ["", f"## Seite {c.page + 1}", ""]
        meta = " · ".join(p for p in (c.author, format_date(c.date), c.status_label) if p)
        lines.append(f"- **{c.label}**" + (f" · {meta}" if meta else ""))
        if c.quoted:
            lines.append(f"  > {_one_line(c.quoted)}")
        if c.contents.strip():
            lines += [f"  {line}" if line.strip() else "" for line in c.contents.strip().splitlines()]
        for reply in c.replies:
            who = " · ".join(p for p in (reply.author, format_date(reply.date)) if p)
            lines.append(f"  - *Antwort{(' ' + who) if who else ''}:* {_one_line(reply.contents)}")
    return "\n".join(lines) + "\n"


CSV_HEADER = ["Nr", "Seite", "Art", "Autor", "Datum", "Status", "Kommentar", "Markierter Text", "Antwort auf"]


def csv_cell(value) -> str:
    """Zellen, die Excel/LibreOffice als Formel lesen würde (=, +, -, @, Tab, CR am Anfang), mit ' entschärfen –
    Kommentare können aus fremden PDFs stammen (CSV-Injection)."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def summary_csv(comments: list[Comment]) -> str:
    """CSV (Semikolon, für Excel mit BOM speichern): eine Zeile je Kommentar und je Antwort."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    number = 0
    for c in sorted(comments, key=SORT_KEYS["page"]):
        number += 1
        parent = number
        writer.writerow([number, c.page + 1, c.label, csv_cell(c.author), format_date(c.date), c.status_label,
                         csv_cell(c.contents), csv_cell(c.quoted), ""])
        for reply in c.replies:
            number += 1
            writer.writerow([number, reply.page + 1, "Antwort", csv_cell(reply.author), format_date(reply.date), "",
                             csv_cell(reply.contents), "", parent])
    return buffer.getvalue()
