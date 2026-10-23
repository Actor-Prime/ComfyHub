APP_VERSION = "0.5.85"

# -*- coding: utf-8 -*-
"""配置管理：多实例配置持久化 + 自动探测本机 ComfyUI"""
import json
import os
import sys


def app_config_dir() -> str:
    """配置目录：%APPDATA%\\ComfyHub；打包单文件模式下也稳定"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ComfyHub")
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_PATH = os.path.join(app_config_dir(), "config.json")

VRAM_MODES = {
    "自动": "",
    "低显存": "--lowvram",
    "中显存": "--medvram",
    "高显存": "--highvram",
    "禁用显存优化": "--novram",
    "CPU 模式": "--cpu",
}

# 常见 ComfyUI 安装位置（用于自动探测）
_PROBE_ROOTS = [
    r"F:\ComfyUI",
    r"E:\ComfyUI",
    r"D:\ComfyUI",
    r"C:\ComfyUI",
    os.path.expanduser(r"~\Desktop\ComfyUI"),
    r"F:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI",
    r"D:\AI\ComfyUI",
]


def _find_python_for(comfyui_path: str) -> str:
    """在 ComfyUI 目录附近探测配套 Python 解释器"""
    candidates = [
        # ComfyUI 旁边的独立 venv（如 F:\ComfyUI_env）
        r"F:\ComfyUI_env\Scripts\python.exe",
        # 便携包内置环境
        os.path.join(comfyui_path, "python_embeded", "python.exe"),
        os.path.join(comfyui_path, "..", "python_embeded", "python.exe"),
        os.path.join(comfyui_path, ".venv", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "..", ".venv", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "..", "ComfyUI_env", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "venv", "Scripts", "python.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return sys.executable  # 兜底：当前解释器


def default_instance() -> dict:
    """生成默认实例（探测本机安装）"""
    inst = {
        "id": "inst_default",
        "name": "默认实例",
        "comfyui_path": "",
        "python_path": "",
        "port": 8188,
        "vram_mode": "自动",
        "extra_args": "",
        "auto_open_browser": True,
    }
    for root in _PROBE_ROOTS:
        if os.path.isfile(os.path.join(root, "main.py")):
            inst["comfyui_path"] = os.path.normpath(root)
            inst["python_path"] = _find_python_for(root)
            break
    return inst


def load_config() -> dict:
    cfg = {
        "version": 1,
        "instances": [],
        "last_instance_id": "",
    }
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass  # 配置损坏时回退默认，不崩溃
    # 用户删光实例后保持为空（显示"（无实例）"占位），不再自动生成
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)  # 原子写，防断电损坏


def is_comfyui_dir(path: str) -> bool:
    """是否为 ComfyUI 实例目录：有 main.py 且具备 ComfyUI 特征文件。
    仅看 main.py 会误判（任何 Python 项目都可能有 main.py，例如本启动器自身）。"""
    import os
    if not os.path.isfile(os.path.join(path, "main.py")):
        return False
    # ComfyUI 三个标志性文件/目录，命中任意一个即认定为 ComfyUI
    return (os.path.isdir(os.path.join(path, "comfy"))
            or os.path.isfile(os.path.join(path, "nodes.py"))
            or os.path.isfile(os.path.join(path, "folder_paths.py")))
