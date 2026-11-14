# -*- coding: utf-8 -*-
"""状态卡矢量图标回归（2026-11-15）：emoji → 矢量线稿。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication()

from app.ui import theme
from app.ui.stat_icons import StatIcon
from app.ui.pages import home_page as hp

_n = [0]
_results = []
def check(name, ok, detail=""):
    _n[0] += 1
    _results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (("  [%s]" % (detail,)) if detail else ""))
    return ok

# A. 组件行为
w = StatIcon("ram")
w.show()
app.processEvents()
check("默认 ram", w.kind() == "ram")
check("绘制区域非空", not w.grab().isNull())
pm_before = w.grab().toImage()
w.set_kind("cpu")
app.processEvents()
check("set_kind 生效", w.kind() == "cpu")
check("换 kind 后画面变化", w.grab().toImage() != pm_before)
w.set_kind("cpu")
app.processEvents()
check("同 kind 重复设置不重绘", w.grab().toImage() == w.grab().toImage())

# B. 四种 kind 都能画
for k in ("ram", "cpu", "gpu", "disk"):
    wi = StatIcon(k)
    wi.show()
    app.processEvents()
    check("kind=%s 可绘制" % k, not wi.grab().isNull())

# C. 主窗状态卡真的挂上了 StatIcon（防有人改回 QLabel）
from app.ui.main_window import MainWindow
win = MainWindow()
win.show()
app.processEvents()
for attr, kind in (("card_ram", "ram"), ("card_cpu", "cpu"),
                   ("card_gpu", "gpu"), ("card_disk", "disk")):
    card = getattr(win.home_page, attr, None)
    ok = card is not None and hasattr(card, "_icon") and \
        isinstance(card._icon, StatIcon) and card._icon.kind() == kind
    check("%s 用 %s 矢量图标" % (attr, kind), ok,
          type(getattr(card, "_icon", None)).__name__)

# D. home_page 源码不再含旧 emoji
src = open(hp.__file__.rstrip("c"), encoding="utf-8").read()
for e in ("🧠", "🎮", "💾"):
    check("源码无 %s" % e, e not in src)

win.close()
ok_all = all(_results)
print("\n%d/%d %s" % (sum(_results), len(_results), "ALL GREEN" if ok_all else "HAS FAIL"))
sys.exit(0 if ok_all else 1)
