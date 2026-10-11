"""Eingebettete Schrift für alles, was fckNotes selbst auf PDF-Seiten schreibt (Qt-frei).

Mitgeliefert ist Noto Sans (SIL OFL 1.1) in vier Schnitten. Statt einer der 14 Standardschriften (Helvetica, WinAnsi)
wird eine **Untermenge** eingebettet – nur die benutzten Zeichen – als Type0-Schrift (CIDFontType2, Identity-H) mit
ToUnicode-Tabelle. Dadurch:
- sieht der Text in jedem Betrachter und auf Windows wie Linux identisch aus,
- geht Unicode über WinAnsi hinaus (Ł, Ş, Ω, →, Kyrillisch, Griechisch …),
- bleibt der Text kopier- und durchsuchbar.

Kodiert wird mit den Glyph-Nummern der Originalschrift (`retain_gids`), deshalb hängt die Kodierung nicht von der
Untermenge ab und lässt sich vorher berechnen. Zeichen, die die Schrift nicht hat (z. B. Emoji), werden zu „?“.

Ausnahme: Formularfelder behalten Helvetica (siehe `pdfforms`). Ihre Werte zeichnen fremde Betrachter beim Tippen
neu – mit einer eingebetteten Untermenge fehlten dort Zeichen, die erst später eingegeben werden.
"""
from __future__ import annotations

import hashlib
import io
import unicodedata
import zlib
from functools import lru_cache
from pathlib import Path

from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, StreamObject,
                           TextStringObject)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "noto"
FILES = {(False, False): "NotoSans-Regular.ttf", (True, False): "NotoSans-Bold.ttf",
         (False, True): "NotoSans-Italic.ttf", (True, True): "NotoSans-BoldItalic.ttf"}
RESOURCE_NAMES = {(False, False): "/FnR", (True, False): "/FnB", (False, True): "/FnI", (True, True): "/FnBI"}
FALLBACK = "?"


class Face:
    """Ein Schnitt: Breiten, Glyph-Nummern, Kennzahlen. Wird je Schnitt einmal geladen (`face()`)."""

    def __init__(self, bold: bool = False, italic: bool = False) -> None:
        from fontTools.ttLib import TTFont
        self.key = (bool(bold), bool(italic))
        self.path = FONT_DIR / FILES[self.key]
        font = TTFont(str(self.path), lazy=True)
        self.units = font["head"].unitsPerEm
        self.cmap: dict[int, str] = dict(font.getBestCmap() or {})
        order = font.getGlyphOrder()
        self.gid = {name: index for index, name in enumerate(order)}
        metrics = font["hmtx"].metrics
        self._advance = {name: metrics[name][0] for name in self.cmap.values() if name in metrics}
        self._advance_notdef = metrics[order[0]][0]
        hhea, os2, head, post = font["hhea"], font["OS/2"], font["head"], font["post"]
        scale = 1000 / self.units
        self.ascent = round(hhea.ascent * scale)
        self.descent = round(hhea.descent * scale)
        self.cap_height = round(getattr(os2, "sCapHeight", hhea.ascent * 0.7) * scale)
        self.bbox = [round(v * scale) for v in (head.xMin, head.yMin, head.xMax, head.yMax)]
        self.italic_angle = float(post.italicAngle)
        self.postscript = font["name"].getDebugName(6) or self.path.stem
        font.close()

    # ---- Text ------------------------------------------------------------------------------------------------
    def has(self, ch: str) -> bool:
        return ord(ch) in self.cmap

    def normalize(self, text: str) -> str:
        """Steuerzeichen raus, Tabs → Leerzeichen, nicht vorhandene Zeichen → „?“ (vorher NFC zusammensetzen)."""
        out = []
        for ch in unicodedata.normalize("NFC", text.replace("\t", "    ")):
            if ch in "\r\n":
                out.append(ch)
            elif unicodedata.category(ch) in ("Cc", "Cf"):
                continue
            else:
                out.append(ch if self.has(ch) else FALLBACK)
        return "".join(out)

    def advance(self, ch: str) -> float:
        """Breite eines Zeichens in 1/1000 em."""
        name = self.cmap.get(ord(ch)) or self.cmap.get(ord(FALLBACK))
        units = self._advance.get(name, self._advance_notdef) if name else self._advance_notdef
        return units * 1000 / self.units

    def width(self, text: str, size: float) -> float:
        return sum(self.advance(ch) for ch in self.normalize(text)) * size / 1000

    def glyph(self, ch: str) -> int:
        name = self.cmap.get(ord(ch)) or self.cmap.get(ord(FALLBACK))
        return self.gid.get(name, 0) if name else 0

    def encode(self, text: str) -> str:
        """Hex-String für `Tj` (Identity-H, je Zeichen vier Hex-Ziffern = Glyph-Nummer)."""
        return "<" + "".join(f"{self.glyph(ch):04X}" for ch in self.normalize(text) if ch not in "\r\n") + ">"


@lru_cache(maxsize=None)
def face(bold: bool = False, italic: bool = False) -> Face:
    return Face(bold, italic)


def width(text: str, size: float, bold: bool = False, italic: bool = False) -> float:
    return face(bold, italic).width(text, size)


def wrap(text: str, size: float, max_width: float, bold: bool = False, italic: bool = False) -> list[str]:
    """Zeilenumbruch nach Wörtern (überlange Wörter werden hart getrennt); Absätze aus dem Text bleiben."""
    f = face(bold, italic)
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if f.width(candidate, size) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
            while f.width(current, size) > max_width and len(current) > 1:
                cut = len(current)
                while cut > 1 and f.width(current[:cut], size) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines


