from __future__ import annotations

import shlex
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
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
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
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
)
from aura_rift.i18n import Translator
from aura_rift.services import environment
from aura_rift.services.comfy import (
    ComfyProcess,
    command_environment,
    create_venv_commands,
    install_comfy_commands,
    install_manager_commands,
    install_plugin_command,
    reinstall_package_command,
)
from aura_rift.services.files import directory_size_hint, ensure_dir, open_path
from aura_rift.services.git_service import DirtyRepositoryError, GitError, GitService
from aura_rift.services.tasks import CommandSpec, TaskHandle
from aura_rift.theme import stylesheet


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
        self.status = label("未运行", 18, True)
        header_layout.addWidget(self.status, 1)
        stop_button = QPushButton("终止进程")
        stop_button.setObjectName("danger")
        stop_button.clicked.connect(window.stop_comfy)
        start_button = QPushButton("一键启动")
        start_button.clicked.connect(window.start_comfy)
        header_layout.addWidget(stop_button)
        header_layout.addWidget(start_button)
        layout.addWidget(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("console")
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)

    def append(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)

    def set_status(self, status: str) -> None:
        self.status.setText(status)


class LaunchPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        hero = QFrame()
        hero.setObjectName("hero")
        hero.setMinimumHeight(180)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(42, 28, 42, 28)
        hero_layout.addStretch(1)
        hero_layout.addWidget(label("ComfyUI", 18))
        hero_layout.addWidget(label("Aura-Rift 启动器", 28, True))
        hero_layout.addWidget(label("让 Linux 下的 ComfyUI 启动、升级和插件管理更顺手。", 16))
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
        status = environment.dependency_status(comfy, self.window.config.python_path_override)
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
            install_comfy_commands(target, command_environment(self.window.config)),
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
        header_layout.addWidget(label("高级选项", 16, True))
        header_layout.addWidget(label("环境维护", 16, True))
        header_layout.addStretch(1)
        cmd_button = QPushButton("显示启动命令")
        cmd_button.clicked.connect(self.show_launch_command)
        start_button = QPushButton("一键启动")
        start_button.clicked.connect(window.start_comfy)
        header_layout.addWidget(cmd_button)
        header_layout.addWidget(start_button)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_launch_options(), "高级选项")
        tabs.addTab(self._build_maintenance(), "环境维护")
        layout.addWidget(tabs, 1)

    def _build_launch_options(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
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
        form.addWidget(label("网络与附加参数", 16, True), 0, 0, 1, 2)
        form.addWidget(self.listen_check, 1, 0)
        form.addWidget(self.host_edit, 1, 1)
        form.addWidget(label("端口"), 2, 0)
        form.addWidget(self.port_spin, 2, 1)
        form.addWidget(self.disable_auto_launch, 3, 0, 1, 2)
        form.addWidget(label("额外参数"), 4, 0)
        form.addWidget(self.extra_args, 4, 1)
        root.addWidget(network_card)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = QPushButton("恢复默认设置")
        reset.clicked.connect(self.reset_launch_options)
        save = QPushButton("保存高级选项")
        save.clicked.connect(self.apply_launch_options)
        buttons.addWidget(reset)
        buttons.addWidget(save)
        root.addLayout(buttons)
        root.addStretch(1)
        return page

    def _build_maintenance(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(14)

        warning = card()
        warning_layout = QVBoxLayout(warning)
        warning_layout.addWidget(label("警告", 16, True))
        warning_layout.addWidget(label("环境维护会改动项目内 .venv 或 custom_nodes。执行前请确认当前任务已经停止。"))
        root.addWidget(warning)

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
        launch.listen = self.listen_check.isChecked()
        launch.host = self.host_edit.text().strip() or "0.0.0.0"
        launch.port = self.port_spin.value()
        launch.disable_auto_launch = self.disable_auto_launch.isChecked()
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
        python = environment.resolve_python(comfy, self.window.config.python_path_override)
        args = [str(comfy / "main.py"), *self.window.config.launch.to_args()]
        command = " ".join(shlex.quote(str(part)) for part in [python, *args])
        self.window.show_page("console")
        self.window.append_log(command + "\n")

    def refresh(self) -> None:
        comfy = self.window.comfy_dir()
        deps = environment.dependency_status(comfy, self.window.config.python_path_override)
        self.dep_table.setRowCount(0)
        for key, value in deps.items():
            row = self.dep_table.rowCount()
            self.dep_table.insertRow(row)
            self.dep_table.setItem(row, 0, QTableWidgetItem(key))
            self.dep_table.setItem(row, 1, QTableWidgetItem(value))
        torch = environment.inspect_torch(
            environment.resolve_python(comfy, self.window.config.python_path_override)
        )
        self.torch_label.setText(
            f"PyTorch：{torch.torch}，CUDA：{torch.cuda}，设备：{torch.device}"
            if torch.installed
            else f"PyTorch：未安装或不可用（{torch.detail}）"
        )

    def create_venv(self) -> None:
        comfy = self.window.comfy_dir()
        self.window.run_commands(create_venv_commands(comfy, command_environment(self.window.config)), "创建 .venv")

    def reinstall_package(self) -> None:
        package = self.package_edit.text().strip()
        if not package:
            QMessageBox.warning(self, "缺少包名", "请输入需要重装的 Python 包名。")
            return
        self.window.run_commands(
            [reinstall_package_command(self.window.comfy_dir(), package, command_environment(self.window.config))],
            f"重装 {package}",
        )


class VersionPage(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)
        header_layout.addWidget(label("内核", 16, True))
        header_layout.addWidget(label("扩展", 16, True))
        header_layout.addWidget(label("安装新扩展", 16, True))
        header_layout.addStretch(1)
        refresh = QPushButton("刷新列表")
        refresh.clicked.connect(self.refresh)
        update = QPushButton("一键更新")
        update.clicked.connect(self.update_core)
        header_layout.addWidget(refresh)
        header_layout.addWidget(update)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_core_tab(), "内核")
        tabs.addTab(self._build_extensions_tab(), "扩展")
        tabs.addTab(self._build_install_tab(), "安装新扩展")
        layout.addWidget(tabs, 1)

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

        branch_row = QHBoxLayout()
        self.branch_combo = QComboBox()
        switch_branch = QPushButton("切换分支")
        switch_branch.clicked.connect(self.checkout_branch)
        fetch = QPushButton("拉取远端信息")
        fetch.clicked.connect(self.fetch_core)
        branch_row.addWidget(label("分支"))
        branch_row.addWidget(self.branch_combo, 1)
        branch_row.addWidget(switch_branch)
        branch_row.addWidget(fetch)
        root.addLayout(branch_row)

        self.commit_table = QTableWidget(0, 5)
        self.commit_table.setHorizontalHeaderLabels(["版本 ID", "提交信息", "日期", "当前", "操作"])
        self.commit_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.commit_table.verticalHeader().setVisible(False)
        self.commit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.commit_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.commit_table, 1)
        return page

    def _build_extensions_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 24, 24, 24)
        self.extension_table = QTableWidget(0, 5)
        self.extension_table.setHorizontalHeaderLabels(["扩展名", "分支", "版本", "状态", "路径"])
        self.extension_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.extension_table.verticalHeader().setVisible(False)
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
        manager_card = card()
        manager_layout = QHBoxLayout(manager_card)
        self.manager_label = label("ComfyUI-Manager：未检测")
        manager_button = QPushButton("安装或更新 ComfyUI-Manager")
        manager_button.clicked.connect(self.window.install_or_update_manager)
        manager_layout.addWidget(self.manager_label, 1)
        manager_layout.addWidget(manager_button)
        root.addWidget(manager_card)

        url_card = card()
        url_layout = QHBoxLayout(url_card)
        self.plugin_url = QLineEdit()
        self.plugin_url.setPlaceholderText("扩展 Git URL，例如 https://github.com/user/node-pack.git")
        install = QPushButton("安装")
        install.clicked.connect(self.install_plugin)
        url_layout.addWidget(label("扩展 URL"))
        url_layout.addWidget(self.plugin_url, 1)
        url_layout.addWidget(install)
        root.addWidget(url_card)

        help_text = QTextBrowser()
        help_text.setMarkdown(
            "### 插件管理策略\n\n"
            "- 首选安装 ComfyUI-Manager，让它负责完整插件生态。\n"
            "- 这里额外保留 Git URL 安装，用于安装未进入 Manager 列表的自定义节点。\n"
            "- 所有扩展默认安装到 `custom_nodes`。"
        )
        root.addWidget(help_text, 1)
        return page

    def refresh(self) -> None:
        self.refresh_core()
        self.refresh_extensions()

    def refresh_core(self) -> None:
        comfy = self.window.comfy_dir()
        self.commit_table.setRowCount(0)
        self.branch_combo.clear()
        if not (comfy / ".git").exists():
            self.remote_label.setText("远程地址：未检测到 ComfyUI Git 仓库")
            self.branch_label.setText("当前分支：-")
            self.commit_label.setText("当前版本：-")
            return
        try:
            git = GitService(comfy)
            self.remote_label.setText(f"远程地址：{git.remote_url()}")
            self.branch_label.setText(f"当前分支：{git.current_branch()}")
            current = git.current_commit()
            self.commit_label.setText(f"当前版本：{current}")
            for branch in git.branches():
                self.branch_combo.addItem(branch)
            index = self.branch_combo.findText(git.current_branch())
            if index >= 0:
                self.branch_combo.setCurrentIndex(index)
            for item in git.commits():
                row = self.commit_table.rowCount()
                self.commit_table.insertRow(row)
                self.commit_table.setItem(row, 0, QTableWidgetItem(item.short_hash))
                self.commit_table.setItem(row, 1, QTableWidgetItem(item.subject))
                self.commit_table.setItem(row, 2, QTableWidgetItem(item.date))
                self.commit_table.setItem(row, 3, QTableWidgetItem("是" if item.current else ""))
                button = QPushButton("当前" if item.current else "切换")
                button.setEnabled(not item.current)
                button.clicked.connect(lambda _=False, rev=item.full_hash: self.checkout_revision(rev))
                self.commit_table.setCellWidget(row, 4, button)
        except GitError as exc:
            self.remote_label.setText(f"远程地址：读取失败：{exc}")

    def refresh_extensions(self) -> None:
        custom_nodes = self.window.comfy_dir() / "custom_nodes"
        self.extension_table.setRowCount(0)
        manager = custom_nodes / "ComfyUI-Manager"
        self.manager_label.setText("ComfyUI-Manager：已安装" if manager.exists() else "ComfyUI-Manager：未安装")
        if not custom_nodes.exists():
            return
        for child in sorted((p for p in custom_nodes.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
            row = self.extension_table.rowCount()
            self.extension_table.insertRow(row)
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

    def fetch_core(self) -> None:
        self.window.run_commands(
            [CommandSpec(["git", "fetch", "--all", "--tags", "--prune"], cwd=self.window.comfy_dir())],
            "拉取远端信息",
        )

    def update_core(self) -> None:
        try:
            git = GitService(self.window.comfy_dir())
            if git.is_dirty():
                raise DirtyRepositoryError("检测到未提交的本地修改，已阻止更新")
        except (DirtyRepositoryError, GitError) as exc:
            QMessageBox.warning(self, "已阻止", str(exc))
            self.window.append_log(f"更新 ComfyUI 已阻止：{exc}\n")
            return
        self.window.run_commands(
            [CommandSpec(["git", "pull", "--ff-only"], cwd=self.window.comfy_dir())],
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

    def install_plugin(self) -> None:
        url = self.plugin_url.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少 URL", "请输入扩展 Git URL。")
            return
        ensure_dir(self.window.comfy_dir() / "custom_nodes")
        self.window.run_commands(
            [install_plugin_command(self.window.comfy_dir(), url, command_environment(self.window.config))],
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
            environment.resolve_python(comfy, self.window.config.python_path_override)
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        tabs.addTab(self._build_general(), "一般设置")
        tabs.addTab(self._build_about(), "关于")
        root.addWidget(tabs, 1)

    def _build_general(self) -> QWidget:
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

        self.project_path = QLineEdit(self.window.config.comfy_path)
        choose_project = QPushButton("选择")
        choose_project.clicked.connect(self.choose_project)
        root.addWidget(self.path_row("ComfyUI 目录", "选择已有项目或新建安装目标", self.project_path, choose_project))

        self.python_override = QLineEdit(self.window.config.python_path_override)
        self.python_override.setPlaceholderText("留空则使用项目 .venv/bin/python")
        choose_python = QPushButton("选择")
        choose_python.clicked.connect(self.choose_python)
        root.addWidget(self.path_row("Python 路径覆盖", "用于兼容已有 Python/venv，Git 路径覆盖已在 Linux 版删除", self.python_override, choose_python))

        self.http_proxy = QLineEdit(self.window.config.network.http_proxy)
        self.https_proxy = QLineEdit(self.window.config.network.https_proxy)
        self.pypi_mirror = QCheckBox("使用 PyPI 国内镜像")
        self.pypi_mirror.setChecked(self.window.config.network.pypi_mirror)
        proxy_card = card()
        proxy_layout = QGridLayout(proxy_card)
        proxy_layout.setContentsMargins(18, 18, 18, 18)
        proxy_layout.addWidget(label("代理设置", 16, True), 0, 0, 1, 2)
        proxy_layout.addWidget(label("HTTP_PROXY"), 1, 0)
        proxy_layout.addWidget(self.http_proxy, 1, 1)
        proxy_layout.addWidget(label("HTTPS_PROXY"), 2, 0)
        proxy_layout.addWidget(self.https_proxy, 2, 1)
        proxy_layout.addWidget(self.pypi_mirror, 3, 0, 1, 2)
        root.addWidget(proxy_card)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("保存设置")
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        root.addLayout(buttons)
        root.addStretch(1)
        return page

    def _build_about(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 28, 28, 28)
        browser = QTextBrowser()
        browser.setMarkdown(bundled_markdown("about.md"))
        root.addWidget(browser, 1)
        return page

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

    def choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 目录", self.project_path.text())
        if path:
            self.project_path.setText(path)

    def choose_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 Python 可执行文件", str(Path.home()))
        if path:
            self.python_override.setText(path)

    def save(self) -> None:
        self.window.config.comfy_path = self.project_path.text().strip() or str(default_comfy_dir())
        self.window.config.python_path_override = self.python_override.text().strip()
        self.window.config.language = self.language_combo.currentData()
        self.window.config.expert_mode = bool(self.mode_combo.currentData())
        self.window.config.network.http_proxy = self.http_proxy.text().strip()
        self.window.config.network.https_proxy = self.https_proxy.text().strip()
        self.window.config.network.pypi_mirror = self.pypi_mirror.isChecked()
        self.window.save_config()
        self.window.refresh_pages()
        QMessageBox.information(self, "已保存", "设置已保存。")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.translator = Translator(self.config.language)
        self.process = ComfyProcess()
        self.current_task: TaskHandle | None = None
        self.nav_buttons: dict[str, QPushButton] = {}
        self.pages: dict[str, QWidget] = {}

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.process.output.connect(self.append_log)
        self.process.state_changed.connect(self.on_process_state)
        self.process.finished.connect(lambda _code: self.refresh_pages())
        self.rebuild()

    def comfy_dir(self) -> Path:
        return Path(self.config.comfy_path).expanduser()

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
        title_layout.setContentsMargins(18, 8, 18, 8)
        title_layout.addWidget(label(f"{APP_NAME} {__version__}", 17, True))
        title_layout.addStretch(1)
        theme_button = QPushButton("灯泡")
        theme_button.setObjectName("flat")
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
        side.setFixedWidth(108)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(8, 18, 8, 18)
        layout.setSpacing(10)
        group = QButtonGroup(side)
        group.setExclusive(True)
        items = [
            ("launch", "一键启动"),
            ("advanced", "高级选项"),
            ("versions", "版本管理"),
            ("tools", "小工具"),
            ("console", "控制台"),
            ("settings", "设置"),
        ]
        for name, text in items:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumHeight(58)
            button.clicked.connect(lambda _=False, n=name: self.show_page(n))
            self.nav_buttons[name] = button
            group.addButton(button)
            layout.addWidget(button)
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
        for page in self.pages.values():
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
        self.config_store.save(self.config)
        self.show_page("console")
        self.process.start(self.config)

    def stop_comfy(self) -> None:
        self.process.stop()

    def run_commands(self, commands: list[CommandSpec], title: str) -> None:
        if self.current_task is not None:
            QMessageBox.warning(self, "任务进行中", "已有后台任务正在运行，请等待完成。")
            return
        self.show_page("console")
        self.append_log(f"\n== {title} ==\n")
        handle = TaskHandle(commands)
        self.current_task = handle
        handle.output.connect(self.append_log)
        handle.finished.connect(self.on_task_finished)
        handle.start()

    def on_task_finished(self, ok: bool, message: str) -> None:
        self.append_log(f"\n== {'完成' if ok else '失败'}：{message} ==\n")
        self.current_task = None
        self.refresh_pages()

    def run_git_action(self, action, title: str) -> None:
        comfy = self.comfy_dir()
        try:
            action(GitService(comfy))
        except DirtyRepositoryError as exc:
            QMessageBox.warning(self, "已阻止", str(exc))
            self.append_log(f"{title} 已阻止：{exc}\n")
            return
        except GitError as exc:
            QMessageBox.warning(self, "Git 失败", str(exc))
            self.append_log(f"{title} 失败：{exc}\n")
            return
        self.append_log(f"{title} 完成。\n")
        self.refresh_pages()

    def install_or_update_manager(self) -> None:
        comfy = self.comfy_dir()
        custom_nodes = ensure_dir(comfy / "custom_nodes")
        manager = custom_nodes / "ComfyUI-Manager"
        if manager.exists():
            commands = [CommandSpec(["git", "pull", "--ff-only"], cwd=manager)]
        else:
            commands = install_manager_commands(comfy, command_environment(self.config))
        self.run_commands(commands, "安装或更新 ComfyUI-Manager")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.process.is_running():
            result = QMessageBox.question(self, "退出", "ComfyUI 仍在运行，是否终止进程并退出？")
            if result != QMessageBox.Yes:
                event.ignore()
                return
            self.process.stop()
        event.accept()
