# -*- coding: utf-8 -*-
"""模拟核心链路（不经过 UI 事件循环）：
  源码安装 → 依赖安装 → 可运行性验证 → 启动实例 → HTTP 探活
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import bootstrap

TARGET = r"F:\_sim\ComfyUI"
PY = os.path.join(os.environ["APPDATA"], "ComfyHub", "tools", "python", "python.exe")

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

print("=" * 60, flush=True)
print("模拟：全新实例安装 → 运行", flush=True)
print(f"目标: {TARGET}", flush=True)
print(f"解释器: {PY} (存在: {os.path.isfile(PY)})", flush=True)
print("=" * 60, flush=True)

# 步骤 1：Python 检测
status, info, payload = bootstrap.detect_python_step(None, bootstrap.tools_root())
log(f"步骤1 检测: {status} | {info[:70]}")

# 步骤 2：源码
status, info, payload = bootstrap.detect_source_step(TARGET)
log(f"步骤2 检测: {status} | {info[:70]}")
if status != "ok":
    log("步骤2 安装：下载源码…")
    t0 = time.time()
    real = bootstrap.install_comfyui_source(TARGET, log=log)
    log(f"步骤2 完成: {real} ({time.time()-t0:.0f}s)")
    log("步骤2 附带 ComfyUI-Manager…")
    bootstrap.install_comfyui_manager(real, log=log)

# 步骤 3：依赖
status, info, payload = bootstrap.detect_requirements_step(PY, TARGET)
log(f"步骤3 检测: {status} | {info[:70]}")
if status != "ok":
    log("步骤3 安装：pip 装依赖（共享 Python 已有缓存，通常很快）…")
    t0 = time.time()
    ok = bootstrap.install_comfyui_requirements(PY, TARGET, log=log)
    log(f"步骤3 完成: {ok} ({time.time()-t0:.0f}s)")

# 步骤 4：torch（共享 Python 已装 → 应直接 ok）
status, info, payload = bootstrap.detect_torch_step(PY)
log(f"步骤4 检测: {status} | {info[:70]}")

# 最终验证
log("最终验证：verify_comfyui_runnable…")
t0 = time.time()
ok, info = bootstrap.verify_comfyui_runnable(PY, TARGET)
log(f"验证结果: ok={ok} | {str(info)[:120]} ({time.time()-t0:.0f}s)")

print("\n" + "=" * 60, flush=True)
print(f"安装链路: {'✅ 通过' if ok else '❌ 失败'}", flush=True)
print(f"  ComfyUI 目录: {TARGET}", flush=True)
print(f"  main.py: {os.path.isfile(os.path.join(TARGET, 'main.py'))}", flush=True)
print(f"  custom_nodes/ComfyUI-Manager: {os.path.isdir(os.path.join(TARGET, 'custom_nodes', 'ComfyUI-Manager'))}", flush=True)
sys.exit(0 if ok else 1)
