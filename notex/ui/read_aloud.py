"""Vorlesen (Block V11): Satz für Satz über QtTextToSpeech, der gerade gelesene Satz wird hervorgehoben.

- `QtEngine` hüllt QTextToSpeech ein (Windows SAPI/WinRT, Linux speech-dispatcher) – offline, nichts geht ins Netz.
- `Reader` reiht Sätze nacheinander ein (nicht den ganzen Text auf einmal): so lässt sich jeder Satz zuverlässig
  markieren, unabhängig davon, ob die Engine Wort-Positionen meldet. Pause, Weiter, Stopp, Tempo, Stimme.
- Ziele: `EditorTarget` (Notiz: ab Cursor, Markierung oder Anfang) und `PdfTarget` (PDF: ab Seitenanfang oder
  Markierung, über die folgenden Seiten; Markierung über die Zeichen-Rechtecke von PDFium).
- `ReadAloudBar` ist die Bedienleiste unten im Fenster.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QSlider

from notex.core import sentences
from notex.ui.widgets import IconButton


# ---- Engine -----------------------------------------------------------------------------------------------------
class QtEngine(QObject):
    """Dünne Hülle um QTextToSpeech: `say`, `pause`, `resume`, `stop`, Tempo, Stimmen; `done` nach jedem Satz."""
    done = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtTextToSpeech import QTextToSpeech
        self._speech = QTextToSpeech(self)
        self._speaking = False
        self._speech.stateChanged.connect(self._state)

    def _state(self, state) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech
        if state == QTextToSpeech.State.Speaking:
            self._speaking = True
        elif state == QTextToSpeech.State.Ready and self._speaking:
            self._speaking = False
            self.done.emit()

    def say(self, text: str) -> None:
        self._speech.say(text)

    def pause(self) -> None:
        self._speech.pause()

    def resume(self) -> None:
        self._speech.resume()

    def stop(self) -> None:
        self._speaking = False
        self._speech.stop()

    def set_rate(self, rate: float) -> None:
        self._speech.setRate(max(-1.0, min(1.0, rate)))

    def voices(self) -> list[str]:
        return [voice.name() for voice in self._speech.availableVoices()]

    def set_voice(self, name: str) -> None:
        for voice in self._speech.availableVoices():
            if voice.name() == name:
                self._speech.setVoice(voice)
                return


# ---- Ziele ------------------------------------------------------------------------------------------------------
@dataclass
class Sentence:
    text: str
    start: int
    end: int


class EditorTarget:
    """Notiz: ab Cursor (aktueller Satz), nur die Markierung oder ab Anfang."""

    def __init__(self, editor, mode: str = "cursor") -> None:
        self.editor = editor
        self.path = getattr(editor, "path", None)
        text = editor.toPlainText()
        cursor = editor.textCursor()
        spans = sentences.split(text)
        if mode == "selection" and cursor.hasSelection():
            low, high = cursor.selectionStart(), cursor.selectionEnd()
            spans = [(max(s, low), min(e, high)) for s, e in spans if e > low and s < high]
            first = 0
        elif mode == "start":
            first = 0
        else:
            first = sentences.sentence_at(spans, cursor.position()) if spans else 0
        self.sentences = [Sentence(text[s:e], s, e) for s, e in spans[first:]]

    def highlight(self, sentence: Sentence | None) -> None:
        if sentence is None:
            self.editor.set_read_mark(None)
        else:
            self.editor.set_read_mark(sentence.start, sentence.end)


class PdfTarget:
    """PDF: ab Anfang der aktuellen Seite (bis zum Ende) oder nur die Markierung; Sätze über Seiten hinweg."""

    def __init__(self, page, mode: str = "page") -> None:
        import pypdfium2 as pdfium
        self.page = page
        self.path = page.path
        self.document = pdfium.PdfDocument(page.data)
        self.pages: list[tuple[int, int, object]] = []        # (Seite, Anfang im Gesamttext, Textseite)
        parts: list[str] = []
        offset = 0
        start_page = page.canvas.current_page
        selected = page.canvas.selected_text().strip() if mode == "selection" else ""
        if selected:
            start_page = page.canvas.selection_page
        last = start_page if selected else len(self.document) - 1
        for index in range(start_page, last + 1):
            textpage = self.document[index].get_textpage()
            count = textpage.count_chars()
            text = textpage.get_text_range(0, count) if count else ""
            self.pages.append((index, offset, textpage))
            parts.append(text)
            offset += len(text) + 2
        full = "\n\n".join(parts).replace("\r\n", "\n ").replace("\r", " ").replace("￾", "-")
        spans = sentences.split(full)
        if selected:
            low = full.find(selected[: max(10, min(len(selected), 40))])
            if low >= 0:
                high = low + len(selected)
                spans = [(max(s, low), min(e, high)) for s, e in spans if e > low and s < high]
        self.sentences = [Sentence(full[s:e], s, e) for s, e in spans]

    def _locate(self, position: int):
        for index, offset, textpage in reversed(self.pages):
            if position >= offset:
                return index, position - offset, textpage
        return self.pages[0][0], 0, self.pages[0][2]

    def rects(self, sentence: Sentence) -> tuple[int, list[QRectF]]:
        """Seite und Rechtecke (Ansichts-Punkte) des Satzes – auf der Seite, auf der er beginnt."""
        from notex.core import pdfannot
        page_index, char, textpage = self._locate(sentence.start)
        count = max(1, min(sentence.end - sentence.start, textpage.count_chars() - char))
        geom = pdfannot.page_geometry(self.page.data, page_index)
        out = []
        try:
            for i in range(textpage.count_rects(char, count)):
                left, bottom, right, top = textpage.get_rect(i)
                x0, y0, x1, y1 = geom.rect_to_view((left, bottom, right, top))
                out.append(QRectF(x0, y0, x1 - x0, y1 - y0))
        except Exception:                                 # noqa: BLE001 – ohne Rechtecke wird nur vorgelesen
            pass
        return page_index, out

    def highlight(self, sentence: Sentence | None) -> None:
        canvas = self.page.canvas
        if sentence is None:
            canvas.current_highlight = None
        else:
            page_index, rects = self.rects(sentence)
            canvas.current_highlight = (page_index, rects) if rects else None
            if not self._visible(page_index, rects):
                top = rects[0].topLeft() if rects else None
                canvas.go_to(page_index, top)
        canvas.viewport().update()

    def _visible(self, page_index: int, rects: list[QRectF]) -> bool:
        """Liegt der Satz schon ganz im sichtbaren Bereich? Dann nicht scrollen (ruhiges Mitlesen)."""
        canvas = self.page.canvas
        layout = getattr(canvas, "layout_", None)
        if not rects or layout is None or page_index >= len(layout.pages):
            return canvas.current_page == page_index and not rects
        top = layout.pages[page_index].y + min(r.top() for r in rects) * layout.scale
        bottom = layout.pages[page_index].y + max(r.bottom() for r in rects) * layout.scale
        first = canvas.verticalScrollBar().value()
        return top >= first and bottom <= first + canvas.viewport().height()

    def close(self) -> None:
        for _index, _offset, textpage in self.pages:
            try:
                textpage.close()
            except Exception:                             # noqa: BLE001
                pass
        self.document.close()


# ---- Steuerung --------------------------------------------------------------------------------------------------
class Reader(QObject):
    """Liest die Sätze eines Ziels nacheinander; `progress(index, total)`, `finished()`."""
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, engine, target, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.target = target
        self.index = -1
        self.paused = False
        self.running = False
        engine.done.connect(self._next)

    @property
    def total(self) -> int:
        return len(self.target.sentences)

    def start(self, index: int = 0) -> None:
        self.running = True
        self.paused = False
        self.index = index - 1
        self._next()

    def _next(self) -> None:
        if not self.running or self.paused:
            return
        self.index += 1
        if self.index >= self.total:
            self.stop()
            return
        sentence = self.target.sentences[self.index]
        try:
            self.target.highlight(sentence)
        except RuntimeError:                              # Tab/Editor inzwischen geschlossen
            self.stop()
            return
        self.progress.emit(self.index, self.total)
        self.engine.say(sentence.text)

    def toggle_pause(self) -> None:
        if not self.running:
            return
        if self.paused:
            self.paused = False
            self.engine.resume()
        else:
            self.paused = True
            self.engine.pause()

    def skip(self, step: int) -> None:
        """Satz vor/zurück (Pfeiltasten in der Leiste)."""
        if not self.running:
            return
        self.engine.stop()
        self.paused = False
        self.index = max(-1, min(self.total - 1, self.index + step) - 1)
        self._next()

    def stop(self) -> None:
        was_running = self.running
        self.running = False
        self.paused = False
        try:
            self.engine.done.disconnect(self._next)
        except (RuntimeError, TypeError):
            pass
        self.engine.stop()
        try:
            self.target.highlight(None)
        except RuntimeError:                              # Ziel schon weg
            pass
        if hasattr(self.target, "close"):
            self.target.close()
        if was_running:
            self.finished.emit()


class ReadAloudBar(QFrame):
    """Bedienleiste: Pause/Weiter, Satz zurück/vor, Stopp, Tempo, Stimme, „Satz 3 / 40“."""
    rate_changed = Signal(float)
    voice_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ReadAloudBar")
        self.reader: Reader | None = None
        self.pause_button = IconButton("pause", "Pause / Weiter  F8")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.back_button = IconButton("chevron-left", "Satz zurück  ←")
        self.back_button.clicked.connect(lambda: self.reader.skip(-1) if self.reader else None)
        self.next_button = IconButton("chevron-right", "Satz vor  →")
        self.next_button.clicked.connect(lambda: self.reader.skip(1) if self.reader else None)
        self.stop_button = IconButton("square", "Stopp  Shift+F8 / Esc")
        self.stop_button.clicked.connect(self.stop)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(-10, 10)
        self.rate.setFixedWidth(120)
        self.rate.setToolTip("Tempo")
        self.rate.valueChanged.connect(lambda v: self.rate_changed.emit(v / 10))
        self.voice = QComboBox()
        self.voice.setToolTip("Stimme")
        self.voice.activated.connect(lambda _i: self.voice_changed.emit(self.voice.currentText()))
        self.label = QLabel()
        self.label.setObjectName("SettingsNote")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(QLabel("Vorlesen"))
        for widget in (self.back_button, self.pause_button, self.next_button, self.stop_button):
            row.addWidget(widget)
        row.addWidget(QLabel("Tempo"))
        row.addWidget(self.rate)
        row.addWidget(self.voice)
        row.addWidget(self.label, 1)
        self.hide()

    def attach(self, reader: Reader, voices: list[str], voice: str = "", rate: float = 0.0) -> None:
        self.reader = reader
        reader.progress.connect(lambda index, total: self.label.setText(f"Satz {index + 1} / {total}"))
        reader.finished.connect(self._finished)
        self.voice.blockSignals(True)
        self.voice.clear()
        self.voice.addItems(voices)
        if voice in voices:
            self.voice.setCurrentText(voice)
        self.voice.setVisible(bool(voices))
        self.voice.blockSignals(False)
        self.rate.blockSignals(True)
        self.rate.setValue(round(rate * 10))
        self.rate.blockSignals(False)
        self.label.setText("")
        self.show()

    def toggle_pause(self) -> None:
        if self.reader is not None:
            self.reader.toggle_pause()
            from notex.theme.icons import icon
            self.pause_button.setIcon(icon("play" if self.reader.paused else "pause"))

    def stop(self) -> None:
        if self.reader is not None:
            self.reader.stop()

    def _finished(self) -> None:
        self.reader = None
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.stop()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.reader is not None:
            self.reader.skip(-1 if event.key() == Qt.Key.Key_Left else 1)
            return
        super().keyPressEvent(event)
