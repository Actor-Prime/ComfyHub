# -*- coding: utf-8 -*-
"""切换会话后自动贴底回归（2026-11-14 用户报障）

需求：在左侧点一个会话，视图应该直接落在**最新一条**，
      不该让用户自己把滚动条往下拖。

坑（这次踩的就是它）：气泡走 setFixedWidth + 自动换行，高度要等布局
激活才算出；渲染完立刻 setValue(maximum()) 拿到旧值（常见是 0），
所以只滚一次不够 —— 实现是「短窗口期内跟着 rangeChanged 贴底」，
并且用户一动滚动条就解除，不跟用户抢。

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ai_chat_scroll.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="ch_scroll_")
os.environ["COMFYHUB_CHAT_DIR"] = os.path.join(_TMP, "chats")
os.environ["COMFYHUB_SKILL_DIR"] = os.path.join(_TMP, "skills")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import ai_chat_store  # noqa: E402
from app.ui.pages.ai_page import AiPage  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print(" FAIL " + name + ("  ← " + str(detail) if detail else ""))


INST = {"id": "i1", "name": "实例甲", "port": 8188,
        "comfyui_path": "F:/A", "python_path": ""}


class _MW:
    def __init__(self):
        self.cfg = {"instances": [INST], "settings": {}}

    def current_inst(self):
        return INST


def pump(ms=300):
    """跑一会儿事件循环：布局/换行高度要几轮事件才算稳定"""
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def long_chat(n=14):
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": "第 %d 个问题：这张图为什么糊" % i})
        msgs.append({"role": "assistant",
                     "content": ("第 %d 个回答。" % i) + "这里是足够长的正文，" * 6
                                + "\n- 要点一\n- 要点二\n- 要点三"})
    return msgs


def main():
    ai_chat_store.save("long", long_chat(), title="长会话")
    ai_chat_store.save("short", [{"role": "user", "content": "短会话第一条"},
                                 {"role": "assistant", "content": "短会话回答"}],
                       title="短会话")

    page = AiPage({"_MW": 1}, _MW())
    page.resize(900, 520)          # 故意矮一点，保证内容一定溢出
    page.show()
    pump(400)

    print("== A. 点开长会话 = 直接落在最新一条 ==")
    page._open_chat("long")
    pump(600)
    sb = page.scroll.verticalScrollBar()
    check("内容确实超出一屏（前提成立）", sb.maximum() > 0, sb.maximum())
    check("滚动条已在底部", sb.value() == sb.maximum(),
          "value=%d max=%d" % (sb.value(), sb.maximum()))

    print("== B. 从长会话切到另一个会话、再切回来 ==")
    page._open_chat("short")
    pump(600)
    page._open_chat("long")
    pump(600)
    sb = page.scroll.verticalScrollBar()
    check("切回来仍在底部", sb.value() == sb.maximum(),
          "value=%d max=%d" % (sb.value(), sb.maximum()))

    print("== C. 通过列表点击触发（真实交互路径） ==")
    page._open_chat("short")
    pump(400)
    idx = [i for i in range(page.lst_chat.count())
           if page.lst_chat.item(i).data(0x0100) == "long"][0]
    page.lst_chat.setCurrentRow(idx)     # 等价于用户点这一行
    pump(700)
    sb = page.scroll.verticalScrollBar()
    check("点列表项也贴底", sb.value() == sb.maximum(),
          "value=%d max=%d" % (sb.value(), sb.maximum()))

    print("== D. 用户主动上滑后不再被拽回底部 ==")
    sb.setValue(0)
    page._unpin_bottom()
    pump(300)
    check("手动滚到顶部后保持不动", sb.value() == 0, sb.value())
    check("解除贴底后 rangeChanged 不再改写位置", page._pin_until == 0.0,
          page._pin_until)

    page.close()
    print("\nPASS %d / FAIL %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
