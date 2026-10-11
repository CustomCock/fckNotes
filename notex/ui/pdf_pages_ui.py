"""Dialoge für die Seitenwerkzeuge (Block V1): leere Seite, Seiten ersetzen, Zuschneiden, Seitenbeschriftungen.

Die Dialoge sammeln nur Eingaben; die Arbeit machen die Qt-freien Funktionen in `notex.core.pdfpages`.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from notex.core import pdfpages
from notex.core.pdfpages import LABEL_STYLES, MM, LabelRange, PdfEditError
from notex.theme.tokens import COLORS

SCOPES = [("current", "Aktuelle Seite"), ("range", "Seiten …"), ("odd", "Ungerade Seiten (1, 3, 5 …)"),
          ("even", "Gerade Seiten (2, 4, 6 …)"), ("all", "Alle Seiten")]


def _error_label() -> QLabel:
    label = QLabel()
    label.setObjectName("SettingsNote")
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {COLORS.danger}")
    return label


def _buttons(dialog: QDialog, ok_text: str, accept) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    box.accepted.connect(accept)
    box.rejected.connect(dialog.reject)
    return box


class BlankPageDialog(QDialog):
    """Leere Seite vor/nach der aktuellen Seite; Größe wie die Nachbarseite, A4 oder Letter."""

    def __init__(self, parent: QWidget | None, current: int, count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Leere Seite einfügen")
        self.position = QComboBox()
        self.position.addItem(f"Nach Seite {current + 1}", current + 1)
        self.position.addItem(f"Vor Seite {current + 1}", current)
        self.position.addItem("Am Anfang", 0)
        self.position.addItem("Am Ende", count)
        self.size = QComboBox()
        self.size.addItem("Wie die Nachbarseite", "neighbor")
        self.size.addItem("A4 (210 × 297 mm)", "A4")
        self.size.addItem("Letter (8,5 × 11 in)", "Letter")
        form = QFormLayout()
        form.addRow("Wo", self.position)
        form.addRow("Größe", self.size)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_buttons(self, "Einfügen", self.accept))

    def values(self) -> tuple[int, str]:
        return self.position.currentData(), self.size.currentData()


class ReplaceDialog(QDialog):
    """Seitenbereich durch Seiten aus einem anderen PDF ersetzen (ein Schritt)."""

    def __init__(self, parent: QWidget | None, count: int, first: int, last: int, folder: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seiten ersetzen")
        self.count = count
        self.folder = Path(folder)
        self.source: Path | None = None
        self.source_count = 0
        self.first = QSpinBox()
        self.last = QSpinBox()
        for box, value in ((self.first, first), (self.last, last)):
            box.setRange(1, count)
            box.setValue(value + 1)
        self.file_label = QLabel("Noch keine Datei gewählt")
        self.file_label.setObjectName("SettingsNote")
        choose = QPushButton("PDF wählen …")
        choose.clicked.connect(lambda: self.choose())
        self.other_pages = QLineEdit()
        self.other_pages.setPlaceholderText("leer = alle, z. B. 1-3")
        self.error = _error_label()
        row = QHBoxLayout()
        row.addWidget(self.first)
        row.addWidget(QLabel("bis"))
        row.addWidget(self.last)
        row.addStretch(1)
        form = QFormLayout()
        form.addRow("Diese Seiten", row)
        form.addRow("Ersetzen durch", choose)
        form.addRow("", self.file_label)
        form.addRow("Seiten daraus", self.other_pages)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Ersetzen", self._accept))

    def choose(self, path: str | None = None) -> bool:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Ersatzseiten aus …", str(self.folder), "PDF (*.pdf)")
            if not path:
                return False
        try:
            self.source_count = pdfpages.page_count(Path(path).read_bytes())
        except (OSError, PdfEditError) as error:
            self.error.setText(str(error))
            return False
        self.source = Path(path)
        self.file_label.setText(f"{self.source.name} ({self.source_count} Seiten)")
        self.error.clear()
        return True

    def values(self) -> tuple[int, int, Path, list[int] | None]:
        if self.source is None:
            raise PdfEditError("Bitte ein PDF mit den Ersatzseiten wählen")
        text = self.other_pages.text().strip()
        pages = pdfpages.parse_ranges(text, self.source_count) if text else None
        return self.first.value() - 1, self.last.value() - 1, self.source, pages

    def _accept(self) -> None:
        try:
            self.values()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class CropDialog(QDialog):
    """Ränder in mm, für welche Seiten, weiße Ränder automatisch – und ein deutlicher Hinweis: kein Schwärzen."""

    def __init__(self, parent: QWidget | None, count: int, current: int,
                 margins_pt: tuple[float, float, float, float] = (0, 0, 0, 0), detect=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seiten zuschneiden")
        self.count, self.current, self.detect = count, current, detect
        self.boxes: list[QDoubleSpinBox] = []
        grid = QGridLayout()
        for i, label in enumerate(("Links", "Oben", "Rechts", "Unten")):
            box = QDoubleSpinBox()
            box.setRange(0, 2000)
            box.setDecimals(1)
            box.setSuffix(" mm")
            box.setValue(round(margins_pt[i] / MM, 1))
            self.boxes.append(box)
            grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            grid.addWidget(box, i // 2, (i % 2) * 2 + 1)
        auto = QPushButton("Weiße Ränder erkennen")
        auto.setToolTip("Misst die aktuelle Seite und trägt die Ränder ein")
        auto.clicked.connect(self.detect_white)
        auto.setEnabled(detect is not None)
        self.threshold = QSpinBox()
        self.threshold.setRange(128, 254)
        self.threshold.setValue(245)
        self.threshold.setPrefix("Schwelle ")
        self.threshold.setToolTip("Ab welcher Helligkeit (0–255) ein Punkt als „weiß“ gilt – kleiner für Scans "
                                  "mit grauem Rand")
        self.threshold.setEnabled(detect is not None)
        self.scope = QComboBox()
        for key, label in SCOPES:
            self.scope.addItem(label, key)
        self.range_field = QLineEdit()
        self.range_field.setPlaceholderText("z. B. 1-3, 7")
        self.range_field.textEdited.connect(lambda _t: self.scope.setCurrentIndex(1))
        self.reset = QCheckBox("Zuschnitt aufheben (ganze Seite zeigen)")
        self.reset.toggled.connect(lambda on: [b.setEnabled(not on) for b in self.boxes])
        note = QLabel("Zuschneiden blendet nur Ränder aus – der Inhalt bleibt vollständig in der Datei und lässt "
                      "sich wieder einblenden. Das ist <b>kein Schwärzen</b>.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        self.error = _error_label()
        scope_row = QHBoxLayout()
        scope_row.addWidget(self.scope)
        scope_row.addWidget(self.range_field, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        auto_row = QHBoxLayout()
        auto_row.addWidget(auto)
        auto_row.addWidget(self.threshold)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)
        layout.addLayout(scope_row)
        layout.addWidget(self.reset)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Zuschneiden", self._accept))

    def detect_white(self) -> None:
        try:
            margins = self.detect(self.current, self.threshold.value())
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        if margins is None:
            self.error.setText("Die Seite ist leer – nichts zu erkennen")
            return
        for box, value in zip(self.boxes, margins):
            box.setValue(round(value / MM, 1))

    def values(self) -> tuple[list[int], tuple[float, float, float, float] | None]:
        """(Seiten, Ränder in Punkt) – Ränder None = Zuschnitt aufheben."""
        pages = pdfpages.pages_for(self.scope.currentData(), self.count, self.current, self.range_field.text())
        if self.reset.isChecked():
            return pages, None
        return pages, tuple(box.value() * MM for box in self.boxes)

    def _accept(self) -> None:
        try:
            self.values()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class LabelsDialog(QDialog):
    """Seitenbeschriftungen: Bereiche (ab Seite, Stil, Präfix, Startwert) mit Vorschau."""

    def __init__(self, parent: QWidget | None, count: int, ranges: list[LabelRange]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seitenbeschriftungen")
        self.count = count
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ab Seite", "Stil", "Präfix", "Startwert"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for r in ranges or [LabelRange(0)]:
            self.add_row(r)
        add = QPushButton("Bereich hinzufügen")
        add.clicked.connect(lambda: self.add_row(LabelRange(min(self.count - 1, self._last_start() + 1))))
        remove = QPushButton("Bereich entfernen")
        remove.clicked.connect(self.remove_row)
        clear = QPushButton("Alle entfernen (1, 2, 3 …)")
        clear.clicked.connect(lambda: self.table.setRowCount(0) or self._preview())
        self.preview = QLabel()
        self.preview.setObjectName("SettingsNote")
        self.preview.setWordWrap(True)
        self.error = _error_label()
        row = QHBoxLayout()
        for button in (add, remove, clear):
            row.addWidget(button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(self.preview)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Übernehmen", self._accept))
        self.resize(560, 360)
        self._preview()

    def _last_start(self) -> int:
        starts = [self.table.cellWidget(r, 0).value() - 1 for r in range(self.table.rowCount())]
        return max(starts, default=-1)

    def add_row(self, r: LabelRange) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        start = QSpinBox()
        start.setRange(1, max(1, self.count))
        start.setValue(r.start + 1)
        style = QComboBox()
        for key, label in LABEL_STYLES.items():
            style.addItem(label, key)
        style.setCurrentIndex(max(0, style.findData(r.style)))
        prefix = QTableWidgetItem(r.prefix)
        first = QSpinBox()
        first.setRange(1, 99999)
        first.setValue(r.first)
        self.table.setCellWidget(row, 0, start)
        self.table.setCellWidget(row, 1, style)
        self.table.setItem(row, 2, prefix)
        self.table.setCellWidget(row, 3, first)
        for widget in (start, first):
            widget.valueChanged.connect(lambda _v: self._preview())
        style.currentIndexChanged.connect(lambda _i: self._preview())
        self.table.itemChanged.connect(lambda _item: self._preview())
        self._preview()

    def remove_row(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True) or \
            ([self.table.rowCount() - 1] if self.table.rowCount() else [])
        for row in rows:
            self.table.removeRow(row)
        self._preview()

    def ranges(self) -> list[LabelRange]:
        out = []
        for row in range(self.table.rowCount()):
            prefix_item = self.table.item(row, 2)
            out.append(LabelRange(self.table.cellWidget(row, 0).value() - 1, self.table.cellWidget(row, 1).currentData(),
                                  prefix_item.text() if prefix_item else "", self.table.cellWidget(row, 3).value()))
        return out

    def _preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        labels = pdfpages.label_list(self.ranges(), self.count)
        shown = ", ".join(labels[:12]) + (" …" if len(labels) > 12 else "")
        self.preview.setText(f"Vorschau: {shown}")

    def _accept(self) -> None:
        starts = [r.start for r in self.ranges()]
        if len(starts) != len(set(starts)):
            self.error.setText("Zwei Bereiche beginnen auf derselben Seite")
            return
        if any(not r.style and not r.prefix for r in self.ranges()):
            self.error.setText("Ein Bereich ohne Zahl braucht ein Präfix")
            return
        self.accept()
