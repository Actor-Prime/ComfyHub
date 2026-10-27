# -*- coding: utf-8 -*-
"""端到端：配置端口被占 → 自动顺延 → 实际端口识别（真启动 ComfyUI）

用法: .venv_build/Scripts/python.exe tests/e2e_port_bump.py
注意：会真实启动一个 ComfyUI 实例（约 30~60 秒），不进 test_* 日常回归。
      自动跳过 auto_open_browser（不在用户桌面开浏览器）。
"""
import os
import socket
import sys
import time
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config

insts = (load_config().get("instances") or [])
inst = next((i for i in insts
             if i.get("python_path") and os.path.isfile(i.get("python_path") or "")
             and i.get("comfyui_path")), None)
assert inst, "配置里没有带合法 python_path 的实例"
inst = dict(inst)                       # 测试副本，不回写配置
inst["auto_open_browser"] = False       # 不在用户桌面开浏览器
cfg_port = int(inst.get("port") or 8188)
print(f"[e2e] 实例={inst.get('name')} 配置端口={cfg_port}")

# ---------- 1) 占住配置端口（模拟被其他程序占用） ----------
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
dummy = False
try:
    srv.bind(("127.0.0.1", cfg_port))
    srv.listen(1)
    dummy = True
    print(f"[e2e] 占位监听已起 127.0.0.1:{cfg_port}")
except OSError as e:
    print(f"[e2e] 配置端口已被外部占用（{e}），直接用现成占用者作阻碍")

from PySide6.QtCore import QCoreApplication

app = QCoreApplication([])
from app.process import (ComfyProcess, actual_port_for_pid, listening_ports,
                         port_in_use)

proc = ComfyProcess()
logs = []
proc.log_line.connect(lambda s: (logs.append(s), print("  |", s)))

exit_code = 1
try:
    # ---------- 2) 启动：应自动顺延 ----------
    proc.start(inst)
    assert proc.is_running, "进程没起来（日志见上）\n" + "\n".join(logs[-10:])
    assert any("已自动顺延到" in s for s in logs), \
        "日志里没有顺延提示\n" + "\n".join(logs[-10:])

    # 等真实端口开始监听（aki 启动含 torch 导入，最多 120s）
    actual = None
    t0 = time.time()
    while time.time() - t0 < 120:
        app.processEvents()
        if not proc.is_running:
            break
        ports = listening_ports(int(proc.pid)) if proc.pid else []
        if ports:
            actual = ports[0]
            break
        time.sleep(0.5)
    assert proc.is_running, "等待期间进程退出\n" + "\n".join(logs[-15:])
    assert actual, f"120s 内 {proc.pid} 没有进入 LISTENING\n" + "\n".join(logs[-15:])
    print(f"[e2e] 实际监听端口={actual} pid={proc.pid}")

    # ---------- 3) 断言 ----------
    # 3a. dummy 占着配置端口 → 实际端口必须已顺延（≠配置端口）且由本进程监听
    if dummy:
        assert actual != cfg_port, "顺延失败：实际端口仍等于配置端口"
    assert actual in listening_ports(int(proc.pid))

    # 3b. 实际端口识别函数给同一个答案（打开网页走的就是它）
    resolved = actual_port_for_pid(int(proc.pid), cfg_port)
    assert resolved == actual, f"actual_port_for_pid={resolved} != {actual}"
    print(f"[e2e] actual_port_for_pid 正确 → {resolved}")

    # 3c. 顺延日志内容
    bump_lines = [s for s in logs if "已自动顺延到" in s]
    print(f"[e2e] 顺延日志: {bump_lines[0]}")

    # 3d. HTTP 真实可达（ComfyUI 首页）
    url = f"http://127.0.0.1:{actual}/"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    print(f"[e2e] GET {url} -> HTTP {code}")
    assert code < 500, f"HTTP {code}"

    # 3e. 就绪自动开页日志用的是实际端口
    ready = [s for s in logs if "服务就绪" in s]
    assert ready and f":{actual}" in ready[0], f"就绪日志没用实际端口: {ready}"
    print(f"[e2e] 就绪日志: {ready[0]}")

    exit_code = 0
    print("E2E ALL PASS")
except AssertionError as e:
    print(f"E2E FAIL: {e}")
except Exception as e:
    print(f"E2E ERROR: {type(e).__name__}: {e}")
    print("\n".join(logs[-15:]))
finally:
    try:
        proc.stop()          # kill_tree 确保测试实例不残留
    except Exception:
        pass
    if dummy:
        srv.close()
print(f"exit={exit_code}")
sys.exit(exit_code)
