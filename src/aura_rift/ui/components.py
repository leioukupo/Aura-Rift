"""Reusable controls for the Aura-Rift launcher shell.

The real 绘世 launcher uses a deliberately quiet, card based interface.  The
widgets in this module keep that visual language in one place so pages can be
rebuilt without duplicating spacing, typography, or interaction code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aura_rift.ui.icons import make_nav_icon


def card(parent: QWidget | None = None, object_name: str = "card") -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName(object_name)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    return frame


def heading(text: str, size: int = 18, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("sectionTitle")
    label.setProperty("headingSize", size)
    font = label.font()
    font.setPixelSize(size)
    font.setBold(True)
    label.setFont(font)
    return label


def muted(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


class ScrollPage(QScrollArea):
    """A page with target-compatible margins and a stable scroll bar."""

    def __init__(self, parent: QWidget | None = None, margins: tuple[int, int, int, int] = (28, 26, 28, 30)) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setObjectName("pageScroll")
        self.inner = QWidget()
        self.inner.setObjectName("scrollContent")
        self.layout = QVBoxLayout(self.inner)
        self.layout.setContentsMargins(*margins)
        self.layout.setSpacing(14)
        self.setWidget(self.inner)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self.layout.addWidget(widget, stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self.layout.addStretch(stretch)


class SettingCard(QFrame):
    """Horizontal setting card with title, explanation, and a control."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingCard")
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 15, 20, 15)
        row.setSpacing(18)
        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        text_box.addWidget(title_label)
        if description:
            text_box.addWidget(muted(description))
        row.addLayout(text_box, 1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignVCenter)


class ExpandCard(QFrame):
    """Collapsible card used by maintenance and expert settings pages."""

    toggled = Signal(bool)

    def __init__(self, title: str, description: str = "", parent: QWidget | None = None, expanded: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("expandCard")
        self._expanded = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.button = QToolButton()
        self.button.setObjectName("expandButton")
        self.button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.button.setText(title if not description else f"{title}  ·  {description}")
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.clicked.connect(self.set_expanded)
        root.addWidget(self.button)
        self.body = QWidget()
        self.body.setObjectName("expandBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 4, 20, 20)
        self.body_layout.setSpacing(10)
        root.addWidget(self.body)
        self.set_expanded(expanded)

    def set_expanded(self, value: bool) -> None:
        self._expanded = bool(value)
        self.button.setChecked(self._expanded)
        self.button.setArrowType(Qt.DownArrow if self._expanded else Qt.RightArrow)
        self.body.setVisible(self._expanded)
        self.toggled.emit(self._expanded)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)


class WarningBar(QFrame):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("warningBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 12, 18, 12)
        label = QLabel(text)
        label.setWordWrap(True)
        row.addWidget(label, 1)


class LinkCard(QFrame):
    clicked = Signal()

    def __init__(
        self,
        title: str,
        description: str,
        url: str = "",
        callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        icon_name: str = "link",
        icon_color: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.setObjectName("linkCard")
        self.setCursor(Qt.PointingHandCursor)
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(14)
        icon = QLabel()
        icon.setObjectName("linkIcon")
        icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(34, 34)
        # Let the active Qt palette choose a readable glyph colour.  The
        # original target is dark-first, but Aura-Rift also exposes the bulb
        # light theme; a hard-coded near-white icon becomes invisible there.
        if not icon_color:
            icon_color = self.palette().color(QPalette.Text).name()
        icon.setPixmap(make_nav_icon(icon_name, icon_color, 30).pixmap(30, 30))
        root.addWidget(icon, 0, Qt.AlignVCenter)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("linkTitle")
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_box.addWidget(title_label)
        if description:
            description_label = muted(description)
            description_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            text_box.addWidget(description_label)
        if url:
            url_label = muted(url)
            url_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            text_box.addWidget(url_label)
        root.addLayout(text_box, 1)
        arrow = QLabel("›")
        arrow.setObjectName("linkArrow")
        arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setFixedWidth(18)
        root.addWidget(arrow, 0, Qt.AlignVCenter)
        self._callback = callback

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            if self._callback:
                self._callback()
        super().mouseReleaseEvent(event)


class Toggle(QCheckBox):
    """QCheckBox styled as the compact launcher switch."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("switch")


class Banner(QFrame):
    def __init__(self, image_path: str | Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setScaledContents(True)
        # A pixmap's natural size must not become the page's minimum width;
        # the launcher is expected to remain usable at 1280×800 and below.
        self.image.setMinimumSize(0, 0)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image.setObjectName("bannerImage")
        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackAll)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.image)
        if image_path:
            self.set_image(image_path)

    def add_overlay(self, widget: QWidget) -> None:
        """Place a transparent overlay widget above the image."""
        widget.setAttribute(Qt.WA_TranslucentBackground, True)
        self._stack.addWidget(widget)
        widget.raise_()

    def set_image(self, image_path: str | Path) -> None:
        pixmap = QPixmap(str(image_path))
        if not pixmap.isNull():
            self.image.setPixmap(pixmap)
            self.setMinimumHeight(max(170, int(pixmap.height() * 0.43)))

    def minimumSizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize

        return QSize(0, max(170, self.minimumHeight()))


class TitleBar(QFrame):
    """Frameless-window title bar with desktop-neutral Qt actions."""

    help_requested = Signal()
    theme_requested = Signal()
    close_requested = Signal()
    minimize_requested = Signal()
    maximize_requested = Signal()

    def __init__(self, title: str, version: str, icon: QPixmap | None = None, parent: QWidget | None = None, include_theme: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(78)
        self._drag_pos: QPoint | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(26, 0, 18, 0)
        root.setSpacing(12)
        if icon is not None and not icon.isNull():
            icon_label = QLabel()
            # Let the title bar receive drag/double-click events even when
            # the pointer is over the avatar itself.
            icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            icon_label.setPixmap(icon.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            root.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setObjectName("windowTitle")
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(title_label)
        version_label = QLabel(version)
        version_label.setObjectName("windowVersion")
        version_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(version_label)
        root.addStretch(1)
        actions = [("?", "help_requested", "titleHelp")]
        if include_theme:
            actions.append(("☼", "theme_requested", "titleTheme"))
        actions.extend((("—", "minimize_requested", "titleMin"), ("□", "maximize_requested", "titleMax"), ("×", "close_requested", "titleClose")))
        for text, signal_name, obj in actions:
            button = QToolButton()
            button.setObjectName(obj)
            button.setText(text)
            button.setFixedSize(42, 42)
            signal = getattr(self, signal_name)
            button.clicked.connect(lambda _checked=False, signal=signal: signal.emit())
            root.addWidget(button)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            # Wayland and some compositors require the platform-mediated move
            # gesture; use it when available and retain the manual fallback
            # for X11/older Qt backends.
            handle = self.window().windowHandle() if self.window() else None
            start_move = getattr(handle, "startSystemMove", None) if handle else None
            if callable(start_move):
                try:
                    if start_move():
                        self._drag_pos = None
                        event.accept()
                        return
                except (RuntimeError, TypeError):
                    pass
            self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton and self.window():
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.window().move(self.window().pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.maximize_requested.emit()
        super().mouseDoubleClickEvent(event)


__all__ = [
    "Banner", "ExpandCard", "LinkCard", "ScrollPage", "SettingCard",
    "TitleBar", "Toggle", "WarningBar", "card", "heading", "muted",
]
