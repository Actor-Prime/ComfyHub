# -*- coding: utf-8 -*-
"""英文包补词工具：导出「英文模式下仍显示中文」的控件文案。

用途：给 app/i18n_en.py 补漏译。静态扫描抓不到用辅助函数拼出来的行文案
（如 _Row() 生成的行标签、表格刷新后的表头），只能靠运行时导出。

用法：.venv_build/Scripts/python.exe tests/i18n_leftover.py [out.txt]
输出：默认 tests/_i18n_left.txt，每行 `<控件类型>\t<中文原文>`，可直接抄进词表。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import (      # noqa: E402
    QApplication, QLabel, QAbstractButton, QGroupBox, QLineEdit, QTabWidget,
    QTableWidget,
)

app = QApplication([])
CJK = re.compile(r"[\u4e00-\u9fff]")

from app.config import load_config       # noqa: E402
from app.ui import main_window as mw     # noqa: E402

cfg = load_config()
cfg.setdefault("settings", {})
cfg["settings"].update({"close_behavior": "close", "lang": "en", "theme": "dark"})
mw.load_config = lambda: cfg
win = mw.MainWindow()
app.processEvents()
# 切一遍所有页面：触发 on_show 刷新与导航补扫，覆盖动态文案
for i in range(win.stack.count()):
    win.nav.setCurrentRow(i)
    app.processEvents()
win.nav.setCurrentRow(0)
app.processEvents()

left = {}
for w in win.findChildren(QLabel):
    t = w.text()
    if t and CJK.search(t):
        left.setdefault(t, "QLabel")
for w in win.findChildren(QAbstractButton):
    t = w.text()
    if t and CJK.search(t):
        left.setdefault(t, "Button")
for w in win.findChildren(QGroupBox):
    t = w.title()
    if t and CJK.search(t):
        left.setdefault(t, "GroupBox")
for w in win.findChildren(QLineEdit):
    for t in (w.placeholderText(), w.toolTip()):
        if t and CJK.search(t):
            left.setdefault(t, "LineEdit")
for w in win.findChildren(QTabWidget):
    for i in range(w.count()):
        if CJK.search(w.tabText(i)):
            left.setdefault(w.tabText(i), "Tab")
for w in win.findChildren(QTableWidget):
    for c in range(w.columnCount()):
        it = w.horizontalHeaderItem(c)
        if it and it.text() and CJK.search(it.text()):
            left.setdefault(it.text(), "Header")

out = [f"{v}\t{k}" for k, v in left.items()]
txt = "\n".join(out)
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tests", "_i18n_left.txt")
with open(path, "w", encoding="utf-8") as f:
    f.write(txt)
print(f"残留 {len(left)} 条 → {path}")
win.cfg["settings"]["close_behavior"] = "close"
win.close()
