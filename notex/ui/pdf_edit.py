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


# ---- Formular-Panel ----------------------------------------------------------------------------------------------
class FormPanel(QWidget):
    """Alle Formularfelder untereinander: ausfüllen, „Übernehmen“ schreibt alles in einem Schritt (Ctrl+Z-fähig)."""
    apply_requested = Signal(dict)       # Feldname → Wert
    field_activated = Signal(object)     # FieldInfo (zum Feld springen)

    def __init__(self) -> None:
        super().__init__()
        from PySide6.QtWidgets import QHBoxLayout, QScrollArea
        self.setObjectName("PdfForm")
        self.setMinimumWidth(240)
        self.fields: list = []
        self.editors: dict[str, QWidget] = {}
        self.title = QLabel("Formular")
        self.title.setObjectName("SettingsNote")
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(SPACING.sm, SPACING.sm, SPACING.sm, SPACING.sm)
        self.grid.setVerticalSpacing(SPACING.sm)
        self.area.setWidget(self.inner)
        self.apply_button = QPushButton("Übernehmen")
        self.apply_button.clicked.connect(lambda: self.apply_requested.emit(self.changed_values()))
        reset = QPushButton("Zurücksetzen")
        reset.clicked.connect(lambda: self.set_fields(self.fields))
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(self.apply_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.area, 1)
        layout.addLayout(row)

    def set_fields(self, fields: list) -> None:
        from PySide6.QtWidgets import QCheckBox, QComboBox, QPlainTextEdit
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()                 # sofort weg, nicht erst beim nächsten deleteLater-Durchlauf
                item.widget().deleteLater()
        self.fields = list(fields)
        self.editors = {}
        editable = [f for f in self.fields if f.kind in ("text", "checkbox", "radio", "choice")]
        self.title.setText(f"Formular · {len(self.fields)} Felder" if self.fields else
                           "Keine Formularfelder – mit „Textfeld“/„Kästchen“ anlegen")
        for row, info in enumerate(self.fields):
            label = QPushButton(info.name + (" (schreibgeschützt)" if info.read_only else ""))
            label.setFlat(True)
            label.setStyleSheet("text-align: left; padding: 2px 0;")
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.setToolTip(f"Zum Feld springen (Seite {info.page + 1})")
            label.clicked.connect(lambda _c=False, i=info: self.field_activated.emit(i))
            if info.kind == "text" and info.multiline:
                editor = QPlainTextEdit(info.value)
                editor.setFixedHeight(64)
            elif info.kind == "text":
                editor = QLineEdit(info.value)
            elif info.kind == "checkbox":
                editor = QCheckBox()
                editor.setChecked(info.checked)
            elif info.kind in ("radio", "choice"):
                editor = QComboBox()
                editor.addItem("—", "")
                for option in info.options:
                    editor.addItem(option, option)
                editor.setCurrentIndex(max(0, editor.findData(info.value)))
                if info.kind == "choice" and info.value and editor.findData(info.value) < 0:
                    editor.addItem(info.value, info.value)
                    editor.setCurrentIndex(editor.count() - 1)
            else:
                editor = QLabel("(hier nicht ausfüllbar)" if info.kind != "signature" else "(digitale Signatur)")
            editor.setEnabled(not info.read_only and info in editable)
            self.grid.addWidget(label, row * 2, 0)
            self.grid.addWidget(editor, row * 2 + 1, 0)
            self.editors[info.name] = editor
        self.grid.setRowStretch(len(self.fields) * 2, 1)
        self.apply_button.setEnabled(bool(editable))

    def value_of(self, info):
        from PySide6.QtWidgets import QCheckBox, QComboBox, QPlainTextEdit
        editor = self.editors.get(info.name)
        if isinstance(editor, QPlainTextEdit):
            return editor.toPlainText()
        if isinstance(editor, QLineEdit):
            return editor.text()
        if isinstance(editor, QCheckBox):
            return editor.isChecked()
        if isinstance(editor, QComboBox):
            return editor.currentData() or ""
        return None

    def set_value(self, name: str, value) -> None:
        from PySide6.QtWidgets import QCheckBox, QComboBox, QPlainTextEdit
        editor = self.editors.get(name)
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText(str(value))
        elif isinstance(editor, QLineEdit):
            editor.setText(str(value))
        elif isinstance(editor, QCheckBox):
            editor.setChecked(bool(value))
        elif isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, editor.findData(value)))

    def changed_values(self) -> dict:
        out = {}
        for info in self.fields:
            if info.read_only or info.kind not in ("text", "checkbox", "radio", "choice"):
                continue
            value = self.value_of(info)
            before = info.checked if info.kind == "checkbox" else info.value
            if value is not None and value != before:
                out[info.name] = value
        return out


