"""Drucken (Block V11): eigener Dialog mit den PDF-Optionen und die Ausgabe auf einen Drucker über QtPrintSupport.

Ein echter Drucker bekommt Seite für Seite ein mit PDFium gerendertes Bild (bis 300 dpi) in derselben Platzierung wie
„In PDF drucken“ (`notex.core.pdfprint`). Kopien und Sortierung erledigt fckNotes selbst – das klappt mit jedem
Treiber gleich. Die Treiber-Einstellungen (Duplex, Fach …) öffnet „Druckereinstellungen …“ (System-Dialog).
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from notex.core import pdfprint as P
from notex.core.pdfpages import PdfEditError, parse_ranges
from notex.ui.pdf_pages_ui import _buttons, _error_label

TO_PDF = "\x00pdf"                                        # Eintrag „In PDF-Datei“ in der Druckerliste
QT_PAGE_SIZES = {"a4": QPageSize.PageSizeId.A4, "letter": QPageSize.PageSizeId.Letter, "a3": QPageSize.PageSizeId.A3}
MAX_DPI = 300


def printers() -> list[str]:
    try:
        from PySide6.QtPrintSupport import QPrinterInfo
        return list(QPrinterInfo.availablePrinterNames())
    except ImportError:
        return []


class PrintDialog(QDialog):
    """Drucker (oder „In PDF-Datei“), Seiten, Kopien, Einpassen, Papier, Anmerkungen/Formular."""

    def __init__(self, parent: QWidget | None, page_count: int, current: int = 0, hint: str = "",
                 names: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Drucken")
        self.page_count = page_count
        self.current = current
        self.printer = None                               # QPrinter mit Treiber-Einstellungen (falls geöffnet)
        self.target = QComboBox()
        for name in (printers() if names is None else names):
            self.target.addItem(name, name)
        self.target.addItem("In PDF-Datei speichern", TO_PDF)
        self.setup = QPushButton("Druckereinstellungen …")
        self.setup.clicked.connect(self._driver_settings)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, 1)
        target_row.addWidget(self.setup)
        self.all_pages = QRadioButton("Alle")
        self.this_page = QRadioButton(f"Aktuelle Seite ({current + 1})")
        self.range_button = QRadioButton("Seiten:")
        self.range = QLineEdit()
        self.range.setPlaceholderText("z. B. 1-3, 5, 8-")
        self.range.textEdited.connect(lambda _t: self.range_button.setChecked(True))
        group = QButtonGroup(self)
        for button in (self.all_pages, self.this_page, self.range_button):
            group.addButton(button)
        self.all_pages.setChecked(True)
        pages_row = QHBoxLayout()
        for widget in (self.all_pages, self.this_page, self.range_button):
            pages_row.addWidget(widget)
        pages_row.addWidget(self.range, 1)
        self.copies = QSpinBox()
        self.copies.setRange(1, P.MAX_COPIES)
        self.collate = QCheckBox("Sortieren (1 2 3, 1 2 3)")
        self.collate.setChecked(True)
        copies_row = QHBoxLayout()
        copies_row.addWidget(self.copies)
        copies_row.addWidget(self.collate)
        copies_row.addStretch(1)
        self.fit = QComboBox()
        for key, label in P.FITS.items():
            self.fit.addItem(label, key)
        self.paper = QComboBox()
        for key, label in P.PAPER_LABELS.items():
            self.paper.addItem(label, key)
        self.auto_rotate = QCheckBox("Hoch/Quer automatisch nach der Seite")
        self.auto_rotate.setChecked(True)
        self.annotations = QCheckBox("Kommentare, Stempel und Zeichnungen drucken")
        self.annotations.setChecked(True)
        self.forms = QCheckBox("Formularfelder mit Inhalt drucken")
        self.forms.setChecked(True)
        form = QFormLayout()
        form.addRow("Drucker", target_row)
        form.addRow("Seiten", pages_row)
        form.addRow("Kopien", copies_row)
        form.addRow("Größe", self.fit)
        form.addRow("Papier", self.paper)
        form.addRow("", self.auto_rotate)
        form.addRow("", self.annotations)
        form.addRow("", self.forms)
        self.hint = QLabel(hint)
        self.hint.setObjectName("SettingsNote")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        self.error = _error_label()
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Drucken", self._accept))
        self.target.currentIndexChanged.connect(self._target_changed)
        self._target_changed()
        self.resize(520, 0)

    def _target_changed(self) -> None:
        to_pdf = self.target.currentData() == TO_PDF
        self.setup.setEnabled(not to_pdf)
        self.printer = None

    def _driver_settings(self) -> None:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPrinterName(self.target.currentData())
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.printer = printer

    def pages_text(self) -> str:
        if self.this_page.isChecked():
            return str(self.current + 1)
        if self.range_button.isChecked():
            return self.range.text().strip()
        return ""

    def options(self) -> P.PrintOptions:
        options = P.PrintOptions(pages=self.pages_text(), copies=self.copies.value(), collate=self.collate.isChecked(),
                                 fit=self.fit.currentData(), auto_rotate=self.auto_rotate.isChecked(),
                                 annotations=self.annotations.isChecked(), forms=self.forms.isChecked(),
                                 paper=self.paper.currentData()).checked()
        parse_ranges(options.pages, self.page_count)       # Bereich prüfen (Fehlermeldung im Dialog)
        return options

    def destination(self) -> str | None:
        """Druckername oder None für „In PDF-Datei“."""
        data = self.target.currentData()
        return None if data == TO_PDF else data

    def _accept(self) -> None:
        try:
            self.options()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


def print_on(printer, data: bytes, options: P.PrintOptions, progress=None) -> int:
    """Auf einen QPrinter drucken (UI-Thread). `progress(done, total)` darf False liefern = abbrechen.
    Liefert die Zahl gedruckter Blätter."""
    import pypdfium2 as pdfium
    options = options.checked()
    prepared = P.prepare(data, options)
    document = pdfium.PdfDocument(prepared)
    painter = None
    printed = 0
    try:
        order = P.page_order(len(document), options.copies, options.collate)
        printer.setCopyCount(1)
        printer.setFullPage(True)
        if options.paper in QT_PAGE_SIZES:
            printer.setPageSize(QPageSize(QT_PAGE_SIZES[options.paper]))
        dpi = min(printer.resolution(), MAX_DPI)
        for number, index in enumerate(order):
            page = document[index]
            width, height = page.get_size()
            if options.paper == "source":                       # Papier wie die Seite (hoch, Ausrichtung folgt)
                printer.setPageSize(QPageSize(QSizeF(min(width, height), max(width, height)), QPageSize.Unit.Point))
            portrait_paper = printer.pageLayout().pageSize().size(QPageSize.Unit.Point)
            paper_w, paper_h = portrait_paper.width(), portrait_paper.height()
            place = P.placement(width, height, paper_w, paper_h, options.fit, options.auto_rotate)
            printer.setPageOrientation(QPageLayout.Orientation.Landscape if place.landscape
                                       else QPageLayout.Orientation.Portrait)
            if painter is None:
                painter = QPainter()
                if not painter.begin(printer):
                    raise PdfEditError("Der Drucker lässt sich nicht öffnen")
            elif not printer.newPage():
                raise PdfEditError("Neue Seite fehlgeschlagen")
            scale = dpi / 72 * place.scale
            picture = page.render(scale=scale, draw_annots=False).to_pil().convert("RGB")
            page.close()
            raw = picture.tobytes()
            image = QImage(raw, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888)
            device = printer.resolution() / 72
            top = (place.paper_height - place.y - place.height) * device       # PDF-y nach oben → Gerät nach unten
            painter.drawImage(QRectF(place.x * device, top, place.width * device, place.height * device), image)
            printed += 1
            if progress is not None and progress(number + 1, len(order)) is False:
                printer.abort()
                break
    finally:
        if painter is not None and painter.isActive():
            painter.end()
        document.close()
    return printed
