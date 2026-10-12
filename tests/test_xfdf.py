"""V4: XFDF – Rundlauf mit eigener Datei, Acrobat-ähnliche Datei, Threads bleiben verbunden, sicheres Parsen."""
import pytest

from notex.core import pdfannot as A, pdfcomments as C, pdfdraw as D, pdfstamps as S, xfdf as X
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf

BASE = None


def _base():
    global BASE
    if BASE is None:
        BASE = make_pdf(["Die Biene sammelt Nektar", "Seite zwei", "Seite drei"], size=(600, 800), outline=False)
    return BASE


def _full():
    data = A.add_markup(_base(), 0, "highlight", [(73, 82, 351, 101)], contents="Wichtig")
    data = A.add_markup(data, 0, "strikeout", [(73, 82, 200, 101)])
    data = A.add_note(data, 1, 100, 100, "Notiz auf Seite 2")
    data = A.add_text(data, 1, (100, 200, 300, 240), "Text auf der Seite", 14, "#123456")
    data = D.add_ink(data, 2, [[(50, 50), (100, 80), (150, 60)]], D.Style("#2060c0", 3, opacity=0.5))
    data = D.add_shape(data, 2, "cloud", [(100, 300), (300, 380)], D.Style("#d03030", 2, "#fff3a0"))
    data = D.add_shape(data, 2, "arrow", [(50, 500), (200, 450)], D.Style(dash="dashed"))
    data = D.add_shape(data, 2, "polygon", [(300, 500), (400, 500), (350, 600)], D.Style())
    data = D.add_shape(data, 2, "ellipse", [(400, 100), (500, 150)], D.Style(dash="dotted"))
    data = S.add_stamp(data, 0, (300, 400, 300, 400), S.builtin("approved"))
    data = C.add_reply(data, 0, 0, "Sehe ich auch so", "Mia")
    data = C.set_status(data, 0, 0, "accepted", "Jo")
    return data


def _summary(data):
    return [(c.page, c.kind, c.contents, c.status, [(r.author, r.contents) for r in c.replies], c.nm)
            for c in C.list_comments(data, with_quotes=False)]


def test_roundtrip_own_file():
    original = _full()
    xml = X.export_xfdf(original, "aufsatz.pdf")
    assert xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>') and b'<f href="aufsatz.pdf"/>' in xml
    result = X.import_xfdf(_base(), xml)
    assert_valid_pdf(result.data)
    assert result.skipped == [] and result.added == 12           # 10 Kommentare + Antwort + Status
    assert sorted(_summary(result.data)) == sorted(_summary(original))
    kinds = [a.kind for p in range(3) for a in A.list_annotations(result.data, p)]
    assert sorted(kinds) == sorted(["highlight", "strikeout", "stamp", "note", "text", "ink", "square", "line",
                                    "polygon", "circle"])
    mark = C.list_comments(result.data)[0]
    assert "Biene" in mark.quoted and mark.replies[0].parent_nm == mark.nm   # Thread bleibt verbunden


def test_reimport_does_not_duplicate_but_updates_text():
    original = _full()
    xml = X.export_xfdf(original)
    again = X.import_xfdf(original, xml)
    assert (again.added, again.updated) == (0, 0) and len(_summary(again.data)) == len(_summary(original))
    changed = xml.replace(b"<contents>Wichtig</contents>", b"<contents>Sehr wichtig</contents>")
    updated = X.import_xfdf(original, changed)
    assert updated.updated == 1 and C.list_comments(updated.data)[0].contents == "Sehr wichtig"


