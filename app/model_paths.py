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

# 来源分层（2026-11-08 用户要求「实例模型库 / 外置模型库不能混淆」）
SRC_ALL = "all"          # 两者合并（历史行为，默认）
SRC_BUILTIN = "builtin"  # 实例内置：模型根（inst.models_dir 或 <comfyui>/models）
SRC_EXTRA = "extra"      # 外部挂载：extra_model_paths.yaml 指向的目录

# ComfyUI 标准类别子目录名（共享外挂库自动映射用）
STANDARD_CATS = (
    "checkpoints", "configs", "loras", "vae", "text_encoders", "clip",
    "diffusion_models", "unet", "clip_vision", "style_models", "embeddings",
    "diffusers", "vae_approx", "controlnet", "t2i_adapter", "gligen",
    "upscale_models", "hypernetworks", "photomaker", "model_patches",
    "audio_encoders",
)


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


def shared_config_path() -> str:
    """ComfyHub 托管的「共享外挂库」配置（2026-11-08，B 阶段）

    放在 ComfyHub 自己的配置目录，不污染各实例目录。开启共享外挂后由
    sync_shared_config() 生成，启动实例时用 --extra-model-paths-config 传进去。
    """
    from app.config import app_config_dir
    return os.path.join(app_config_dir(), "extra_model_paths_shared.yaml")


def instance_config_path(inst: dict) -> str:
    """单实例「挂载模型目录」的 yaml（ComfyHub 生成，启动时传给 ComfyUI）

    2026-11-08 用户要求：模型目录那一栏下面加「启用挂载模型目录」开关 + 目录选择，
    按实例各自挂载。文件落在 ComfyHub 配置目录下（不污染 ComfyUI 实例目录，
    免得覆盖用户自己写的 extra_model_paths.yaml）。
    """
    from app.config import app_config_dir
    inst = inst or {}
    iid = str(inst.get("id") or inst.get("name") or "default")
    iid = re.sub(r"[^0-9A-Za-z_.\-]", "_", iid) or "default"
    return os.path.join(app_config_dir(), "instances", iid,
                        "extra_model_paths.yaml")


def mount_root_of(inst: dict) -> str:
    """实例级挂载根目录（未启用 / 未选 / 目录不存在 → 空串）"""
    inst = inst or {}
    if not inst.get("mount_enabled"):
        return ""
    p = str(inst.get("mount_dir") or "").strip()
    if not p:
        return ""
    p = os.path.normpath(p.replace("/", os.sep))
    return p if os.path.isdir(p) else ""


def _mount_dirs(root: str) -> dict:
    """挂载根下的类别子目录 → {类别: [绝对目录]}

    只认 ComfyUI 标准类别名（外加 unet 旧名），避免把用户目录里的杂物当模型类别。
    """
    out = {}
    if not root or not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for cat in names:
        if cat not in STANDARD_CATS and cat != "unet":
            continue
        p = os.path.join(root, cat)
        if os.path.isdir(p):
            out.setdefault(cat, []).append(p)
    return out


def _write_mount_yaml(path: str, root: str, section: str, header: list) -> str:
    """把「一个挂载根」写成 extra_model_paths.yaml（类别自动映射，存在的才写）"""
    lines = list(header) + [f"{section}:",
                            f"    base_path: {root.replace(os.sep, '/')}"]
    for cat in STANDARD_CATS:
        if os.path.isdir(os.path.join(root, cat)):
            lines.append(f"    {cat}: {cat}")
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        return ""
    return path


def sync_instance_config(inst: dict, enabled=None, root=None) -> str:
    """写/删「单实例挂载」yaml，返回文件路径（未启用返回空串）

    每次启动顺手 sync 一次：开关改了没保存、目录被挪走时能自愈，不留僵尸挂载。
    """
    path = instance_config_path(inst)
    inst = inst or {}
    on = bool(inst.get("mount_enabled")) if enabled is None else bool(enabled)
    rt = (str(inst.get("mount_dir") or "") if root is None else str(root)).strip()
    rt = os.path.normpath(rt.replace("/", os.sep)) if rt else ""
    if not on or not rt or not os.path.isdir(rt):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        return ""
    return _write_mount_yaml(
        path, rt, "comfyhub_mount",
        ["# 由 ComfyHub 生成：本实例的挂载模型目录（实例设置里改，改这份文件会被覆盖）"])


