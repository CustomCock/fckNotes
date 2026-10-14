"""Text in Sätze zerlegen (Qt-frei, Block V11) – fürs Vorlesen Satz für Satz mit Hervorhebung.

Ein Satz endet an „.“, „!“, „?“, „…“ (auch mit schließenden Anführungszeichen/Klammern) vor Leerraum und einem
Satzanfang – nicht nach Abkürzungen („z. B.“, „Dr.“, „usw.“), Initialen („A. Meier“), Ordnungszahlen („3. Oktober“)
oder Dezimalzahlen („3.5“). Ein Absatz (Leerzeile) und eine Zeile ohne Satzzeichen (Überschrift, Listenpunkt) enden
ebenfalls. Sehr lange Sätze werden an Satzzeichen oder Leerzeichen geteilt, damit Sprachausgaben nicht stocken.

`split` liefert (Anfang, Ende) im Originaltext – damit lässt sich der gerade gelesene Satz genau markieren.
"""
from __future__ import annotations

import re

ABBREVIATIONS = {
    "z", "b", "d", "h", "u", "a", "bzw", "usw", "ca", "dr", "prof", "nr", "s", "vgl", "evtl", "ggf", "inkl", "etc",
    "e", "g", "i", "mr", "mrs", "ms", "st", "str", "tel", "abs", "art", "bsp", "hr", "fr", "frl", "jh", "jhd", "mio",
    "mrd", "max", "min", "zzgl", "abzgl", "gem", "geb", "sog", "bspw", "allg", "insb", "v", "chr", "ff", "f", "o",
    "jan", "feb", "mär", "apr", "jun", "jul", "aug", "sep", "sept", "okt", "nov", "dez", "vs", "no", "fig", "approx",
    "ing", "dipl", "mag", "med", "jur", "phil", "rer", "nat", "co", "inc", "ltd", "gmbh",
}
MAX_SENTENCE = 400                                        # Zeichen; längere werden geteilt
_END = re.compile(r"[.!?…]+[\"'»«“”’)\]]*")
_WORD_BEFORE = re.compile(r"([A-Za-zÄÖÜäöüß]+)\.?$")


def _is_boundary(text: str, end_match: re.Match) -> bool:
    start, end = end_match.start(), end_match.end()
    rest = text[end:]
    if rest and not rest[0].isspace():
        return False                                      # „3.5“, „www.example.org“, „z.B.x“
    following = rest.lstrip()
    if not following:
        return True
    punct = end_match.group(0)
    if punct.startswith(".") and len(punct.rstrip("\"'»«“”’)]")) == 1:
        before = text[:start]
        word = _WORD_BEFORE.search(before)
        if word is not None:
            token = word.group(1)
            if token.lower() in ABBREVIATIONS or len(token) == 1:
                return False                              # Abkürzung oder Initiale
        if re.search(r"(?:^|\n)[ \t]*\d{1,3}$", before):
            return False                                  # „1. Erstens“: Listennummer am Zeilenanfang
        if re.search(r"(?:^|\s)\d{1,3}$", before) and following[:1].isalnum() and not following[:1].isupper():
            return False                                  # „am 3. oktober“ (Ordnungszahl, kleiner Folgetext)
        if re.search(r"(?:^|\s)\d{1,2}$", before) and following[:1].isupper() and \
                re.match(r"[A-ZÄÖÜ][a-zäöüß]+", following) and _month_or_noun_after_number(following):
            return False                                  # „3. Oktober“, „2. Auflage“
    first = following[:1]
    return first.isupper() or first.isdigit() or first in "\"'»«“„([" or not first.isalpha()


def _month_or_noun_after_number(following: str) -> bool:
    word = re.match(r"[A-Za-zÄÖÜäöüß]+", following).group(0).lower()
    return word[:3] in {"jan", "feb", "mär", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "dez"} or \
        word in {"auflage", "klasse", "stock", "platz", "mal", "kapitel", "jahrhundert", "liga", "runde"}


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def _limit(text: str, span: tuple[int, int]) -> list[tuple[int, int]]:
    start, end = span
    out = []
    while end - start > MAX_SENTENCE:
        window = text[start:start + MAX_SENTENCE]
        cut = max(window.rfind(sep) for sep in (", ", "; ", ": ", " – ", " - "))
        cut = cut + 1 if cut > MAX_SENTENCE // 3 else window.rfind(" ")
        if cut <= 0:
            cut = MAX_SENTENCE
        piece = _trim(text, start, start + cut)
        if piece:
            out.append(piece)
        start += cut
    piece = _trim(text, start, end)
    if piece:
        out.append(piece)
    return out


def split(text: str) -> list[tuple[int, int]]:
    """Sätze als (Anfang, Ende) im Text, ohne umgebenden Leerraum."""
    spans: list[tuple[int, int]] = []
    for block in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text):   # Absätze (durch Leerzeilen getrennt)
        lines = list(re.finditer(r"[^\n]+", block.group(0)))
        base = block.start()
        segment_start = base
        for index, line in enumerate(lines):
            line_text = line.group(0).rstrip()
            line_end = base + line.end()
            is_last = index == len(lines) - 1
            ends_open = not re.search(r"[.!?…:;,\"'»«“”’)\]-]$", line_text)
            next_line = lines[index + 1].group(0).lstrip() if not is_last else ""
            if is_last or (ends_open and (re.match(r"^(?:[-*•–]|\d+[.)])\s", next_line)
                                          or re.match(r"^(?:[-*•–]|#+)\s", line_text.lstrip())
                                          or len(line_text.strip()) < 60 and next_line[:1].isupper())):
                spans.extend(_sentences_in(text, segment_start, line_end))
                segment_start = line_end
    out: list[tuple[int, int]] = []
    for span in spans:
        out.extend(_limit(text, span))
    return out


def _sentences_in(text: str, start: int, end: int) -> list[tuple[int, int]]:
    out = []
    cursor = start
    chunk = text[start:end]
    for match in _END.finditer(chunk):
        absolute = re.compile(re.escape(match.group(0)))
        match_abs = absolute.match(text, start + match.start())
        if match_abs is None or not _is_boundary(text[:end], match_abs):
            continue
        piece = _trim(text, cursor, start + match.end())
        if piece:
            out.append(piece)
        cursor = start + match.end()
    piece = _trim(text, cursor, end)
    if piece:
        out.append(piece)
    return out


def sentence_at(spans: list[tuple[int, int]], position: int) -> int:
    """Index des Satzes, in dem (oder vor dem) `position` liegt – Start fürs Vorlesen ab Cursor."""
    for index, (start, end) in enumerate(spans):
        if position < end:
            return index
    return max(0, len(spans) - 1)
