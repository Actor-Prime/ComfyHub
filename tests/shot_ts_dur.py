# -*- coding: utf-8 -*-
"""离屏截图：核验「发送时间 + 本轮耗时」渲染（2026-11-17 用户反馈）。

用法：QT_QPA_PLATFORM=offscreen python tests/shot_ts_dur.py <out.png>
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.pages.ai_page import AiPage  # noqa: E402


class _FakeWin:
    cfg = {}

    def current_inst(self):
        return {}


out = sys.argv[1] if len(sys.argv) > 1 else "shot_ts_dur.png"
now = time.time()
page = AiPage({}, _FakeWin())
page.resize(900, 700)
page.show()
app.processEvents()

# 注入带 ts/dur 的历史（等价于重启后 _open_chat 回放）。
# _open_chat 会从磁盘 load，所以先落盘再打开。
from app import ai_chat_store  # noqa: E402

hist = [
    {"role": "user", "content": "帮我看看现在有几个实例在跑",
     "ts": now - 95},
    {"role": "assistant", "content": "当前有 2 个实例在跑：sd-webui（运行中）与 comfyui-default（空闲）。",
     "ts": now - 88, "dur": 7.4},
    {"role": "user", "content": "把空闲那个关掉", "ts": now - 30},
    {"role": "assistant", "content": "已停止 comfyui-default。",
     "ts": now - 24, "dur": 6.2},
    # 老数据（无 ts/dur）：应不显时间/耗时、不造假
    {"role": "user", "content": "这条是老会话消息"},
    {"role": "assistant", "content": "老会话的回复，标题里不该出现耗时。"},
]
page._chat_id = None          # 防止 _open_chat 先把空历史存回去
ai_chat_store.save("__shot__", hist, title="截图")
page._open_chat("__shot__")
app.processEvents()

# 重排一次，模拟真实窗口宽度
page._relayout_messages()
app.processEvents()

page.grab().save(out)
print("saved:", out)
page.close()
