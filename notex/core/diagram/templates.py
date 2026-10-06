"""Startvorlagen für typische Schul-/Ausbildungsdiagramme (Qt-frei). Jede Vorlage setzt Formen und Verbinder ab
einem Ursprung in ein bestehendes Diagramm – danach ist alles frei bearbeitbar."""
from __future__ import annotations

from notex.core.diagram.model import Diagram, End

TEMPLATES = {
    "class": "UML-Klassendiagramm (Vererbung + Assoziation)",
    "usecase": "Use-Case-Diagramm (Akteur, System, Anwendungsfälle)",
    "activity": "Aktivitätsdiagramm (Start, Aktion, Entscheidung, Ende)",
    "flow": "Flussdiagramm (Start, Eingabe, Verarbeitung, Ausgabe)",
    "sequence": "Sequenzdiagramm (Lebenslinien, Aktivierung, Nachricht, Antwort)",
    "state": "Zustandsdiagramm (Start, Zustände, Übergänge, Ende)",
    "component": "Komponentendiagramm (Schnittstelle bereitgestellt / benötigt)",
    "deployment": "Verteilungsdiagramm (Knoten, Artefakt, Verbindung)",
    "object": "Objektdiagramm (Objekte mit Werten, Verknüpfung)",
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
        diagram.add_shape("system", ox + 110, oy, 260, 230, "System")      # Systemgrenze liegt hinten
        actor = diagram.add_shape("actor", ox, oy + 70, 36, 70, "Akteur")
        cases = [diagram.add_shape("ellipse", ox + 160, oy + 34 + i * 62, 160, 46, text)
                 for i, text in enumerate(("Anwendungsfall 1", "Anwendungsfall 2", "Anwendungsfall 3"))]
        for case in cases:
            diagram.connect(End(actor.id), End(case.id), "Assoziation", route="straight")
        diagram.connect(End(cases[1].id, "s2"), End(cases[2].id, "n2"), "Include «include»", route="straight")
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
        a = diagram.add_shape("lifeline", ox, oy, 110, 250, "a : A")
        b = diagram.add_shape("lifeline", ox + 220, oy, 110, 250, "b : B")
        act = diagram.add_shape("activation", b.x + b.w / 2 - 6, oy + 80, 12, 60)    # 80 … 140 = 0,32 … 0,56
        diagram.connect(End(a.id, "y0.32"), End(act.id, "wy0"), "Nachricht (synchron)", label="nachricht()",
                        route="straight")
        diagram.connect(End(act.id, "wy1"), End(a.id, "y0.56"), "Antwort", label="ergebnis", route="straight")
        diagram.connect(End(a.id, "y0.76"), End(b.id, "y0.76"), "Nachricht (asynchron)", label="signal",
                        route="straight")
    elif name == "state":
        start = diagram.add_shape("circle", ox + 80, oy, 20, 20)
        idle = diagram.add_shape("state", ox + 25, oy + 50, 130, 60, "Bereit\n--\nentry / anzeigen()")
        busy = diagram.add_shape("state", ox + 25, oy + 170, 130, 60, "Arbeitet\n--\ndo / rechnen()")
        end = diagram.add_shape("endstate", ox + 79, oy + 280, 22, 22)
        diagram.connect(End(start.id, "s2"), End(idle.id, "n2"), "Steuerfluss / Übergang")
        diagram.connect(End(idle.id, "s1"), End(busy.id, "n1"), "Steuerfluss / Übergang", label="start")
        diagram.connect(End(busy.id, "n3"), End(idle.id, "s3"), "Steuerfluss / Übergang", label="fertig")
        diagram.connect(End(busy.id, "s2"), End(end.id, "n2"), "Steuerfluss / Übergang", label="aus")
    elif name == "component":
        server = diagram.add_shape("component", ox + 230, oy, 140, 70, "Server")
        client = diagram.add_shape("component", ox, oy, 140, 70, "Client")
        ball = diagram.add_shape("lollipop", ox + 190, oy + 26, 18, 18, "")
        cup = diagram.add_shape("socket", ox + 180, oy + 24, 12, 22, "API")
        diagram.connect(End(server.id, "w2"), End(ball.id, "e2"), "Linie", route="straight")
        diagram.connect(End(client.id, "e2"), End(cup.id, "w2"), "Linie", route="straight")
    elif name == "deployment":
        pc = diagram.add_shape("node", ox, oy, 150, 90, "«device»\nPC")
        server = diagram.add_shape("node", ox + 250, oy, 170, 120, "«executionEnvironment»\nServer")
        diagram.add_shape("artifact", ox + 265, oy + 50, 120, 50, "«artifact»\napp.jar")
        diagram.connect(End(pc.id, "e2"), End(server.id), "Assoziation", label="«HTTPS»", route="straight")
    elif name == "object":
        anna = diagram.add_shape("object", ox, oy, 150, 60, "anna : Schüler\n--\nname = \"Anna\"")
        school = diagram.add_shape("object", ox + 230, oy, 150, 60, "gym : Schule\n--\nort = \"Kiel\"")
        diagram.connect(End(anna.id, "e2"), End(school.id, "w2"), "Assoziation", label="besucht")
    else:
        raise ValueError(f"Unbekannte Vorlage „{name}“")
    return [s.id for s in diagram.shapes if s.id not in before] + \
        [c.id for c in diagram.connectors if c.id not in before]
