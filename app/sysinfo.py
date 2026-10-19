# -*- coding: utf-8 -*-
"""硬件/系统信息（对标管理大师 os.js：CPU / 内存 / 显卡 / 磁盘）"""
import os
import shutil

import psutil

from app.winsub import run


def cpu_info() -> dict:
    return {
        "name": os.environ.get("PROCESSOR_IDENTIFIER", "未知").split(",")[0].strip()
                or "未知",
        "cores_physical": psutil.cpu_count(logical=False) or 0,
        "cores_logical": psutil.cpu_count() or 0,
        # 非阻塞：返回自上次调用以来的占用率（首次为 0），不冻结 UI
        "usage": psutil.cpu_percent(interval=None),
    }


def ram_info() -> dict:
    vm = psutil.virtual_memory()
    return {
        "total_gb": vm.total / 1024 ** 3,
        "used_gb": (vm.total - vm.available) / 1024 ** 3,
        "percent": vm.percent,
    }


def gpu_info_display() -> str:
    """复用 bootstrap 的检测，返回人类可读文本"""
    try:
        from app.bootstrap import detect_gpu
        g = detect_gpu()
        return g.label
    except Exception as e:
        return f"检测失败: {e}"


def disk_info(drive: str = "F:\\") -> dict:
    """指定盘的容量信息（默认关注 F 盘，ComfyUI 所在盘）"""
    try:
        u = shutil.disk_usage(drive)
        return {
            "drive": drive,
            "total_gb": u.total / 1024 ** 3,
            "free_gb": u.free / 1024 ** 3,
            "percent": (u.used / u.total) * 100 if u.total else 0,
        }
    except Exception:
        return {"drive": drive, "total_gb": 0, "free_gb": 0, "percent": 0}


# 环境摘要缓存：同一 python_path 只探测一次（torch 导入需数秒，禁止反复同步调用）
_ENV_CACHE = {}


def python_env_summary(python_path: str) -> dict:
    """实例 Python 环境摘要：版本 / torch / CUDA（带缓存）"""
    if python_path in _ENV_CACHE:
        return _ENV_CACHE[python_path]
    out = {"python": "?", "torch": "未安装", "cuda": "未安装"}
    if not python_path or not os.path.isfile(python_path):
        return out
    try:
        probe = (
            "import sys; print(sys.version.split()[0])\n"
            "try:\n"
            "    import torch; print(torch.__version__)\n"
            "    print('CUDA可用' if torch.cuda.is_available() else '仅CPU')\n"
            "except Exception:\n"
            "    print('未安装'); print('未安装')\n"
        )
        r = run(
            [python_path, "-c", probe],
            capture_output=True, text=True, timeout=60)
        lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
        # torch 缺失时第二段 import 会抛错，此时至少拿到 python 版本
        if lines:
            out["python"] = lines[0]
        if len(lines) >= 3:
            out["torch"], out["cuda"] = lines[1], lines[2]
    except Exception:
        pass
    _ENV_CACHE[python_path] = out
    return out
