# -*- coding: utf-8 -*-
"""AI 页右侧概要抽屉出图（简介 / 产物 / 技能三页）

用法：cd F:/ComfyHub && .venv_build/Scripts/python.exe tests/shoot_ai_drawer.py
输出：analysis/ai_drawer_{brief,art,skill}.png
"""
import os
import sys
import time

os.environ["COMFYHUB_CHAT_DIR"] = os.path.join(
    os.environ.get("TEMP", "."), "ch_shot_chats")
os.environ["COMFYHUB_SKILL_DIR"] = os.path.join(
    os.environ.get("TEMP", "."), "ch_shot_skills")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

app = QApplication(sys.argv)


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


from app.ui.main_window import MainWindow  # noqa: E402

OUT = os.path.join(ROOT, "analysis")
os.makedirs(OUT, exist_ok=True)

win = MainWindow()
_w = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
_h = int(sys.argv[2]) if len(sys.argv) > 2 else 940
win.resize(_w, _h)
win.show()
pump(2.0)

page = win.ai_page
for i in range(win.stack.count()):
    if win.stack.widget(i) is page:
        win.goto(i)
        break
pump(1.0)

# 造一个「像真的」会话：设定 + 技能 + 产物 + 摘要
page._profile = {"topic": "H3 视频专用", "workflow": "H3_i2v_15s.json",
                 "model": "h3-q8_0.gguf", "inst_id": ""}
skills = [s["id"] for s in __import__("app.ai_skills", fromlist=["x"]).list_skills()][:2]
page._chat_skills = skills
page._summary = ("用户在做 H3 图生视频：先用 Z-Image 出首帧，再用 H3 工作流跑 15 秒镜头；"
                 "已经定好 13 个分镜，画幅 1280x720，24fps。")
page._history = [{"role": "user", "content": "帮我跑一下第 3 个分镜"},
                 {"role": "assistant", "content": "已提交，队列第 2 位"}]
page._artifacts = [
    {"kind": "image", "path": "C:/out/H3_shot03_00001.png", "label": "H3_shot03_00001.png", "ts": 0},
    {"kind": "image", "path": "C:/out/H3_shot03_00002.png", "label": "H3_shot03_00002.png", "ts": 0},
    {"kind": "workflow", "path": "F:/ComfyHub/workflows/H3_i2v_15s.json",
     "label": "H3_i2v_15s.json", "ts": 0},
    {"kind": "model", "path": "F:/ComfyUI/models/unet/h3-q8_0.gguf",
     "label": "h3-q8_0.gguf", "ts": 0},
]
page._apply_right_open(True, remember=False)
page._update_session_btn()
pump(0.6)

for idx, name in ((0, "brief"), (1, "art"), (2, "skill")):
    page.tabs_r.setCurrentIndex(idx)
    pump(0.5)
    p = os.path.join(OUT, f"ai_drawer_{name}.png")
    win.grab().save(p)
    print("saved", p)

# 收起态（只剩把手）
page._apply_right_open(False, remember=False)
pump(0.5)
win.grab().save(os.path.join(OUT, "ai_drawer_closed.png"))
print("saved ai_drawer_closed.png")
print("把手可见:", page.right_handle.isVisible(), "面板可见:", page.side_r.isVisible())
win.close()
