"""Schmale Plattformschicht: alles, was unter Windows und Linux verschieden läuft (Drucken, Sprachausgabe, Scanner,
Dateien extern öffnen). Der Rest von fckNotes bleibt plattformneutral.

Jede Komponente hat `available() -> Availability`. Fehlt etwas (CUPS, speech-dispatcher, SANE …), graut die
Oberfläche die Funktion aus und zeigt `hint` – sie stürzt nie ab. Kein Modul hier öffnet Netzwerkverbindungen.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

WINDOWS = sys.platform.startswith("win")
LINUX = sys.platform.startswith("linux")


@dataclass(frozen=True)
class Availability:
    ok: bool
    hint: str = ""          # was fehlt und wie man es nachrüstet (leer, wenn ok)

    def __bool__(self) -> bool:
        return self.ok
