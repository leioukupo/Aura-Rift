from __future__ import annotations

import shlex
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QTimer, QSize, Qt, Signal, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from aura_rift import __version__
from aura_rift.config import (
    APP_NAME,
    AppConfig,
    ConfigStore,
    bundled_markdown,
    default_comfy_dir,
    PYPI_MIRRORS,
)
from aura_rift.i18n import Translator
from aura_rift.services import environment
from aura_rift.services.comfy import (
    ComfyProcess,
    create_venv_commands,
    filter_cli_args,
    install_comfy_commands,
    install_manager_commands,
    install_missing_deps_commands,
    install_plugin_command,
    reinstall_requirements_commands,
    reinstall_package_command,
    platform_blocked_cli_flags,
    supported_cli_flags,
)
from aura_rift.services.files import directory_size_hint, ensure_dir, open_path
from aura_rift.services.git_service import (
    DirtyRepositoryError,
    GitChange,
    GitError,
    GitService,
    git_command_args,
)
from aura_rift.services.registry import ExtensionEntry, get_extensions, mark_installed, search_entries
from aura_rift.services.tasks import CommandSpec, TaskHandle
from aura_rift.theme import stylesheet
from aura_rift.ui.icons import make_lightbulb_icon, make_nav_icon
from aura_rift.ui.components import (
    Banner,
    ExpandCard,
    LinkCard,
    TitleBar,
    Toggle,
    WarningBar,
)


import re
import unicodedata

# --- Console rendering: ANSI color parsing + emoji support -------------------
# Aura-Rift streams ComfyUI / git / pip subprocess output into a read-only
# console.  ComfyUI colours its own logs with ANSI SGR codes (see
# ComfyUI/app/logger.py: INFO green, DEBUG cyan, WARNING yellow+bold, ERROR
# red+bold).  Instead of stripping those (as the old plain-text console did)
# we parse them here into QTextCharFormat runs so the launcher shows the same
# colour as a terminal.  Emoji / arrow / box glyphs are kept and rendered via
# Qt font fallback, dropping to ASCII labels only when no emoji font exists.

# Fully-formed escape sequences emitted by subprocesses.
#  - OSC (title/clipboard), terminated by BEL or ST (\x1b \)
#  - CSI (SGR colour, cursor moves, clears), final byte 0x40-0x7e
_ALL_ESC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]")
# A trailing, possibly incomplete CSI (`\x1b[1;3` with no final byte yet) that
# may arrive split across two ready-read chunks; buffered until the rest comes.
_INCOMPLETE_ESC_RE = re.compile(r"\x1b\[?[0-9;?]*$")
# Other non-rendering control characters (keep \n and \t).  Also strips a lone
# ESC that never formed a complete sequence.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Curated emoji -> ASCII labels, used *only* as a fallback when no emoji-capable
# font is registered (so the console never shows tofu / missing-glyph boxes).
_EMOJI_MAP = {
    "\U0001f525": "[fire] ",
    "\U0001f4a1": "[tip] ",
    "\u2714": "[ok] ",
    "\u2705": "[ok] ",
    "\u2716": "[x] ",
    "\u274c": "[x] ",
    "\u26a0": "[warn] ",
    "\u26a0\ufe0f": "[warn] ",
    "\U0001f680": "[go]  ",
    "\U0001f6a8": "[!]   ",
}


def _emoji_font_available() -> bool:
    """True when a font capable of rendering emoji glyphs is registered with Qt."""
    from PySide6.QtGui import QFontDatabase  # local import: heavy symbol table
    for fam in QFontDatabase.families():
        if "emoji" in fam.lower():
            return True
    return False


# Detected on first append, then cached for the window's lifetime.
_EMOJI_AVAILABLE: bool | None = None


# Default console text colour — matches QPlainTextEdit#console in both themes.
_CONSOLE_DEFAULT_FG = "#d6e2d0"


def _resource_file(name: str) -> Path | None:
    """Return a bundled visual resource, with a source-tree fallback.

    The launcher is also run directly from a checkout during development, so
    resource lookup intentionally works both for an installed wheel and for
    ``src/`` without depending on a Windows DLL.
    """
    try:
        from aura_rift.resources import resource_path
        candidate = resource_path(name)
        if candidate is not None:
            return candidate
    except (FileNotFoundError, ModuleNotFoundError, TypeError, ValueError):
        pass
    for candidate in (
        Path(__file__).resolve().parents[1] / "resources" / name,
        Path.cwd() / "src" / "aura_rift" / "resources" / name,
        Path.cwd() / "resources" / name,
    ):
        if candidate.is_file():
            return candidate
    return None


def _launcher_catalog(config: AppConfig):
    """Return the catalog selected by the current AppConfig.

    Keeping this tiny adapter at the UI boundary avoids each page silently
    falling back to the repository catalog when the user selected a custom
    ComfyUI checkout or an explicit ``catalog_path``.
    """
    from aura_rift.services.catalog import LauncherCatalog
    factory = getattr(LauncherCatalog, "from_config", None)
    if callable(factory):
        return factory(config)
    # Keep compatibility with older/test doubles that only expose the
    # original constructor.
    return LauncherCatalog(getattr(config, "catalog_path", "") or None)


def _web_ui_url(config: AppConfig) -> str:
    """Build the browser URL using the same bind semantics as ComfyUI.

    With ``--listen`` disabled ComfyUI binds loopback regardless of a stale
    host value in the config.  Wildcard bind addresses are not routable from
    a browser URL, so map them to localhost; preserve custom IPv4/IPv6 hosts
    and use HTTPS when both TLS files are configured.
    """
    launch = config.launch
    # Keep the historical IPv4 loopback spelling for deterministic copied
    # commands and integrations; it is equivalent to ``localhost`` but does
    # not depend on a host-file/IPv6 preference on Linux desktops.
    loopback = "127.0.0.1"
    host = str(launch.host or loopback).strip()
    if not launch.listen:
        host = loopback
    elif "," in host:
        # ComfyUI accepts a comma-separated listen host list.  That is a
        # bind configuration rather than a routable browser host; choose the
        # first concrete address and fall back to loopback when the list only
        # contains wildcard binds (for example ``0.0.0.0,::``).
        wildcard_hosts = {"", "0.0.0.0", "::", "[::]", "*"}
        candidates = [part.strip() for part in host.split(",")]
        host = next((part for part in candidates if part not in wildcard_hosts), loopback)
    elif host in {"", "0.0.0.0", "::", "[::]", "*"}:
        host = loopback
    # urlsplit-style bracketed IPv6 literals are required in host:port URLs.
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"
    full = getattr(config, "full", None)
    keyfile = str(getattr(full, "tls_keyfile", "") or "").strip()
    certfile = str(getattr(full, "tls_certfile", "") or "").strip()
    scheme = "https" if keyfile and certfile else "http"
    return f"{scheme}://{host}:{int(launch.port)}"


def _announcement_text(config: AppConfig) -> str:
    """Resolve the front-page announcement with explicit source precedence."""
    local = bundled_markdown(
        "announcement.md",
        include_package=False,
        extra_paths=[Path(config.comfy_path).expanduser() / "announcement.md"],
    )
    if local:
        return local
    try:
        catalog = _launcher_catalog(config)
        value = getattr(catalog, "announcement", "")
        announcement = value() if callable(value) else str(value or "")
        if announcement:
            return announcement
    except Exception:
        pass
    return bundled_markdown("announcement.md")

# 16-colour ANSI palette tuned for the near-black console background (#0c0d11).
# Index 0 (black) is mapped to a visible gray so it never vanishes into the bg.
_ANSI_PALETTE = [
    QColor("#5b6472"),  # 0  black
    QColor("#ef6b6b"),  # 1  red
    QColor("#7ee787"),  # 2  green
    QColor("#f5d76b"),  # 3  yellow
    QColor("#6cb6ff"),  # 4  blue
    QColor("#d699ff"),  # 5  magenta
    QColor("#56d4dd"),  # 6  cyan
    QColor("#e6e8ee"),  # 7  white
    QColor("#9ca3af"),  # 8  bright black
    QColor("#ff8a80"),  # 9  bright red
    QColor("#a3f7a3"),  # 10 bright green
    QColor("#ffe066"),  # 11 bright yellow
    QColor("#9cc8ff"),  # 12 bright blue
    QColor("#e8b6ff"),  # 13 bright magenta
    QColor("#8ae8f0"),  # 14 bright cyan
    QColor("#ffffff"),  # 15 bright white
]


