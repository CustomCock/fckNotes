"""Dialoge für Block V7: Bilder im Dokument, Ersetzen, Zuschneiden und Eigenschaften eingefügter Bilder.

Die Dialoge sammeln nur Eingaben; gearbeitet wird in `notex.core.pdfimages` über die Seite (`PdfPage`), damit jede
Änderung ein Rückgängig-Schritt ist.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QRadioButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from notex.core.pdfimages import KIND_LABELS, PageImage
from notex.ui.pdf_pages_ui import _buttons, _error_label

IMAGE_FILTER = "Bilder (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff)"


class ImagesDialog(QDialog):
    """Alle Bilder im Seiteninhalt: anzeigen, exportieren (einzeln/alle), ersetzen, zuschneiden, löschen."""
    show_requested = Signal(int, int)
    export_requested = Signal(int, int)
    export_all_requested = Signal()
    replace_requested = Signal(int, int)
    crop_requested = Signal(int, int)
    delete_requested = Signal(int, int)

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bilder im Dokument")
        self.items: list[PageImage] = []
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Seite", "Art", "Pixel", "Format", "dpi", "Verwendet"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.doubleClicked.connect(lambda _i: self._emit(self.export_requested))
        self.export_button = QPushButton("Exportieren …")
        self.export_button.clicked.connect(lambda: self._emit(self.export_requested))
        self.export_all = QPushButton("Alle exportieren …")
        self.export_all.clicked.connect(self.export_all_requested)
        self.replace_button = QPushButton("Ersetzen …")
        self.replace_button.clicked.connect(lambda: self._emit(self.replace_requested))
        self.crop_button = QPushButton("Zuschneiden …")
        self.crop_button.clicked.connect(lambda: self._emit(self.crop_requested))
        self.delete_button = QPushButton("Löschen")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(lambda: self._emit(self.delete_requested))
        self.empty = QLabel("Auf den Seiten liegen keine Bilder (eingefügte Bilder sind Objekte – Werkzeug „Auswählen“).")
        self.empty.setObjectName("SettingsNote")
        self.empty.setWordWrap(True)
        note = QLabel("JPEG und JPEG 2000 werden unverändert exportiert, alles andere als PNG. Zuschneiden entfernt die "
                      "Pixel außerhalb wirklich. Bilder, die mehrfach vorkommen, lassen sich hier oder überall "
                      "ersetzen. Inline-Bilder: nur exportieren und löschen. Doppelklick = exportieren.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        row = QHBoxLayout()
        for button in (self.export_button, self.export_all, self.replace_button, self.crop_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.delete_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty)
        layout.addLayout(row)
        layout.addWidget(note)
        self.resize(640, 380)
        self.editable = True

    def set_items(self, items: list[PageImage], editable: bool = True) -> None:
        selected = self.current()
        self.items = items
        self.editable = editable
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            cells = [str(item.page + 1), KIND_LABELS.get(item.kind, item.kind),
                     f"{item.width_px} × {item.height_px}", item.format_label, str(round(item.dpi)),
                     f"{item.uses}×" if item.uses > 1 else "1×"]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if column in (0, 4, 5):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                cell.setToolTip(item.describe())
                self.table.setItem(row, column, cell)
        self.empty.setVisible(not items)
        self.table.setVisible(bool(items))
        self.export_all.setEnabled(bool(items))
        if selected is not None:
            again = next((r for r, i in enumerate(items) if (i.page, i.index) == (selected.page, selected.index)), None)
            if again is not None:
                self.table.selectRow(again)
        elif items:
            self.table.selectRow(0)
        self._update_buttons()

    def current(self) -> PageImage | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.items):
            return None
        return self.items[rows[0].row()]

    def select(self, page: int, index: int) -> None:
        row = next((r for r, i in enumerate(self.items) if (i.page, i.index) == (page, index)), None)
        if row is not None:
            self.table.selectRow(row)

    def _selected(self) -> None:
        self._update_buttons()
        item = self.current()
        if item is not None:
            self.show_requested.emit(item.page, item.index)

    def _update_buttons(self) -> None:
        item = self.current()
        self.export_button.setEnabled(item is not None)
        self.replace_button.setEnabled(self.editable and item is not None
                                       and (item.can_replace_here or item.can_replace_all))
        self.crop_button.setEnabled(self.editable and item is not None and item.can_crop)
        self.delete_button.setEnabled(self.editable and item is not None and item.can_delete)

    def _emit(self, signal) -> None:
        item = self.current()
        if item is not None:
            signal.emit(item.page, item.index)


class ReplaceImageDialog(QDialog):
    """Wo ersetzen (nur hier / überall) und ob das Seitenverhältnis des neuen Bilds erhalten bleibt."""

    def __init__(self, parent: QWidget | None, image: PageImage) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bild ersetzen")
        self.here = QRadioButton("Nur an dieser Stelle")
        self.everywhere = QRadioButton(f"Überall, wo dieses Bild vorkommt ({image.uses}×)")
        self.here.setEnabled(image.can_replace_here)
        self.everywhere.setEnabled(image.can_replace_all)
        (self.here if image.can_replace_here else self.everywhere).setChecked(True)
        self.keep = QCheckBox("Seitenverhältnis des neuen Bilds beibehalten (einpassen statt strecken)")
        self.keep.setChecked(True)
        self.here.toggled.connect(self.keep.setEnabled)
        self.keep.setEnabled(self.here.isChecked())
        note = QLabel("„Überall“ tauscht das Bild-Objekt selbst aus; das neue Bild füllt dann jeden alten Rahmen. "
                      "Danach wählst du die Bilddatei.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(image.describe()))
        layout.addWidget(self.here)
        layout.addWidget(self.everywhere)
        layout.addWidget(self.keep)
        layout.addWidget(note)
        layout.addWidget(_buttons(self, "Bild wählen …", self.accept))
        self.resize(460, 0)

    def values(self) -> tuple[bool, bool]:
        """(überall, Seitenverhältnis beibehalten)."""
        return self.everywhere.isChecked(), self.keep.isChecked()


class CropImageDialog(QDialog):
    """Zuschneiden per Tastatur: Ränder in Prozent; mit der Maus geht es über „Rahmen aufziehen“."""

    def __init__(self, parent: QWidget | None, image: PageImage) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bild zuschneiden")
        self.drag = False
        self.boxes: dict[str, QDoubleSpinBox] = {}
        form = QFormLayout()
        for key, label in (("left", "Links"), ("top", "Oben"), ("right", "Rechts"), ("bottom", "Unten")):
            box = QDoubleSpinBox()
            box.setRange(0.0, 99.0)
            box.setDecimals(1)
            box.setSuffix(" %")
            self.boxes[key] = box
            form.addRow(label, box)
        draw = QPushButton("Stattdessen Rahmen auf der Seite aufziehen")
        draw.clicked.connect(self._drag)
        note = QLabel("Abgeschnitten wird, wie das Bild auf der Seite erscheint. Die Pixel außerhalb verschwinden "
                      "aus der Datei; die Stelle bekommt ein eigenes Bild, andere Verwendungen bleiben.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        self.error = _error_label()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(image.describe()))
        layout.addLayout(form)
        layout.addWidget(draw)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Zuschneiden", self._accept))
        self.resize(420, 0)

    def _drag(self) -> None:
        self.drag = True
        self.accept()

    def margins(self) -> tuple[float, float, float, float]:
        return tuple(self.boxes[k].value() / 100 for k in ("left", "top", "right", "bottom"))

    def _accept(self) -> None:
        left, top, right, bottom = self.margins()
        if left + right >= 0.99 or top + bottom >= 0.99:
            self.error.setText("Es muss etwas vom Bild übrig bleiben")
            return
        if not any(self.margins()):
            self.error.setText("Bitte mindestens einen Rand angeben")
            return
        self.accept()


class ImagePropertiesDialog(QDialog):
    """Eingefügtes Bild: Drehwinkel und Deckkraft."""

    def __init__(self, parent: QWidget | None, angle: float, opacity: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bild-Eigenschaften")
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-359.0, 359.0)
        self.angle.setDecimals(1)
        self.angle.setSuffix(" °")
        self.angle.setValue(angle if angle <= 180 else angle - 360)
        self.angle.setToolTip("Im Uhrzeigersinn; negative Werte drehen nach links")
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(round(opacity * 100))
        form = QFormLayout()
        form.addRow("Drehung", self.angle)
        form.addRow("Deckkraft", self.opacity)
        note = QLabel("Schnell drehen: Taste R (rechts) bzw. Shift+R (links) am ausgewählten Bild. Größe ändern an den "
                      "Griffen; Shift beim Ziehen an einer Ecke verzerrt (nur bei geraden Winkeln).")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(_buttons(self, "Übernehmen", self.accept))
        self.resize(380, 0)

    def values(self) -> tuple[float, float]:
        return self.angle.value() % 360, self.opacity.value() / 100
