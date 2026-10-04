"""Theme tokens and Qt stylesheets for the Aura-Rift shell.

The reference launcher (绘世 2.9.2) uses a restrained, near-black surface
palette rather than a blue/teal dashboard theme.  Keeping the colours in a
small token object makes it possible to tune the visual language without
duplicating every selector in the light and dark stylesheets.

``stylesheet(theme)`` is intentionally kept as the public entry point used by
older Aura-Rift code.  The module also exposes ``get_tokens``/``palette`` for
widgets which need a colour outside a stylesheet (for example a painted icon).
No Qt imports are needed here, so the tokens can be inspected in headless
tests and by packaging tools.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from string import Template
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Named colours shared by the QSS and custom-painted controls.

    Values are CSS/Qt colour literals.  Keeping names semantic (``surface``
    instead of ``gray_700``) lets the light theme stay structurally identical
    while changing contrast appropriately.
    """

    name: str
    window: str
    title_bar: str
    sidebar: str
    header: str
    surface: str
    surface_alt: str
    surface_raised: str
    surface_hover: str
    input: str
    console: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_text: str
    danger: str
    danger_hover: str
    danger_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    selection: str
    scrollbar: str
    scrollbar_hover: str
    console_text: str
    font: str = (
        '"Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", '
        '"WenQuanYi Micro Hei", "Microsoft YaHei", "Arial Unicode MS", '
        '"Segoe UI", sans-serif'
    )
    mono_font: str = (
        '"Cascadia Mono", "Cascadia Code", "JetBrains Mono", Consolas, '
        '"Noto Sans Mono CJK SC", monospace'
    )

    def as_dict(self) -> dict[str, str]:
        """Return a plain mapping suitable for templating or JSON output."""

        return {key: value for key, value in asdict(self).items()}

    def __getitem__(self, key: str) -> str:
        """Allow mapping-style access for older theme integrations."""

        return _token_map(self)[key]

    def get(self, key: str, default: str | None = None) -> str | None:
        """Mapping-compatible ``get`` helper."""

        return _token_map(self).get(key, default)


# The values below are sampled from the public 绘世 2.9.x screenshots: #262626
# page surfaces, #202020 chrome, #2e2e2e cards, and a coral-pink active rail.
# They intentionally avoid pure black/white, which causes harsh edges on LCDs.
DARK: Final[ThemeTokens] = ThemeTokens(
    name="dark",
    window="#262626",
    title_bar="#202020",
    sidebar="#202020",
    header="#343434",
    surface="#2e2e2e",
    surface_alt="#292929",
    surface_raised="#333333",
    surface_hover="#454545",
    input="#333333",
    console="#0c0c0c",
    border="#3b3b3b",
    border_strong="#505050",
    text="#f2f2f2",
    text_secondary="#c8c8c8",
    text_muted="#999999",
    accent="#ff8198",
    accent_hover="#ff9bad",
    accent_pressed="#e96d84",
    accent_soft="#593740",
    primary="#a3a3a3",
    primary_hover="#b5b5b5",
    primary_pressed="#8c8c8c",
    primary_text="#171717",
    danger="#df6178",
    danger_hover="#ec7188",
    danger_soft="#512d36",
    success="#8ed3aa",
    success_soft="#294b3a",
    warning="#e7b96e",
    warning_soft="#442726",
    selection="#454545",
    scrollbar="#5b5b5b",
    scrollbar_hover="#737373",
    console_text="#d6e2d0",
)

LIGHT: Final[ThemeTokens] = ThemeTokens(
    name="light",
    window="#f4f4f4",
    title_bar="#ffffff",
    sidebar="#ffffff",
    header="#eeeeee",
    surface="#ffffff",
    surface_alt="#f7f7f7",
    surface_raised="#ffffff",
    surface_hover="#ededed",
    input="#ffffff",
    console="#171717",
    border="#dddddd",
    border_strong="#c9c9c9",
    text="#222222",
    text_secondary="#555555",
    text_muted="#7c7c7c",
    accent="#d94f6b",
    accent_hover="#e76680",
    accent_pressed="#bd3e59",
    accent_soft="#f7dce2",
    primary="#5b5b5b",
    primary_hover="#484848",
    primary_pressed="#707070",
    primary_text="#ffffff",
    danger="#cf4d65",
    danger_hover="#dc5c73",
    danger_soft="#f8dde2",
    success="#317e55",
    success_soft="#dcefe4",
    warning="#9a6a1b",
    warning_soft="#f8edcf",
    selection="#f4dce2",
    scrollbar="#bcbcbc",
    scrollbar_hover="#999999",
    console_text="#d6e2d0",
)


