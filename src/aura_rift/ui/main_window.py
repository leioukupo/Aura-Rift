from __future__ import annotations

import shlex
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
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
    install_comfy_commands,
    install_manager_commands,
    install_missing_deps_commands,
    install_plugin_command,
    install_requirements_command,
    reinstall_package_command,
)
from aura_rift.services.files import directory_size_hint, ensure_dir, open_path
from aura_rift.services.git_service import DirtyRepositoryError, GitError, GitService
from aura_rift.services.registry import ExtensionEntry, get_extensions, mark_installed, search_entries
from aura_rift.services.tasks import CommandSpec, TaskHandle
from aura_rift.theme import stylesheet
from aura_rift.ui.icons import make_lightbulb_icon, make_nav_icon


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
    emoji_ok = _EMOJI_AVAILABLE

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
        if plain:
            cursor.insertText(plain, self._fmt)

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
        start_button.setMinimumWidth(140)
        start_button.setFixedHeight(40)
        start_button.clicked.connect(window.start_comfy)
        header_layout.addWidget(stop_button)
        header_layout.addWidget(start_button)
        layout.addWidget(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("console")
        self.output.setReadOnly(True)
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
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        hero = QFrame()
        hero.setObjectName("hero")
        hero.setMinimumHeight(190)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(46, 30, 46, 30)
        hero_layout.addStretch(1)
        hero_layout.addWidget(label("ComfyUI · Linux", 13))
        hero_layout.addWidget(label("Aura-Rift 启动器", 30, True))
        hero_layout.addWidget(label("让 Linux 下的 ComfyUI 启动、升级和插件管理更顺手。", 15))
        hero_layout.addStretch(1)
        root.addWidget(hero)

        path_card = card()
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
        root.addWidget(path_card)

        middle = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label("文件夹", 20, True))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.folder_buttons: list[QPushButton] = []
        folders = [
            ("根目录", "."),
            ("自定义节点", "custom_nodes"),
            ("输入图片", "input"),
            ("输出图片", "output"),
            ("模型", "models"),
        ]
        for index, (title, rel) in enumerate(folders):
            button = QPushButton(f"{title}\n{rel}")
            button.setMinimumHeight(72)
            button.clicked.connect(lambda _=False, r=rel: self.open_folder(r))
            grid.addWidget(button, index // 3, index % 3)
            self.folder_buttons.append(button)
        left.addLayout(grid)
        left.addStretch(1)

        self.version_label = label("ComfyUI：未检测", 12)
        self.env_label = label("环境：未检测", 12)
        left.addWidget(self.version_label)
        left.addWidget(self.env_label)

        right = QVBoxLayout()
        right.addWidget(label("公告", 20, True))
        self.announcement = QTextBrowser()
        self.announcement.setMinimumWidth(320)
        self.announcement.setMarkdown(bundled_markdown("announcement.md"))
        right.addWidget(self.announcement, 1)
        start = QPushButton("一键启动")
        start.setObjectName("primary")
        start.clicked.connect(self.window.start_comfy)
        right.addWidget(start)

        middle.addLayout(left, 1)
        middle.addSpacing(20)
        middle.addLayout(right)
        root.addLayout(middle, 1)

    def refresh(self) -> None:
        self.announcement.setMarkdown(bundled_markdown("announcement.md"))
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
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        self.advanced_header_title = label(self.window._tr("adv.title", "高级选项"), 16, True)
        header_layout.addWidget(self.advanced_header_title)
        header_layout.addStretch(1)
        cmd_button = QPushButton(self.window._tr("button.show_command", "显示启动命令"))
        cmd_button.clicked.connect(self.show_launch_command)
        start_button = QPushButton(self.window._tr("button.start", "一键启动"))
        start_button.clicked.connect(window.start_comfy)
        header_layout.addWidget(cmd_button)
        header_layout.addWidget(start_button)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_launch_options(), self.window._tr("adv.title", "高级选项"))
        tabs.addTab(self._build_maintenance(), self.window._tr("maint.title", "环境维护"))
        tabs.addTab(self._build_full_params(), self.window._tr("adv.full", "完整参数"))
        self.advanced_tab_titles = [
            self.window._tr("adv.title", "高级选项"),
            self.window._tr("maint.title", "环境维护"),
            self.window._tr("adv.full", "完整参数"),
        ]
        self.adv_tabs = tabs
        self._adv_tabs_index = 0
        tabs.currentChanged.connect(self.on_advanced_tab_changed)
        layout.addWidget(tabs, 1)

    def on_advanced_tab_changed(self, index: int) -> None:
        self._adv_tabs_index = index
        if 0 <= index < len(self.advanced_tab_titles):
            self.advanced_header_title.setText(self.advanced_tab_titles[index])
        if index == 1:
            self.refresh()  # environment maintenance tab
        elif index == 2 and hasattr(self, "full_scroll"):
            self.refresh_full_params()

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

        self.cpu_vae_check = QCheckBox("使用 CPU 运行 VAE")
        self.cpu_vae_check.setChecked(self.window.config.launch.cpu_vae)
        root.addWidget(self.option_row("VAE 运行位置", "显存紧张时可启用，但速度会下降", self.cpu_vae_check))

        self.cache_combo = self.combo(
            [("由 ComfyUI 决定", "auto"), ("LRU 缓存", "lru"), ("经典缓存", "classic"), ("不缓存", "none")],
            self.window.config.launch.cache_strategy,
        )
        root.addWidget(self.option_row("缓存策略", "管理模型在显存中的缓存方式", self.cache_combo))

        self.disable_smart_memory_check = QCheckBox("禁用智能内存管理")
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

        self.cuda_malloc_check = QCheckBox("使用 CUDA Malloc")
        self.cuda_malloc_check.setChecked(self.window.config.launch.cuda_malloc)
        root.addWidget(self.option_row("CUDA 内分配", "启用后可能提升速度，部分环境可能不稳定", self.cuda_malloc_check))

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
        form.addWidget(label("网络、目录与附加参数", 16, True), 0, 0, 1, 2)
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
        row("cuda_device", "CUDA 设备 ID", "逗号分隔列表，例如 0 或 0,1。留空则全部可见", text(full.cuda_device, "0,1"))
        row("default_device", "默认设备 ID", "多卡时设为默认使用的设备 ID，其余仍可见。留 -1 不设置", spin_int(full.default_device, -1, 31))
        row("directml", "DirectML 设备", "改用 torch-directml。关闭=不启用，自动=不传设备号", combo([("关闭", "-2"), ("自动", "-1"), ("0", "0"), ("1", "1"), ("2", "2")], str(full.directml)))
        row("oneapi_device_selector", "oneAPI 设备选择", "oneAPI 设备选择字符串", text(full.oneapi_device_selector))
        row("supports_fp8_compute", "声明支持 FP8 计算", "让 ComfyUI 当作设备支持 FP8 计算", check("supports_fp8_compute", full.supports_fp8_compute))
        row("enable_triton_backend", "启用 Triton 后端", "启用 comfy-kitchen 的 Triton 后端，默认启动时关闭", check("enable_triton_backend", full.enable_triton_backend))
        row("force_channels_last", "强制 channels-last", "推理时强制使用 channels-last 内存布局", check("force_channels_last", full.force_channels_last))
        row("fp16_intermediates", "FP16 中间张量", "实验特性：节点间中间张量改用 FP16 而非 FP32", check("fp16_intermediates", full.fp16_intermediates))
        row("fp64_unet", "扩散模型 FP64", "以 FP64 精度运行扩散模型", check("fp64_unet", full.fp64_unet))
        row("fp8_e8m0fnu_unet", "扩散模型 FP8 e8m0fnu", "以 FP8 e8m0fnu 格式存储扩散模型权重", check("fp8_e8m0fnu_unet", full.fp8_e8m0fnu_unet))
        row("force_non_blocking", "强制非阻塞操作", "强制所有适用张量使用非阻塞操作，部分非 Nvidia 设备可能更快但不稳", check("force_non_blocking", full.force_non_blocking))

        # ---- 显存 / 缓存 ----
        section("显存与缓存")
        row("cache_ram", "RAM 压力缓存阈值", "以 GB 为单位的阈值，空格分隔（可选第二项为 inactive 阈值），例如 4 或 4 8", text(full.cache_ram, "4 8"))
        row("high_ram", "高内存模式", "适合高内存或优先使用页面文件的环境，可略微提升性能", check("high_ram", full.high_ram))
        row("reserve_vram", "预留显存(GB)", "为系统/其他程序预留的显存，留 0 表示不设置", spin_double(full.reserve_vram, 0, 64, 0.1))
        row("vram_headroom", "DynamicVRAM 余量(GB)", "预留完全空闲的显存余量，留 0 使用默认", spin_double(full.vram_headroom, 0, 32, 0.1))
        row("async_offload", "异步权重卸载", "自动=不启用，开启=默认 2 流，或填入自定义流数", combo([("自动", "auto"), ("开启(默认2流)", "on"), ("3 流", "3"), ("4 流", "4"), ("6 流", "6")], full.async_offload))
        row("disable_async_offload", "禁用异步卸载", "关闭异步权重卸载", check("disable_async_offload", full.disable_async_offload))
        row("disable_dynamic_vram", "禁用 DynamicVRAM", "改用基于估计的模型加载策略", check("disable_dynamic_vram", full.disable_dynamic_vram))
        row("enable_dynamic_vram", "启用 DynamicVRAM", "在默认未开启的系统上启用 DynamicVRAM", check("enable_dynamic_vram", full.enable_dynamic_vram))
        row("fast_disk", "快速磁盘模式", "优先磁盘动态加载/卸载，适合 NVMe；可改善部分用户体验", check("fast_disk", full.fast_disk))
        row("disable_pinned_memory", "禁用 pinned memory", "关闭锁页内存，某些环境需要", check("disable_pinned_memory", full.disable_pinned_memory))
        row("deterministic", "确定性算法", "让 PyTorch 使用更慢但确定性的算法，部分场景可复现但不能保证结果完全一致", check("deterministic", full.deterministic))

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
        row("enable_assets", "启用资源系统", "启用资源系统（API 路由、数据库同步、后台扫描）", check("enable_assets", full.enable_assets))
        row("enable_asset_hashing", "资源哈希扫描", "扫描资源时计算 blake3 哈希，可去重但增加开销", check("enable_asset_hashing", full.enable_asset_hashing))
        row("feature_flags", "特性开关", "逗号分隔，例如 show_signin_button=true,another_flag", text(full.feature_flags, "a=true,b"))

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
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(hint)
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
        group(["fp64_unet", "fp8_e8m0fnu_unet"])
        group(["use_sage_attention", "use_flash_attention"])
        group(["force_upcast_attention", "dont_upcast_attention"])
        group(["disable_dynamic_vram", "enable_dynamic_vram"])
        # text-encoder precision variants are pick-one
        group(["fp16_text_enc", "fp32_text_enc", "bf16_text_enc"])
        # enable-manager must stay on when its UI is disabled or legacy UI used
        group_all = [
            ("disable_manager_ui", "enable_manager_legacy_ui"),  # both off vs legacy
        ]

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

        # cross-page: this tab's cuda-malloc-disable isn't in FullOptions; treat the
        # 完整参数 tab alone. Apply initial derived states now (the toggled()
        # signal above does not fire when a control starts unchecked, so we
        # materialise the dependent states explicitly to avoid stale UI).
        if "disable_all_custom_nodes" in self.full_widgets and "whitelist_custom_nodes" in self.full_widgets:
            self._enforce_whitelist(self.full_widgets["disable_all_custom_nodes"], self.full_widgets["whitelist_custom_nodes"])
        if "async_offload" in self.full_widgets and "disable_async_offload" in self.full_widgets:
            self._enforce_async(self.full_widgets["async_offload"], self.full_widgets["disable_async_offload"])
        if "enable_manager" in self.full_widgets and "enable_manager_legacy_ui" in self.full_widgets:
            self._enforce_manager(self.full_widgets["enable_manager"], self.full_widgets["enable_manager_legacy_ui"])

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

        warning = card()
        warning_layout = QVBoxLayout(warning)
        warning_layout.addWidget(label("警告", 16, True))
        warning_layout.addWidget(label("环境维护会改动项目内 .venv 或 custom_nodes。执行前请确认当前任务已经停止。"))
        root.addWidget(warning)

        # venv manager selector
        self.venv_manager_combo = QComboBox()
        for mgr in environment.VenvManager:
            detected = environment.detect_venv_managers(self.window.comfy_dir()).get(mgr)
            suffix = "（已安装）" if detected and detected.available else "（未检测到）"
            if detected and detected.has_lock:
                suffix = "（检测到锁文件）"
            self.venv_manager_combo.addItem(environment.MANAGER_LABELS[mgr], mgr.value)
        index = self.venv_manager_combo.findData(self.window.config.venv_manager)
        if index >= 0:
            self.venv_manager_combo.setCurrentIndex(index)
        apply_mgr = QPushButton("应用并保存")
        apply_mgr.clicked.connect(self.apply_venv_manager)
        root.addWidget(self.option_row(
            "虚拟环境管理器",
            "选择创建环境和管理依赖的方式。检测到锁文件时建议跟随。",
            self.venv_manager_combo,
        ))
        mgr_button_row = QHBoxLayout()
        mgr_button_row.addStretch(1)
        mgr_button_row.addWidget(apply_mgr)
        root.addLayout(mgr_button_row)

        self.dep_table = QTableWidget(0, 2)
        self.dep_table.setHorizontalHeaderLabels(["项目", "状态"])
        self.dep_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.dep_table.verticalHeader().setVisible(False)
        self.dep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.dep_table)

        self.torch_label = label("PyTorch：未检测")
        root.addWidget(self.torch_label)

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
        action_layout.addWidget(refresh, 0, 0)
        action_layout.addWidget(create, 0, 1)
        action_layout.addWidget(self.package_edit, 1, 0)
        action_layout.addWidget(reinstall, 1, 1)
        action_layout.addWidget(manager, 2, 0, 1, 2)
        root.addWidget(actions)
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
        launch.listen = self.listen_check.isChecked()
        launch.host = self.host_edit.text().strip() or "0.0.0.0"
        launch.port = self.port_spin.value()
        launch.disable_auto_launch = self.disable_auto_launch.isChecked()
        launch.enable_cors = self.enable_cors_edit.text().strip()
        launch.output_directory = self.output_directory_edit.text().strip()
        launch.input_directory = self.input_directory_edit.text().strip()
        launch.extra_args = self.extra_args.text().strip()
        self.window.save_config()
        QMessageBox.information(self, "已保存", "高级选项已保存。")

    def reset_launch_options(self) -> None:
        self.window.config.launch = AppConfig().launch
        self.window.save_config()
        self.window.rebuild()

    def show_launch_command(self) -> None:
        self.apply_launch_options()
        comfy = self.window.comfy_dir()
        python = environment.resolve_python(comfy, self.window.config.python_path_override, self.window.config.venv_manager)
        args = [str(comfy / "main.py"), *self.window.config.launch.to_args(), *self.window.config.full.to_args()]
        command = " ".join(shlex.quote(str(part)) for part in [python, *args])
        self.window.show_page("console")
        self.window.append_log(command + "\n")

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

    def create_venv(self) -> None:
        comfy = self.window.comfy_dir()
        self.window.run_commands(create_venv_commands(comfy, self.window.config), "创建 .venv")

    def reinstall_package(self) -> None:
        package = self.package_edit.text().strip()
        if not package:
            QMessageBox.warning(self, "缺少包名", "请输入需要重装的 Python 包名。")
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
        self.extensions_loaded.connect(self._on_extensions_loaded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        self.version_header_title = label(self.window._tr("ver.core", "内核"), 16, True)
        header_layout.addWidget(self.version_header_title)
        header_layout.addStretch(1)
        refresh = QPushButton(self.window._tr("ver.refresh_list", "刷新列表"))
        refresh.clicked.connect(self.reload_extension_list)
        update = QPushButton(self.window._tr("ver.update_all", "一键更新"))
        update.clicked.connect(self.update_core)
        header_layout.addWidget(refresh)
        header_layout.addWidget(update)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_core_tab(), self.window._tr("ver.core", "内核"))
        tabs.addTab(self._build_extensions_tab(), self.window._tr("ver.extensions", "扩展"))
        tabs.addTab(self._build_install_tab(), self.window._tr("ver.install", "安装新扩展"))
        self.version_tab_titles = [self.window._tr("ver.core", "内核"), self.window._tr("ver.extensions", "扩展"), self.window._tr("ver.install", "安装新扩展")]
        self.tabs = tabs
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
        self.commit_table.setColumnWidth(4, 100)
        self.commit_table.verticalHeader().setVisible(False)
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
        self.extension_table = QTableWidget(0, 6)
        self.extension_table.setHorizontalHeaderLabels(["扩展名", "分支", "版本", "状态", "路径", "操作"])
        header = self.extension_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.extension_table.setColumnWidth(1, 140)
        self.extension_table.setColumnWidth(2, 110)
        self.extension_table.setColumnWidth(3, 96)
        self.extension_table.setColumnWidth(5, 120)
        self.extension_table.verticalHeader().setVisible(False)
        self.extension_table.verticalHeader().setDefaultSectionSize(44)
        self.extension_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.extension_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.extension_table, 1)
        buttons = QHBoxLayout()
        update_selected = QPushButton("更新选中扩展")
        update_selected.clicked.connect(self.update_selected_extension)
        open_selected = QPushButton("打开选中扩展目录")
        open_selected.clicked.connect(self.open_selected_extension)
        buttons.addStretch(1)
        buttons.addWidget(open_selected)
        buttons.addWidget(update_selected)
        root.addLayout(buttons)
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
        self.extension_install_list.setHorizontalHeaderLabels(["插件名称", "作者", "类别", "状态", "操作"])
        header = self.extension_install_list.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.extension_install_list.setColumnWidth(1, 160)
        self.extension_install_list.setColumnWidth(2, 120)
        self.extension_install_list.setColumnWidth(3, 80)
        self.extension_install_list.setColumnWidth(4, 120)
        self.extension_install_list.verticalHeader().setVisible(False)
        self.extension_install_list.verticalHeader().setDefaultSectionSize(44)
        self.extension_install_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.extension_install_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.extension_install_list.setMinimumHeight(300)
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
            self.extension_install_list.setItem(row, 1, QTableWidgetItem(entry.author))
            self.extension_install_list.setItem(row, 2, QTableWidgetItem(entry.category))
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

        def worker() -> None:
            try:
                entries = get_extensions(Path(comfy_path))
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

    def refresh_core(self) -> None:
        comfy = self.window.comfy_dir()
        self.commit_table.setRowCount(0)
        # expert mode controls branch row visibility
        self.branch_row_widget.setVisible(self.window.config.expert_mode)
        if not self.branch_combo.isEnabled():
            self.branch_combo.setEnabled(True)
        if not (comfy / ".git").exists():
            self.remote_label.setText("远程地址：未检测到 ComfyUI Git 仓库")
            self.branch_label.setText("当前分支：-")
            self.commit_label.setText("当前版本：-")
            return
        try:
            git = GitService(comfy)
            self.remote_label.setText(f"远程地址：{git.remote_url()}")
            branch = git.current_branch()
            self.branch_label.setText(f"当前分支：{branch or '-'}")
            current = git.current_commit()
            self.commit_label.setText(
                f"当前版本：{current[:8]}" if current else "当前版本：尚未有任何提交（空仓库）"
            )
            if self.window.config.expert_mode:
                self.branch_combo.clear()
                for branch in git.branches():
                    self.branch_combo.addItem(branch)
                index = self.branch_combo.findText(branch)
                if index >= 0:
                    self.branch_combo.setCurrentIndex(index)
            else:
                self.branch_combo.clear()
            # stable channel→tags; dev channel→all commits
            items = git.tags() if self.is_stable_channel() else git.commits()
            self.commit_table.setUpdatesEnabled(False)
            self.commit_table.setRowCount(len(items))
            for row, item in enumerate(items):
                self.commit_table.setItem(row, 0, QTableWidgetItem(item.short_hash))
                self.commit_table.setItem(row, 1, QTableWidgetItem(item.subject))
                self.commit_table.setItem(row, 2, QTableWidgetItem(item.date))
                self.commit_table.setItem(row, 3, QTableWidgetItem("是" if item.current else ""))
                button = QPushButton("当前" if item.current else "切换")
                button.setFixedHeight(30)
                button.setEnabled(not item.current)
                button.clicked.connect(lambda _=False, rev=item.full_hash: self.checkout_revision(rev))
                self.commit_table.setCellWidget(row, 4, button)
            self.commit_table.setUpdatesEnabled(True)
        except GitError as exc:
            self.remote_label.setText(f"远程地址：读取失败：{exc}")
            self.branch_label.setText("当前分支：读取失败")
            self.commit_label.setText("当前版本：读取失败")

    def refresh_extensions(self) -> None:
        custom_nodes = self.window.comfy_dir() / "custom_nodes"
        manager = custom_nodes / "ComfyUI-Manager"
        self.manager_label.setText("ComfyUI-Manager：已安装" if manager.exists() else "ComfyUI-Manager：未安装")
        if not custom_nodes.exists():
            self.extension_table.setRowCount(0)
            return
        dirs = sorted((p for p in custom_nodes.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
        self.extension_table.setUpdatesEnabled(False)
        self.extension_table.setRowCount(len(dirs))
        for row, child in enumerate(dirs):
            self.extension_table.setItem(row, 0, QTableWidgetItem(child.name))
            self.extension_table.setItem(row, 4, QTableWidgetItem(str(child)))
            if (child / ".git").exists():
                try:
                    git = GitService(child)
                    dirty = "有本地修改" if git.is_dirty() else "干净"
                    self.extension_table.setItem(row, 1, QTableWidgetItem(git.current_branch()))
                    self.extension_table.setItem(row, 2, QTableWidgetItem(git.current_commit()[:8]))
                    self.extension_table.setItem(row, 3, QTableWidgetItem(dirty))
                except GitError as exc:
                    self.extension_table.setItem(row, 3, QTableWidgetItem(str(exc)))
            else:
                self.extension_table.setItem(row, 1, QTableWidgetItem("-"))
                self.extension_table.setItem(row, 2, QTableWidgetItem("-"))
                self.extension_table.setItem(row, 3, QTableWidgetItem("非 Git 扩展"))
            uninstall_btn = QPushButton("卸载")
            uninstall_btn.setObjectName("danger")
            uninstall_btn.setFixedHeight(30)
            uninstall_btn.clicked.connect(lambda _=False, p=child: self._uninstall_extension(p))
            self.extension_table.setCellWidget(row, 5, uninstall_btn)
        self.extension_table.setUpdatesEnabled(True)

    def fetch_core(self) -> None:
        self.window.run_commands(
            [CommandSpec(["git", "fetch", "--all", "--tags", "--prune"], cwd=self.window.comfy_dir(), env=self.window.config.network.environment())],
            "拉取远端信息",
        )

    def update_core(self) -> None:
        try:
            git = GitService(self.window.comfy_dir())
            git.assert_clean("更新")
        except (DirtyRepositoryError, GitError) as exc:
            QMessageBox.warning(self, "已阻止", str(exc))
            self.window.append_log(f"更新 ComfyUI 已阻止：{exc}\n")
            return
        self.window.run_commands(
            [CommandSpec(["git", "pull", "--ff-only"], cwd=self.window.comfy_dir(), env=self.window.config.network.environment())],
            "更新 ComfyUI",
        )

    def checkout_branch(self) -> None:
        branch = self.branch_combo.currentText()
        if branch:
            self.checkout_revision(branch)

    def checkout_revision(self, revision: str) -> None:
        self.window.run_git_action(lambda git: git.checkout(revision), f"切换版本：{revision}")

    def selected_extension_path(self) -> Path | None:
        rows = self.extension_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.warning(self, "未选择", "请先选择一个扩展。")
            return None
        return Path(self.extension_table.item(rows[0].row(), 4).text())

    def update_selected_extension(self) -> None:
        path = self.selected_extension_path()
        if not path:
            return
        if not (path / ".git").exists():
            QMessageBox.warning(self, "无法更新", "选中的扩展不是 Git 仓库。")
            return
        self.window.run_commands([CommandSpec(["git", "pull", "--ff-only"], cwd=path)], f"更新扩展 {path.name}")

    def open_selected_extension(self) -> None:
        path = self.selected_extension_path()
        if path and not open_path(path):
            InternalFileBrowser(path, self).exec()

    def _uninstall_extension(self, path: Path) -> None:
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
        self.window.run_commands(
            [install_plugin_command(self.window.comfy_dir(), url, self.window.config)],
            "安装扩展",
        )


class ToolsPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 36, 30)
        root.setSpacing(14)
        root.addWidget(label("小工具", 22, True))

        grid = QGridLayout()
        actions = [
            ("打开 Web UI", self.open_web_ui),
            ("打开 ComfyUI 根目录", lambda: self.open_relative(".")),
            ("打开 models", lambda: self.open_relative("models")),
            ("打开 custom_nodes", lambda: self.open_relative("custom_nodes")),
            ("打开 output", lambda: self.open_relative("output")),
            ("创建常用目录", self.create_common_dirs),
            ("显示启动命令", self.show_command),
            ("刷新硬件信息", self.refresh_hardware),
        ]
        for index, (title, callback) in enumerate(actions):
            button = QPushButton(title)
            button.setMinimumHeight(64)
            button.clicked.connect(callback)
            grid.addWidget(button, index // 4, index % 4)
        root.addLayout(grid)

        self.hardware = QTextBrowser()
        root.addWidget(self.hardware, 1)
        self.refresh_hardware()

    def open_web_ui(self) -> None:
        launch = self.window.config.launch
        host = "127.0.0.1" if launch.listen else launch.host
        QDesktopServices.openUrl(QUrl(f"http://{host}:{launch.port}"))

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


class SettingsPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.nav_buttons: dict[str, QPushButton] = {}
        self.pages: dict[str, QWidget] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        layout.addWidget(self._build_sub_nav())
        layout.addWidget(self.stack, 1)

        self.about_browser = QTextBrowser()
        self._build_general()
        self._build_env()
        self._build_proxy()
        self._build_about()

    def _build_sub_nav(self) -> QWidget:
        side = QFrame()
        side.setObjectName("settingsNav")
        side.setFixedWidth(130)
        nav = QVBoxLayout(side)
        nav.setContentsMargins(8, 18, 8, 18)
        nav.setSpacing(8)
        group = QButtonGroup(side)
        group.setExclusive(True)
        items = [
            ("general", self.window._tr("settings.general", "一般设置")),
            ("environment", self.window._tr("settings.environment", "环境设置")),
            ("proxy", self.window._tr("settings.proxy", "代理设置")),
            ("about", self.window._tr("settings.about", "关于")),
        ]
        for name, text in items:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumHeight(46)
            button.clicked.connect(lambda _=False, n=name: self.show_sub_page(n))
            self.nav_buttons[name] = button
            group.addButton(button)
            nav.addWidget(button)
        nav.addStretch(1)
        return side

    def show_sub_page(self, name: str) -> None:
        page = self.pages.get(name)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        if name in self.nav_buttons:
            self.nav_buttons[name].setChecked(True)
        if name == "about":
            self.about_browser.setMarkdown(bundled_markdown("about.md"))

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

        save = QPushButton("保存设置")
        save.clicked.connect(self.save_general)
        root.addLayout(self._save_row(save))
        root.addStretch(1)
        self.stack.addWidget(page)
        self.pages["general"] = page
        self.show_sub_page("general")

    def _build_env(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        self.project_path = QLineEdit(self.window.config.comfy_path)
        choose_project = QPushButton("选择")
        choose_project.clicked.connect(self.choose_project)
        root.addWidget(self.path_row("ComfyUI 目录", "选择已有项目或新建安装目标", self.project_path, choose_project))

        self.python_override = QLineEdit(self.window.config.python_path_override)
        self.python_override.setPlaceholderText("留空则使用项目 .venv/bin/python")
        choose_python = QPushButton("选择")
        choose_python.clicked.connect(self.choose_python)
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
        self.stack.addWidget(page)
        self.pages["environment"] = page

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
        self.stack.addWidget(page)
        self.pages["proxy"] = page

    def _build_about(self) -> None:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        self.about_browser.setMarkdown(bundled_markdown("about.md"))
        root.addWidget(self.about_browser, 1)
        self.stack.addWidget(page)
        self.pages["about"] = page

    def refresh(self) -> None:
        self.about_browser.setMarkdown(bundled_markdown("about.md"))

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

    def choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 目录", self.project_path.text())
        if path:
            self.project_path.setText(path)

    def choose_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 Python 可执行文件", str(Path.home()))
        if path:
            self.python_override.setText(path)

    def save_general(self) -> None:
        self.window.config.language = self.language_combo.currentData()
        self.window.config.expert_mode = bool(self.mode_combo.currentData())
        self.window.save_config()
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
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.translator = Translator(self.config.language)
        self.process = ComfyProcess()
        self.current_task: TaskHandle | None = None
        self.nav_buttons: dict[str, QPushButton] = {}
        self.pages: dict[str, QWidget] = {}
        self._pending_launch = False
        self._precheck_worker = None
        self.deps_checked.connect(self._on_deps_checked)

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.process.output.connect(self.append_log)
        self.process.state_changed.connect(self.on_process_state)
        self.process.finished.connect(lambda _code: self.refresh_pages())
        self.rebuild()

    def comfy_dir(self) -> Path:
        return Path(self.config.comfy_path).expanduser()

    def _tr(self, key: str, default: str | None = None) -> str:
        return self.translator.tr(key, default)

    def rebuild(self) -> None:
        self.nav_buttons = {}
        QApplication.instance().setStyleSheet(stylesheet(self.config.theme))
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = QFrame()
        title.setObjectName("titleBar")
        title_layout = QHBoxLayout(title)
        title_layout.setContentsMargins(22, 10, 18, 10)
        title_layout.addWidget(label(APP_NAME, 16, True))
        version_tag = QLabel(__version__)
        version_tag.setObjectName("footnote")
        title_layout.addWidget(version_tag)
        title_layout.addStretch(1)
        icon_color = "#e8e8ee" if self.config.theme == "dark" else "#2a2f37"
        theme_button = QPushButton(make_lightbulb_icon(icon_color), "")
        theme_button.setObjectName("flat")
        theme_button.setToolTip("切换深色/浅色主题")
        theme_button.setIconSize(QPixmap(24, 24).size())
        theme_button.clicked.connect(self.toggle_theme)
        title_layout.addWidget(theme_button)
        root.addWidget(title)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.stack = QStackedWidget()
        content.addWidget(self.build_sidebar())
        content.addWidget(self.stack, 1)
        root.addLayout(content, 1)
        self.setCentralWidget(central)

        self.launch_page = LaunchPage(self)
        self.advanced_page = AdvancedPage(self)
        self.version_page = VersionPage(self)
        self.tools_page = ToolsPage(self)
        self.console_page = ConsolePage(self)
        self.settings_page = SettingsPage(self)
        self.pages = {
            "launch": self.launch_page,
            "advanced": self.advanced_page,
            "versions": self.version_page,
            "tools": self.tools_page,
            "console": self.console_page,
            "settings": self.settings_page,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.show_page("launch")
        self.refresh_pages()

    def build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sideBar")
        side.setFixedWidth(132)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(10, 18, 10, 18)
        layout.setSpacing(8)
        group = QButtonGroup(side)
        group.setExclusive(True)
        accent = "#38bdb2" if self.config.theme != "light" else "#1aa090"
        # fixed button dimensions so every nav entry has identical width/height
        # and icons line up on a consistent baseline regardless of label length
        button_w = 132 - 10 - 10
        button_h = 78
        icon_size = QSize(28, 28)
        items = [
            ("launch", "rocket", self._tr("nav.launch", "一键启动")),
            ("advanced", "sliders", self._tr("nav.advanced", "高级选项")),
            ("versions", "git-branch", self._tr("nav.versions", "版本管理")),
            ("tools", "wrench", self._tr("nav.tools", "小工具")),
            ("console", "terminal", self._tr("nav.console", "控制台")),
            ("settings", "settings", self._tr("nav.settings", "设置")),
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
            group.addButton(button)
            layout.addWidget(button, 0, Qt.AlignCenter)
            if name == "tools":
                layout.addStretch(1)
        return side

    def show_page(self, name: str) -> None:
        page = self.pages.get(name)
        if not page:
            return
        self.stack.setCurrentWidget(page)
        if name in self.nav_buttons:
            self.nav_buttons[name].setChecked(True)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def toggle_theme(self) -> None:
        self.config.theme = "light" if self.config.theme == "dark" else "dark"
        self.save_config()
        self.rebuild()

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

    def on_process_state(self, state: str) -> None:
        if hasattr(self, "console_page"):
            self.console_page.set_status(state)

    def start_comfy(self) -> None:
        """Begin the launch flow: precheck dependencies, then launch.

        resolve_python returns the interpreter ComfyUI will actually run on:
        a configured override, a conda env, a project .venv, or — as a last
        resort — the launcher's own sys.executable. The dependency check is
        always run against that interpreter; the only exception is a frozen
        (PyInstaller-packaged) build where sys.executable is the binary
        itself and cannot execute -c scripts.
        """
        self.config_store.save(self.config)
        self.show_page("console")
        if hasattr(self, "console_page"):
            self.console_page.clear_output()
        comfy = self.comfy_dir()
        self.append_log("\n\033[1;36mAura-Rift\033[0m  \033[2m准备启动 ComfyUI\033[0m\n")
        if not (comfy / "main.py").exists():
            self.append_log("\033[1;31m未找到 main.py，请先选择或安装 ComfyUI。\033[0m\n")
            return
        python = environment.resolve_python(
            comfy, self.config.python_path_override, self.config.venv_manager
        )
        if str(python) == str(environment.sys.executable) and getattr(
            environment.sys, "frozen", False
        ):
            self.append_log("未检测到 ComfyUI 专属 Python 环境，跳过依赖检查并直接启动。\n")
            self._launch_comfy()
            return
        self.append_log("\033[33m正在检查 ComfyUI 与插件依赖是否满足...\033[0m\n")
        self._run_precheck(comfy, python)

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
        cancel_btn = box.addButton("取消", QMessageBox.DestructiveRole)
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
        self._pending_launch = True
        commands = install_missing_deps_commands(self.comfy_dir(), files, self.config)
        self.run_commands(commands, "安装缺失依赖")

    def _launch_comfy(self) -> None:
        self.show_page("console")
        self.process.start(self.config)

    def stop_comfy(self) -> None:
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
        comfy = self.comfy_dir()
        custom_nodes = ensure_dir(comfy / "custom_nodes")
        manager = custom_nodes / "ComfyUI-Manager"
        if manager.exists():
            commands = [CommandSpec(["git", "pull", "--ff-only"], cwd=manager)]
        else:
            commands = install_manager_commands(comfy, self.config)
        self.run_commands(commands, "安装或更新 ComfyUI-Manager")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.process.is_running():
            result = QMessageBox.question(self, "退出", "ComfyUI 仍在运行，是否终止进程并退出？")
            if result != QMessageBox.Yes:
                event.ignore()
                return
            self.process.stop()
        event.accept()
