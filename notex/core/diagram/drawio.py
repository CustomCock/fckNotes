"""draw.io-Dateien (.drawio / .xml / .drawio.svg) lesen und schreiben (Qt-frei).

draw.io speichert `<mxfile>` mit einer oder mehreren Seiten (`<diagram>`); der Seiteninhalt ist entweder ein
`<mxGraphModel>` oder komprimiert (URL-kodiert → Deflate → Base64). Darin stehen Zellen (`mxCell`): Formen
(vertex) mit Stil-Zeichenkette und Geometrie, Verbinder (edge) mit Quelle/Ziel und Andock-Anteilen (exitX/entryX).

Übernommen wird, was das eigene Modell kennt: Formen werden auf die nächstliegende eigene Form abgebildet
(unbekannte → Rechteck), UML-Klassen (swimlane + Zeilen) zu Klassen mit Abschnitten, Pfeilspitzen, gestrichelt,
Farben, Beschriftungen und Multiplizitäten. Nicht übernommen: Wegpunkte (eigenes Routing), Bilder, Drehung,
Schriftarten, Ebenen-Sichtbarkeit. Beim Lesen werden DOCTYPE/ENTITY abgelehnt und Größen begrenzt.
"""
from __future__ import annotations

import base64
import html
import re
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import quote, unquote

from notex.core.diagram.model import PORTS, _SIDE, Connector, Diagram, End, Shape, new_id, nearest_port

MAX_FILE = 20 * 1024 * 1024          # 20 MB Datei
MAX_INFLATED = 50 * 1024 * 1024      # 50 MB entpackt je Seite
MAX_CELLS = 20000


class DrawioError(ValueError):
    pass


# ---- Lesen: Datei → Seiten ------------------------------------------------------------------------------------
def _parse_xml(text: str) -> ET.Element:
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise DrawioError("Datei enthält DOCTYPE/ENTITY – aus Sicherheitsgründen nicht gelesen")
    try:
        return ET.fromstring(text)
    except ET.ParseError as error:
        raise DrawioError(f"Keine gültige XML-Datei ({error})") from error


def _inflate(data: str) -> str:
    """Komprimierten Seiteninhalt entpacken: Base64 → rohes Deflate → URL-Dekodierung."""
    try:
        raw = base64.b64decode(data.strip(), validate=False)
        inflater = zlib.decompressobj(-15)
        out = inflater.decompress(raw, MAX_INFLATED)
        if len(out) >= MAX_INFLATED or inflater.unconsumed_tail:
            raise DrawioError("Seite zu groß")
        return unquote(out.decode("utf-8"))
    except (ValueError, zlib.error, UnicodeDecodeError) as error:
        if isinstance(error, DrawioError):
            raise
        raise DrawioError(f"Komprimierte Seite nicht lesbar ({error})") from error


