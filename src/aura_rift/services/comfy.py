from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from aura_rift.config import AppConfig, COMFY_REPO_URL, MANAGER_REPO_URL
from aura_rift.services.environment import (
    VenvManager,
    _resolve_override,
    conda_env_name,
    normalize_venv_manager,
    resolve_python,
    venv_pip,
    venv_python,
)
from aura_rift.services.tasks import CommandSpec


_CLI_FLAG_RE = re.compile(r"add_argument\(\s*['\"](--[A-Za-z0-9][A-Za-z0-9_-]*)")


def supported_cli_flags(comfy_path: Path) -> set[str] | None:
    """Read the selected ComfyUI CLI flag names when the parser is local.

    ComfyUI evolves faster than the launcher.  A persisted expert option may
    be valid in one checkout but unknown to another, which otherwise makes
    ``argparse`` abort before the server starts.  The parser source is a
    cheap, read-only capability probe; ``None`` means the checkout uses a
    different layout and the caller should preserve the generated argv.
    """
    candidates = (
        Path(comfy_path) / "comfy" / "cli_args.py",
        Path(comfy_path) / "comfy" / "cli_args" / "__init__.py",
    )
    for candidate in candidates:
        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        flags = set(_CLI_FLAG_RE.findall(source))
        if flags:
            return flags
    return None