ACROBAT = b"""<?xml version="1.0" encoding="UTF-8" ?>
<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">
<annots>
<highlight color="#FFFF00" creationdate="D:20261010120000+02'00'" flags="print" date="D:20261010120500+02'00'"
  name="a1b2c3" page="0" coords="73,717,351,717,73,699,351,699" rect="72,698,352,718" subject="Highlight"
  title="Max Muster" opacity="0.6"><contents-richtext><body xmlns="http://www.w3.org/1999/xhtml"><p>Quelle?</p></body>
  </contents-richtext><popup flags="print,nozoom,norotate" page="0" rect="600,600,780,720" open="no"/></highlight>
<text color="#FFFF00" date="D:20261010121000+02'00'" flags="print,nozoom,norotate" name="r1" page="0"
  rect="72,698,352,718" title="Erika" inreplyto="a1b2c3" replyType="reply" icon="Comment"><contents>Steht in Kapitel 2.</contents></text>
<text color="#FFFF00" date="D:20261010122000+02'00'" flags="hidden,print,nozoom,norotate" name="s1" page="0"
  rect="72,698,352,718" title="Erika" inreplyto="a1b2c3" state="Completed" statemodel="Review"><contents>Erika: Completed</contents></text>
<square color="#E52237" width="2" style="cloudy" intensity="1" interior-color="#FFFFFF" name="sq1" page="1"
  rect="90,90,310,210" fringe="9,9,9,9" title="Max Muster"><contents>Hier fehlt was</contents></square>
<line color="#E52237" width="1.5" head="None" tail="OpenArrow" start="100,300" end="250,400" name="l1" page="1" rect="95,295,255,405"/>
<ink color="#0000FF" width="2" name="i1" page="2" rect="45,45,205,105"><inklist><gesture>50,50;100,100;200,60</gesture>
  <gesture>60,90;120,95</gesture></inklist></ink>
<freetext name="f1" page="2" rect="100,500,300,540" width="0"><contents>Toll!</contents>
  <defaultappearance>/Helv 16 Tf 0 0 1 rg</defaultappearance></freetext>
<stamp name="st1" page="2" rect="300,600,450,650" icon="Approved" title="Max Muster"/>
<fileattachment name="fa1" page="0" rect="10,10,30,30" file="geheim.exe"/>
<sound name="snd" page="0" rect="10,10,30,30"/>
</annots>
<f href="Bericht.pdf"/>
<ids original="ABC" modified="DEF"/>
</xfdf>"""


def test_acrobat_like_file():
    result = X.import_xfdf(_base(), ACROBAT)
    assert_valid_pdf(result.data)
    assert result.added == 8 and sorted(result.skipped) == ["Datei-Anhang", "Klang"]
    comments = C.list_comments(result.data)
    by_nm = {c.nm: c for c in comments}
    mark = by_nm["a1b2c3"]
    assert mark.author == "Max Muster" and mark.contents == "Quelle?" and mark.status == "done"
    assert [(r.author, r.contents) for r in mark.replies] == [("Erika", "Steht in Kapitel 2.")]
    assert "Biene" in mark.quoted
    assert by_nm["sq1"].kind == "square" and by_nm["l1"].kind == "line" and by_nm["i1"].kind == "ink"
    assert by_nm["f1"].kind == "text" and by_nm["f1"].contents == "Toll!"
    assert by_nm["st1"].kind == "stamp" and by_nm["st1"].contents == "GENEHMIGT"
    from pypdf import PdfReader
    import io
    square = next(a.get_object() for a in PdfReader(io.BytesIO(result.data)).pages[1]["/Annots"]
                  if a.get_object().get("/NM") == "sq1")
    assert square["/BE"]["/S"] == "/C" and "/AP" in square
    assert "Erfolg" not in result.summary() and "2 nicht übernommen" in result.summary()


@pytest.mark.parametrize("raw, message", [
    (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><xfdf><annots/></xfdf>', "Entities"),
    (b'<!DOCTYPE xfdf SYSTEM "file:///etc/passwd"><xfdf>&xxe;</xfdf>', "XFDF"),
    (b"<html><body/></html>", "keine XFDF"),
    (b"<xfdf><annots>", "gültige"),
])
def test_unsafe_or_broken_xml_rejected(raw, message):
    with pytest.raises(X.XfdfError, match=message):
        X.import_xfdf(_base(), raw)


def test_size_limit(monkeypatch):
    monkeypatch.setattr(X, "MAX_FILE", 100)
    with pytest.raises(X.XfdfError, match="zu groß"):
        X.import_xfdf(_base(), b"<xfdf>" + b" " * 200 + b"</xfdf>")


def test_bad_entries_are_skipped_not_fatal():
    raw = b"""<xfdf xmlns="http://ns.adobe.com/xfdf/"><annots>
    <square name="a" page="7" rect="0,0,10,10"/>
    <square name="b" page="0" rect="0,0,nan,10"/>
    <square name="c" page="0" rect="0,0,1e30,10"/>
    <highlight name="d" page="0" rect="0,0,10,10"/>
    <text name="e" page="0" rect="0,0,10,10" inreplyto="gibt-es-nicht"><contents>x</contents></text>
    <text name="ok" page="0" rect="100,700,120,720" color="kein-farbwert"><contents>gut</contents></text>
    </annots></xfdf>"""
    result = X.import_xfdf(_base(), raw)
    assert result.added == 1 and len(result.skipped) == 5
    (note,) = C.list_comments(result.data)
    assert note.contents == "gut" and note.color == "#ffd400"