def deflate(xml_text: str) -> str:
    """Gegenstück zu `_inflate` (wie draw.io komprimiert)."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = compressor.compress(quote(xml_text, safe="~()*!.'").encode("ascii")) + compressor.flush()
    return base64.b64encode(raw).decode("ascii")


def pages(text: str) -> list[tuple[str, ET.Element]]:
    """(Seitenname, mxGraphModel) aller Seiten einer draw.io-Datei (auch .drawio.svg mit `content`)."""
    if len(text) > MAX_FILE:
        raise DrawioError("Datei zu groß")
    root = _parse_xml(text.lstrip("﻿").strip())
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "svg":
        content = root.get("content")
        if not content:
            raise DrawioError("SVG ohne eingebettetes draw.io-Diagramm")
        return pages(content if content.lstrip().startswith("<") else _inflate(content))
    if tag == "mxGraphModel":
        return [("Seite 1", root)]
    if tag != "mxfile":
        raise DrawioError("Keine draw.io-Datei (mxfile erwartet)")
    out = []
    for number, diagram in enumerate(root.findall("diagram"), 1):
        name = diagram.get("name") or f"Seite {number}"
        model = diagram.find("mxGraphModel")
        if model is None:
            content = (diagram.text or "").strip()
            if not content:
                continue
            model = _parse_xml(_inflate(content))
            if model.tag != "mxGraphModel":
                continue
        out.append((name, model))
    if not out:
        raise DrawioError("Die Datei enthält keine Seite")
    return out


# ---- Lesen: Zellen → Modell ----------------------------------------------------------------------------------
def parse_style(style: str | None) -> dict[str, str]:
    """„rounded=1;whiteSpace=wrap;ellipse;“ → {"rounded": "1", …, "ellipse": ""}"""
    out = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        key, _sep, value = part.partition("=")
        out[key.strip()] = value.strip()
    return out


_BREAK_RE = re.compile(r"<\s*(br|/p|div|li)\b[^>]*>", re.I)          # <div>/<li> beginnen eine neue Zeile
_HR_RE = re.compile(r"<\s*hr[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(value: str | None, is_html: bool) -> str:
    """Zellwert als Text; HTML-Werte: Umbrüche erhalten, <hr> trennt Klassen-Abschnitte, Tags weg."""
    value = value or ""
    if is_html:
        value = _HR_RE.sub("\n--\n", value)
        value = _BREAK_RE.sub("\n", value)
        value = html.unescape(_TAG_RE.sub("", value)).replace("\xa0", " ")
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip("\n")
    return re.sub(r"\n{3,}", "\n\n", text)


def _color(value: str | None, default: str) -> str:
    if value is None or value == "" or value == "default":
        return default
    if value == "none":
        return "none"
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:]).lower()
    return default


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) < 1e7 else default


def _kind(style: dict[str, str], w: float, h: float) -> str:
    shape = style.get("shape", "")
    if "swimlane" in style or shape == "swimlane":
        return "class"
    if shape == "umlActor":
        return "actor"
    if shape in ("endState", "doubleEllipse") or "endState" in style:
        return "endstate"
    if shape == "startState":
        return "circle"
    if shape in ("note", "note2", "card"):
        return "note"
    if shape in ("folder", "umlFrame", "package", "module", "component"):
        return "package"
    if shape in ("cylinder", "cylinder2", "cylinder3", "datastore", "dataStorage"):
        return "database"
    if shape in ("parallelogram", "manualInput", "trapezoid", "document", "dataIO"):
        return "parallelogram"
    if "rhombus" in style or shape == "rhombus":
        return "diamond"
    if "ellipse" in style or shape == "ellipse":
        fill = style.get("fillColor", "")
        dark = fill in ("strokeColor", "#000000", "#000", "#1a1a1a") or fill.lower() == "#000000"
        return "circle" if dark and max(w, h) <= 40 else "ellipse"
    if "text" in style or "edgeLabel" in style or (style.get("strokeColor") == "none" and
                                                     style.get("fillColor") in ("none", None)):
        return "text"
    if style.get("rounded") == "1" or shape in ("process", "mxgraph.flowchart.terminator", "terminator"):
        return "rounded"
    return "rect"


DRAWIO_ARROWS = {"classic": "arrow", "classicThin": "arrow", "block": "arrow", "blockThin": "arrow",
                 "open": "open", "openThin": "open", "openAsync": "open", "async": "open",
                 "diamond": "diamond", "diamondThin": "diamond", "oval": "circle", "circle": "circle",
                 "dash": "none", "cross": "none", "none": "none", "": "none"}


def _arrow(style: dict[str, str], which: str) -> str:
    default = "classic" if which == "end" else "none"
    name = style.get(f"{which}Arrow", default)
    fill = style.get(f"{which}Fill", "1") != "0"
    kind = DRAWIO_ARROWS.get(name, "arrow")
    if name in ("block", "blockThin") and not fill:
        return "triangle"
    if kind == "diamond" and fill:
        return "diamond_filled"
    return kind


def _port_for(shape: Shape, fx: float, fy: float) -> str:
    name, _d = nearest_port(shape, shape.x + fx * shape.w, shape.y + fy * shape.h)
    return name


def _geometry(cell: ET.Element) -> ET.Element | None:
    return cell.find("mxGeometry")


def to_diagram(model: ET.Element) -> Diagram:
    """mxGraphModel → Diagramm (Größe passend zum Inhalt)."""
    root = model.find("root")
    if root is None:
        raise DrawioError("Seite ohne Inhalt")
    cells = [c for c in root if c.tag in ("mxCell", "object", "UserObject")]
    if len(cells) > MAX_CELLS:
        raise DrawioError("Zu viele Elemente")
    # <object label="…"><mxCell …/></object>: Attribute zusammenführen
    flat: dict[str, dict] = {}
    order: list[str] = []
    for cell in cells:
        if cell.tag != "mxCell":
            inner = cell.find("mxCell")
            if inner is None:
                continue
            attrs = dict(inner.attrib)
            attrs["id"] = cell.get("id", attrs.get("id", ""))
            attrs["value"] = cell.get("label", attrs.get("value", ""))
            geometry = _geometry(inner)
        else:
            attrs = dict(cell.attrib)
            geometry = _geometry(cell)
        cid = attrs.get("id")
        if not cid or cid in flat:
            continue
        attrs["_geom"] = geometry
        attrs["_style"] = parse_style(attrs.get("style"))
        flat[cid] = attrs
        order.append(cid)

    def parent_of(cid):
        return flat.get(flat[cid].get("parent", ""))

    def is_layer(attrs) -> bool:
        return attrs is not None and attrs.get("vertex") != "1" and attrs.get("edge") != "1"

    # absolute Lage jeder Form (Kinder von Gruppen/Swimlanes sind relativ zum Elternteil)
    absolute: dict[str, tuple[float, float, float, float]] = {}

    def box(cid, depth=0):
        if cid in absolute:
            return absolute[cid]
        attrs = flat[cid]
        g = attrs["_geom"]
        x = _number(g.get("x")) if g is not None else 0.0
        y = _number(g.get("y")) if g is not None else 0.0
        w = _number(g.get("width")) if g is not None else 0.0
        h = _number(g.get("height")) if g is not None else 0.0
        parent = parent_of(cid)
        if parent is not None and parent.get("vertex") == "1" and depth < 50:
            px, py, _pw, _ph = box(parent["id"], depth + 1)
            x, y = x + px, y + py
        absolute[cid] = (x, y, w, h)
        return absolute[cid]

    diagram = Diagram()
    shapes: dict[str, Shape] = {}
    consumed: set[str] = set()
    for cid in order:
        attrs = flat[cid]
        if attrs.get("vertex") != "1" or cid in consumed:
            continue
        style = attrs["_style"]
        parent = parent_of(cid)
        if parent is not None and parent.get("edge") == "1":
            continue                                           # Kantenbeschriftung, kommt unten
        if "group" in style and not attrs.get("value"):
            continue                                           # Gruppe selbst ist unsichtbar
        x, y, w, h = box(cid)
        if w <= 0 or h <= 0:
            continue
        is_html = style.get("html") == "1"
        text = plain_text(attrs.get("value"), is_html)
        kind = _kind(style, w, h)
        if kind in ("rect", "rounded") and is_html and "<hr" in (attrs.get("value") or "").lower():
            kind = "class"                                     # UML-Klasse als ein HTML-Kasten
        if kind == "class":
            text = _class_text(cid, text, flat, order, consumed)
            if int(_number(style.get("fontStyle"), 0)) & 2 and "{abstract}" not in text:
                text = "{abstract}\n" + text
        elif kind == "text" and parent is not None and parent.get("_style", {}).get("childLayout") == "stackLayout":
            continue                                           # Zeile einer Klasse ohne erkannte Klasse
        shape = Shape(new_id("s"), kind, x, y, w, h, text)
        shape.fill = _color(style.get("fillColor"), "#ffffff")
        shape.stroke = _color(style.get("strokeColor"), "#1a1a1a")
        shape.text_color = _color(style.get("fontColor"), "#1a1a1a")
        if kind in ("circle", "endstate") and shape.fill in ("#ffffff", "none"):
            shape.fill = "#1a1a1a"
        if kind == "text":
            shape.fill = "none" if style.get("fillColor") in (None, "none", "default") else shape.fill
            shape.stroke = "none" if style.get("strokeColor") in (None, "none") else shape.stroke
        if style.get("fillColor") == "none" and kind not in ("text",):
            shape.fill = "none"
        shape.font_size = max(6.0, min(40.0, _number(style.get("fontSize"), 11.0) or 11.0))
        shape.bold = bool(int(_number(style.get("fontStyle"), 0)) & 1) and kind != "class"
        shape.dashed = style.get("dashed") == "1"
        shapes[cid] = shape
        diagram.shapes.append(shape)

    labels: dict[str, list[tuple[float, str]]] = {}
    for cid in order:
        attrs = flat[cid]
        parent = parent_of(cid)
        if attrs.get("vertex") == "1" and parent is not None and parent.get("edge") == "1":
            g = attrs["_geom"]
            pos = _number(g.get("x")) if g is not None else 0.0
            text = plain_text(attrs.get("value"), attrs["_style"].get("html") == "1")
            if text:
                labels.setdefault(parent["id"], []).append((pos, text))

    for cid in order:
        attrs = flat[cid]
        if attrs.get("edge") != "1":
            continue
        style = attrs["_style"]
        g = attrs["_geom"]
        points = {}
        if g is not None:
            for point in g.findall("mxPoint"):
                points[point.get("as")] = (_number(point.get("x")), _number(point.get("y")))
        parent = parent_of(cid)
        ox = oy = 0.0
        if parent is not None and parent.get("vertex") == "1":
            ox, oy, _w, _h = box(parent["id"])

        def end(which, key_x, key_y, point_name):
            shape = shapes.get(attrs.get(which, ""))
            if shape is not None:
                if key_x in style and key_y in style:
                    return End(shape.id, _port_for(shape, _number(style[key_x], 0.5), _number(style[key_y], 0.5)))
                return End(shape.id, None)
            px, py = points.get(point_name, (0.0, 0.0))
            return End(None, None, px + ox, py + oy)

        source = end("source", "exitX", "exitY", "sourcePoint")
        target = end("target", "entryX", "entryY", "targetPoint")
        if source.shape is None and target.shape is None and (source.x, source.y) == (target.x, target.y):
            continue
        edge_style = style.get("edgeStyle", "")
        conn = Connector(new_id("c"), source, target,
                         route="orthogonal" if edge_style in ("orthogonalEdgeStyle", "elbowEdgeStyle",
                                                              "entityRelationEdgeStyle") else "straight",
                         start_arrow=_arrow(style, "start"), end_arrow=_arrow(style, "end"),
                         dashed=style.get("dashed") == "1",
                         label=plain_text(attrs.get("value"), style.get("html") == "1"),
                         stroke=_color(style.get("strokeColor"), "#1a1a1a"),
                         width=max(0.5, min(6.0, _number(style.get("strokeWidth"), 1.2) or 1.2)))
        for pos, text in labels.get(cid, []):
            if pos <= -0.5 and not conn.start_label:
                conn.start_label = text
            elif pos >= 0.5 and not conn.end_label:
                conn.end_label = text
            elif not conn.label:
                conn.label = text
        diagram.connectors.append(conn)

    if not diagram.shapes and not diagram.connectors:
        raise DrawioError("Auf dieser Seite ist nichts, was fckNotes zeichnen kann")
    diagram.fit(margin=6)
    return diagram


def _class_text(cid: str, name: str, flat: dict, order: list[str], consumed: set[str]) -> str:
    """UML-Klasse aus draw.io: Kopf = Name, Kinder = Zeilen, „line“-Kinder trennen Abschnitte."""
    sections: list[list[str]] = [[]]
    children = [c for c in order if flat[c].get("parent") == cid and flat[c].get("vertex") == "1"]
    children.sort(key=lambda c: _number(flat[c]["_geom"].get("y")) if flat[c]["_geom"] is not None else 0.0)
    for child in children:
        consumed.add(child)
        style = flat[child]["_style"]
        if "line" in style or style.get("shape") == "line":
            sections.append([])
            continue
        text = plain_text(flat[child].get("value"), style.get("html") == "1")
        if text:
            sections[-1].append(text)
    if any(line.strip() == "--" for line in name.split("\n")) and not any(sections[0]) and len(sections) == 1:
        parts = [part.strip("\n") for part in re.split(r"^\s*--\s*$", name, flags=re.M)]  # HTML-Klasse (<hr>)
    elif not children:
        parts = [name]                                          # nur Kopf, keine Abschnitte
    else:
        parts = [name] + ["\n".join(lines) for lines in sections]
    return "\n--\n".join(parts)


def read(text: str, page: int = 0) -> Diagram:
    """Seite `page` einer draw.io-Datei als Diagramm."""
    found = pages(text)
    if not 0 <= page < len(found):
        raise DrawioError("Diese Seite gibt es nicht")
    return to_diagram(found[page][1])


def page_names(text: str) -> list[str]:
    return [name for name, _model in pages(text)]


# ---- Schreiben -----------------------------------------------------------------------------------------------
KIND_STYLES = {
    "rect": "rounded=0;whiteSpace=wrap;html=1;",
    "rounded": "rounded=1;whiteSpace=wrap;html=1;",
    "ellipse": "ellipse;whiteSpace=wrap;html=1;",
    "diamond": "rhombus;whiteSpace=wrap;html=1;",
    "circle": "ellipse;html=1;shape=startState;",
    "endstate": "ellipse;html=1;shape=endState;",
    "note": "shape=note;whiteSpace=wrap;html=1;size=14;",
    "text": "text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;",
    "actor": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;",
    "package": "shape=folder;fontStyle=1;spacingTop=10;tabWidth=40;tabHeight=14;tabPosition=left;html=1;"
               "whiteSpace=wrap;",
    "database": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;",
    "parallelogram": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;"
                     "fixedSize=1;",
}
ARROW_STYLES = {"none": ("none", "1"), "arrow": ("classic", "1"), "open": ("open", "1"), "triangle": ("block", "0"),
                "diamond": ("diamondThin", "0"), "diamond_filled": ("diamondThin", "1"), "circle": ("oval", "0")}


def _html(text: str) -> str:
    return html.escape(text, quote=False).replace("\n", "<br>")


def _style_colors(shape: Shape) -> str:
    out = f"fillColor={shape.fill};strokeColor={shape.stroke};fontColor={shape.text_color};" \
          f"fontSize={shape.font_size:g};"
    if shape.bold:
        out += "fontStyle=1;"
    if shape.dashed:
        out += "dashed=1;"
    return out


def _port_fraction(shape: Shape, port: str | None) -> tuple[float, float] | None:
    for name, fx, fy in PORTS.get(shape.kind, _SIDE):
        if name == port:
            return fx, fy
    return None


def write(diagram: Diagram, name: str = "Seite 1", compressed: bool = False) -> str:
    """Diagramm als draw.io-Datei (mxfile, eine Seite)."""
    model = ET.Element("mxGraphModel", {"dx": "800", "dy": "600", "grid": "1", "gridSize": "10", "guides": "1",
                                        "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1", "page": "1",
                                        "pageScale": "1", "pageWidth": str(round(max(diagram.width, 100))),
                                        "pageHeight": str(round(max(diagram.height, 100))), "math": "0",
                                        "shadow": "0"})
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    def geometry(cell, x, y, w, h, **extra):
        attrs = {"x": f"{x:g}", "y": f"{y:g}", "width": f"{w:g}", "height": f"{h:g}", "as": "geometry"}
        attrs.update(extra)
        return ET.SubElement(cell, "mxGeometry", attrs)

    for shape in diagram.shapes:
        if shape.kind == "class":
            _write_class(root, shape, geometry)
            continue
        cell = ET.SubElement(root, "mxCell", {"id": shape.id, "value": _html(shape.text),
                                              "style": KIND_STYLES.get(shape.kind, KIND_STYLES["rect"]) +
                                              _style_colors(shape),
                                              "vertex": "1", "parent": "1"})
        geometry(cell, shape.x, shape.y, shape.w, shape.h)

    known = {s.id: s for s in diagram.shapes}
    for conn in diagram.connectors:
        start, start_fill = ARROW_STYLES.get(conn.start_arrow, ("none", "1"))
        end, end_fill = ARROW_STYLES.get(conn.end_arrow, ("classic", "1"))
        style = ("edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
                 if conn.route == "orthogonal" else "rounded=0;")
        style += f"html=1;startArrow={start};startFill={start_fill};endArrow={end};endFill={end_fill};" \
                 f"strokeColor={conn.stroke};strokeWidth={conn.width:g};"
        if conn.start_arrow in ("diamond", "diamond_filled") or conn.end_arrow in ("diamond", "diamond_filled"):
            style += "startSize=12;endSize=12;"
        if conn.dashed:
            style += "dashed=1;"
        attrs = {"id": conn.id, "value": _html(conn.label), "style": "", "edge": "1", "parent": "1"}
        for end_obj, which, prefix in ((conn.source, "source", "exit"), (conn.target, "target", "entry")):
            shape = known.get(end_obj.shape or "")
            if shape is None:
                continue
            attrs[which] = shape.id
            fraction = _port_fraction(shape, end_obj.port)
            if fraction is not None:
                style += f"{prefix}X={fraction[0]:g};{prefix}Y={fraction[1]:g};{prefix}Dx=0;{prefix}Dy=0;"
        attrs["style"] = style
        cell = ET.SubElement(root, "mxCell", attrs)
        g = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        for end_obj, point_name in ((conn.source, "sourcePoint"), (conn.target, "targetPoint")):
            if end_obj.shape is None or end_obj.shape not in known:
                ET.SubElement(g, "mxPoint", {"x": f"{end_obj.x:g}", "y": f"{end_obj.y:g}", "as": point_name})
        for text, pos, align in ((conn.start_label, "-1", "left"), (conn.end_label, "1", "right")):
            if not text:
                continue
            label = ET.SubElement(root, "mxCell", {
                "id": new_id("l"), "value": _html(text), "vertex": "1", "connectable": "0", "parent": conn.id,
                "style": f"edgeLabel;resizable=0;html=1;align={align};verticalAlign=bottom;"})
            ET.SubElement(label, "mxGeometry", {"x": pos, "relative": "1", "as": "geometry"})

    page_xml = ET.tostring(model, encoding="unicode")
    mxfile = ET.Element("mxfile", {"host": "fckNotes", "type": "device", "compressed": str(compressed).lower()})
    page = ET.SubElement(mxfile, "diagram", {"id": new_id("d"), "name": name})
    if compressed:
        page.text = deflate(page_xml)
    else:
        page.append(model)
    return ET.tostring(mxfile, encoding="unicode", xml_declaration=False)


def _write_class(root: ET.Element, shape: Shape, geometry) -> None:
    sections = shape.class_sections()
    name = sections[0].strip()
    abstract = name.startswith("{abstract}")
    if abstract:
        name = name.replace("{abstract}", "", 1).strip()
    header = 26.0
    font_style = 3 if abstract else 1
    cell = ET.SubElement(root, "mxCell", {
        "id": shape.id, "value": _html(name), "vertex": "1", "parent": "1",
        "style": f"swimlane;fontStyle={font_style};align=center;verticalAlign=top;childLayout=stackLayout;"
                 f"horizontal=1;startSize={header:g};horizontalStack=0;resizeParent=1;resizeParentMax=0;"
                 f"resizeLast=0;collapsible=1;marginBottom=0;whiteSpace=wrap;html=1;fillColor={shape.fill};"
                 f"strokeColor={shape.stroke};fontColor={shape.text_color};fontSize={shape.font_size:g};"})
    rows = sections[1:]
    line_h = shape.font_size + 6
    blocks = [max(1, len([r for r in rows_text.split("\n") if r.strip()])) * line_h + 4 for rows_text in rows]
    total = header + sum(blocks) + 8 * max(0, len(rows) - 1)
    height = max(shape.h, total)
    geometry(cell, shape.x, shape.y, shape.w, height)
    y = header
    for number, (rows_text, block) in enumerate(zip(rows, blocks)):
        if number:
            line = ET.SubElement(root, "mxCell", {
                "id": new_id("l"), "value": "", "vertex": "1", "parent": shape.id,
                "style": "line;strokeWidth=1;fillColor=none;align=left;verticalAlign=middle;spacingTop=-1;"
                         "spacingLeft=3;spacingRight=3;rotatable=0;labelPosition=right;points=[];portConstraint="
                         "eastwest;strokeColor=inherit;"})
            geometry(line, 0, y, shape.w, 8)
            y += 8
        row = ET.SubElement(root, "mxCell", {
            "id": new_id("r"), "value": _html(rows_text.strip("\n")), "vertex": "1", "parent": shape.id,
            "style": f"text;strokeColor=none;fillColor=none;align=left;verticalAlign=top;spacingLeft=4;"
                     f"spacingRight=4;overflow=hidden;rotatable=0;points=[[0,0.5],[1,0.5]];portConstraint="
                     f"eastwest;whiteSpace=wrap;html=1;fontSize={shape.font_size:g};"})
        geometry(row, 0, y, shape.w, block)
        y += block