def filter_cli_args(
    args: list[str],
    supported_flags: set[str] | None = None,
    blocked_flags: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Drop generated options unknown to a detected ComfyUI parser.

    Values belonging to a removed option are skipped until the next option
    token.  This handles both ordinary ``--flag value`` pairs and options
    such as ``--whitelist-custom-nodes`` that consume several values.  The
    returned second list contains unique dropped flag names for a visible log.
    """
    blocked = {str(flag).split("=", 1)[0] for flag in (blocked_flags or set())}
    if not supported_flags and not blocked:
        return list(args), []
    result: list[str] = []
    dropped: list[str] = []
    index = 0
    while index < len(args):
        token = str(args[index])
        if token.startswith("--"):
            flag = token.split("=", 1)[0]
            if flag in blocked or (
                supported_flags is not None and flag not in supported_flags
            ):
                if flag not in dropped:
                    dropped.append(flag)
                index += 1
                while index < len(args) and not str(args[index]).startswith("--"):
                    index += 1
                continue
        result.append(args[index])
        index += 1
    return result, dropped


def platform_blocked_cli_flags() -> set[str]:
    """Return options that are unsafe for the current Linux edition.

    DirectML is a Windows-only backend.  Keep the migrated config field and
    its serialization for compatibility, but never pass a stale ``--directml``
    setting to a Linux ComfyUI process where ``torch-directml`` is unavailable.
    """
    return {"--directml"} if sys.platform.startswith("linux") else set()


class ComfyProcess(QObject):
    output = Signal(str)
    state_changed = Signal(str)
    finished = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.started.connect(lambda: self.state_changed.emit("运行中"))
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)

    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning

    def start(self, config: AppConfig) -> None:
        comfy_path = Path(config.comfy_path).expanduser()
        main_py = comfy_path / "main.py"
        if self.is_running():
            self.output.emit("\033[33mComfyUI 已在运行。\033[0m\n")
            return
        if not main_py.exists():
            self.output.emit("\033[1;31m未找到 main.py，请先选择或安装 ComfyUI。\033[0m\n")
            self.state_changed.emit("路径错误")
            return
        python = str(resolve_python(comfy_path, config.python_path_override, config.venv_manager))
        launch_args = config.launch.to_args()
        # The cache switches are mutually exclusive in ComfyUI's argparse
        # group.  Avoid emitting a second ``--cache-ram`` when the compact
        # launch option selected classic/LRU/none caching.
        try:
            full_args = config.full.to_args(
                cache_strategy=config.launch.cache_strategy,
                cuda_malloc=config.launch.cuda_malloc,
                vram_mode=config.launch.vram_mode,
                attention=config.launch.attention,
                text_enc_precision=config.launch.text_enc_precision,
            )
        except TypeError:
            # A few third-party integrations provide a legacy FullOptions
            # adapter whose ``to_args`` accepts no keyword arguments.  Keep
            # that public shape usable; the built-in dataclass takes the
            # richer path above.
            full_args = config.full.to_args()
        supported = supported_cli_flags(comfy_path)
        blocked = platform_blocked_cli_flags()
        launch_args, launch_unsupported = filter_cli_args(
            launch_args, supported, blocked_flags=blocked
        )
        full_args, unsupported = filter_cli_args(
            full_args, supported, blocked_flags=blocked
        )
        unsupported = [*launch_unsupported, *unsupported]
        args = [str(main_py), *launch_args, *full_args]
        env = QProcessEnvironment.systemEnvironment()
        for key, value in command_environment(config, scope="environment").items():
            env.insert(key, value)
        # Model mirrors/offline mode are a separate settings scope.  ComfyUI
        # performs most model downloads inside its own process, so carry the
        # model-specific variables into QProcess even when the general
        # environment proxy switch is disabled.  HTTP(S)_PROXY is intentionally
        # left to the environment scope to avoid silently proxying every
        # request made by the server.
        model_env = command_environment(config, scope="models")
        for key in (
            "HF_ENDPOINT", "AURA_RIFT_MODEL_SERVER", "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE", "HTTP_PROXY", "HTTPS_PROXY",
            "http_proxy", "https_proxy",
        ):
            if key in model_env:
                env.insert(key, model_env[key])
        # Don't let the launcher's own virtualenv leak into ComfyUI's process.
        env.remove("VIRTUAL_ENV")
        env.remove("PYTHONHOME")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(comfy_path))
        if unsupported:
            self.output.emit(
                "\033[33m已忽略当前 ComfyUI 不支持的专家参数："
                + ", ".join(unsupported)
                + "\033[0m\n"
            )
        self.output.emit(f"\033[2m$ {python} {' '.join(args)}\033[0m\n")
        self.process.start(python, args)

    def stop(self) -> None:
        if not self.is_running():
            self.output.emit("\033[33m当前没有运行中的 ComfyUI 进程。\033[0m\n")
            return
        self.process.terminate()
        if not self.process.waitForFinished(3000):
            self.output.emit("普通终止超时，正在强制结束进程。\n")
            self.process.kill()

    def _read(self) -> None:
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if data:
            self.output.emit(data)

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self.state_changed.emit("未运行")
        self.finished.emit(code)
        if code == 0:
            self.output.emit("\033[2m\n进程已正常退出。\033[0m\n")
        else:
            self.output.emit(f"\033[1;31m\n进程已退出，退出码：{code}\033[0m\n")

    def _error(self, error) -> None:
        if error == QProcess.FailedToStart:
            detail = self.process.errorString() or "无法启动进程"
            self.state_changed.emit("启动失败")
            self.output.emit(f"\033[1;31m启动 ComfyUI 失败：{detail}\033[0m\n")


def _github_url(url: str, config: AppConfig) -> str:
    """Apply github mirror prefix if configured."""
    proxy = (config.network.git_mirror or config.network.github_proxy).strip()
    if (
        getattr(config, "proxy_enabled", False)
        and getattr(config, "proxy_for_git", True)
        and proxy
    ):
        # A catalog mirror is a repository-to-repository rule, not a generic
        # proxy prefix.  Resolve it first; the fallback supports the historic
        # ``https://mirror.example/`` convention.
        try:
            from aura_rift.services.catalog import LauncherCatalog

            # Resolve mirrors from the same explicit catalog/comfy checkout as
            # the UI.  Passing only ``catalog_path`` would silently fall back
            # to the repository bundle for installations that keep data.json
            # under ``<comfy_path>/.launcher``.
            catalog = LauncherCatalog.from_config(config)
            resolved = catalog.resolve_git_url(url, proxy)
            if resolved != url:
                return resolved
        except Exception:
            pass
        if url.startswith("https://github.com/"):
            # The legacy GitHub proxy setting accepts both
            # ``https://proxy.example`` and ``https://proxy.example/``.
            generic = config.network.github_proxy.strip()
            if generic and generic.startswith(("http://", "https://")):
                return generic.rstrip("/") + "/" + url
            # A git_mirror without a matching catalog rule is only safe as a
            # generic host prefix when it has no repository path.  A full
            # destination such as ``.../hanamizuki/comfyui`` must remain
            # unchanged rather than producing an invalid concatenation.
            if proxy.startswith(("http://", "https://")):
                path = urlsplit(proxy).path.strip("/")
                if not path or proxy.endswith("/"):
                    return proxy.rstrip("/") + "/" + url
    return url


def install_comfy_commands(
    target: Path,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    parent = target.parent
    cfg = config or AppConfig()
    supplied_env = env is not None
    if env is None:
        env = command_environment(cfg, scope="git")
    manager = cfg.venv_manager or "venv"
    repo_url = _github_url(COMFY_REPO_URL, cfg)
    commands = [
        CommandSpec(["git", "clone", repo_url, str(target)], cwd=parent, env=env, title="克隆 ComfyUI"),
    ]
    # A caller-provided environment is deliberately reused for compatibility;
    # otherwise dependency installation gets the Pip-specific proxy scope.
    pip_env = env if supplied_env else command_environment(cfg, scope="pip")
    # ``target`` does not exist until the clone command above has completed.
    # Do not inspect requirements/lock files while constructing this list:
    # doing so used to produce a clone-only install on a fresh checkout.  The
    # project install path is deliberately generated in "post clone" mode;
    # every command below runs after the clone and can therefore address the
    # files shipped by ComfyUI.
    commands.extend(_create_env_commands(
        target,
        manager,
        pip_env,
        assume_project_files=True,
        python_override=cfg.python_path_override,
    ))
    # The list is intentionally useful to callers that only want to preview
    # the install (and preserves the historical ``commands[1]`` contract),
    # but the task worker can replace the fallback stages after ``git clone``
    # has completed.  At that point lock files are visible and Conda/uv can
    # faithfully use ``environment.yml``/``uv.lock`` instead of a generic
    # requirements fallback.
    if len(commands) > 1:
        first_install = commands[1]
        first_install.expand = lambda: _create_env_commands(
            target,
            manager,
            pip_env,
            assume_project_files=False,
            python_override=cfg.python_path_override,
        )
        first_install.replace_remaining = True
    return commands


def _create_env_commands(
    target: Path,
    manager: str,
    env: dict[str, str],
    *,
    assume_project_files: bool = False,
    python_override: str = "",
) -> list[CommandSpec]:
    """Build environment creation/install commands.

    ``assume_project_files`` is used by :func:`install_comfy_commands`: the
    commands are assembled before ``git clone`` runs, so checking the target
    filesystem at construction time is inherently racy.  Existing-project
    maintenance keeps the conservative existence checks by default.
    """
    mgr = normalize_venv_manager(manager)
    venv_python_path = venv_python(target)
    venv_pip_path = venv_pip(target)
    requirements = target / "requirements.txt"

    # Resolve an explicitly configured interpreter once while constructing the
    # task list. A directory override (for example
    # ``~/miniconda/envs/foo``) is converted to its platform-specific Python
    # executable; malformed overrides are ignored so the selected manager can
    # fall back to its normal discovery behavior. This matters for fresh
    # installs because the target ``.venv`` does not exist yet.
    override_python = ""
    if str(python_override or "").strip():
        resolved_override = _resolve_override(str(python_override).strip())
        if str(resolved_override):
            override_python = str(resolved_override)

    if mgr == VenvManager.POETRY:
        has_pyproject = (target / "pyproject.toml").exists()
        if not has_pyproject and not assume_project_files:
            # A user may select Poetry for an older/custom checkout that has
            # no pyproject at all.  ``poetry run`` cannot bootstrap such a
            # tree, so use the deterministic stdlib venv fallback instead of
            # failing after a successful clone.
            return _create_env_commands(
                target, VenvManager.VENV, env,
                python_override=python_override,
            )
        commands = [
            *(
                [CommandSpec(
                    ["poetry", "env", "use", override_python],
                    cwd=target, env=env, title="选择 Poetry Python",
                )]
                if override_python and (has_pyproject or assume_project_files) else []
            ),
            # Keep Poetry's own project/lock handling, then install ComfyUI's
            # canonical requirements file.  The upstream ComfyUI pyproject
            # intentionally contains metadata only, so ``poetry install`` by
            # itself would otherwise leave torch and the server dependencies
            # absent. ``--no-root`` is needed because ComfyUI is an application
            # checkout rather than an installable Poetry package.
            *(
                [CommandSpec(["poetry", "install", "--no-root"], cwd=target, env=env, title="Poetry install")]
                if has_pyproject or assume_project_files else []
            ),
        ]
        if requirements.exists() or assume_project_files:
            commands.append(CommandSpec(
                ["poetry", "run", "pip", "install", "-r", "requirements.txt"],
                cwd=target, env=env, title="安装 ComfyUI 依赖"))
        return commands
    if mgr == VenvManager.PDM:
        has_pyproject = (target / "pyproject.toml").exists()
        if not has_pyproject and not assume_project_files:
            return _create_env_commands(
                target, VenvManager.VENV, env,
                python_override=python_override,
            )
        commands = [
            *(
                [CommandSpec(
                    ["pdm", "use", override_python],
                    cwd=target, env=env, title="选择 PDM Python",
                )]
                if override_python and (has_pyproject or assume_project_files) else []
            ),
            # ComfyUI is an application checkout, not a distributable PDM
            # package. ``--no-self`` keeps PDM from trying to build a package
            # that is not declared in the upstream pyproject.
            *(
                [CommandSpec(["pdm", "install", "--no-self"], cwd=target, env=env, title="PDM install")]
                if has_pyproject or assume_project_files else []
            ),
        ]
        if requirements.exists() or assume_project_files:
            commands.append(CommandSpec(
                ["pdm", "run", "pip", "install", "-r", "requirements.txt"],
                cwd=target, env=env, title="安装 ComfyUI 依赖"))
        return commands
    if mgr == VenvManager.UV:
        uv_args = ["uv", "venv"]
        if override_python:
            # Without this, uv may choose the launcher process's interpreter
            # (or another PATH default) and silently ignore the override.
            uv_args.extend(["--python", override_python])
        uv_args.append(str(target / ".venv"))
        commands = [
            CommandSpec(uv_args, cwd=target, env=env, title="uv venv"),
        ]
        uv_lock = target / "uv.lock"
        if uv_lock.exists() and not assume_project_files:
            commands.append(CommandSpec(
                ["uv", "sync", "--python", str(venv_python_path)],
                cwd=target, env=env, title="uv sync"))
        elif requirements.exists() or assume_project_files:
            commands.append(CommandSpec(
                ["uv", "pip", "install", "--python", str(venv_python_path), "-r", "requirements.txt"],
                cwd=target, env=env, title="安装 ComfyUI 依赖"))
        return commands
    if mgr == VenvManager.CONDA:
        env_file = target / "environment.yml"
        env_name = conda_env_name(target)
        if env_file.exists() and not assume_project_files:
            commands = [
                CommandSpec(["conda", "env", "create", "-f", "environment.yml"], cwd=target, env=env, title="conda env create"),
            ]
            if requirements.exists():
                commands.append(CommandSpec(
                    ["conda", "run", "-n", env_name or target.name, "pip", "install", "-r", "requirements.txt"],
                    cwd=target, env=env, title="安装 ComfyUI 依赖"))
            return commands
        name = env_name or target.name
        commands = [
            CommandSpec(["conda", "create", "-n", name, "python=3.11", "-y"], cwd=target, env=env, title="conda create"),
        ]
        if requirements.exists() or assume_project_files:
            commands.append(
                CommandSpec(["conda", "run", "-n", name, "pip", "install", "-r", "requirements.txt"], cwd=target, env=env, title="安装 ComfyUI 依赖")
            )
        return commands

    # stdlib venv (default)
    # A valid override is preferred for creating the project environment.  If
    # it is absent/invalid, resolve_python falls back to the launcher Python;
    # that is still deterministic and avoids relying on a PATH ``python3``
    # which may not exist on minimal distributions.
    bootstrap_python = str(resolve_python(target, python_override, VenvManager.VENV))
    # A frozen/PyInstaller launcher cannot be invoked as ``<launcher> -m
    # venv``.  Prefer a real system interpreter for the bootstrap step while
    # retaining the deterministic ``sys.executable`` behavior for normal
    # source launches and environments where no system Python is discoverable.
    if (
        getattr(sys, "frozen", False)
        and not str(python_override or "").strip()
        and bootstrap_python == str(sys.executable)
    ):
        bootstrap_python = shutil.which("python3") or shutil.which("python") or bootstrap_python
    commands = [
        CommandSpec([bootstrap_python, "-m", "venv", str(target / ".venv")], cwd=target.parent, env=env, title="创建项目虚拟环境"),
        CommandSpec([str(venv_python_path), "-m", "pip", "install", "--upgrade", "pip"], cwd=target, env=env, title="升级 pip"),
    ]
    if requirements.exists() or assume_project_files:
        commands.append(
            CommandSpec([str(venv_pip_path), "install", "-r", "requirements.txt"], cwd=target, env=env, title="安装 ComfyUI 依赖")
        )
    return commands


def install_manager_commands(
    comfy_path: Path,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="git")
    custom_nodes = comfy_path / "custom_nodes"
    manager_path = custom_nodes / "ComfyUI-Manager"
    repo_url = _github_url(MANAGER_REPO_URL, cfg)
    return [
        CommandSpec(["git", "clone", repo_url, str(manager_path)], cwd=custom_nodes, env=env, title="安装 ComfyUI-Manager"),
    ]


def create_venv_commands(
    comfy_path: Path,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="pip")
    manager = cfg.venv_manager or "venv"
    return _create_env_commands(
        comfy_path,
        manager,
        env,
        python_override=cfg.python_path_override,
    )


def reinstall_package_command(
    comfy_path: Path,
    package_name: str,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> CommandSpec:
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="pip")
    manager = cfg.venv_manager or "venv"
    mgr = normalize_venv_manager(manager)

    if mgr == VenvManager.CONDA:
        env_name = conda_env_name(comfy_path) or comfy_path.name
        return CommandSpec(
            ["conda", "run", "-n", env_name, "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )
    if mgr == VenvManager.POETRY:
        return CommandSpec(
            ["poetry", "run", "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )
    if mgr == VenvManager.PDM:
        return CommandSpec(
            ["pdm", "run", "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )
    if mgr == VenvManager.UV:
        # Keep uv tied to the ComfyUI project environment.  If the venv has
        # not been created yet, point at its deterministic future path rather
        # than allowing uv to discover Aura-Rift's own virtualenv from PATH.
        uv_python = venv_python(comfy_path)
        if not uv_python.exists():
            override = str(cfg.python_path_override or "").strip()
            if override:
                resolved_override = _resolve_override(override)
                if str(resolved_override):
                    uv_python = Path(str(resolved_override))
        return CommandSpec(
            ["uv", "pip", "install", "--python", str(uv_python), "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )

    # stdlib venv (default)
    pip = venv_pip(comfy_path)
    if pip.exists():
        pip_args = [str(pip)]
    else:
        # Never fall back to a bare ``pip`` lookup: PATH may point at the
        # launcher's environment (or no pip at all).  ``-m pip`` keeps the
        # selected interpreter and package installation target explicit.
        python = resolve_python(comfy_path, cfg.python_path_override, mgr)
        pip_args = [str(python), "-m", "pip"]
    return CommandSpec(
        [*pip_args, "install", "--upgrade", "--force-reinstall", package_name],
        cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
    )


def install_requirements_command(
    comfy_path: Path,
    requirements_file: Path,
    config: "AppConfig | None" = None,
    env: dict[str, str] | None = None,
    *,
    force_reinstall: bool = False,
) -> CommandSpec:
    """Build a single `pip install -r <file>` command for the configured venv manager."""
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="pip")
    manager = cfg.venv_manager or "venv"
    mgr = normalize_venv_manager(manager)
    title = requirements_file.parent.name if requirements_file.parent.name and requirements_file.parent != comfy_path else "ComfyUI"
    label = f"安装缺失依赖：{title}"
    install_flags = ["--upgrade", "--force-reinstall"] if force_reinstall else []

    if mgr == VenvManager.CONDA:
        env_name = conda_env_name(comfy_path) or comfy_path.name
        return CommandSpec(
            ["conda", "run", "-n", env_name, "pip", "install", *install_flags, "-r", str(requirements_file)],
            cwd=comfy_path, env=env, title=label,
        )
    if mgr == VenvManager.POETRY:
        return CommandSpec(
            ["poetry", "run", "pip", "install", *install_flags, "-r", str(requirements_file)],
            cwd=comfy_path, env=env, title=label,
        )
    if mgr == VenvManager.PDM:
        return CommandSpec(
            ["pdm", "run", "pip", "install", *install_flags, "-r", str(requirements_file)],
            cwd=comfy_path, env=env, title=label,
        )
    if mgr == VenvManager.UV:
        # Explicitly target the ComfyUI venv's interpreter so uv never
        # resolves to the launcher's own venv via VIRTUAL_ENV or parent-crawl.
        python = venv_python(comfy_path)
        if not python.exists() and cfg.python_path_override:
            resolved_override = _resolve_override(cfg.python_path_override)
            if str(resolved_override):
                python = Path(str(resolved_override))
        cmd = ["uv", "pip", "install", "--python", str(python), *install_flags, "-r", str(requirements_file)]
        return CommandSpec(cmd, cwd=comfy_path, env=env, title=label)

    pip = venv_pip(comfy_path)
    if pip.exists():
        pip_args = [str(pip)]
    else:
        python = resolve_python(comfy_path, cfg.python_path_override, mgr)
        pip_args = [str(python), "-m", "pip"]
    return CommandSpec(
        [*pip_args, "install", *install_flags, "-r", str(requirements_file)],
        cwd=comfy_path, env=env, title=label,
    )


def reinstall_requirements_commands(
    comfy_path: Path,
    files: list[Path],
    config: "AppConfig | None" = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    """Build visible force-reinstall tasks for core and extension requirements."""
    if not files:
        return []
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="pip")
    return [
        install_requirements_command(comfy_path, file, cfg, env, force_reinstall=True)
        for file in files
    ]


def install_missing_deps_commands(
    comfy_path: Path,
    files: list[Path],
    config: "AppConfig | None" = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    """One pip install -r command per missing requirements file."""
    if not files:
        return []
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="pip")
    return [
        install_requirements_command(comfy_path, f, cfg, env)
        for f in files
    ]


def install_plugin_command(
    comfy_path: Path,
    url: str,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> CommandSpec:
    cfg = config or AppConfig()
    if env is None:
        env = command_environment(cfg, scope="git")
    custom_nodes = comfy_path / "custom_nodes"
    raw_url = str(url).strip()
    if not raw_url:
        raise ValueError("扩展 Git URL 不能为空")
    parsed = urlsplit(raw_url)
    # Accept HTTPS/HTTP and the common SCP-like GitHub form, but never let a
    # query/path fragment turn into an arbitrary custom_nodes destination.
    if parsed.scheme and parsed.scheme not in {"http", "https", "ssh", "git"}:
        raise ValueError("仅支持 HTTP(S)/SSH Git URL")
    name_source = raw_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    name = name_source.rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or name in {".", ".."}:
        raise ValueError("扩展 URL 的仓库名无效")
    clone_url = _github_url(url, cfg)
    return CommandSpec(
        ["git", "clone", clone_url, str(custom_nodes / name)],
        cwd=custom_nodes, env=env, title=f"安装扩展：{name}",
    )


def command_environment(config: AppConfig, *, scope: str = "environment") -> dict[str, str]:
    # The master switch is intentionally strict.  AppConfig migration turns
    # on this flag for old files that only contained legacy network fields, so
    # disabling it in the new UI cannot accidentally keep injecting proxies.
    apply_proxy = bool(getattr(config, "proxy_enabled", False))
    scope = str(scope or "environment").strip().lower()
    if scope not in {"environment", "git", "pip", "models"}:
        raise ValueError(f"未知代理作用域：{scope}")
    enabled_by_scope = {
        "environment": getattr(config, "proxy_for_environment", True),
        "git": getattr(config, "proxy_for_git", True),
        "pip": getattr(config, "proxy_for_pip", True),
        "models": getattr(config, "proxy_for_models", True),
    }
    scoped = apply_proxy and bool(enabled_by_scope[scope])
    if not scoped:
        # Preserve the historical API contract: an inactive scope returns an
        # empty overlay, while subprocess callers still inherit their normal
        # process environment from ``os.environ``/QProcess.
        if getattr(config, "huggingface_offline", False) and scope in {"environment", "models"}:
            return {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        return {}
    env = config.network.environment()
    if getattr(config, "huggingface_offline", False) and scope in {"environment", "models"}:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    if scoped and scope == "pip" and config.network.pypi_mirror:
        env["PIP_INDEX_URL"] = config.network.pypi_mirror
        env["UV_INDEX_URL"] = config.network.pypi_mirror
    if scoped and scope == "models":
        if config.network.hf_mirror:
            env["HF_ENDPOINT"] = config.network.hf_mirror
        if config.network.model_server:
            env["AURA_RIFT_MODEL_SERVER"] = config.network.model_server
    # ``GITHUB_PROXY`` is a generic prefix consumed by a few extensions.  A
    # catalog ``git_mirror`` destination is handled as an explicit Git
    # insteadOf rule/clone URL and must not be exposed as a generic prefix.
    github_proxy = config.network.github_proxy.strip()
    if scoped and scope == "git" and github_proxy:
        env["GITHUB_PROXY"] = github_proxy
    return env


def model_download_environment(config: AppConfig) -> dict[str, str]:
    """Return the environment intended for model downloads.

    Model downloaders are hosted by ComfyUI/extensions rather than by the
    launcher itself, but exposing this small helper gives those integrations a
    single, correctly gated implementation of the fourth proxy switch.
    """
    return command_environment(config, scope="models")


__all__ = [
    "ComfyProcess",
    "command_environment",
    "create_venv_commands",
    "filter_cli_args",
    "install_comfy_commands",
    "install_manager_commands",
    "install_missing_deps_commands",
    "install_plugin_command",
    "install_requirements_command",
    "model_download_environment",
    "reinstall_requirements_commands",
    "reinstall_package_command",
    "platform_blocked_cli_flags",
    "supported_cli_flags",
]
