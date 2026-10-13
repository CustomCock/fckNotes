"""Dialog für Block V8: Wasserzeichen, Kopf-/Fußzeile (Seitenzahlen) und Hintergrund – mit Vorschau und Vorlagen.

Der Dialog sammelt nur Eingaben und zeigt eine Vorschau (eine Seite, über `pdfstamp_layers.preview` und PDFium).
Angewendet wird von der Seite (`PdfPage.page_layer`) im Hintergrund, als ein Rückgängig-Schritt.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from notex.core import pdfstamp_layers as L
from notex.core.pdfpages import PdfEditError
from notex.ui.pdf_draw_ui import ColorButton
from notex.ui.pdf_images_ui import IMAGE_FILTER
from notex.ui.pdf_pages_ui import _buttons, _error_label

TITLES = {"watermark": "Wasserzeichen", "header": "Kopf-/Fußzeile und Seitenzahlen", "background": "Hintergrund"}
PREVIEW_WIDTH = 260


class PageLayerDialog(QDialog):
    """Eine Ebenen-Art bearbeiten. `preview(spec, image_bytes, page) -> QImage | None` liefert die Vorschau;
    `templates` = gespeicherte Vorlagen dieser Art [{"name", "spec", "image"}]."""

    def __init__(self, parent: QWidget | None, kind: str, page_count: int, current_page: int = 0,
                 spec: L.Spec | None = None, preview=None, templates: list[dict] | None = None,
                 image_folder: Path | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(TITLES[kind])
        self.page_count = max(1, page_count)
        self.preview_fn = preview
        self.templates = list(templates or [])
        self.image_folder = image_folder
        self.image: bytes | None = None
        self.image_name = ""
        self.saved_templates: list[dict] | None = None   # geänderte Vorlagenliste (None = unverändert)
        spec = spec or L.SPECS[kind]()
        self.fields: dict[str, QWidget] = {}
        form = QFormLayout()
        if kind == "watermark":
            self._watermark_fields(form, spec)
        elif kind == "header":
            self._header_fields(form, spec)
        else:
            self._background_fields(form, spec)
        self.pages = QLineEdit(spec.pages)
        self.pages.setPlaceholderText("alle Seiten – oder z. B. 1-3, 5, 8-")
        form.addRow("Seiten", self.pages)
        self.preview_page = QSpinBox()
        self.preview_page.setRange(1, self.page_count)
        self.preview_page.setValue(min(self.page_count, current_page + 1))
        self.preview_page.setPrefix("Vorschau Seite ")
        self.preview_label = QLabel()
        self.preview_label.setFixedWidth(PREVIEW_WIDTH)
        self.preview_label.setMinimumHeight(round(PREVIEW_WIDTH * 1.414))
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setObjectName("PdfLayerPreview")
        self.template_box = QComboBox()
        self.template_box.addItem("Vorlage …", None)
        for entry in self.templates:
            self.template_box.addItem(str(entry.get("name", "?")), entry)
        self.template_box.activated.connect(self._template_chosen)
        save = QPushButton("Als Vorlage speichern …")
        save.clicked.connect(lambda: self.save_template())
        remove = QPushButton("Vorlage löschen")
        remove.clicked.connect(self._delete_template)
        templates_row = QHBoxLayout()
        templates_row.addWidget(self.template_box, 1)
        templates_row.addWidget(save)
        templates_row.addWidget(remove)
        note = QLabel(self._note())
        note.setObjectName("SettingsNote")
        note.setWordWrap(True)
        self.error = _error_label()
        left = QVBoxLayout()
        left.addLayout(form)
        left.addLayout(templates_row)
        left.addWidget(note)
        left.addWidget(self.error)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(self.preview_label)
        right.addWidget(self.preview_page)
        right.addStretch(1)
        row = QHBoxLayout()
        row.addLayout(left, 1)
        row.addLayout(right)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(_buttons(self, "Anwenden", self._accept))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.update_preview)
        for widget in (*self.fields.values(), self.pages, self.preview_page):
            self._watch(widget)
        self.resize(780, 0)
        self.schedule_preview()

    # ---- Felder ----------------------------------------------------------------------------------------------
    def _spin(self, value: float, low: float, high: float, suffix: str = "", decimals: int = 1) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(decimals)
        box.setValue(value)
        if suffix:
            box.setSuffix(suffix)
        return box

    def _image_row(self, form: QFormLayout, use: bool) -> None:
        self.fields["image"] = QCheckBox("Bild statt Text" if self.kind == "watermark" else "Bild statt Farbe")
        self.fields["image"].setChecked(use)
        choose = QPushButton("Bild wählen …")
        choose.clicked.connect(lambda: self.choose_image())
        self.image_label = QLabel("kein Bild gewählt")
        self.image_label.setObjectName("SettingsNote")
        row = QHBoxLayout()
        row.addWidget(self.fields["image"])
        row.addWidget(choose)
        row.addWidget(self.image_label, 1)
        form.addRow("Bild", row)

    def _watermark_fields(self, form: QFormLayout, spec: L.Watermark) -> None:
        f = self.fields
        f["text"] = QPlainTextEdit(spec.text)
        f["text"].setFixedHeight(56)
        f["font_size"] = self._spin(spec.font_size, 4, 400, " pt", 0)
        f["bold"] = QCheckBox("fett")
        f["bold"].setChecked(spec.bold)
        f["color"] = ColorButton(spec.color, "Farbe des Wasserzeichens")
        size_row = QHBoxLayout()
        for widget in (f["font_size"], f["bold"], f["color"]):
            size_row.addWidget(widget)
        size_row.addStretch(1)
        f["opacity"] = QSpinBox()
        f["opacity"].setRange(5, 100)
        f["opacity"].setSuffix(" %")
        f["opacity"].setValue(round(spec.opacity * 100))
        f["rotation"] = self._spin(spec.rotation if spec.rotation <= 180 else spec.rotation - 360, -180, 180, " °")
        f["position"] = QComboBox()
        for key in L.POSITIONS:
            f["position"].addItem(L.POSITION_LABELS[key], key)
        f["position"].setCurrentIndex(L.POSITIONS.index(spec.position))
        f["scale"] = QSpinBox()
        f["scale"].setRange(2, 100)
        f["scale"].setSuffix(" % der Seitenbreite")
        f["scale"].setValue(round(spec.scale * 100))
        f["behind"] = QCheckBox("Hinter dem Inhalt (sonst darüber)")
        f["behind"].setChecked(spec.behind)
        form.addRow("Text", f["text"])
        form.addRow("Schrift", size_row)
        self._image_row(form, spec.image)
        form.addRow("Bildgröße", f["scale"])
        form.addRow("Deckkraft", f["opacity"])
        form.addRow("Drehung", f["rotation"])
        form.addRow("Position", f["position"])
        form.addRow("", f["behind"])

    def _header_fields(self, form: QFormLayout, spec: L.HeaderFooter) -> None:
        f = self.fields
        grid = QGridLayout()
        for column, label in enumerate(("links", "Mitte", "rechts")):
            grid.addWidget(QLabel(label), 0, column + 1, alignment=Qt.AlignmentFlag.AlignCenter)
        for row, part in enumerate(("header", "footer")):
            grid.addWidget(QLabel("Kopf" if part == "header" else "Fuß"), row + 1, 0)
            for column, side in enumerate(("left", "center", "right")):
                key = f"{part}_{side}"
                field = QLineEdit(getattr(spec, key))
                field.setPlaceholderText("leer")
                f[key] = field
                grid.addWidget(field, row + 1, column + 1)
        form.addRow(grid)
        f["preset"] = QComboBox()
        f["preset"].addItem("Seitenzahl unten Mitte …", "")
        for preset in L.NUMBER_PRESETS:
            f["preset"].addItem(preset, preset)
        f["preset"].activated.connect(self._preset)
        holders = QLabel("Platzhalter: " + ", ".join(L.PLACEHOLDERS))
        holders.setObjectName("SettingsNote")
        holders.setWordWrap(True)
        holders.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Seitenzahl", f["preset"])
        form.addRow("", holders)
        f["font_size"] = self._spin(spec.font_size, 4, 72, " pt", 1)
        f["bold"] = QCheckBox("fett")
        f["bold"].setChecked(spec.bold)
        f["color"] = ColorButton(spec.color, "Schriftfarbe")
        size_row = QHBoxLayout()
        for widget in (f["font_size"], f["bold"], f["color"]):
            size_row.addWidget(widget)
        size_row.addStretch(1)
        form.addRow("Schrift", size_row)
        f["margin_mm"] = self._spin(spec.margin_mm, 0, 100, " mm")
        f["side_mm"] = self._spin(spec.side_mm, 0, 100, " mm")
        margins = QHBoxLayout()
        margins.addWidget(QLabel("oben/unten"))
        margins.addWidget(f["margin_mm"])
        margins.addWidget(QLabel("seitlich"))
        margins.addWidget(f["side_mm"])
        margins.addStretch(1)
        form.addRow("Abstand", margins)
        f["start"] = QSpinBox()
        f["start"].setRange(0, 99999)
        f["start"].setValue(spec.start)
        f["start"].setToolTip("Nummer der ersten Seite im Bereich – z. B. 1 für eine Zählung ab der zweiten Seite")
        form.addRow("Erste Nummer", f["start"])

    def _background_fields(self, form: QFormLayout, spec: L.Background) -> None:
        f = self.fields
        f["color"] = ColorButton(spec.color, "Hintergrundfarbe")
        form.addRow("Farbe", f["color"])
        self._image_row(form, spec.image)
        f["fit"] = QComboBox()
        f["fit"].addItem("Ganze Seite füllen (strecken)", "stretch")
        f["fit"].addItem("Einpassen (Seitenverhältnis bleibt)", "fit")
        f["fit"].setCurrentIndex(0 if spec.fit == "stretch" else 1)
        form.addRow("Bild", f["fit"])
        f["opacity"] = QSpinBox()
        f["opacity"].setRange(5, 100)
        f["opacity"].setSuffix(" %")
        f["opacity"].setValue(round(spec.opacity * 100))
        form.addRow("Deckkraft", f["opacity"])

    def _note(self) -> str:
        return {"watermark": "Das Wasserzeichen kommt in eine eigene Ebene: es lässt sich später entfernen oder "
                             "aktualisieren, ohne den Seiteninhalt anzufassen. Vorhandene Wasserzeichen dieses "
                             "Programms werden ersetzt.",
                "header": "{seite} zählt ab „Erste Nummer“ über die Seiten im Bereich, {seiten} ist die letzte "
                          "Nummer. Nach dem Einfügen oder Löschen von Seiten: Ebenen › Aktualisieren.",
                "background": "Der Hintergrund liegt hinter dem Inhalt. Seiten mit eigenem weißen Hintergrund (z. B. "
                              "Scans) verdecken ihn."}[self.kind]

    def _watch(self, widget: QWidget) -> None:
        for signal in ("textChanged", "valueChanged", "toggled", "currentIndexChanged", "clicked"):
            if hasattr(widget, signal):
                try:
                    getattr(widget, signal).connect(lambda *_a: self.schedule_preview())
                except (RuntimeError, TypeError):
                    continue

    def _preset(self, _index: int) -> None:
        preset = self.fields["preset"].currentData()
        if preset:
            self.fields["footer_center"].setText(preset)

    # ---- Werte ----------------------------------------------------------------------------------------------
    def spec(self) -> L.Spec:
        f = self.fields
        if self.kind == "watermark":
            spec = L.Watermark(text=f["text"].toPlainText(), font_size=f["font_size"].value(),
                               bold=f["bold"].isChecked(), color=f["color"].color,
                               opacity=f["opacity"].value() / 100, rotation=f["rotation"].value() % 360,
                               position=f["position"].currentData(), scale=f["scale"].value() / 100,
                               behind=f["behind"].isChecked(), image=f["image"].isChecked(),
                               pages=self.pages.text().strip())
        elif self.kind == "header":
            spec = L.HeaderFooter(**{key: f[key].text() for key in L.HeaderFooter().slots},
                                  font_size=f["font_size"].value(), bold=f["bold"].isChecked(),
                                  color=f["color"].color, margin_mm=f["margin_mm"].value(),
                                  side_mm=f["side_mm"].value(), start=f["start"].value(),
                                  pages=self.pages.text().strip())
        else:
            spec = L.Background(color=f["color"].color, opacity=f["opacity"].value() / 100,
                                image=f["image"].isChecked(), fit=f["fit"].currentData(),
                                pages=self.pages.text().strip())
        return L.checked(spec)

    def set_spec(self, spec: L.Spec) -> None:
        f = self.fields
        for key, value in L.to_dict(spec).items():
            widget = f.get(key)
            if widget is None:
                continue
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(str(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, ColorButton):
                widget.set_color(str(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QSpinBox) and key in ("opacity", "scale"):
                widget.setValue(round(float(value) * 100))
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                number = float(value)
                if key == "rotation" and number > 180:
                    number -= 360
                widget.setValue(int(number) if isinstance(widget, QSpinBox) else number)
        self.pages.setText(spec.pages)
        self.schedule_preview()

    def set_image(self, content: bytes, name: str) -> None:
        self.image, self.image_name = content, name
        if hasattr(self, "image_label"):
            self.image_label.setText(name)
        if "image" in self.fields:
            self.fields["image"].setChecked(True)
        self.schedule_preview()

    def choose_image(self, path: str | None = None) -> bool:
        if path is None:
            path, _f = QFileDialog.getOpenFileName(self, "Bild wählen", "", IMAGE_FILTER)
            if not path:
                return False
        try:
            content = Path(path).read_bytes()
        except OSError as error:
            self.error.setText(f"Nicht lesbar: {error}")
            return False
        self.set_image(content, Path(path).name)
        return True

    # ---- Vorlagen --------------------------------------------------------------------------------------------
    def _template_chosen(self, _index: int) -> None:
        entry = self.template_box.currentData()
        if not entry:
            return
        try:
            self.set_spec(L.from_dict(dict(entry.get("spec", {}))))
        except PdfEditError as error:
            self.error.setText(f"Vorlage nicht lesbar: {error}")
            return
        image = str(entry.get("image", "") or "")
        if image and self.image_folder is not None:
            path = self.image_folder / Path(image).name
            if path.is_file():
                self.set_image(path.read_bytes(), path.name)
            else:
                self.error.setText(f"Bild der Vorlage fehlt: {path.name}")

    def save_template(self, name: str | None = None) -> bool:
        try:
            spec = self.spec()
        except PdfEditError as error:
            self.error.setText(str(error))
            return False
        if name is None:
            name, ok = QInputDialog.getText(self, "Vorlage speichern", "Name der Vorlage:")
            if not ok:
                return False
        name = name.strip()[:60]
        if not name:
            self.error.setText("Bitte einen Namen für die Vorlage angeben")
            return False
        image_name = ""
        if getattr(spec, "image", False) and self.image and self.image_folder is not None:
            import hashlib
            suffix = Path(self.image_name).suffix.lower() or ".png"
            image_name = f"ebene-{hashlib.sha1(self.image).hexdigest()[:12]}{suffix}"
            try:
                self.image_folder.mkdir(parents=True, exist_ok=True)
                (self.image_folder / image_name).write_bytes(self.image)
            except OSError as error:
                self.error.setText(f"Bild nicht gespeichert: {error}")
                return False
        entry = {"name": name, "spec": L.to_dict(spec), "image": image_name}
        self.templates = [t for t in self.templates if t.get("name") != name] + [entry]
        self.saved_templates = list(self.templates)
        index = self.template_box.findText(name)
        if index >= 0:
            self.template_box.removeItem(index)
        self.template_box.addItem(name, entry)
        self.error.clear()
        return True

    def _delete_template(self) -> None:
        entry = self.template_box.currentData()
        if not entry:
            return
        self.templates = [t for t in self.templates if t.get("name") != entry.get("name")]
        self.saved_templates = list(self.templates)
        self.template_box.removeItem(self.template_box.currentIndex())

    # ---- Vorschau und Übernehmen -----------------------------------------------------------------------------
    def schedule_preview(self) -> None:
        self._timer.start()

    def update_preview(self) -> None:
        if self.preview_fn is None:
            return
        try:
            spec = self.spec()
            if getattr(spec, "image", False) and not self.image:
                raise PdfEditError("Bitte ein Bild wählen")
            image: QImage | None = self.preview_fn(spec, self.image, self.preview_page.value() - 1)
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        self.error.clear()
        if image is not None and not image.isNull():
            self.preview_label.setPixmap(QPixmap.fromImage(image).scaledToWidth(
                PREVIEW_WIDTH, Qt.TransformationMode.SmoothTransformation))

    def _accept(self) -> None:
        try:
            spec = self.spec()
        except PdfEditError as error:
            self.error.setText(str(error))
            return
        if getattr(spec, "image", False) and not self.image:
            self.error.setText("Bitte ein Bild wählen")
            return
        self.accept()
