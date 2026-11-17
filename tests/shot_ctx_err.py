# -*- coding: utf-8 -*-
"""真平台截图：核验「上下文超限专用文案 + error 收卡标已中断」（2026-11-17）。

用法：QT_QPA_PLATFORM=windows python tests/shot_ctx_err.py <out.png>
（offscreen 下 CJK 变豆腐块，截图核验必须用 windows 平台）
"""
import os
import sys
import time

# 平台由命令行 QT_QPA_PLATFORM 决定；没设才默认 windows（截图要真渲染）
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.pages.ai_page import AiPage  # noqa: E402
from app import ai_assist as A  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "shot_ctx_err.png"
now = time.time()


class _FakeWin:
    cfg = {}

    def current_inst(self):
        return {}


page = AiPage({}, _FakeWin())
page.resize(980, 760)
page.show()
app.processEvents()

# ---- 历史：用户发过消息 + AI 正在流式回 ----
from app import ai_chat_store  # noqa: E402

hist = [
    {"role": "user", "content": "帮我生成一张 JK 制服三视图，正面/侧面/背面",
     "ts": now - 60},
]
page._chat_id = None
ai_chat_store.save("__shot_ctx__", hist, title="截图")
page._open_chat("__shot_ctx__")
app.processEvents()

# 开流式卡（模拟 AI 正在打字；kind=delta 才是流式中，assistant 是收尾）
page._on_event({"kind": "delta", "text": "正在 patch workflow，节点 34 不存在，"
                                         "改用子图节点 ag:bd4bd7ec-xxx:34 重试"})
for _ in range(6):
    page._on_event({"kind": "delta", "text": "，参数校验通过"})
    app.processEvents()
page._stream_flush()
app.processEvents()
assert page._stream is not None, "流式卡应开着"

# ---- error：上下文超限 400（用户截图原文）----
raw = ("request (8243 tokens) exceeds the available context size (8192 tokens)")
text = A.describe_error(A.ApiError(400, raw))
page._on_event({"kind": "error", "text": text, "raw": raw})
app.processEvents()

# 断言（截图之外的硬校验）
assert page._stream is None, "error 后流式卡必须收尾"
cap = page._last_card["cap"].text() if page._last_card else ""
assert "已中断" in cap, f"标题应含已中断: {cap}"
assert "上下文已满" in text and "8243" in text, text[:80]
assert "参数或模型名有问题" not in text, "不该再误导改模型名"

page._relayout_messages()
app.processEvents()
page.grab().save(out)
print("saved:", out)
print("cap:", cap)
print("err:", text.replace("\n", " / ")[:200])
page.close()