def _config_files(comfyui_path: str) -> list:
    """ComfyUI 可能认的几处配置文件（启动参数指定的那种我们读不到，只能扫默认位置）"""
    files = [
        os.path.join(comfyui_path, "extra_model_paths.yaml"),
        os.path.join(comfyui_path, "extra_model_paths.yml"),
        os.path.join(comfyui_path, "user", "default", "extra_model_paths.yaml"),
    ]
    try:
        shared = shared_config_path()
        if os.path.isfile(shared):
            files.append(shared)
    except Exception:                       # 配置目录不可用也不能影响扫描
        pass
    return files


def sync_shared_config(root: str, enabled: bool = True) -> str:
    """写/删共享外挂配置，返回文件路径（未启用时返回空串）

    只配一个「挂载根」，类别按 ComfyUI 标准子目录名自动映射（存在的才写），
    避免让用户手填十几行映射（2026-11-08）。
    """
    path = shared_config_path()
    root = (root or "").strip()
    if not enabled or not root:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        return ""
    return _write_mount_yaml(
        path, os.path.normpath(root.replace("/", os.sep)), "comfyhub_shared",
        ["# 由 ComfyHub 生成：共享外挂模型库（所有实例通用）",
         "# 在 ComfyHub「系统设置 → 共享外挂模型库」里改，改这份文件会被覆盖"])


def _norm_root(mount_root: str) -> str:
    r = (mount_root or "").strip()
    return os.path.normpath(r.replace("/", os.sep)) if r else ""


def extra_roots(comfyui_path: str, mount_root: str = "") -> list:
    """外部挂载的模型根目录（去重后，用于 UI 提示「外部模型库 N 处」）

    mount_root：实例级挂载根（实例设置里的开关），没有就只算 yaml 挂载。
    """
    roots = []
    mr = _norm_root(mount_root)
    if mr and os.path.isdir(mr):
        roots.append(mr)
    if not comfyui_path:
        return roots
    for cfg in _config_files(comfyui_path):
        for _sec, kv in parse_extra_paths(cfg).items():
            base = kv.get("base_path") or comfyui_path
            base = os.path.normpath(base.replace("/", os.sep))
            if os.path.isdir(base) and base not in roots:
                roots.append(base)
    return roots


def extra_model_dirs_tagged(comfyui_path: str,
                            mount_root: str = "") -> dict:
    """{类别: [(绝对目录, 挂载根)]} —— 带来源信息，供 UI 区分「哪个盘挂进来的」

    mount_root：实例级挂载根（按标准类别子目录名映射，与 yaml 挂载合并去重）。
    """
    out = {}
    mr = _norm_root(mount_root)
    for cat, dirs in _mount_dirs(mr).items():
        lst = out.setdefault(cat, [])
        for d in dirs:
            if all(d != x for x, _b in lst):
                lst.append((d, mr))
    if not comfyui_path:
        return out
    for cfg in _config_files(comfyui_path):
        for _sec, kv in parse_extra_paths(cfg).items():
            base = kv.get("base_path") or comfyui_path
            base = base.replace("/", os.sep)
            base_abs = os.path.normpath(base if _ABS_RE.match(base)
                                        else os.path.join(comfyui_path, base))
            for key, val in kv.items():
                if key in _SKIP_KEYS or not val:
                    continue
                p = val.replace("/", os.sep)
                if not _ABS_RE.match(val):
                    p = os.path.join(base, p)
                p = os.path.normpath(p)
                if os.path.isdir(p):
                    lst = out.setdefault(key, [])
                    if all(p != d for d, _b in lst):
                        lst.append((p, base_abs))
    return out


