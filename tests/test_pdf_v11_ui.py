"""V11 in der Oberfläche: Druckdialog, „In PDF drucken“, Ausgabe auf einen QPrinter (PDF-Ausgabe statt echtem Gerät),
Vorlesen mit Satz-Hervorhebung in Notiz und PDF (Fake-Engine – die echte Sprachausgabe hängt vom System ab)."""
import io

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from notex.core import pdfprint as P  # noqa: E402
from notex.platform import Availability  # noqa: E402
from notex.ui.pdf_print_ui import TO_PDF, PrintDialog, print_on  # noqa: E402
from pdffixtures import assert_valid_pdf  # noqa: E402
from pdfhelp import make_pdf  # noqa: E402


class FakeEngine(QObject):
    done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.said: list[str] = []
        self.state = "ready"
        self.rate = 0.0
        self.voice = ""

    def say(self, text: str) -> None:
        self.said.append(text)
        self.state = "speaking"

    def pause(self) -> None:
        self.state = "paused"

    def resume(self) -> None:
        self.state = "speaking"

    def stop(self) -> None:
        self.state = "ready"

    def set_rate(self, rate: float) -> None:
        self.rate = rate

    def voices(self) -> list[str]:
        return ["Anna", "Max"]

    def set_voice(self, name: str) -> None:
        self.voice = name


def open_pdf(win, name: str, data: bytes):
    path = win.root / name
    path.write_bytes(data)
    page = win.tabs.open_viewer(path, "pdf")
    page.resize(1000, 800)
    page.show()
    QApplication.processEvents()
    return page


def open_note(win, text: str, name: str = "notiz.md"):
    path = win.root / name
    path.write_text(text, encoding="utf-8")
    win.tabs.open_file(path)
    QApplication.processEvents()
    return win.tabs.current_editor()


@pytest.fixture
def engine(win):
    fake = FakeEngine()
    win.speech_engine_factory = lambda: fake
    return fake


def pages(data: bytes) -> list[str]:
    return [p.extract_text().strip() for p in PdfReader(io.BytesIO(data)).pages]


# ---- Drucken ----------------------------------------------------------------------------------------------------
def test_print_to_pdf_file_with_range_and_copies(win):
    tab = open_pdf(win, "brief.pdf", make_pdf(["Eins", "Zwei", "Drei"], outline=False, title=""))
    before = tab.path.read_bytes()
    target = win.root / "druck.pdf"
    options = P.PrintOptions(pages="2-3", copies=2, collate=False)
    assert tab.print_pdf(options, None, target, wait=True)
    data = target.read_bytes()
    assert_valid_pdf(data)
    assert pages(data) == ["Zwei", "Zwei", "Drei", "Drei"]
    assert tab.path.read_bytes() == before and not tab.is_dirty            # Original unverändert


def test_print_to_pdf_refuses_own_file(win):
    tab = open_pdf(win, "selbst.pdf", make_pdf(["A"], outline=False, title=""))
    before = tab.path.read_bytes()
    assert not tab.print_pdf(P.PrintOptions(), None, tab.path, wait=True)
    assert tab.path.read_bytes() == before


def test_print_dialog_options_and_validation(win):
    dialog = PrintDialog(None, 5, current=1, names=["Büro-Drucker"])
    assert dialog.destination() == "Büro-Drucker" and dialog.setup.isEnabled()
    dialog.target.setCurrentIndex(dialog.target.findData(TO_PDF))
    assert dialog.destination() is None and not dialog.setup.isEnabled()
    dialog.this_page.setChecked(True)
    dialog.copies.setValue(3)
    dialog.collate.setChecked(False)
    dialog.fit.setCurrentIndex(dialog.fit.findData("actual"))
    dialog.paper.setCurrentIndex(dialog.paper.findData("letter"))
    dialog.annotations.setChecked(False)
    options = dialog.options()
    assert (options.pages, options.copies, options.collate, options.fit, options.paper, options.annotations) == \
        ("2", 3, False, "actual", "letter", False)
    dialog.range.setText("9-12")
    dialog.range.textEdited.emit("9-12")
    assert dialog.range_button.isChecked()
    dialog._accept()
    assert dialog.error.text() and dialog.result() == 0


def test_print_on_qprinter_writes_all_sheets(win, tmp_path):
    from PySide6.QtPrintSupport import QPrinter
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    out = tmp_path / "geraet.pdf"
    printer.setOutputFileName(str(out))
    data = make_pdf(["Hoch", "Quer"], outline=False, title="")
    calls = []
    count = print_on(printer, data, P.PrintOptions(copies=2), lambda done, total: calls.append((done, total)))
    assert count == 4 and calls[-1] == (4, 4)
    assert len(PdfReader(io.BytesIO(out.read_bytes())).pages) == 4


def test_print_on_cancel_stops_early(win, tmp_path):
    from PySide6.QtPrintSupport import QPrinter
    printer = QPrinter()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "abbruch.pdf"))
    count = print_on(printer, make_pdf(["a", "b", "c"], outline=False, title=""), P.PrintOptions(),
                     lambda done, total: False)
    assert count == 1


