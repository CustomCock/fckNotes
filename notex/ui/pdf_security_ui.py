"""Dialoge für Block V6: Passwortschutz und Schwärzen nach Muster.

Passwörter stehen nur in den Eingabefeldern und in der zurückgegebenen `Protection` – nie im Log, nie gespeichert.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from notex import APP_NAME
from notex.core import pdfsecurity as S
from notex.core import redact_patterns as R
from notex.core.pdfpages import PdfEditError
from notex.ui.pdf_pages_ui import _buttons, _error_label


def _password_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setMaxLength(127)                               # AES-256 nutzt höchstens 127 Byte UTF-8
    return field


class ProtectionDialog(QDialog):
    """Öffnen-Passwort und Rechte-Passwort mit Rechten setzen, ändern oder den Schutz entfernen."""

    def __init__(self, parent: QWidget | None, current: S.Protection | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Passwortschutz")
        self.removed = False
        permissions = current.permissions if current else S.Permissions()
        self.open_on = QCheckBox("Öffnen-Passwort (ohne Passwort lässt sich das PDF nicht öffnen)")
        self.open_on.setChecked(bool(current and current.user_password))
        self.open_pw, self.open_pw2 = _password_field(), _password_field()
        self.rights_on = QCheckBox("Rechte einschränken (mit Rechte-Passwort)")
        self.rights_on.setChecked(bool(current and permissions != S.Permissions()))
        self.rights_pw, self.rights_pw2 = _password_field(), _password_field()
        self.boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for i, (key, label) in enumerate(S.LABELS.items()):
            box = QCheckBox(label)
            box.setChecked(getattr(permissions, key))
            self.boxes[key] = box
            grid.addWidget(box, i // 2, i % 2)
        self.open_pw.setPlaceholderText("Öffnen-Passwort eingeben")
        self.open_pw2.setPlaceholderText("noch einmal")
        self.rights_pw2.setPlaceholderText("noch einmal")
        if current:
            self.open_pw.setPlaceholderText("leer lassen = bisheriges behalten")
            self.rights_pw.setPlaceholderText("leer lassen = bisheriges behalten" if current.owner_password
                                              else "leer lassen = zufällig")
        else:
            self.rights_pw.setPlaceholderText("leer lassen = zufällig (Rechte lassen sich dann nicht mehr ändern)")
        open_form = QFormLayout()
        open_form.addRow("Passwort", self.open_pw)
        open_form.addRow("Wiederholen", self.open_pw2)
        rights_form = QFormLayout()
        rights_form.addRow("Passwort", self.rights_pw)
        rights_form.addRow("Wiederholen", self.rights_pw2)
        note = QLabel(f"Verschlüsselt wird mit AES-256. Rechte sind nur eine Bitte an PDF-Programme und kein echter "
                      f"Schutz – wer das PDF öffnen kann, kann den Inhalt lesen. {APP_NAME} merkt sich keine "
                      "Passwörter; der Schutz wird beim Speichern angewendet.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        self.error = _error_label()
        remove = QPushButton("Schutz entfernen")
        remove.setObjectName("Danger")
        remove.setEnabled(current is not None)
        remove.clicked.connect(self._remove)
        layout = QVBoxLayout(self)
        layout.addWidget(self.open_on)
        layout.addLayout(open_form)
        layout.addWidget(self.rights_on)
        layout.addLayout(rights_form)
        layout.addLayout(grid)
        layout.addWidget(note)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addWidget(remove)
        row.addStretch(1)
        row.addWidget(_buttons(self, "Übernehmen", self._accept))
        layout.addLayout(row)
        self.current = current
        for toggle, widgets in ((self.open_on, (self.open_pw, self.open_pw2)),
                                (self.rights_on, (self.rights_pw, self.rights_pw2, *self.boxes.values()))):
            toggle.toggled.connect(lambda on, ws=widgets: [w.setEnabled(on) for w in ws])
            for widget in widgets:
                widget.setEnabled(toggle.isChecked())
        self.resize(520, 0)

    def _remove(self) -> None:
        self.removed = True
        self.accept()

    def protection(self) -> S.Protection | None:
        """Neuer Schutz (None = keiner). Wirft PdfEditError bei unstimmigen Eingaben."""
        if self.removed:
            return None
        current = self.current
        user = ""
        if self.open_on.isChecked():
            if self.open_pw.text() != self.open_pw2.text():
                raise PdfEditError("Die beiden Öffnen-Passwörter stimmen nicht überein")
            user = self.open_pw.text() or (current.user_password if current and current.user_password else "")
            if not user:
                raise PdfEditError("Bitte ein Öffnen-Passwort eingeben")
        rights = S.Permissions()
        owner = None
        if self.rights_on.isChecked():
            if self.rights_pw.text() != self.rights_pw2.text():
                raise PdfEditError("Die beiden Rechte-Passwörter stimmen nicht überein")
            rights = S.Permissions(**{key: box.isChecked() for key, box in self.boxes.items()})
            owner = self.rights_pw.text() or (current.owner_password if current else None)
        if not user and rights == S.Permissions():
            return None                                   # nichts zu schützen = Schutz entfernen
        if owner and owner == user:
            raise PdfEditError("Öffnen- und Rechte-Passwort müssen sich unterscheiden")
        return S.Protection(user, owner, rights, "AES-256", "owner")

    def _accept(self) -> None:
        try:
            self.protection()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.accept()


class PatternRedactDialog(QDialog):
    """Welche Muster gesucht werden: eingebaute (mit Prüfziffern) und eigene reguläre Ausdrücke."""

    def __init__(self, parent: QWidget | None, custom: list[dict] | None = None,
                 selected: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nach Mustern schwärzen")
        selected = selected if selected is not None else ["email", "phone_de", "phone_intl", "iban", "card"]
        self.boxes: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for i, (key, pattern) in enumerate(R.PATTERNS.items()):
            box = QCheckBox(pattern.label)
            box.setToolTip(f"z. B. {pattern.example}" if pattern.example else "")
            box.setChecked(key in selected)
            self.boxes[key] = box
            grid.addWidget(box, i // 2, i % 2)
        self.custom = QTreeWidget()
        self.custom.setHeaderLabels(["Name", "Regulärer Ausdruck"])
        self.custom.setRootIsDecorated(False)
        self.custom.setFixedHeight(110)
        for entry in custom or []:
            self._add_custom(entry.get("name", ""), entry.get("regex", ""), bool(entry.get("on", True)))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name, z. B. Aktenzeichen")
        self.regex = QLineEdit()
        self.regex.setPlaceholderText(r"z. B. AZ-\d{4}/\d+")
        add = QPushButton("Hinzufügen")
        add.clicked.connect(self._add_clicked)
        remove = QPushButton("Entfernen")
        remove.clicked.connect(self._remove_clicked)
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(self.regex, 2)
        row.addWidget(add)
        row.addWidget(remove)
        note = QLabel("Prüfziffern (IBAN, Kreditkarte, Steuer-ID) und Kalender (Datum) verwerfen Fehltreffer. Eigene "
                      "Ausdrücke werden gespeichert; zu langsame brechen nach einem Zeitlimit ab.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        self.error = _error_label()
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(QLabel("Eigene Muster"))
        layout.addWidget(self.custom)
        layout.addLayout(row)
        layout.addWidget(note)
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Suchen", self._accept))
        self.resize(560, 0)

    def _add_custom(self, name: str, regex: str, on: bool = True) -> None:
        item = QTreeWidgetItem([name, regex])
        item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.custom.addTopLevelItem(item)

    def _add_clicked(self) -> None:
        name, regex = self.name.text().strip(), self.regex.text().strip()
        if not name:
            self.error.setText("Bitte einen Namen für das Muster angeben")
            return
        try:
            R.compile_custom(regex)
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self._add_custom(name, regex)
        self.name.clear()
        self.regex.clear()
        self.error.clear()

    def _remove_clicked(self) -> None:
        item = self.custom.currentItem()
        if item is not None:
            self.custom.takeTopLevelItem(self.custom.indexOfTopLevelItem(item))

    def keys(self) -> list[str]:
        return [key for key, box in self.boxes.items() if box.isChecked()]

    def custom_entries(self) -> list[dict]:
        out = []
        for i in range(self.custom.topLevelItemCount()):
            item = self.custom.topLevelItem(i)
            out.append({"name": item.text(0), "regex": item.text(1),
                        "on": item.checkState(0) == Qt.CheckState.Checked})
        return out

    def active_custom(self) -> dict[str, str]:
        return {e["name"]: e["regex"] for e in self.custom_entries() if e["on"]}

    def _accept(self) -> None:
        if not self.keys() and not self.active_custom():
            self.error.setText("Bitte mindestens ein Muster wählen")
            return
        self.accept()


class PatternHitsDialog(QDialog):
    """Vorschau: alle Treffer mit Seite und Text, einzeln abwählbar."""

    def __init__(self, parent: QWidget | None, hits: list[R.Hit]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{len(hits)} Treffer zum Schwärzen")
        self.hits = hits
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Text", "Art", "Seite"])
        self.tree.setRootIsDecorated(False)
        for hit in hits:
            item = QTreeWidgetItem([hit.text, hit.label, str(hit.page + 1)])
            item.setCheckState(0, Qt.CheckState.Checked)
            self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)
        self.all_box = QCheckBox("Alle")
        self.all_box.setChecked(True)
        self.all_box.toggled.connect(self._toggle_all)
        self.apply_now = QCheckBox("Danach gleich schwärzen (sonst nur vormerken)")
        note = QLabel("Vorgemerkte Stellen erscheinen auf der Seite als dunkle Balken. Erst „Schwärzen anwenden“ "
                      "entfernt den Text endgültig – danach prüft die erweiterte Prüfung, ob er noch irgendwo steht.")
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.all_box)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.apply_now)
        layout.addWidget(note)
        layout.addWidget(_buttons(self, "Vormerken", self.accept))
        self.resize(620, 460)

    def _toggle_all(self, on: bool) -> None:
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def chosen(self) -> list[R.Hit]:
        return [hit for i, hit in enumerate(self.hits)
                if self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]
