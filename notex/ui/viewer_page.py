"""Tabs, die keine Texteditoren sind: Bilder, Hex-Ansicht, PDF.

ViewerPage ist die gemeinsame Basis: sie kennt ihren Pfad, liefert Text für die Statusleiste und kann bei
Umbenennen/Verschieben nachgezogen werden. Viewer schreiben nur, wenn sie ausdrücklich bearbeitbar sind (PDF im
Bearbeiten-Modus): dann melden sie `dirty_changed` und speichern über `save`/`save_as` (Ctrl+S wie bei Texten).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{size} B"


class ViewerPage(QWidget):
    kind = "viewer"
    icon_name = "file"
    status_changed = Signal()
    dirty_changed = Signal(bool)     # nur bearbeitbare Viewer (PDF)
    saved = Signal(Path, Path)       # (alter Pfad, neuer Pfad) nach Speichern bzw. Speichern unter
    notice = Signal(str)             # kurze Meldung für den Toast
    open_requested = Signal(Path)    # z. B. neu erzeugtes PDF in einem Tab öffnen

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.setObjectName("ViewerPage")
        self.path = Path(path)

    def status_parts(self) -> list[str]:
        """Einträge für die Statusleiste (links nach rechts), z. B. Maße, Größe, Typ."""
        try:
            return [human_size(self.path.stat().st_size)]
        except OSError:
            return []

    @property
    def is_dirty(self) -> bool:
        return False

    def save(self) -> bool:
        """Ungespeicherte Änderungen schreiben; False = nicht gespeichert (abgebrochen/Fehler)."""
        return True

    def save_as(self) -> bool:
        return False

    def rename(self, new_path: Path) -> None:
        self.path = Path(new_path)

    def retheme(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
