"""Dialoge für Block V5: neues Feld (Optionsgruppe, Dropdown, Listenfeld, Schaltfläche) und Tab-Reihenfolge.

Die Dialoge sammeln nur Eingaben; angelegt wird in `notex.core.pdffieldtypes`.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from notex.core.pdfpages import PdfEditError
from notex.ui.pdf_pages_ui import _buttons, _error_label

TITLES = {"radio": "Optionsfeld-Gruppe", "combo": "Dropdown", "listbox": "Listenfeld", "button": "Schaltfläche"}


class NewFieldDialog(QDialog):
    """Name und Einträge eines neuen Felds; bei Optionsfeldern die Anordnung, bei Schaltflächen Aktion."""

    def __init__(self, parent: QWidget | None, kind: str, name: str, page_count: int = 1,
                 current_page: int = 0) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(f"Neu: {TITLES[kind]}")
        self.name = QLineEdit(name)
        self.options = QPlainTextEdit("Ja\nNein" if kind == "radio" else "")
        self.options.setPlaceholderText("Ein Eintrag pro Zeile")
        self.options.setFixedHeight(110)
        self.vertical = QComboBox()
        self.vertical.addItem("Untereinander", True)
        self.vertical.addItem("Nebeneinander", False)
        self.editable = QCheckBox("Eigene Eingabe erlauben")
        self.label = QLineEdit("Zurücksetzen")
        self.action = QComboBox()
        self.action.addItem("Formular zurücksetzen", "reset")
        self.action.addItem("Seite anspringen", "goto")
        self.target = QSpinBox()
        self.target.setRange(1, max(1, page_count))
        self.target.setValue(min(page_count, current_page + 1))
        self.action.currentIndexChanged.connect(self._action_changed)
        self.error = _error_label()
        form = QFormLayout()
        form.addRow("Name", self.name)
        if kind == "button":
            form.addRow("Beschriftung", self.label)
            form.addRow("Aktion", self.action)
            form.addRow("Seite", self.target)
            note = QLabel("Schaltflächen können nur das Formular zurücksetzen oder eine Seite anspringen – "
                          "Skripte gibt es nicht.")
        else:
            form.addRow("Einträge" if kind != "radio" else "Optionen", self.options)
            if kind == "radio":
                form.addRow("Anordnung", self.vertical)
                note = QLabel("Je Option entsteht ein Kreis im aufgezogenen Bereich; genau eine ist wählbar.")
            else:
                if kind == "combo":
                    form.addRow("", self.editable)
                note = QLabel("Die Einträge lassen sich später unter „Feld-Eigenschaften“ ändern.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Anlegen", self._accept))
        self._action_changed()
        self.resize(420, 0)

    def _action_changed(self) -> None:
        self.target.setEnabled(self.action.currentData() == "goto")
        if self.action.currentData() == "goto" and self.label.text() == "Zurücksetzen":
            self.label.setText("Weiter")
        elif self.action.currentData() == "reset" and self.label.text() == "Weiter":
            self.label.setText("Zurücksetzen")

    def values(self) -> dict:
        name = self.name.text().strip()
        if not name or "." in name:
            raise PdfEditError("Feldname darf nicht leer sein und keinen Punkt enthalten")
        out: dict = {"name": name}
        if self.kind == "button":
            if not self.label.text().strip():
                raise PdfEditError("Bitte eine Beschriftung eingeben")
            out.update(label=self.label.text().strip(), action=self.action.currentData(),
                       target_page=self.target.value() - 1)
            return out
        options = [line.strip() for line in self.options.toPlainText().splitlines() if line.strip()]
        if not options:
            raise PdfEditError("Bitte mindestens einen Eintrag angeben (einer pro Zeile)")
        out["options"] = options
        if self.kind == "radio":
            out["vertical"] = bool(self.vertical.currentData())
        if self.kind == "combo":
            out["editable"] = self.editable.isChecked()
        return out

    def _accept(self) -> None:
        try:
            self.values()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class TabOrderDialog(QDialog):
    """Tab-Reihenfolge einer Seite: Felder per Ziehen sortieren oder „nach Zeilen“/„nach Spalten“."""

    def __init__(self, parent: QWidget | None, page: int, names: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Tab-Reihenfolge – Seite {page + 1}")
        self.mode = "custom"
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.addItems(names)
        self.list.model().rowsMoved.connect(lambda *_a: setattr(self, "mode", "custom"))
        up = QPushButton("Nach oben")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Nach unten")
        down.clicked.connect(lambda: self._move(1))
        rows = QPushButton("Nach Zeilen")
        rows.clicked.connect(lambda: self._auto("rows"))
        columns = QPushButton("Nach Spalten")
        columns.clicked.connect(lambda: self._auto("columns"))
        side = QVBoxLayout()
        for button in (up, down, rows, columns):
            side.addWidget(button)
        side.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(side)
        note = QLabel("Ziehen oder die Knöpfe ändern die Reihenfolge, in der die Tab-Taste durch die Felder springt. "
                      "„Nach Zeilen/Spalten“ ordnet nach der Lage auf der Seite.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(note)
        layout.addWidget(_buttons(self, "Übernehmen", self.accept))
        self.resize(420, 380)

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self.mode = "custom"

    def _auto(self, mode: str) -> None:
        self.mode = mode
        self.accept()

    def order(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]
