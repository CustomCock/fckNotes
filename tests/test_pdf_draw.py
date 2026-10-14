"""V3 (A10, Teil 1): Freihand, Radierer, Formen – Erscheinungsbild parsebar, Deckkraft über ExtGState, gültiges PDF,
der Radierer entfernt nur ganze Striche, Verschieben/Größe rechnet die Geometrie um."""
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ContentStream, NameObject

from notex.core import pdfannot as A, pdfdraw as D, pdfobjects, pdfpages
from notex.core.pdfpages import PdfEditError
from pdffixtures import assert_valid_pdf
from pdfhelp import make_pdf

WAVE = [(50 + i * 3, 100 + (i % 7) * 4) for i in range(60)]


def _doc():
    return make_pdf(["Seite eins"], size=(600, 800), outline=False)


def _annot(data, position=-1, page=0):
    reader = PdfReader(io.BytesIO(data))
    return reader, reader.pages[page]["/Annots"][position].get_object()


def _ops(reader, annot):
    stream = annot["/AP"]["/N"].get_object()
    return [op.decode() for _args, op in ContentStream(stream, reader).operations], stream


def test_ink_smoothed_with_opacity_extgstate():
    data = D.add_ink(_doc(), 0, [WAVE], D.Style("#2060c0", 3, opacity=0.5))
    assert_valid_pdf(data)
    reader, annot = _annot(data)
    assert annot["/Subtype"] == "/Ink" and float(annot["/CA"]) == 0.5
    ops, stream = _ops(reader, annot)
    assert "c" in ops and ops.count("S") == 1 and "gs" in ops
    state = stream["/Resources"]["/ExtGState"]["/GS0"]
    assert float(state["/CA"]) == 0.5 and float(state["/ca"]) == 0.5
    assert len(annot["/InkList"][0]) < len(WAVE) * 2                    # vereinfacht


def test_ink_unsmoothed_uses_straight_segments_and_one_annotation_per_stroke():
    data = D.add_ink(_doc(), 0, [WAVE, [(50, 300), (200, 320)]], D.Style(smooth=False))
    reader = PdfReader(io.BytesIO(data))
    inks = [a.get_object() for a in reader.pages[0]["/Annots"]]
    assert [a["/Subtype"] for a in inks] == ["/Ink", "/Ink"]
    ops, _s = _ops(reader, inks[0])
    assert "c" not in ops and "l" in ops


@pytest.mark.parametrize("kind, points, subtype", [
    ("rect", [(100, 400), (250, 480)], "/Square"), ("ellipse", [(300, 400), (450, 480)], "/Circle"),
    ("line", [(50, 600), (200, 650)], "/Line"), ("arrow", [(250, 600), (400, 560)], "/Line"),
    ("polygon", [(450, 600), (550, 600), (500, 700)], "/Polygon"), ("cloud", [(100, 500), (300, 580)], "/Square"),
])
def test_shapes_have_parseable_appearance(kind, points, subtype):
    style = D.Style("#d03030", 2, "#ffee88", 0.8, "dashed")
    data = D.add_shape(_doc(), 0, kind, points, style)
    assert_valid_pdf(data)
    reader, annot = _annot(data)
    assert annot["/Subtype"] == subtype and annot["/NM"].startswith(A.ID_PREFIX)
    ops, stream = _ops(reader, annot)
    assert ops[0] == "q" and ops[-1] == "Q" and "d" in ops and "gs" in ops
    assert annot["/BS"]["/S"] == "/D"
    if kind in ("line", "arrow"):
        assert "/IC" not in annot and ("B" not in ops)                 # Linien haben keine Füllung
    else:
        assert "B" in ops and "/IC" in annot
    if kind == "arrow":
        assert list(annot["/LE"]) == ["/None", "/OpenArrow"] and ops.count("S") == 2
    if kind == "cloud":
        assert annot["/BE"]["/S"] == "/C" and ops.count("c") > 20
    if kind == "rect":
        assert "re" in ops


def test_shape_rect_matches_view_on_rotated_page():
    rotated = pdfpages.rotate(_doc(), [0], 90)
    data = D.add_shape(rotated, 0, "rect", [(100, 120), (300, 200)], D.Style(width=2))
    (info,) = A.list_annotations(data, 0)
    pad = 2 / 2 + 1
    assert info.rect == pytest.approx((100 - pad, 120 - pad, 300 + pad, 200 + pad))


def test_dotted_and_solid_dash_patterns():
    dotted = D.add_shape(_doc(), 0, "ellipse", [(10, 10), (100, 60)], D.Style(dash="dotted", width=2))
    _r, annot = _annot(dotted)
    assert [float(v) for v in annot["/BS"]["/D"]] == [0, 4]
    assert D.style_of(annot).dash == "dotted"
    solid = D.add_shape(_doc(), 0, "rect", [(10, 10), (100, 60)], D.Style())
    _r, annot = _annot(solid)
    assert annot["/BS"]["/S"] == "/S" and D.style_of(annot).dash == "solid" and "/CA" not in annot


@pytest.mark.parametrize("kind, points", [("rect", [(10, 10), (11, 11)]), ("line", [(10, 10), (10.5, 10)]),
                                          ("polygon", [(10, 10), (50, 50)]), ("triangle", [(0, 0), (9, 9)])])
def test_invalid_shapes_rejected(kind, points):
    with pytest.raises(PdfEditError):
        D.add_shape(_doc(), 0, kind, points)