def _ansi_256(n: int) -> QColor:
    """Map an xterm 256-colour index to a QColor."""
    if 0 <= n < 16:
        return QColor(_ANSI_PALETTE[n])
    if 16 <= n < 232:
        k = n - 16
        ch = (k // 36, (k // 6) % 6, k % 6)

        def level(v: int) -> int:
            return 0 if v == 0 else 55 + 40 * v

        return QColor(level(ch[0]), level(ch[1]), level(ch[2]))
    if 232 <= n < 256:  # grayscale ramp
        v = 8 + (n - 232) * 10
        return QColor(v, v, v)
    return QColor(_ANSI_PALETTE[7])


def _normalize_emoji(text: str) -> str:
    """Keep emoji only when a capable font exists; else degrade to ASCII."""
    global _EMOJI_AVAILABLE
    if not text:
        return text
    if _EMOJI_AVAILABLE is None:
        _EMOJI_AVAILABLE = _emoji_font_available()

    if _EMOJI_AVAILABLE:
        return text  # Qt font fallback renders the glyphs
    text = re.sub(r"[\ue000-\uf8ff]", "-", text)
    text = (
        text.replace("\u2192", "->")
            .replace("\u2190", "<-")
            .replace("\u25b8", ">")
            .replace("\u25c2", "<")
            .replace("\u25b6", ">")
            .replace("\u25c0", "<]")
    )
    for emoji, label in _EMOJI_MAP.items():
        text = text.replace(emoji, label)
    out = []
    for ch in text:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cat == "So" or cp >= 0x1F000 or 0xE000 <= cp <= 0xF8FF:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


class AnsiConsoleParser:
    """Streaming ANSI-to-QTextCharFormat renderer for the console widget.

    ``feed`` is called repeatedly with raw chunks from the subprocess and keeps
    the running SGR state across calls (so a colour code split between two
    chunks is still applied) plus a small buffer for a trailing, incomplete CSI
    escape.  Carriage-return progress lines are collapsed to their final
    segment; emoji glyphs are kept and rely on Qt font fallback, degrading to
    ASCII labels only when no emoji font is registered.
    """

    def __init__(self, default_fg: str = _CONSOLE_DEFAULT_FG) -> None:
        self._default_fg = QColor(default_fg)
        self._pending = ""
        self._fg = self._default_fg
        self._bg: QColor | None = None
        self._bold = False
        self._italic = False
        self._underline = False
        self._fmt = self._build_format()

    def _build_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Bold if self._bold else QFont.Normal)
        fmt.setFontItalic(self._italic)
        fmt.setFontUnderline(self._underline)
        fmt.setForeground(QBrush(self._fg))
        if self._bg is not None:
            fmt.setBackground(QBrush(self._bg))
        return fmt

    def reset(self) -> None:
        """Drop buffered/buffered state so each run starts from default colour."""
        self._pending = ""
        self._fg = self._default_fg
        self._bg = None
        self._bold = self._italic = self._underline = False
        self._fmt = self._build_format()

    @staticmethod
    def _collapse_cr(text: str) -> str:
        return "\n".join(
            line.rsplit("\r", 1)[-1] if "\r" in line else line
            for line in text.split("\n")
        )

    def _emit_plain(self, cursor: QTextCursor, raw: str) -> None:
        if not raw:
            return
        plain = _CTRL_RE.sub("", raw)
        plain = _normalize_emoji(plain)
        if not plain:
            return
        for part in plain.splitlines(keepends=True):
            cursor.insertText(part, self._format_for_plain(part))

    def _format_for_plain(self, text: str) -> QTextCharFormat:
        if self._bg is not None or self._bold or self._italic or self._underline or self._fg != self._default_fg:
            return self._fmt
        line = text.strip().lower()
        if not line:
            return self._fmt

        def fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
            out = QTextCharFormat(self._fmt)
            out.setForeground(QBrush(QColor(color)))
            out.setFontWeight(QFont.Bold if bold else QFont.Normal)
            out.setFontItalic(italic)
            return out

        if line.startswith(("fatal:", "error:", "exception", "traceback")) or " failed" in line or "失败" in line or "错误" in line:
            return fmt("#ff8a80", bold=True)
        if line.startswith(("warning:", "warn:", "hint:")) or "warning" in line or "警告" in line:
            return fmt("#f5d76b", italic=line.startswith("hint:"))
        if line.startswith(("from ", "来自 ", "remote:", "取得：", "命中：")):
            return fmt("#8ae8f0")
        if line.startswith(("already up to date", "已经是最新", "done", "完成")):
            return fmt("#a3f7a3")
        return self._fmt

    def feed(self, text: str, widget: QPlainTextEdit) -> None:
        if not text:
            return
        text = self._pending + text
        self._pending = ""
        hold = _INCOMPLETE_ESC_RE.search(text)  # partial CSI at the tail
        if hold:
            self._pending = text[hold.start():]
            text = text[: hold.start()]
        if not text:
            return
        text = self._collapse_cr(text)
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.End)
        pos = 0
        for match in _ALL_ESC_RE.finditer(text):
            self._emit_plain(cursor, text[pos: match.start()])
            seq = match.group(0)
            if not seq.startswith("\x1b]"):  # OSC is dropped; CSI SGR applied
                self._apply_sgr(seq)
            pos = match.end()
        self._emit_plain(cursor, text[pos:])

    def _apply_sgr(self, seq: str) -> None:
        """Mutate the running colour/style state from one CSI sequence."""
        if not seq.endswith("m"):
            return  # cursor moves / clears are not rendered here
        body = seq[2:-1]  # strip "\x1b[" and "m"
        if not body:
            self._fg = self._default_fg
            self._bg = None
            self._bold = self._italic = self._underline = False
            self._fmt = self._build_format()
            return
        try:
            codes = [int(c) if c != "" else 0 for c in body.split(";")]
        except ValueError:
            return
        i = 0
        n = len(codes)
        changed = False
        while i < n:
            c = codes[i]
            if c == 0:
                self._fg = self._default_fg
                self._bg = None
                self._bold = self._italic = self._underline = False
                changed = True
            elif c == 1:
                self._bold = True
                changed = True
            elif c in (2,):
                pass  # faint: not rendered
            elif c == 3:
                self._italic = True
                changed = True
            elif c == 4:
                self._underline = True
                changed = True
            elif c == 22:
                self._bold = False
                changed = True
            elif c == 23:
                self._italic = False
                changed = True
            elif c == 24:
                self._underline = False
                changed = True
            elif c == 39:
                self._fg = self._default_fg
                changed = True
            elif c == 49:
                self._bg = None
                changed = True
            elif 30 <= c <= 37:
                self._fg = QColor(_ANSI_PALETTE[c - 30])
                changed = True
            elif 40 <= c <= 47:
                self._bg = QColor(_ANSI_PALETTE[c - 40])
                changed = True
            elif 90 <= c <= 97:
                self._fg = QColor(_ANSI_PALETTE[c - 90 + 8])
                changed = True
            elif 100 <= c <= 107:
                self._bg = QColor(_ANSI_PALETTE[c - 100 + 8])
                changed = True
            elif c in (38, 48):
                color, i = self._parse_extended(codes, i + 1)
                if color is not None:
                    if c == 38:
                        self._fg = color
                    else:
                        self._bg = color
                    changed = True
                continue  # _parse_extended already advanced i
            # other codes (blink, conceal, reverse, cursor) are ignored
            i += 1
        if changed:
            self._fmt = self._build_format()

    @staticmethod
    def _parse_extended(codes: list[int], i: int) -> tuple[QColor | None, int]:
        """Parse a `5;n` or `2;r;g;b` extended-colour payload at index *i*."""
        if i >= len(codes):
            return None, len(codes)
        mode = codes[i]
        if mode == 5:
            if i + 1 < len(codes):
                return _ansi_256(codes[i + 1]), i + 2
            return None, len(codes)
        if mode == 2:
            if i + 3 < len(codes):
                return (
                    QColor(codes[i + 1] & 0xFF, codes[i + 2] & 0xFF, codes[i + 3] & 0xFF),
                    i + 4,
                )
            return None, len(codes)
        return None, min(i + 1, len(codes))  # legacy/unknown form: skip one


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def label(text: str, size: int | None = None, bold: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setWordWrap(True)
    if size or bold:
        font = QFont()
        if size:
            font.setPointSize(size)
        font.setBold(bold)
        widget.setFont(font)
    return widget


class InternalFileBrowser(QDialog):
    def __init__(self, start_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("文件夹")
        self.resize(780, 520)
        self.current = start_path.expanduser()

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.path_label = QLabel()
        up_button = QPushButton("上一级")
        up_button.clicked.connect(self.go_up)
        top.addWidget(self.path_label, 1)
        top.addWidget(up_button)
        layout.addLayout(top)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.open_item)
        layout.addWidget(self.list_widget, 1)
        self.populate()

    def populate(self) -> None:
        self.path_label.setText(str(self.current))
        self.list_widget.clear()
        try:
            entries = sorted(self.current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as exc:
            self.list_widget.addItem(f"无法读取：{exc}")
            return
        for entry in entries:
            prefix = "[目录] " if entry.is_dir() else "[文件] "
            item = QListWidgetItem(prefix + entry.name)
            item.setData(Qt.UserRole, str(entry))
            self.list_widget.addItem(item)

    def go_up(self) -> None:
        if self.current.parent != self.current:
            self.current = self.current.parent
            self.populate()

    def open_item(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.UserRole))
        if path.is_dir():
            self.current = path
            self.populate()
        else:
            open_path(path)


class ConsolePage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("pageHeader")
        header.setFixedHeight(88)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        title_lbl = label(self.window._tr("nav.console", "控制台"), 18, True)
        header_layout.addWidget(title_lbl)
        self.status = QLabel(self.window._tr("console.idle", "未运行"))
        self.status.setObjectName("statusBadge")
        self.status.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.status)
        header_layout.addStretch(1)
        stop_button = QPushButton(self.window._tr("button.stop", "终止进程"))
        stop_button.setObjectName("danger")
        stop_button.setMinimumWidth(140)
        stop_button.setFixedHeight(40)
        stop_button.clicked.connect(window.stop_comfy)
        start_button = QPushButton("一键启动")
        start_button.setObjectName("primary")
        primary_icon = "#171717" if self.window.config.theme != "light" else "#ffffff"
        start_button.setIcon(make_nav_icon("play", primary_icon, 20))
        start_button.setIconSize(QSize(20, 20))
        start_button.setMinimumWidth(140)
        start_button.setFixedHeight(40)
        start_button.clicked.connect(window.start_comfy)
        header_layout.addWidget(stop_button)
        header_layout.addWidget(start_button)
        layout.addWidget(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("console")
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setUndoRedoEnabled(False)
        self.output.document().setMaximumBlockCount(12000)
        # Monospace with emoji + CJK fallback so glyphs like fire / ok / arrows
        # render instead of tofu even though no single font covers everything.
        console_font = QFont()
        console_font.setStyleHint(QFont.Monospace)
        console_font.setFamilies([
            "JetBrains Mono", "Cascadia Code", "Consolas", "Menlo",
            "Noto Color Emoji", "Noto Sans Mono CJK SC",
            "Microsoft YaHei", "monospace",
        ])
        self.output.setFont(console_font)
        self.output.setTabStopDistance(self.output.fontMetrics().horizontalAdvance(" ") * 4)
        self.parser = AnsiConsoleParser()
        layout.addWidget(self.output, 1)

    def append(self, text: str) -> None:
        self.parser.feed(text, self.output)
        self.output.moveCursor(QTextCursor.End)

    def clear_output(self) -> None:
        """Clear the console so each new run starts from a fresh log."""
        self.output.clear()
        self.parser.reset()

    def set_status(self, status: str) -> None:
        running = ("运行" in status) and ("未" not in status)
        error = ("错" in status) or ("失败" in status)
        self.status.setObjectName("statusRunning" if running else ("statusError" if error else "statusBadge"))
        self.status.setText(status)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class LaunchPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        # The reference launcher has a generous desktop canvas, but the
        # compact 1280x800/Linux window must still expose every control.  A
        # page-local scroll viewport lets the same geometry breathe at the
        # reference size and remains usable when the announcement and folder
        # cards need more vertical room on a small display.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        page_scroll = QScrollArea()
        page_scroll.setObjectName("launchScroll")
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.NoFrame)
        page_content = QWidget()
        page_content.setObjectName("launchContent")
        root = QVBoxLayout(page_content)
        root.setContentsMargins(32, 36, 32, 28)
        # The reference leaves a deliberate breathing band between the banner
        # and the folder/announcement columns.
        root.setSpacing(53)
        page_scroll.setWidget(page_content)
        outer.addWidget(page_scroll)

        banner_path = _resource_file("hanabi.jpg") or _resource_file("banner.jpg")
        hero = Banner(banner_path)
        hero.setMinimumHeight(190)
        hero.setMaximumHeight(376)
        # Keep a readable brand overlay even when the optional extracted image
        # is unavailable in a source checkout.  The stacked overlay mirrors
        # the text block in the reference launcher.
        overlay = QWidget()
        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(76, 0, 20, 0)
        overlay_layout.addStretch(1)
        kicker = QLabel("ComfyUI")
        kicker.setObjectName("bannerKicker")
        title = QLabel("Aura-Rift 启动器")
        title.setObjectName("bannerTitle")
        subtitle = QLabel("让 AI 与你一起创作，让画笔随心所欲！")
        subtitle.setObjectName("bannerSubtitle")
        overlay_layout.addWidget(kicker)
        overlay_layout.addWidget(title)
        overlay_layout.addWidget(subtitle)
        overlay_layout.addStretch(1)
        hero.add_overlay(overlay)
        root.addWidget(hero)

        path_card = card()
        path_card.setObjectName("pathCard")
        path_layout = QHBoxLayout(path_card)
        path_layout.setContentsMargins(18, 14, 18, 14)
        path_layout.addWidget(label("ComfyUI 目录", 14, True))
        self.path_edit = QLineEdit()
        self.path_edit.setText(self.window.config.comfy_path)
        path_layout.addWidget(self.path_edit, 1)
        choose = QPushButton("选择已有")
        choose.clicked.connect(self.choose_comfy)
        install = QPushButton("新建安装")
        install.clicked.connect(self.install_comfy)
        save = QPushButton("保存路径")
        save.clicked.connect(self.save_path)
        path_layout.addWidget(choose)
        path_layout.addWidget(install)
        path_layout.addWidget(save)
        # The path is managed from 设置 in the Linux layout.  Keep the legacy
        # editors as a hidden compatibility surface for integrations/tests.
        path_card.setVisible(False)
        root.addWidget(path_card)

        middle = QHBoxLayout()
        middle.setSpacing(18)
        self.middle_layout = middle
        left = QVBoxLayout()
        left.setSpacing(30)
        left.addWidget(label("文件夹", 18, True))
        grid = QGridLayout()
        # Cards sit slightly inset beneath the section heading in the
        # reference launcher; keeping this as a grid margin also prevents the
        # three columns from touching the main content edge on compact views.
        grid.setContentsMargins(16, 0, 16, 0)
        grid.setSpacing(12)
        self.folder_buttons: list[QPushButton] = []
        folders = [
            ("根目录", ".", "folder"),
            ("自定义节点", "custom_nodes", "git-branch"),
            ("输入图片", "input", "image"),
            ("输出图片", "output", "image"),
            ("模型", "models", "package"),
        ]
        for index, (title, rel, icon_name) in enumerate(folders):
            button = QPushButton(f"{title}\n{rel}   ›")
            button.setObjectName("folderCard")
            button.setIcon(make_nav_icon(icon_name, "#f2f2f2", 28))
            button.setIconSize(QSize(28, 28))
            # A scoped QSS rule keeps the card height from being replaced by
            # the generic QPushButton minimum-height rule.
            button.setProperty("folderCardLarge", True)
            button.setMinimumHeight(102)
            button.clicked.connect(lambda _=False, r=rel: self.open_folder(r))
            grid.addWidget(button, index // 3, index % 3)
            self.folder_buttons.append(button)
        left.addLayout(grid)
        left.addStretch(1)

        self.launcher_version_label = label(f"启动器版本：{__version__}", 12)
        self.description_version_label = label("描述文件版本：内置", 12)
        self.version_label = label("ComfyUI：未检测", 12)
        self.env_label = label("环境：未检测", 12)
        # Stable aliases used by integrations that predate the target-style
        # labels.  Keep the widgets themselves as the values so callers can
        # still read/update text without reaching into private layout items.
        self.path = self.path_edit
        self.launcher_version = self.launcher_version_label
        self.description_version = self.description_version_label
        self.comfy_version = self.version_label
        self.environment_status = self.env_label
        left.addWidget(self.launcher_version_label)
        left.addWidget(self.description_version_label)
        left.addWidget(self.version_label)
        left.addWidget(self.env_label)

        right = QVBoxLayout()
        right.setSpacing(18)
        right.setAlignment(Qt.AlignTop)
        announcement_title = label("公告", 18, True)
        # Prevent the flexible middle column from donating all spare height
        # to the heading when the browser has a maximum height.
        announcement_title.setFixedHeight(32)
        right.addWidget(announcement_title)
        self.announcement = QTextBrowser()
        self.announcement.setObjectName("announcementBox")
        # Keep the target's tall announcement on desktop, while allowing the
        # right column to shrink at 1280px without creating horizontal scroll.
        self.announcement.setMinimumHeight(360)
        self.announcement.setMaximumHeight(455)
        self.announcement.setMinimumWidth(0)
        self.announcement.setMarkdown(_announcement_text(self.window.config))
        right.addWidget(self.announcement, 0)
        start = QPushButton("一键启动")
        start.setObjectName("primary")
        primary_icon = "#171717" if self.window.config.theme != "light" else "#ffffff"
        start.setIcon(make_nav_icon("play", primary_icon, 30))
        start.setIconSize(QSize(30, 30))
        start.setProperty("launchButtonLarge", True)
        start.setFixedHeight(104)
        start.setMinimumWidth(300)
        start.clicked.connect(self.window.start_comfy)
        right.addSpacing(50)
        right.addWidget(start, 0, Qt.AlignRight)

        # Cap the folder column on very wide displays.  The reference leaves
        # a generous gutter before the announcement card instead of stretching
        # each folder tile to half a screen.
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(1360)
        self.left_widget = left_widget
        middle.addWidget(left_widget, 1)
        # Wide reference captures leave a broad gutter between folder cards
        # and the announcement column; the spacer is reduced when the layout
        # switches to a vertical compact arrangement below.
        middle.addSpacing(111)
        self.middle_gap = middle.itemAt(middle.count() - 1).spacerItem()
        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setMinimumWidth(240)
        right_widget.setMaximumWidth(423)
        self.right_widget = right_widget
        middle.addWidget(right_widget)
        root.addLayout(middle, 1)

    def refresh(self) -> None:
        # A local announcement is an explicit user/project override.  Only
        # when it is absent should the selected catalog win, followed by the
        # packaged offline copy.
        announcement = _announcement_text(self.window.config)
        self.announcement.setMarkdown(announcement)
        try:
            catalog = _launcher_catalog(self.window.config)
            self.description_version_label.setText(f"描述文件版本：{catalog.get('version', '内置')}")
        except Exception:
            pass
        self.path_edit.setText(self.window.config.comfy_path)
        comfy = self.window.comfy_dir()
        if (comfy / ".git").exists():
            try:
                git = GitService(comfy)
                commit = git.current_commit()[:8]
                branch = git.current_branch()
                self.version_label.setText(f"ComfyUI：{branch} / {commit}")
            except GitError as exc:
                self.version_label.setText(f"ComfyUI：{exc}")
        else:
            self.version_label.setText("ComfyUI：未检测到 Git 仓库")
        status = environment.dependency_status(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        self.env_label.setText("环境：" + "，".join(f"{k} {v}" for k, v in status.items()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        hero = self.findChild(Banner)
        if hero is not None:
            # Reference banner is intentionally panoramic (~5:1 in the
            # 2107×1317 capture); preserve that rhythm at smaller windows.
            target = max(190, min(376, int(self.width() * 0.198)))
            hero.setFixedHeight(target)
        right_widget = getattr(self, "right_widget", None)
        if right_widget is not None:
            # Preserve the roughly 400px announcement column at the target
            # desktop size, then give the folder grid more room on compact
            # windows so the page only needs a vertical (not horizontal)
            # scroll bar.
            width = self.width()
            if width < 1000:
                self.middle_layout.setDirection(QBoxLayout.TopToBottom)
                if self.middle_gap is not None:
                    self.middle_gap.changeSize(0, 28, QSizePolicy.Minimum, QSizePolicy.Fixed)
                # Reserve the viewport's scrollbar gutter as well as the
                # page margins; otherwise a narrow window gets an avoidable
                # 10px horizontal scrollbar while the vertical one is shown.
                target_width = max(240, width - 80)
            else:
                self.middle_layout.setDirection(QBoxLayout.LeftToRight)
                if self.middle_gap is not None:
                    # Interpolate the desktop gutter: the 2107px reference
                    # uses a broad 111px gap, while a 1280px window needs a
                    # smaller one to avoid a horizontal page scrollbar.
                    gap = min(111, max(64, int(28 + (width - 828) * 0.194)))
                    self.middle_gap.changeSize(gap, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
                target_width = 423 if width >= 1400 else 400
            self.middle_layout.invalidate()
            right_widget.setFixedWidth(target_width)

    def choose_comfy(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)
            self.save_path()

    def save_path(self) -> None:
        self.window.config.comfy_path = self.path_edit.text().strip() or str(default_comfy_dir())
        self.window.save_config()
        self.window.refresh_pages()

    def install_comfy(self) -> None:
        self.save_path()
        target = self.window.comfy_dir()
        if target.exists() and any(target.iterdir()):
            QMessageBox.warning(self, "无法安装", "目标目录已经存在且非空，请选择空目录或已有 ComfyUI。")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        self.window.run_commands(
            install_comfy_commands(target, self.window.config),
            "安装 ComfyUI",
        )

    def open_folder(self, relative: str) -> None:
        comfy = self.window.comfy_dir()
        path = comfy if relative == "." else comfy / relative
        if relative != ".":
            ensure_dir(path)
        if not open_path(path):
            InternalFileBrowser(path, self).exec()


class AdvancedPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("pageHeader")
        header.setFixedHeight(88)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        self.advanced_header_title = label(self.window._tr("adv.title", "高级选项"), 16, True)
        # Keep the old title label as a compatibility attribute, but place
        # the visible navigation tabs directly in the toolbar like 绘世.
        self.advanced_header_title.setVisible(False)
        accent = "#f2f2f2" if self.window.config.theme != "light" else "#333333"
        cmd_button = QPushButton("启动命令提示符")
        cmd_button.clicked.connect(self.open_command_shell)
        preview_button = QPushButton(self.window._tr("button.show_command", "显示启动命令"))
        preview_button.setObjectName("flat")
        preview_button.clicked.connect(self.show_launch_command)
        start_button = QPushButton(self.window._tr("button.start", "一键启动"))
        start_button.setIcon(make_nav_icon("play", accent, 20))
        start_button.setIconSize(QSize(20, 20))
        start_button.clicked.connect(window.start_comfy)
        header_layout.addStretch(1)
        header_layout.addWidget(preview_button)
        header_layout.addWidget(cmd_button)
        header_layout.addWidget(start_button)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_launch_options(), self.window._tr("adv.title", "高级选项"))
        tabs.addTab(self._build_maintenance(), self.window._tr("maint.title", "环境维护"))
        # FullOptions remains available to expert-mode command generation, but
        # is folded into the advanced model rather than exposed as a fourth
        # top-level tab (matching 绘世 2.9.2's hierarchy).
        self.full_params_page = self._build_full_params()
        self.patch_page = PatchPage(window)
        tabs.addTab(self.patch_page, "补丁管理")
        self.advanced_tab_titles = [
            self.window._tr("adv.title", "高级选项"),
            self.window._tr("maint.title", "环境维护"),
            "补丁管理",
        ]
        self.adv_tabs = tabs
        # Public compatibility alias: integrations and older offscreen tests
        # referred to the tab widget simply as ``advanced_page.tabs`` before
        # the inline 绘世-style navigation bar was introduced.
        self.tabs = tabs
        tabs.tabBar().setVisible(False)
        self.adv_nav_tabs = QTabBar(header)
        self.adv_nav_tabs.setObjectName("inlineTabs")
        self.adv_nav_tabs.setExpanding(False)
        self.adv_nav_tabs.setDrawBase(False)
        for index, title in enumerate(self.advanced_tab_titles):
            icon = make_nav_icon(("sliders", "wrench", "package")[index], accent, 18)
            self.adv_nav_tabs.addTab(icon, title)
        self.adv_nav_tabs.setCurrentIndex(0)
        self.adv_nav_tabs.currentChanged.connect(tabs.setCurrentIndex)
        tabs.currentChanged.connect(self.adv_nav_tabs.setCurrentIndex)
        header_layout.insertWidget(0, self.adv_nav_tabs)
        self._adv_tabs_index = 0
        tabs.currentChanged.connect(self.on_advanced_tab_changed)
        layout.addWidget(tabs, 1)

        # Public compatibility surface retained for older integrations and
        # offscreen tests.  The visible controls still use the more explicit
        # method names below.
        self.save = self.apply_launch_options
        self.restore_defaults = self.reset_launch_options
        self.show_command = self.show_launch_command
        self.open_shell = self.open_command_shell

    def on_advanced_tab_changed(self, index: int) -> None:
        self._adv_tabs_index = index
        if 0 <= index < len(self.advanced_tab_titles):
            self.advanced_header_title.setText(self.advanced_tab_titles[index])
        if index == 1:
            self.refresh()  # environment maintenance tab
        elif index == 2:
            self.patch_page.refresh()

    def _build_launch_options(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("launchScroll")
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        # 绘世 keeps the advanced page's performance heading and reset action
        # in the content toolbar rather than burying them below a long list of
        # cards.  Keep the actual save action at the bottom (where it remains
        # reachable after scrolling), but expose a second reset affordance in
        # the same visual position as the reference launcher.
        heading_row = QHBoxLayout()
        heading_row.addWidget(label("性能设置", 21, True))
        heading_row.addStretch(1)
        reset_top = QPushButton("恢复默认设置")
        reset_top.clicked.connect(self.reset_launch_options)
        heading_row.addWidget(reset_top)
        root.addLayout(heading_row)

        self.engine_combo = self.combo(
            [
                ("自动选择", "auto"),
                ("GPU / CUDA", "gpu"),
                ("CPU", "cpu"),
            ],
            (
                "cpu" if self.window.config.full.cpu
                else "gpu" if self.window.config.full.gpu_only
                else "auto"
            ),
        )
        root.addWidget(self.option_row("生成引擎", "选择参与计算的硬件引擎；自动模式交由 ComfyUI 决定", self.engine_combo))

        self.vram_combo = self.combo(
            [
                ("由 ComfyUI 决定", "auto"),
                ("低显存", "lowvram"),
                ("普通显存", "normalvram"),
                ("高显存", "highvram"),
                ("无显存/CPU", "novram"),
            ],
            self.window.config.launch.vram_mode,
        )
        root.addWidget(self.option_row("显存优化", "选择 ComfyUI 显存策略", self.vram_combo))

        self.attention_combo = self.combo(
            [
                ("由 ComfyUI 决定", "auto"),
                ("Split Cross Attention", "split"),
                ("Sub-Quadratic", "quad"),
                ("PyTorch Cross Attention", "pytorch"),
            ],
            self.window.config.launch.attention,
        )
        root.addWidget(self.option_row("Cross-Attention 优化方案", "参考 ComfyUI 启动参数", self.attention_combo))

        self.precision_combo = self.combo(
            [("自动", "auto"), ("强制 FP16", "fp16"), ("强制 FP32", "fp32")],
            self.window.config.launch.precision,
        )
        root.addWidget(self.option_row("计算精度设置", "平衡速度、显存占用与兼容性", self.precision_combo))

        self.preview_combo = self.combo(
            [
                ("自动", "auto"),
                ("关闭预览", "none"),
                ("latent2rgb", "latent2rgb"),
                ("taesd", "taesd"),
            ],
            self.window.config.launch.preview_method,
        )
        root.addWidget(self.option_row("预览图生成模式", "选择生成过程中的预览算法", self.preview_combo))

        self.cpu_vae_check = Toggle("使用 CPU 运行 VAE")
        self.cpu_vae_check.setChecked(self.window.config.launch.cpu_vae)
        root.addWidget(self.option_row("VAE 运行位置", "显存紧张时可启用，但速度会下降", self.cpu_vae_check))

        self.fast_check = Toggle("启用实验性提速")
        self.fast_check.setChecked(bool(str(self.window.config.full.fast or "").strip()))
        root.addWidget(self.option_row("提速策略", "启用 ComfyUI 的实验性快速优化；可能影响稳定性或结果", self.fast_check))

        self.cache_combo = self.combo(
            [("由 ComfyUI 决定", "auto"), ("LRU 缓存", "lru"), ("经典缓存", "classic"), ("不缓存", "none")],
            self.window.config.launch.cache_strategy,
        )
        root.addWidget(self.option_row("缓存策略", "管理模型在显存中的缓存方式", self.cache_combo))

        self.disable_smart_memory_check = Toggle("禁用智能内存管理")
        self.disable_smart_memory_check.setChecked(self.window.config.launch.disable_smart_memory)
        root.addWidget(self.option_row("智能内存管理", "关闭后 ComfyUI 会手动管理显存上下文，显存占用更保守", self.disable_smart_memory_check))

        self.vae_precision_combo = self.combo(
            [("自动", "auto"), ("BF16", "bf16"), ("FP16", "fp16"), ("FP32", "fp32")],
            self.window.config.launch.vae_precision,
        )
        root.addWidget(self.option_row("VAE 精度", "VAE 解码精度，BF16/FP16 省显存但略有精度损失", self.vae_precision_combo))

        self.text_enc_precision_combo = self.combo(
            [("自动", "auto"), ("FP8 E4M3FN", "e4m3fn"), ("FP8 E5M2", "e5m2")],
            self.window.config.launch.text_enc_precision,
        )
        root.addWidget(self.option_row("文本编码器精度", "FP8 可大幅降低显存，需硬件支持", self.text_enc_precision_combo))

        self.cuda_malloc_check = Toggle("使用 CUDA Malloc")
        self.cuda_malloc_check.setChecked(self.window.config.launch.cuda_malloc)
        root.addWidget(self.option_row("CUDA 内分配", "启用后可能提升速度，部分环境可能不稳定", self.cuda_malloc_check))

        self.deterministic_check = Toggle("稳定计算（确定性算法）")
        self.deterministic_check.setChecked(self.window.config.full.deterministic)
        root.addWidget(self.option_row("稳定计算", "牺牲部分速度换取更可复现的结果", self.deterministic_check))
        self.multi_user_check = Toggle("启用多用户模式")
        self.multi_user_check.setChecked(self.window.config.full.multi_user)
        root.addWidget(self.option_row("多用户", "允许 ComfyUI 按用户目录隔离数据", self.multi_user_check))
        self.hf_offline_advanced = Toggle("HuggingFace 离线")
        self.hf_offline_advanced.setChecked(self.window.config.huggingface_offline)
        root.addWidget(self.option_row("模型网络", "仅使用本地 HuggingFace 缓存", self.hf_offline_advanced))

        network_card = card()
        form = QGridLayout(network_card)
        form.setContentsMargins(18, 18, 18, 18)
        self.listen_check = QCheckBox("允许局域网访问")
        self.listen_check.setChecked(self.window.config.launch.listen)
        self.host_edit = QLineEdit(self.window.config.launch.host)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.window.config.launch.port)
        self.disable_auto_launch = QCheckBox("不自动打开浏览器")
        self.disable_auto_launch.setChecked(self.window.config.launch.disable_auto_launch)
        self.extra_args = QLineEdit(self.window.config.launch.extra_args)
        self.extra_args.setPlaceholderText("额外 ComfyUI 参数，例如 --front-end-version Comfy-Org/ComfyUI_frontend@latest")
        self.enable_cors_edit = QLineEdit(self.window.config.launch.enable_cors)
        self.enable_cors_edit.setPlaceholderText("留空则不启用 CORS，例如 *")
        self.output_directory_edit = QLineEdit(self.window.config.launch.output_directory)
        self.output_directory_edit.setPlaceholderText("留空使用默认 output 目录")
        self.input_directory_edit = QLineEdit(self.window.config.launch.input_directory)
        self.input_directory_edit.setPlaceholderText("留空使用默认 input 目录")
        form.addWidget(label("监听 / API 与目录", 16, True), 0, 0, 1, 2)
        form.addWidget(self.listen_check, 1, 0)
        form.addWidget(self.host_edit, 1, 1)
        form.addWidget(label("端口"), 2, 0)
        form.addWidget(self.port_spin, 2, 1)
        form.addWidget(self.disable_auto_launch, 3, 0, 1, 2)
        form.addWidget(label("CORS Header"), 4, 0)
        form.addWidget(self.enable_cors_edit, 4, 1)
        form.addWidget(label("输出目录"), 5, 0)
        form.addWidget(self.output_directory_edit, 5, 1)
        form.addWidget(label("输入目录"), 6, 0)
        form.addWidget(self.input_directory_edit, 6, 1)
        form.addWidget(label("额外参数"), 7, 0)
        form.addWidget(self.extra_args, 7, 1)
        root.addWidget(network_card)

        self.expert_params_button = QPushButton("专家模式：更多性能 / 网络 / 安全参数")
        self.expert_params_button.setObjectName("flat")
        self.expert_params_button.clicked.connect(self.show_full_params)
        self.expert_params_button.setVisible(bool(self.window.config.expert_mode))
        root.addWidget(self.expert_params_button)

        root.addStretch(1)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = QPushButton("恢复默认设置")
        reset.clicked.connect(self.reset_launch_options)
        save = QPushButton("保存高级选项")
        save.clicked.connect(self.apply_launch_options)
        buttons.addWidget(reset)
        buttons.addWidget(save)
        outer.addLayout(buttons)
        return page

    def show_full_params(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("专家参数")
        dialog.resize(900, 680)
        layout = QVBoxLayout(dialog)
        # Reparenting the existing page keeps all legacy controls and
        # FullOptions.to_args() mappings intact without adding a fourth tab.
        page = self.full_params_page
        if page.parent() is not dialog:
            page.setParent(dialog)
        layout.addWidget(page, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh_full_params()
        dialog.exec()
        page.setParent(self)

    def _build_full_params(self) -> QWidget:
        page = QWidget()
        page.setObjectName("fullParams")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("fullScroll")
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(14)

        self.full_widgets: dict[str, QWidget] = {}
        full = self.window.config.full

        def section(title: str) -> None:
            h = label(title, 15, True)
            h.setObjectName("sectionTitle")
            root.addWidget(h)

        def spacer(width: int, height: int) -> QWidget:
            w = QWidget()
            w.setFixedSize(width, height)
            return w

        def row(cfg_key: str, title: str, desc: str, control: QWidget) -> None:
            self.full_widgets[cfg_key] = control
            control.setObjectName(f"full_{cfg_key}")
            root.addWidget(self.option_row(title, desc, control))

        def check(cfg_key: str, value: bool) -> QCheckBox:
            cb = QCheckBox("启用")
            cb.setChecked(value)
            return cb

        def text(value: str, placeholder: str = "") -> QLineEdit:
            le = QLineEdit()
            le.setText(value)
            if placeholder:
                le.setPlaceholderText(placeholder)
            return le

        def combo(items: list[tuple[str, str]], current: str) -> QComboBox:
            cb = QComboBox()
            for txt, val in items:
                cb.addItem(txt, val)
            idx = cb.findData(current)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            return cb

        def spin_int(value: int, lo: int, hi: int, unset: int = -1) -> QSpinBox:
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setValue(value)
            return sp

        def spin_double(value: float, lo: float, hi: float, step: float = 0.1) -> QDoubleSpinBox:
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setValue(value)
            return sp

        # ---- GPU 与设备 ----
        section("GPU 与设备")
        row("cuda_device", "CUDA 设备 ID", "设置单个 CUDA 设备 ID，留空则全部可见", text(full.cuda_device, "0"))
        row("default_device", "默认设备 ID", "多卡时设为默认使用的设备 ID，其余仍可见。留 -1 不设置", spin_int(full.default_device, -1, 31))
        # DirectML is a Windows-only backend and is intentionally absent from
        # the Linux edition.  Keep a detached compatibility control so old
        # integrations can still read/write the migrated field without
        # painting a dead Windows setting into the page.
        directml_control = combo(
            [("关闭", "-2"), ("自动", "-1"), ("0", "0"), ("1", "1"), ("2", "2")],
            str(full.directml),
        )
        self.full_widgets["directml"] = directml_control
        directml_control.setVisible(False)
        row("oneapi_device_selector", "oneAPI 设备选择", "oneAPI 设备选择字符串", text(full.oneapi_device_selector))
        row("supports_fp8_compute", "声明支持 FP8 计算", "让 ComfyUI 当作设备支持 FP8 计算", check("supports_fp8_compute", full.supports_fp8_compute))
        row("enable_triton_backend", "启用 Triton 后端", "允许 comfy-kitchen 使用 Triton；需要已安装对应组件", check("enable_triton_backend", full.enable_triton_backend))
        row("fp16_intermediates", "FP16 中间张量", "在节点之间使用 FP16 中间张量，可能降低显存占用", check("fp16_intermediates", full.fp16_intermediates))
        row("disable_ipex_optimize", "禁用 IPEX 优化", "Intel XPU 环境下禁用 ipex.optimize", check("disable_ipex_optimize", full.disable_ipex_optimize))
        row("disable_cuda_malloc", "禁用 CUDA Malloc", "与高级 CUDA 内存分配开关互斥", check("disable_cuda_malloc", full.disable_cuda_malloc))
        row("force_channels_last", "强制 channels-last", "推理时强制使用 channels-last 内存布局", check("force_channels_last", full.force_channels_last))
        row("fp32_unet", "扩散模型 FP32", "以 FP32 精度运行扩散模型", check("fp32_unet", full.fp32_unet))
        row("fp64_unet", "扩散模型 FP64", "以 FP64 精度运行扩散模型", check("fp64_unet", full.fp64_unet))
        row("bf16_unet", "扩散模型 BF16", "以 BF16 精度运行扩散模型", check("bf16_unet", full.bf16_unet))
        row("fp16_unet", "扩散模型 FP16", "以 FP16 精度运行扩散模型", check("fp16_unet", full.fp16_unet))
        row("fp8_e4m3fn_unet", "扩散模型 FP8 e4m3fn", "以 FP8 e4m3fn 格式存储扩散模型权重", check("fp8_e4m3fn_unet", full.fp8_e4m3fn_unet))
        row("fp8_e5m2_unet", "扩散模型 FP8 e5m2", "以 FP8 e5m2 格式存储扩散模型权重", check("fp8_e5m2_unet", full.fp8_e5m2_unet))
        row("fp8_e8m0fnu_unet", "扩散模型 FP8 e8m0fnu", "以 FP8 e8m0fnu 格式存储扩散模型权重", check("fp8_e8m0fnu_unet", full.fp8_e8m0fnu_unet))
        row("force_non_blocking", "强制非阻塞操作", "强制所有适用张量使用非阻塞操作，部分非 Nvidia 设备可能更快但不稳", check("force_non_blocking", full.force_non_blocking))

        # ---- 显存 / 缓存 ----
        section("显存与缓存")
        row("cache_ram", "RAM 压力缓存阈值", "以 GB 为单位的阈值，空格分隔（可选第二项为 inactive 阈值），例如 4 或 4 8", text(full.cache_ram, "4 8"))
        row("high_ram", "高内存模式", "高内存机器可改善模型加载性能，并让 ComfyUI 使用经典缓存策略", check("high_ram", full.high_ram))
        row("gpu_only", "仅使用 GPU", "将文本编码器等全部驻留在 GPU；与 CPU 模式互斥", check("gpu_only", full.gpu_only))
        row("cpu", "纯 CPU 模式", "所有计算使用 CPU（速度较慢）", check("cpu", full.cpu))
        row("reserve_vram", "预留显存(GB)", "为系统/其他程序预留的显存，留 0 表示不设置", spin_double(full.reserve_vram, 0, 64, 0.1))
        row("vram_headroom", "动态显存余量(GB)", "动态显存模式额外保留的显存空间", spin_double(full.vram_headroom, 0, 64, 0.1))
        row("async_offload", "异步权重卸载", "自动=不启用，开启=默认 2 流，或填入自定义流数", combo([("自动", "auto"), ("开启(默认2流)", "on"), ("3 流", "3"), ("4 流", "4"), ("6 流", "6")], full.async_offload))
        row("disable_async_offload", "禁用异步卸载", "关闭异步权重卸载", check("disable_async_offload", full.disable_async_offload))
        row("disable_dynamic_vram", "禁用动态显存", "强制使用传统显存估算与模型加载", check("disable_dynamic_vram", full.disable_dynamic_vram))
        row("enable_dynamic_vram", "启用动态显存", "在默认未启用的设备上尝试动态显存", check("enable_dynamic_vram", full.enable_dynamic_vram))
        row("fast_disk", "快速磁盘卸载", "使用磁盘后端进行动态加载与卸载，适合高速 NVMe", check("fast_disk", full.fast_disk))
        row("disable_pinned_memory", "禁用 pinned memory", "关闭锁页内存，某些环境需要", check("disable_pinned_memory", full.disable_pinned_memory))
        row("deterministic", "确定性算法", "让 PyTorch 使用更慢但确定性的算法，部分场景可复现但不能保证结果完全一致", check("deterministic", full.deterministic))
        row("fast", "快速实验优化", "留空关闭；填写 all 或 fp16_accumulation,fp8_matrix_mult,cublas_ops,autotune", text(full.fast, "all"))

        # ---- Attention 优化 ----
        section("Cross-Attention 优化")
        row("use_sage_attention", "使用 Sage Attention", "采用 sage attention 实现", check("use_sage_attention", full.use_sage_attention))
        row("use_flash_attention", "使用 Flash Attention", "采用 FlashAttention 实现", check("use_flash_attention", full.use_flash_attention))
        row("disable_xformers", "禁用 xformers", "关闭 xformers 优化", check("disable_xformers", full.disable_xformers))
        row("force_upcast_attention", "强制上采样 Attention", "强制启用 attention 上采样，修复黑图可尝试", check("force_upcast_attention", full.force_upcast_attention))
        row("dont_upcast_attention", "禁止上采样 Attention", "关闭所有 attention 上采样，除调试外一般不需要", check("dont_upcast_attention", full.dont_upcast_attention))

        # ---- 文本编码器精度（额外变体）----
        section("文本编码器精度")
        row("fp16_text_enc", "FP16 文本编码器", "以 FP16 存储文本编码器权重", check("fp16_text_enc", full.fp16_text_enc))
        row("fp32_text_enc", "FP32 文本编码器", "以 FP32 存储文本编码器权重", check("fp32_text_enc", full.fp32_text_enc))
        row("bf16_text_enc", "BF16 文本编码器", "以 BF16 存储文本编码器权重", check("bf16_text_enc", full.bf16_text_enc))

        # ---- 预览 ----
        section("预览")
        row("preview_size", "预览最大尺寸", "采样节点的最大预览图边长（像素）", spin_int(full.preview_size, 64, 2048))

        # ---- 网络与服务端 ----
        section("网络与服务端")
        row("tls_keyfile", "TLS 密钥文件", "启用 HTTPS 所需密钥文件，需配合证书使用", text(full.tls_keyfile, "/path/to/key.pem"))
        row("tls_certfile", "TLS 证书文件", "启用 HTTPS 所需证书文件", text(full.tls_certfile, "/path/to/cert.pem"))
        row("max_upload_size", "最大上传体积(MB)", "上传体积上限", spin_double(full.max_upload_size, 1, 4096, 1))
        row("enable_compress_response_body", "压缩响应体", "启用 HTTP 响应体压缩", check("enable_compress_response_body", full.enable_compress_response_body))
        row("comfy_api_base", "Comfy API 基础 URL", "ComfyUI API 基础地址，留空使用默认 https://api.comfy.org", text(full.comfy_api_base, "https://api.comfy.org"))
        row("database_url", "数据库 URL", "例如 sqlite:///:memory:。留空使用默认", text(full.database_url, "sqlite:///:memory:"))
        row("enable_assets", "启用资产服务", "启用 ComfyUI 资产 API、数据库与后台扫描", check("enable_assets", full.enable_assets))
        row("enable_asset_hashing", "启用资产哈希", "为资产扫描计算 blake3 内容哈希，增加启动与扫描开销", check("enable_asset_hashing", full.enable_asset_hashing))
        row("feature_flags", "服务端 Feature Flags", "逗号分隔的 KEY 或 KEY=VALUE，例如 show_signin_button=true", text(full.feature_flags, "KEY=VALUE"))

        # ---- 目录 ----
        section("目录与前端")
        row("base_directory", "基础目录", "统一设置 models/custom_nodes/input/output/temp/user 的根目录", text(full.base_directory, "/path/to/base"))
        row("temp_directory", "临时目录", "覆盖默认 temp 目录", text(full.temp_directory, "/path/to/temp"))
        row("user_directory", "用户目录", "覆盖默认 user 目录", text(full.user_directory, "/path/to/user"))
        row("front_end_version", "前端版本", "格式 [owner]/[repo]@[version]，例如 Comfy-Org/ComfyUI_frontend@latest", text(full.front_end_version, "Comfy-Org/ComfyUI_frontend@latest"))
        row("front_end_root", "前端根目录", "本地前端目录路径，优先级高于前端版本", text(full.front_end_root, "/path/to/frontend"))
        row("extra_model_paths_config", "额外模型路径配置", "空格分隔的 extra_model_paths.yaml 路径", text(full.extra_model_paths_config, "/path/extra_model_paths.yaml"))

        # ---- 杂项 ----
        section("杂项")
        row("default_hashing_function", "哈希函数", "重复文件名/内容比对使用的哈希", combo([("默认(sha256)", ""), ("md5", "md5"), ("sha1", "sha1"), ("sha256", "sha256"), ("sha512", "sha512")], full.default_hashing_function))
        row("mmap_torch_files", "mmap 加载 ckpt/pt", "加载 ckpt/pt 文件时使用 mmap", check("mmap_torch_files", full.mmap_torch_files))
        row("disable_mmap", "禁用 mmap(safetensors)", "加载 safetensors 时不使用 mmap", check("disable_mmap", full.disable_mmap))
        row("dont_print_server", "不打印服务端输出", "关闭服务端日志输出", check("dont_print_server", full.dont_print_server))
        row("disable_metadata", "禁用元数据写入", "不在输出文件中保存 prompt 元数据", check("disable_metadata", full.disable_metadata))
        row("disable_all_custom_nodes", "禁用所有自定义节点", "加载时不启用任何 custom_nodes", check("disable_all_custom_nodes", full.disable_all_custom_nodes))
        row("whitelist_custom_nodes", "自定义节点白名单", "空格分隔，仅这些节点在禁用全部时仍加载", text(full.whitelist_custom_nodes, "ComfyUI-Manager"))
        row("disable_api_nodes", "禁用 API 节点", "禁用所有 api 节点并阻止前端联网", check("disable_api_nodes", full.disable_api_nodes))
        row("multi_user", "多用户模式", "启用按用户隔离存储", check("multi_user", full.multi_user))
        row("verbose", "日志级别", "留空=默认 INFO", combo([("默认", ""), ("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR"), ("CRITICAL", "CRITICAL")], full.verbose))
        row("log_stdout", "日志输出到 stdout", "常规进程输出改发到 stdout", check("log_stdout", full.log_stdout))
        row("enable_manager", "启用 Manager", "启用 ComfyUI-Manager", check("enable_manager", full.enable_manager))
        row("disable_manager_ui", "禁用 Manager UI", "仅关闭 Manager 界面与端点，后台任务仍运行", check("disable_manager_ui", full.disable_manager_ui))
        row("enable_manager_legacy_ui", "Manager 传统界面", "启用 Manager 传统界面，隐含 --enable-manager", check("enable_manager_legacy_ui", full.enable_manager_legacy_ui))

        self._setup_full_constraints()

        root.addStretch(1)
        outer.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        reset_btn = QPushButton(self.window._tr("button.reset", "恢复默认设置"))
        reset_btn.setMinimumWidth(170)
        reset_btn.setFixedHeight(48)
        reset_btn.clicked.connect(self.reset_full_params)
        save_btn = QPushButton("保存完整参数")
        save_btn.setObjectName("primary")
        save_btn.setMinimumWidth(170)
        save_btn.setFixedHeight(48)
        save_btn.clicked.connect(self.apply_full_params)
        bottom.addWidget(reset_btn)
        bottom.addWidget(save_btn)
        hint = QLabel("参数会进入启动命令，在『一键启动』时生效。仅当填入值时才会覆盖默认。")
        hint.setObjectName("footnote")
        bottom.addSpacing(14)
        bottom.addWidget(hint, 1)
        outer.addLayout(bottom)
        return page

    def _setup_full_constraints(self) -> None:
        """Wire mutually-exclusive ComfyUI flags so selecting one disables the rest.

        Groups follow ComfyUI's own argparse mutually_exclusive groups, plus a
        couple of logical pairs that are de-facto exclusive but parsed as plain
        store_true flags.
        """
        self._action_handles: list = []  # prevent GC of lambdas

        def group(keys: list[str]) -> None:
            widgets = [self.full_widgets[k] for k in keys if k in self.full_widgets]
            for w in widgets:
                self._action_handles.append(w)
                w.toggled.connect(lambda _=False, grp=widgets, src=w: self._enforce_group(grp, src))

        # same-page strict groups (checkboxes)
        group([
            "fp32_unet", "fp64_unet", "bf16_unet", "fp16_unet",
            "fp8_e4m3fn_unet", "fp8_e5m2_unet", "fp8_e8m0fnu_unet",
        ])
        group(["gpu_only", "cpu"])
        group(["use_sage_attention", "use_flash_attention"])
        group(["force_upcast_attention", "dont_upcast_attention"])
        group(["disable_dynamic_vram", "enable_dynamic_vram"])
        # text-encoder precision variants are pick-one
        group(["fp16_text_enc", "fp32_text_enc", "bf16_text_enc"])
        disable_cuda = self.full_widgets.get("disable_cuda_malloc")
        if isinstance(disable_cuda, QCheckBox) and hasattr(self, "cuda_malloc_check"):
            disable_cuda.toggled.connect(self._on_full_disable_cuda)
            self.cuda_malloc_check.toggled.connect(self._on_launch_cuda_malloc)
            self._action_handles.extend([disable_cuda, self.cuda_malloc_check])
        # combo + checkbox logical pair: async offload
        async_combo = self.full_widgets.get("async_offload")
        async_disable = self.full_widgets.get("disable_async_offload")
        if isinstance(async_combo, QComboBox) and isinstance(async_disable, QCheckBox):
            async_combo.currentIndexChanged.connect(lambda _=False: self._enforce_async(async_combo, async_disable, async_combo))
            async_disable.toggled.connect(lambda _=False: self._enforce_async(async_combo, async_disable, async_disable))
            self._action_handles.extend([async_combo, async_disable])

        # whitelist_custom_nodes only matters when disable_all_custom_nodes is on
        disable_nodes = self.full_widgets.get("disable_all_custom_nodes")
        wl = self.full_widgets.get("whitelist_custom_nodes")
        if isinstance(disable_nodes, QCheckBox) and isinstance(wl, QLineEdit):
            disable_nodes.toggled.connect(lambda _=False: self._enforce_whitelist(disable_nodes, wl))
            self._action_handles.extend([disable_nodes, wl])

        # Manager legacy UI requires Manager enabled -> cross-enable on toggle
        lid = self.full_widgets.get("enable_manager_legacy_ui")
        men = self.full_widgets.get("enable_manager")
        if isinstance(lid, QCheckBox) and isinstance(men, QCheckBox):
            lid.toggled.connect(lambda _=False: self._enforce_manager(men, lid))
            men.toggled.connect(lambda _=False: self._enforce_manager(men, lid))
            self._action_handles.extend([lid, men])

        # cross-page: keep the compact launch CUDA control and the expert
        # FullOptions disable flag mutually exclusive. Apply initial derived
        # states now (the toggled()
        # signal above does not fire when a control starts unchecked, so we
        # materialise the dependent states explicitly to avoid stale UI).
        if "disable_all_custom_nodes" in self.full_widgets and "whitelist_custom_nodes" in self.full_widgets:
            self._enforce_whitelist(self.full_widgets["disable_all_custom_nodes"], self.full_widgets["whitelist_custom_nodes"])
        if "async_offload" in self.full_widgets and "disable_async_offload" in self.full_widgets:
            self._enforce_async(self.full_widgets["async_offload"], self.full_widgets["disable_async_offload"])
        if "enable_manager" in self.full_widgets and "enable_manager_legacy_ui" in self.full_widgets:
            self._enforce_manager(self.full_widgets["enable_manager"], self.full_widgets["enable_manager_legacy_ui"])
        if isinstance(disable_cuda, QCheckBox):
            self._on_full_disable_cuda(disable_cuda.isChecked())

    def _enforce_group(self, grp: list, src) -> None:
        """Within a pick-one group, checking one disables the others."""
        if not isinstance(src, QCheckBox) or not src.isChecked():
            return
        for other in grp:
            if other is src:
                continue
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)

    def _enforce_async(self, combo: QComboBox, disable_check: QCheckBox, src=None) -> None:
        # async-offload is pick-one-OR-auto: a specific stream count ("on")
        # conflicts with --disable-async-offload. Whichever control the user
        # just moved wins; on init (src=None) preferring 'on' is safer.
        if combo.currentData() != "auto" and disable_check.isChecked():
            if src is disable_check:
                combo.blockSignals(True)
                idx = combo.findData("auto")
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
            else:
                disable_check.blockSignals(True)
                disable_check.setChecked(False)
                disable_check.blockSignals(False)

    def _enforce_whitelist(self, disable_nodes: QCheckBox, wl: QLineEdit) -> None:
        wl.setEnabled(disable_nodes.isChecked())

    def _enforce_manager(self, men: QCheckBox, lid: QCheckBox) -> None:
        if lid.isChecked() and not men.isChecked():
            men.blockSignals(True)
            men.setChecked(True)
            men.blockSignals(False)

    def _on_full_disable_cuda(self, checked: bool) -> None:
        """Keep the compact ``--cuda-malloc`` and expert disable flag exclusive."""
        launch = getattr(self, "cuda_malloc_check", None)
        if isinstance(launch, QCheckBox) and checked and launch.isChecked():
            launch.blockSignals(True)
            launch.setChecked(False)
            launch.blockSignals(False)

    def _on_launch_cuda_malloc(self, checked: bool) -> None:
        expert = self.full_widgets.get("disable_cuda_malloc") if hasattr(self, "full_widgets") else None
        if isinstance(expert, QCheckBox) and checked and expert.isChecked():
            expert.blockSignals(True)
            expert.setChecked(False)
            expert.blockSignals(False)

    def refresh_full_params(self) -> None:
        full = self.window.config.full
        # Pull current values back to the controls so switching tabs reflects
        # any external changes.
        for key, widget in getattr(self, "full_widgets", {}).items():
            if not hasattr(full, key):
                continue
            value = getattr(full, key)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findData(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value if value is not None else ""))

    def apply_full_params(self) -> None:
        full = self.window.config.full
        for key, widget in self.full_widgets.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                value = widget.value()
            elif isinstance(widget, QComboBox):
                value = widget.currentData()
                # directml stored as int
                if key == "directml":
                    value = int(value)
            elif isinstance(widget, QLineEdit):
                value = widget.text().strip()
            else:
                continue
            setattr(full, key, value)
        self.window.save_config()
        QMessageBox.information(self, "已保存", "完整参数已保存。")

    def reset_full_params(self) -> None:
        from aura_rift.config import FullOptions
        self.window.config.full = FullOptions()
        self.window.save_config()
        self.refresh_full_params()
        QMessageBox.information(self, "已重置", "完整参数已恢复默认。")

    def _build_maintenance(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("maintScroll")
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        root.addWidget(WarningBar("警告：在此页面随意操作可能导致软件包损坏。执行前请确认当前任务已经停止。"))

        torch_card = ExpandCard("安装 PyTorch / xFormers", "检测版本、强制重装和异步日志")
        torch_form = QGridLayout()
        torch_form.setHorizontalSpacing(12)
        torch_form.addWidget(label("PyTorch 版本"), 0, 0)
        self.torch_version_combo = QComboBox()
        torch_choices: list[tuple[str, object]] = [("自动匹配", ""), ("PyTorch 2.5 + CUDA 12.4", "torch==2.5.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124"), ("PyTorch 2.4 + CUDA 12.1", "torch==2.4.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121"), ("CPU", "torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu")]
        try:
            raw_versions = _launcher_catalog(self.window.config).section("torch_versions", [])
            if isinstance(raw_versions, list):
                for entry in raw_versions:
                    if isinstance(entry, dict):
                        # The bundled Aki catalog also contains DirectML
                        # (Windows-only) Torch recipes.  Aura-Rift is the
                        # Linux edition, so keep those records in the raw
                        # catalog for compatibility but never offer them as
                        # installable choices here.
                        engine_type = str(entry.get("engine_type") or "").strip().lower()
                        if engine_type in {"dml", "directml", "windows", "win32"}:
                            continue
                        text = str(entry.get("name") or entry.get("title") or entry.get("version") or "")
                        spec = str(entry.get("pip") or entry.get("command") or entry.get("package") or "")
                        step_specs: list[list[str]] = []
                        if not spec and isinstance(entry.get("steps"), list):
                            # Preserve each catalog step as its own pip task;
                            # xFormers often needs a different index/options.
                            for step in entry["steps"]:
                                if isinstance(step, dict) and isinstance(step.get("arguments"), list):
                                    step_specs.append([str(part) for part in step["arguments"] if str(part) != "install"])
                            spec = "__catalog_steps__" if step_specs else ""
                        if text and spec:
                            torch_choices.append((text, step_specs if step_specs else spec))
        except Exception:
            pass
        seen_torch: set[str] = set()
        for text, value in torch_choices:
            key = repr(value)
            if key in seen_torch:
                continue
            seen_torch.add(key)
            self.torch_version_combo.addItem(text, value)
        torch_form.addWidget(self.torch_version_combo, 0, 1)
        self.install_xformers_check = QCheckBox("同时安装 xFormers")
        torch_form.addWidget(self.install_xformers_check, 1, 0)
        self.force_torch_check = QCheckBox("强制重装")
        torch_form.addWidget(self.force_torch_check, 1, 1)
        install_torch = QPushButton("安装 / 更新")
        install_torch.clicked.connect(self.install_torch)
        torch_form.addWidget(install_torch, 2, 1, 1, 1, Qt.AlignRight)
        torch_card.add_widget(self._layout_widget(torch_form))
        root.addWidget(torch_card)

        # venv manager selector and dependency repair are a collapsed target
        # card; opening it exposes the existing operational controls.
        repair_card = ExpandCard("环境修复", "修复核心与扩展依赖")
        repair_body = QWidget()
        repair_root = QVBoxLayout(repair_body)
        repair_root.setContentsMargins(0, 0, 0, 0)
        repair_root.setSpacing(12)

        # venv manager selector
        self.venv_manager_combo = QComboBox()
        for mgr in environment.VenvManager:
            detected = environment.detect_venv_managers(self.window.comfy_dir()).get(mgr)
            suffix = "（已安装）" if detected and detected.available else "（未检测到）"
            if detected and detected.has_lock:
                suffix = "（检测到锁文件）"
            self.venv_manager_combo.addItem(f"{environment.MANAGER_LABELS[mgr]}{suffix}", mgr.value)
        index = self.venv_manager_combo.findData(self.window.config.venv_manager)
        if index >= 0:
            self.venv_manager_combo.setCurrentIndex(index)
        apply_mgr = QPushButton("应用并保存")
        apply_mgr.clicked.connect(self.apply_venv_manager)
        repair_root.addWidget(self.option_row(
            "虚拟环境管理器",
            "选择创建环境和管理依赖的方式。检测到锁文件时建议跟随。",
            self.venv_manager_combo,
        ))
        mgr_button_row = QHBoxLayout()
        mgr_button_row.addStretch(1)
        mgr_button_row.addWidget(apply_mgr)
        repair_root.addLayout(mgr_button_row)

        self.dep_table = QTableWidget(0, 2)
        self.dep_table.setHorizontalHeaderLabels(["项目", "状态"])
        self.dep_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dep_table.verticalHeader().setVisible(False)
        self.dep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        repair_root.addWidget(self.dep_table)

        self.torch_label = label("PyTorch：未检测")
        repair_root.addWidget(self.torch_label)

        actions = card()
        action_layout = QGridLayout(actions)
        action_layout.setContentsMargins(18, 18, 18, 18)
        refresh = QPushButton("刷新环境信息")
        refresh.clicked.connect(self.refresh)
        create = QPushButton("创建/补齐项目 .venv")
        create.clicked.connect(self.create_venv)
        self.package_edit = QLineEdit()
        self.package_edit.setPlaceholderText("输入包名，例如 numpy")
        reinstall = QPushButton("重装单个 Python 组件")
        reinstall.clicked.connect(self.reinstall_package)
        manager = QPushButton("安装或更新 ComfyUI-Manager")
        manager.clicked.connect(self.window.install_or_update_manager)
        repair = QPushButton("修复核心与扩展依赖")
        repair.clicked.connect(self.repair_dependencies)
        reinstall_all = QPushButton("重装已有依赖")
        reinstall_all.clicked.connect(self.reinstall_all_dependencies)
        action_layout.addWidget(refresh, 0, 0)
        action_layout.addWidget(create, 0, 1)
        action_layout.addWidget(self.package_edit, 1, 0)
        action_layout.addWidget(reinstall, 1, 1)
        action_layout.addWidget(manager, 2, 0, 1, 2)
        action_layout.addWidget(repair, 3, 0, 1, 2)
        action_layout.addWidget(reinstall_all, 4, 0, 1, 2)
        repair_root.addWidget(actions)
        repair_card.add_widget(repair_body)
        root.addWidget(repair_card)

        native_card = ExpandCard("原生组件管理", "Git、FFmpeg、CMake、Ninja")
        native_body = QWidget()
        native_root = QVBoxLayout(native_body)
        native_root.setContentsMargins(18, 16, 18, 16)
        native_root.addWidget(label("Git、FFmpeg、CMake、Ninja 仅显示 Linux 包管理器命令；执行前会在可见终端中请求权限。", 12))
        self.native_table = QTableWidget(0, 4)
        self.native_table.setHorizontalHeaderLabels(["组件", "状态", "版本", "操作"])
        self.native_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.native_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.native_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.native_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.native_table.verticalHeader().setVisible(False)
        self.native_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        native_root.addWidget(self.native_table)
        native_card.add_widget(native_body)
        root.addWidget(native_card)
        root.addStretch(1)
        outer.addWidget(scroll, 1)
        return page

    def combo(self, items: list[tuple[str, str]], current: str) -> QComboBox:
        combo = QComboBox()
        for text, value in items:
            combo.addItem(text, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    @staticmethod
    def _layout_widget(layout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def install_torch(self) -> None:
        selected = self.torch_version_combo.currentData()
        if isinstance(selected, list):
            package_steps = [list(map(str, step)) for step in selected]
        else:
            spec = str(selected or "").strip() or "torch torchvision torchaudio"
            try:
                package_steps = [shlex.split(spec)]
            except ValueError as exc:
                QMessageBox.warning(self, "版本参数无效", str(exc))
                return
        # Keep the package/index arguments explicit and visible in the task
        # console; no installer is invoked from the GUI thread.
        force = ["--upgrade", "--force-reinstall"] if self.force_torch_check.isChecked() else ["--upgrade"]
        comfy = self.window.comfy_dir()
        python = environment.resolve_python(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        if not self.window._dedicated_python_available(python):
            self.window.append_log(
                "未检测到 ComfyUI 专属 Python 环境，已阻止把 PyTorch/xFormers "
                "安装到 Aura-Rift 环境；请先在环境维护中创建虚拟环境。\n"
            )
            return
        commands: list[CommandSpec] = []
        # Catalog entries commonly contain a dedicated xFormers step.  Do not
        # append a second unpinned package to every Torch step when the user
        # checks the convenience box; only add one instruction to the group.
        if self.install_xformers_check.isChecked() and not any(
            "xformers" in item.lower() for step in package_steps for item in step
        ):
            if len(package_steps) == 1:
                package_steps[0] = [*package_steps[0], "xformers"]
            else:
                package_steps.append(["xformers"])
        for packages in package_steps:
            command = [str(python), "-m", "pip", "install", *force, *packages]
            from aura_rift.services.comfy import command_environment
            commands.append(CommandSpec(command, cwd=comfy, env=command_environment(self.window.config, scope="pip"), title="安装 PyTorch / xFormers"))
        self.window.run_commands(commands, "安装 PyTorch / xFormers")

    def option_row(self, title: str, desc: str, control: QWidget) -> QWidget:
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(18, 16, 18, 16)
        texts = QVBoxLayout()
        texts.addWidget(label(title, 15, True))
        texts.addWidget(label(desc, 12))
        layout.addLayout(texts, 1)
        layout.addWidget(control)
        return row

    def apply_launch_options(self) -> None:
        launch = self.window.config.launch
        self._sync_launch_options(launch)
        self.window.save_config()
        QMessageBox.information(self, "已保存", "高级选项已保存。")

    def _sync_launch_options(self, launch=None) -> None:
        """Copy the visible launch controls into the persisted model."""
        launch = launch or self.window.config.launch
        engine = self.engine_combo.currentData()
        self.window.config.full.cpu = engine == "cpu"
        self.window.config.full.gpu_only = engine == "gpu"
        self.window.config.full.fast = "all" if self.fast_check.isChecked() else ""
        launch.vram_mode = self.vram_combo.currentData()
        launch.attention = self.attention_combo.currentData()
        launch.precision = self.precision_combo.currentData()
        launch.preview_method = self.preview_combo.currentData()
        launch.cpu_vae = self.cpu_vae_check.isChecked()
        launch.cache_strategy = self.cache_combo.currentData()
        launch.disable_smart_memory = self.disable_smart_memory_check.isChecked()
        launch.vae_precision = self.vae_precision_combo.currentData()
        launch.text_enc_precision = self.text_enc_precision_combo.currentData()
        launch.cuda_malloc = self.cuda_malloc_check.isChecked()
        self.window.config.full.deterministic = self.deterministic_check.isChecked()
        self.window.config.full.multi_user = self.multi_user_check.isChecked()
        self.window.config.huggingface_offline = self.hf_offline_advanced.isChecked()
        launch.listen = self.listen_check.isChecked()
        launch.host = self.host_edit.text().strip() or "0.0.0.0"
        launch.port = self.port_spin.value()
        launch.disable_auto_launch = self.disable_auto_launch.isChecked()
        launch.enable_cors = self.enable_cors_edit.text().strip()
        launch.output_directory = self.output_directory_edit.text().strip()
        launch.input_directory = self.input_directory_edit.text().strip()
        launch.extra_args = self.extra_args.text().strip()

    def reset_launch_options(self) -> None:
        self.window.config.launch = AppConfig().launch
        self.window.save_config()
        self.window.rebuild()

    def show_launch_command(self) -> None:
        self._sync_launch_options()
        self.window.save_config()
        comfy = self.window.comfy_dir()
        python = environment.resolve_python(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        supported = supported_cli_flags(comfy)
        blocked = platform_blocked_cli_flags()
        launch_args, launch_unsupported = filter_cli_args(
            self.window.config.launch.to_args(),
            supported,
            blocked_flags=blocked,
        )
        try:
            raw_full_args = self.window.config.full.to_args(
                cache_strategy=self.window.config.launch.cache_strategy,
                cuda_malloc=self.window.config.launch.cuda_malloc,
                vram_mode=self.window.config.launch.vram_mode,
                attention=self.window.config.launch.attention,
                text_enc_precision=self.window.config.launch.text_enc_precision,
            )
        except TypeError:
            # Preserve command preview compatibility with legacy FullOptions
            # adapters that expose only ``to_args()``.
            raw_full_args = self.window.config.full.to_args()
        full_args, unsupported = filter_cli_args(
            raw_full_args, supported, blocked_flags=blocked
        )
        unsupported = [*launch_unsupported, *unsupported]
        args = [
            str(comfy / "main.py"),
            *launch_args,
            *full_args,
        ]
        command = " ".join(shlex.quote(str(part)) for part in [python, *args])
        self.window.show_page("console")
        if unsupported:
            self.window.append_log(
                "已忽略当前 ComfyUI 不支持的专家参数："
                + ", ".join(unsupported)
                + "\n"
            )
        self.window.append_log(command + "\n")
        box = QMessageBox(self)
        box.setWindowTitle("启动命令预览")
        box.setTextFormat(Qt.PlainText)
        box.setText(command)
        copy_button = box.addButton("复制命令", QMessageBox.AcceptRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is copy_button:
            QApplication.clipboard().setText(command)

    def open_command_shell(self) -> None:
        """Open the user's Linux shell in the ComfyUI directory.

        No Windows API or hidden privilege escalation is used.  The command
        is also written to the console so headless/offscreen environments have
        a useful, testable result.
        """
        comfy = self.window.comfy_dir()
        shell = os.environ.get("SHELL") or "bash"
        terminal_args: list[str] | None = None
        # Prefer a terminal emulator so the child shell is visible when the
        # launcher itself was started from a desktop entry without a TTY.
        candidates = [
            ("x-terminal-emulator", ["--working-directory", str(comfy), "-e", shell]),
            ("gnome-terminal", [f"--working-directory={comfy}", "--", shell]),
            ("konsole", ["--workdir", str(comfy), "-e", shell]),
            ("xfce4-terminal", [f"--working-directory={comfy}", "--command", shell]),
            ("kitty", ["--directory", str(comfy), shell]),
            ("alacritty", ["--working-directory", str(comfy), "-e", shell]),
        ]
        for executable, args in candidates:
            path = shutil.which(executable)
            if path:
                terminal_args = [path, *args]
                break
        command_preview = " ".join(shlex.quote(str(part)) for part in (terminal_args or [shell]))
        self.window.append_log(f"在 {comfy} 打开命令提示符：{command_preview}\n")
        try:
            if comfy.exists():
                subprocess.Popen(terminal_args or shlex.split(shell), cwd=str(comfy), start_new_session=True)
        except (OSError, ValueError) as exc:
            self.window.append_log(f"无法打开 shell：{exc}\n")

    def refresh(self) -> None:
        # Only run heavy env inspection when the maintenance tab is visible
        if getattr(self, "_adv_tabs_index", 0) != 1:
            return
        comfy = self.window.comfy_dir()
        deps = environment.dependency_status(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        self.dep_table.setRowCount(0)
        for key, value in deps.items():
            row = self.dep_table.rowCount()
            self.dep_table.insertRow(row)
            self.dep_table.setItem(row, 0, QTableWidgetItem(key))
            self.dep_table.setItem(row, 1, QTableWidgetItem(value))
        torch = environment.inspect_torch(
            environment.resolve_python(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        )
        self.torch_label.setText(
            f"PyTorch：{torch.torch}，CUDA：{torch.cuda}，设备：{torch.device}"
            if torch.installed
            else f"PyTorch：未安装或不可用（{torch.detail}）"
        )
        try:
            interpreter = environment.resolve_python(
                comfy,
                self.window.config.python_path_override,
                self.window.config.venv_manager,
            )
            probe = subprocess.run(
                [str(interpreter), "-c", "import importlib.metadata as m; print(m.version('xformers'))"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            xformers = probe.stdout.strip() if probe.returncode == 0 else "未安装"
        except (OSError, subprocess.SubprocessError, ValueError):
            xformers = "未安装"
        self.torch_label.setText(self.torch_label.text() + f"；xFormers：{xformers}")
        self._refresh_native_components()

    def _refresh_native_components(self) -> None:
        if not hasattr(self, "native_table"):
            return
        try:
            from aura_rift.services.native_components import NativeComponentService
            service = NativeComponentService()
            statuses = service.detect(["git", "ffmpeg", "cmake", "ninja"])
        except Exception as exc:
            statuses = [{"name": "检测失败", "reason": str(exc), "installed": False}]
            service = None
        self.native_table.setRowCount(len(statuses))
        for row, item in enumerate(statuses):
            if isinstance(item, dict):
                name = item.get("label", item.get("name", ""))
                installed = bool(item.get("installed", item.get("available", False)))
                version = item.get("version", "")
                reason = item.get("reason", "")
                component = item.get("name", name)
            else:
                name = getattr(item, "label", getattr(item, "name", ""))
                installed = bool(getattr(item, "installed", False))
                version = getattr(item, "version", "")
                reason = getattr(item, "reason", "")
                component = getattr(item, "name", name)
            self.native_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self.native_table.setItem(row, 1, QTableWidgetItem("已安装" if installed else (str(reason) or "未安装")))
            self.native_table.setItem(row, 2, QTableWidgetItem(str(version)))
            if service is not None:
                action = QWidget()
                action_layout = QHBoxLayout(action)
                action_layout.setContentsMargins(2, 2, 2, 2)
                if not installed:
                    button = QPushButton("安装命令")
                    button.clicked.connect(
                        lambda _=False, c=component, svc=service: self._show_native_command(svc, c)
                    )
                    action_layout.addWidget(button)
                else:
                    button = QPushButton("卸载命令")
                    button.clicked.connect(
                        lambda _=False, c=component, svc=service: self._show_native_command(
                            svc, c, uninstall=True
                        )
                    )
                    action_layout.addWidget(button)
                self.native_table.setCellWidget(row, 3, action)
            else:
                self.native_table.setItem(row, 3, QTableWidgetItem("—"))

    def _show_native_command(self, service, component: str, uninstall: bool = False) -> None:
        try:
            method = service.uninstall_command if uninstall else service.install_command
            command = method(component)
            if isinstance(command, list) and command and isinstance(command[0], list):
                command = command[0]
            text = " ".join(shlex.quote(str(part)) for part in command)
            box = QMessageBox(self)
            box.setWindowTitle("Linux 卸载命令" if uninstall else "Linux 安装命令")
            box.setText(f"请在可见终端执行：\n\n{text}")
            copy = box.addButton("复制", QMessageBox.AcceptRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is copy:
                QApplication.clipboard().setText(text)
        except Exception as exc:
            QMessageBox.warning(self, "无法生成命令", str(exc))

    def create_venv(self) -> None:
        comfy = self.window.comfy_dir()
        self.window.run_commands(create_venv_commands(comfy, self.window.config), "创建 .venv")

    def _ensure_dedicated_python(self, action: str) -> bool:
        """Prevent maintenance actions from targeting Aura-Rift's Python."""
        comfy = self.window.comfy_dir()
        python = environment.resolve_python(
            comfy, self.window.config.python_path_override, self.window.config.venv_manager
        )
        if self.window._dedicated_python_available(python):
            return True
        self.window.append_log(
            f"未检测到 ComfyUI 专属 Python 环境，已阻止{action}；"
            "请先创建/补齐项目虚拟环境。\n"
        )
        return False

    def repair_dependencies(self) -> None:
        if not self._ensure_dedicated_python("依赖修复"):
            return
        comfy = self.window.comfy_dir()
        files: list[Path] = []
        core = comfy / "requirements.txt"
        if core.exists():
            files.append(core)
        custom = comfy / "custom_nodes"
        if custom.exists():
            files.extend(sorted(custom.glob("*/requirements.txt")))
        if not files:
            QMessageBox.information(self, "没有依赖清单", "未找到 requirements.txt。")
            return
        self.window.run_commands(install_missing_deps_commands(comfy, files, self.window.config), "修复核心与扩展依赖")

    def reinstall_all_dependencies(self) -> None:
        if not self._ensure_dedicated_python("依赖重装"):
            return
        comfy = self.window.comfy_dir()
        files: list[Path] = []
        core = comfy / "requirements.txt"
        if core.exists():
            files.append(core)
        custom = comfy / "custom_nodes"
        if custom.exists():
            files.extend(sorted(custom.glob("*/requirements.txt")))
        if not files:
            QMessageBox.information(self, "没有依赖清单", "未找到 requirements.txt。")
            return
        self.window.run_commands(
            reinstall_requirements_commands(comfy, files, self.window.config),
            "重装核心与扩展依赖",
        )

    def reinstall_package(self) -> None:
        package = self.package_edit.text().strip()
        if not package:
            QMessageBox.warning(self, "缺少包名", "请输入需要重装的 Python 包名。")
            return
        # Keep the argv-based task safe from option injection while accepting
        # normal package extras such as ``onnxruntime-gpu[cuda]``.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}(?:\[[A-Za-z0-9_. ,-]{1,128}\])?", package):
            QMessageBox.warning(self, "包名无效", "请输入单个合法 Python 包名，不要包含命令行选项。")
            return
        if not self._ensure_dedicated_python("Python 组件重装"):
            return
        self.window.run_commands(
            [reinstall_package_command(self.window.comfy_dir(), package, self.window.config)],
            f"重装 {package}",
        )

    def apply_venv_manager(self) -> None:
        self.window.config.venv_manager = self.venv_manager_combo.currentData()
        self.window.save_config()
        self.refresh()
        QMessageBox.information(self, "已保存", "虚拟环境管理器已更新。")


class VersionPage(QWidget):
    extensions_loaded = Signal(list)

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self._install_loading = False
        self._version_tabs_index = 0
        self._installed_extension_rows: list[Path] = []
        self.extensions_loaded.connect(self._on_extensions_loaded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("pageHeader")
        header.setFixedHeight(88)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        self.version_header_title = label(self.window._tr("ver.core", "内核"), 16, True)
        self.version_header_title.setVisible(False)
        accent = "#f2f2f2" if self.window.config.theme != "light" else "#333333"
        refresh = QPushButton(self.window._tr("ver.refresh_list", "刷新列表"))
        refresh.setIcon(make_nav_icon("terminal", accent, 18))
        refresh.clicked.connect(self.reload_extension_list)
        update = QPushButton(self.window._tr("ver.update_all", "一键更新"))
        update.setIcon(make_nav_icon("git-branch", accent, 18))
        update.clicked.connect(self.update_all)
        header_layout.addWidget(refresh)
        header_layout.addWidget(update)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(
            self._build_core_tab(),
            make_nav_icon("git-branch", accent, 18),
            self.window._tr("ver.core", "内核"),
        )
        tabs.addTab(
            self._build_extensions_tab(),
            make_nav_icon("wrench", accent, 18),
            self.window._tr("ver.extensions", "扩展"),
        )
        tabs.addTab(
            self._build_install_tab(),
            make_nav_icon("rocket", accent, 18),
            self.window._tr("ver.install", "安装新扩展"),
        )
        self.version_tab_titles = [
            self.window._tr("ver.core", "内核"),
            self.window._tr("ver.extensions", "扩展"),
            self.window._tr("ver.install", "安装新扩展"),
        ]
        self.tabs = tabs
        tabs.tabBar().setVisible(False)
        self.version_nav_tabs = QTabBar(header)
        self.version_nav_tabs.setObjectName("inlineTabs")
        self.version_nav_tabs.setExpanding(False)
        self.version_nav_tabs.setDrawBase(False)
        for index, title in enumerate(self.version_tab_titles):
            icon = make_nav_icon(("git-branch", "wrench", "rocket")[index], accent, 18)
            self.version_nav_tabs.addTab(icon, title)
        self.version_nav_tabs.currentChanged.connect(tabs.setCurrentIndex)
        tabs.currentChanged.connect(self.version_nav_tabs.setCurrentIndex)
        header_layout.insertWidget(0, self.version_nav_tabs)
        header_layout.insertStretch(1, 1)
        tabs.currentChanged.connect(self.on_version_tab_changed)
        layout.addWidget(tabs, 1)

    def on_version_tab_changed(self, index: int) -> None:
        self._version_tabs_index = index
        if 0 <= index < len(self.version_tab_titles):
            self.version_header_title.setText(self.version_tab_titles[index])
        # Lazy: only refresh the tab if it hasn't been loaded yet
        loaded = getattr(self, "_loaded_tabs", set())
        if index not in loaded:
            self.refresh_current_tab()
            loaded.add(index)
            self._loaded_tabs = loaded

    def _build_core_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 24, 24, 24)
        self.remote_label = label("远程地址：")
        self.branch_label = label("当前分支：")
        self.commit_label = label("当前版本：")
        root.addWidget(self.remote_label)
        root.addWidget(self.branch_label)
        root.addWidget(self.commit_label)

        # channel selector: stable (tags) / dev (all commits)
        channel_card = card()
        channel_layout = QHBoxLayout(channel_card)
        channel_layout.setContentsMargins(18, 12, 18, 12)
        channel_layout.addWidget(label("版本通道", 14, True))
        self.channel_group = QButtonGroup(page)
        self.channel_stable = QPushButton("稳定版")
        self.channel_stable.setCheckable(True)
        self.channel_dev = QPushButton("开发版")
        self.channel_dev.setCheckable(True)
        self.channel_group.addButton(self.channel_stable)
        self.channel_group.addButton(self.channel_dev)
        self.channel_stable.clicked.connect(self.on_channel_change)
        self.channel_dev.clicked.connect(self.on_channel_change)
        self.channel_stable.setChecked(True)
        channel_layout.addWidget(self.channel_stable)
        channel_layout.addWidget(self.channel_dev)
        channel_layout.addStretch(1)
        root.addWidget(channel_card)

        # branch row—visible only in expert mode
        self.branch_row_widget = QWidget()
        branch_row = QHBoxLayout(self.branch_row_widget)
        branch_row.setContentsMargins(0, 0, 0, 0)
        self.branch_combo = QComboBox()
        switch_branch = QPushButton("切换分支")
        switch_branch.clicked.connect(self.checkout_branch)
        fetch = QPushButton("拉取远端信息")
        fetch.clicked.connect(self.fetch_core)
        branch_row.addWidget(label("分支"))
        branch_row.addWidget(self.branch_combo, 1)
        branch_row.addWidget(switch_branch)
        branch_row.addWidget(fetch)
        root.addWidget(self.branch_row_widget)

        self.commit_table = QTableWidget(0, 5)
        self.commit_table.setHorizontalHeaderLabels(["版本 ID", "提交信息", "日期", "当前", "操作"])
        header = self.commit_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        self.commit_table.setColumnWidth(0, 110)
        self.commit_table.setColumnWidth(2, 130)
        self.commit_table.setColumnWidth(3, 60)
        self.commit_table.setColumnWidth(4, 112)
        self.commit_table.verticalHeader().setVisible(False)
        self.commit_table.verticalHeader().setDefaultSectionSize(44)
        self.commit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.commit_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.commit_table, 1)
        return page

    def is_stable_channel(self) -> bool:
        return self.channel_stable.isChecked()

    def _build_extensions_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        search_row = QHBoxLayout()
        self.installed_extension_search = QLineEdit()
        self.installed_extension_search.setPlaceholderText("搜索已安装插件、远程地址、分支...")
        self.installed_extension_search.textChanged.connect(self.refresh_extensions)
        update_selected = QPushButton("更新选中扩展")
        update_selected.clicked.connect(self.update_selected_extension)
        open_selected = QPushButton("打开选中扩展目录")
        open_selected.clicked.connect(self.open_selected_extension)
        search_row.addWidget(self.installed_extension_search, 1)
        search_row.addWidget(open_selected)
        search_row.addWidget(update_selected)
        root.addLayout(search_row)

        self.extension_table = QTableWidget(0, 6)
        self.extension_table.setHorizontalHeaderLabels(["插件名", "远程地址", "当前分支", "版本 ID", "更新时间/状态", "操作"])
        header = self.extension_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.extension_table.setColumnWidth(0, 230)
        self.extension_table.setColumnWidth(2, 120)
        self.extension_table.setColumnWidth(3, 100)
        self.extension_table.setColumnWidth(4, 160)
        self.extension_table.setColumnWidth(5, 230)
        self.extension_table.verticalHeader().setVisible(False)
        self.extension_table.verticalHeader().setDefaultSectionSize(46)
        self.extension_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.extension_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.extension_table, 1)
        return page

    def _build_install_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        manager_card = card()
        manager_layout = QHBoxLayout(manager_card)
        manager_layout.setContentsMargins(18, 12, 18, 12)
        self.manager_label = label("ComfyUI-Manager：未检测")
        manager_button = QPushButton("安装或更新 ComfyUI-Manager")
        manager_button.clicked.connect(self.window.install_or_update_manager)
        manager_layout.addWidget(self.manager_label, 1)
        manager_layout.addWidget(manager_button)
        root.addWidget(manager_card)

        # Extension browser
        self.all_extensions: list[ExtensionEntry] = []
        search_row = QHBoxLayout()
        self.extension_search = QLineEdit()
        self.extension_search.setPlaceholderText("搜索扩展名称、作者、类别...")
        self._search_timer = None
        self.extension_search.textChanged.connect(self._on_search_text_changed)
        search_button = QPushButton("搜索")
        search_button.clicked.connect(self.filter_extensions)
        refresh_list = QPushButton("刷新列表")
        refresh_list.clicked.connect(self.reload_extension_list)
        search_row.addWidget(label("搜索扩展", 14, True))
        search_row.addWidget(self.extension_search, 1)
        search_row.addWidget(search_button)
        search_row.addWidget(refresh_list)
        root.addLayout(search_row)

        self.extension_count_label = label("加载中...")
        root.addWidget(self.extension_count_label)

        # Extension list in a scroll area
        self.extension_install_list = QTableWidget(0, 5)
        self.extension_installed_rows: set[int] = set()
        self._current_page = 0
        self._page_size = 100
        self._filtered_extensions: list[ExtensionEntry] = []
        self.extension_install_list.setHorizontalHeaderLabels(["插件名称", "简介", "作者/类别", "状态", "操作"])
        header = self.extension_install_list.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.extension_install_list.setColumnWidth(0, 230)
        self.extension_install_list.setColumnWidth(2, 170)
        self.extension_install_list.setColumnWidth(3, 90)
        self.extension_install_list.setColumnWidth(4, 120)
        self.extension_install_list.verticalHeader().setVisible(False)
        self.extension_install_list.verticalHeader().setDefaultSectionSize(72)
        self.extension_install_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.extension_install_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.extension_install_list.setMinimumHeight(300)
        self.extension_install_list.setWordWrap(True)
        root.addWidget(self.extension_install_list, 1)

        # Pagination toolbar
        pager = QHBoxLayout()
        pager.setSpacing(6)
        self.page_first = QPushButton("首页")
        self.page_prev = QPushButton("上一页")
        self.page_next = QPushButton("下一页")
        self.page_last = QPushButton("末页")
        self.page_first.setObjectName("flat")
        self.page_prev.setObjectName("flat")
        self.page_next.setObjectName("flat")
        self.page_last.setObjectName("flat")
        self.page_first.clicked.connect(lambda: self.go_to_page(0))
        self.page_prev.clicked.connect(lambda: self.go_to_page(self._current_page - 1))
        self.page_next.clicked.connect(lambda: self.go_to_page(self._current_page + 1))
        self.page_last.clicked.connect(lambda: self.go_to_page(-1))
        self.page_label = label("", 12)
        self.page_spin = QSpinBox()
        self.page_spin.setFixedWidth(70)
        self.page_spin.setMinimum(1)
        self.page_spin.valueChanged.connect(self._on_page_spin_changed)
        pager.addStretch(1)
        pager.addWidget(self.page_first)
        pager.addWidget(self.page_prev)
        pager.addWidget(self.page_label)
        pager.addWidget(self.page_next)
        pager.addWidget(self.page_last)
        pager.addSpacing(12)
        pager.addWidget(self.page_spin)
        self.page_bar = self._wrap_layout(pager)
        root.addWidget(self.page_bar)

        # Manual URL install (kept at bottom)
        url_card = card()
        url_layout = QHBoxLayout(url_card)
        url_layout.setContentsMargins(18, 12, 18, 12)
        self.plugin_url = QLineEdit()
        self.plugin_url.setPlaceholderText("扩展 Git URL，例如 https://github.com/user/node-pack.git")
        install = QPushButton("安装")
        install.clicked.connect(self.install_plugin)
        url_layout.addWidget(label("手动安装 URL", 14, True))
        url_layout.addWidget(self.plugin_url, 1)
        url_layout.addWidget(install)
        root.addWidget(url_card)
        return page

    def _on_search_text_changed(self) -> None:
        """Debounce search to avoid rebuilding the table on every keystroke."""
        if self._search_timer is not None:
            self._search_timer.stop()
        from PySide6.QtCore import QTimer
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.filter_extensions)
        self._search_timer.start(300)

    def filter_extensions(self) -> None:
        query = self.extension_search.text()
        filtered = search_entries(self.all_extensions, query)
        self._populate_extension_list(filtered)

    def _wrap_layout(self, layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _populate_extension_list(self, entries: list[ExtensionEntry]) -> None:
        self._filtered_extensions = entries
        self._current_page = 0
        self._render_install_page()

    def _render_install_page(self) -> None:
        total = len(self._filtered_extensions)
        size = self._page_size
        last_page = max(0, (total - 1) // size) if total else 0
        if self._current_page < 0:
            self._current_page = 0
        if self._current_page > last_page:
            self._current_page = last_page
        start = self._current_page * size
        shown = self._filtered_extensions[start:start + size]

        self.extension_install_list.setUpdatesEnabled(False)
        self.extension_install_list.setRowCount(0)
        self.extension_install_list.setRowCount(len(shown))
        for row, entry in enumerate(shown):
            self.extension_install_list.setItem(row, 0, QTableWidgetItem(entry.title))
            self.extension_install_list.setItem(row, 1, QTableWidgetItem(entry.description or entry.repository_url))
            author_category = " / ".join(part for part in (entry.author, entry.category) if part)
            self.extension_install_list.setItem(row, 2, QTableWidgetItem(author_category or "-"))
            self.extension_install_list.setItem(row, 3, QTableWidgetItem("已安装" if entry.installed else "未安装"))
            button_text = "已安装" if entry.installed else "安装"
            button = QPushButton(button_text)
            button.setEnabled(not entry.installed)
            button.setFixedHeight(30)
            if not entry.installed:
                button.clicked.connect(lambda _=False, url=entry.repository_url: self._install_from_url(url))
            self.extension_install_list.setCellWidget(row, 4, button)
        self.extension_install_list.setUpdatesEnabled(True)

        page_count = last_page + 1
        page_idx = self._current_page + 1
        self.page_label.setText(f"{page_idx} / {page_count} 页")
        self.count_label_total = total
        if total:
            self.extension_count_label.setText(
                f"共 {total} 个扩展，当前第 {page_idx}/{page_count} 页（第 {start + 1}-{start + len(shown)} 项）"
            )
        else:
            self.extension_count_label.setText("未找到扩展")
        has_pages = page_count > 1
        for w in (self.page_first, self.page_prev, self.page_next, self.page_last, self.page_spin):
            w.setEnabled(has_pages)
        if has_pages:
            self.page_spin.blockSignals(True)
            self.page_spin.setRange(1, page_count)
            self.page_spin.setValue(page_idx)
            self.page_spin.blockSignals(False)
        self.page_first.setEnabled(has_pages and self._current_page > 0)
        self.page_prev.setEnabled(has_pages and self._current_page > 0)
        self.page_next.setEnabled(has_pages and self._current_page < last_page)
        self.page_last.setEnabled(has_pages and self._current_page < last_page)

    def go_to_page(self, page: int) -> None:
        if page == -1:
            total = len(self._filtered_extensions)
            size = self._page_size
            page = max(0, (total - 1) // size) if total else 0
        self._current_page = page
        self._render_install_page()

    def _on_page_spin_changed(self, value: int) -> None:
        self._current_page = value - 1
        self._render_install_page()

    def _install_from_url(self, url: str) -> None:
        self.plugin_url.setText(url)
        self.install_plugin()

    def refresh(self) -> None:
        if not hasattr(self, "_version_tabs_index"):
            self._version_tabs_index = 0
        self.refresh_current_tab()

    def refresh_current_tab(self) -> None:
        """Only refresh the currently visible sub-tab, not all three."""
        idx = getattr(self, "_version_tabs_index", 0)
        if idx == 0:
            self.refresh_core()
        elif idx == 1:
            self.refresh_extensions()
        elif idx == 2:
            self.refresh_install_tab()

    def refresh_install_tab(self) -> None:
        comfy = self.window.comfy_dir()
        custom_nodes = comfy / "custom_nodes"
        manager = custom_nodes / "ComfyUI-Manager"
        self.manager_label.setText("ComfyUI-Manager：已安装" if manager.exists() else "ComfyUI-Manager：未安装")
        # Already loaded: just re-mark installed state and refresh the table (fast).
        if self.all_extensions:
            self.all_extensions = mark_installed(self.all_extensions, custom_nodes)
            self.filter_extensions()
            return
        # Avoid stacking concurrent loads.
        if self._install_loading:
            return
        self._install_loading = True
        self._populate_extension_list([])
        self.extension_count_label.setText("正在加载扩展列表…")
        import threading
        comfy_path = str(comfy)
        extension_index_url = self.window.config.network.extension_index_url.strip()
        if not extension_index_url:
            try:
                extension_index_url = _launcher_catalog(self.window.config).extension_index_url("comfyui")
            except Exception:
                extension_index_url = ""

        def worker() -> None:
            try:
                entries = get_extensions(Path(comfy_path), registry_url=extension_index_url or None)
            except Exception:
                entries = []
            # marshal back to the GUI thread via a queued signal
            self.extensions_loaded.emit(entries)

        threading.Thread(target=worker, daemon=True).start()

    def _on_extensions_loaded(self, entries: list) -> None:
        self._install_loading = False
        self.all_extensions = entries
        custom_nodes = self.window.comfy_dir() / "custom_nodes"
        self.all_extensions = mark_installed(self.all_extensions, custom_nodes)
        self.filter_extensions()

    def reload_extension_list(self) -> None:
        """Force reload the extension list from disk."""
        self.all_extensions = []
        self._loaded_tabs = set()
        self.refresh_current_tab()

    def on_channel_change(self) -> None:
        self.refresh_core()

    def _catalog_version_entries(self) -> list[dict[str, object]]:
        try:
            catalog = _launcher_catalog(self.window.config)
            values = catalog.version_entries("comfyui", self.is_stable_channel())
            # ``{from: tags}`` is an instruction to query Git, not a version
            # row.  Ignore it when rendering an offline fallback table.
            rows = [item for item in values if item.get("commit") or item.get("hash") or item.get("revision")]
            if not rows and not self.is_stable_channel():
                rows = [
                    {
                        "revision": item.get("branch", ""),
                        "name": item.get("name", item.get("branch", "")),
                        "description": item.get("remote", ""),
                    }
                    for item in catalog.version_entries("comfyui", False)
                    if item.get("internal_id") in {None, "comfyui"} and item.get("branch")
                ]
            return rows
        except Exception:
            return []

    def _populate_catalog_versions(self, entries: list[dict[str, object]], current: str = "") -> None:
        self.commit_table.setUpdatesEnabled(False)
        self.commit_table.setRowCount(len(entries))
        for row, item in enumerate(entries):
            revision = str(item.get("commit") or item.get("hash") or item.get("revision") or "")
            short = str(item.get("short_hash") or revision[:8] or item.get("name") or "-")
            subject = str(item.get("description") or item.get("message") or item.get("subject") or item.get("name") or "-")
            date = str(item.get("date") or item.get("updated_at") or item.get("created_at") or "-")
            is_current = bool(current and revision and current.startswith(revision))
            self.commit_table.setItem(row, 0, QTableWidgetItem(short))
            self.commit_table.setItem(row, 1, QTableWidgetItem(subject))
            self.commit_table.setItem(row, 2, QTableWidgetItem(date))
            self.commit_table.setItem(row, 3, QTableWidgetItem("是" if is_current else ""))
            button = QPushButton("当前" if is_current else "切换")
            button.setFixedSize(72, 30)
            button.setEnabled(bool(revision) and not is_current and (self.window.comfy_dir() / ".git").exists())
            if revision:
                button.clicked.connect(lambda _=False, rev=revision: self.checkout_revision(rev))
            self.commit_table.setCellWidget(row, 4, self._commit_action_cell(button))
        self.commit_table.setUpdatesEnabled(True)

    def refresh_core(self) -> None:
        comfy = self.window.comfy_dir()
        self.commit_table.setRowCount(0)
        # expert mode controls branch row visibility
        self.branch_row_widget.setVisible(self.window.config.expert_mode)
        if not self.branch_combo.isEnabled():
            self.branch_combo.setEnabled(True)
        if not (comfy / ".git").exists():
            self.remote_label.setText("远程地址：未检测到本地 Git（显示离线清单）")
            self.branch_label.setText("当前分支：-")
            self.commit_label.setText("当前版本：-")
            self.branch_combo.clear()
            if self.window.config.expert_mode:
                try:
                    for item in _launcher_catalog(self.window.config).version_entries("comfyui", False):
                        branch = str(item.get("branch") or "")
                        if branch and self.branch_combo.findText(branch) < 0:
                            self.branch_combo.addItem(branch)
                except Exception:
                    pass
            entries = self._catalog_version_entries()
            self._populate_catalog_versions(entries)
            return
        try:
            git = GitService(comfy)
            self.remote_label.setText(f"远程地址：{git.remote_url()}")
            current_branch = git.current_branch()
            self.branch_label.setText(f"当前分支：{current_branch or '-'}")
            current = git.current_commit()
            self.commit_label.setText(
                f"当前版本：{current[:8]}" if current else "当前版本：尚未有任何提交（空仓库）"
            )
            if self.window.config.expert_mode:
                self.branch_combo.clear()
                for branch_name in git.branches():
                    self.branch_combo.addItem(branch_name)
                index = self.branch_combo.findText(current_branch)
                if index >= 0:
                    self.branch_combo.setCurrentIndex(index)
            else:
                self.branch_combo.clear()
            # stable channel→tags; dev channel→all commits
            items = git.tags() if self.is_stable_channel() else git.commits()
            if not items:
                self._populate_catalog_versions(self._catalog_version_entries(), current)
                return
            self.commit_table.setUpdatesEnabled(False)
            self.commit_table.setRowCount(len(items))
            for row, item in enumerate(items):
                self.commit_table.setItem(row, 0, QTableWidgetItem(item.short_hash))
                self.commit_table.setItem(row, 1, QTableWidgetItem(item.subject))
                self.commit_table.setItem(row, 2, QTableWidgetItem(item.date))
                self.commit_table.setItem(row, 3, QTableWidgetItem("是" if item.current else ""))
                button = QPushButton("当前" if item.current else "切换")
                button.setFixedSize(72, 30)
                button.setEnabled(not item.current)
                button.clicked.connect(lambda _=False, rev=item.full_hash: self.checkout_revision(rev))
                self.commit_table.setCellWidget(row, 4, self._commit_action_cell(button))
            self.commit_table.setUpdatesEnabled(True)
        except GitError as exc:
            self.remote_label.setText(f"远程地址：读取失败：{exc}")
            self.branch_label.setText("当前分支：读取失败")
            self.commit_label.setText("当前版本：读取失败")

    def _commit_action_cell(self, button: QPushButton) -> QWidget:
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        layout.addStretch(1)
        layout.addWidget(button, 0, Qt.AlignCenter)
        layout.addStretch(1)
        return cell

    def refresh_extensions(self) -> None:
        custom_nodes = self.window.comfy_dir() / "custom_nodes"
        manager = custom_nodes / "ComfyUI-Manager"
        if hasattr(self, "manager_label"):
            self.manager_label.setText("ComfyUI-Manager：已安装" if manager.exists() else "ComfyUI-Manager：未安装")
        if not custom_nodes.exists():
            self.extension_table.setRowCount(0)
            self._installed_extension_rows = []
            return
        dirs = sorted((p for p in custom_nodes.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
        query = self.installed_extension_search.text().strip().lower() if hasattr(self, "installed_extension_search") else ""
        rows: list[tuple[Path, str, str, str, str, str, bool]] = []
        for child in dirs:
            remote = "-"
            branch = "-"
            commit = "-"
            status = "非 Git 扩展"
            is_git = (child / ".git").exists()
            if is_git:
                try:
                    git = GitService(child)
                    remote = git.remote_url()
                    branch = git.current_branch() or "(detached)"
                    commit = git.current_commit()[:8] or "-"
                    dirty = "有本地修改" if git.is_dirty(include_custom_nodes=True) else "干净"
                    date = git.current_commit_date()
                    status = f"{date} · {dirty}" if date else dirty
                except GitError as exc:
                    status = f"读取失败：{exc}"
            haystack = " ".join((child.name, remote, branch, commit, status, str(child))).lower()
            if query and query not in haystack:
                continue
            rows.append((child, remote, branch, commit, status, str(child), is_git))
        self._installed_extension_rows = [row[0] for row in rows]
        self.extension_table.setUpdatesEnabled(False)
        self.extension_table.setRowCount(len(rows))
        for row, (child, remote, branch, commit, status, _path, is_git) in enumerate(rows):
            name_item = QTableWidgetItem(child.name)
            name_item.setData(Qt.UserRole, str(child))
            self.extension_table.setItem(row, 0, name_item)
            self.extension_table.setItem(row, 1, QTableWidgetItem(remote))
            self.extension_table.setItem(row, 2, QTableWidgetItem(branch))
            self.extension_table.setItem(row, 3, QTableWidgetItem(commit))
            self.extension_table.setItem(row, 4, QTableWidgetItem(status))

            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 4, 0, 4)
            action_layout.setSpacing(6)
            update_btn = QPushButton("更新")
            update_btn.setFixedHeight(30)
            update_btn.setEnabled(is_git)
            update_btn.clicked.connect(lambda _=False, p=child: self.update_extension_path(p))
            open_btn = QPushButton("打开")
            open_btn.setFixedHeight(30)
            open_btn.clicked.connect(lambda _=False, p=child: self.open_extension_path(p))
            uninstall_btn = QPushButton("卸载")
            uninstall_btn.setObjectName("danger")
            uninstall_btn.setFixedHeight(30)
            uninstall_btn.clicked.connect(lambda _=False, p=child: self._uninstall_extension(p))
            toggle_btn = QPushButton("禁用" if not child.name.endswith(".disabled") else "启用")
            toggle_btn.setFixedHeight(30)
            toggle_btn.clicked.connect(lambda _=False, p=child: self.toggle_extension(p))
            branch_btn = QPushButton("分支")
            branch_btn.setFixedHeight(30)
            branch_btn.setEnabled(is_git)
            # Branch switching is an expert-mode affordance, matching the
            # core version tab where the branch row is hidden for beginners.
            # Keep the action wired for integrations, but do not expose it in
            # the default (beginner) workflow.
            branch_btn.setVisible(bool(self.window.config.expert_mode))
            branch_btn.clicked.connect(lambda _=False, p=child: self.switch_extension_branch(p))
            action_layout.insertWidget(0, toggle_btn)
            action_layout.insertWidget(1, branch_btn)
            action_layout.addWidget(update_btn)
            action_layout.addWidget(open_btn)
            action_layout.addWidget(uninstall_btn)
            self.extension_table.setCellWidget(row, 5, actions)
        self.extension_table.setUpdatesEnabled(True)

    def toggle_extension(self, path: Path) -> None:
        """Enable/disable a node pack by a reversible ``.disabled`` rename."""
        path = self._safe_extension_path(path)
        if path is None or not path.exists():
            return
        target = path.with_name(path.name[:-9] if path.name.endswith(".disabled") else path.name + ".disabled")
        try:
            path.rename(target)
            self.refresh_extensions()
        except OSError as exc:
            QMessageBox.warning(self, "扩展状态修改失败", str(exc))

    def switch_extension_branch(self, path: Path) -> None:
        if not (path / ".git").exists():
            return
        try:
            branches = GitService(path).branches()
        except GitError as exc:
            QMessageBox.warning(self, "读取分支失败", str(exc))
            return
        branch, ok = QInputDialog.getItem(self, "切换扩展分支", path.name, branches, 0, False)
        if ok and branch:
            commands = self.window.build_repo_checkout_commands(path, path.name, branch, include_custom_nodes=True)
            if commands:
                self.window.run_commands(commands, f"切换扩展分支：{path.name}")

    def uninstall_extension(self, path: Path) -> None:
        self._uninstall_extension(path)

    def _safe_extension_path(self, path: Path | str) -> Path | None:
        """Validate a plugin path before any rename, Git action, or delete.

        The table normally supplies children of ``custom_nodes``.  Keeping
        the check at the page boundary also protects compatibility callers
        that invoke ``toggle_extension``/``uninstall_extension`` directly;
        a malformed catalog or stale selection must never turn an uninstall
        into a recursive delete outside the ComfyUI tree.
        """
        try:
            root = (self.window.comfy_dir() / "custom_nodes").expanduser().resolve()
            candidate = Path(path).expanduser().resolve(strict=False)
            if candidate == root or candidate.parent != root:
                return None
            # Do not follow a symlink supplied as the selected extension.  A
            # real directory may contain symlinks internally, but the top
            # level entry itself must be an ordinary directory.
            raw = Path(path).expanduser()
            if raw.is_symlink() or not candidate.is_dir():
                return None
            return candidate
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def fetch_core(self) -> None:
        if not self.window.can_start_task():
            return
        self.window.run_commands(
            [
                self.window.git_command_spec(
                    self.window.comfy_dir(),
                    ["fetch", "--all", "--tags", "--prune", "--force"],
                    "拉取远端信息",
                )
            ],
            "拉取远端信息",
        )

    def update_core(self) -> None:
        if not self.window.can_start_task():
            return
        comfy = self.window.comfy_dir()
        if not (comfy / ".git").exists():
            QMessageBox.warning(self, "无法更新", "当前 ComfyUI 目录不是 Git 仓库。")
            return
        commands = self.window.build_repo_update_commands(
            comfy,
            "ComfyUI",
            include_custom_nodes=False,
        )
        if commands is None:
            return
        self.window.run_commands(commands, "更新 ComfyUI")

    def update_all(self) -> None:
        if not self.window.can_start_task():
            return
        comfy = self.window.comfy_dir()
        commands: list[CommandSpec] = []
        if (comfy / ".git").exists():
            core_commands = self.window.build_repo_update_commands(
                comfy,
                "ComfyUI",
                include_custom_nodes=False,
            )
            if core_commands is None:
                return
            commands.extend(core_commands)
        else:
            self.window.append_log("跳过 ComfyUI：当前目录不是 Git 仓库。\n")

        custom_nodes = comfy / "custom_nodes"
        if custom_nodes.exists():
            for child in sorted((p for p in custom_nodes.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
                if not (child / ".git").exists():
                    self.window.append_log(f"跳过非 Git 扩展：{child.name}\n")
                    continue
                plugin_commands = self.window.build_repo_update_commands(
                    child,
                    child.name,
                    include_custom_nodes=True,
                )
                if plugin_commands is None:
                    return
                commands.extend(plugin_commands)
        else:
            self.window.append_log("跳过扩展：custom_nodes 目录不存在。\n")

        if not commands:
            QMessageBox.information(self, "无需更新", "没有找到可更新的 Git 仓库。")
            return
        self.window.run_commands(
            commands,
            "一键更新内核与插件",
        )

    def checkout_branch(self) -> None:
        branch = self.branch_combo.currentText()
        if branch:
            self.checkout_revision(branch)

    def checkout_revision(self, revision: str) -> None:
        if not self.window.can_start_task():
            return
        commands = self.window.build_repo_checkout_commands(
            self.window.comfy_dir(),
            "ComfyUI",
            revision,
            include_custom_nodes=False,
        )
        if commands is not None:
            self.window.run_commands(commands, f"切换版本：{revision}")

    def selected_extension_path(self) -> Path | None:
        rows = self.extension_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "未选择", "请先选择一个扩展。")
            return None
        item = self.extension_table.item(rows[0].row(), 0)
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return Path(value) if value else None

    def update_selected_extension(self) -> None:
        path = self.selected_extension_path()
        if path:
            self.update_extension_path(path)

    def update_extension_path(self, path: Path) -> None:
        if not self.window.can_start_task():
            return
        if not path:
            return
        if not (path / ".git").exists():
            QMessageBox.warning(self, "无法更新", "选中的扩展不是 Git 仓库。")
            return
        commands = self.window.build_repo_update_commands(
            path,
            path.name,
            include_custom_nodes=True,
        )
        if commands is None:
            return
        self.window.run_commands(commands, f"更新扩展 {path.name}")

    def open_selected_extension(self) -> None:
        path = self.selected_extension_path()
        if path:
            self.open_extension_path(path)

    def open_extension_path(self, path: Path) -> None:
        if not open_path(path):
            InternalFileBrowser(path, self).exec()

    def _uninstall_extension(self, path: Path) -> None:
        path = self._safe_extension_path(path)
        if path is None:
            QMessageBox.warning(self, "卸载失败", "扩展路径不在当前 ComfyUI/custom_nodes 目录内。")
            return
        name = path.name
        reply = QMessageBox.question(
            self, "卸载确认",
            f"确认删除扩展 {name}？\n这会从磁盘移除整个目录，操作不可恢复。",
        )
        if reply != QMessageBox.Yes:
            return
        import shutil
        try:
            shutil.rmtree(path)
        except OSError as exc:
            QMessageBox.warning(self, "卸载失败", f"删除目录失败：{exc}")
            return
        self.window.append_log(f"已卸载扩展：{name}\n")
        self.refresh_extensions()

    def install_plugin(self) -> None:
        url = self.plugin_url.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少 URL", "请输入扩展 Git URL。")
            return
        ensure_dir(self.window.comfy_dir() / "custom_nodes")
        try:
            command = install_plugin_command(self.window.comfy_dir(), url, self.window.config)
        except (TypeError, ValueError) as exc:
            # The service validates schemes and the destination repository
            # name.  Surface malformed user input in the dialog instead of
            # letting an exception escape a Qt signal handler.
            QMessageBox.warning(self, "扩展 URL 无效", str(exc))
            return
        self.window.run_commands([command], "安装扩展")


class TroubleshootPage(QWidget):
    """Safe, local diagnostics matching the launcher’s 排障 page.

    Scanning never changes files.  Rows returned by the service may be either
    dataclasses or dictionaries so older plug-ins and hidden integrations stay
    compatible with the UI.
    """

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        root = QVBoxLayout(self)
        # Keep the diagnostics toolbar aligned with the other target pages:
        # the reference uses a full-width 88px gray band immediately below
        # the title bar, with the table/content inset inside that band.
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("pageHeader")
        header.setFixedHeight(88)
        h = QHBoxLayout(header)
        h.setContentsMargins(24, 12, 24, 12)
        h.addWidget(label("疑难解答", 22, True))
        h.addStretch(1)
        scan = QPushButton("扫描")
        scan.setObjectName("primary")
        scan.clicked.connect(self.scan)
        h.addWidget(scan)
        root.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 24, 24, 24)
        body_layout.setSpacing(14)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("diagnosticTable")
        self.table.setHorizontalHeaderLabels(["异常名", "描述", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        body_layout.addWidget(self.table, 1)
        self.empty_label = label("点击“扫描”检查当前 ComfyUI 环境。", 13)
        self.empty_label.setObjectName("muted")
        body_layout.addWidget(self.empty_label)
        root.addWidget(body, 1)

    @staticmethod
    def _value(item, *names, default=""):
        if isinstance(item, dict):
            for name in names:
                if name in item:
                    return item[name]
            return default
        for name in names:
            if hasattr(item, name):
                return getattr(item, name)
        return default

    def scan(self) -> None:
        try:
            from aura_rift.services.troubleshooter import Troubleshooter
            service = Troubleshooter(self.window.comfy_dir(), self.window.config)
            result = service.scan()
        except Exception as exc:
            result = [{"name": "扫描失败", "description": str(exc), "fixable": False}]
        if isinstance(result, dict):
            result = result.get("issues", result.get("findings", []))
        rows = list(result or [])
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            name = str(self._value(item, "name", "title", "key", default="未知问题"))
            description = str(self._value(item, "description", "detail", "message", default=""))
            fixable = bool(self._value(item, "fixable", "can_fix", default=False))
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(description))
            if fixable:
                button = QPushButton("修复")
                button.clicked.connect(lambda _=False, issue=item: self.fix(issue))
                self.table.setCellWidget(row, 2, button)
            else:
                self.table.setItem(row, 2, QTableWidgetItem("无需操作"))
        self.empty_label.setText("扫描完成：未发现异常。" if not rows else f"扫描完成：发现 {len(rows)} 项。")

    def fix(self, issue) -> None:
        try:
            code = str(self._value(issue, "code", default=""))
            if code == "missing_dependencies":
                # Dependency installation is the one repair that must use the
                # existing TaskHandle pipeline rather than blocking the GUI.
                raw_path = self._value(issue, "path", default="")
                requirements = Path(str(raw_path)).expanduser().resolve()
                root = self.window.comfy_dir().resolve()
                try:
                    requirements.relative_to(root)
                except ValueError as exc:
                    raise ValueError("依赖清单路径不在 ComfyUI 目录内") from exc
                if not requirements.is_file():
                    raise FileNotFoundError(f"未找到依赖清单：{requirements}")
                python = environment.resolve_python(
                    root,
                    self.window.config.python_path_override,
                    self.window.config.venv_manager,
                )
                if not self.window._dedicated_python_available(python):
                    self.window.append_log(
                        "未检测到 ComfyUI 专属 Python 环境，已阻止排障依赖安装；"
                        "请先在环境维护中创建虚拟环境。\n"
                    )
                    return
                self.window.run_commands(
                    install_missing_deps_commands(
                        root, [requirements], self.window.config
                    ),
                    "排障：安装缺失依赖",
                )
                return
            from aura_rift.services.troubleshooter import Troubleshooter
            service = Troubleshooter(self.window.comfy_dir(), self.window.config)
            fixer = getattr(service, "fix", None) or getattr(service, "repair", None)
            if callable(fixer):
                result = fixer(issue)
                self.window.append_log(f"排障修复：{result}\n")
                self.scan()
                return
        except Exception as exc:
            QMessageBox.warning(self, "修复失败", str(exc))
            return
        QMessageBox.information(self, "暂不可修复", "该异常没有可用的安全修复动作。")

    def refresh(self) -> None:
        # Do not run potentially slow checks merely by navigating to the page.
        pass


class PatchPage(QWidget):
    """Linux-safe hotfix inventory and apply/revert controls."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.items: list = []
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        root.addWidget(WarningBar("补丁只会处理清单中带 Linux 载荷且通过校验的文件；应用前会自动备份，失败不会继续启动。"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["补丁", "说明", "适用版本", "状态", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.table, 1)
        self.refresh_button = QPushButton("刷新补丁清单")
        self.refresh_button.clicked.connect(self.refresh)
        root.addWidget(self.refresh_button, 0, Qt.AlignRight)
        self.refresh()

    @staticmethod
    def _value(item, *names, default=""):
        if isinstance(item, dict):
            for name in names:
                if name in item:
                    return item[name]
            return default
        for name in names:
            if hasattr(item, name):
                return getattr(item, name)
        return default

    def _manager(self):
        from aura_rift.services.hotfixes import HotfixManager
        try:
            # Keep the inventory page in sync with startup: custom catalog and
            # backup-directory settings must affect both listing and apply.
            return HotfixManager(self.window.comfy_dir(), config=self.window.config)
        except TypeError:
            # Compatibility for third-party managers that still expose the
            # older positional constructor.
            try:
                return HotfixManager(self.window.comfy_dir(), self.window.config)
            except TypeError:
                return HotfixManager(self.window.comfy_dir())

    def refresh(self) -> None:
        try:
            manager = self._manager()
            listed = manager.list() if callable(getattr(manager, "list", None)) else manager.status()
            self.items = list(listed or [])
        except Exception as exc:
            self.items = [{"name": "无法读取清单", "description": str(exc), "available": False}]
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            name = str(self._value(item, "name", "title", default="未命名补丁"))
            desc = str(self._value(item, "description", "detail", default=""))
            version = str(self._value(item, "version", "versions", "applicable", default="全部"))
            available = bool(self._value(item, "available", "supported", default=True))
            applied = bool(self._value(item, "applied", "active", default=False))
            catalog_enabled = bool(self._value(item, "catalog_enabled", "enabled", default=True))
            user_enabled = bool(self._value(item, "user_enabled", "enabled", default=True))
            reason = str(self._value(item, "reason", "unavailable_reason", default=""))
            stale = bool(self._value(item, "stale", default=False))
            if not catalog_enabled:
                state = "清单已禁用"
            elif stale:
                state = reason or "目标文件已被修改，请先检查备份"
            else:
                state = ("已应用" if applied else "可应用") if available else (reason or "Linux 不可用")
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(desc))
            self.table.setItem(row, 2, QTableWidgetItem(version))
            self.table.setItem(row, 3, QTableWidgetItem(state))
            if stale:
                self.table.setItem(row, 4, QTableWidgetItem("需人工确认"))
            elif applied:
                # Reverting is always safe and must remain reachable even if
                # the user later disables a patch or the catalog marks it
                # unavailable.  Enabling is independent from undoing the
                # already-backed-up change.
                actions = QWidget()
                action_layout = QHBoxLayout(actions)
                action_layout.setContentsMargins(2, 2, 2, 2)
                button = QPushButton("撤销")
                button.clicked.connect(lambda _=False, patch=item: self.apply(patch, True))
                action_layout.addWidget(button)
                if not user_enabled and catalog_enabled:
                    enable = QPushButton("启用")
                    enable.clicked.connect(lambda _=False, patch=item: self.toggle_enabled(patch, True))
                    action_layout.addWidget(enable)
                elif catalog_enabled:
                    disable = QPushButton("禁用")
                    disable.clicked.connect(lambda _=False, patch=item: self.toggle_enabled(patch, False))
                    action_layout.addWidget(disable)
                self.table.setCellWidget(row, 4, actions)
            elif not user_enabled and catalog_enabled:
                button = QPushButton("启用")
                button.clicked.connect(lambda _=False, patch=item: self.toggle_enabled(patch, True))
                self.table.setCellWidget(row, 4, button)
            elif available and catalog_enabled:
                actions = QWidget()
                action_layout = QHBoxLayout(actions)
                action_layout.setContentsMargins(2, 2, 2, 2)
                button = QPushButton("应用")
                button.clicked.connect(lambda _=False, patch=item: self.apply(patch, False))
                action_layout.addWidget(button)
                disable = QPushButton("禁用")
                disable.clicked.connect(lambda _=False, patch=item: self.toggle_enabled(patch, False))
                action_layout.addWidget(disable)
                self.table.setCellWidget(row, 4, actions)
            else:
                self.table.setItem(row, 4, QTableWidgetItem("不可用"))

    def toggle_enabled(self, patch, enabled: bool) -> None:
        try:
            manager = self._manager()
            setter = getattr(manager, "set_enabled", None)
            if not callable(setter):
                raise RuntimeError("当前补丁服务不支持逐项启用状态")
            setter(self._value(patch, "name", "title", default=""), enabled)
            self.window.save_config()
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "保存补丁状态失败", str(exc))

    def apply(self, patch, undo: bool = False) -> None:
        manager = self._manager()
        try:
            name = self._value(patch, "name", "title", default="")
            if undo:
                result = manager.revert(name)
            else:
                result = manager.apply(name)
            self.window.append_log(f"补丁 {'撤销' if undo else '应用'}：{result}\n")
            self.refresh()
        except Exception as exc:
            self.window.append_log(f"补丁操作失败：{exc}\n")
            QMessageBox.warning(self, "补丁操作失败", str(exc))



class ToolsPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        root = QVBoxLayout(self)
        # The tools reference uses a deeper top inset beneath the title bar;
        # this keeps the first card at the same y-position as 绘世 while the
        # scroll area still handles the long catalog on compact windows.
        root.setContentsMargins(28, 46, 28, 24)
        root.setSpacing(37)
        root.addWidget(label("小工具", 22, True))
        self.link_area = QWidget()
        self.link_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.link_grid = QGridLayout(self.link_area)
        # Match the reference two-column rhythm: cards are inset from the
        # scroll viewport and separated by a visible central gutter.
        self.link_grid.setContentsMargins(24, 0, 24, 0)
        self.link_grid.setHorizontalSpacing(36)
        self.link_grid.setVerticalSpacing(14)
        self.link_scroll = QScrollArea()
        self.link_scroll.setObjectName("toolsScroll")
        self.link_scroll.setWidgetResizable(True)
        self.link_scroll.setFrameShape(QFrame.NoFrame)
        self.link_scroll.setWidget(self.link_area)
        root.addWidget(self.link_scroll, 1)

        hardware_card = card()
        hardware_layout = QVBoxLayout(hardware_card)
        hardware_layout.setContentsMargins(18, 14, 18, 14)
        hardware_layout.addWidget(label("硬件状态", 16, True))
        self.hardware = QTextBrowser()
        self.hardware.setMinimumHeight(130)
        hardware_layout.addWidget(self.hardware)
        root.addWidget(hardware_card, 0)
        self._populate_links()
        self.hardware.setMarkdown("进入本页后检测硬件与目录状态。")

    def _populate_links(self) -> None:
        while self.link_grid.count():
            item = self.link_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # ``deleteLater`` alone leaves the old card visible until the
                # next event-loop turn.  ToolsPage.refresh() runs during
                # navigation, so successive visits used to paint duplicate
                # rows (and retain the previous window's width).  Detach and
                # hide first, then defer destruction safely.
                widget.setParent(None)
                widget.hide()
                widget.deleteLater()
            elif item.layout() is not None:
                child_layout = item.layout()
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget() is not None:
                        child.widget().setParent(None)
                        child.widget().deleteLater()
        groups: dict[str, list] = {}
        try:
            catalog = _launcher_catalog(self.window.config)
            raw = getattr(catalog, "utilities", None)
            if callable(raw):
                raw = raw()
            if isinstance(raw, dict):
                groups = raw
            elif isinstance(raw, list):
                # 绘世 data.json stores utilities as
                # [{name: ..., links: [{name, url}, ...]}, ...].
                for group in raw:
                    if isinstance(group, dict):
                        group_name = str(group.get("name") or group.get("title") or "小工具")
                        entries = group.get("links", group.get("items", []))
                        if isinstance(entries, list):
                            groups[group_name] = entries
                if not groups:
                    groups = {"小工具": list(raw)}
        except Exception:
            groups = {}
        if not groups:
            groups = {
                "小工具": [
                    {"title": "ComfyUI Web", "description": "在默认浏览器打开 ComfyUI", "action": "web"},
                    {"title": "ComfyUI 根目录", "description": "打开项目文件夹", "action": "root"},
                    {"title": "模型目录", "description": "打开 models", "action": "models"},
                    {"title": "输出目录", "description": "打开 output", "action": "output"},
                    {"title": "自定义节点", "description": "打开 custom_nodes", "action": "custom_nodes"},
                    {"title": "启动命令", "description": "查看当前 CLI 参数", "action": "command"},
                ],
                "开源项目": [],
            }
        row = 0
        card_rows = 0
        heading_rows = 0
        group_gaps = 0
        for group_name, entries in groups.items():
            if not isinstance(entries, (list, tuple)):
                continue
            if not entries:
                continue
            # The page already has a top-level “小工具” heading.  The
            # catalog uses the same name for its first group, so avoid a
            # duplicate heading while retaining headings for every other
            # category (UP 主、开源项目、模型站、图站…).
            if str(group_name).strip() != "小工具" or row:
                if row:
                    # Separate catalog groups with the same open breathing
                    # band visible in the reference page (about 48px from a
                    # card's bottom edge to the next group heading).
                    self.link_grid.setRowMinimumHeight(row, 34)
                    row += 1
                    group_gaps += 1
                title = label(str(group_name), 16, True)
                self.link_grid.addWidget(title, row, 0, 1, 2)
                row += 1
                heading_rows += 1
            for index, entry in enumerate(entries):
                if isinstance(entry, str):
                    entry = {"title": entry, "url": entry}
                # Catalog data is downloaded/overridden independently from
                # the launcher.  Ignore malformed list members rather than
                # letting an integer/null entry abort the whole tools page.
                if not isinstance(entry, Mapping):
                    continue
                title_text = str(entry.get("title") or entry.get("name") or "链接")
                description = str(entry.get("description") or entry.get("desc") or "")
                url = str(entry.get("url") or entry.get("link") or "")
                action = str(entry.get("action") or "")
                callback = lambda action=action, url=url: self._open_utility(action, url)
                widget = LinkCard(title_text, description, url, callback)
                # The reference cards reserve a two-line text block plus the
                # link glyph/chevron.  A 76px minimum compresses QLabel's
                # natural 100px hint and makes adjacent rows paint over one
                # another when the catalog contains long URLs.
                widget.setMinimumHeight(102)
                self.link_grid.addWidget(widget, row + index // 2, index % 2)
            group_rows = (len(entries) + 1) // 2
            row += group_rows
            card_rows += group_rows
        # Let QScrollArea grow the catalog canvas to the rows' natural height.
        # Without an explicit minimum, Qt compresses grid rows below the
        # 102px card minimum when many catalog groups are present, causing
        # successive LinkCards to overlap at desktop sizes.
        self.link_grid.setRowStretch(row, 0)
        self.link_grid.activate()
        # Derive a stable canvas height from the row minima.  ``QGridLayout``
        # can report a transient zero sizeHint while old cards are waiting for
        # DeferredDelete during a refresh, so relying on sizeHint alone lets
        # rows collapse and overlap.  This arithmetic remains deterministic
        # across repeated page visits and window sizes.
        row_height = card_rows * 102 + heading_rows * 24 + group_gaps * 34
        row_gaps = max(0, row - 1) * self.link_grid.verticalSpacing()
        self.link_area.setMinimumHeight(max(0, row_height + row_gaps))

    def _open_utility(self, action: str, url: str) -> None:
        actions = {
            "web": self.open_web_ui,
            "root": lambda: self.open_relative("."),
            "models": lambda: self.open_relative("models"),
            "output": lambda: self.open_relative("output"),
            "custom_nodes": lambda: self.open_relative("custom_nodes"),
            "command": self.show_command,
        }
        if action in actions:
            actions[action]()
        elif url:
            QDesktopServices.openUrl(QUrl(url))

    def open_web_ui(self) -> None:
        QDesktopServices.openUrl(QUrl(_web_ui_url(self.window.config)))

    def open_relative(self, relative: str) -> None:
        path = self.window.comfy_dir() if relative == "." else self.window.comfy_dir() / relative
        if relative != ".":
            ensure_dir(path)
        if not open_path(path):
            InternalFileBrowser(path, self).exec()

    def create_common_dirs(self) -> None:
        for name in ("models", "input", "output", "custom_nodes", "user"):
            ensure_dir(self.window.comfy_dir() / name)
        QMessageBox.information(self, "已完成", "常用目录已创建或确认存在。")
        self.refresh_hardware()

    def show_command(self) -> None:
        self.window.advanced_page.show_launch_command()

    def refresh_hardware(self) -> None:
        comfy = self.window.comfy_dir()
        torch = environment.inspect_torch(
            environment.resolve_python(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        )
        gpu_lines = "\n".join(f"- {item}" for item in environment.detect_gpu())
        self.hardware.setMarkdown(
            "### 硬件与目录\n\n"
            f"**ComfyUI：** `{comfy}`\n\n"
            f"**models：** {directory_size_hint(comfy / 'models')}\n\n"
            f"**custom_nodes：** {directory_size_hint(comfy / 'custom_nodes')}\n\n"
            f"**PyTorch：** {torch.torch} / CUDA {torch.cuda}\n\n"
            f"**GPU：**\n{gpu_lines}"
        )

    def refresh(self) -> None:
        # Hardware probes can invoke external tools; defer them until the
        # page is actually opened instead of delaying launcher startup.
        self._populate_links()
        self.refresh_hardware()


class SettingsPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.pages: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.about_browser = QTextBrowser()
        self._build_general()
        self._build_env()
        self._build_proxy()
        self._build_about()
        # Linux edition keeps the compact two-tab settings hierarchy.  The
        # environment/proxy builders remain available for compatibility and
        # their controls are mirrored in the long general page below.
        for hidden_name in ("environment", "proxy"):
            hidden_page = self.pages.get(hidden_name)
            if hidden_page is not None:
                idx = self.tabs.indexOf(hidden_page)
                if idx >= 0:
                    self.tabs.removeTab(idx)

        # Match the reference two-tab toolbar while retaining the QTabWidget
        # object/``tabs`` attribute used by older integrations.
        self.tabs.tabBar().setVisible(False)
        header = QFrame()
        header.setObjectName("pageHeader")
        header.setFixedHeight(88)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        self.settings_nav_tabs = QTabBar(header)
        self.settings_nav_tabs.setObjectName("inlineTabs")
        self.settings_nav_tabs.setExpanding(False)
        self.settings_nav_tabs.setDrawBase(False)
        accent = "#f2f2f2" if self.window.config.theme != "light" else "#333333"
        settings_icons = ("settings", "terminal")
        for index in range(self.tabs.count()):
            self.settings_nav_tabs.addTab(
                make_nav_icon(settings_icons[index] if index < len(settings_icons) else "settings", accent, 18),
                self.tabs.tabText(index),
            )
        self.settings_nav_tabs.currentChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.settings_nav_tabs.setCurrentIndex)
        header_layout.addWidget(self.settings_nav_tabs)
        header_layout.addStretch(1)
        layout.addWidget(header)
        layout.addWidget(self.tabs, 1)

        # Keep legacy save entry points available even though the Linux
        # layout consolidates them into the long “一般设置” card page.
        self.save = self.save_general
        self.save_settings = self.save_general

    def _settings_icon(self, name: str) -> QIcon:
        accent = "#f2f2f2" if self.window.config.theme != "light" else "#333333"
        return make_nav_icon(name, accent, 18)

    def show_sub_page(self, name: str) -> None:
        # The Linux settings surface intentionally exposes only ``general``
        # and ``about``.  Older integrations may still ask to open the
        # removed environment/proxy tabs; route those requests to the
        # consolidated general card instead of silently doing nothing.
        name = {
            "environment": "general",
            "env": "general",
            "proxy": "general",
            "network": "general",
        }.get(name, name)
        page = self.pages.get(name)
        if not page:
            return
        self.tabs.setCurrentWidget(page)
        if name == "about":
            self.about_browser.setMarkdown(self._about_text())

    def _add_settings_tab(self, name: str, page: QWidget, icon_name: str, title: str) -> None:
        self.pages[name] = page
        self.tabs.addTab(page, self._settings_icon(icon_name), title)

    def _on_tab_changed(self, index: int) -> None:
        if index >= 0 and self.tabs.widget(index) is self.pages.get("about"):
            self.about_browser.setMarkdown(self._about_text())

    def _about_text(self) -> str:
        """Resolve about.md with the selected ComfyUI checkout as an override."""
        return bundled_markdown(
            "about.md",
            extra_paths=[Path(self.window.config.comfy_path).expanduser() / "about.md"],
        )

    def _build_general(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("新手", False)
        self.mode_combo.addItem("专家", True)
        self.mode_combo.setCurrentIndex(1 if self.window.config.expert_mode else 0)
        root.addWidget(self.option_row("配置模式", "专家模式会显示分支和提交切换能力", self.mode_combo))

        self.language_combo = QComboBox()
        self.language_combo.addItem("中文（简体）", "zh_CN")
        root.addWidget(self.option_row("界面语言", "首版仅提供中文，已预留后续多语言结构", self.language_combo))

        # Target launcher settings are deliberately long-form cards rather
        # than a Windows-style property dialog.
        self.apply_proxy_env = Toggle("应用到 Git / Pip / 环境变量 / 模型下载")
        self.apply_proxy_env.setChecked(bool(self.window.config.proxy_enabled))
        root.addWidget(self.option_row("代理开关", "统一控制代理是否传递给网络任务", self.apply_proxy_env))
        self.proxy_git_toggle = Toggle("Git")
        self.proxy_git_toggle.setChecked(self.window.config.proxy_for_git)
        root.addWidget(self.option_row("Git 代理", "将 GitHub 加速和 HTTP 代理传递给 Git", self.proxy_git_toggle))
        self.proxy_pip_toggle = Toggle("Pip")
        self.proxy_pip_toggle.setChecked(self.window.config.proxy_for_pip)
        root.addWidget(self.option_row("Pip 代理", "将镜像源和代理传递给 pip / uv", self.proxy_pip_toggle))
        self.proxy_env_toggle = Toggle("环境变量")
        self.proxy_env_toggle.setChecked(self.window.config.proxy_for_environment)
        root.addWidget(self.option_row("环境变量代理", "为 ComfyUI 子进程设置 HTTP(S)_PROXY", self.proxy_env_toggle))
        self.proxy_models_toggle = Toggle("模型下载")
        self.proxy_models_toggle.setChecked(self.window.config.proxy_for_models)
        root.addWidget(self.option_row("模型下载代理", "允许模型站点请求使用代理", self.proxy_models_toggle))
        self.auto_browser = Toggle("启动后自动打开默认浏览器")
        browser_value = self.window.config.default_browser
        self.auto_browser.setChecked(bool(browser_value) and not self.window.config.launch.disable_auto_launch)
        root.addWidget(self.option_row("浏览器", "使用 Linux 桌面环境的默认浏览器", self.auto_browser))
        self.crash_restart = Toggle("ComfyUI 异常退出后自动重启")
        self.crash_restart.setChecked(bool(self.window.config.crash_auto_restart))
        root.addWidget(self.option_row("崩溃处理", "仅在启动器仍处于运行状态时重启", self.crash_restart))
        self.integrity_check = Toggle("依赖完整性检查")
        self.integrity_check.setChecked(bool(self.window.config.dependency_integrity_check))
        root.addWidget(self.option_row("依赖检测", "启动前扫描 requirements 与重复扩展", self.integrity_check))
        self.component_conflict_check = Toggle("组件冲突检测")
        self.component_conflict_check.setChecked(self.window.config.component_conflict_check)
        root.addWidget(self.option_row("组件冲突", "检测 Torch / xFormers / CUDA 组合是否冲突", self.component_conflict_check))
        self.duplicate_extension_check = Toggle("重复插件检测")
        self.duplicate_extension_check.setChecked(self.window.config.duplicate_extension_check)
        root.addWidget(self.option_row("重复插件", "扫描 custom_nodes 中重复仓库和目录", self.duplicate_extension_check))
        self.hotfixes_toggle = Toggle("启动前应用已启用 Linux 补丁")
        self.hotfixes_toggle.setChecked(self.window.config.hotfixes_enabled)
        root.addWidget(self.option_row("热补丁", "仅应用带校验载荷的补丁，不修改不支持的条目", self.hotfixes_toggle))

        self.general_project_path = QLineEdit(self.window.config.comfy_path)
        choose_project = QPushButton("选择")
        choose_project.clicked.connect(
            lambda _checked=False: self.choose_project(self.general_project_path)
        )
        root.addWidget(self.path_row("ComfyUI 路径", "Linux 下路径不存在时可选择空目录进行安装", self.general_project_path, choose_project))
        self.general_python_path = QLineEdit(self.window.config.python_path_override)
        choose_python = QPushButton("选择")
        choose_python.clicked.connect(
            lambda _checked=False: self.choose_python(self.general_python_path)
        )
        root.addWidget(self.path_row("Python 路径覆盖", "留空时自动选择 venv / Poetry / PDM / uv / Conda", self.general_python_path, choose_python))
        self.general_venv_manager_combo = QComboBox()
        for manager in environment.VenvManager:
            self.general_venv_manager_combo.addItem(environment.MANAGER_LABELS[manager], manager.value)
        manager_index = self.general_venv_manager_combo.findData(self.window.config.venv_manager)
        if manager_index >= 0:
            self.general_venv_manager_combo.setCurrentIndex(manager_index)
        root.addWidget(self.option_row("虚拟环境管理器", "支持 venv、Poetry、PDM、uv 和 Conda", self.general_venv_manager_combo))

        self.proxy_http_general = QLineEdit(self.window.config.network.http_proxy)
        self.proxy_http_general.setPlaceholderText("http://127.0.0.1:7890")
        root.addWidget(self.option_row("HTTP 代理", "用于 Git、Pip 和模型下载", self.proxy_http_general))
        self.proxy_https_general = QLineEdit(self.window.config.network.https_proxy)
        root.addWidget(self.option_row("HTTPS 代理", "留空则跟随 HTTP 代理", self.proxy_https_general))
        self.pypi_mirror_general = QComboBox()
        for name, url in PYPI_MIRRORS:
            self.pypi_mirror_general.addItem(name, url)
        try:
            pip_entries = _launcher_catalog(self.window.config).section("mirrors", {}).get("pip_index", [])
            known_urls = {str(self.pypi_mirror_general.itemData(i)) for i in range(self.pypi_mirror_general.count())}
            if isinstance(pip_entries, list):
                for entry in pip_entries:
                    if isinstance(entry, dict):
                        url = str(entry.get("index_url") or "").strip()
                        if url and url not in known_urls:
                            self.pypi_mirror_general.addItem(f"目录镜像 · {url}", url)
                            known_urls.add(url)
        except Exception:
            pass
        idx = self.pypi_mirror_general.findData(self.window.config.network.pypi_mirror)
        self.pypi_mirror_general.setCurrentIndex(idx if idx >= 0 else 0)
        root.addWidget(self.option_row("PyPI 镜像", "用于依赖安装和环境修复", self.pypi_mirror_general))
        catalog_mirrors: dict[str, object] = {}
        catalog_model_server = ""
        try:
            catalog = _launcher_catalog(self.window.config)
            raw_mirrors = catalog.section("mirrors", {})
            if isinstance(raw_mirrors, dict):
                catalog_mirrors = raw_mirrors
            catalog_model_server = str(catalog.get("model_server", "") or "")
        except Exception:
            pass
        # Catalog entries describe complete source/destination mirror rules,
        # not a universal URL prefix.  Do not put the first destination into
        # the editable prefix field: doing so would turn a GitHub URL into a
        # malformed ``<destination>/https://github.com/...`` clone URL.
        git_mirror_default = self.window.config.network.git_mirror
        extension_default = self.window.config.network.extension_index_url
        if not extension_default and isinstance(catalog_mirrors.get("extension_index_url"), dict):
            extension_default = str(catalog_mirrors["extension_index_url"].get("comfyui", "") or "")
        self.github_proxy_general = QLineEdit(self.window.config.network.github_proxy)
        self.github_proxy_general.setPlaceholderText("GitHub 加速前缀")
        root.addWidget(self.option_row("GitHub 加速", "仅作为 Git 的临时 insteadOf 配置", self.github_proxy_general))
        self.git_mirror_general = QLineEdit(git_mirror_default)
        self.git_mirror_general.setPlaceholderText("可选 Git 镜像前缀")
        root.addWidget(self.option_row("Git 镜像", "用于克隆/更新时替换 GitHub 地址", self.git_mirror_general))
        self.hf_mirror_general = QLineEdit(self.window.config.network.hf_mirror)
        self.hf_mirror_general.setPlaceholderText("例如 https://hf-mirror.com")
        root.addWidget(self.option_row("HuggingFace 镜像", "模型下载使用的 HF_ENDPOINT", self.hf_mirror_general))
        self.extension_index_general = QLineEdit(extension_default)
        self.extension_index_general.setPlaceholderText("扩展列表 JSON URL")
        root.addWidget(self.option_row("扩展列表镜像", "版本管理/安装新扩展使用的列表地址", self.extension_index_general))
        self.model_server_general = QLineEdit(self.window.config.network.model_server or catalog_model_server)
        self.model_server_general.setPlaceholderText("模型服务地址（可选）")
        root.addWidget(self.option_row("模型服务", "供扩展读取的 Aura-Rift 模型服务地址", self.model_server_general))
        self.hf_offline_toggle = Toggle("仅使用本地 HuggingFace 缓存")
        self.hf_offline_toggle.setChecked(self.window.config.huggingface_offline)
        root.addWidget(self.option_row("HuggingFace 离线", "禁止模型下载请求访问网络", self.hf_offline_toggle))

        save = QPushButton("保存设置")
        save.clicked.connect(self.save_general)
        root.addLayout(self._save_row(save))
        root.addStretch(1)
        self.general_content = page
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("settingsScroll")
        scroll.setWidget(page)
        self._add_settings_tab("general", scroll, "settings", self.window._tr("settings.general", "一般设置"))

    def _build_env(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        self.project_path = QLineEdit(self.window.config.comfy_path)
        choose_project = QPushButton("选择")
        choose_project.clicked.connect(lambda _checked=False: self.choose_project())
        root.addWidget(self.path_row("ComfyUI 目录", "选择已有项目或新建安装目标", self.project_path, choose_project))

        self.python_override = QLineEdit(self.window.config.python_path_override)
        self.python_override.setPlaceholderText("留空则使用项目 .venv/bin/python")
        choose_python = QPushButton("选择")
        choose_python.clicked.connect(lambda _checked=False: self.choose_python())
        root.addWidget(self.path_row("Python 路径覆盖", "用于兼容已有 Python/venv，Git 路径覆盖已在 Linux 版删除", self.python_override, choose_python))

        self.venv_manager_combo = QComboBox()
        for mgr in environment.VenvManager:
            self.venv_manager_combo.addItem(environment.MANAGER_LABELS[mgr], mgr.value)
        index = self.venv_manager_combo.findData(self.window.config.venv_manager)
        if index >= 0:
            self.venv_manager_combo.setCurrentIndex(index)
        root.addWidget(self.option_row("虚拟环境管理器", "选择创建环境和管理依赖的方式", self.venv_manager_combo))

        save = QPushButton("保存环境设置")
        save.clicked.connect(self.save_env)
        root.addLayout(self._save_row(save))
        root.addStretch(1)
        self._add_settings_tab("environment", page, "wrench", self.window._tr("settings.environment", "环境设置"))

    def _build_proxy(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        self.http_proxy = QLineEdit(self.window.config.network.http_proxy)
        self.https_proxy = QLineEdit(self.window.config.network.https_proxy)
        self.pypi_mirror = QComboBox()
        for name, url in PYPI_MIRRORS:
            self.pypi_mirror.addItem(name, url)
        idx = self.pypi_mirror.findData(self.window.config.network.pypi_mirror)
        self.pypi_mirror.setCurrentIndex(idx if idx >= 0 else 0)
        self.github_proxy_edit = QLineEdit(self.window.config.network.github_proxy)
        self.github_proxy_edit.setPlaceholderText("GitHub 镜像前缀，例如 https://gh-proxy.com/")
        proxy_card = card()
        proxy_layout = QGridLayout(proxy_card)
        proxy_layout.setContentsMargins(18, 18, 18, 18)
        proxy_layout.addWidget(label("代理与镜像设置", 16, True), 0, 0, 1, 2)
        proxy_layout.addWidget(label("HTTP_PROXY"), 1, 0)
        proxy_layout.addWidget(self.http_proxy, 1, 1)
        proxy_layout.addWidget(label("HTTPS_PROXY"), 2, 0)
        proxy_layout.addWidget(self.https_proxy, 2, 1)
        proxy_layout.addWidget(label("PyPI 镜像源"), 3, 0)
        proxy_layout.addWidget(self.pypi_mirror, 3, 1)
        proxy_layout.addWidget(label("GitHub 镜像前缀"), 4, 0)
        proxy_layout.addWidget(self.github_proxy_edit, 4, 1)
        root.addWidget(proxy_card)

        save = QPushButton("保存代理设置")
        save.clicked.connect(self.save_proxy)
        root.addLayout(self._save_row(save))
        root.addStretch(1)
        self._add_settings_tab("proxy", page, "git-branch", self.window._tr("settings.proxy", "代理设置"))

    def _build_about(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(36, 28, 36, 36)
        root.setSpacing(14)
        root.addStretch(1)
        avatar_path = _resource_file("icon_minimi.png")
        if avatar_path:
            avatar = QLabel()
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setPixmap(QPixmap(str(avatar_path)).scaled(92, 92, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            root.addWidget(avatar, 0, Qt.AlignCenter)
        title = label("Aura-Rift", 28, True)
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)
        version = label(f"版本 {__version__}", 13)
        version.setAlignment(Qt.AlignCenter)
        root.addWidget(version)
        tagline = label("Aura-Rift Linux ComfyUI launcher", 14)
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setObjectName("muted")
        root.addWidget(tagline)

        # 绘世的关于页把项目入口集中成一排链接卡片。  Keep the same
        # information density while replacing the original brand with
        # Aura-Rift and using Linux-safe/default-browser navigation.
        link_specs = [
            ("Aura-Rift", "项目主页", "https://github.com/leioukupo/Aura-Rift", "external"),
            ("ComfyUI", "开源生成引擎", "https://github.com/comfyanonymous/ComfyUI", "book"),
            ("NovelAI.Dev", "NAI 兴趣组", "https://novelai.dev/", "network"),
            ("秋葉aaaki", "Bilibili", "https://space.bilibili.com/12566101", "play"),
            ("喵喵hmkai", "Bilibili", "https://space.bilibili.com/2082155", "play"),
        ]
        self.about_links_container = QWidget()
        self.about_links_layout = QGridLayout(self.about_links_container)
        self.about_links_layout.setContentsMargins(0, 0, 0, 0)
        self.about_links_layout.setHorizontalSpacing(12)
        self.about_links_layout.setVerticalSpacing(12)
        self.about_link_cards: list[LinkCard] = []
        for title_text, description, url, icon_name in link_specs:
            card_widget = LinkCard(
                title_text,
                description,
                "",
                lambda url=url: QDesktopServices.openUrl(QUrl(url)),
                icon_name=icon_name,
            )
            card_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            # Keep the five-card row readable when the about browser shares a
            # compact 960×640 viewport; without a floor the surrounding
            # stretch items can compress LinkCard to a blank 22px strip.
            card_widget.setMinimumHeight(70)
            self.about_link_cards.append(card_widget)
        self._relayout_about_links()
        root.addWidget(self.about_links_container)
        self.about_browser.setMarkdown(self._about_text())
        self.about_browser.setMinimumHeight(220)
        root.addWidget(self.about_browser, 1)
        root.addStretch(1)
        # Keep the centered about composition intact on the reference
        # desktop, while allowing the five-card row and local about.md to be
        # reached on compact Linux windows.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("settingsScroll")
        scroll.setWidget(page)
        self._add_settings_tab("about", scroll, "terminal", self.window._tr("settings.about", "关于"))

    def _relayout_about_links(self) -> None:
        """Use the reference five-column row on desktop and wrap compactly."""
        layout = getattr(self, "about_links_layout", None)
        cards = getattr(self, "about_link_cards", None)
        if layout is None or not cards:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(self.about_links_container)
        width = max(0, self.width())
        columns = 5 if width >= 1500 else 3 if width >= 1100 else 2
        for index, widget in enumerate(cards):
            layout.addWidget(widget, index // columns, index % columns)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_about_links()

    def refresh(self) -> None:
        self.about_browser.setMarkdown(self._about_text())

    def option_row(self, title: str, desc: str, control: QWidget) -> QWidget:
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(18, 16, 18, 16)
        texts = QVBoxLayout()
        texts.addWidget(label(title, 15, True))
        texts.addWidget(label(desc, 12))
        layout.addLayout(texts, 1)
        layout.addWidget(control)
        return row

    def path_row(self, title: str, desc: str, edit: QLineEdit, button: QPushButton) -> QWidget:
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(18, 16, 18, 16)
        texts = QVBoxLayout()
        texts.addWidget(label(title, 15, True))
        texts.addWidget(label(desc, 12))
        layout.addLayout(texts)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _save_row(self, button: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        return row

    def choose_project(self, target: QLineEdit | None = None) -> None:
        """Choose a ComfyUI directory for either visible settings form.

        The Linux layout keeps the general settings form visible while the
        legacy environment form remains a compatibility surface.  Accepting
        an explicit target prevents the two buttons from accidentally writing
        to the hidden editor.
        """
        target = target or self.project_path
        path = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 目录", target.text())
        if path:
            target.setText(path)

    def choose_python(self, target: QLineEdit | None = None) -> None:
        target = target or self.python_override
        path, _ = QFileDialog.getOpenFileName(self, "选择 Python 可执行文件", str(Path.home()))
        if path:
            target.setText(path)

    def save_general(self) -> None:
        self.window.config.language = self.language_combo.currentData()
        self.window.config.expert_mode = bool(self.mode_combo.currentData())
        self.window.config.comfy_path = self.general_project_path.text().strip() or str(default_comfy_dir())
        self.window.config.python_path_override = self.general_python_path.text().strip()
        self.window.config.venv_manager = self.general_venv_manager_combo.currentData()
        self.window.config.network.http_proxy = self.proxy_http_general.text().strip()
        self.window.config.network.https_proxy = self.proxy_https_general.text().strip()
        self.window.config.network.pypi_mirror = self.pypi_mirror_general.currentData()
        self.window.config.network.github_proxy = self.github_proxy_general.text().strip()
        self.window.config.network.git_mirror = self.git_mirror_general.text().strip()
        self.window.config.network.hf_mirror = self.hf_mirror_general.text().strip()
        self.window.config.network.extension_index_url = self.extension_index_general.text().strip()
        self.window.config.network.model_server = self.model_server_general.text().strip()
        self.window.config.huggingface_offline = self.hf_offline_toggle.isChecked()
        self.window.config.launch.disable_auto_launch = not self.auto_browser.isChecked()
        self.window.config.proxy_enabled = self.apply_proxy_env.isChecked()
        self.window.config.proxy_for_git = self.proxy_git_toggle.isChecked()
        self.window.config.proxy_for_pip = self.proxy_pip_toggle.isChecked()
        self.window.config.proxy_for_environment = self.proxy_env_toggle.isChecked()
        self.window.config.proxy_for_models = self.proxy_models_toggle.isChecked()
        self.window.config.default_browser = self.auto_browser.isChecked()
        self.window.config.crash_auto_restart = self.crash_restart.isChecked()
        self.window.config.dependency_integrity_check = self.integrity_check.isChecked()
        self.window.config.auto_check_dependencies = self.integrity_check.isChecked()
        self.window.config.component_conflict_check = self.component_conflict_check.isChecked()
        self.window.config.duplicate_extension_check = self.duplicate_extension_check.isChecked()
        self.window.config.hotfixes_enabled = self.hotfixes_toggle.isChecked()
        self.window.save_config()
        # Apply the two mode-dependent affordances immediately without
        # rebuilding the whole frameless shell (which would discard scroll
        # position and any unsaved controls on the page).
        if hasattr(self.window, "advanced_page"):
            self.window.advanced_page.expert_params_button.setVisible(self.window.config.expert_mode)
        if hasattr(self.window, "version_page"):
            self.window.version_page.branch_row_widget.setVisible(self.window.config.expert_mode)
            self.window.version_page.on_channel_change()
        self.window.refresh_pages()
        QMessageBox.information(self, "已保存", "一般设置已保存。")

    def save_env(self) -> None:
        self.window.config.comfy_path = self.project_path.text().strip() or str(default_comfy_dir())
        py_override = self.python_override.text().strip()
        if py_override and not str(environment._resolve_override(py_override)):
            QMessageBox.warning(
                self, "Python 路径无效",
                "所选路径不是有效的 Python 解释器（未通过 --version 检查）。\n"
                "该覆盖已被忽略，将自动检测项目 .venv 或系统 Python。",
            )
            py_override = ""
        self.window.config.python_path_override = py_override
        self.window.config.venv_manager = self.venv_manager_combo.currentData()
        self.window.save_config()
        self.window.refresh_pages()
        QMessageBox.information(self, "已保存", "环境设置已保存。")

    def save_proxy(self) -> None:
        self.window.config.network.http_proxy = self.http_proxy.text().strip()
        self.window.config.network.https_proxy = self.https_proxy.text().strip()
        self.window.config.network.pypi_mirror = self.pypi_mirror.currentData()
        self.window.config.network.github_proxy = self.github_proxy_edit.text().strip()
        self.window.save_config()
        QMessageBox.information(self, "已保存", "代理设置已保存。")


class MainWindow(QMainWindow):
    deps_checked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        # A native-looking shell is drawn by Qt so the Linux build has the
        # same proportions and controls as 绘世 without calling Win32 APIs.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(960, 640)
        try:
            from aura_rift.resources import register_font, register_system_cjk_font
            register_font("cascadiamono.ttf")
            register_font("segmdl2.ttf")
            register_system_cjk_font()
        except Exception:
            pass
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.translator = Translator(self.config.language)
        self.process = ComfyProcess()
        self.current_task: TaskHandle | None = None
        self.nav_buttons: dict[str, QPushButton] = {}
        self.pages: dict[str, QWidget] = {}
        self._pending_launch = False
        self._precheck_worker = None
        self._browser_opened_for_run = False
        self._restart_attempts = 0
        self._restart_scheduled = False
        self._restart_timer: QTimer | None = None
        self._manual_stop_requested = False
        self._closing = False
        self.deps_checked.connect(self._on_deps_checked)

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 900)
        geometry = self.config.window_geometry
        if isinstance(geometry, dict) and geometry.get("width", 0) >= 960 and geometry.get("height", 0) >= 640:
            self.resize(int(geometry["width"]), int(geometry["height"]))
            if "x" in geometry and "y" in geometry:
                self.move(int(geometry["x"]), int(geometry["y"]))
        if self.config.window_maximized:
            self.showMaximized()
        self.process.output.connect(self.append_log)
        self.process.state_changed.connect(self.on_process_state)
        self.process.finished.connect(self._on_process_finished)
        self.rebuild()

    def comfy_dir(self) -> Path:
        return Path(self.config.comfy_path).expanduser()

    def _tr(self, key: str, default: str | None = None) -> str:
        return self.translator.tr(key, default)

    def rebuild(self) -> None:
        self.nav_buttons = {}
        QApplication.instance().setStyleSheet(stylesheet(self.config.theme))
        central = QWidget()
        central.setObjectName("windowFrame")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        icon_path = _resource_file("icon_minimi.png") or _resource_file("icon.png")
        icon_pixmap = QPixmap(str(icon_path)) if icon_path else None
        if icon_pixmap is not None and not icon_pixmap.isNull():
            # Also expose the bundled avatar to the desktop shell/task switcher;
            # the custom title bar uses the same pixmap below.
            self.setWindowIcon(QIcon(icon_pixmap))
        title = TitleBar(APP_NAME, __version__, icon_pixmap)
        title.help_requested.connect(self.show_about)
        title.theme_requested.connect(self.toggle_theme)
        title.minimize_requested.connect(self.showMinimized)
        title.maximize_requested.connect(self.toggle_maximize)
        title.close_requested.connect(self.close)
        self.title_bar = title
        root.addWidget(title)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.stack = QStackedWidget()
        self.sidebar = self.build_sidebar()
        content.addWidget(self.sidebar)
        content.addWidget(self.stack, 1)
        root.addLayout(content, 1)
        self.setCentralWidget(central)

        self.launch_page = LaunchPage(self)
        self.advanced_page = AdvancedPage(self)
        self.version_page = VersionPage(self)
        self.troubleshoot_page = TroubleshootPage(self)
        self.tools_page = ToolsPage(self)
        self.console_page = ConsolePage(self)
        self.settings_page = SettingsPage(self)
        self.pages = {
            "launch": self.launch_page,
            "advanced": self.advanced_page,
            "troubleshoot": self.troubleshoot_page,
            "versions": self.version_page,
            "tools": self.tools_page,
            "console": self.console_page,
            "settings": self.settings_page,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        # Older releases persisted these page identifiers.  Normalize them at
        # restore time so an upgrade does not unexpectedly land on the home
        # page (and preserve the old hotfix/patch tab selection below).
        saved_page = str(getattr(self.config, "last_page", "launch") or "launch")
        initial_aliases = {
            "troubleshooting": "troubleshoot",
            "diagnostics": "troubleshoot",
            "version": "versions",
            "maintenance": "advanced",
            "environment": "advanced",
            "env": "advanced",
            "patches": "advanced",
            "hotfixes": "advanced",
            "about": "settings",
            "general": "settings",
            "proxy": "settings",
            "network": "settings",
        }
        initial_page = initial_aliases.get(saved_page, saved_page)
        if initial_page not in self.pages:
            initial_page = "launch"
        self.show_page(initial_page)
        if saved_page in {"patches", "hotfixes"}:
            self.advanced_page.adv_tabs.setCurrentIndex(2)
        elif saved_page in {"maintenance", "environment", "env"}:
            self.advanced_page.adv_tabs.setCurrentIndex(1)
        elif saved_page in {"about", "general", "proxy", "network"}:
            self.settings_page.show_sub_page(saved_page)
        self.refresh_pages()

    def build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sideBar")
        side.setFixedWidth(132)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(10, 0, 10, 8)
        layout.setSpacing(4)
        group = QButtonGroup(side)
        group.setExclusive(True)
        accent = "#f2f2f2" if self.config.theme != "light" else "#333333"
        # fixed button dimensions so every nav entry has identical width/height
        # and icons line up on a consistent baseline regardless of label length
        button_w = 132 - 10 - 10
        button_h, bulb_h = self._sidebar_item_heights()
        icon_size = QSize(28, 28)
        sidebar_buttons: list[QToolButton] = []
        items = [
            ("launch", "rocket", self._tr("nav.launch", "一键启动")),
            ("advanced", "sliders", self._tr("nav.advanced", "高级选项")),
            ("troubleshoot", "wrench", "疑难解答"),
            ("versions", "history", self._tr("nav.versions", "版本管理")),
            ("tools", "briefcase", self._tr("nav.tools", "小工具")),
        ]
        for name, icon_name, text in items:
            button = QToolButton()
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setIcon(make_nav_icon(icon_name, accent, icon_size.width()))
            button.setIconSize(icon_size)
            button.setText(text)
            button.setFixedSize(button_w, button_h)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.clicked.connect(lambda _=False, n=name: self.show_page(n))
            self.nav_buttons[name] = button
            sidebar_buttons.append(button)
            group.addButton(button)
            layout.addWidget(button, 0, Qt.AlignCenter)
        # The lightbulb is an action, not a page, and deliberately lives in
        # the navigation rail like the target launcher.
        bulb = QToolButton()
        bulb.setObjectName("navButtonAccent")
        bulb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        bulb.setIcon(make_lightbulb_icon("#f4b942", 27))
        bulb.setIconSize(QSize(27, 27))
        bulb.setText("灯泡")
        bulb.setFixedSize(button_w, bulb_h)
        bulb.setToolTip("切换深色/浅色主题")
        bulb.clicked.connect(self.toggle_theme)
        layout.addWidget(bulb, 0, Qt.AlignCenter)
        layout.addStretch(1)
        for name, icon_name, text in (
            ("console", "terminal", self._tr("nav.console", "控制台")),
            ("settings", "settings", self._tr("nav.settings", "设置")),
        ):
            button = QToolButton()
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setIcon(make_nav_icon(icon_name, accent, icon_size.width()))
            button.setIconSize(icon_size)
            button.setText(text)
            button.setFixedSize(button_w, button_h)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.clicked.connect(lambda _=False, n=name: self.show_page(n))
            self.nav_buttons[name] = button
            sidebar_buttons.append(button)
            group.addButton(button)
            layout.addWidget(button, 0, Qt.AlignCenter)
        # Keep references for resize-time fitting.  A 2107px desktop can use
        # the full-height rail, while a 1280x800 window needs a compact rail
        # so the final console/settings actions never overlap or disappear.
        self._sidebar_buttons = sidebar_buttons
        self._sidebar_bulb = bulb
        return side

    def _sidebar_item_heights(self) -> tuple[int, int]:
        """Return rail item heights that fit the current client height.

        There are seven regular entries and one lightbulb action separated by
        seven small gaps.  Keep the desktop proportions (100/80) but shrink
        predictably below that threshold instead of allowing QBoxLayout to
        overlap fixed-size buttons.
        """

        client_height = max(320, int(self.height()) - 78)
        gaps = 7 * 4 + 8  # layout spacing plus top/bottom margins
        bulb_height = min(80, max(52, int(client_height * 0.10)))
        regular = (client_height - gaps - bulb_height) // 7
        regular = min(100, max(56, regular))
        return regular, bulb_height

    def _fit_sidebar(self) -> None:
        buttons = getattr(self, "_sidebar_buttons", ())
        bulb = getattr(self, "_sidebar_bulb", None)
        if not buttons and bulb is None:
            return
        regular, bulb_height = self._sidebar_item_heights()
        for button in buttons:
            button.setFixedHeight(regular)
        if bulb is not None:
            bulb.setFixedHeight(bulb_height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_sidebar()

    def show_page(self, name: str) -> None:
        original_name = name
        name = {
            "troubleshooting": "troubleshoot",
            "diagnostics": "troubleshoot",
            "version": "versions",
            "maintenance": "advanced",
            "environment": "advanced",
            "env": "advanced",
            "patches": "advanced",
            "hotfixes": "advanced",
            "about": "settings",
            "general": "settings",
            "proxy": "settings",
            "network": "settings",
        }.get(name, name)
        page = self.pages.get(name)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        self.config.last_page = name
        if name in self.nav_buttons:
            self.nav_buttons[name].setChecked(True)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        if original_name in {"patches", "hotfixes"} and hasattr(self.advanced_page, "adv_tabs"):
            self.advanced_page.adv_tabs.setCurrentIndex(2)
        elif original_name in {"maintenance", "environment", "env"} and hasattr(self.advanced_page, "adv_tabs"):
            self.advanced_page.adv_tabs.setCurrentIndex(1)
        elif original_name in {"about", "general", "proxy", "network"} and hasattr(self, "settings_page"):
            self.settings_page.show_sub_page(original_name)

    def toggle_theme(self) -> None:
        current_name = next((name for name, page in self.pages.items() if getattr(self, "stack", None) and self.stack.currentWidget() is page), "launch")
        advanced_index = (
            self.advanced_page.adv_tabs.currentIndex()
            if current_name == "advanced" and hasattr(self, "advanced_page")
            else 0
        )
        version_index = (
            self.version_page.tabs.currentIndex()
            if current_name == "versions" and hasattr(self, "version_page")
            else 0
        )
        settings_index = (
            self.settings_page.tabs.currentIndex()
            if current_name == "settings" and hasattr(self, "settings_page")
            else 0
        )
        self.config.theme = "light" if self.config.theme == "dark" else "dark"
        self.save_config()
        self.rebuild()
        self.show_page(current_name)
        if current_name == "advanced":
            self.advanced_page.adv_tabs.setCurrentIndex(max(0, min(advanced_index, self.advanced_page.adv_tabs.count() - 1)))
        elif current_name == "versions":
            self.version_page.tabs.setCurrentIndex(max(0, min(version_index, self.version_page.tabs.count() - 1)))
        elif current_name == "settings":
            self.settings_page.tabs.setCurrentIndex(max(0, min(settings_index, self.settings_page.tabs.count() - 1)))

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def show_about(self) -> None:
        self.show_page("settings")
        if hasattr(self, "settings_page"):
            self.settings_page.show_sub_page("about")

    def save_config(self) -> None:
        self.config_store.save(self.config)

    def refresh_pages(self) -> None:
        """Refresh only the currently visible page to avoid heavy I/O on hidden pages."""
        page = self.stack.currentWidget() if hasattr(self, "stack") else None
        if page:
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def append_log(self, text: str) -> None:
        if hasattr(self, "console_page"):
            self.console_page.append(text)

    def can_start_task(self) -> bool:
        if self.current_task is None:
            return True
        QMessageBox.warning(self, "任务进行中", "已有后台任务正在运行，请等待完成。")
        return False

    def git_command_spec(self, repo: Path, args: list[str], title: str = "") -> CommandSpec:
        # AppConfig migration enables the master flag for legacy files.  Once
        # the user turns it off, Git must receive neither proxy env vars nor a
        # temporary GitHub insteadOf rewrite.
        proxy_active = bool(self.config.proxy_enabled)
        proxy = self.config.network.github_proxy if (
            proxy_active and self.config.proxy_for_git
        ) else ""
        from aura_rift.services.comfy import command_environment
        mirror_rules = []
        if proxy_active and self.config.proxy_for_git and self.config.network.git_mirror:
            try:
                mirror_rules = _launcher_catalog(self.config).git_mirror_rule_pairs(
                    self.config.network.git_mirror
                )
            except Exception:
                mirror_rules = []
            # A trailing-slash setting is the legacy generic proxy format and
            # should be passed through the existing GitHub insteadOf logic.
            if not mirror_rules and not proxy:
                mirror_value = self.config.network.git_mirror.strip()
                if mirror_value.startswith(("http://", "https://")):
                    from urllib.parse import urlsplit

                    mirror_path = urlsplit(mirror_value).path.strip("/")
                    if not mirror_path or mirror_value.endswith("/"):
                        proxy = mirror_value
        env = command_environment(self.config, scope="git")
        return CommandSpec(
            git_command_args(args, proxy, mirror_rules),
            cwd=repo,
            env=env,
            title=title,
        )

    def ask_local_change_policy(
        self,
        repo: Path,
        label_text: str,
        changes: list[GitChange],
        action: str,
    ) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("检测到本地修改")
        dialog.setModal(True)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = label(f"{label_text} 存在本地修改", 16, True)
        root.addWidget(title)
        root.addWidget(label(f"仓库：{repo}"))
        root.addWidget(label(f"以下 {len(changes)} 个改动会影响{action}，请选择处理方式。"))

        change_list = QPlainTextEdit()
        change_list.setReadOnly(True)
        change_list.setLineWrapMode(QPlainTextEdit.NoWrap)
        change_list.setPlainText("\n".join(f"{change.label}: {change.path}" for change in changes))
        change_list.setMinimumHeight(180)
        change_list.setMaximumHeight(360)
        root.addWidget(change_list, 1)

        hint = QLabel("保留：先暂存改动，更新后再恢复；放弃：丢弃上面列出的改动后继续。")
        hint.setObjectName("footnote")
        hint.setWordWrap(True)
        root.addWidget(hint)

        suffix = "更新" if action == "更新" else "切换"
        selected: dict[str, str | None] = {"policy": None}
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        discard_btn = QPushButton(f"放弃并{suffix}")
        discard_btn.setObjectName("danger")
        keep_btn = QPushButton(f"保留并{suffix}")
        keep_btn.setObjectName("primary")
        buttons.addWidget(cancel_btn)
        buttons.addWidget(discard_btn)
        buttons.addWidget(keep_btn)
        root.addLayout(buttons)

        def choose(policy: str | None) -> None:
            selected["policy"] = policy
            dialog.accept() if policy else dialog.reject()

        keep_btn.clicked.connect(lambda: choose("keep"))
        discard_btn.clicked.connect(lambda: choose("discard"))
        cancel_btn.clicked.connect(lambda: choose(None))

        screen = QApplication.primaryScreen()
        max_h = 560
        if screen is not None:
            max_h = max(420, min(max_h, screen.availableGeometry().height() - 80))
        dialog.resize(760, max_h)
        if dialog.exec() != QDialog.Accepted:
            return None
        if selected["policy"] == "keep":
            return "keep"
        if selected["policy"] == "discard":
            return "discard"
        return None

    def _wrap_git_action_commands(
        self,
        repo: Path,
        label_text: str,
        action: str,
        git_args: list[list[str]],
        changes: list[GitChange],
    ) -> list[CommandSpec] | None:
        policy = "clean"
        if changes:
            policy = self.ask_local_change_policy(repo, label_text, changes, action)
            if policy is None:
                self.append_log(f"{label_text} {action}已取消。\n")
                return None

        specs: list[CommandSpec] = []
        if policy == "keep":
            msg = f"Aura-Rift auto stash before {action} {label_text}"
            for args in GitService.stash_commands(changes, msg):
                specs.append(self.git_command_spec(repo, args, f"暂存本地修改：{label_text}"))
        elif policy == "discard":
            for args in GitService.discard_commands(changes):
                specs.append(self.git_command_spec(repo, args, f"放弃本地修改：{label_text}"))

        for index, args in enumerate(git_args):
            title = f"{action}：{label_text}" if index == 0 else ""
            specs.append(self.git_command_spec(repo, args, title))

        if policy == "keep":
            for args in GitService.stash_pop_commands(changes):
                specs.append(self.git_command_spec(repo, args, f"恢复本地修改：{label_text}"))
        return specs

    def build_repo_update_commands(
        self,
        repo: Path,
        label_text: str,
        include_custom_nodes: bool = True,
    ) -> list[CommandSpec] | None:
        try:
            git = GitService(repo)
            changes = git.changes(include_custom_nodes=include_custom_nodes)
            git_args = git.fast_forward_commands(require_clean=False)
        except GitError as exc:
            QMessageBox.warning(self, "无法更新", str(exc))
            self.append_log(f"更新 {label_text} 失败：{exc}\n")
            return None
        return self._wrap_git_action_commands(repo, label_text, "更新", git_args, changes)

    def build_repo_checkout_commands(
        self,
        repo: Path,
        label_text: str,
        revision: str,
        include_custom_nodes: bool = False,
    ) -> list[CommandSpec] | None:
        try:
            git = GitService(repo)
            changes = git.changes(include_custom_nodes=include_custom_nodes)
            git_args = git.checkout_commands(revision, require_clean=False)
        except GitError as exc:
            QMessageBox.warning(self, "Git 失败", str(exc))
            self.append_log(f"切换 {label_text} 失败：{exc}\n")
            return None
        return self._wrap_git_action_commands(repo, label_text, "切换版本", git_args, changes)

    def on_process_state(self, state: str) -> None:
        if hasattr(self, "console_page"):
            self.console_page.set_status(state)
        if state == "运行中":
            if not self._browser_opened_for_run and self.config.default_browser and not self.config.launch.disable_auto_launch:
                self._browser_opened_for_run = True
                QDesktopServices.openUrl(QUrl(_web_ui_url(self.config)))

    def _on_process_finished(self, code: int) -> None:
        self.refresh_pages()
        self._browser_opened_for_run = False
        manual_stop = self._manual_stop_requested
        self._manual_stop_requested = False
        if code == 0 or manual_stop:
            self._restart_attempts = 0
            self._restart_scheduled = False
            return
        if (
            code != 0
            and self.config.crash_auto_restart
            and self._restart_attempts < 3
            and not self._pending_launch
            and not self._closing
        ):
            self._restart_attempts += 1
            self.append_log(f"进程异常退出，将在 2 秒后自动重启（第 {self._restart_attempts}/3 次）。\n")
            self._restart_scheduled = True
            if self._restart_timer is None:
                self._restart_timer = QTimer(self)
                self._restart_timer.setSingleShot(True)
                self._restart_timer.timeout.connect(self.start_comfy)
            self._restart_timer.start(2000)

    def start_comfy(self) -> None:
        """Begin the launch flow: precheck dependencies, then launch.

        resolve_python returns the interpreter ComfyUI will actually run on:
        a configured override, a conda env, a project .venv, or — as a last
        resort — the launcher's own sys.executable. A missing dedicated
        environment uses that fallback only for a direct launch; dependency
        repair is skipped so requirements are never installed into Aura-Rift's
        own interpreter. Frozen (PyInstaller-packaged) builds likewise skip
        checks when no explicit ComfyUI interpreter is available.
        """
        if self._closing:
            return
        if not self._restart_scheduled:
            # A fresh user launch starts a new crash-restart budget.  The
            # scheduled callback keeps the existing counter intact.
            self._restart_attempts = 0
        self._restart_scheduled = False
        self._manual_stop_requested = False
        self.config_store.save(self.config)
        self.show_page("console")
        if hasattr(self, "console_page"):
            self.console_page.clear_output()
        comfy = self.comfy_dir()
        self.append_log("\n\033[1;36mAura-Rift\033[0m  \033[2m准备启动 ComfyUI\033[0m\n")
        if not (comfy / "main.py").exists():
            self.append_log("\033[1;31m未找到 main.py，请先选择或安装 ComfyUI。\033[0m\n")
            self._prompt_missing_comfy()
            return
        if not self._apply_enabled_hotfixes():
            self.append_log("\033[1;31m热补丁应用失败，已阻止启动。\033[0m\n")
            return
        python = environment.resolve_python(
            comfy, self.config.python_path_override, self.config.venv_manager
        )
        if not self._dedicated_python_available(python):
            # Never offer a requirements install against Aura-Rift's own
            # interpreter when the selected ComfyUI environment is absent.
            # ``resolve_python`` intentionally has a system-Python fallback so
            # that a launch command can still be previewed; using that
            # fallback for dependency repair would silently contaminate the
            # launcher environment.  This also preserves the documented Linux
            # behavior of skipping the precheck until the user creates an
            # environment from Environment Maintenance.
            self.append_log(
                "未检测到 ComfyUI 专属 Python 环境，跳过依赖检查；"
                "请先在环境维护中创建虚拟环境。\n"
            )
            self._launch_comfy()
            return
        if str(python) == str(environment.sys.executable) and getattr(
            environment.sys, "frozen", False
        ):
            self.append_log("未检测到 ComfyUI 专属 Python 环境，跳过依赖检查并直接启动。\n")
            self._launch_comfy()
            return
        if not self.config.auto_check_dependencies or not self.config.dependency_integrity_check:
            self.append_log("已在设置中关闭启动前依赖检查，直接启动 ComfyUI。\n")
            self._launch_comfy()
            return
        self.append_log("\033[33m正在检查 ComfyUI 与插件依赖是否满足...\033[0m\n")
        self._run_precheck(comfy, python)

    def _dedicated_python_available(self, python: Path | str) -> bool:
        """Whether *python* is safe to use for ComfyUI dependency repairs.

        The resolver deliberately falls back to ``sys.executable`` when a
        configured manager has not created an environment yet.  That fallback
        is suitable for attempting a direct launch, but never for
        ``pip install -r``: doing so would install ComfyUI packages into the
        launcher's own environment.  An explicit, valid override is treated as
        intentional even when it points at a system interpreter.
        """
        override = str(getattr(self.config, "python_path_override", "") or "").strip()
        if override:
            try:
                raw = Path(override).expanduser()
                if raw.is_dir():
                    if os.name == "nt":
                        candidates = [raw / "Scripts" / "python.exe"]
                    else:
                        candidates = [raw / "bin" / "python", raw / "bin" / "python3"]
                    raw = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
                # ``resolve_python`` has already validated the override (and
                # selected the resulting path) before this helper is called;
                # compare paths here without running a second ``python
                # --version`` subprocess on the GUI thread.
                return raw.is_file() and Path(str(python)).expanduser().resolve() == raw.resolve()
            except (OSError, ValueError, TypeError, RuntimeError):
                return False
        try:
            # Compare the selected *path*, not ``realpath``: Python venvs on
            # Linux commonly symlink ``.venv/bin/python`` to the system
            # binary.  Resolving that symlink would mistake an isolated venv
            # for the launcher's interpreter and skip a valid precheck.
            selected = os.path.normcase(os.path.abspath(str(python)))
            launcher = os.path.normcase(os.path.abspath(str(environment.sys.executable)))
            if selected == launcher:
                return False
            return Path(str(python)).expanduser().is_file()
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def _prompt_missing_comfy(self) -> None:
        """Offer Linux-safe path selection/install actions when main.py is absent."""
        box = QMessageBox(self)
        box.setWindowTitle("未找到 ComfyUI")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            "当前路径没有找到 ComfyUI 的 main.py。\n"
            "请选择已有项目目录，或选择一个空目录作为安装目标。"
        )
        choose = box.addButton("选择已有目录", QMessageBox.AcceptRole)
        install = box.addButton("选择目录并安装", QMessageBox.ActionRole)
        settings = box.addButton("打开设置", QMessageBox.HelpRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is choose:
            path = QFileDialog.getExistingDirectory(self, "选择已有 ComfyUI 目录", str(self.comfy_dir()))
            if path:
                self.config.comfy_path = path
                self.save_config()
                self.refresh_pages()
                self.append_log(f"已选择 ComfyUI 目录：{path}\n")
        elif clicked is install:
            path = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 安装目录（需为空）", str(self.comfy_dir().parent))
            if path:
                self.config.comfy_path = path
                self.save_config()
                self.launch_page.path_edit.setText(path)
                self.launch_page.install_comfy()
        elif clicked is settings:
            self.show_page("settings")
            self.settings_page.show_sub_page("general")

    def _apply_enabled_hotfixes(self) -> bool:
        """Apply configured Linux hotfixes before launch, if the service exists."""
        if not self.config.hotfixes_enabled:
            return True
        try:
            from aura_rift.services.hotfixes import HotfixManager
            try:
                manager = HotfixManager(self.comfy_dir(), self.config)
            except TypeError:
                manager = HotfixManager(self.comfy_dir())
            status = manager.status() if callable(getattr(manager, "status", None)) else []
            entries = status.get("items", status) if isinstance(status, dict) else status
            for item in entries or []:
                if isinstance(item, dict):
                    enabled = item.get("enabled", item.get("catalog_enabled", False))
                    applied = item.get("applied", False)
                    available = item.get("available", item.get("supported", True))
                else:
                    enabled = getattr(item, "enabled", getattr(item, "catalog_enabled", False))
                    applied = getattr(item, "applied", False)
                    available = getattr(item, "available", getattr(item, "supported", True))
                if not enabled or applied:
                    continue
                if not available:
                    name = item.get("name", "") if isinstance(item, dict) else getattr(item, "name", "")
                    reason = item.get("unavailable_reason", item.get("reason", "")) if isinstance(item, dict) else getattr(item, "unavailable_reason", getattr(item, "reason", ""))
                    self.append_log(f"跳过不可用热补丁 {name}：{reason or '当前 Linux 环境不支持'}\n")
                    continue
                name = item.get("name", "") if isinstance(item, dict) else getattr(item, "name", "")
                result = manager.apply(name)
                self.append_log(f"热补丁 {name}：{result}\n")
                # HotfixManager returns a HotfixResult, but keep compatibility
                # with older adapters returning a bool or mapping.  A failed
                # application must never be followed by a ComfyUI launch.
                if isinstance(result, dict):
                    result_ok = result.get("ok", True)
                else:
                    result_ok = getattr(result, "ok", result)
                if isinstance(result_ok, str):
                    result_ok = result_ok.strip().lower() not in {
                        "", "0", "false", "no", "off", "failed", "error",
                    }
                if result_ok is False or not bool(result_ok):
                    self.append_log(f"热补丁 {name} 应用失败，已阻止启动。\n")
                    return False
            return True
        except ImportError:
            return True
        except Exception as exc:
            self.append_log(f"热补丁错误：{exc}\n")
            return False

    def _run_precheck(self, comfy: Path, python) -> None:
        # Run the (potentially slow) dependency check off the GUI thread; the
        # worker only computes and emits a queued signal, so it never touches
        # any QWidget directly.
        import threading
        from aura_rift.services.environment import check_dependencies

        if self._precheck_worker is not None:
            return

        def worker() -> None:
            try:
                result = check_dependencies(comfy, python, timeout=25)
            except Exception:
                result = None  # GUI thread reports the failure + launches anyway
            self.deps_checked.emit(result)

        self._precheck_worker = threading.Thread(target=worker, daemon=True)
        self._precheck_worker.start()

    def _on_deps_checked(self, result) -> None:
        self._precheck_worker = None
        if result is None:
            self.append_log("\033[33m依赖检查未能完成，直接启动 ComfyUI。\033[0m\n")
            self._launch_comfy()
            return
        # ``check_dependencies`` uses ``installed_count=-1`` to distinguish
        # a failed probe from a real, empty requirements set.  An empty
        # ``missing_files`` alone would make ``result.ok`` true and produce a
        # misleading "0 个依赖，已全部安装" message.
        if getattr(result, "installed_count", 0) < 0:
            self.append_log("\033[33m依赖检查未能完成，直接启动 ComfyUI。\033[0m\n")
            self._launch_comfy()
            return
        if result.ok:
            self.append_log("\033[32m" + result.summary() + "\033[0m\n")
            self._launch_comfy()
            return
        # Something missing: prompt the user.
        self.append_log("\033[33m依赖检查：" + result.summary() + "\033[0m\n")
        lines = []
        for path, refs in result.missing_files.items():
            title = path.parent.name if path.parent != self.comfy_dir() else "ComfyUI"
            names = "，".join(r.name for r in refs[:6])
            extra = f" 等 {len(refs)} 个" if len(refs) > 6 else ""
            lines.append(f"  · {title}: {names}{extra}")
        detail = "\n".join(lines)
        box = QMessageBox(self)
        box.setWindowTitle("依赖缺失")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"检测到 ComfyUI 启动所需依赖不满足。\n\n{detail}\n\n是否自动安装缺失依赖并启动？")
        install_btn = box.addButton("安装并启动", QMessageBox.AcceptRole)
        launch_btn = box.addButton("直接启动", QMessageBox.RejectRole)
        box.addButton("取消", QMessageBox.DestructiveRole)
        box.setDefaultButton(install_btn)
        box.exec()
        choice = box.clickedButton()
        if choice is install_btn:
            self._install_missing_then_launch(list(result.missing_files.keys()))
        elif choice is launch_btn:
            self._launch_comfy()
        # cancel-clicked: do nothing

    def _install_missing_then_launch(self, files: list[Path]) -> None:
        if not files:
            self._launch_comfy()
            return
        python = environment.resolve_python(
            self.comfy_dir(), self.config.python_path_override, self.config.venv_manager
        )
        if not self._dedicated_python_available(python):
            self.append_log(
                "未创建 ComfyUI 专属 Python 环境，已阻止将依赖安装到启动器环境。"
                "请先在环境维护中创建虚拟环境。\n"
            )
            self._pending_launch = False
            return
        self._pending_launch = True
        commands = install_missing_deps_commands(self.comfy_dir(), files, self.config)
        self.run_commands(commands, "安装缺失依赖")

    def _launch_comfy(self) -> None:
        self.show_page("console")
        self.process.start(self.config)

    def stop_comfy(self) -> None:
        self._manual_stop_requested = True
        self.process.stop()

    def run_commands(self, commands: list[CommandSpec], title: str) -> None:
        if self.current_task is not None:
            QMessageBox.warning(self, "任务进行中", "已有后台任务正在运行，请等待完成。")
            return
        self.show_page("console")
        self.append_log(f"\n\033[1;36m== {title} ==\033[0m\n")
        handle = TaskHandle(commands)
        self.current_task = handle
        handle.output.connect(self.append_log)
        handle.finished.connect(self.on_task_finished)
        handle.start()

    def on_task_finished(self, ok: bool, message: str) -> None:
        tail = "完成" if ok else "失败"
        color = "\033[32m" if ok else "\033[1;31m"
        self.append_log(f"\n{color}== {tail}: {message} ==\033[0m\n")
        was_install_for_launch = self._pending_launch
        self._pending_launch = False
        self.current_task = None
        self.refresh_pages()
        if was_install_for_launch and ok:
            self.append_log("\033[32m依赖安装完成，自动启动 ComfyUI。\033[0m\n")
            self._launch_comfy()
        elif was_install_for_launch and not ok:
            self.append_log("\033[1;31m依赖安装失败，已取消启动。\033[0m\n")

    def run_git_action(self, action, title: str) -> None:
        comfy = self.comfy_dir()
        try:
            action(GitService(comfy))
        except DirtyRepositoryError as exc:
            QMessageBox.warning(self, "已阻止", str(exc))
            self.append_log(f"\033[33m{title} 已阻止：{exc}\033[0m\n")
            return
        except GitError as exc:
            QMessageBox.warning(self, "Git 失败", str(exc))
            self.append_log(f"\033[1;31m{title} 失败：{exc}\033[0m\n")
            return
        self.append_log(f"\033[32m{title} 完成。\033[0m\n")
        self.refresh_pages()

    def install_or_update_manager(self) -> None:
        if not self.can_start_task():
            return
        comfy = self.comfy_dir()
        custom_nodes = ensure_dir(comfy / "custom_nodes")
        manager = custom_nodes / "ComfyUI-Manager"
        if manager.exists():
            commands = self.build_repo_update_commands(
                manager,
                "ComfyUI-Manager",
                include_custom_nodes=True,
            )
            if commands is None:
                return
        else:
            commands = install_manager_commands(comfy, self.config)
        self.run_commands(commands, "安装或更新 ComfyUI-Manager")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.process.is_running():
            result = QMessageBox.question(self, "退出", "ComfyUI 仍在运行，是否终止进程并退出？")
            if result != QMessageBox.Yes:
                event.ignore()
                return
            self._closing = True
            self.process.stop()
        else:
            self._closing = True
        # Do not leave an environment/Git task running after the window has
        # accepted the close event.  TaskHandle.cancel() is idempotent and
        # terminates the current child process without touching user files.
        if self.current_task is not None:
            self.current_task.cancel()
            self._pending_launch = False
        if self._restart_timer is not None:
            self._restart_timer.stop()
            self._restart_scheduled = False
        if not self.isMaximized():
            self.config.window_geometry = {
                "x": self.x(), "y": self.y(),
                "width": self.width(), "height": self.height(),
            }
        self.config.window_maximized = self.isMaximized()
        self.save_config()
        event.accept()
