from __future__ import annotations


ZH_CN: dict[str, str] = {
    "app.title": "Aura-Rift 启动器",
    "nav.launch": "一键启动",
    "nav.advanced": "高级选项",
    "nav.versions": "版本管理",
    "nav.tools": "小工具",
    "nav.console": "控制台",
    "nav.settings": "设置",
    "nav.theme": "灯泡",
    "button.start": "一键启动",
    "button.stop": "终止进程",
    "button.install": "安装",
    "button.update": "更新",
    "button.refresh": "刷新",
    "button.choose": "选择",
    "button.save": "保存",
}


class Translator:
    def __init__(self, language: str = "zh_CN") -> None:
        self.language = language
        self._catalogs = {"zh_CN": ZH_CN}

    def tr(self, key: str, default: str | None = None) -> str:
        return self._catalogs.get(self.language, ZH_CN).get(key, default or key)