def test_invalid_style_rejected():
    with pytest.raises(PdfEditError):
        D.add_shape(_doc(), 0, "rect", [(10, 10), (100, 60)], D.Style(dash="wavy"))
    with pytest.raises(PdfEditError):
        D.add_ink(_doc(), 0, [[]])


def _two_stroke_ink() -> bytes:
    """Eine fremde Ink-Anmerkung mit zwei Strichen (wie aus Acrobat) plus ein Rechteck darüber."""
    data = D.add_shape(_doc(), 0, "rect", [(40, 90), (300, 340)], D.Style("#00aa00"))
    data = D.add_ink(data, 0, [[(50, 100), (250, 100)]], D.Style("#000000", 2))
    writer = PdfWriter(clone_from=io.BytesIO(data))
    ink = writer.pages[0]["/Annots"][1].get_object()
    geom = A.geometry_of(writer.pages[0])
    second = [c for p in ((50, 300), (250, 300)) for c in geom.to_pdf(*p)]
    ink[NameObject("/InkList")] = ArrayObject([ink["/InkList"][0], A._floats(second)])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_eraser_removes_only_whole_strokes():
    data = _two_stroke_ink()
    before = [float(v) for v in _annot(data, 1)[1]["/InkList"][1]]
    out, removed = D.erase_at(data, 0, [(150, 101)])
    assert removed == 1
    reader, ink = _annot(out, 1)
    assert len(ink["/InkList"]) == 1 and [float(v) for v in ink["/InkList"][0]] == before   # zweiter Strich unverändert
    assert reader.pages[0]["/Annots"][0].get_object()["/Subtype"] == "/Square"             # Rechteck bleibt
    out2, removed = D.erase_at(out, 0, [(150, 299)])
    assert removed == 1 and [a.kind for a in A.list_annotations(out2, 0)] == ["square"]
    assert_valid_pdf(out2)


def test_eraser_miss_changes_nothing():
    data = _two_stroke_ink()
    out, removed = D.erase_at(data, 0, [(500, 700)])
    assert removed == 0 and out == data


def test_move_and_resize_recompute_geometry():
    data = D.add_shape(_doc(), 0, "rect", [(100, 100), (200, 150)], D.Style(width=2))
    data = D.add_ink(data, 0, [[(50, 300), (150, 300)]], D.Style(width=2))
    data = D.add_shape(data, 0, "arrow", [(300, 300), (400, 350)], D.Style(width=2))
    objs = {o.kind: o for o in pdfobjects.list_objects(data, 0)}
    assert {"square", "ink", "line"} <= set(objs) and all(objs[k].movable for k in ("square", "ink", "line"))
    sq = objs["square"]
    x0, y0, x1, y1 = sq.rect
    data = pdfobjects.set_rect(data, 0, sq.index, (x0 + 10, y0 + 20, x1 + 110, y1 + 20))   # 100 breiter, verschoben
    (info,) = [a for a in A.list_annotations(data, 0) if a.kind == "square"]
    assert info.rect[0] == pytest.approx(x0 + 10, abs=0.5) and info.rect[2] == pytest.approx(x1 + 110, abs=0.5)
    ink = next(o for o in pdfobjects.list_objects(data, 0) if o.kind == "ink")
    data = pdfobjects.move_by(data, 0, ink.index, 30, -10)
    _r, annot = _annot(data, ink.index)
    geom = A.page_geometry(data, 0)
    first = geom.to_view(float(annot["/InkList"][0][0]), float(annot["/InkList"][0][1]))
    assert first == pytest.approx((80, 290), abs=0.01)
    line = next(o for o in pdfobjects.list_objects(data, 0) if o.kind == "line")
    data = pdfobjects.move_by(data, 0, line.index, -100, 0)
    _r, annot = _annot(data, line.index)
    start = geom.to_view(float(annot["/L"][0]), float(annot["/L"][1]))
    assert start == pytest.approx((200, 300), abs=0.01)
    assert_valid_pdf(data)


def test_restyle_changes_color_dash_and_fill():
    data = D.add_shape(_doc(), 0, "polygon", [(100, 100), (200, 100), (150, 180)], D.Style())
    data = D.restyle(data, 0, 0, D.Style("#0000ff", 4, "#ccccff", 0.6, "dotted"))
    _r, annot = _annot(data)
    style = D.style_of(annot)
    assert (style.color, style.width, style.fill, style.dash) == ("#0000ff", 4, "#ccccff", "dotted")
    assert style.opacity == pytest.approx(0.6)
    note = A.add_note(_doc(), 0, 50, 50, "Notiz")
    with pytest.raises(PdfEditError):
        D.restyle(note, 0, 0, D.Style())


def test_simplify_and_distance():
    line = [(float(x), 0.0) for x in range(50)]
    assert D.simplify(line) == [(0.0, 0.0), (49.0, 0.0)]
    corner = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert D.simplify(corner) == corner
    assert D.polyline_distance((5, 3), corner) == pytest.approx(3)
    assert D.polyline_distance((0, 0), []) == float("inf")


def test_drawings_get_comments_and_labels():
    data = D.add_shape(_doc(), 0, "ellipse", [(10, 10), (100, 60)])
    (info,) = A.list_annotations(data, 0)
    assert info.label == "Ellipse" and info.text_editable


def test_invalid_colors_rejected():
    with pytest.raises(PdfEditError):
        D.add_shape(_doc(), 0, "rect", [(10, 10), (100, 60)], D.Style(color="rot"))
    with pytest.raises(PdfEditError):
        D.add_shape(_doc(), 0, "rect", [(10, 10), (100, 60)], D.Style(fill="#12345"))
