# -*- coding: utf-8 -*-
"""真平台截图：核验「流式输出时上翻不被拽回」（2026-11-17 用户反馈）。

用法：QT_QPA_PLATFORM=windows python tests/shot_scroll_up.py <out.png>
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.pages.ai_page import AiPage  # noqa: E402
from app import ai_chat_store  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "shot_scroll_up.png"
now = time.time()


class _FakeWin:
    cfg = {}

    def current_inst(self):
        return {}


page = AiPage({}, _FakeWin())
page.resize(980, 720)
page.show()

# 历史：几条长回复，撑出滚动行程
hist = []
for i in range(6):
    hist.append({"role": "user", "content": f"问题 {i}：帮我检查实例状态", "ts": now - 600 + i * 30})
    hist.append({"role": "assistant",
                 "content": (f"回答 {i}：**检查结果**\n"
                             f"- 端口 8188 监听正常\n- 显存 4.38/11.99GB\n"
                             f"- 队列 0 跑 0 排\n" + "细节补充。" * 40),
                 "ts": now - 595 + i * 30, "dur": 12.3})
page._chat_id = None
ai_chat_store.save("__shot_scroll__", hist, title="截图")
page._open_chat("__shot_scroll__")
QTest.qWait(120)

sb = page.scroll.verticalScrollBar()
assert sb.maximum() > 400, f"滚动条行程不足: {sb.maximum()}"

# 用户上翻到偏上位置（看第 1-2 轮历史）
sb.setValue(int(sb.maximum() * 0.25))
QTest.qWait(80)
mid = sb.value()
assert 0 < mid < sb.maximum()

# AI 正在流式输出（本轮在最底部），连续 flush 多次
page._on_event({"kind": "delta", "text": "正在检查实例状态"})
for _ in range(5):
    page._on_event({"kind": "delta", "text": "，读取队列中……"})
    page._stream_flush()
    QTest.qWait(70)
QTest.qWait(150)

moved = abs(sb.value() - mid)
print(f"mid={mid} now={sb.value()} moved={moved} max={sb.maximum()}")
assert moved <= 4, f"上翻被拽回了！移动 {moved}px"

page.grab().save(out)
print("saved:", out)
page.close()
