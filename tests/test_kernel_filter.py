# -*- coding: utf-8 -*-
"""探针：内核版本页「类型筛选」信号链是否真的触发重绘

用法: .venv_build/Scripts/python.exe tests/debug_kernel_filter.py
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
cb = page.cb_kkind
tb = page.tb_kver

# 注入 50 稳定版 + 47 开发版（绕开 git，纯测信号链）
fake = [{"version": f"v0.42.{i}", "hash": "abc123", "date": "2026-10-20",
         "kind": "稳定版"} for i in range(50)]
fake += [{"version": f"fix {i}", "hash": f"def{i:03d}", "date": "2026-10-19",
          "kind": "开发版"} for i in range(47)]
page._all_versions = fake
page._filter_versions()

def pump(s=0.3):
    t = time.time()
    while time.time() - t < s:
        app.processEvents()
        time.sleep(0.05)

pump(0.5)  # 让布局跑完再量高度
print(f"初始 index={cb.currentIndex()} text={cb.currentText()} rows={tb.rowCount()} "
      f"count={page.lb_kcount.text()!r}")

cb.setCurrentIndex(1)   # 开发版
pump()
print(f"切到 开发版 index={cb.currentIndex()} text={cb.currentText()} rows={tb.rowCount()} "
      f"count={page.lb_kcount.text()!r}")

cb.setCurrentIndex(2)   # 全部
pump()
print(f"切到 全部   index={cb.currentIndex()} text={cb.currentText()} rows={tb.rowCount()} "
      f"count={page.lb_kcount.text()!r}")

cb.setCurrentIndex(0)   # 稳定版
pump()
print(f"切回 稳定版 index={cb.currentIndex()} text={cb.currentText()} rows={tb.rowCount()} "
      f"count={page.lb_kcount.text()!r}")

# 表格高度：应撑满到接近窗口底（不再被 240 卡住）
print(f"table maxHeight={tb.maximumHeight()} actual_h={tb.height()} "
      f"win_h={win.height()} "
      f"fills={'YES' if tb.height() > 400 else 'NO'}")
win.cfg.setdefault("settings", {})["close_behavior"] = "close"   # 测试直退，防关闭询问框挂死
win.close()
