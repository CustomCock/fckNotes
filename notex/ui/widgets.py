"""Kleine, wiederverwendbare Bausteine: Chips, Tab-Buttons, Suchfeld."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QTabBar, QToolButton, QWidget

from notex.theme.icons import icon, pixmap
from notex.theme.tokens import COLORS, DURATION, LAYOUT, RADIUS, SPACING
from notex.ui.anim import HoverFade


class FadeButton(QToolButton):
    """QToolButton, der seine Hover-Fläche selbst malt – mit weichem Übergang.

    Das QSS lässt den Hintergrund transparent; hier kommt die Fläche mit
    Deckkraft aus HoverFade darunter, dann zeichnet Qt Text/Icon wie gewohnt.
    """

    def __init__(self) -> None:
        super().__init__()
        self._hover = HoverFade(self, DURATION.hover)
        self._hover.changed.connect(self.update)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def enterEvent(self, event) -> None:
        self._hover.fade_to(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover.fade_to(False)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isCheckable() and self.isChecked():
            painter.setBrush(QColor(COLORS.selection))
            painter.drawRoundedRect(self.rect(), RADIUS.control, RADIUS.control)
        elif self._hover.value > 0:
            color = QColor(COLORS.hover)
            color.setAlphaF(self._hover.value)
            painter.setBrush(color)
            painter.drawRoundedRect(self.rect(), RADIUS.control, RADIUS.control)
        if self.isDown():
            color = QColor(COLORS.selection)
            painter.setBrush(color)
            painter.drawRoundedRect(self.rect(), RADIUS.control, RADIUS.control)
        painter.end()
        super().paintEvent(event)


class IconButton(FadeButton):
    """Flacher Icon-Button (z. B. Seitenleiste umschalten, Leiste schließen)."""

    def __init__(self, icon_name: str, tooltip: str, size: int = 16) -> None:
        super().__init__()
        self.setObjectName("IconButton")
        self.setIcon(icon(icon_name))
        self.setIconSize(QSize(size, size))
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)


class Chip(FadeButton):
    """Kompakter Umschalter (an/aus), Ersatz für eine Checkbox. Gleiche API: isChecked/setChecked/toggled."""

    def __init__(self, text: str, tooltip: str = "") -> None:
        super().__init__()
        self.setObjectName("Chip")
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if tooltip:
            self.setToolTip(tooltip)


class SearchField(QFrame):
    """Suchfeld: Lupe links, Eingabe, Löschen-Kreuz rechts (nur bei Text).

    Ein eigener Rahmen statt QLineEdit-Actions, weil Qt die Icons dort nicht sauber
    zentriert und versteckte Actions trotzdem Platz einnehmen. Der Fokus-Ring wird
    über die dynamische Property `focused` im QSS geschaltet.
    """

    textChanged = Signal(str)

    def __init__(self, placeholder: str) -> None:
        super().__init__()
        self.setObjectName("SearchField")
        self.setCursor(Qt.CursorShape.IBeamCursor)

        self.icon_label = QLabel()
        self.icon_label.setObjectName("SearchIcon")
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setPixmap(pixmap("search", 16, COLORS.text_muted, self.devicePixelRatioF()))

        self.input = QLineEdit()
        self.input.setObjectName("SearchInput")
        self.input.setPlaceholderText(placeholder)
        self.input.setFrame(False)
        self.input.installEventFilter(self)

        self.clear_button = QToolButton()
        self.clear_button.setObjectName("SearchClear")
        self.clear_button.setIcon(icon("x"))
        self.clear_button.setIconSize(QSize(14, 14))
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setToolTip("Leeren  Esc")
        self.clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_button.clicked.connect(self.clear)
        self.clear_button.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING.sm, 0, SPACING.xs, 0)
        layout.setSpacing(SPACING.sm)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.clear_button)
        self.setFixedHeight(32)

        self.input.textChanged.connect(self._on_text_changed)

    # QLineEdit-ähnliche API, damit der Rest des Codes das Feld wie ein Eingabefeld benutzt
    def text(self) -> str:
        return self.input.text()

    def setText(self, text: str) -> None:
        self.input.setText(text)

    def clear(self) -> None:
        self.input.clear()

    def selectAll(self) -> None:
        self.input.selectAll()

    def setFocus(self, *args) -> None:  # type: ignore[override]
        self.input.setFocus(*args)

    def _on_text_changed(self, text: str) -> None:
        self.clear_button.setVisible(bool(text))
        self.textChanged.emit(text)

    def retheme(self) -> None:
        self.icon_label.setPixmap(pixmap("search", 16, COLORS.text_muted, self.devicePixelRatioF()))
        self.clear_button.setIcon(icon("x"))

    def _set_focused(self, focused: bool) -> None:
        self.setProperty("focused", focused)
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.input:
            if event.type() == QEvent.Type.FocusIn:
                self._set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._set_focused(False)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        self.input.setFocus()


class TabButton(QWidget):
    """Rechts im Tab: nichts / Punkt (ungespeichert) / Kreuz (bei Hover). Klick schließt."""

    clicked = Signal()
    SIZE = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dirty = False
        self.tab_hovered = False
        self._self_hovered = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Schließen  Ctrl+W")

    def set_state(self, dirty: bool | None = None, tab_hovered: bool | None = None) -> None:
        if dirty is not None:
            self.dirty = dirty
        if tab_hovered is not None:
            self.tab_hovered = tab_hovered
        self.update()

    def enterEvent(self, event) -> None:
        self._self_hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._self_hovered = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        show_x = self.tab_hovered or self._self_hovered
        if show_x:
            if self._self_hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(COLORS.hover))
                painter.drawRoundedRect(self.rect(), 4, 4)
            size = 12
            pm = pixmap("x", size, COLORS.text if self._self_hovered else COLORS.text_muted, self.devicePixelRatioF())
            painter.drawPixmap((self.SIZE - size) // 2, (self.SIZE - size) // 2, pm)
        elif self.dirty:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS.text))
            radius = 3
            painter.drawEllipse(self.rect().center(), radius, radius)


class EditorTabBar(QTabBar):
    """Tab-Leiste mit eigenen Schließen-Buttons und Hover-Verfolgung pro Tab."""

    close_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self._hovered = -1
        self._hover_prev = -1
        self._hover = HoverFade(self, DURATION.hover)
        self._hover.changed.connect(self.update)

    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        button = TabButton(self)
        button.clicked.connect(lambda b=button: self._close_for(b))
        self.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)

    def _close_for(self, button: TabButton) -> None:
        for i in range(self.count()):
            if self.tabButton(i, QTabBar.ButtonPosition.RightSide) is button:
                self.close_requested.emit(i)
                return

    def set_dirty(self, index: int, dirty: bool) -> None:
        button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
        if isinstance(button, TabButton):
            button.set_state(dirty=dirty)

    def _set_hovered(self, index: int) -> None:
        if index == self._hovered:
            return
        for i in (self._hovered, index):
            button = self.tabButton(i, QTabBar.ButtonPosition.RightSide) if i >= 0 else None
            if isinstance(button, TabButton):
                button.set_state(tab_hovered=(i == index))
        self._hover_prev = self._hovered
        self._hovered = index
        self._hover.value = 0.0
        self._hover.fade_to(True)   # der neue Tab blendet ein, der alte gleichzeitig aus

    def paintEvent(self, event) -> None:
        # Hover-Flächen vor den Tabs malen; das QSS hält die Tab-Hintergründe transparent
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, alpha in ((self._hovered, self._hover.value), (self._hover_prev, 1.0 - self._hover.value)):
            if index < 0 or index == self.currentIndex() or alpha <= 0:
                continue
            color = QColor(COLORS.sidebar)
            color.setAlphaF(alpha)
            painter.setBrush(color)
            rect = self.tabRect(index).adjusted(SPACING.xs, SPACING.xs, -SPACING.xs, -SPACING.xs - 2)
            painter.drawRoundedRect(rect, RADIUS.control, RADIUS.control)
        painter.end()
        super().paintEvent(event)

    TAB_MIME = "application/x-notex-tab"
    DETACH_DISTANCE = 28   # so weit muss der Tab senkrecht aus der Leiste gezogen werden, bevor ein Drag beginnt

    def mousePressEvent(self, event) -> None:
        self._press_pos = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        self._set_hovered(self.tabAt(pos))
        press = getattr(self, "_press_pos", None)
        if press is not None and event.buttons() & Qt.MouseButton.LeftButton \
                and abs(pos.y() - press.y()) > self.DETACH_DISTANCE and self.tabAt(press) >= 0:
            self._start_tab_drag(self.tabAt(press))
            return
        super().mouseMoveEvent(event)

    def _start_tab_drag(self, index: int) -> None:
        """Tab senkrecht aus der Leiste gezogen: als Drag anbieten (Ziel: andere Editorgruppe)."""
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag, QMouseEvent
        from PySide6.QtWidgets import QApplication
        self._press_pos = None
        # QTabBar hält noch seinen internen „Tab wird verschoben“-Zustand – mit einem Release-Ereignis beenden
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(self.tabRect(index).center()),
                              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self, release)
        mime = QMimeData()
        mime.setData(self.TAB_MIME, f"{getattr(self, 'group_key', 0)}:{index}".encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab(self.tabRect(index)))
        drag.exec(Qt.DropAction.MoveAction)

    def leaveEvent(self, event) -> None:
        self._set_hovered(-1)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        # Mittelklick schließt den Tab, wie im Browser
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.close_requested.emit(index)
                return
        super().mouseReleaseEvent(event)

    def tabSizeHint(self, index: int) -> QSize:
        size = super().tabSizeHint(index)
        return QSize(size.width() + SPACING.xs, LAYOUT.tab_height)


class FlowLayout(QLayout):
    """Ordnet Elemente nebeneinander an und bricht bei Platzmangel in die nächste Zeile um (wie Text).

    Für Werkzeugleisten, die sonst die Mindestbreite des Fensters hochtreiben würden. Elemente sind hier ganze
    Gruppen (je ein Widget mit eigener Zeile) – so bleiben zusammengehörige Knöpfe beisammen.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing

    def addItem(self, item) -> None:                       # noqa: N802 – Qt-API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):                          # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):                          # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):                         # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:                   # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:           # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:            # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:                           # noqa: N802
        width = sum(i.sizeHint().width() for i in self._items if not i.isEmpty())
        width += self._spacing * max(0, len(self._items) - 1)
        height = max((i.sizeHint().height() for i in self._items), default=0)
        m = self.contentsMargins()
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def minimumSize(self) -> QSize:                        # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line > 0:
                x = area.x()
                y += line + self._spacing
                line = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()
