"""Diagramm-Kern (notex/core/diagram, pdfdiagram) – Modell, Andockpunkte, Routing, Zeichnen, PDF – ohne Qt."""
import logging
import math

import pytest
from pypdf.generic import ContentStream

from notex.core import pdfdiagram, pdfforms, pdfobjects
from notex.core.diagram import pdf as dpdf, render, templates
from notex.core.diagram.model import Diagram, End, Shape, boundary_point, midpoint, nearest_port, orthogonal, route
from notex.core.pdfpages import PdfEditError
from pdfhelp import make_pdf, reader

logging.getLogger("pypdf").setLevel(logging.ERROR)


def _uml():
    d = Diagram(480, 330)
    a = d.add_shape("class", 20, 20, 160, 100, "{abstract}\nPerson\n--\n- name: String\n--\n+ gruessen(): void")
    b = d.add_shape("class", 20, 200, 160, 90, "Schüler\n--\n- klasse: String\n--\n")
    c = d.add_shape("class", 290, 20, 160, 60, "Schule")
    d.connect(End(b.id, "n2"), End(a.id, "s2"), "Vererbung")
    d.connect(End(c.id, "w2"), End(a.id, "e2"), "Komposition", start_label="1", end_label="*", label="hat")
    return d, a, b, c


# ---- Modell ------------------------------------------------------------------------------------------------------
def test_ports_by_kind():
    rect = Shape("r", "rect", 0, 0, 100, 40)
    names = [p[0] for p in rect.ports()]
    assert len(names) == 12 and rect.port("e2") == (100, 20) and rect.port("n1") == (25, 0)
    ellipse = Shape("e", "ellipse", 0, 0, 100, 40)
    assert len(ellipse.ports()) == 8 and ellipse.port("s2") == (50, 40)
    assert ellipse.port("ne")[0] == pytest.approx(50 + 50 * math.cos(math.radians(315)))
    assert [p[0] for p in Shape("d", "diamond", 0, 0, 10, 10).ports()] == ["n2", "e2", "s2", "w2"]
    assert rect.port("gibtsnicht") is None


def test_boundary_points_per_shape():
    rect = Shape("r", "rect", 0, 0, 100, 40)
    assert boundary_point(rect, (200, 20)) == pytest.approx((100, 20))
    assert boundary_point(rect, (50, -100)) == pytest.approx((50, 0))
    ellipse = Shape("e", "ellipse", 0, 0, 100, 40)
    x, y = boundary_point(ellipse, (100, 40))
    assert ((x - 50) / 50) ** 2 + ((y - 20) / 20) ** 2 == pytest.approx(1)
    diamond = Shape("d", "diamond", 0, 0, 100, 40)
    x, y = boundary_point(diamond, (100, 40))
    assert abs(x - 50) / 50 + abs(y - 20) / 20 == pytest.approx(1)


def test_orthogonal_routes_are_axis_parallel():
    d, a, b, c = _uml()
    for conn in d.connectors:
        points = route(d, conn)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            assert abs(x0 - x1) < 0.01 or abs(y0 - y1) < 0.01
    inherit = route(d, d.connectors[0])
    assert inherit[0] == b.port("n2") and inherit[-1] == a.port("s2")
    assert orthogonal((0, 0), (1, 0), (100, 50), (0, -1)) == [(0, 0), (100, 0), (100, 50)]


def test_connectors_follow_moved_shapes_and_floating_ends():
    d, a, b, c = _uml()
    before = route(d, d.connectors[0])[0]
    b.x += 50
    assert route(d, d.connectors[0])[0] == (before[0] + 50, before[1])
    free = d.connect(End(c.id), End(None, None, 400, 300), "Pfeil", route="straight")
    start, finish = route(d, free)
    assert finish == (400, 300)
    assert c.x <= start[0] <= c.x + c.w and abs(start[1] - (c.y + c.h)) < 0.01      # unten raus, Richtung Ziel


def test_nearest_port_midpoint_distance():
    rect = Shape("r", "rect", 0, 0, 100, 40)
    assert nearest_port(rect, 98, 22)[0] == "e2"
    assert midpoint([(0, 0), (10, 0), (10, 10)]) == (10, 0)
    from notex.core.diagram.model import distance_to_route
    assert distance_to_route([(0, 0), (10, 0)], 5, 3) == pytest.approx(3)


