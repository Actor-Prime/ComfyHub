# -*- coding: utf-8 -*-
"""模型目录解析：内置 models/ + ComfyUI 的 extra_model_paths.yaml 挂载目录

ComfyUI 支持用 extra_model_paths.yaml 把外部盘符的模型库挂进来（秋葉整合包常见），
格式大致是两层缩进：

    my_d_models:
        base_path: D:/ComfyUI-Models
        checkpoints: checkpoints          # 相对 base_path
        text_encoders: E:/other/te        # 也可以是绝对路径
        is_default: true

ComfyHub 之前只认 <comfyui_path>/models，用户在别的盘挂的模型在模型库页完全看不到
（2026-11-07 用户报障：ComfyUI 里能认出十几个模型，启动器只显示 1 个 checkpoint）。

这里用纯标准库做极简 YAML 解析（不为此引入 PyYAML），把挂载目录并进扫描范围。
"""
import os
import re

_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\|^/")
# 这些 key 不是模型类别，别当目录挂
_SKIP_KEYS = {"base_path", "is_default", "custom_nodes", "download_model_base"}


def _clean(v: str) -> str:
    return v.strip().strip("'").strip('"')


def parse_extra_paths(path: str) -> dict:
    """解析 extra_model_paths.yaml → {段名: {key: value}}

    只认「顶层段名 + 一层缩进子键」这种结构，注释和空行忽略。
    解析失败一律返回空 dict（不能因为一个坏文件把模型库搞崩）。
    """
    out = {}
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return out
    cur = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, val = line.strip().partition(":")
        key, val = _clean(key), _clean(val)
        if not key:
            continue
        if indent == 0:
            cur = key
            out.setdefault(cur, {})
        elif cur is not None and val:
            out[cur][key] = val
    return out


def _config_files(comfyui_path: str) -> list:
    """ComfyUI 可能认的几处配置文件（启动参数指定的那种我们读不到，只能扫默认位置）"""
    return [
        os.path.join(comfyui_path, "extra_model_paths.yaml"),
        os.path.join(comfyui_path, "extra_model_paths.yml"),
        os.path.join(comfyui_path, "user", "default", "extra_model_paths.yaml"),
    ]


def extra_roots(comfyui_path: str) -> list:
    """外部挂载的模型根目录（去重后，用于 UI 提示「外部模型库 N 处」）"""
    if not comfyui_path:
        return []
    roots = []
    for cfg in _config_files(comfyui_path):
        for _sec, kv in parse_extra_paths(cfg).items():
            base = kv.get("base_path") or comfyui_path
            base = os.path.normpath(base.replace("/", os.sep))
            if os.path.isdir(base) and base not in roots:
                roots.append(base)
    return roots


def extra_model_dirs(comfyui_path: str) -> dict:
    """{类别: [绝对目录...]} —— 只收真实存在的目录"""
    out = {}
    if not comfyui_path:
        return out
    for cfg in _config_files(comfyui_path):
        for _sec, kv in parse_extra_paths(cfg).items():
            base = kv.get("base_path") or comfyui_path
            base = base.replace("/", os.sep)
            for key, val in kv.items():
                if key in _SKIP_KEYS or not val:
                    continue
                p = val.replace("/", os.sep)
                if not _ABS_RE.match(val):
                    p = os.path.join(base, p)
                p = os.path.normpath(p)
                if os.path.isdir(p):
                    lst = out.setdefault(key, [])
                    if p not in lst:
                        lst.append(p)
    return out


def model_root(comfyui_path: str, models_dir: str = "") -> str:
    """模型根目录：实例设置里的「模型目录」优先，空则回落 <comfyui_path>/models

    2026-11-07：设置页那一栏原来只写进 inst["models_dir"] 却没人读（摆设），
    ComfyUI 启动参数也用不上它；现在三处（启动命令 / 模型库扫描 / AI 工具）统一走这里。
    """
    md = (models_dir or "").strip()
    if md:
        return os.path.normpath(md)
    cp = (comfyui_path or "").strip()
    return os.path.join(cp, "models") if cp else ""


def model_dirs(comfyui_path: str, models_dir: str = "") -> dict:
    """{类别: [目录...]}：模型根下的类别在前，extra_model_paths 挂载接在后面"""
    out = {}
    base = model_root(comfyui_path, models_dir)
    if base and os.path.isdir(base):
        try:
            for cat in sorted(os.listdir(base)):
                p = os.path.join(base, cat)
                if os.path.isdir(p):
                    out.setdefault(cat, []).append(p)
        except OSError:
            pass
    for cat, dirs in extra_model_dirs(comfyui_path).items():
        lst = out.setdefault(cat, [])
        for d in dirs:
            if d not in lst:
                lst.append(d)
    return out


def dirs_for(comfyui_path: str, cat: str, models_dir: str = "") -> list:
    """某类别要扫的所有目录（模型根 + 外部挂载）。unet 与 diffusion_models 在
    ComfyUI 里是同一类的旧/新名，互相兜一层，避免两边看到的数量对不上。"""
    all_dirs = model_dirs(comfyui_path, models_dir)
    out = list(all_dirs.get(cat, []))
    alias = {"unet": "diffusion_models", "diffusion_models": "unet"}.get(cat)
    if alias:
        for d in all_dirs.get(alias, []):
            if d not in out:
                out.append(d)
    return out