def test_print_on_printer_from_viewer(win, tmp_path):
    from PySide6.QtPrintSupport import QPrinter
    tab = open_pdf(win, "geraet.pdf", make_pdf(["x", "y"], outline=False, title=""))
    printer = QPrinter()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "ausgabe.pdf"))
    notices = []
    tab.notice.connect(notices.append)
    assert tab.print_pdf(P.PrintOptions(), "Testdrucker", printer=printer)
    assert any("2 Blatt" in n for n in notices)


def test_print_buttons_and_palette(win):
    tab = open_pdf(win, "knopf.pdf", make_pdf(["x"], outline=False, title=""))
    assert tab.print_button.isVisibleTo(tab) or tab.print_button.parent() is not None
    ids = {c.id for c in win.registry.all()}
    assert {"pdf:print", "speech:read", "speech:selection", "speech:start", "speech:stop"} <= ids
    win.registry.get("pdf:print").callback()                 # Dialog (exec → 0) – darf nichts ändern
    assert not tab.is_dirty


# ---- Vorlesen: Notiz -------------------------------------------------------------------------------------------
def test_read_note_from_cursor_with_highlight(win, engine):
    editor = open_note(win, "Erster Satz. Zweiter Satz! Dritter Satz?\n")
    cursor = editor.textCursor()
    cursor.setPosition(15)
    editor.setTextCursor(cursor)
    assert win.read_aloud("cursor")
    assert engine.said == ["Zweiter Satz!"]
    assert win.read_bar.isVisibleTo(win) and win.read_bar.label.text() == "Satz 1 / 2"
    mark = editor._read_selections[0].cursor
    assert editor.toPlainText()[mark.selectionStart():mark.selectionEnd()] == "Zweiter Satz!"
    assert editor.textCursor().position() == 15                          # Nutzer-Cursor bleibt
    engine.done.emit()
    assert engine.said[-1] == "Dritter Satz?"
    engine.done.emit()                                                   # fertig
    assert win.read_bar.reader is None and not win.read_bar.isVisibleTo(win)
    assert editor._read_selections == []


def test_read_selection_and_start(win, engine):
    editor = open_note(win, "Eins ist hier. Zwei folgt. Drei endet.\n")
    cursor = editor.textCursor()
    cursor.setPosition(15)
    cursor.setPosition(26, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    assert win.read_aloud("selection")
    assert engine.said == ["Zwei folgt."]
    engine.done.emit()
    assert win.read_bar.reader is None
    assert win.read_aloud("start")
    assert engine.said[-1] == "Eins ist hier."
    win.stop_read_aloud()


def test_pause_skip_stop_and_settings(win, engine):
    open_note(win, "A eins. B zwei. C drei. D vier.\n")
    win.registry.get("speech:start").callback()
    win.toggle_read_aloud()                                              # F8 während des Lesens = Pause
    assert engine.state == "paused" and win.read_bar.reader.paused
    win.toggle_read_aloud()
    assert engine.state == "speaking" and not win.read_bar.reader.paused
    win.read_bar.reader.skip(2)
    assert engine.said[-1] == "C drei."
    win.read_bar.reader.skip(-1)
    assert engine.said[-1] == "B zwei."
    win.read_bar.rate.setValue(5)
    assert win.config["speech"]["rate"] == pytest.approx(0.5) and engine.rate == pytest.approx(0.5)
    win.read_bar.voice.setCurrentText("Max")
    win.read_bar.voice.activated.emit(1)
    assert win.config["speech"]["voice"] == "Max" and engine.voice == "Max"
    win.stop_read_aloud()
    assert win.read_bar.reader is None and engine.state == "ready"


def test_unavailable_speech_shows_hint(win, monkeypatch):
    from notex.platform import speech
    monkeypatch.setattr(speech, "available", lambda: Availability(False, "Keine Sprachausgabe gefunden."))
    open_note(win, "Hallo Welt.\n")
    assert not win.read_aloud("start")
    assert "Sprachausgabe" in win.toast.text_label.text()
    assert win.read_bar.reader is None


def test_closing_note_stops_reading(win, engine):
    editor = open_note(win, "Eins. Zwei. Drei.\n", "weg.md")
    assert win.read_aloud("start")
    win.tabs.close_paths_under(editor.path)
    QApplication.processEvents()
    assert win.read_bar.reader is None


# ---- Vorlesen: PDF ---------------------------------------------------------------------------------------------
def test_read_pdf_from_page_highlights_and_follows_pages(win, engine):
    tab = open_pdf(win, "lesen.pdf", make_pdf(["Hallo Welt. Zweiter Satz.", "Seite zwei ist hier."],
                                             outline=False, title=""))
    tab.read_button.click()
    assert engine.said == ["Hallo Welt."]
    highlight = tab.canvas.current_highlight
    assert highlight is not None and highlight[0] == 0 and highlight[1]
    engine.done.emit()
    assert engine.said[-1] == "Zweiter Satz."
    engine.done.emit()
    assert engine.said[-1] == "Seite zwei ist hier."
    assert tab.canvas.current_highlight[0] == 1
    engine.done.emit()
    assert win.read_bar.reader is None and tab.canvas.current_highlight is None


def test_read_pdf_without_text(win, engine):
    tab = open_pdf(win, "leer.pdf", make_pdf([""], outline=False, title=""))
    assert not win.read_aloud("page", tab)
    assert "keinen Text" in win.toast.text_label.text()
