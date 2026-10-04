# Aura-Rift

Aura-Rift 是一个面向 Linux 的 ComfyUI 启动器，按绘世 2.9.2 的信息层级、深色视觉 token 和操作路径重做，保留 Aura-Rift 品牌与 Linux 原生行为。

## 功能

- 无边框标题栏、侧栏导航和响应式滚动布局（基准 2107×1317，兼容 1280×800）
- 一键启动、终止进程、控制台实时日志，以及启动命令预览/复制
- 选择已有 ComfyUI 目录，或从启动页提示安装 ComfyUI
- 使用项目内 `.venv` 管理 Python 环境
- 打开根目录、`custom_nodes`、`input`、`output`、`models`
- 本地 `announcement.md` 控制公告内容
- 本地 `about.md` 控制关于页内容
- 高级选项、环境维护、补丁管理、疑难解答、版本管理、小工具、控制台和设置页面
- PyTorch/xFormers、依赖修复、原生 Git/FFmpeg/CMake/Ninja 命令生成，以及安全的单包重装
- Linux 热补丁清单、文件/hash 校验、专用备份和幂等撤销；不支持的补丁只读展示原因
- 路径/Python/Git/requirements/重复扩展/依赖状态扫描，并提供安全的目录修复动作
- ComfyUI 分支/提交查看与切换
- 扩展目录查看、更新，以及 Git URL 安装
- 安装或更新 ComfyUI-Manager
- 高级启动参数、Python 路径覆盖、代理作用域、PyPI/Git/HuggingFace 镜像和扩展列表设置
- 启动前依赖检查：一键启动时自动核对 ComfyUI 本体 `requirements.txt` 与所有 `custom_nodes/*/requirements.txt`，缺失的依赖给用户选择「安装并启动」/「直接启动」/「取消」；选择安装时按当前 venv 管理器（venv/poetry/pdm/uv/conda）跑 `pip install -r`，装完自动启动；未建 .venv 时跳过检查直接启动
- 内置 `launcher_data.json`、资源字体/图标和公告/关于 fallback；不依赖 Windows DLL
- 深色/浅色主题切换；不包含交流群、诊断包、Visual Studio Build Tools、Git 路径覆盖或 Windows 控件

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
apt install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1 libxkbcommon-x11-0
python -m aura_rift
```

也可以安装后直接运行：

```bash
aura-rift
```

## 字体安装（用于控制台显示 emoji 与等宽日志）

启动器自带控制台会清洗子进程输出（剥离 ANSI 转义、折叠进度条、把箭头/几何字符转 ASCII、emoji 转文字标签），所以即使不装字体也能看到干净日志。

但若想在控制台看到原生的彩色 emoji 和更美观的等宽字体，建议安装以下字体（可二选一或全部安装）。

### 1）彩色 emoji 字体

Debian / Ubuntu：

```bash
sudo apt update
sudo apt install fonts-noto-color-emoji
```

Fedora：

```bash
sudo dnf install google-noto-color-emoji-fonts
```

Arch：

```bash
sudo pacman -S noto-fonts-emoji
```

openSUSE：

```bash
sudo zypper install noto-color-emoji-font
```

安装后刷新字体缓存：

```bash
fc-cache -fv
```

用 `fc-match emoji` 验证，应输出 `NotoColorEmoji.ttf: "Noto Color Emoji"`。

### 2）等宽字体（可选，控制台更好看）

Debian / Ubuntu：

```bash
sudo apt install fonts-jetbrains-mono          # 或 fonts-hack
```

Fedora：

```bash
sudo dnf install jetbrains-mono-fonts-all
```

Arch：

```bash
sudo pacman -S ttf-jetbrains-mono
```

安装后刷新缓存：

```bash
fc-cache -fv
```

### 备注

- 控制台主题里写死了 `JetBrains Mono` -> `Cascadia Code` -> `Consolas` -> 默认等宽字体回退顺序；装了 `fonts-jetbrains-mono` 就直接生效，没装则自动回退。
- emoji / 箭头 / box-drawing 字体的显示是**自动适配**的：启动器会在每次运行时检测系统里是否存在 emoji 字体（`fonts-noto-color-emoji` 等）。**装了字体** → 控制台直接显示原生 emoji（如 🔥 →、箭头 →），彩色且原样保留；**没装字体** → 自动回退为 ASCII 替用 `[fire]`、`->` 等文字标签，避免缺字符方块（tofu）。这种切换无需重启启动器，首次刷新控制台即生效。
- ANSI 颜色转义码会被解析为控制台文字颜色和样式；无法渲染的控制字符会被安全清洗。
- 仓库要求清单中已包含 `libxkbcommon-x11-0` 等 Qt 运行依赖；字体为可选增强，不影响启动器基本运行。

## 说明

- Linux 下优先使用 `xdg-open` 打开文件夹；如果桌面文件管理器不可用，会回退到内置文件浏览器。
- 版本切换遇到未提交的本地修改会被阻止，避免覆盖用户改动。
- `Git 路径覆盖` 和 Windows 专用组件管理已移除。
- 系统包安装/卸载只生成可见的 apt/dnf/pacman/zypper 终端命令，不在后台静默修改系统。
- 热补丁只有在存在 Linux 载荷且通过路径/hash 校验后才会写入 ComfyUI；应用失败会阻止继续启动并记录到控制台。
- `launcher_data.json` 支持绘世数据文件常见的尾部签名格式，仓库/用户目录下的 `announcement.md` 与 `about.md` 优先于内置资源。
