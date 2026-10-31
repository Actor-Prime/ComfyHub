# -*- coding: utf-8 -*-
"""内核快照管理：ComfyUI 源码树按版本存为只读快照（零内核修改）。

设计（2026-10-31 启动模式体系）：
  · 快照 = 纯源码树副本 <tools_root>/kernels/<version>/，不含 custom_nodes/
    （插件归 profile）、不含 models/output 等数据目录（归共享/实例）
  · 元数据注册表 <tools_root>/kernels/index.json：
      {version: {path, python_path, source, added, base_dir_flag}}
  · python_path 只登记不复制（共享 venv / 外部解释器按引用走）
  · base_dir_flag：该内核是否支持 --base-directory（检测 comfy/cli_args.py），
    决定启动模式走官方基座还是 junction 换头
"""
import json
import os
import re
import shutil
import time

# 复制源码树时跳过：数据目录/插件/虚拟环境/版本库/缓存
_COPY_IGNORE = shutil.ignore_patterns(
    "custom_nodes", "models", "output", "input", "temp", "user",
    ".git", "__pycache__", "*.pyc", "venv", ".venv", "ComfyUI_env",
    "python_embeded", "python", "*.part", "web/extensions-core")


def kernels_root() -> str:
    from app.bootstrap import tools_root
    d = os.path.join(tools_root(), "kernels")
    os.makedirs(d, exist_ok=True)
    return d


def _index_path() -> str:
    return os.path.join(kernels_root(), "index.json")


def _load_index() -> dict:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_index(idx: dict) -> None:
    p = _index_path()
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    os.replace(p + ".tmp", p)


def detect_base_dir_flag(kernel_path: str) -> bool:
    """内核是否支持 --base-directory（扫描 comfy/cli_args.py，不实际启动）"""
    f = os.path.join(kernel_path, "comfy", "cli_args.py")
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            return "--base-directory" in fh.read(200_000)
    except OSError:
        return False


def kernel_dir(version: str) -> str:
    return os.path.join(kernels_root(), version)


def list_kernels() -> list:
    """返回注册表内全部内核（按入库时间倒序），目录丢失的标记 missing"""
    idx = _load_index()
    out = []
    for ver, meta in idx.items():
        p = meta.get("path") or kernel_dir(ver)
        m = dict(meta)
        m["version"] = ver
        m["missing"] = not os.path.isfile(os.path.join(p, "main.py"))
        out.append(m)
    out.sort(key=lambda x: x.get("added", ""), reverse=True)
    return out


def get_kernel(version: str) -> dict | None:
    for k in list_kernels():
        if k["version"] == version:
            return k
    return None


def _unique_version(base: str) -> str:
    idx = _load_index()
    v = base
    n = 2
    while v in idx or os.path.isdir(kernel_dir(v)):
        v = f"{base}_{n}"
        n += 1
    return v


def _default_version() -> str:
    return "k" + time.strftime("%Y%m%d") + "-" + os.urandom(2).hex()


def register(version: str, path: str, python_path: str, source: str) -> dict:
    idx = _load_index()
    meta = {
        "path": os.path.normpath(path),
        "python_path": os.path.normpath(python_path) if python_path else "",
        "source": source,
        "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_dir_flag": detect_base_dir_flag(path),
    }
    idx[version] = meta
    _save_index(idx)
    return {"version": version, **meta}


def import_from_instance(comfyui_path: str, python_path: str,
                         version: str | None = None, log=print) -> dict:
    """把现有 ComfyUI 安装收编为内核快照（复制纯源码树）"""
    from app.config import is_comfyui_dir
    if not is_comfyui_dir(comfyui_path):
        raise ValueError(f"不是 ComfyUI 目录：{comfyui_path}")
    ver = version or _default_version()
    ver = _unique_version(ver)
    dest = kernel_dir(ver)
    log(f"[内核] 复制源码树 {comfyui_path} → {dest}（不含 custom_nodes/models）")
    shutil.copytree(comfyui_path, dest, ignore=_COPY_IGNORE)
    return register(ver, dest, python_path, source=f"instance:{comfyui_path}")


def import_from_zip(zip_path: str, python_path: str,
                    version: str | None = None, log=print) -> dict:
    """从 ComfyUI 源码 zip 导入内核快照（向导下载的 ComfyUI_xxx_src.zip 同源）"""
    import zipfile
    if not os.path.isfile(zip_path):
        raise ValueError(f"zip 不存在：{zip_path}")
    ver = version or _default_version()
    ver = _unique_version(ver)
    dest = kernel_dir(ver)
    os.makedirs(dest, exist_ok=True)
    log(f"[内核] 解压 {zip_path} → {dest}")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    # zip 内可能套一层顶层目录（ComfyUI-xxx/），上提到内核根
    entries = [e for e in os.listdir(dest) if not e.startswith(".")]
    if len(entries) == 1:
        top = os.path.join(dest, entries[0])
        if os.path.isdir(top) and os.path.isfile(os.path.join(top, "main.py")):
            for e in os.listdir(top):
                shutil.move(os.path.join(top, e), os.path.join(dest, e))
            os.rmdir(top)
    if not os.path.isfile(os.path.join(dest, "main.py")):
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError("zip 里没找到 main.py，不是 ComfyUI 源码包")
    return register(ver, dest, python_path, source=f"zip:{os.path.basename(zip_path)}")


def remove_kernel(version: str, log=print) -> bool:
    """删除内核快照（源码树 + 注册项）。只删 kernels_root 下的受管目录。"""
    idx = _load_index()
    meta = idx.get(version)
    if not meta:
        return False
    p = os.path.normpath(meta.get("path") or kernel_dir(version))
    root = os.path.normpath(kernels_root())
    if not p.startswith(root):
        log(f"[内核] 拒绝删除注册表外路径：{p}")
        return False
    shutil.rmtree(p, ignore_errors=True)
    del idx[version]
    _save_index(idx)
    log(f"[内核] 已删除内核快照 {version}")
    return True
