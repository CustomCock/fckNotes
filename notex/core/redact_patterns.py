"""Muster zum Schwärzen (Qt-frei, Block V6): personenbezogene Daten im Seitentext finden.

Eingebaut: E-Mail, Telefon (Deutschland und international), IBAN (Prüfsumme Mod-97), BIC, Kreditkarte (Luhn),
Datum (prüft, ob es den Tag gibt), Steuer-ID (Prüfziffer nach ISO 7064 Mod 11,10), PLZ, IP-Adresse (v4/v6), URL –
dazu eigene reguläre Ausdrücke (mit Zeitlimit gegen katastrophales Backtracking).

Muster finden Kandidaten, Prüfer verwerfen Fehltreffer (z. B. eine 22-stellige Zahl ohne gültige IBAN-Prüfsumme).
`find_hits` sucht im Text einer PDF-Seite (PDFium) und liefert je Treffer die Zeilen-Rechtecke zum Schwärzen.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

try:   # Zeitlimit pro Aufruf – wie in der Volltextsuche
    import regex as _regex
except ImportError:  # pragma: no cover
    _regex = None

from notex.core.pdfpages import PdfEditError

MATCH_TIMEOUT = 0.5
MAX_HITS = 20000
IBAN_LENGTHS = {"AT": 20, "BE": 16, "CH": 21, "CZ": 24, "DE": 22, "DK": 18, "ES": 24, "FI": 18, "FR": 27, "GB": 22,
                "IE": 22, "IT": 27, "LI": 21, "LU": 20, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "SE": 24}
COUNTRIES = {"AD", "AE", "AT", "AU", "BE", "BG", "BR", "CA", "CH", "CN", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
             "FR", "GB", "GR", "HR", "HU", "IE", "IN", "IS", "IT", "JP", "LI", "LT", "LU", "LV", "MC", "MT", "NL",
             "NO", "NZ", "PL", "PT", "RO", "RU", "SE", "SG", "SI", "SK", "TR", "UA", "US", "ZA"}


# ---- Prüfer ----------------------------------------------------------------------------------------------------
def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def iban_valid(text: str) -> bool:
    value = re.sub(r"\s", "", text).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value):
        return False
    if IBAN_LENGTHS.get(value[:2], len(value)) != len(value):
        return False
    moved = value[4:] + value[:4]
    number = "".join(str(int(ch, 36)) for ch in moved)
    return int(number) % 97 == 1


def luhn_valid(text: str) -> bool:
    number = digits(text)
    if not 13 <= len(number) <= 19 or len(set(number)) == 1:
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def tax_id_valid(text: str) -> bool:
    """Deutsche Steuer-Identifikationsnummer: 11 Ziffern, erste ≠ 0, Prüfziffer ISO 7064 Mod 11,10, und in den ersten
    zehn Ziffern kommt genau eine Ziffer zwei- oder dreimal vor."""
    number = digits(text)
    if len(number) != 11 or number[0] == "0":
        return False
    counts = sorted((number[:10].count(d) for d in set(number[:10])), reverse=True)
    if counts[0] not in (2, 3) or (len(counts) > 1 and counts[1] > 1):
        return False
    product = 10
    for ch in number[:10]:
        total = (int(ch) + product) % 10 or 10
        product = (total * 2) % 11
    check = (11 - product) % 10
    return check == int(number[10])


def bic_valid(text: str) -> bool:
    value = text.upper()
    return bool(re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", value)) and value[4:6] in COUNTRIES and \
        value[6:8] != "00"


def date_valid(text: str) -> bool:
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", text)
    try:
        if match:
            day, month, year = (int(g) for g in match.groups())
            date(year + (2000 if year < 100 else 0), month, day)
            return True
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
        if match:
            date(*(int(g) for g in match.groups()))
            return True
    except ValueError:
        return False
    return False


def phone_valid(text: str) -> bool:
    return 7 <= len(digits(text)) <= 15


def ip_valid(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def plz_valid(text: str) -> bool:
    return "01001" <= text <= "99998"


@dataclass(frozen=True)
class Pattern:
    key: str
    label: str
    regex: str
    check: Callable[[str], bool] | None = None
    example: str = ""
    flags: int = 0


PATTERNS: dict[str, Pattern] = {p.key: p for p in [
    Pattern("email", "E-Mail-Adresse", r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,24}(?![\w-])",
            example="max.muster@example.org"),
    Pattern("phone_de", "Telefon (Deutschland)",
            r"(?<![\w+])(?:\+49|0049|0)[ \-/]?\(?\d{2,5}\)?(?:[ \-/]?\d{2,}){1,4}(?!\d)", phone_valid,
            "0301 234567"),
    Pattern("phone_intl", "Telefon (international)", r"(?<![\w+])\+(?!49)\d{1,3}(?:[ \-]?\(?\d{1,5}\)?){2,6}(?!\d)",
            phone_valid, "+44 20 7946 0958"),
    Pattern("iban", "IBAN (mit Prüfsumme)", r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Z0-9])", iban_valid,
            "DE89 3704 0044 0532 0130 00"),
    Pattern("bic", "BIC", r"(?<![A-Z0-9])[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?(?![A-Z0-9])", bic_valid,
            "COBADEFFXXX"),
    Pattern("card", "Kreditkarte (Luhn)", r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)", luhn_valid, "4111 1111 1111 1111"),
    Pattern("date", "Datum", r"(?<![\d.])(?:\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})(?![\d])", date_valid,
            "24.12.2026"),
    Pattern("tax_id", "Steuer-ID (mit Prüfziffer)", r"(?<!\d)\d{2} ?\d{3} ?\d{3} ?\d{3}(?!\d)", tax_id_valid,
            "86 095 742 719"),
    Pattern("plz", "Postleitzahl", r"(?<![\d\-/.,])\d{5}(?![\d\-/.,]\d)", plz_valid, "10115"),
    Pattern("ip", "IP-Adresse", r"(?<![\w.:])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})"
            r"(?![\w.:])", ip_valid, "192.168.0.10"),
    Pattern("url", "Webadresse", r"(?:https?://|www\.)[^\s<>\"']+[^\s<>\"'.,;:!?)]", example="https://example.org/x"),
]}


@dataclass(frozen=True)
class Match:
    key: str
    start: int
    end: int
    text: str


def compile_custom(expression: str):
    """Eigenen regulären Ausdruck prüfen und übersetzen (Fehler → PdfEditError)."""
    expression = (expression or "").strip()
    if not expression:
        raise PdfEditError("Der Ausdruck ist leer")
    if len(expression) > 1000:
        raise PdfEditError("Der Ausdruck ist zu lang")
    try:
        return (_regex or re).compile(expression)
    except Exception as error:                            # noqa: BLE001 – re.error / regex.error
        raise PdfEditError(f"Ungültiger Ausdruck: {error}") from error


def _finditer(compiled, text: str):
    if _regex is not None and isinstance(compiled, _regex.Pattern):
        return compiled.finditer(text, timeout=MATCH_TIMEOUT)
    return compiled.finditer(text)


def find(text: str, keys: list[str], custom: dict[str, str] | None = None) -> list[Match]:
    """Alle Treffer der gewählten Muster (und eigener Ausdrücke `{Name: Regex}`), ohne Überlappung, nach Position."""
    candidates: list[Match] = []
    for key in keys:
        pattern = PATTERNS.get(key)
        if pattern is None:
            continue
        for m in _finditer((_regex or re).compile(pattern.regex, pattern.flags), text):
            value = m.group(0).strip()
            if value and (pattern.check is None or pattern.check(value)):
                candidates.append(Match(key, m.start(), m.start() + len(m.group(0).rstrip()), value))
    for name, expression in (custom or {}).items():
        compiled = compile_custom(expression)
        try:
            for m in _finditer(compiled, text):
                if m.group(0).strip():
                    candidates.append(Match(f"custom:{name}", m.start(), m.end(), m.group(0)))
        except TimeoutError as error:
            raise PdfEditError(f"Der Ausdruck „{name}“ ist zu langsam (Zeitlimit)") from error
    candidates.sort(key=lambda m: (m.start, -(m.end - m.start)))
    out: list[Match] = []
    for match in candidates:                              # längerer/früherer Treffer gewinnt bei Überlappung
        if out and match.start < out[-1].end:
            continue
        out.append(match)
        if len(out) >= MAX_HITS:
            break
    return out


def label_of(key: str) -> str:
    if key.startswith("custom:"):
        return f"Eigenes Muster „{key[7:]}“"
    pattern = PATTERNS.get(key)
    return pattern.label if pattern else key


# ---- PDF: Treffer mit Rechtecken ----------------------------------------------------------------------------------
@dataclass
class Hit:
    page: int
    key: str
    text: str
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)   # Ansichts-Punkte

    @property
    def label(self) -> str:
        return label_of(self.key)


def find_hits(data: bytes, keys: list[str], custom: dict[str, str] | None = None,
              progress: Callable[[int, int], None] | None = None) -> list[Hit]:
    """Muster im Text jeder Seite suchen; je Treffer die Zeilen-Rechtecke (Ansichts-Punkte) zum Schwärzen."""
    import pypdfium2 as pdfium
    from notex.core import pdfannot as A
    from notex.core.pdfpages import open_reader
    reader = open_reader(data)
    try:
        doc = pdfium.PdfDocument(data)
    except Exception as error:                            # noqa: BLE001
        raise PdfEditError(f"PDF nicht lesbar ({error})") from error
    hits: list[Hit] = []
    try:
        total = len(doc)
        for index in range(total):
            if progress:
                progress(index, total)
            geom = A.geometry_of(reader.pages[index])
            page = doc[index]
            textpage = page.get_textpage()
            count = textpage.count_chars()
            text = textpage.get_text_range(0, count) if count else ""
            for match in find(text, keys, custom):
                rects = _rects(textpage, match.start, min(match.end, count))
                if rects:
                    hits.append(Hit(index, match.key, match.text, [geom.rect_to_view(r) for r in rects]))
            textpage.close()
            page.close()
            if len(hits) >= MAX_HITS:
                break
    finally:
        doc.close()
    return hits


def _rects(textpage, start: int, end: int) -> list[tuple[float, float, float, float]]:
    """Zeichenkästen eines Bereichs zu Zeilen-Rechtecken (PDF-Koordinaten) zusammenfassen."""
    lines: list[list[float]] = []
    for i in range(start, end):
        try:
            x0, y0, x1, y1 = textpage.get_charbox(i)
        except Exception:                                 # noqa: BLE001
            continue
        if x1 - x0 <= 0 and y1 - y0 <= 0:
            continue                                      # erzeugte Zeichen (Leerzeichen/Umbruch) ohne Kasten
        for line in lines:
            if y0 <= line[3] and y1 >= line[1]:          # senkrecht überlappend = dieselbe Zeile (auch „.“, „_“)
                line[0], line[1] = min(line[0], x0), min(line[1], y0)
                line[2], line[3] = max(line[2], x1), max(line[3], y1)
                break
        else:
            lines.append([x0, y0, x1, y1])
    return [tuple(line) for line in lines]