def test_remove_cascades_to_connectors():
    d, a, b, c = _uml()
    d.remove({a.id})
    assert [s.id for s in d.shapes] == [b.id, c.id] and d.connectors == []


def test_json_roundtrip_and_errors():
    d, *_ = _uml()
    again = Diagram.from_json(d.to_json())
    assert again == d
    with pytest.raises(ValueError):
        Diagram.from_json('{"format": "anders"}')
    tolerant = Diagram.from_json('{"format":"fcknotes-diagram","shapes":[{"id":"x","kind":"rect","x":0,"y":0,'
                                 '"w":10,"h":10,"neu":1}],"connectors":[]}')
    assert tolerant.shapes[0].id == "x"


def test_fit_and_bounds():
    d, *_ = _uml()
    d.translate(100, 50)
    d.fit(margin=5)
    box = d.bounds()
    assert box[0] == pytest.approx(5) and box[1] == pytest.approx(5)
    assert d.width == pytest.approx(box[2] + 5)
    assert Diagram().bounds() is None
    with pytest.raises(ValueError):
        Diagram().add_shape("stern", 0, 0)


@pytest.mark.parametrize("name", list(templates.TEMPLATES))
def test_templates_build_connected_diagrams(name):
    d = Diagram(400, 300)
    ids = templates.apply(d, name)
    assert ids and d.connectors
    shape_ids = {s.id for s in d.shapes}
    for c in d.connectors:
        for end in (c.source, c.target):
            assert end.shape is None or end.shape in shape_ids
    assert render.primitives(d)


# ---- Zeichnen ------------------------------------------------------------------------------------------------
def test_primitives_heads_labels_and_class_sections():
    d, a, b, c = _uml()
    prims = render.primitives(d)
    texts = [p[3] for p in prims if p[0] == "text"]
    assert {"Person", "- name: String", "+ gruessen(): void", "hat", "1", "*", "Schüler"} <= set(texts)
    person = next(p for p in prims if p[0] == "text" and p[3] == "Person")
    assert person[7] is True and person[8] is True                       # fett, kursiv (abstrakt)
    filled = [p for p in prims if p[0] == "path" and p[2] and p[3] == "#1a1a1a"]
    hollow = [p for p in prims if p[0] == "path" and p[2] and p[3] == "#ffffff"]
    assert filled and hollow                                              # Kompositionsraute, Vererbungsdreieck
    layout = render.class_layout(a)
    assert layout[0][0] == a.y and layout[-1][1] == a.y + a.h
    assert render.min_class_height(a) < a.h


def test_text_width_bold_wider():
    assert render.text_width("Klasse", 11, True) > render.text_width("Klasse", 11)
    lines = render.wrap("eins zwei drei vier", 11, 40)
    assert len(lines) >= 3 and all(render.text_width(line, 11) <= 40 for line in lines)


def test_pdf_content_parses_and_contains_text():
    d, *_ = _uml()
    content = dpdf.content(d)
    assert content.startswith("q") and content.rstrip().endswith("Q")
    assert "(Person) Tj" in content and "/HeBO" in content and "(Sch\\374ler) Tj" in content
    assert "[4 3] 0 d" not in content
    d.connectors[0].dashed = True
    assert "[4 3] 0 d" in dpdf.content(d)


# ---- PDF ------------------------------------------------------------------------------------------------------
def test_add_read_update_diagram_in_pdf():
    d, *_ = _uml()
    data = pdfdiagram.add_diagram(make_pdf(["Blatt"], outline=False), 0, (60, 120), d)
    objs = pdfobjects.list_objects(data, 0)
    assert [o.kind for o in objs] == ["diagram"]
    assert objs[0].rect == pytest.approx((60, 120, 540, 450))
    assert "Person" in objs[0].contents
    back = pdfdiagram.read_diagram(data, 0, 0)
    assert back == d
    annot = reader(data).pages[0]["/Annots"][0].get_object()
    form = annot["/AP"]["/N"].get_object()
    ContentStream(form, reader(data)).operations                         # gültiger Inhalt
    assert set(form["/Resources"]["/Font"].keys()) == {"/Helv", "/HeBo", "/HeOb", "/HeBO"}
    d.shapes[0].text = "Neu"
    d.width = 300
    updated = pdfdiagram.update_diagram(data, 0, 0, d)
    obj = pdfobjects.list_objects(updated, 0)[0]
    assert obj.rect[:2] == pytest.approx((60, 120)) and obj.rect[2] == pytest.approx(360)
    assert pdfdiagram.read_diagram(updated, 0, 0).shapes[0].text == "Neu"
    moved = pdfdiagram.update_diagram(data, 0, 0, d, (10, 20))
    assert pdfobjects.list_objects(moved, 0)[0].rect[:2] == pytest.approx((10, 20))


