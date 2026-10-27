# -*- coding: utf-8 -*-
"""表格内嵌按钮文字完整性回归（2026-10-27 用户报障「框住的字显示不全」）

根因链：QSS item padding 4px 把 cellWidget 挤小（32 行 → 23px），
基础按钮 padding 6px 需 26px → 文字上下被裁；插件页 setFixedHeight(24) 也超 23。

用法: .venv_build/Scripts/python.exe tests/test_cell_button_fit.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget, QWidget, QHBoxLayout

app = QApplication(sys.argv[:1])
from app.ui.theme import DARK_QSS, TABLE_ROW_H

app.setStyleSheet(DARK_QSS)

fails = []


def check(name, ok, detail=""):
    print(("  OK  " if ok else " FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


# 1) 规则必须存在且压过基础按钮 padding（级联：后写 + 更具体选择器）
check("QSS 含 QTableWidget QPushButton 紧凑规则",
      "QTableWidget QPushButton { padding: 2px 8px; }" in DARK_QSS)

tb = QTableWidget(3, 4)
tb.verticalHeader().setDefaultSectionSize(TABLE_ROW_H)
tb.verticalHeader().setMinimumSectionSize(TABLE_ROW_H)
tb.setColumnWidth(3, 150)                    # 与 tb_kver 操作列同宽

# 2) 直接 setCellWidget 的按钮（内核版本页形态）
b1 = QPushButton("切换并装依赖")
tb.setCellWidget(0, 3, b1)

# 3) holder + setFixedHeight 的按钮（插件页操作列形态）
holder = QWidget()
hl = QHBoxLayout(holder)
hl.setContentsMargins(2, 0, 2, 0)
hl.setSpacing(4)
b2 = QPushButton("更新")
b2.setFixedHeight(22)                        # 新值：必须 ≤ cell 可用高
hl.addWidget(b2)
hl.addStretch()
tb.setCellWidget(1, 3, holder)

# 4) setFixedSize 形态（插件市场「安装」按钮）
holder2 = QWidget()
hl2 = QHBoxLayout(holder2)
hl2.setContentsMargins(0, 0, 0, 0)
b3 = QPushButton("安装")
b3.setFixedSize(80, 22)
hl2.addWidget(b3, 0, Qt.AlignCenter)
tb.setCellWidget(2, 3, holder2)

tb.resize(600, 200)
tb.show()
app.processEvents()


def text_fits(btn, geom_h, geom_w, padx=2, border=1):
    """内容区能否装下文字：几何高 - padding*2 - border*2 >= 字体高；
    宽同理（Qt 不换行，横向超宽会省略/裁切）"""
    fm = btn.fontMetrics()
    need_h = fm.height()
    need_w = fm.horizontalAdvance(btn.text())
    avail_h = geom_h - padx * 2 - border * 2
    avail_w = geom_w - padx * 2 - border * 2
    return (avail_h >= need_h, avail_w >= need_w,
            f"高{avail_h}>={need_h} 宽{avail_w}>={need_w}")


# 行高 32 下 cellWidget 可用高必须是 23（32 - item padding 8 - border 1）
g1 = b1.geometry()
check("直设按钮拿到 cell 全高（23px）", g1.height() == 23, f"h={g1.height()}")

ok_h, ok_w, d1 = text_fits(b1, g1.height(), g1.width())
check("「切换并装依赖」文字上下不裁", ok_h, d1)
check("「切换并装依赖」文字横向不裁", ok_w, d1)

g2 = b2.geometry()
ok_h2, ok_w2, d2 = text_fits(b2, g2.height(), g2.width())
check("插件页操作按钮(固定22)文字不裁", ok_h2 and ok_w2, d2)
check("插件页操作按钮 22 ≤ cell 可用高 23", g2.height() <= 23, f"h={g2.height()}")

g3 = b3.geometry()
ok_h3, ok_w3, d3 = text_fits(b3, g3.height(), g3.width())
check("市场安装按钮(固定80x22)文字不裁", ok_h3 and ok_w3, d3)

# 5) 表外按钮不受紧凑规则影响（保持 6px padding / 26px sizeHint）
b_out = QPushButton("启动")
check("表外按钮 sizeHint 仍为 26 高（未被波及）",
      b_out.sizeHint().height() == 26, f"h={b_out.sizeHint().height()}")

# 6) 行高兜底仍是 32
check("表格行高仍为 32", tb.rowHeight(0) == TABLE_ROW_H, f"h={tb.rowHeight(0)}")

tb.close()
if fails:
    print(f"\n结果：{len(fails)} 项失败 -> {fails}")
    sys.exit(1)
print("\nALL PASS：表格内嵌按钮文字完整，表外按钮不受影响")
sys.exit(0)
