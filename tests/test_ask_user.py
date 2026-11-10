# -*- coding: utf-8 -*-
"""ask_user 选项按钮回归测试（2026-11-11）。

用户诉求：助手反问（如「需要我启动实例吗？」）时像 WorkBuddy 那样
在消息下方跳出可点选项。实现 = ask_user 工具（function calling）：
模型给 options → UI 渲染按钮行 → 点击作为用户回复发出。

断言：
  · ask_user 工具注册且 schema 合法（options 数组必填）
  · execute 返回 ok + 规整后的 options（空项剔除、上限 6）
  · system 提示含 ask_user 使用约定
  · UI：tool 事件（name=ask_user）收集 options 到待渲染队列；其它工具不收集
  · UI：_option_row 渲染按钮行，点击 → _quick 发送且整行禁用（防重复）
  · UI：切会话清空待渲染选项（防串台）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ask_user.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_passed = 0
_failed = []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  ok  {name}")
    else:
        _failed.append(name)
        print(f" FAIL {name}  {detail}")


def main():
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QPushButton
    app = QApplication.instance() or QApplication(sys.argv)

    from app import ai_tools, ai_agent
    from app.ui.pages.ai_page import AiPage

    # ---------------------------------------------------------- 1. 工具注册
    print("[1] ask_user 工具注册与 schema")
    t = next((x for x in ai_tools.TOOLS if x["name"] == "ask_user"), None)
    check("工具已注册", t is not None)
    check("options 必填", t and "options" in (t["schema"].get("required") or []),
          f"{t and t['schema']}")
    check("options 是数组 schema",
          t and t["schema"]["properties"]["options"]["type"] == "array", "")
    names = {f["function"]["name"] for f in ai_tools.schemas()}
    check("schemas() 带出 ask_user", "ask_user" in names, f"{names}")

    # ---------------------------------------------------------- 2. 执行
    print("[2] execute 返回选项")
    ctx = ai_tools.build_ctx({}, {})
    r = ai_tools.execute("ask_user", {"question": "启动吗？",
                                      "options": ["启动", "不用了", "", "  "]}, ctx)
    check("ok + 空项剔除", r.get("ok") is True and r.get("options") == ["启动", "不用了"],
          f"{r}")
    r2 = ai_tools.execute("ask_user", {"options": ["1", "2", "3", "4", "5", "6",
                                                   "7"]}, ctx)
    check("上限 6 项", len(r2.get("options") or []) == 6, f"{r2}")

    # ---------------------------------------------------------- 3. system 约定
    print("[3] system 提示含使用约定")
    p = ai_agent._system_prompt(types.SimpleNamespace(inst={"name": "t", "port": 1},
                                                      cfg={}))
    check("含 ask_user 指引", "ask_user" in p and "选项按钮" in p, "")

    # ---------------------------------------------------------- 4. UI 事件收集
    print("[4] tool 事件收集 options")
    fake = types.SimpleNamespace(_pending_options=[], _tool_calls=[])
    fake._tool_item = lambda name, args: fake._tool_calls.append(name)
    fake._think_stop = lambda: None
    AiPage._on_event(fake, {"kind": "tool", "name": "ask_user",
                            "args": {"options": ["A", "B"]}})
    check("ask_user 收集", fake._pending_options == ["A", "B"],
          f"{fake._pending_options}")
    AiPage._on_event(fake, {"kind": "tool", "name": "get_health", "args": {}})
    check("其它工具不覆盖队列", fake._pending_options == ["A", "B"]
          and fake._tool_calls == ["ask_user", "get_health"], "")

    # ---------------------------------------------------------- 5. 选项按钮
    print("[5] _option_row 渲染与点击")
    sent = []
    fake2 = types.SimpleNamespace(_pending_options=["启动实例", "不用了"],
                                  ST_CHIP=AiPage.ST_CHIP,
                                  flow=QVBoxLayout())
    fake2._quick = lambda text: sent.append(text)
    fake2._pick_option = types.MethodType(AiPage._pick_option, fake2)  # 真方法
    AiPage._option_row(fake2)
    row = fake2.flow.itemAt(0).widget()
    btns = row.findChildren(QPushButton)
    check("渲染 2 个选项按钮", [b.text() for b in btns] == ["启动实例", "不用了"],
          f"{[b.text() for b in btns]}")
    check("队列已清（选完即焚）", fake2._pending_options == [], "")
    btns[0].click()
    check("点击 → _quick 发送", sent == ["启动实例"], f"{sent}")
    check("整行禁用防重复", all(not b.isEnabled() for b in btns), "")

    fake3 = types.SimpleNamespace(_pending_options=[], flow=QVBoxLayout())
    AiPage._option_row(fake3)
    check("空队列不渲染", fake3.flow.count() == 0, f"{fake3.flow.count()}")

    # ---------------------------------------------------------- 6. 切会话清队列
    print("[6] 切会话清空待渲染选项")
    fake4 = types.SimpleNamespace(_pending_options=["X"], _worker=None,
                                  _chat_skills=[], _profile={},
                                  _history=[], _chat_id="",
                                  _summary="", _summary_upto=0, _last_user="",
                                  _atts=[{"kind": "image"}])
    fake4._update_session_btn = lambda: None
    fake4._refresh_atts = lambda: None
    fake4._clear_flow = lambda: None
    fake4._greet = lambda: None
    fake4._reload_chat_list = lambda **k: None
    fake4._save_chat = lambda: None
    AiPage._new_chat(fake4)
    check("_new_chat 清空", fake4._pending_options == [], f"{fake4._pending_options}")
    check("_new_chat 清附件", fake4._atts == [], f"{fake4._atts}")

    print(f"\n{_passed} passed, {len(_failed)} failed")
    if _failed:
        print("失败项:", _failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
