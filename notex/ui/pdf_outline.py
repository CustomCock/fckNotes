"""Lesezeichen bearbeiten (Block V1): Baum zum Anlegen, Umbenennen, Löschen, Verschieben und Verschachteln, dazu der
Vorschau-Dialog „Lesezeichen aus Überschriften“.

Jede Änderung schreibt den ganzen Baum über `notex.core.pdfoutline.write_outline` – ein Rückgängig-Schritt.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from notex.core.pdfoutline import Bookmark, Heading, headings_to_outline
from notex.ui.widgets import IconButton

ROLE = Qt.ItemDataRole.UserRole


class OutlineTree(QTreeWidget):
    """Baum mit Ziehen und Ablegen; meldet nach jedem Verschieben `moved`."""
    moved = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed |
                             QAbstractItemView.EditTrigger.SelectedClicked)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.moved.emit()


class OutlineEditor(QWidget):
    """Bearbeitbare Lesezeichen. Die Seite (`PdfPage`) liefert und speichert den Baum."""
    changed = Signal(list)             # neuer Baum (list[Bookmark]) – ein Rückgängig-Schritt
    jump = Signal(int, object)         # Seite, y in PDF-Koordinaten (oder None)
    add_requested = Signal()
    headings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PdfOutlineEditor")
        self.tree = OutlineTree()
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemChanged.connect(self._renamed)
        self.tree.moved.connect(self._emit)
        add = IconButton("bookmark-plus", "Lesezeichen hier hinzufügen (aktuelle Seite und Position)  Ctrl+Shift+B")
        add.clicked.connect(self.add_requested)
        indent = IconButton("chevron-right", "Einrücken (unter das vorige Lesezeichen)")
        indent.clicked.connect(self.indent)
        outdent = IconButton("chevron-left", "Ausrücken")
        outdent.clicked.connect(self.outdent)
        delete = IconButton("trash", "Lesezeichen löschen  Entf")
        delete.clicked.connect(self.delete_selected)
        headings = IconButton("heading", "Lesezeichen aus Überschriften erzeugen …")
        headings.clicked.connect(self.headings_requested)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (add, indent, outdent, delete, headings):
            row.addWidget(widget)
        row.addStretch(1)
        hint = QLabel("Ziehen = verschieben/verschachteln · F2 = umbenennen")
        hint.setObjectName("SettingsNote")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.tree, 1)
        layout.addWidget(hint)
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.tree)
        shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        shortcut.activated.connect(self.delete_selected)
        self._loading = False

    # ---- Baum ↔ Bookmarks --------------------------------------------------------------------------------------
    def set_items(self, items: list[Bookmark]) -> None:
        self._loading = True
        self.tree.clear()

        def add(parent, mark: Bookmark) -> None:
            item = QTreeWidgetItem([mark.title])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled |
                          Qt.ItemFlag.ItemIsDropEnabled)
            stored = copy.copy(mark)
            stored.children = []
            item.setData(0, ROLE, stored)
            item.setToolTip(0, "Webadresse/Aktion (bleibt erhalten)" if mark.page is None else f"Seite {mark.page + 1}")
            (parent.addChild(item) if parent is not None else self.tree.addTopLevelItem(item))
            for child in mark.children:
                add(item, child)
            item.setExpanded(mark.open or bool(mark.children))
        for mark in items:
            add(None, mark)
        self._loading = False

    def items(self) -> list[Bookmark]:
        def read(item: QTreeWidgetItem) -> Bookmark:
            mark = copy.copy(item.data(0, ROLE))
            mark.title = item.text(0).strip() or mark.title
            mark.open = item.isExpanded() and item.childCount() > 0
            mark.children = [read(item.child(i)) for i in range(item.childCount())]
            return mark
        return [read(self.tree.topLevelItem(i)) for i in range(self.tree.topLevelItemCount())]

    def count(self) -> int:
        total = 0
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            total += 1
            iterator += 1
        return total

    # ---- Bearbeiten --------------------------------------------------------------------------------------------
    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit(self.items())

    def _renamed(self, item, _column) -> None:
        if self._loading:
            return
        if not item.text(0).strip():
            self._loading = True
            item.setText(0, item.data(0, ROLE).title)     # leerer Titel: alten behalten
            self._loading = False
            return
        self._emit()

    def _clicked(self, item, _column) -> None:
        mark = item.data(0, ROLE)
        if mark is not None and mark.page is not None:
            self.jump.emit(mark.page, mark.top)

    def delete_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        (parent.removeChild(item) if parent is not None else
         self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item)))
        self._emit()

    def indent(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        siblings_owner = parent if parent is not None else self.tree.invisibleRootItem()
        index = siblings_owner.indexOfChild(item)
        if index <= 0:
            return
        new_parent = siblings_owner.child(index - 1)
        siblings_owner.takeChild(index)
        new_parent.addChild(item)
        new_parent.setExpanded(True)
        self.tree.setCurrentItem(item)
        self._emit()

    def outdent(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            return
        parent = item.parent()
        grand = parent.parent() or self.tree.invisibleRootItem()
        parent.takeChild(parent.indexOfChild(item))
        grand.insertChild(grand.indexOfChild(parent) + 1, item)
        self.tree.setCurrentItem(item)
        self._emit()


class HeadingsDialog(QDialog):
    """Vorschau der gefundenen Überschriften: einzeln abwählbar, dann ersetzen oder anhängen."""

    def __init__(self, parent: QWidget | None, headings: list[Heading], has_outline: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lesezeichen aus Überschriften")
        self.headings = headings
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Überschrift", "Seite", "Größe"])
        for heading in headings:
            item = QTreeWidgetItem(["    " * (heading.level - 1) + heading.text, str(heading.page + 1),
                                    f"{heading.size:g} pt"])
            item.setCheckState(0, Qt.CheckState.Checked)
            self.list.addTopLevelItem(item)
        self.list.resizeColumnToContents(0)
        self.mode = QComboBox()
        self.mode.addItem("Vorhandene Lesezeichen ersetzen", "replace")
        self.mode.addItem("An die vorhandenen anhängen", "append")
        self.mode.setEnabled(has_outline)
        self.all_box = QCheckBox("Alle")
        self.all_box.setChecked(True)
        self.all_box.toggled.connect(self._toggle_all)
        info = QLabel(f"{len(headings)} Überschriften gefunden (größere Schrift als der Fließtext). "
                      "Einrückung = Ebene.")
        info.setObjectName("SettingsNote")
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Übernehmen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.all_box)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.mode)
        layout.addWidget(buttons)
        self.resize(560, 460)

    def _toggle_all(self, on: bool) -> None:
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for i in range(self.list.topLevelItemCount()):
            self.list.topLevelItem(i).setCheckState(0, state)

    def chosen(self) -> list[Bookmark]:
        picked = [h for i, h in enumerate(self.headings)
                  if self.list.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]
        return headings_to_outline(picked)
