# -*- coding: utf-8 -*-
"""硬件/系统信息（对标管理大师 os.js：CPU / 内存 / 显卡 / 磁盘）"""
import os
import shutil
import time

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


# 显存占用缓存：nvidia-smi 启动开销 ~200ms，禁止每秒 spawn（2026-11-10 工作台显存卡）
_VRAM_CACHE = {"ts": 0.0, "data": {}}


def gpu_vram_usage(max_age: float = 3.0) -> dict:
    """N 卡显存实时占用。返回 {used_mb, total_mb, percent}，无 N 卡/查询失败返回 {}。
    结果缓存 max_age 秒：工作台每秒刷新不能带起 nvidia-smi 进程风暴。"""
    now = time.time()
    if now - _VRAM_CACHE["ts"] < max_age:
        return _VRAM_CACHE["data"]
    data = {}
    try:
        r = run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
        # 多卡取第一行（主显示卡）；输出形如 "2148, 12288"
        line = (r.stdout or "").strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        used_mb, total_mb = float(parts[0]), float(parts[1])
        if total_mb > 0:
            data = {"used_mb": used_mb, "total_mb": total_mb,
                    "percent": used_mb / total_mb * 100}
    except Exception:
        pass
    _VRAM_CACHE["ts"] = now
    _VRAM_CACHE["data"] = data
    return data


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
