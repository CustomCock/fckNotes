"""Seitenleiste „Kommentare“ (Block V4): alle Kommentare als Threads, filtern und sortieren, ein Klick springt zur
Stelle; antworten, Status setzen, löschen; Zusammenfassung (Markdown, CSV, PDF) und XFDF.

Die Leiste zeigt nur an und meldet Wünsche per Signal – die Seite (`PdfPage`) ändert das PDF (ein Rückgängig-Schritt).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from notex.core import pdfcomments as C
from notex.theme.theme import style_menu
from notex.theme.tokens import SPACING
from notex.ui.widgets import IconButton

ROLE = Qt.ItemDataRole.UserRole
SORTS = [("page", "Seite"), ("date", "Datum"), ("author", "Autor"), ("kind", "Art"), ("status", "Status")]


class CommentsPanel(QWidget):
    jump = Signal(int, tuple)                 # Seite, Rechteck (Ansichts-Punkte)
    reply_requested = Signal(int, int)        # Seite, Position in /Annots
    status_requested = Signal(int, int, str)
    delete_requested = Signal(int, int)
    export_requested = Signal(str)            # md | csv | pdf | xfdf
    import_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PdfComments")
        self.setMinimumWidth(260)
        self.comments: list[C.Comment] = []
        self.current_page = 0
        self.search = QLineEdit()
        self.search.setPlaceholderText("Kommentare durchsuchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.kind = QComboBox()
        self.author = QComboBox()
        self.status = QComboBox()
        self.status.addItem("Alle Status", "")
        for key, label in C.STATE_LABELS.items():
            self.status.addItem(label.capitalize(), key)
        self.sort = QComboBox()
        for key, label in SORTS:
            self.sort.addItem(f"Nach {label}", key)
        self.descending = QCheckBox("absteigend")
        self.this_page = QCheckBox("nur diese Seite")
        for widget in (self.kind, self.author, self.status, self.sort):
            widget.currentIndexChanged.connect(lambda _i: self.refresh())
        for box in (self.descending, self.this_page):
            box.toggled.connect(lambda _on: self.refresh())
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(False)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemActivated.connect(self._clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.count = QLabel()
        self.count.setObjectName("SettingsNote")
        reply = IconButton("reply", "Antworten …  R")
        reply.clicked.connect(self._reply)
        status = IconButton("check-check", "Status setzen (offen, angenommen, abgelehnt, erledigt)")
        status.clicked.connect(lambda: self._status_menu(status))
        delete = IconButton("trash", "Kommentar samt Antworten löschen  Entf")
        delete.clicked.connect(self._delete)
        export = IconButton("download", "Zusammenfassung (Markdown, CSV, PDF) oder XFDF exportieren")
        export.clicked.connect(lambda: self._export_menu(export))
        load = IconButton("upload", "Kommentare aus XFDF importieren …")
        load.clicked.connect(self.import_requested)
        filters = QHBoxLayout()
        for widget in (self.kind, self.author):
            filters.addWidget(widget, 1)
        sorting = QHBoxLayout()
        sorting.addWidget(self.status, 1)
        sorting.addWidget(self.sort, 1)
        checks = QHBoxLayout()
        checks.addWidget(self.descending)
        checks.addWidget(self.this_page)
        checks.addStretch(1)
        buttons = QHBoxLayout()
        for button in (reply, status, delete):
            buttons.addWidget(button)
        buttons.addStretch(1)
        for button in (export, load):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.sm)
        layout.addWidget(self.search)
        layout.addLayout(filters)
        layout.addLayout(sorting)
        layout.addLayout(checks)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.count)
        for key, slot in ((Qt.Key.Key_Delete, self._delete), (Qt.Key.Key_R, self._reply)):
            shortcut = QShortcut(QKeySequence(key), self.tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)

    # ---- Daten ---------------------------------------------------------------------------------------------
    def set_comments(self, comments: list[C.Comment]) -> None:
        self.comments = comments
        for combo, values, everything in (
                (self.kind, sorted({(c.kind, c.label) for c in comments}, key=lambda kv: kv[1]), "Alle Arten"),
                (self.author, sorted({(c.author, c.author) for c in comments if c.author}), "Alle Autoren")):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(everything, "")
            for key, label in values:
                combo.addItem(label, key)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)
        self.refresh()

    def visible_comments(self) -> list[C.Comment]:
        return C.filter_comments(self.comments, kind=self.kind.currentData() or "",
                                 page=self.current_page if self.this_page.isChecked() else None,
                                 author=self.author.currentData() or "", status=self.status.currentData() or "",
                                 text=self.search.text(), sort=self.sort.currentData() or "page",
                                 descending=self.descending.isChecked())

    def refresh(self) -> None:
        selected = self.selected()
        key = (selected.page, selected.index) if selected else None
        self.tree.clear()
        shown = self.visible_comments()
        for comment in shown:
            text = comment.contents.strip() or (f"„{comment.quoted}“" if comment.quoted else "")
            head = " · ".join(p for p in (comment.label, f"S. {comment.page + 1}", comment.author,
                                          comment.status_label) if p)
            item = QTreeWidgetItem([head + (f"\n{_short(text)}" if text else "")])
            item.setData(0, ROLE, comment)
            tip = [head, C.format_date(comment.date)]
            if comment.quoted:
                tip.append(f"„{comment.quoted}“")
            if comment.contents:
                tip.append(comment.contents)
            item.setToolTip(0, "\n".join(t for t in tip if t))
            for reply in comment.replies:
                who = " · ".join(p for p in (reply.author or "Antwort", C.format_date(reply.date)) if p)
                child = QTreeWidgetItem([f"{who}\n{_short(reply.contents)}"])
                child.setData(0, ROLE, comment)
                child.setToolTip(0, f"{who}\n{reply.contents}")
                item.addChild(child)
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)
            if key == (comment.page, comment.index):
                self.tree.setCurrentItem(item)
        total = len(self.comments)
        self.count.setText(f"{len(shown)} von {total} Kommentaren" if len(shown) != total
                           else f"{total} Kommentare")

    def selected(self) -> C.Comment | None:
        item = self.tree.currentItem()
        return item.data(0, ROLE) if item is not None else None

    def select(self, page: int, index: int) -> bool:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            comment = item.data(0, ROLE)
            if (comment.page, comment.index) == (page, index):
                self.tree.setCurrentItem(item)
                return True
        return False

    # ---- Aktionen ------------------------------------------------------------------------------------------
    def _clicked(self, item, _column=0) -> None:
        comment = item.data(0, ROLE)
        if comment is not None:
            self.jump.emit(comment.page, tuple(comment.rect))

    def _reply(self) -> None:
        comment = self.selected()
        if comment is not None:
            self.reply_requested.emit(comment.page, comment.index)

    def _delete(self) -> None:
        comment = self.selected()
        if comment is not None:
            self.delete_requested.emit(comment.page, comment.index)

    def _status_menu(self, anchor) -> None:
        comment = self.selected()
        if comment is None:
            return
        menu = style_menu(QMenu(self))
        for key, label in C.STATE_LABELS.items():
            action = menu.addAction(label.capitalize(),
                                    lambda k=key: self.status_requested.emit(comment.page, comment.index, k))
            action.setCheckable(True)
            action.setChecked(comment.status == key)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _export_menu(self, anchor) -> None:
        menu = style_menu(QMenu(self))
        for key, label in (("md", "Zusammenfassung als Markdown …"), ("csv", "Zusammenfassung als CSV …"),
                           ("pdf", "Zusammenfassung als PDF …"), ("xfdf", "Kommentare als XFDF …")):
            menu.addAction(label, lambda k=key: self.export_requested.emit(k))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        comment = item.data(0, ROLE)
        menu = style_menu(QMenu(self))
        menu.addAction("Zur Stelle springen", lambda: self._clicked(item))
        menu.addAction("Antworten …", self._reply)
        status = menu.addMenu("Status")
        for key, label in C.STATE_LABELS.items():
            action = status.addAction(label.capitalize(),
                                      lambda k=key: self.status_requested.emit(comment.page, comment.index, k))
            action.setCheckable(True)
            action.setChecked(comment.status == key)
        menu.addSeparator()
        menu.addAction("Löschen (samt Antworten)", self._delete)
        menu.exec(self.tree.viewport().mapToGlobal(pos))


def _short(text: str, limit: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
