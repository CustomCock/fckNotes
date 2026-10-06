"""Startvorlagen für typische Schul-/Ausbildungsdiagramme (Qt-frei). Jede Vorlage setzt Formen und Verbinder ab
einem Ursprung in ein bestehendes Diagramm – danach ist alles frei bearbeitbar."""
from __future__ import annotations

from notex.core.diagram.model import Diagram, End

TEMPLATES = {
    "class": "UML-Klassendiagramm (Vererbung + Assoziation)",
    "usecase": "Use-Case-Diagramm (Akteur, System, Anwendungsfälle)",
    "activity": "Aktivitätsdiagramm (Start, Aktion, Entscheidung, Ende)",
    "flow": "Flussdiagramm (Start, Eingabe, Verarbeitung, Ausgabe)",
    "sequence": "Sequenzdiagramm (zwei Teilnehmer, Nachricht, Antwort)",
}


def apply(diagram: Diagram, name: str, ox: float = 10.0, oy: float = 10.0) -> list[str]:
    """Vorlage `name` ab (ox, oy) einfügen; liefert die neuen IDs (zum Auswählen)."""
    before = {s.id for s in diagram.shapes} | {c.id for c in diagram.connectors}
    if name == "class":
        base = diagram.add_shape("class", ox + 70, oy, 150, 90, "Oberklasse\n--\n- attribut: Typ\n--\n+ methode(): void")
        left = diagram.add_shape("class", ox, oy + 160, 130, 80, "Unterklasse A\n--\n- a: int\n--\n+ tuA(): void")
        right = diagram.add_shape("class", ox + 160, oy + 160, 130, 80, "Unterklasse B\n--\n- b: String\n--\n")
        other = diagram.add_shape("class", ox + 330, oy + 15, 130, 60, "Andere Klasse\n--\n\n--\n")
        diagram.connect(End(left.id, "n2"), End(base.id, "s1"), "Vererbung")
        diagram.connect(End(right.id, "n2"), End(base.id, "s3"), "Vererbung")
        diagram.connect(End(base.id, "e2"), End(other.id, "w2"), "Gerichtete Assoziation", start_label="1",
                        end_label="*", label="nutzt")
    elif name == "usecase":
        system = diagram.add_shape("rect", ox + 110, oy, 260, 220, "System")
        system.font_size = 11
        system.bold = True
        actor = diagram.add_shape("actor", ox, oy + 70, 36, 70, "Akteur")
        cases = [diagram.add_shape("ellipse", ox + 160, oy + 30 + i * 62, 160, 46, text)
                 for i, text in enumerate(("Anwendungsfall 1", "Anwendungsfall 2", "Anwendungsfall 3"))]
        system.text = "System"
        system.fill = "#ffffff"
        diagram.shapes.remove(system)
        diagram.shapes.insert(0, system)                # Systemgrenze hinter die Anwendungsfälle
        for case in cases:
            diagram.connect(End(actor.id), End(case.id), "Assoziation", route="straight")
        diagram.connect(End(cases[1].id, "s2"), End(cases[2].id, "n2"), "Abhängigkeit", label="«include»",
                        route="straight")
    elif name == "activity":
        start = diagram.add_shape("circle", ox + 90, oy, 20, 20)
        action = diagram.add_shape("rounded", ox + 40, oy + 50, 120, 44, "Aktion")
        decision = diagram.add_shape("diamond", ox + 70, oy + 130, 60, 44, "")
        yes = diagram.add_shape("rounded", ox, oy + 210, 100, 40, "Ja-Zweig")
        no = diagram.add_shape("rounded", ox + 140, oy + 210, 100, 40, "Nein-Zweig")
        end = diagram.add_shape("endstate", ox + 89, oy + 290, 22, 22)
        diagram.connect(End(start.id, "s2"), End(action.id, "n2"), "Pfeil")
        diagram.connect(End(action.id, "s2"), End(decision.id, "n2"), "Pfeil")
        diagram.connect(End(decision.id, "w2"), End(yes.id, "n2"), "Pfeil", label="[ja]")
        diagram.connect(End(decision.id, "e2"), End(no.id, "n2"), "Pfeil", label="[nein]")
        diagram.connect(End(yes.id, "s2"), End(end.id, "w2"), "Pfeil")
        diagram.connect(End(no.id, "s2"), End(end.id, "e2"), "Pfeil")
    elif name == "flow":
        start = diagram.add_shape("rounded", ox + 30, oy, 120, 36, "Start")
        start.h = 36
        inp = diagram.add_shape("parallelogram", ox + 30, oy + 66, 120, 44, "Eingabe")
        proc = diagram.add_shape("rect", ox + 30, oy + 140, 120, 44, "Verarbeitung")
        out = diagram.add_shape("parallelogram", ox + 30, oy + 214, 120, 44, "Ausgabe")
        stop = diagram.add_shape("rounded", ox + 30, oy + 288, 120, 36, "Ende")
        chain = [start, inp, proc, out, stop]
        for a, b in zip(chain, chain[1:]):
            diagram.connect(End(a.id, "s2"), End(b.id, "n2"), "Pfeil")
    elif name == "sequence":
        a = diagram.add_shape("rect", ox, oy, 100, 34, "Teilnehmer A")
        b = diagram.add_shape("rect", ox + 220, oy, 100, 34, "Teilnehmer B")
        for head in (a, b):                              # Lebenslinien als gestrichelte freie Linien
            cx = head.x + head.w / 2
            diagram.connect(End(head.id, "s2"), End(None, None, cx, oy + 220), "Linie", dashed=True,
                            route="straight")
        diagram.connect(End(None, None, ox + 50, oy + 80), End(None, None, ox + 270, oy + 80), "Pfeil",
                        label="nachricht()", route="straight")
        diagram.connect(End(None, None, ox + 270, oy + 130), End(None, None, ox + 50, oy + 130), "Antwort",
                        label="antwort", route="straight")
    else:
        raise ValueError(f"Unbekannte Vorlage „{name}“")
    return [s.id for s in diagram.shapes if s.id not in before] + \
        [c.id for c in diagram.connectors if c.id not in before]