THEMES: Final[Mapping[str, ThemeTokens]] = {"dark": DARK, "light": LIGHT}
TOKENS: Final[Mapping[str, ThemeTokens]] = THEMES
DARK_THEME: Final[ThemeTokens] = DARK
LIGHT_THEME: Final[ThemeTokens] = LIGHT

# Mapping aliases retained for integrations which used token dictionaries
# before ``ThemeTokens`` was introduced.  The short aliases are intentionally
# additive: callers can use either ``text_muted`` or the familiar ``muted``
# spelling without changing the canonical dataclass.
def _token_map(value: ThemeTokens) -> dict[str, str]:
    result = value.as_dict()
    result.update(
        background=value.window,
        bg=value.window,
        bg_primary=value.window,
        bg_secondary=value.surface_alt,
        window_bg=value.window,
        sidebar_bg=value.sidebar,
        foreground=value.text,
        fg=value.text,
        fg_primary=value.text,
        fg_secondary=value.text_secondary,
        fg_muted=value.text_muted,
        toolbar=value.header,
        selected=value.selection,
        muted=value.text_muted,
        card=value.surface,
        card_bg=value.surface,
        panel=value.surface_alt,
        accent_primary=value.accent,
    )
    return result


DARK_TOKENS: Final[dict[str, str]] = _token_map(DARK)
LIGHT_TOKENS: Final[dict[str, str]] = _token_map(LIGHT)
THEME_TOKENS: Final[Mapping[str, ThemeTokens]] = THEMES


def normalize_theme(theme: str | None) -> str:
    """Normalize user/config values to ``"dark"`` or ``"light"``.

    Unknown values deliberately fall back to dark, matching the historical
    ``stylesheet`` behaviour and avoiding an unreadable unstyled window when a
    stale config contains a future theme name.
    """

    value = str(theme or "dark").strip().lower()
    if value in {"light", "day", "亮", "浅色", "白"}:
        return "light"
    return "dark"


def get_tokens(theme: str | None = "dark") -> ThemeTokens:
    """Return immutable tokens for *theme*."""

    return THEMES[normalize_theme(theme)]


def palette(theme: str | None = "dark") -> dict[str, str]:
    """Return a copy of the selected token mapping."""

    return _token_map(get_tokens(theme))


