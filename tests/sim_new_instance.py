# -*- coding: utf-8 -*-
"""模拟：新建实例全流程（向导 4 步 → 完成 → 注册实例）

用法：.venv_build/Scripts/python.exe tools/sim_new_instance.py [目标目录]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

TARGET = sys.argv[1] if len(sys.argv) > 1 else r"F:\_sim\ComfyUI"
MAX_MIN = 20

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow
from app.ui.setup_wizard import SetupWizard

win = MainWindow()
win.show()
wiz = SetupWizard(win)
wiz.show()

state = {"last_log": 0, "t0": time.time(), "stage": "", "done": False}
os.makedirs(TARGET, exist_ok=True)
wiz.ed_dir.setText(TARGET)
wiz.ed_name.setText("模拟新实例")

RESULT = {"ok": False, "reason": ""}


def go():
    print(f"[A-4] 目标目录: {TARGET}", flush=True)
    # 确认 python 选择
    print(f"[A-4] Python 选项: {[wiz.cb_python.itemText(i) for i in range(wiz.cb_python.count())][:4]}", flush=True)
    print(f"[A-4] 当前选择: {wiz.cb_python.currentText()}", flush=True)
    wiz.next()   # 进入安装页


def poll():
    if state["done"]:
        return
    p2 = wiz.p2
    try:
        lines = p2.log.toPlainText().splitlines()
        for ln in lines[state["last_log"]:][-6:]:
            print("  LOG>", ln[:130], flush=True)
        state["last_log"] = len(lines)

        cur = getattr(p2, "cur", 0) + 1
        print(f"[{int(time.time() - state['t0'])}s] 步骤 {cur}/{p2.state.get('total', 4)} "
              f"| 进度 {p2.bar.value()} | {p2.lb_state.text()[:70]}", flush=True)

        # 完成判定：向导跳到完成页 or p2.done_ok
        if getattr(p2, "done_ok", False):
            RESULT["ok"] = True
            RESULT["reason"] = "4 步全部完成"
            state["done"] = True
            print("✅ [A-4] 安装流程完成", flush=True)
            app.quit()
            return
        if time.time() - state["t0"] > MAX_MIN * 60:
            RESULT["reason"] = "超时"
            state["done"] = True
            print("⏱ [A-4] 超时退出", flush=True)
            app.quit()
    except Exception as e:
        print("poll 异常:", e, flush=True)


QTimer.singleShot(1500, go)
t = QTimer()
t.timeout.connect(poll)
t.start(2000)

app.exec()

# 校验实例是否注册进配置
from app.config import load_config
cfg = load_config()
insts = cfg.get("instances", [])
matched = [i for i in insts if os.path.normpath(i.get("path", "")) == os.path.normpath(TARGET)]
print(f"\n=== A-4 结果 ===", flush=True)
print(f"流程: {'成功' if RESULT['ok'] else '失败'} ({RESULT['reason']})", flush=True)
print(f"总实例数: {len(insts)}", flush=True)
print(f"新实例已注册: {bool(matched)}", flush=True)
if matched:
    i = matched[0]
    print(f"  名称={i.get('name')} 路径={i.get('path')} 端口={i.get('port')} python={i.get('python')}", flush=True)
main_exit = 0 if (RESULT["ok"] and matched) else 1
sys.exit(main_exit)
