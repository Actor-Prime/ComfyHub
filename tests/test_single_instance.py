# -*- coding: utf-8 -*-
"""单实例守卫回归（2026-11-01）

背景：重复双击 exe 会再起一套进程，托盘堆一串图标、两套管理器抢同一批实例。
覆盖：主实例拿锁 / 第二个实例被拒并唤醒已有窗口 / 释放后可重新拿锁 / 残留 socket 自愈。

用法：.venv_build/Scripts/python.exe tests/test_single_instance.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer

app = QApplication([])
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.3):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


from app.single_instance import SingleInstance  # noqa: E402

KEY = "ComfyHub-Test-SingleInstance"

# 干净起步：先清掉可能残留的同名 socket
QLocalServer.removeServer(KEY)

g1 = SingleInstance(KEY)
check("1a 第一个实例拿到主控权", g1.try_acquire() is True)

fired = []
g1.activated.connect(lambda: fired.append(1))

g2 = SingleInstance(KEY)
check("1b 第二个实例被判定为重复启动", g2.try_acquire() is False)
pump(0.4)
check("1c 第二个实例唤醒了已有窗口", bool(fired), f"fired={len(fired)}")

# 再点一次仍然只是唤醒，不会变成主实例
fired.clear()
g3 = SingleInstance(KEY)
check("1d 第三次启动同样被拒", g3.try_acquire() is False)
pump(0.3)
check("1e 已有窗口再次被唤醒", bool(fired))

# 释放后能重新当主实例（模拟正常退出再启动）
g1.release()
g4 = SingleInstance(KEY)
check("1f 释放后可重新拿锁", g4.try_acquire() is True)
g4.release()

# 模拟崩溃残留：socket 文件还在但没有服务在听 → 自愈
QLocalServer.removeServer(KEY)
stale = QLocalServer()
stale.listen(KEY)
stale.close()          # 关掉但不清 socket（近似崩溃现场）
g5 = SingleInstance(KEY)
check("1g 残留 socket 不影响下次启动", g5.try_acquire() is True)
g5.release()

QLocalServer.removeServer(KEY)

# -------------------------------------------------- 2. 第二个实例 → 主窗从托盘恢复
# 用户场景：窗口缩在托盘里，又双击了一次 exe —— 必须自己弹回来，而不是没反应。
from app.config import load_config          # noqa: E402
from app.ui import main_window as mw_mod    # noqa: E402

_cfg = load_config()
_cfg.setdefault("settings", {})["close_behavior"] = "close"
mw_mod.load_config = lambda: _cfg

win = mw_mod.MainWindow()
win.hide()
pump(0.2)
check("2a 主窗可隐藏到托盘", win.isHidden())

g6 = SingleInstance(KEY)
check("2b 主实例拿锁", g6.try_acquire() is True)
_bubbles = []
g6.activated.connect(lambda: (_bubbles.append(1), win._restore_from_tray()))

g7 = SingleInstance(KEY)
check("2c 第二个实例被拒", g7.try_acquire() is False)
pump(0.4)
check("2d 主窗被唤到前台", not win.isHidden(), f"hidden={win.isHidden()}")
check("2e 唤醒信号确实发出", len(_bubbles) >= 1, f"n={len(_bubbles)}")

win.cfg.setdefault("settings", {})["close_behavior"] = "close"
win.close()
pump(0.2)
g6.release()
QLocalServer.removeServer(KEY)

fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"单实例守卫回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
if fails:
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
sys.exit(1 if fails else 0)
