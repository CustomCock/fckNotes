"""Dialoge für Block V9: Komprimieren, Seiten als Bilder, Bilder → PDF.

Die Dialoge sammeln nur Eingaben; gearbeitet wird in `notex.core.pdfconvert` (im Hintergrund, über die Seite bzw.
das Hauptfenster).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from notex.core import pdfconvert as C
from notex.core.pdfpages import PdfEditError, parse_ranges
from notex.ui.pdf_edit import MergeDialog
from notex.ui.pdf_images_ui import IMAGE_FILTER
from notex.ui.pdf_pages_ui import _buttons, _error_label


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SettingsNote")
    label.setWordWrap(True)
    return label


class CompressDialog(QDialog):
    """Profil wählen (oder eigene Werte); zeigt, ob das Ergebnis verlustbehaftet ist."""

    def __init__(self, parent: QWidget | None, size: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF komprimieren")
        self.profile = QComboBox()
        for key, profile in C.PROFILES.items():
            self.profile.addItem(profile.label, key)
        self.profile.addItem("Eigene Werte", "custom")
        self.profile.setCurrentIndex(1)
        self.dpi = QSpinBox()
        self.dpi.setRange(0, 1200)
        self.dpi.setSpecialValueText("nicht verkleinern")
        self.dpi.setSuffix(" dpi")
        self.quality = QSpinBox()
        self.quality.setRange(0, 100)
        self.quality.setSpecialValueText("nicht neu kodieren")
        self.unused = QCheckBox("Ungenutzte Schriften, Bilder und Objekte entfernen")
        self.streams = QCheckBox("Objekt-Streams (kleinere Struktur, ab PDF 1.5)")
        self.flate = QCheckBox("Streams stärker packen (langsamer)")
        self.lossy = _note("")
        form = QFormLayout()
        form.addRow("Profil", self.profile)
        form.addRow("Bilder höchstens", self.dpi)
        form.addRow("JPEG-Qualität", self.quality)
        form.addRow("", self.unused)
        form.addRow("", self.streams)
        form.addRow("", self.flate)
        self.error = _error_label()
        layout = QVBoxLayout(self)
        if size:
            layout.addWidget(QLabel(f"Aktuelle Größe: {C.human(size)}"))
        layout.addLayout(form)
        layout.addWidget(self.lossy)
        layout.addWidget(_note("Das Ergebnis siehst du vor dem Übernehmen (vorher/nachher). Frühere Speicherstände "
                               "in der Datei fallen dabei weg; Rückgängig geht mit Ctrl+Z."))
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Komprimieren", self._accept))
        self.profile.currentIndexChanged.connect(self._load_profile)
        for widget in (self.dpi, self.quality):
            widget.valueChanged.connect(self._update_note)
        self._load_profile()
        self.resize(460, 0)

    def _load_profile(self) -> None:
        key = self.profile.currentData()
        custom = key == "custom"
        for widget in (self.dpi, self.quality, self.unused, self.streams, self.flate):
            widget.setEnabled(custom)
        if not custom:
            profile = C.PROFILES[key]
            self.dpi.setValue(profile.dpi)
            self.quality.setValue(profile.jpeg_quality)
            self.unused.setChecked(profile.remove_unused)
            self.streams.setChecked(profile.object_streams)
            self.flate.setChecked(profile.recompress_flate)
        self._update_note()

    def _update_note(self) -> None:
        if self.dpi.value() or self.quality.value():
            self.lossy.setText("⚠ Verlustbehaftet: Bilder werden verkleinert bzw. als JPEG neu kodiert. Text, "
                               "Vektorgrafik und Strichzeichnungen bleiben verlustfrei.")
        else:
            self.lossy.setText("Verlustfrei: Bilder bleiben unverändert, nur die Struktur wird gepackt.")

    def value(self) -> C.Profile:
        key = self.profile.currentData()
        if key != "custom":
            return C.PROFILES[key]
        return C.Profile("custom", "Eigene Werte", self.dpi.value(), self.quality.value(), self.unused.isChecked(),
                         self.streams.isChecked(), self.flate.isChecked()).checked()

    def _accept(self) -> None:
        try:
            self.value()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class PageImagesDialog(QDialog):
    """Seiten als PNG oder JPG: Format, Auflösung, Seitenbereich, Zielordner."""

    def __init__(self, parent: QWidget | None, page_count: int, folder: Path, current: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seiten als Bilder speichern")
        self.page_count = page_count
        self.format = QComboBox()
        self.format.addItem("PNG (verlustfrei)", "png")
        self.format.addItem("JPG (kleiner)", "jpg")
        self.dpi = QSpinBox()
        self.dpi.setRange(36, 1200)
        self.dpi.setValue(150)
        self.dpi.setSuffix(" dpi")
        self.quality = QSpinBox()
        self.quality.setRange(10, 100)
        self.quality.setValue(90)
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(f"alle {page_count} Seiten – oder z. B. {current + 1} oder 1-3, 5")
        self.folder = QLineEdit(str(folder))
        choose = QPushButton("…")
        choose.setFixedWidth(32)
        choose.clicked.connect(self._choose)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(choose)
        form = QFormLayout()
        form.addRow("Format", self.format)
        form.addRow("Auflösung", self.dpi)
        form.addRow("JPG-Qualität", self.quality)
        form.addRow("Seiten", self.pages)
        form.addRow("Ordner", folder_row)
        self.format.currentIndexChanged.connect(lambda _i: self.quality.setEnabled(self.format.currentData() == "jpg"))
        self.quality.setEnabled(False)
        self.error = _error_label()
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_note("Dateinamen: <PDF-Name>-S<Seite>. Vorhandene Dateien werden nie überschrieben – "
                               "es kommt „(2)“ dazu."))
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Speichern", self._accept))
        self.resize(460, 0)

    def _choose(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Ordner wählen", self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def values(self) -> dict:
        pages = parse_ranges(self.pages.text(), self.page_count)
        folder = Path(self.folder.text().strip())
        if not folder.is_dir():
            raise PdfEditError("Diesen Ordner gibt es nicht")
        return {"pages": pages, "folder": folder, "dpi": self.dpi.value(), "fmt": self.format.currentData(),
                "quality": self.quality.value()}

    def _accept(self) -> None:
        try:
            self.values()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class ImagesToPdfDialog(MergeDialog):
    """Bilder in Reihenfolge bringen (Ziehen oder Hoch/Runter) und Seitengröße wählen."""

    def __init__(self, parent: QWidget, paths: list[Path], start_dir: Path) -> None:
        super().__init__(parent, paths, start_dir)
        self.setWindowTitle("Bilder zu PDF")
        self.size = QComboBox()
        self.size.addItem("Wie das Bild (Größe aus der Bildauflösung)", "image")
        self.size.addItem("A4 (hoch/quer nach Bild)", "a4")
        self.size.addItem("Letter (hoch/quer nach Bild)", "letter")
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 100)
        self.margin.setSuffix(" mm")
        self.margin.setDecimals(0)
        form = QFormLayout()
        form.addRow("Seitengröße", self.size)
        form.addRow("Rand", self.margin)
        layout = self.layout()
        layout.insertLayout(layout.count() - 1, form)
        box = self.findChild(QDialogButtonBox)
        if box is not None:
            box.button(QDialogButtonBox.StandardButton.Ok).setText("PDF erstellen …")
            box.accepted.disconnect()
            box.accepted.connect(lambda: self.accept() if self.list.count() >= 1 else None)
        label = self.findChild(QLabel)
        if label is not None:
            label.setText("Reihenfolge = Seitenreihenfolge im PDF (Ziehen zum Umsortieren). PNG, JPG, WebP, GIF, "
                          "BMP, TIFF; JPEGs werden unverändert übernommen.")
            label.setWordWrap(True)

    def _add_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Bilder hinzufügen", str(self.start_dir), IMAGE_FILTER)
        for path in chosen:
            self._add(Path(path))

    def options(self) -> tuple[str, float]:
        return self.size.currentData(), self.margin.value()

