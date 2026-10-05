"""PDF bearbeiten – Bausteine für den PDF-Tab: Seitenleiste mit Miniaturen (Drag & Drop), Dialoge zum Aufteilen und
Zusammenfügen, sicheres Schreiben (optional altes Original in den Papierkorb).

Die eigentlichen Änderungen macht der Qt-freie Kern (`notex.core.pdfpages`); hier nur Oberfläche und Dateien.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QGridLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from notex.core import fileops, pdfpages
from notex.theme.tokens import COLORS, SPACING

THUMB_WIDTH = 96
THUMBS_PER_TICK = 6


def write_pdf(path: Path, data: bytes, backup: bool = False) -> str:
    """PDF atomar schreiben. `backup`: die bisherige Datei vorher in den Papierkorb legen (einmal je Sitzung –
    entscheidet der Aufrufer). Liefert einen Hinweis für den Toast ("" = nichts Besonderes)."""
    path = Path(path)
    if not backup or not path.exists():
        fileops.atomic_write_bytes(path, data)
        return ""
    temp = path.with_name(f".{path.name}.fcknotes-neu")
    fileops.atomic_write_bytes(temp, data)
    note = "Original liegt im Papierkorb"
    try:
        from send2trash import send2trash
        send2trash(str(path))
    except Exception:                                    # noqa: BLE001 – kein Papierkorb (Netzlaufwerk, USB …)
        note = "Kein Papierkorb verfügbar – Original überschrieben"
    try:
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    return note


# ---- Seitenleiste ------------------------------------------------------------------------------------------------
class PageStrip(QListWidget):
    """Miniaturen aller Seiten. Mehrfachauswahl (Ctrl/Shift), Ziehen sortiert um, Klick springt zur Seite."""
    page_activated = Signal(int)
    order_changed = Signal(list)         # neue Reihenfolge als Liste alter Seitenindizes

    def __init__(self, document: QPdfDocument) -> None:
        super().__init__()
        self.setObjectName("PdfPages")
        self.doc = document
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.TopToBottom)
        self.setWrapping(False)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)
        self.setIconSize(QSize(THUMB_WIDTH, round(THUMB_WIDTH * 1.42)))
        self.setSpacing(SPACING.sm)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setFixedWidth(THUMB_WIDTH + 2 * SPACING.lg + 14)
        self.itemClicked.connect(lambda item: self.page_activated.emit(item.data(Qt.ItemDataRole.UserRole)))
        self._options = QPdfDocumentRenderOptions()
        self._options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        self._queue: list[int] = []
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_some)

    def rebuild(self) -> None:
        self.clear()
        for index in range(self.doc.pageCount()):
            item = QListWidgetItem(self._placeholder(index), str(index + 1))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            self.addItem(item)
        self._queue = list(range(self.doc.pageCount()))
        self._timer.start()

    def _thumb_size(self, index: int) -> QSize:
        size = self.doc.pagePointSize(index)
        w, h = max(1.0, size.width()), max(1.0, size.height())
        return QSize(THUMB_WIDTH, max(8, round(THUMB_WIDTH * h / w)))

    def _placeholder(self, index: int) -> QIcon:
        pixmap = QPixmap(self._thumb_size(index))
        pixmap.fill(QColor("#ffffff"))
        return QIcon(pixmap)

    def _render_some(self) -> None:
        for _ in range(THUMBS_PER_TICK):
            if not self._queue:
                self._timer.stop()
                return
            index = self._queue.pop(0)
            if index >= self.count():
                continue
            dpr = self.devicePixelRatioF()
            size = self._thumb_size(index)
            image = self.doc.render(index, QSize(round(size.width() * dpr), round(size.height() * dpr)),
                                    self._options)
            if image.isNull():
                continue
            framed = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
            framed.fill(QColor("#ffffff"))
            painter = QPainter(framed)
            painter.drawImage(0, 0, image)
            painter.setPen(QColor(COLORS.border))
            painter.drawRect(0, 0, framed.width() - 1, framed.height() - 1)
            painter.end()
            pixmap = QPixmap.fromImage(framed)
            pixmap.setDevicePixelRatio(dpr)
            self.item(index).setIcon(QIcon(pixmap))

    def selected_pages(self) -> list[int]:
        return sorted(self.row(item) for item in self.selectedItems())

    def select_page(self, index: int) -> None:
        """Aktuelle Seite nachziehen (beim Scrollen) – ohne eine Mehrfachauswahl zu zerstören."""
        if 0 <= index < self.count():
            self.setCurrentRow(index, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.scrollToItem(self.item(index))

    def dropEvent(self, event) -> None:
        before = [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]
        super().dropEvent(event)
        after = [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]
        if after != before and sorted(after) == list(range(len(after))):
            self.order_changed.emit(after)


# ---- Dialoge -----------------------------------------------------------------------------------------------------
class SplitDialog(QDialog):
    """Aufteilen: jede Seite einzeln, alle N Seiten oder nach Bereichen („1-3; 4-6“). Ergebnis: Gruppen + Ordner."""

    def __init__(self, parent: QWidget, count: int, folder: Path, stem: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF aufteilen")
        self.count = count
        self.groups: list[list[int]] = []
        self.folder = Path(folder)
        self.stem = stem
        self.single = QRadioButton("Jede Seite einzeln")
        self.chunks = QRadioButton("Alle")
        self.size = QSpinBox()
        self.size.setRange(1, max(1, count))
        self.size.setValue(min(2, max(1, count)))
        self.size.setSuffix(" Seiten ein neues PDF")
        self.ranges = QRadioButton("Nach Bereichen")
        self.ranges_field = QLineEdit()
        self.ranges_field.setPlaceholderText("z. B. 1-3; 4-6; 7-")
        self.ranges_field.textEdited.connect(lambda _t: self.ranges.setChecked(True))
        group = QButtonGroup(self)
        for button in (self.single, self.chunks, self.ranges):
            group.addButton(button)
        self.single.setChecked(True)
        self.folder_label = QLabel()
        self.folder_label.setObjectName("SettingsNote")
        self.folder_label.setWordWrap(True)
        choose = QPushButton("Ordner …")
        choose.clicked.connect(self._choose_folder)
        self.error = QLabel()
        self.error.setObjectName("SettingsNote")
        self.error.setStyleSheet(f"color: {COLORS.danger}")
        grid = QGridLayout()
        grid.addWidget(self.single, 0, 0, 1, 2)
        grid.addWidget(self.chunks, 1, 0)
        grid.addWidget(self.size, 1, 1)
        grid.addWidget(self.ranges, 2, 0)
        grid.addWidget(self.ranges_field, 2, 1)
        grid.addWidget(self.folder_label, 3, 0)
        grid.addWidget(choose, 3, 1, Qt.AlignmentFlag.AlignRight)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aufteilen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{count} Seiten – Teile werden als „{stem}_teil1.pdf“ … gespeichert."))
        layout.addLayout(grid)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self._show_folder()

    def _show_folder(self) -> None:
        self.folder_label.setText(f"Ziel: {self.folder}")

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Zielordner", str(self.folder))
        if chosen:
            self.folder = Path(chosen)
            self._show_folder()

    def compute(self) -> list[list[int]]:
        if self.single.isChecked():
            return pdfpages.every(self.count, 1)
        if self.chunks.isChecked():
            return pdfpages.every(self.count, self.size.value())
        return pdfpages.parse_groups(self.ranges_field.text(), self.count)

    def _accept(self) -> None:
        try:
            self.groups = self.compute()
        except pdfpages.PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class MergeDialog(QDialog):
    """Reihenfolge der zusammenzufügenden PDFs festlegen (Ziehen oder Hoch/Runter), weitere hinzufügen."""

    def __init__(self, parent: QWidget, paths: list[Path], start_dir: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDFs zusammenfügen")
        self.resize(520, 380)
        self.start_dir = start_dir
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for path in paths:
            self._add(path)
        add = QPushButton("Hinzufügen …")
        add.clicked.connect(self._add_files)
        up, down, remove = QPushButton("Nach oben"), QPushButton("Nach unten"), QPushButton("Entfernen")
        up.clicked.connect(lambda: self._shift(-1))
        down.clicked.connect(lambda: self._shift(1))
        remove.clicked.connect(lambda: [self.list.takeItem(self.list.row(i)) for i in self.list.selectedItems()])
        side = QVBoxLayout()
        for button in (add, up, down, remove):
            side.addWidget(button)
        side.addStretch(1)
        grid = QGridLayout()
        grid.addWidget(self.list, 0, 0)
        grid.addLayout(side, 0, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Zusammenfügen …")
        buttons.accepted.connect(lambda: self.accept() if self.list.count() >= 2 else None)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Reihenfolge = Reihenfolge im neuen PDF (Ziehen zum Umsortieren)."))
        layout.addLayout(grid)
        layout.addWidget(buttons)

    def _add(self, path: Path) -> None:
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        item.setToolTip(str(path))
        self.list.addItem(item)

    def _add_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "PDFs hinzufügen", str(self.start_dir), "PDF (*.pdf)")
        for path in chosen:
            self._add(Path(path))

    def _shift(self, direction: int) -> None:
        row = self.list.currentRow()
        target = row + direction
        if 0 <= row < self.list.count() and 0 <= target < self.list.count():
            item = self.list.takeItem(row)
            self.list.insertItem(target, item)
            self.list.setCurrentRow(target)

    def paths(self) -> list[Path]:
        return [Path(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count())]


TEXT_COLORS = [("Schwarz", "#1a1a1a"), ("Blau", "#2f6fdf"), ("Rot", "#d03030"), ("Grün", "#2f8f4f")]
MARK_COLORS = [("Gelb", "#ffd400"), ("Grün", "#7fd36b"), ("Blau", "#6cb6ff"), ("Rosa", "#ff8fc8"), ("Rot", "#ff6b6b")]


class TextDialog(QDialog):
    """Text für eine Haftnotiz oder Text auf der Seite (mit Schriftgröße, Farbe, Rahmen)."""

    def __init__(self, parent: QWidget, title: str, text: str = "", with_style: bool = False, size: float = 12.0,
                 color: str = "#1a1a1a", border: bool = False) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QPlainTextEdit
        self.setWindowTitle(title)
        self.resize(460, 260)
        self.edit = QPlainTextEdit(text)
        self.size = QSpinBox()
        self.size.setRange(6, 72)
        self.size.setValue(round(size))
        self.size.setSuffix(" pt")
        self.color = QComboBox()
        for name, value in TEXT_COLORS:
            self.color.addItem(name, value)
        index = self.color.findData(color)
        self.color.setCurrentIndex(max(0, index))
        self.border = QCheckBox("Rahmen")
        self.border.setChecked(border)
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit, 1)
        if with_style:
            row = QHBoxLayout()
            for widget in (QLabel("Größe"), self.size, QLabel("Farbe"), self.color, self.border):
                row.addWidget(widget)
            row.addStretch(1)
            layout.addLayout(row)
            note = QLabel("Schrift: Helvetica. Zeichen außerhalb von Westeuropäisch (z. B. Emoji) erscheinen als „?“.")
            note.setObjectName("SettingsNote")
            note.setWordWrap(True)
            layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.edit.setFocus()

    def values(self) -> tuple[str, float, str, bool]:
        return self.edit.toPlainText().strip(), float(self.size.value()), self.color.currentData(), \
            self.border.isChecked()