# Keep placeholders in a Template instead of an f-string: QSS uses braces for
# every selector, while token substitution only needs to replace ``$name``.
_QSS = Template(
    r"""/* Aura-Rift / 绘世 2.9.2 visual language */
* {
    font-family: $font;
    /* The reference capture is rendered at a 1x 2107px desktop scale.  A
       16px base keeps Chinese labels readable there while scrollable pages
       remain usable at the 1280x800 minimum. */
    font-size: 16px;
    outline: 0;
}
QMainWindow, QDialog, QWidget {
    background: $window;
    color: $text;
}
QMainWindow {
    border: 0;
}
QWidget#windowFrame {
    background: $window;
    border: 2px solid #3a3a3a;
}
QToolTip {
    background: $surface_raised;
    color: $text;
    border: 1px solid $border_strong;
    padding: 6px 9px;
    border-radius: 5px;
}
QFrame#titleBar, #titleBar {
    background: $title_bar;
    border: 0;
    border-bottom: 1px solid $border;
}
QFrame#sideBar, #sideBar {
    background: $sidebar;
    border: 0;
    border-right: 1px solid $border;
}
QFrame#settingsNav, #settingsNav {
    background: $title_bar;
    border: 0;
    border-right: 1px solid $border;
}
QFrame#pageHeader, #pageHeader {
    background: $header;
    border: 0;
    border-bottom: 1px solid $border;
}
QLabel { background: transparent; }
QLabel#windowTitle { color: $text; font-size: 25px; font-weight: 600; }
QLabel#windowVersion, QLabel#footnote, QLabel#muted, QLabel#mutedTitle {
    color: $text_muted;
    font-size: 14px;
}
QLabel#sectionTitle { color: $text; font-size: 21px; font-weight: 600; }

/* Navigation rail */
QToolButton#navButton, QPushButton#navButton {
    background: transparent;
    border: 0;
    border-radius: 5px;
    color: $text_secondary;
    padding: 10px 6px;
    font-size: 18px;
    text-align: center;
}
QToolButton#navButton:hover, QPushButton#navButton:hover {
    background: $surface_hover;
    color: $text;
}
QToolButton#navButton:checked, QPushButton#navButton:checked {
    background: $surface_hover;
    color: $text;
    border: 0;
    border-left: 3px solid $accent;
}
QToolButton#navButtonAccent, QPushButton#navButtonAccent {
    background: transparent;
    border: 0;
    border-radius: 5px;
    color: $accent;
    padding: 10px 6px;
    font-size: 18px;
}
QToolButton#navButtonAccent:hover, QPushButton#navButtonAccent:hover {
    background: $surface_hover;
}

/* Cards and the banner */
QFrame#card, QGroupBox, QFrame#pathCard, QFrame#settingCard,
QFrame#expandCard, QFrame#linkCard, QFrame#softCard {
    background: $surface;
    border: 1px solid $border;
    border-radius: 6px;
}
QFrame#card:hover, QFrame#pathCard:hover, QFrame#linkCard:hover {
    border-color: $border_strong;
}
QFrame#softCard { background: $surface_alt; }
QFrame#banner, QFrame#hero {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 8px;
}
QLabel#bannerImage { border-radius: 7px; }
QLabel#bannerOverlay {
    color: $text;
    background: transparent;
    font-weight: 600;
}
QLabel#bannerKicker, QLabel#bannerTitle, QLabel#bannerSubtitle {
    background: transparent;
    color: #ffffff;
}
QLabel#bannerKicker { font-size: 17px; }
QLabel#bannerTitle { font-size: 30px; font-weight: 700; }
QLabel#bannerSubtitle { font-size: 18px; }
QPushButton#folderCard {
    text-align: left;
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 13px 16px;
}
/* ``setMinimumHeight`` is otherwise superseded by the generic button rule
   when the stylesheet is polished.  The scoped property preserves the
   generous folder-card rhythm of the reference launcher. */
QPushButton[folderCardLarge="true"] { min-height: 74px; }
QPushButton#folderCard:hover { background: $surface_hover; border-color: $border_strong; }
QPushButton[launchButtonLarge="true"] {
    min-height: 82px;
    font-size: 22px;
}
QLabel#cardTitle, QLabel#linkTitle { color: $text; font-weight: 600; }
QToolButton#expandButton {
    background: transparent;
    border: 0;
    color: $text;
    text-align: left;
    padding: 14px 18px;
}
QToolButton#expandButton:hover { background: $surface_hover; }
QFrame#warningBar {
    background: $warning_soft;
    color: $text;
    border: 1px solid $warning;
    border-radius: 5px;
}

/* Inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox,
QSpinBox, QDoubleSpinBox {
    background: $input;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 5px;
    padding: 7px 9px;
    selection-background-color: $selection;
    selection-color: $text;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QTextBrowser:hover,
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: $border_strong; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: $text_muted; background: $surface_alt; border-color: $border;
}
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox::down-arrow { width: 8px; height: 8px; }
QComboBox QAbstractItemView {
    background: $surface_raised;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 5px;
    selection-background-color: $selection;
    selection-color: $text;
    outline: 0;
    padding: 4px;
}
QCheckBox, QRadioButton { spacing: 8px; color: $text; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 17px; height: 17px;
    border: 1px solid $border_strong;
    background: $input;
    border-radius: 4px;
}
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: $accent;
    border-color: $accent;
}
QCheckBox#switch::indicator {
    width: 38px; height: 21px; border-radius: 11px; background: $border_strong;
}
QCheckBox#switch::indicator:checked { background: $accent; }
QCheckBox#switch::indicator::unchecked { image: none; }

/* Buttons */
QPushButton {
    background: $surface_raised;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 5px;
    padding: 8px 16px;
    min-height: 18px;
}
QPushButton:hover { background: $surface_hover; border-color: $text_muted; }
QPushButton:pressed { background: $border_strong; }
QPushButton:disabled { color: $text_muted; background: $surface_alt; border-color: $border; }
QPushButton#primary {
    background: $primary;
    color: $primary_text;
    border: 0;
    border-radius: 6px;
    font-weight: 600;
    padding: 10px 20px;
}
QPushButton#primary:hover { background: $primary_hover; }
QPushButton#primary:pressed { background: $primary_pressed; }
QPushButton#danger { background: $danger; color: #ffffff; border: 0; border-radius: 6px; }
QPushButton#danger:hover { background: $danger_hover; }
QPushButton#danger:pressed { background: $danger_soft; }
QPushButton#flat {
    background: transparent; color: $text_secondary; border: 0; padding: 6px 10px;
    border-radius: 5px;
}
QPushButton#flat:hover { background: $surface_hover; color: $text; }

/* Buttons embedded in version/extension table cells need to remain usable
   when the launcher is scaled down.  Their labels are deliberately short,
   so a smaller horizontal inset gives the table's responsive layout room to
   wrap actions onto a second row instead of clipping them at the cell edge. */
QPushButton[actionButton="true"] {
    padding: 4px 7px;
    min-height: 14px;
}

/* Console and data views */
QPlainTextEdit#console {
    background: $console;
    color: $console_text;
    border: 1px solid $border;
    border-radius: 0;
    padding: 12px 14px;
    font-family: $mono_font;
    font-size: 13px;
    selection-background-color: $selection;
    selection-color: $text;
}
QTableWidget, QTableView {
    background: $surface;
    alternate-background-color: $surface_alt;
    color: $text;
    gridline-color: $border;
    border: 1px solid $border;
    border-radius: 6px;
    selection-background-color: $selection;
    selection-color: $text;
    outline: 0;
}
QTableWidget::item, QTableView::item { padding: 7px 9px; border: 0; }
QTableWidget::item:selected, QTableView::item:selected { background: $selection; }
QHeaderView { background: transparent; border: 0; }
QHeaderView::section {
    background: $surface_raised;
    color: $text_secondary;
    border: 0;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
    padding: 9px 10px;
    font-weight: 600;
}
QListWidget {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
}
QListWidget::item { padding: 8px 10px; border-bottom: 1px solid $border; }
QListWidget::item:hover { background: $surface_hover; }
QListWidget::item:selected { background: $selection; color: $text; }
QListWidget#breadcrumb { border: 1px solid $border; }

/* Tabs, scrolling, menus, and status badges */
QTabWidget::pane { border: 0; border-top: 1px solid $border; background: transparent; }
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: transparent; color: $text_secondary; padding: 18px 16px 16px;
    border: 0; border-bottom: 3px solid transparent; margin-right: 3px;
    min-height: 50px;
}
QTabBar::tab:hover { color: $text; border-bottom-color: $border_strong; }
QTabBar::tab:selected { color: $text; border-bottom-color: $accent; }
QScrollArea, QAbstractScrollArea, QScrollArea#pageScroll, QScrollArea#launchScroll,
QScrollArea#maintScroll, QScrollArea#fullScroll {
    background: transparent;
    border: 0;
}
/* QTextBrowser's viewport inherits QAbstractScrollArea's transparent rule;
   this more specific selector restores the raised announcement card. */
QTextBrowser#announcementBox {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 12px 14px;
}
QWidget#scrollContent, QWidget#fullParams { background: transparent; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: $scrollbar; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $scrollbar_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scrollbar; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: $scrollbar_hover; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QMenuBar { background: $title_bar; color: $text; }
QMenu { background: $surface_raised; color: $text; border: 1px solid $border_strong; }
QMenu::item { padding: 7px 24px 7px 12px; }
QMenu::item:selected { background: $selection; }
QMessageBox { background: $window; }
QMessageBox QLabel { color: $text; min-width: 260px; }
QGroupBox { margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 2px 8px; color: $text_secondary; font-weight: 600; }
QFrame#hr, QFrame#divider { background: $border; border: 0; max-height: 1px; }
QLabel#statusBadge, QLabel#statusRunning, QLabel#statusError {
    border-radius: 10px; padding: 5px 14px; min-width: 96px; font-weight: 600;
}
QLabel#statusBadge { background: $surface_raised; color: $text_secondary; }
QLabel#statusRunning { background: $success_soft; color: $success; }
QLabel#statusError { background: $danger_soft; color: $accent_hover; }
QToolButton#titleHelp, QToolButton#titleTheme, QToolButton#titleMin,
QToolButton#titleMax, QToolButton#titleClose {
    background: transparent; border: 0; color: $text_secondary; border-radius: 4px;
}
QToolButton#titleHelp:hover, QToolButton#titleTheme:hover, QToolButton#titleMin:hover,
QToolButton#titleMax:hover { background: $surface_hover; color: $text; }
QToolButton#titleClose:hover { background: $danger; color: #ffffff; }

/* Inline page navigation used by Advanced/Version/Settings.  The actual
   QTabWidget tab bars stay hidden for compatibility, while this bar sits in
   the same 88px toolbar as the reference launcher actions. */
QTabBar#inlineTabs {
    background: transparent;
    qproperty-drawBase: 0;
}
QTabBar#inlineTabs::tab {
    min-height: 58px;
    padding: 12px 16px 10px;
    font-size: 19px;
}
"""
)


