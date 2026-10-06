"""draw.io-Dateien (notex/core/diagram/drawio.py) lesen und schreiben – ohne Qt."""
import base64
import zlib

import pytest

from notex.core.diagram import drawio, render
from notex.core.diagram.model import Diagram, End, route

CLASS_FILE = """<mxfile host="app.diagrams.net" type="device">
  <diagram id="a" name="Klassen">
    <mxGraphModel dx="1000" dy="600" grid="1"><root>
      <mxCell id="0" /><mxCell id="1" parent="0" />
      <mxCell id="c1" value="Person" style="swimlane;fontStyle=3;childLayout=stackLayout;startSize=26;html=1;"
              vertex="1" parent="1"><mxGeometry x="160" y="80" width="160" height="86" as="geometry" /></mxCell>
      <mxCell id="c1a" value="- name: String" style="text;strokeColor=none;fillColor=none;html=1;" vertex="1"
              parent="c1"><mxGeometry y="26" width="160" height="26" as="geometry" /></mxCell>
      <mxCell id="c1l" value="" style="line;strokeWidth=1;fillColor=none;" vertex="1" parent="c1">
              <mxGeometry y="52" width="160" height="8" as="geometry" /></mxCell>
      <mxCell id="c1m" value="+ gruessen(): void" style="text;strokeColor=none;fillColor=none;html=1;" vertex="1"
              parent="c1"><mxGeometry y="60" width="160" height="26" as="geometry" /></mxCell>
      <mxCell id="c2" value="&lt;p&gt;&lt;b&gt;Schüler&lt;/b&gt;&lt;/p&gt;&lt;hr size=&quot;1&quot;/&gt;&lt;p&gt;- klasse: String&lt;/p&gt;&lt;hr/&gt;&lt;p&gt;&lt;/p&gt;"
              style="verticalAlign=top;align=left;overflow=fill;html=1;" vertex="1" parent="1">
              <mxGeometry x="160" y="260" width="160" height="80" as="geometry" /></mxCell>
      <mxCell id="e1" value="" style="endArrow=block;endFill=0;html=1;exitX=0.5;exitY=0;entryX=0.5;entryY=1;"
              edge="1" parent="1" source="c2" target="c1"><mxGeometry relative="1" as="geometry" /></mxCell>
      <mxCell id="u1" value="Schule" style="ellipse;whiteSpace=wrap;html=1;fillColor=#DAE8FC;strokeColor=#6c8ebf;"
              vertex="1" parent="1"><mxGeometry x="420" y="90" width="120" height="60" as="geometry" /></mxCell>
      <mxCell id="e2" value="hat" style="endArrow=none;startArrow=diamondThin;startFill=1;edgeStyle=orthogonalEdgeStyle;dashed=1;"
              edge="1" parent="1" source="u1" target="c1"><mxGeometry relative="1" as="geometry" /></mxCell>
      <mxCell id="e2s" value="1" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e2">
              <mxGeometry x="-1" relative="1" as="geometry" /></mxCell>
      <mxCell id="e2t" value="*" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e2">
              <mxGeometry x="1" relative="1" as="geometry" /></mxCell>
      <mxCell id="a1" value="Akteur" style="shape=umlActor;html=1;" vertex="1" parent="1">
              <mxGeometry x="40" y="100" width="30" height="60" as="geometry" /></mxCell>
      <mxCell id="f1" value="" style="endArrow=classic;html=1;" edge="1" parent="1" source="a1">
              <mxGeometry relative="1" as="geometry"><mxPoint x="100" y="300" as="targetPoint" /></mxGeometry></mxCell>
    </root></mxGraphModel>
  </diagram>
</mxfile>"""


def _by_text(d: Diagram, start: str):
    return next(s for s in d.shapes if s.text.startswith(start))


def test_import_uml_classes_edges_and_labels():
    d = drawio.read(CLASS_FILE)
    person = _by_text(d, "{abstract}")
    assert person.kind == "class" and person.text == "{abstract}\nPerson\n--\n- name: String\n--\n+ gruessen(): void"
    pupil = _by_text(d, "Schüler")
    assert pupil.kind == "class" and pupil.class_sections() == ["Schüler", "- klasse: String", ""]
    school = _by_text(d, "Schule")
    assert school.kind == "ellipse" and school.fill == "#dae8fc" and school.stroke == "#6c8ebf"
    assert _by_text(d, "Akteur").kind == "actor"
    assert len(d.shapes) == 4                                    # Klassenzeilen sind keine eigenen Formen
    inherit, compose, free = d.connectors
    assert (inherit.source.shape, inherit.source.port) == (pupil.id, "n2")
    assert (inherit.target.shape, inherit.target.port) == (person.id, "s2")
    assert inherit.end_arrow == "triangle" and inherit.route == "straight"
    assert compose.start_arrow == "diamond_filled" and compose.end_arrow == "none" and compose.dashed
    assert compose.route == "orthogonal" and compose.source.port is None
    assert (compose.label, compose.start_label, compose.end_label) == ("hat", "1", "*")
    assert free.target.shape is None and free.end_arrow == "arrow"
    box = d.bounds()
    assert box[0] == pytest.approx(6) and box[1] == pytest.approx(6)     # an den Inhalt angepasst
    assert render.primitives(d)


