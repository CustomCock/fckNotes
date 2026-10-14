"""Dialoge für Block V2: Link-Ziel wählen und die Anhänge-Liste.

Die Dialoge sammeln nur Eingaben; gearbeitet wird in `notex.core.pdflinks` / `notex.core.pdfattach`. Öffnen und
Speichern von Anhängen erledigt die Seite (`PdfPage.open_attachment` / `save_attachment`) – nach Bestätigung.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from notex import APP_NAME
from notex.core import pdflinks
from notex.core.pdfattach import Attachment
from notex.core.pdflinks import LinkTarget
from notex.core.pdfoutline import Bookmark
from notex.core.pdfpages import PdfEditError
from notex.ui.pdf_pages_ui import _buttons, _error_label
from notex.ui.viewer_page import human_size

KINDS = [("page", "Seite in diesem PDF"), ("bookmark", "Lesezeichen"), ("url", "Webadresse"),
         ("file", "Anderes PDF")]


def _flat(items: list[Bookmark], depth: int = 0) -> list[tuple[str, Bookmark]]:
    out = []
    for mark in items:
        if mark.page is not None:
            out.append(("    " * depth + mark.title, mark))
        out.extend(_flat(mark.children, depth + 1))
    return out


class LinkDialog(QDialog):
    """Ziel eines Links: Seite (mit Position), Lesezeichen, Webadresse oder anderes PDF; Rahmen sichtbar/unsichtbar."""

    def __init__(self, parent: QWidget | None, count: int, bookmarks: list[Bookmark] | None = None,
                 target: LinkTarget | None = None, border: bool = False, current_page: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("Link" if target is None else "Link bearbeiten")
        self.count = count
        self.kind = QComboBox()
        marks = _flat(bookmarks or [])
        for key, label in KINDS:
            if key != "bookmark" or marks:
                self.kind.addItem(label, key)
        self.stack = QStackedWidget()
        # Seite
        page_box = QWidget()
        page_form = QFormLayout(page_box)
        page_form.setContentsMargins(0, 0, 0, 0)
        self.page = QSpinBox()
        self.page.setRange(1, max(1, count))
        self.page.setValue(min(count, current_page + 1))
        self.keep_top = QCheckBox("An die gespeicherte Höhe springen")
        self.top = QDoubleSpinBox()
        self.top.setRange(-100000, 100000)
        self.top.setDecimals(0)
        self.top.setSuffix(" pt")
        self.top.setEnabled(False)
        self.keep_top.toggled.connect(self.top.setEnabled)
        page_form.addRow("Seite", self.page)
        page_form.addRow(self.keep_top, self.top)
        # Lesezeichen
        self.bookmark = QComboBox()
        for label, mark in marks:
            self.bookmark.addItem(f"{label}  (S. {mark.page + 1})", mark)
        # Webadresse
        url_box = QWidget()
        url_layout = QVBoxLayout(url_box)
        url_layout.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://… oder name@example.org")
        url_note = QLabel(f"Nur http, https und mailto. {APP_NAME} selbst öffnet keine Webadressen.")
        url_note.setObjectName("SettingsNote")
        url_note.setWordWrap(True)
        url_layout.addWidget(self.url)
        url_layout.addWidget(url_note)
        # anderes PDF
        file_box = QWidget()
        file_form = QFormLayout(file_box)
        file_form.setContentsMargins(0, 0, 0, 0)
        self.file = QLineEdit()
        self.file.setPlaceholderText("anhang/kapitel2.pdf (relativ zu diesem PDF)")
        self.file_page = QSpinBox()
        self.file_page.setRange(1, 100000)
        file_form.addRow("Datei", self.file)
        file_form.addRow("Seite dort", self.file_page)
        self._pages = {"page": page_box, "bookmark": self.bookmark, "url": url_box, "file": file_box}
        for widget in self._pages.values():
            self.stack.addWidget(widget)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.border = QCheckBox("Rahmen sichtbar")
        self.border.setChecked(border)
        self.error = _error_label()
        form = QFormLayout()
        form.addRow("Ziel", self.kind)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.stack)
        layout.addWidget(self.border)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Übernehmen", self._accept))
        self.resize(460, 0)
        if target is not None:
            self._load(target)
        self._kind_changed()

    def _load(self, target: LinkTarget) -> None:
        if target.kind == "page" and target.page is not None:
            self.page.setValue(target.page + 1)
            if target.top is not None:
                self.keep_top.setChecked(True)
                self.top.setValue(target.top)
        elif target.kind == "url":
            self.url.setText(target.url)
        elif target.kind == "file":
            self.file.setText(target.file)
            self.file_page.setValue(target.file_page + 1)
        index = self.kind.findData(target.kind)
        if index >= 0:
            self.kind.setCurrentIndex(index)

    def _kind_changed(self) -> None:
        self.stack.setCurrentWidget(self._pages[self.kind.currentData()])
        self.error.clear()

    def target(self) -> LinkTarget:
        kind = self.kind.currentData()
        if kind == "page":
            return LinkTarget("page", self.page.value() - 1, self.top.value() if self.keep_top.isChecked() else None)
        if kind == "bookmark":
            mark: Bookmark = self.bookmark.currentData()
            return LinkTarget("page", mark.page, mark.top)
        if kind == "url":
            return LinkTarget("url", url=pdflinks.clean_url(self.url.text()))
        return LinkTarget("file", file=pdflinks.clean_file(self.file.text()), file_page=self.file_page.value() - 1)

    def values(self) -> tuple[LinkTarget, bool]:
        return self.target(), self.border.isChecked()

    def _accept(self) -> None:
        try:
            self.values()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class AttachmentsDialog(QDialog):
    """Alle Anhänge (Dokument + Datei-Anmerkungen): speichern, öffnen (nach Rückfrage), hinzufügen, entfernen."""
    open_requested = Signal(str)
    save_requested = Signal(str)
    add_requested = Signal()
    remove_requested = Signal(str)

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Anhänge")
        self.items: list[Attachment] = []
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Größe", "Ort", "Beschreibung"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda _i: self._emit(self.save_requested))
        self.save_button = QPushButton("Speichern unter …")
        self.save_button.clicked.connect(lambda: self._emit(self.save_requested))
        self.open_button = QPushButton("Öffnen …")
        self.open_button.clicked.connect(lambda: self._emit(self.open_requested))
        self.add_button = QPushButton("Hinzufügen …")
        self.add_button.clicked.connect(self.add_requested)
        self.remove_button = QPushButton("Entfernen")
        self.remove_button.setObjectName("Danger")
        self.remove_button.clicked.connect(lambda: self._emit(self.remove_requested))
        self.empty = QLabel("Dieses PDF hat keine Anhänge.")
        self.empty.setObjectName("SettingsNote")
        note = QLabel("Anhänge können Schadsoftware enthalten. Programme, Skripte und Makro-Dokumente lassen sich nur "
                      "speichern, nie öffnen. Doppelklick = speichern.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        row = QHBoxLayout()
        for button in (self.save_button, self.open_button, self.add_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty)
        layout.addLayout(row)
        layout.addWidget(note)
        self.resize(620, 340)
        self.can_open = True
        self.open_hint = ""

    def set_items(self, items: list[Attachment], editable: bool = True) -> None:
        selected = self.current()
        self.items = items
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            name = QTableWidgetItem(("⚠ " if item.risky else "") + item.name)
            if item.risky:
                name.setToolTip("Programm, Skript oder Makro-Dokument – nur speichern")
            size = QTableWidgetItem(human_size(item.size) if item.size else "?")
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for column, cell in enumerate((name, size, QTableWidgetItem(item.where),
                                           QTableWidgetItem(item.description))):
                self.table.setItem(row, column, cell)
        self.empty.setVisible(not items)
        self.table.setVisible(bool(items))
        self.add_button.setEnabled(editable)
        self._editable = editable
        keys = [i.key for i in items]
        if selected is not None and selected.key in keys:
            self.table.selectRow(keys.index(selected.key))
        elif items:
            self.table.selectRow(0)
        self._update_buttons()

    def current(self) -> Attachment | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.items):
            return None
        return self.items[rows[0].row()]

    def _update_buttons(self) -> None:
        item = self.current()
        self.save_button.setEnabled(item is not None)
        self.open_button.setEnabled(item is not None and not item.risky and self.can_open)
        if item is not None and item.risky:
            self.open_button.setToolTip(f"Programme, Skripte und Makro-Dokumente öffnet {APP_NAME} nie – nur speichern")
        else:
            self.open_button.setToolTip("" if self.can_open else self.open_hint)
        self.remove_button.setEnabled(item is not None and getattr(self, "_editable", True))

    def _emit(self, signal) -> None:
        item = self.current()
        if item is not None:
            signal.emit(item.key)
