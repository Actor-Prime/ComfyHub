# -*- coding: utf-8 -*-
"""AI 多会话持久化回归（2026-11-06）

用户需求：AI 页要能新建对话、分开任务，且重启不丢上下文。
"""
import os
import sys
import pathlib
import shutil
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0


def check(name, ok, detail=""):
    global PASS
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        sys.exit(1)
    PASS += 1


tmp = tempfile.mkdtemp(prefix="ch_chats_")
os.environ["COMFYHUB_CHAT_DIR"] = tmp

from app import ai_chat_store as st  # noqa: E402

# ---- 1. 基本存取 ----
c1 = st.new_id()
check("1a 会话 id 唯一", c1 != st.new_id())
check("1b 空 id 时无历史", st.list_chats() == [])

st.save(c1, [{"role": "user", "content": "第一个问题：怎么装插件"}])
d = st.load(c1)
check("1c 保存后可读回", len(d["messages"]) == 1 and d["messages"][0]["content"].startswith("第一个问题"))
check("1d 标题取首条用户消息", d["title"].startswith("第一个问题"), d["title"])

st.save(c1, d["messages"] + [{"role": "assistant", "content": "这样装"}])
check("1e 续写不丢历史", len(st.load(c1)["messages"]) == 2)
check("1f 列表含该会话", [c["id"] for c in st.list_chats()] == [c1])
check("1g 列表带条数", st.list_chats()[0]["count"] == 2)

# ---- 2. 多会话互不干扰 ----
c2 = st.new_id()
st.save(c2, [{"role": "user", "content": "第二个任务：换显卡"}])
ids = [c["id"] for c in st.list_chats()]
check("2a 两个会话都在", set(ids) == {c1, c2}, str(ids))
check("2b 最新在前", ids[0] == c2, str(ids))
check("2c 内容互不串台",
      st.load(c1)["messages"][0]["content"].startswith("第一个")
      and st.load(c2)["messages"][0]["content"].startswith("第二个"))

# ---- 3. 重命名 / 删除 ----
st.rename(c2, "显卡相关")
check("3a 重命名生效",
      next(c["title"] for c in st.list_chats() if c["id"] == c2) == "显卡相关")
st.delete(c2)
check("3b 删除后不在列表", [c["id"] for c in st.list_chats()] == [c1])

# ---- 4. 只存 role/content，且截断 ----
st.save(c1, [{"role": "user", "content": "x" * 50000, "images": ["big.png"]}])
msgs = st.load(c1)["messages"]
check("4a 只保留 role/content", set(msgs[0]) == {"role", "content"}, str(set(msgs[0])))
check("4b 超长内容被截断", len(msgs[0]["content"]) <= st.MAX_CHARS, str(len(msgs[0]["content"])))

many = [{"role": "user", "content": f"m{i}"} for i in range(st.MAX_MSGS + 40)]
st.save(c1, many)
check("4c 条数上限生效", len(st.load(c1)["messages"]) == st.MAX_MSGS,
      str(len(st.load(c1)["messages"])))

# ---- 5. 重启不丢（模拟：模块重载后仍能读到）----
import importlib  # noqa: E402
importlib.reload(st)
check("5a 重载后仍能列出会话", st.list_chats() and st.list_chats()[0]["id"] == c1)
check("5b 重载后内容完整", len(st.load(c1)["messages"]) == st.MAX_MSGS)

# ---- 5.5 压缩摘要随会话一起存（2026-11-06）----
st.save(c1, [{"role": "user", "content": "长对话"}], summary="要点A；要点B", upto=7)
_d = st.load(c1)
check("5c 摘要与覆盖条数可持久化", _d["summary"] == "要点A；要点B" and _d["summary_upto"] == 7,
      str((_d["summary"], _d["summary_upto"])))
st.save(c1, _d["messages"])          # 普通保存（不传摘要）
check("5d 普通保存不会清掉摘要", st.load(c1)["summary"] == "要点A；要点B",
      st.load(c1)["summary"])
st.save(c1, [{"role": "user", "content": "重启前的问题"},
             {"role": "assistant", "content": "重启前的回答"}])

# ---- 6. 界面能恢复（离屏构造 AiPage）----
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
app = QApplication.instance() or QApplication([])
from app.ui.pages.ai_page import AiPage  # noqa: E402


class _FakeWin:
    def current_inst(self):
        return {}


page = AiPage({}, _FakeWin())
check("6a 启动时选中上次会话", page._chat_id == c1, page._chat_id)
check("6b 历史被恢复到内存", len(page._history) == 2, str(page._history))
check("6c 左侧会话栏列出历史",
      page.lst_chat.count() >= 1
      and any(page.lst_chat.item(i).data(Qt.UserRole) == c1
              for i in range(page.lst_chat.count())),
      [page.lst_chat.item(i).text() for i in range(page.lst_chat.count())])
page._new_chat()
check("6d 新建对话清空上下文", page._history == [] and page._chat_id != c1)
page._history.append({"role": "user", "content": "新对话内容"})
page._save_chat()
check("6e 新会话落盘", any(c["id"] == page._chat_id for c in st.list_chats()))
page.close()

shutil.rmtree(tmp, ignore_errors=True)
os.environ.pop("COMFYHUB_CHAT_DIR", None)

print(f"共 {PASS} 项，全部通过")
