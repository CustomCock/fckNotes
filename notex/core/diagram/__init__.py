"""Diagramme für PDFs (draw.io-artig, Qt-frei): Modell + Routing (`model`), Zeichen-Grundelemente (`render`),
PDF-Inhalt (`pdf`). Der Editor in `notex/ui/diagram_editor.py` zeichnet dieselben Grundelemente mit QPainter."""
from notex.core.diagram.model import (ARROWS, LABELS, RELATIONS, ROUTES, SHAPE_KINDS, Connector, Diagram, End,  # noqa: F401
                                      Shape, route)
