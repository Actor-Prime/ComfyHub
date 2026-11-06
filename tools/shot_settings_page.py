# -*- coding: utf-8 -*-
"""渲染设置页截图，验证「保存设置」是否底部常驻（临时校验脚本）"""
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 9))
try:
    from app.ui.theme import apply_theme
    apply_theme(app, {"settings": {"theme": "dark"}})
except Exception:
    pass

from app.ui.pages.settings_page import SettingsPage  # noqa: E402


class _W:
    cfg = {"settings": {}, "instances": []}

    def current_inst(self):
        return {}


w = SettingsPage(_W.cfg, _W())
w.resize(1000, 720)
w.show()
for _ in range(10):
    app.processEvents()

# 滚到中间，证明按钮仍在底部
sc = w.findChild(QScrollArea)
sb = sc.verticalScrollBar()
sb.setValue(int(sb.maximum() * 0.45))
for _ in range(10):
    app.processEvents()

out = ROOT / "docs" / "settings_sticky_save.png"
w.grab().save(str(out))
print("saved", out, "| 滚动位置", sb.value(), "/", sb.maximum(),
      "| 按钮可见:", w.b_save.isVisible())
w.hide()
