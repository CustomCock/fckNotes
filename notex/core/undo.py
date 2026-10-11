"""Rückgängig/Wiederholen für ganze Dateifassungen (Qt-frei) – mit Speicherbudget.

PDF-Bearbeitung legt vor jeder Änderung die komplette alte Fassung ab. Bei großen Dateien (über 500 Seiten, viele MB)
wären 40 Fassungen schnell Gigabytes. Deshalb zählt hier die **Gesamtgröße**: Was über das Budget geht, wird in eine
Cache-Datei ausgelagert (älteste zuerst) und erst beim Zurückholen wieder gelesen. Sehr alte Schritte fallen erst weg,
wenn `max_steps` erreicht ist. Cache-Dateien verschwinden, sobald ihr Schritt wegfällt oder `clear()` läuft.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BUDGET = 512 * 1024 * 1024
DEFAULT_STEPS = 200


@dataclass
class _Entry:
    data: bytes | None
    page: int
    size: int
    spill: Path | None = None

    def load(self) -> bytes:
        if self.data is not None:
            return self.data
        return self.spill.read_bytes() if self.spill else b""

    def drop(self) -> None:
        if self.spill is not None:
            try:
                self.spill.unlink()
            except OSError:
                pass
            self.spill = None


class SnapshotStack:
    """Zwei Stapel (rückgängig/wiederholen) aus (Bytes, Seite) mit gemeinsamem Speicherbudget."""

    def __init__(self, budget: int = DEFAULT_BUDGET, max_steps: int = DEFAULT_STEPS,
                 spill_dir: Path | None = None) -> None:
        self.budget = budget
        self.max_steps = max_steps
        self.spill_dir = spill_dir
        self._undo: list[_Entry] = []
        self._redo: list[_Entry] = []

    # ---- Zustand -----------------------------------------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def __len__(self) -> int:
        return len(self._undo)

    def memory(self) -> int:
        return sum(e.size for e in self._undo + self._redo if e.data is not None)

    def spilled(self) -> int:
        return sum(1 for e in self._undo + self._redo if e.spill is not None)

    # ---- Ändern ------------------------------------------------------------------------------------------------
    def push(self, data: bytes, page: int) -> None:
        """Neue Fassung kommt: die bisherige (`data`) wird ein Rückgängig-Schritt, Wiederholen verfällt."""
        self._undo.append(_Entry(data, page, len(data)))
        for entry in self._redo:
            entry.drop()
        self._redo.clear()
        self._trim()

    def undo(self, current: bytes, page: int) -> tuple[bytes, int] | None:
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(_Entry(current, page, len(current)))
        return self._take(entry)

    def redo(self, current: bytes, page: int) -> tuple[bytes, int] | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(_Entry(current, page, len(current)))
        return self._take(entry)

    def clear(self) -> None:
        for entry in self._undo + self._redo:
            entry.drop()
        self._undo.clear()
        self._redo.clear()

    # ---- intern ------------------------------------------------------------------------------------------------
    def _take(self, entry: _Entry) -> tuple[bytes, int]:
        data = entry.load()
        entry.drop()
        self._trim()
        return data, entry.page

    def _trim(self) -> None:
        while len(self._undo) > self.max_steps:
            self._undo.pop(0).drop()
        # älteste zuerst auslagern, bis das Budget passt (die jüngsten bleiben schnell im Speicher)
        for entry in self._undo + self._redo[::-1]:
            if self.memory() <= self.budget:
                break
            if entry.data is not None:
                self._spill(entry)

    def _spill(self, entry: _Entry) -> None:
        folder = self.spill_dir or Path(tempfile.gettempdir())
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"undo-{os.getpid()}-{uuid.uuid4().hex}.bin"
            path.write_bytes(entry.data or b"")
        except OSError:
            return                                       # kein Platz/Recht: dann bleibt es eben im Speicher
        entry.spill = path
        entry.data = None
