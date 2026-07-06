# Aura-Rift

Aura-Rift 是一个面向 Linux 的 ComfyUI 启动器，界面参考 ComfyUI-Aki 的信息结构，但首版以实用功能为主。

## 首版功能

- 选择已有 ComfyUI 目录，或新建安装 ComfyUI
- 使用项目内 `.venv` 管理 Python 环境
- 一键启动、终止进程、控制台实时日志
- 打开根目录、`custom_nodes`、`input`、`output`、`models`
- 本地 `announcement.md` 控制公告内容
- 本地 `about.md` 控制关于页内容
- ComfyUI 分支/提交查看与切换
- 扩展目录查看、更新，以及 Git URL 安装
- 安装或更新 ComfyUI-Manager
- 高级启动参数、Python 路径覆盖、代理和 PyPI 镜像设置
- 深色/浅色主题切换

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python -m aura_rift
```

也可以安装后直接运行：

```bash
aura-rift
```

## 说明

- Linux 下优先使用 `xdg-open` 打开文件夹；如果桌面文件管理器不可用，会回退到内置文件浏览器。
- 版本切换遇到未提交的本地修改会被阻止，避免覆盖用户改动。
- `Git 路径覆盖` 和 Windows 专用组件管理已移除。
- `环境修复`、`补丁管理`、`疑难解答` 首版没有做成独立页面，只保留可靠的环境检测、`.venv` 创建和单包重装。