# ---- Unterschrift ------------------------------------------------------------------------------------------------
class _SignaturePad(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(480, 180)
        self.strokes: list[list[tuple[float, float]]] = []
        self.setCursor(Qt.CursorShape.CrossCursor)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QPen(QColor("#c8c8c8"), 1, Qt.PenStyle.DashLine))
        base = self.height() * 0.75
        painter.drawLine(20, round(base), self.width() - 20, round(base))
        painter.setPen(QPen(QColor("#1a2a6c"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        for stroke in self.strokes:
            for (x0, y0), (x1, y1) in zip(stroke, stroke[1:]):
                painter.drawLine(round(x0), round(y0), round(x1), round(y1))
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.strokes.append([(event.position().x(), event.position().y())])
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self.strokes and event.buttons() & Qt.MouseButton.LeftButton:
            self.strokes[-1].append((event.position().x(), event.position().y()))
            self.update()

    def clear(self) -> None:
        self.strokes = []
        self.update()


def normalize_strokes(strokes: list[list[tuple[float, float]]], margin: float = 4.0):
    """Gezeichnete Striche auf ihren Umriss zuschneiden → (Striche 0..1, Seitenverhältnis Breite/Höhe)."""
    points = [p for s in strokes for p in s]
    if not points:
        return [], 1.0
    x0, y0 = min(p[0] for p in points) - margin, min(p[1] for p in points) - margin
    x1, y1 = max(p[0] for p in points) + margin, max(p[1] for p in points) + margin
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    out = [[((x - x0) / w, (y - y0) / h) for x, y in s] for s in strokes if len(s) >= 2]
    return out, w / h


def image_to_bytes(image: QImage, max_width: int = 1200) -> tuple[int, int, bytes, bytes | None]:
    """QImage → (Breite, Höhe, RGB-Bytes, Alpha-Bytes oder None) für den Kern; große Bilder verkleinert."""
    if image.width() > max_width:
        image = image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    stride = rgba.bytesPerLine()
    raw = bytes(rgba.constBits())[: stride * h]
    pixels = raw if stride == w * 4 else b"".join(raw[y * stride: y * stride + w * 4] for y in range(h))
    rgb = bytearray(w * h * 3)
    for channel in range(3):
        rgb[channel::3] = pixels[channel::4]
    alpha = pixels[3::4]
    return w, h, bytes(rgb), alpha if alpha.count(255) != len(alpha) else None


class SignatureDialog(QDialog):
    """Unterschrift zeichnen oder ein Bild wählen. Es wird nichts gespeichert – nur in dieses PDF gesetzt."""

    def __init__(self, parent: QWidget, start_dir: Path) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout
        self.setWindowTitle("Unterschrift")
        self.start_dir = start_dir
        self.image: QImage | None = None
        self.pad = _SignaturePad()
        clear = QPushButton("Leeren")
        clear.clicked.connect(self.pad.clear)
        load = QPushButton("Bild laden …")
        load.clicked.connect(self._load)
        note = QLabel("Mit der Maus oder dem Stift unterschreiben – oder ein Bild (PNG mit transparentem Hintergrund) "
                      "laden. Danach auf der Seite einen Bereich aufziehen oder klicken. Das ist eine sichtbare "
                      "Unterschrift, keine digitale Signatur; gespeichert wird sie nirgends.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(clear)
        row.addWidget(load)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Platzieren")
        buttons.accepted.connect(lambda: self.accept() if self.pad.strokes or self.image is not None else None)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.pad, 1)
        layout.addLayout(row)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _load(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Bild der Unterschrift", str(self.start_dir),
                                                "Bilder (*.png *.jpg *.jpeg *.bmp *.webp)")
        if chosen:
            image = QImage(chosen)
            if not image.isNull():
                self.image = image
                self.accept()

    def result_value(self):
        """("image", QImage) oder ("strokes", Striche 0..1, Seitenverhältnis)."""
        if self.image is not None:
            return ("image", self.image)
        strokes, aspect = normalize_strokes(self.pad.strokes)
        return ("strokes", strokes, aspect)
