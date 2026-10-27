# -*- coding: utf-8 -*-
"""表格行高统一回归：全部 QTableWidget 必须 32px（防「字体显示不全」复发）

用法: .venv_build/Scripts/python.exe tests/test_row_height.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QTableWidget

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow
from app.ui.theme import TABLE_ROW_H

win = MainWindow()          # 不 show()：孤儿弹窗 isVisible 守卫不弹
tables = win.findChildren(QTableWidget)
assert len(tables) == 8, f"主窗预期 8 张表，实际 {len(tables)}"

# 懒创建对话框表（CivitaiDialog 的 tb_files/tb_hf）：主窗扫描覆盖不到，
# 靠构造时 fit_table 就地统一；model_name="" 走不到网络搜索分支
from app.ui.pages.model_market_widget import CivitaiDialog
dlg = CivitaiDialog(win, "1", "", [])
tables += dlg.findChildren(QTableWidget)
assert len(tables) == 10, f"预期 10 张表（8 主窗 + 2 对话框），实际 {len(tables)}"

# 1) 每张表：默认行高与最小行高都是 32
for tb in tables:
    vh = tb.verticalHeader()
    assert vh.defaultSectionSize() == TABLE_ROW_H, \
        f"{tb.objectName() or tb}: default={vh.defaultSectionSize()}"
    assert vh.minimumSectionSize() == TABLE_ROW_H, \
        f"{tb.objectName() or tb}: min={vh.minimumSectionSize()}"

# 2) 新建行吃默认值（数据加载发生在建表之后，靠 default 生效）
tb = tables[0]
old = tb.rowCount()
tb.setRowCount(old + 3)
for r in range(old, old + 3):
    assert tb.rowHeight(r) == TABLE_ROW_H, f"新行 {r} 行高={tb.rowHeight(r)}"
tb.setRowCount(old)

# 3) 已有行兜底（fit_table 循环 setRowHeight 路径）
from app.ui.theme import fit_table
tb.setRowCount(2)
tb.setRowHeight(0, 10)       # 会被 minimumSectionSize 钳到 32
fit_table(tb)
assert tb.rowHeight(0) >= TABLE_ROW_H and tb.rowHeight(1) >= TABLE_ROW_H
tb.setRowCount(old)

print(f"PASS {len(tables)} 张表行高全为 {TABLE_ROW_H}px（默认+最小+新行+兜底）")
win.close()
sys.exit(0)