def test_child_coordinates_are_absolute_and_unknown_shapes_become_rects():
    text = """<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="g" value="" style="group" vertex="1" parent="1"><mxGeometry x="100" y="50" width="200" height="100" as="geometry"/></mxCell>
      <mxCell id="a" value="A" style="shape=mxgraph.aws4.lambda;" vertex="1" parent="g"><mxGeometry x="10" y="20" width="40" height="30" as="geometry"/></mxCell>
      <mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="40" height="30" as="geometry"/></mxCell>
      <mxCell id="t" value="Hinweis" style="text;html=1;" vertex="1" parent="1"><mxGeometry x="0" y="200" width="60" height="20" as="geometry"/></mxCell>
      <mxCell id="r" value="?" style="rhombus;" vertex="1" parent="1"><mxGeometry x="300" y="0" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="s" value="" style="ellipse;fillColor=#000000;" vertex="1" parent="1"><mxGeometry x="300" y="100" width="20" height="20" as="geometry"/></mxCell>
      <mxCell id="d" value="DB" style="shape=cylinder3;" vertex="1" parent="1"><mxGeometry x="300" y="200" width="40" height="60" as="geometry"/></mxCell>
    </root></mxGraphModel>"""
    d = drawio.read(text)
    kinds = {s.text: s.kind for s in d.shapes}
    assert kinds == {"A": "rect", "B": "rounded", "Hinweis": "text", "?": "diamond", "": "circle", "DB": "database"}
    a, b = _by_text(d, "A"), _by_text(d, "B")
    assert (a.x - b.x, a.y - b.y) == pytest.approx((110, 70))           # Gruppe (100,50) + (10,20)
    assert _by_text(d, "Hinweis").stroke == "none"


def test_compressed_pages_svg_and_page_choice():
    model = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="x" value="Zwei" ' \
            'style="rounded=0;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="80" height="40" ' \
            'as="geometry"/></mxCell></root></mxGraphModel>'
    packed = drawio.deflate(model)
    first = CLASS_FILE.split('name="Klassen">')[1].split("</diagram>")[0]
    text = f'<mxfile><diagram name="Eins">{first}</diagram><diagram name="Zwei">{packed}</diagram></mxfile>'
    assert drawio.page_names(text) == ["Eins", "Zwei"]
    assert drawio.read(text, 1).shapes[0].text == "Zwei"
    with pytest.raises(drawio.DrawioError):
        drawio.read(text, 5)
    from xml.sax.saxutils import quoteattr
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" content={quoteattr(text)}><g/></svg>'
    assert drawio.page_names(svg) == ["Eins", "Zwei"]
    assert drawio._inflate(packed) == model


