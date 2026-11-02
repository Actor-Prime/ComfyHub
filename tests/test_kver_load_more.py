# -*- coding: utf-8 -*-
"""内核版本列表分页探针：一次 20 行 + 「加载更多」按钮 + 滚动触底自动追加

用法: .venv_build/Scripts/python.exe tests/test_kver_load_more.py
（2026-11-02 分页改造配套）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow

win = MainWindow()
win.show()
t = time.time()
while time.time() - t < 0.8:
    app.processEvents()
    time.sleep(0.05)

page = win.comfyui_page
win.cfg.setdefault("settings", {})["close_behavior"] = "close"   # 测试直退防挂
# 必须先把页面切到导航栈顶 + 切「内核版本」Tab：非当前 Tab 整棵子树隐藏，
# 滚动条布局/max 都是假的，自动加载也不会触发（pitfalls 第 11 条同类坑）
win.stack.setCurrentWidget(page)
page.tabs.setCurrentIndex(1)
t = time.time()
while time.time() - t < 0.5:
    app.processEvents()
    time.sleep(0.05)

fake = [{"version": f"v0.42.{i}", "hash": f"aaa{i:03d}", "date": "2026-10-20",
         "kind": "稳定版"} for i in range(50)]
fake += [{"version": f"fix {i}", "hash": f"def{i:03d}", "date": "2026-10-19",
          "kind": "开发版"} for i in range(47)]
page._all_versions = fake

fails = []


def check(name, cond, detail=""):
    print(("  PASS" if cond else "  FAIL"), name, detail)
    if not cond:
        fails.append(name)


def pump(s=0.3):
    t = time.time()
    while time.time() - t < s:
        app.processEvents()
        time.sleep(0.05)


def rows():
    return page.tb_kver.rowCount()


def more_visible():
    # 与 _on_kver_scroll 守卫同语义：看自身显隐标志（isVisible 受父链影响）
    return not page.b_kmore.isHidden()


print("[1] 初始分页 20 行")
page._filter_versions()
pump()
check("初始 20 行", rows() == 20, f"rows={rows()}")
check("加载更多可见", more_visible())
check("按钮带剩余条数", "还有 30 条" in page.b_kmore.text(), page.b_kmore.text())
check("计数显示 20/97", page.lb_kcount.text().startswith("显示 20 / 共 97"),
      page.lb_kcount.text())

print("[2] 点「加载更多」追加 20 行")
page._load_more()
pump()
check("追加后 40 行", rows() == 40, f"rows={rows()}")
check("剩余变 10", "还有 10 条" in page.b_kmore.text(), page.b_kmore.text())

print("[3] 滚动触底自动追加")
sb = page.tb_kver.verticalScrollBar()
sb.setValue(sb.maximum())
pump()
check("触底自动加载到 50 行", rows() == 50,
      f"rows={rows()} sb_max={sb.maximum()}")
check("稳定版已尽按钮隐藏", not more_visible(), page.b_kmore.text())

print("[4] 切筛选重置分页")
page.cb_kkind.setCurrentIndex(1)   # 开发版（47 条）
pump()
check("开发版回到 20 行", rows() == 20, f"rows={rows()}")
check("开发版剩余 27", "还有 27 条" in page.b_kmore.text(), page.b_kmore.text())

print("[5] 连点加载到底")
page._load_more()
page._load_more()
pump()
check("开发版到 47 行", rows() == 47, f"rows={rows()}")
check("到尽按钮隐藏", not more_visible())

print("[6] 无数据时按钮隐藏")
page._all_versions = []
page._filter_versions()
pump()
check("空列表 0 行", rows() == 0, f"rows={rows()}")
check("空列表按钮隐藏", not more_visible(), page.b_kmore.text())

win.close()
print()
print(f"结果: {len(fails)} 失败" + (f" → {fails}" if fails else "，全部通过"))
sys.exit(1 if fails else 0)
