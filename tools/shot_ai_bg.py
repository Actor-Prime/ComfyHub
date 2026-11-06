# -*- coding: utf-8 -*-
"""验证：自定义背景图下 AI 页是否还看得清（渲染带背景的对比图，临时脚本）"""
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["COMFYHUB_CHAT_DIR"] = str(pathlib.Path(os.environ.get("TEMP", ".")) / "shot_chats_bg")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QPixmap, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 9))
from app.ui.theme import apply_theme  # noqa: E402

BG = r"C:\Users\10696\AppData\Roaming\ComfyHub\backgrounds\bg_1793963367.jpg"
cfg = {"settings": {"theme": "custom", "bg_image": BG, "bg_opacity": 75,
                    "ai_model": "mimo-v2.6-flash"}}
apply_theme(app, cfg)

from app.ui.pages.ai_page import AiPage  # noqa: E402


class _W:
    def current_inst(self):
        return {"id": "i", "name": "ComfyUI", "port": 8188, "comfyui_path": "F:/X"}


class Backdrop(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        pm = QPixmap(BG)
        if not pm.isNull():
            p.drawPixmap(self.rect(), pm.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                                Qt.SmoothTransformation))


host = Backdrop()
host.resize(1040, 700)
lay = QVBoxLayout(host)
lay.setContentsMargins(0, 0, 0, 0)
page = AiPage(cfg, _W())
lay.addWidget(page)
host.show()
for _ in range(10):
    app.processEvents()

page._clear_flow()
for _ in range(6):
    app.processEvents()
page._user_bubble("帮我启动一下")
page._ai_card("实例已启动就绪：http://127.0.0.1:8188（pid 16536，端口 8188）。")
for _ in range(12):
    app.processEvents()

out = ROOT / "docs" / "ai_page_bg_fix.png"
host.grab().save(str(out))
print("saved", out)
host.hide()