def stylesheet(theme: str | None = "dark") -> str:
    """Render the Qt stylesheet for *theme* (dark by default).

    The function accepts the historical ``stylesheet(theme)`` signature and
    returns a new string each time, preventing callers from mutating a shared
    stylesheet accidentally.
    """

    return _QSS.substitute(get_tokens(theme).as_dict())


def dark_stylesheet() -> str:
    """Convenience wrapper for integrations that do not store a theme value."""

    return stylesheet("dark")


def light_stylesheet() -> str:
    """Convenience wrapper for integrations that do not store a theme value."""

    return stylesheet("light")


def apply_theme(application, theme: str | None = "dark") -> str:
    """Apply and return a stylesheet on a ``QApplication``-like object.

    The loose protocol keeps Qt optional for callers that only import this
    module.  It is convenient for plugins and tests while the main window can
    continue calling ``QApplication.instance().setStyleSheet`` directly.
    """

    rendered = stylesheet(theme)
    if application is not None:
        setter = getattr(application, "setStyleSheet", None)
        if callable(setter):
            setter(rendered)
    return rendered


# Friendly aliases used by a few downstream integrations.
theme_tokens = get_tokens
tokens = get_tokens
get_theme_tokens = get_tokens


# The old module exposed these private strings; retain them for downstream
# callers while making the token implementation the single source of truth.
_DARK: Final[str] = dark_stylesheet()
_LIGHT: Final[str] = light_stylesheet()
DARK_QSS: Final[str] = _DARK
LIGHT_QSS: Final[str] = _LIGHT


__all__ = [
    "DARK",
    "DARK_THEME",
    "LIGHT",
    "LIGHT_THEME",
    "DARK_TOKENS",
    "DARK_QSS",
    "LIGHT_TOKENS",
    "LIGHT_QSS",
    "THEMES",
    "THEME_TOKENS",
    "TOKENS",
    "ThemeTokens",
    "apply_theme",
    "dark_stylesheet",
    "get_tokens",
    "get_theme_tokens",
    "light_stylesheet",
    "normalize_theme",
    "palette",
    "stylesheet",
    "theme_tokens",
    "tokens",
]
