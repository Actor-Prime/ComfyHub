# -*- coding: utf-8 -*-
"""真平台截图：核验「坏工具参数 500 专用文案 + 重试 notice 分流」（2026-11-17）。

用法：QT_QPA_PLATFORM=windows python tests/shot_toolargs_err.py <out.png>
（offscreen 下 CJK 变豆腐块，截图核验必须用 windows 平台）
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui.pages.ai_page import AiPage  # noqa: E402
from app import ai_assist as A  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "shot_toolargs_err.png"
now = time.time()


class _FakeWin:
    cfg = {}

    def current_inst(self):
        return {}


page = AiPage({}, _FakeWin())
page.resize(980, 760)
page.show()
app.processEvents()

# ---- 历史：用户发过一条消息 ----
from app import ai_chat_store  # noqa: E402

hist = [
    {"role": "user", "content": "帮我跑一下默认文生图工作流", "ts": now - 30},
]
page._chat_id = None
ai_chat_store.save("__shot_toolargs__", hist, title="截图")
page._open_chat("__shot_toolargs__")
app.processEvents()

# ---- D2 notice：服务端 500 的重试提示（不再说「连接中断」）----
page._on_event({"kind": "notice",
                "text": "服务端返回 500，2 秒后自动重试（第 1/2 次）"})
app.processEvents()

# ---- D1 error：坏工具参数 500（用户截图服务端原文）----
raw = ("Failed to parse tool call arguments as JSON: "
       "[json.exception.parse_error.101] parse error at line 1, "
       "column 9719: syntax error w")
text = A.describe_error(A.ApiError(500, raw))
page._on_event({"kind": "error", "text": text, "raw": raw})
app.processEvents()

# 断言（截图之外的硬校验）
assert "不是合法 JSON" in text, text[:100]
assert "厂商" not in text, "不该再甩锅厂商: " + text[:100]
assert "换一个模型" in text and "新对话" in text, text[:200]
assert raw[:40] in text, "应附服务端原文"

# 文案必须真的渲染进了页面（不是只在字符串里）
from PySide6.QtWidgets import QLabel  # noqa: E402

page._relayout_messages()
app.processEvents()
_rendered = " ".join(lb.text() for lb in page.findChildren(QLabel))
assert "不是合法 JSON" in _rendered, "error 文案未渲染进页面"
assert "服务端返回 500" in _rendered, "D2 notice 未渲染进页面"
assert "连接中断" not in _rendered, "不该出现连接中断"

page.grab().save(out)
print("saved:", out)
print("err:", text.replace("\n", " / ")[:240])
page.close()
