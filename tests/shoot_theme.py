# -*- coding: utf-8 -*-
"""主题逐页截图（改主题/配色后目视自检用）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/shoot_theme.py [宽] [高] [dark|light]
输出：analysis/<主题>/ 下的逐页 PNG（含各 Tab）

为什么要有：只跑断言测不出「白底白字」「浅色里嵌黑块」这类问题，
主题改动必须看图 —— 深色 / 浅色两条都要扫。
"""
import os
import sys
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="sweep_")
os.environ["COMFYHUB_CHAT_DIR"] = os.path.join(_TMP, "chats")
os.environ["COMFYHUB_SKILL_DIR"] = os.path.join(_TMP, "skills")

W = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
H = int(sys.argv[2]) if len(sys.argv) > 2 else 900
MODE = sys.argv[3] if len(sys.argv) > 3 else "light"

from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

app = QApplication(sys.argv)

from app import ai_chat_store  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.nav import NAV  # noqa: E402

OUT = os.path.join(ROOT, "analysis", MODE)
os.makedirs(OUT, exist_ok=True)

NAMES = {NAV.HOME: "home", NAV.COMFYUI: "comfyui", NAV.PLUGINS: "plugins",
         NAV.ENV: "env", NAV.MODELS: "models", NAV.WORKFLOW: "workflow",
         NAV.AI: "ai", NAV.CONSOLE: "console", NAV.SETTINGS: "settings"}


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


ai_chat_store.save("c1", [
    {"role": "user", "content": "做一次实例体检，简要汇报状态"},
    {"role": "assistant", "content": "**体检结果：实例已启动，状态正常。**\n\n- ComfyUI：0.43.2 ｜ PyTorch：2.14.1+cu130\n- 显存：空闲 10.8 GB / 共 11.99 GB\n- 队列：运行 0，排队 0"},
], title="做一次实例体检，简要汇报状态")

win = MainWindow()
win.cfg.setdefault("settings", {})
win.cfg["settings"]["theme"] = MODE
theme.apply_theme(app, win.cfg, force=True)
win.resize(W, H)
win.show()
pump(2.0)

for idx, key in NAMES.items():
    win.goto(idx)
    pump(1.0)
    page = win.stack.widget(idx)
    p = os.path.join(OUT, f"{idx:02d}_{key}.png")
    win.grab().save(p)
    print("saved", os.path.basename(p))
    if page is None:
        continue
    for ti, tab in enumerate(page.findChildren(QTabWidget)):
        for i in range(tab.count()):
            tab.setCurrentIndex(i)
            pump(0.35)
            tn = (tab.tabText(i) or str(i)).replace("/", "_").replace(" ", "")[:12]
            p2 = os.path.join(OUT, f"{idx:02d}_{key}_t{ti}_{tn}.png")
            win.grab().save(p2)
            print("  saved", os.path.basename(p2))
print("OUT", OUT)
