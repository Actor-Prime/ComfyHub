# -*- coding: utf-8 -*-
"""插件市场「点名称浏览器打开」回归测试

用法: .venv_build/Scripts/python.exe tests/test_market_link.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------- 1) web_url 纯函数 ----------
from app import plugin_store as store

cases = [
    ({"reference": "https://github.com/a/b.git"}, "https://github.com/a/b"),
    ({"reference": "https://gitee.com/x/y.git"}, "https://gitee.com/x/y"),
    ({"reference": "git@github.com:owner/repo.git"}, "https://github.com/owner/repo"),
    ({"reference": "ssh://git@github.com/owner/repo.git"}, "https://github.com/owner/repo"),
    ({"reference": "https://github.com/a/b"}, "https://github.com/a/b"),
    ({"reference": ""}, ""),
    ({}, ""),
    ({"files": ["https://github.com/f/g.git"]}, "https://github.com/f/g"),
]
ok = True
for node, want in cases:
    got = store.web_url(node)
    flag = "OK " if got == want else "FAIL"
    if got != want:
        ok = False
    print(f"  {flag} {node} -> {got!r}" + ("" if got == want else f" (want {want!r})"))
print(("PASS" if ok else "FAIL") + " web_url 转换")

# ---------- 2) UI 点击链 ----------
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
import app.ui.pages.plugins_page as P

captured = []


class _Stub:
    @staticmethod
    def openUrl(qurl):
        captured.append(qurl.url())
        return True


P.QDesktopServices = _Stub   # 捕获，不真开浏览器

from app.ui.main_window import MainWindow

win = MainWindow()
win.show()
t = __import__("time")
tt = t.time()
while t.time() - tt < 0.8:
    app.processEvents()
    t.sleep(0.05)

page = win.plugins_page
page._last_market_rows = [
    {"title": "PlugA", "reference": "https://github.com/aaa/pluga.git"},
    {"title": "PlugB", "reference": "git@github.com:bbb/plugb.git"},
]
page._render_market(page._last_market_rows)

it = page.tb_market.item(0, 0)
tip = it.toolTip()
underline = it.font().underline()
print(f"  tooltip={tip!r} underline={underline}")

# 单击：不应打开（2026-10-27 用户定：单击仅选中，双击才跳）
page.tb_market.cellClicked.emit(0, 0)
page.tb_market.cellClicked.emit(1, 0)
ok_single = captured == []
print(f"  单击后 captured={captured}（应为空）")

# 双击：名称列打开，其他列不动作
page.tb_market.cellDoubleClicked.emit(0, 0)
page.tb_market.cellDoubleClicked.emit(1, 0)
page.tb_market.cellDoubleClicked.emit(0, 1)

expect = ["https://github.com/aaa/pluga", "https://github.com/bbb/plugb"]
ok2 = (ok_single and captured == expect
       and "双击在浏览器打开" in tip and underline)
print(f"  双击后 captured={captured}")
print(("PASS" if ok2 else "FAIL") + " 双击链（单击不跳/双击跳/列守卫）")

# ---------- 3) 已安装页：Git 地址列双击跳、单击不跳 + _toggle 列守卫 ----------
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt as _Qt

page.tabs.setCurrentIndex(0)
page._rows_data = [
    {"name": "ComfyUI-GGUF", "url": "https://github.com/city96/ComfyUI-GGUF.git",
     "enabled": True, "git": True, "dir": "F:\\x"},
]
tb2 = page.tb
tb2.setRowCount(1)
it0 = P.QTableWidgetItem("ComfyUI-GGUF")
tb2.setItem(0, 0, it0)
it1 = P.QTableWidgetItem("https://github.com/city96/ComfyUI-GGUF")
tb2.setItem(0, 1, it1)
for c in (2, 3, 4):
    tb2.setItem(0, c, P.QTableWidgetItem("—"))
captured.clear()


def _pump(s=0.2):
    import time as _t
    end = _t.time() + s
    while _t.time() < end:
        app.processEvents()
        _t.sleep(0.03)


cell = tb2.visualItemRect(it1).center()
QTest.mouseClick(tb2.viewport(), _Qt.LeftButton, _Qt.NoModifier, cell)
_pump()
ok3a = captured == []                       # 单击不跳
QTest.mouseDClick(tb2.viewport(), _Qt.LeftButton, _Qt.NoModifier, cell)
_pump()
ok3b = captured == ["https://github.com/city96/ComfyUI-GGUF"]  # 双击跳一次
print(f"  已安装单击 captured 应空: {ok3a}；双击后={captured}")
# 双击地址列：_toggle 应直接 return（不弹窗不 rename）
idx = tb2.model().index(0, 1)
page._toggle(idx)          # guard 失败会弹 QMessageBox 卡死 → 测试挂起即失败
ok3 = ok3a and ok3b
print(("PASS" if ok3 else "FAIL") + " 已安装页地址列（单击不跳/双击跳/守卫）")

sys.exit(0 if (ok and ok2 and ok3) else 1)