def extra_model_dirs(comfyui_path: str, mount_root: str = "") -> dict:
    """{类别: [绝对目录...]} —— 只收真实存在的目录"""
    return {cat: [d for d, _b in items]
            for cat, items in extra_model_dirs_tagged(
                comfyui_path, mount_root).items()}


def builtin_model_dirs(comfyui_path: str, models_dir: str = "") -> dict:
    """{类别: [目录]}：只含实例模型根下的类别目录（不含任何外挂）"""
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


def _norm_source(source: str) -> str:
    s = (source or SRC_ALL).strip().lower()
    return s if s in (SRC_ALL, SRC_BUILTIN, SRC_EXTRA) else SRC_ALL


def model_dirs(comfyui_path: str, models_dir: str = "",
               source: str = SRC_ALL, mount_root: str = "") -> dict:
    """{类别: [目录...]}：模型根下的类别在前，extra_model_paths 挂载接在后面

    source 可限定只看实例内置（builtin）或只看外部挂载（extra）；
    mount_root 是实例级挂载根（实例设置里的「启用挂载模型目录」）。
    """
    src = _norm_source(source)
    out = {}
    if src in (SRC_ALL, SRC_BUILTIN):
        for cat, dirs in builtin_model_dirs(comfyui_path, models_dir).items():
            out.setdefault(cat, []).extend(dirs)
    if src in (SRC_ALL, SRC_EXTRA):
        for cat, dirs in extra_model_dirs(comfyui_path, mount_root).items():
            lst = out.setdefault(cat, [])
            for d in dirs:
                if d not in lst:
                    lst.append(d)
    return out


def dirs_for(comfyui_path: str, cat: str, models_dir: str = "",
             source: str = SRC_ALL, mount_root: str = "") -> list:
    """某类别要扫的所有目录（按 source 过滤）。unet 与 diffusion_models 在
    ComfyUI 里是同一类的旧/新名，互相兜一层，避免两边看到的数量对不上。"""
    all_dirs = model_dirs(comfyui_path, models_dir, source, mount_root)
    out = list(all_dirs.get(cat, []))
    alias = {"unet": "diffusion_models", "diffusion_models": "unet"}.get(cat)
    if alias:
        for d in all_dirs.get(alias, []):
            if d not in out:
                out.append(d)
    return out


def dirs_tagged(comfyui_path: str, cat: str, models_dir: str = "",
                source: str = SRC_ALL, mount_root: str = "") -> list:
    """某类别所有目录 + 来源：[(目录, 来源种类(builtin|extra), 来源标签路径)]

    来源标签给 UI 显示用：内置 → 模型根；外挂 → 挂载根（如 D:\\ComfyUI-Models）。
    """
    src = _norm_source(source)
    out = []
    if src in (SRC_ALL, SRC_BUILTIN):
        for d in builtin_model_dirs(comfyui_path, models_dir).get(cat, []):
            out.append((d, SRC_BUILTIN, model_root(comfyui_path, models_dir)))
    if src in (SRC_ALL, SRC_EXTRA):
        for d, base in extra_model_dirs_tagged(
                comfyui_path, mount_root).get(cat, []):
            out.append((d, SRC_EXTRA, base))
    alias = {"unet": "diffusion_models", "diffusion_models": "unet"}.get(cat)
    if alias:                                # 旧名/新名目录互兜（避免数量对不上）
        seen = {d for d, _k, _b in out}
        if src in (SRC_ALL, SRC_BUILTIN):
            for d in builtin_model_dirs(comfyui_path, models_dir).get(alias, []):
                if d not in seen:
                    out.append((d, SRC_BUILTIN,
                                model_root(comfyui_path, models_dir)))
        if src in (SRC_ALL, SRC_EXTRA):
            for d, base in extra_model_dirs_tagged(
                    comfyui_path, mount_root).get(alias, []):
                if d not in seen:
                    out.append((d, SRC_EXTRA, base))
    return out
