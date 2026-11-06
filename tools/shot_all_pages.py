# -*- coding: utf-8 -*-
"""逐页渲染检查（2026-11-06）：custom 背景图下每页滚到中段截图，人工/量化检查可读性"""
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 9))

from app.config import load_config  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

cfg = load_config()
win = MainWindow()
win.resize(1180, 780)
win.show()
for _ in range(15):
    app.processEvents()

names = ["工作台", "ComfyUI", "插件管理", "环境管理", "模型库", "工作流", "AI助手", "控制台", "系统设置"]
outdir = ROOT / "docs" / "pages"
outdir.mkdir(exist_ok=True)

report = []
for i in range(win.stack.count()):
    try:
        win.stack.setCurrentIndex(i)
        page = win.stack.currentWidget()
        for _ in range(10):
            app.processEvents()
        # 滚轮效果：找到页面里的滚动区滚到中段
        from PySide6.QtWidgets import QScrollArea
        for sa in page.findChildren(QScrollArea):
            sb = sa.verticalScrollBar()
            if sb.maximum() > 0:
                sb.setValue(int(sb.maximum() * 0.45))
        for _ in range(10):
            app.processEvents()
        pm = page.grab()
        out = outdir / f"p{i}_{names[i] if i < len(names) else i}.png"
        pm.save(str(out))
        report.append(f"p{i} {names[i] if i < len(names) else i}: OK {pm.width()}x{pm.height()}")
    except Exception as e:
        report.append(f"p{i}: FAIL {type(e).__name__}: {e}")

win.close()
print("\n".join(report))
