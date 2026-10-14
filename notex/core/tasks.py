"""Lange Arbeiten (OCR, Stapel, Ordnersuche, Komprimieren …) Qt-frei beschreiben: Fortschritt melden, abbrechbar.

Eine Kernfunktion bekommt einen `TaskContext` und ruft `ctx.step(...)` – das meldet den Fortschritt und wirft
`Cancelled`, sobald abgebrochen wurde. Die Oberfläche (`notex/ui/tasks.py`) startet die Funktion im Hintergrund und
zeigt Fortschritt und „Abbrechen“; in Tests reicht ein `TaskContext()` ohne Rückmeldung.
"""
from __future__ import annotations

import threading
from typing import Callable


class Cancelled(Exception):
    """Der Nutzer hat abgebrochen."""


class TaskContext:
    def __init__(self, on_progress: Callable[[int, int, str], None] | None = None) -> None:
        self._cancel = threading.Event()
        self._on_progress = on_progress

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def step(self, done: int, total: int, text: str = "") -> None:
        """Fortschritt melden (done von total) und Abbruch prüfen."""
        self.check()
        if self._on_progress is not None:
            self._on_progress(done, total, text)
