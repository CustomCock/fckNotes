"""Lange Arbeiten im Hintergrund: Fortschritt anzeigen, abbrechbar, Oberfläche friert nie ein.

    task = run_task(parent, "Komprimieren …", core_function, data, on_done=..., on_error=...)

`core_function(ctx, *args)` bekommt einen `notex.core.tasks.TaskContext` und ruft `ctx.step(done, total, text)`.
Der Dialog ist nicht modal blockierend: er zeigt Text + Balken + „Abbrechen“ und schließt sich am Ende selbst.
"""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from notex.core.tasks import Cancelled, TaskContext


class TaskWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, function: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.function, self.args, self.kwargs = function, args, kwargs
        self.context = TaskContext(lambda d, t, text: self.progress.emit(int(d), int(t), str(text)))

    def run(self) -> None:
        try:
            result = self.function(self.context, *self.args, **self.kwargs)
        except Cancelled:
            self.cancelled.emit()
        except Exception as error:                     # noqa: BLE001 – Fehler als Meldung, nie als Absturz
            traceback.print_exc()
            self.failed.emit(str(error) or error.__class__.__name__)
        else:
            self.done.emit(result)

    def cancel(self) -> None:
        self.context.cancel()


class TaskDialog(QDialog):
    """Fortschritt + Abbrechen. `Esc` bricht ebenfalls ab."""

    def __init__(self, parent: QWidget | None, title: str, worker: TaskWorker) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.worker = worker
        self.label = QLabel(title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)                         # unbestimmt, bis die erste Meldung kommt
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.clicked.connect(self.request_cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignRight)
        worker.progress.connect(self._progress)
        for signal in (worker.done, worker.failed, worker.cancelled):
            signal.connect(lambda *_a: self.accept())

    def _progress(self, done: int, total: int, text: str) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        if text:
            self.label.setText(text)

    def request_cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Wird abgebrochen …")
        self.worker.cancel()

    def reject(self) -> None:                           # Esc / Fenster schließen = abbrechen
        if self.worker.isRunning():
            self.request_cancel()
        else:
            super().reject()


def run_task(parent: QWidget | None, title: str, function: Callable, *args, on_done: Callable | None = None,
             on_error: Callable[[str], None] | None = None, on_cancel: Callable | None = None,
             show_dialog: bool = True, **kwargs) -> TaskWorker:
    """`function(ctx, *args, **kwargs)` im Hintergrund starten; Ergebnis/Fehler/Abbruch kommen im UI-Thread an."""
    worker = TaskWorker(function, *args, **kwargs)
    if on_done:
        worker.done.connect(on_done)
    if on_error:
        worker.failed.connect(on_error)
    if on_cancel:
        worker.cancelled.connect(on_cancel)
    worker.finished.connect(worker.deleteLater)
    if show_dialog:
        dialog = TaskDialog(parent, title, worker)
        worker.dialog = dialog                         # am Leben halten
        dialog.show()
    worker.start()
    return worker