def test_read_errors_and_flatten():
    plain = pdfforms.add_strokes(make_pdf(["X"], outline=False), 0, (0, 0, 50, 20), [[(0, 0), (1, 1)]])
    with pytest.raises(PdfEditError, match="kein fckNotes-Diagramm"):
        pdfdiagram.read_diagram(plain, 0, 0)
    with pytest.raises(PdfEditError):
        pdfdiagram.read_diagram(plain, 0, 5)
    d, *_ = _uml()
    data = pdfdiagram.add_diagram(make_pdf(["X"], outline=False), 0, (10, 10), d)
    flat = pdfforms.flatten(data)
    assert pdfobjects.list_objects(flat, 0) == []
    assert b"fckNotesDiagram" not in flat                                # Modell fällt beim Einbrennen weg
    with pytest.raises(PdfEditError, match="klein"):
        pdfdiagram.add_diagram(data, 0, (0, 0), Diagram(2, 2))


def test_diagram_on_rotated_page_and_move():
    from notex.core import pdfpages
    d, *_ = _uml()
    rotated = pdfpages.rotate(make_pdf(["Quer"], outline=False), [0], 90)
    data = pdfdiagram.add_diagram(rotated, 0, (40, 40), d)
    form = reader(data).pages[0]["/Annots"][0].get_object()["/AP"]["/N"].get_object()
    assert [float(v) for v in form["/Matrix"]] == [0, 1, -1, 0, 0, 0]
    assert pdfobjects.list_objects(data, 0)[0].rect == pytest.approx((40, 40, 520, 370))
    moved = pdfobjects.set_rect(data, 0, 0, (100, 50, 580, 380))
    assert pdfobjects.list_objects(moved, 0)[0].rect == pytest.approx((100, 50, 580, 380))
    assert pdfdiagram.read_diagram(moved, 0, 0) == d                     # Modell bleibt beim Verschieben


def test_too_wide_diagram_is_scaled_to_fit_and_scale_is_kept():
    d = Diagram(700, 200)
    d.add_shape("rect", 0, 0, 700, 200, "breit")
    data = pdfdiagram.add_diagram(make_pdf(["X"], outline=False), 0, (72, 100), d)
    obj = pdfobjects.list_objects(data, 0)[0]
    assert obj.rect[2] == pytest.approx(595 - 12) and obj.rect[0] == pytest.approx(72)
    ratio = (obj.rect[2] - obj.rect[0]) / (obj.rect[3] - obj.rect[1])
    assert ratio == pytest.approx(3.5, rel=0.01)                          # Seitenverhältnis bleibt
    small = pdfobjects.set_rect(data, 0, 0, (72, 100, 247, 150))          # Nutzer verkleinert auf 25 %
    d.shapes[0].text = "geändert"
    again = pdfdiagram.update_diagram(small, 0, 0, d)
    assert pdfobjects.list_objects(again, 0)[0].rect[2] == pytest.approx(247)
    assert pdfdiagram.fit_scale(Diagram(100, 100), (0, 0), (595, 842)) == 1.0


def test_align_free_ends_makes_nearly_straight_lines_exact():
    from notex.core.diagram.model import align_free_ends, align_point
    assert align_point((103, 40), (100, 200), 8) == (100, 40)
    assert align_point((130, 195), (100, 200), 8) == (130, 200)
    assert align_point((130, 40), (100, 200), 8) == (130, 40)              # weit weg: bleibt schräg
    assert align_point((130, 40), (100, 200), 8, constrain=True) == (100, 40)
    d = Diagram(300, 300)
    box = d.add_shape("rounded", 50, 200, 120, 60)
    up = d.connect(End(box.id, "n2"), End(None, None, 113, 50), "Pfeil", route="straight")
    align_free_ends(d, up, 8)
    assert (up.target.x, up.target.y) == (110, 50)                        # senkrecht über dem Andockpunkt
    start = route(d, up)
    assert start[0][0] == start[-1][0]
    back = d.connect(End(None, None, 172, 52), End(box.id, "e2"), "Pfeil", route="straight")
    align_free_ends(d, back, 8)
    assert (back.source.x, back.source.y) == (170, 52)
