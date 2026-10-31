# -*- coding: utf-8 -*-
"""关闭行为回归（2026-10-31）

覆盖：
1. CloseChoiceDialog：退出/最小化/取消 三分支 + 「记住我的选择」勾选
2. closeEvent 三态：ask 弹框（记住写入设置）/ close 直接退出 / minimize 收托盘
3. 取消分支不关闭；托盘「退出」直通不被 minimize 截胡
4. 实例运行确认（No 拦下 / Yes 放行）
5. 设置页「关闭主窗口时」下拉持久化 + 与 closeEvent 联动

用法：.venv_build/Scripts/python.exe tests/test_close_behavior.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QDialog

app = QApplication([])

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.2):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


# QMessageBox 三件套拦截（offscreen 防模态挂死）
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

from app.ui.close_dialog import CloseChoiceDialog
from app.ui import main_window as mw_mod
from app.ui.pages import settings_page as sp_mod


def make_exec(click_btn=None, remember=False):
    """替换 CloseChoiceDialog.exec：自动勾选/点按钮后返回 result（不进模态循环）"""
    def _exec(self):
        if remember:
            self.ck_remember.setChecked(True)
        if click_btn:
            for b in self.findChildren(QPushButton):
                if b.text() == click_btn:
                    b.click()
                    break
        return self.result()
    return _exec


# -------------------------------------------------- 1. CloseChoiceDialog
dlg = CloseChoiceDialog()
check("1a 初始未选择", dlg.choice is None and dlg.result() != QDialog.Accepted)
check("1b 记住默认不勾", not dlg.remembered())

dlg = CloseChoiceDialog()
dlg.exec = make_exec("退出程序", remember=True).__get__(dlg)
dlg.exec()
check("1c 退出程序→choice=close", dlg.choice == "close")
check("1d 勾选记住→remembered", dlg.remembered())

dlg = CloseChoiceDialog()
dlg.exec = make_exec("最小化到托盘").__get__(dlg)
dlg.exec()
check("1e 最小化到托盘→choice=minimize", dlg.choice == "minimize")
check("1f 未勾选→not remembered", not dlg.remembered())

dlg = CloseChoiceDialog()
dlg.exec = make_exec("取消").__get__(dlg)
r = dlg.exec()
check("1g 取消→Rejected+未选择", r != QDialog.Accepted and dlg.choice is None)

# -------------------------------------------------- 2. closeEvent 三态
saved = []
mw_mod.save_config = lambda cfg: saved.append(dict(cfg.get("settings") or {}))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

from app.ui.main_window import MainWindow
win = MainWindow()
win.show()
pump(0.2)
calls = []
win.showMinimized = lambda: calls.append("min")
win.procs.stop_all = lambda: calls.append("stop")

def run_close(behavior, quit_direct=False):
    calls.clear()
    saved.clear()
    win.cfg.setdefault("settings", {})["close_behavior"] = behavior
    win._quit_direct = quit_direct
    ce = QCloseEvent()
    win.closeEvent(ce)
    return ce.isAccepted()

# 2a close：直接退出
ok = run_close("close")
check("2a close=直接退出", ok and "stop" in calls, f"ok={ok} calls={calls}")

# 2b minimize：不退出、收托盘
ok = run_close("minimize")
check("2b minimize=收托盘不退出", not ok and calls == ["min"], f"ok={ok} calls={calls}")

# 2c ask→退出程序+记住：写入 close 行为
win.cfg["settings"]["close_behavior"] = "ask"
CloseChoiceDialog.exec = make_exec("退出程序", remember=True)
ok = run_close("ask")
check("2c ask→退出+记住写 close", ok and saved and saved[-1].get("close_behavior") == "close",
      f"ok={ok} saved={saved}")

# 2d ask→最小化（不记住）：设置保持 ask
CloseChoiceDialog.exec = make_exec("最小化到托盘", remember=False)
ok = run_close("ask")
check("2d ask→最小化不写设置", not ok and calls == ["min"]
      and win.cfg["settings"]["close_behavior"] == "ask",
      f"ok={ok} calls={calls} beh={win.cfg['settings'].get('close_behavior')}")

# 2e ask→取消：留在窗口
CloseChoiceDialog.exec = make_exec("取消")
ok = run_close("ask")
check("2e ask→取消留在窗口", not ok and not calls, f"ok={ok} calls={calls}")

# 2f 托盘退出直通：minimize 行为下也真退
ok = run_close("minimize", quit_direct=True)
check("2f 托盘退出直通不被截胡", ok and "stop" in calls and "min" not in calls,
      f"ok={ok} calls={calls}")

# 2g 实例运行确认拦下（No）
win.procs.is_running = lambda i: True
win.cfg["instances"] = [{"id": "i1", "name": "实例A"}]
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
ok = run_close("close")
check("2g 实例运行确认 No→拦下", not ok and "stop" not in calls, f"ok={ok} calls={calls}")

# 2h 实例运行确认放行（Yes）
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
ok = run_close("close")
check("2h 实例运行确认 Yes→退出", ok and "stop" in calls, f"ok={ok} calls={calls}")
win.procs.is_running = lambda i: False
win.cfg["instances"] = []
win._quit_direct = False

# -------------------------------------------------- 3. 设置页下拉
from app.ui.pages.settings_page import SettingsPage


class FakeMain:
    def current_inst(self):
        return None


sp_mod.save_config = lambda cfg: None
cfg = {"settings": {}}
page = SettingsPage(cfg, FakeMain())
check("3a 下拉默认=每次询问", page.cb_close.currentData() == "ask",
      str(page.cb_close.currentData()))
page.cb_close.setCurrentIndex(page.cb_close.findData("minimize"))
page._save()
check("3b 保存→close_behavior=minimize",
      cfg["settings"].get("close_behavior") == "minimize", str(cfg["settings"]))

# 联动：设置页保存后 closeEvent 立即按新行为执行
win.cfg = cfg
ok = run_close(cfg["settings"]["close_behavior"])
check("3c 设置→closeEvent 联动（minimize）", not ok and calls == ["min"],
      f"ok={ok} calls={calls}")
page.cb_close.setCurrentIndex(page.cb_close.findData("ask"))
page._save()
check("3d 可改回每次询问", cfg["settings"].get("close_behavior") == "ask")

# -------------------------------------------------- 汇总
fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"关闭行为回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
if fails:
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
sys.exit(1 if fails else 0)
