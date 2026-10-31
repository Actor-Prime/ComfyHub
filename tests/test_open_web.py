# -*- coding: utf-8 -*-
"""打开网页三件事回归：未启动禁用 / 实际端口识别 / 端口覆盖构造命令

用法: .venv_build/Scripts/python.exe tests/test_open_web.py
（offscreen，不 show 主窗 → 孤儿弹窗 isVisible 守卫直接返回，不会卡）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------- 1) netstat 解析 + 实际端口判定（monkeypatch run，纯函数层） ----------
from app import process as P

SAMPLE = (
    b"  TCP    127.0.0.1:8188         0.0.0.0:0              LISTENING       1234\r\n"
    b"  TCP    0.0.0.0:8189           0.0.0.0:0              LISTENING       1234\r\n"
    b"  TCP    127.0.0.1:631          0.0.0.0:0              LISTENING       99\r\n"
    b"  TCP    127.0.0.1:8188         127.0.0.1:50000        ESTABLISHED     1234\r\n"
)


class _FakeOut:
    def __init__(self, data):
        self.stdout = data


_orig_run = P.run
P.run = lambda *a, **k: _FakeOut(SAMPLE)
try:
    assert P.pid_listening_on(8188) == [1234], P.pid_listening_on(8188)
    assert P.pid_listening_on(631) == [99]
    assert P.pid_listening_on(9999) == []
    assert P.listening_ports(1234) == [8188, 8189], P.listening_ports(1234)
    assert P.listening_ports(99) == [631]
    assert P.listening_ports(7777) == []
    # 判定：配置在列表→配置；不在→最小；无监听→None
    assert P.actual_port_for_pid(1234, 8188) == 8188
    assert P.actual_port_for_pid(1234, 5000) == 8188
    assert P.actual_port_for_pid(99, 8188) == 631
    assert P.actual_port_for_pid(7777, 8188) is None
finally:
    P.run = _orig_run
print("PASS netstat 解析与实际端口判定")

# ---------- 2) build_command 端口覆盖（顺延后的实际端口注入命令行） ----------
inst0 = {"id": "t1", "comfyui_path": r"F:\ComfyHub", "python_path": "python.exe",
         "port": 8188}
c1 = P.build_command(inst0)
assert c1[c1.index("--port") + 1] == "8188"
c2 = P.build_command(inst0, port=8189)
assert c2[c2.index("--port") + 1] == "8189"
assert inst0["port"] == 8188, "不允许改写实例配置"
print("PASS build_command 端口覆盖")

# ---------- 3) UI：未启动禁用 + 打开按实际端口 ----------
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow

win = MainWindow()          # 故意不 show()：孤儿检查 isVisible 守卫不弹窗
assert win.current_inst() is not None, "测试机需至少一个实例配置"
page = win.comfyui_page

# 3a. 全新管理器没有任何进程 → 未运行 → 打开网页禁用
page._update_state()
assert not page.b_web.isEnabled(), "未运行时打开网页应禁用"
win.home_page._refresh_state()
assert not win.home_page.b_web.isEnabled(), "工作台打开网页也应禁用"

# 3b. 假装运行中 → 启用
win.procs.is_running = lambda i: True
page._update_state()
assert page.b_web.isEnabled(), "运行中打开网页应启用"
win.home_page._refresh_state()
assert win.home_page.b_web.isEnabled()

# 3c. 运行中打开 → 走实际端口（配置 8188，实际 8200）
win.procs.actual_port = lambda i: 8200
import webbrowser
opened = []
_orig_open = webbrowser.open
webbrowser.open = lambda u, *a, **k: opened.append(u)
try:
    page.open_browser()
    assert opened == ["http://127.0.0.1:8200"], opened
    # 实际端口探测失败(None) → 回退配置端口
    win.procs.actual_port = lambda i: None
    opened.clear()
    page.open_browser()
    assert opened == [f"http://127.0.0.1:{int(win.current_inst().get('port') or 8188)}"], opened
    # 3d. 未运行 → 不打开
    win.procs.is_running = lambda i: False
    opened.clear()
    page.open_browser()
    assert opened == [], f"未运行不应打开: {opened}"
finally:
    webbrowser.open = _orig_open
print("PASS UI 禁用/实际端口打开/回退/未运行守卫")

win.cfg.setdefault("settings", {})["close_behavior"] = "close"   # 测试直退，防关闭询问框挂死
win.close()
print("ALL PASS")
sys.exit(0)
