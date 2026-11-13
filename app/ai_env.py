# -*- coding: utf-8 -*-
"""环境画像（2026-11-13）

解决什么问题：AI 每次排查都要先调一次 get_health 才敢下结论，等于把
MAX_ROUNDS=8 里的 1/8 浪费在"拿基本信息"上。把本机的**慢变量**直接写进
system prompt，第一轮就能判断（典型：磁盘只剩 8GB 的"生成失败"根本不用查节点）。

⚠️ 稳定性优先于实时性（这条比"信息全"重要）：
DeepSeek / 通义 / 智谱 的上下文缓存都按「前缀命中」打折（约 0.1x）。system prompt
正是前缀。如果往里塞每分钟会变的值（显存占用、精确磁盘余量、当前时间），
前缀就一直在变，缓存全废——省下的那点信息量抵不上多付的 prefill 钱。
所以这里只收慢变量，并且：
  · 进程内缓存 TTL=300s（同一会话连续提问拿到**逐字相同**的字符串）；
  · 磁盘余量按 10GB 台阶量化（写成「约 120GB」），1GB 的波动不改前缀；
  · 显存只写总量不写实时占用 —— 要实时值让模型自己调 get_health。

任何一项采集失败都只是少一行，绝不抛异常（不能因为查不到显卡就让助手用不了）。
"""
import os
import time

_TTL = 300.0                 # 画像缓存时长（秒）
_CACHE = {}                  # (comfyui_path, python_path) -> (ts, text)


def _gpu_text() -> str:
    """显卡型号 + 显存总量（慢变量）"""
    try:
        from app.process import detect_gpus
        gpus = detect_gpus()
        if not gpus:
            return ""
        bits = []
        for g in gpus[:2]:
            bits.append(f"{g.get('name') or '未知显卡'} "
                        f"{round((g.get('vram_mib') or 0) / 1024)}GB")
        if len(gpus) > 2:
            bits.append(f"等共 {len(gpus)} 张")
        return "、".join(bits)
    except Exception:
        return ""


def _torch_text(python_exe: str) -> str:
    """torch 版本与构建变体（只读 version.py，不 import torch）"""
    if not python_exe or not os.path.isfile(python_exe):
        return ""
    vp = os.path.join(os.path.dirname(os.path.abspath(python_exe)),
                      "Lib", "site-packages", "torch", "version.py")
    if not os.path.isfile(vp):
        return ""
    ver, cuda = "", ""
    try:
        with open(vp, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip().replace(" ", "")
                if s.startswith("__version__=") and not ver:
                    ver = s.split("=", 1)[-1].strip().strip("\"'")
                elif not cuda and (s.startswith("cuda=") or s.startswith("cuda:")):
                    v = s.split("=", 1)[-1].strip().strip("\"'")
                    cuda = "" if v in ("None", "") else v
    except Exception:
        return ""
    if not ver:
        return ""
    if cuda:
        return f"torch {ver}（CUDA {cuda}）"
    return f"torch {ver}（CPU 版）"


def _comfy_version(root: str) -> str:
    """ComfyUI 版本（读目录里的 comfyui_version.py，取不到就算了）"""
    if not root:
        return ""
    p = os.path.join(root, "comfyui_version.py")
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().startswith("__version__"):
                    v = line.split("=", 1)[-1].strip().strip("\"'")
                    return f"ComfyUI {v}" if v else ""
    except Exception:
        pass
    return ""


def _disk_text(path: str) -> str:
    """ComfyUI 所在盘的剩余空间（量化到 10GB，避免小波动改前缀）"""
    if not path:
        return ""
    try:
        import shutil
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        target = (drive + os.sep) if drive else path
        free = shutil.disk_usage(target).free / 1024 ** 3
        label = (drive[0] + " 盘") if drive else target
        if free < 20:
            return f"{label}仅剩 {free:.1f}GB（告急，大文件下载/出图可能失败）"
        return f"{label}约剩 {int(free // 10) * 10}GB"
    except Exception:
        return ""


def _crash_text() -> str:
    """最近 7 天内的最后一次崩溃摘要（过期不提，免得误导）"""
    try:
        from app.paths import data_root
        p = os.path.join(data_root(), "crash.log")
        if not os.path.isfile(p):
            return ""
        if time.time() - os.path.getmtime(p) > 7 * 86400:
            return ""
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4000))
            raw = f.read().decode("utf-8", "ignore")
        lines = [x.strip() for x in raw.splitlines() if x.strip()]
        # 去掉纯分隔线（crash.log 每段以 "-----"/"=====" 收尾，直接取末行只会拿到线）
        lines = [x for x in lines
                 if not (len(x) > 8 and set(x) <= set("=-_─ "))]
        if not lines:
            return ""
        pick = ""
        for x in reversed(lines[-40:]):
            low = x.lower()
            if "error" in low or "exception" in low:
                pick = x
                break
        pick = pick or lines[-1]
        return pick[:120]
    except Exception:
        return ""


def profile_text(cfg: dict, inst: dict) -> str:
    """组装「本机环境」段落；全空返回空串（调用方直接不拼）"""
    try:
        inst = inst or {}
        root = str(inst.get("comfyui_path") or "")
        py = str(inst.get("python_path") or "")
        if not py:
            # 实例没配解释器时，回落到默认共享 Python（向导默认就填这个）
            try:
                from app.paths import data_root
                cand = os.path.join(data_root(), "tools", "python", "python.exe")
                py = cand if os.path.isfile(cand) else ""
            except Exception:
                py = ""
        key = (root, py)
        now = time.time()
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]

        bits = []
        gpu = _gpu_text()
        if gpu:
            bits.append("显卡：" + gpu)
        tj = _torch_text(py)
        if tj:
            bits.append(tj)
        cv = _comfy_version(root)
        if cv:
            bits.append(cv)
        dk = _disk_text(root)
        if dk:
            bits.append("磁盘：" + dk)
        cr = _crash_text()
        if cr:
            bits.append("最近崩溃：" + cr)
        if not bits:
            _CACHE[key] = (now, "")
            return ""
        text = ("【本机环境】（启动时自动采集，可能略有滞后；要实时数据请调 get_health）\n"
                + "；".join(bits) + "\n")
        _CACHE[key] = (now, text)
        return text
    except Exception:
        return ""


def clear_cache() -> None:
    """测试/切换实例后强制重采"""
    _CACHE.clear()