# ---- Einbetten -------------------------------------------------------------------------------------------------
class FontUsage:
    """Sammelt, welche Zeichen in welchem Schnitt benutzt werden, und baut danach die Ressourcen.

        use = FontUsage()
        ops.append(f"BT {use.name()} 12 Tf {use.encode('Hallo')} Tj ET")
        resources = use.resources(writer)
    """

    def __init__(self) -> None:
        self.chars: dict[tuple[bool, bool], set[str]] = {}

    def name(self, bold: bool = False, italic: bool = False) -> str:
        key = (bool(bold), bool(italic))
        self.chars.setdefault(key, set())
        return RESOURCE_NAMES[key]

    def encode(self, text: str, bold: bool = False, italic: bool = False) -> str:
        key = (bool(bold), bool(italic))
        f = face(*key)
        clean = f.normalize(text)
        self.chars.setdefault(key, set()).update(ch for ch in clean if ch not in "\r\n")
        return f.encode(clean)

    def font_dict(self, writer) -> DictionaryObject:
        fonts = DictionaryObject()
        for key, chars in sorted(self.chars.items()):
            fonts[NameObject(RESOURCE_NAMES[key])] = embed(writer, face(*key), chars)
        return fonts

    def resources(self, writer, extra: DictionaryObject | None = None) -> DictionaryObject:
        res = DictionaryObject(extra or {})
        fonts = self.font_dict(writer)
        if fonts:
            existing = res.get("/Font")
            merged = DictionaryObject(existing) if existing is not None else DictionaryObject()
            merged.update(fonts)
            res[NameObject("/Font")] = merged
        return res


def _flate_stream(data: bytes, extra: dict | None = None) -> StreamObject:
    stream = StreamObject()
    stream._data = zlib.compress(data, 9)
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    stream[NameObject("/Length")] = NumberObject(len(stream._data))
    for key, value in (extra or {}).items():
        stream[NameObject(key)] = value
    return stream


@lru_cache(maxsize=64)
def _subset_bytes(path: str, chars: frozenset[str]) -> bytes:
    """Untermenge der Schrift mit den Zeichen (Glyph-Nummern bleiben erhalten); deterministisch."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    options = subset.Options()
    options.retain_gids = True
    options.notdef_outline = True
    options.hinting = False
    options.layout_features = []
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6]
    options.drop_tables += ["GSUB", "GPOS", "GDEF", "DSIG", "STAT", "meta"]
    font = TTFont(path, recalcTimestamp=False)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted({ord(c) for c in chars} | {ord(FALLBACK), 0x20}))
    subsetter.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _tag(f: Face, chars: frozenset[str]) -> str:
    digest = hashlib.sha1((f.postscript + "".join(sorted(chars))).encode("utf-8")).digest()
    return "".join(chr(ord("A") + b % 26) for b in digest[:6])


def _to_unicode(f: Face, chars: frozenset[str]) -> bytes:
    pairs = sorted({(f.glyph(ch), ch) for ch in chars if f.has(ch)})
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    for start in range(0, len(pairs), 100):
        chunk = pairs[start:start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for gid, ch in chunk:
            lines.append(f"<{gid:04X}> <{ch.encode('utf-16-be').hex().upper()}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def embed(writer, f: Face, chars) -> object:
    """Type0-Schrift (Untermenge) in den Writer schreiben; liefert die indirekte Referenz."""
    chars = frozenset(ch for ch in chars if f.has(ch)) | frozenset({FALLBACK, " "})
    base = f"{_tag(f, chars)}+{f.postscript}"
    font_file = _flate_stream(_subset_bytes(str(f.path), chars))
    font_file[NameObject("/Length1")] = NumberObject(len(_subset_bytes(str(f.path), chars)))
    flags = 32 | (64 if f.key[1] else 0)
    descriptor = DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"), NameObject("/FontName"): NameObject("/" + base),
        NameObject("/Flags"): NumberObject(flags),
        NameObject("/FontBBox"): ArrayObject([NumberObject(v) for v in f.bbox]),
        NameObject("/ItalicAngle"): FloatObject(f.italic_angle), NameObject("/Ascent"): NumberObject(f.ascent),
        NameObject("/Descent"): NumberObject(f.descent), NameObject("/CapHeight"): NumberObject(f.cap_height),
        NameObject("/StemV"): NumberObject(120 if f.key[0] else 80),
        NameObject("/FontFile2"): writer._add_object(font_file)})
    widths = ArrayObject()
    for gid, ch in sorted({(f.glyph(ch), ch) for ch in chars}):
        widths += [NumberObject(gid), ArrayObject([NumberObject(round(f.advance(ch)))])]
    cid_font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/CIDFontType2"),
        NameObject("/BaseFont"): NameObject("/" + base),
        NameObject("/CIDSystemInfo"): DictionaryObject({
            NameObject("/Registry"): TextStringObject("Adobe"), NameObject("/Ordering"): TextStringObject("Identity"),
            NameObject("/Supplement"): NumberObject(0)}),
        NameObject("/FontDescriptor"): writer._add_object(descriptor), NameObject("/DW"): NumberObject(1000),
        NameObject("/W"): widths, NameObject("/CIDToGIDMap"): NameObject("/Identity")})
    type0 = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/" + base), NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([writer._add_object(cid_font)]),
        NameObject("/ToUnicode"): writer._add_object(_flate_stream(_to_unicode(f, chars)))})
    return writer._add_object(type0)