def test_roundtrip_keeps_kinds_texts_arrows_ports_and_labels():
    d = Diagram(500, 400)
    kinds = ["class", "rect", "rounded", "ellipse", "diamond", "circle", "endstate", "note", "text", "actor",
             "package", "database", "parallelogram"]
    shapes = [d.add_shape(k, 20 + (i % 4) * 120, 20 + (i // 4) * 110) for i, k in enumerate(kinds)]
    shapes[0].text = "{abstract}\nTier\n--\n- name: String\n- alter: int\n--\n+ laut(): void"
    shapes[1].text = "Zeile 1\nZeile <2> & 3"
    shapes[1].bold = True
    shapes[1].fill = "#ffcc00"
    shapes[3].dashed = True
    d.add_shape("class", 20, 360, 120, 30, "NurName")                     # Klasse ohne Abschnitte bleibt so
    arrows = ["arrow", "open", "triangle", "diamond", "diamond_filled", "circle", "none"]
    for i, arrow in enumerate(arrows):
        d.connect(End(shapes[i].id, "e2"), End(shapes[i + 1].id, "w2"), "Linie", end_arrow=arrow,
                  start_arrow=arrows[-1 - i], dashed=i % 2 == 0, label=f"L{i}", start_label="1", end_label="*",
                  route="orthogonal" if i % 2 else "straight")
    d.connect(End(shapes[2].id), End(None, None, 480, 380), "Pfeil")
    for compressed in (False, True):
        back = drawio.read(drawio.write(d, compressed=compressed))
        assert [(s.kind, s.text) for s in back.shapes] == [(s.kind, s.text) for s in d.shapes]
        b = back.shapes[1]
        assert b.bold and b.fill == "#ffcc00" and back.shapes[3].dashed
        for old, new in zip(d.connectors, back.connectors):
            assert (new.start_arrow, new.end_arrow, new.dashed, new.route, new.label, new.start_label,
                    new.end_label) == (old.start_arrow, old.end_arrow, old.dashed, old.route, old.label,
                                       old.start_label, old.end_label)
        ports = [(c.source.port, c.target.port) for c in back.connectors[:-1]]
        expected = [(c.source.port, c.target.port) for c in d.connectors[:-1]]
        assert ports == expected
        loose = back.connectors[-1]
        assert loose.source.port is None and loose.target.shape is None
        old_end = route(d, d.connectors[-1])[-1]
        offset = (back.shapes[0].x - d.shapes[0].x, back.shapes[0].y - d.shapes[0].y)
        assert (loose.target.x - offset[0], loose.target.y - offset[1]) == pytest.approx(old_end)


def test_rejects_entities_garbage_and_bombs():
    with pytest.raises(drawio.DrawioError, match="ENTITY"):
        drawio.pages('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><mxfile>&a;</mxfile>')
    with pytest.raises(drawio.DrawioError, match="XML"):
        drawio.pages("<mxfile><diagram>")
    with pytest.raises(drawio.DrawioError, match="mxfile"):
        drawio.pages("<html/>")
    with pytest.raises(drawio.DrawioError, match="keine Seite"):
        drawio.pages("<mxfile/>")
    with pytest.raises(drawio.DrawioError):
        drawio.pages("<mxfile><diagram>%%%nicht base64%%%</diagram></mxfile>")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    bomb = base64.b64encode(compressor.compress(b"a" * (drawio.MAX_INFLATED + 10)) + compressor.flush()).decode()
    with pytest.raises(drawio.DrawioError, match="groß"):
        drawio.pages(f"<mxfile><diagram>{bomb}</diagram></mxfile>")
    with pytest.raises(drawio.DrawioError, match="nichts"):
        drawio.read('<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel>')


def test_style_and_text_helpers():
    assert drawio.parse_style("ellipse;fillColor=#fff;;html=1") == {"ellipse": "", "fillColor": "#fff", "html": "1"}
    assert drawio.plain_text("a<br>b&amp;c<div>d</div>", True) == "a\nb&c\nd"
    assert drawio.plain_text("<b>x</b>", False) == "<b>x</b>"
    assert drawio._color("#ABC", "#000000") == "#aabbcc" and drawio._color("red", "#000000") == "#000000"
    assert drawio._color("none", "#000000") == "none"


# ---- andere Exportformate von draw.io ------------------------------------------------------------------------
def _small_file() -> str:
    d = Diagram(200, 100)
    a = d.add_shape("rect", 10, 10, 80, 40, "A")
    b = d.add_shape("rect", 10, 100, 80, 40, "B")
    d.connect(End(a.id, "s2"), End(b.id, "n2"), "Pfeil")
    return drawio.write(d)


def _png(chunks: list[tuple[bytes, bytes]]) -> bytes:
    import struct
    out = b"\x89PNG\r\n\x1a\n"
    for kind, body in chunks + [(b"IEND", b"")]:
        out += struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return out


def test_svg_export_with_plain_doctype_is_read():
    from xml.sax.saxutils import quoteattr
    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
           '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
           f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" content={quoteattr(_small_file())}><g/></svg>')
    d = drawio.read(drawio.load(svg.encode("utf-8")))
    assert {s.text for s in d.shapes} == {"A", "B"} and len(d.connectors) == 1


def test_png_export_text_chunks():
    from urllib.parse import quote
    xml = quote(_small_file()).encode("ascii")
    plain = _png([(b"IHDR", b"\0" * 13), (b"tEXt", b"Software\0draw.io"), (b"tEXt", b"mxfile\0" + xml)])
    assert len(drawio.read(drawio.load(plain)).shapes) == 2
    packed = _png([(b"IHDR", b"\0" * 13), (b"zTXt", b"mxfile\0\0" + zlib.compress(xml))])
    assert len(drawio.read(drawio.load(packed)).shapes) == 2
    international = _png([(b"iTXt", b"mxfile\0\x01\0\0\0" + zlib.compress(xml))])
    assert len(drawio.read(drawio.load(international)).shapes) == 2
    with pytest.raises(drawio.DrawioError, match="PNG ohne"):
        drawio.load(_png([(b"IHDR", b"\0" * 13)]))
    bomb = _png([(b"zTXt", b"mxfile\0\0" + zlib.compress(b"a" * (drawio.MAX_INFLATED + 10)))])
    with pytest.raises(drawio.DrawioError, match="groß"):
        drawio.load(bomb)


def test_html_export_and_bom():
    import html as html_mod
    import json
    config = html_mod.escape(json.dumps({"highlight": "#0000ff", "xml": _small_file()}), quote=True)
    page = f'<!DOCTYPE html><html><body><div class="mxgraph" data-mxgraph="{config}"></div></body></html>'
    assert len(drawio.read(drawio.load(page.encode("utf-8"))).shapes) == 2
    with pytest.raises(drawio.DrawioError, match="HTML"):
        drawio.load(b'<html><div data-mxgraph="kaputt"></div></html>')
    assert drawio.load("\ufeff<mxfile/>".encode("utf-8")) == "<mxfile/>"
