"""Dialoge für Block V3: Stil für Stift und Formen, eigener Stempel.

Die Dialoge sammeln nur Eingaben; gezeichnet wird in `notex.core.pdfdraw` / `notex.core.pdfstamps`.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from notex.core import pdfstamps
from notex.core.pdfdraw import Style
from notex.core.pdfpages import PdfEditError
from notex.ui.pdf_pages_ui import _buttons, _error_label

DASH_LABELS = [("solid", "Durchgezogen"), ("dashed", "Gestrichelt"), ("dotted", "Gepunktet")]


class ColorButton(QPushButton):
    """Knopf, der eine Farbe zeigt und per Klick den Farbwähler öffnet."""

    def __init__(self, color: str, title: str = "Farbe") -> None:
        super().__init__()
        self.title = title
        self.setFixedWidth(64)
        self.clicked.connect(self._choose)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = QColor(color).name()
        self.setStyleSheet(f"background: {self.color}; border: 1px solid #888; min-height: 18px")
        self.setToolTip(self.color)

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.color), self, self.title)
        if chosen.isValid():
            self.set_color(chosen.name())


class DrawStyleDialog(QDialog):
    """Farbe, Stärke, Füllung, Deckkraft, Linienstil – für den Stift zusätzlich die Glättung."""

    def __init__(self, parent: QWidget | None, style: Style, pen: bool = False, can_apply: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stil: Stift" if pen else "Stil: Formen")
        self.pen = pen
        self.color = ColorButton(style.color, "Linienfarbe")
        self.width = QDoubleSpinBox()
        self.width.setRange(0.25, 50)
        self.width.setSingleStep(0.5)
        self.width.setSuffix(" pt")
        self.width.setValue(style.width)
        self.fill_on = QCheckBox("Füllen")
        self.fill_on.setChecked(bool(style.fill))
        self.fill = ColorButton(style.fill or "#fff3a0", "Füllfarbe")
        self.fill.setEnabled(bool(style.fill))
        self.fill_on.toggled.connect(self.fill.setEnabled)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(round(style.opacity * 100))
        self.opacity_label = QLabel()
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v} %"))
        self.opacity_label.setText(f"{self.opacity.value()} %")
        self.dash = QComboBox()
        for key, label in DASH_LABELS:
            self.dash.addItem(label, key)
        self.dash.setCurrentIndex(max(0, self.dash.findData(style.dash)))
        self.smooth = QCheckBox("Striche glätten")
        self.smooth.setChecked(style.smooth)
        form = QFormLayout()
        form.addRow("Linie", self.color)
        form.addRow("Stärke", self.width)
        if not pen:
            fill_row = QHBoxLayout()
            fill_row.addWidget(self.fill_on)
            fill_row.addWidget(self.fill)
            fill_row.addStretch(1)
            form.addRow("Füllung", fill_row)
            form.addRow("Linienstil", self.dash)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity, 1)
        opacity_row.addWidget(self.opacity_label)
        form.addRow("Deckkraft", opacity_row)
        if pen:
            form.addRow("", self.smooth)
        self.apply_selected = QCheckBox("Auch auf das ausgewählte Objekt anwenden")
        self.apply_selected.setVisible(can_apply)
        self.apply_selected.setChecked(can_apply)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.apply_selected)
        layout.addWidget(_buttons(self, "Übernehmen", self.accept))

    def style(self) -> Style:
        return Style(self.color.color, self.width.value(),
                     self.fill.color if self.fill_on.isChecked() and not self.pen else None,
                     self.opacity.value() / 100, self.dash.currentData() if not self.pen else "solid",
                     self.smooth.isChecked())


class CustomStampDialog(QDialog):
    """Eigenen Stempel anlegen: Text und Farbe oder ein Bild (PNG/JPEG); optional Datum und Name."""

    def __init__(self, parent: QWidget | None, start_dir: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("Eigener Stempel")
        self.start_dir = start_dir
        self.image_bytes: bytes | None = None
        self.text = QLineEdit()
        self.text.setPlaceholderText("z. B. KOPIE, ZUR ABLAGE, BEZAHLT")
        self.text.setMaxLength(80)
        self.color = ColorButton("#c02020", "Stempelfarbe")
        self.image_label = QLabel("Kein Bild")
        self.image_label.setObjectName("SettingsNote")
        choose = QPushButton("Bild wählen …")
        choose.clicked.connect(lambda: self.choose_image())
        clear = QPushButton("Bild entfernen")
        clear.clicked.connect(self.clear_image)
        image_row = QHBoxLayout()
        for widget in (choose, clear, self.image_label):
            image_row.addWidget(widget)
        image_row.addStretch(1)
        self.with_date = QCheckBox("Datum und Uhrzeit beim Setzen einfügen")
        self.with_user = QCheckBox("Namen einfügen (Einstellungen › Editor › PDF bearbeiten)")
        note = QLabel("Eigene Stempel liegen im Ordner stamps/ neben der App und wandern mit.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        self.error = _error_label()
        form = QFormLayout()
        form.addRow("Text", self.text)
        form.addRow("Farbe", self.color)
        form.addRow("Bild", image_row)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.with_date)
        layout.addWidget(self.with_user)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Anlegen", self._accept))
        self.resize(460, 0)

    def choose_image(self, path: str | Path | None = None) -> bool:
        if path is None:
            path, _f = QFileDialog.getOpenFileName(self, "Bild für den Stempel", str(self.start_dir),
                                                   "Bilder (*.png *.jpg *.jpeg)")
            if not path:
                return False
        try:
            content = Path(path).read_bytes()
        except OSError as error:
            self.error.setText(f"Nicht lesbar: {error}")
            return False
        if not pdfstamps.image_kind(content) or QImage.fromData(content).isNull():
            self.error.setText("Nur PNG- oder JPEG-Bilder")
            return False
        if len(content) > pdfstamps.MAX_IMAGE:
            self.error.setText("Das Bild ist zu groß (höchstens 5 MB)")
            return False
        self.image_bytes = content
        self.image_label.setText(Path(path).name)
        self.error.clear()
        return True

    def clear_image(self) -> None:
        self.image_bytes = None
        self.image_label.setText("Kein Bild")

    def values(self) -> dict:
        text = self.text.text().strip()
        if not text and not self.image_bytes:
            raise PdfEditError("Bitte Text eingeben oder ein Bild wählen")
        return {"text": text, "color": self.color.color, "image": self.image_bytes,
                "with_date": self.with_date.isChecked(), "with_user": self.with_user.isChecked()}

    def _accept(self) -> None:
        try:
            self.values()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()
