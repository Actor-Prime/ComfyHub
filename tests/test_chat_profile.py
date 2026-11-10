# -*- coding: utf-8 -*-
"""会话工作台（profile）回归测试（2026-11-11）。

用户诉求：A 对话做 H3 视频、B 对话做 Qwen 生图、C 对话做 minimax 音乐，
各自挂不同 skill 与工作流，会话之间**不冲突不混乱**。

本测试守住四层：
  [1] norm_profile / is_empty：只留已知字段、去空白
  [2] chat store：profile 存取往返；None 沿用 / {} 清空；list_chats 带 profile
  [3] render_profile：空配置不注入；有配置时钉住用途/工作流/模型 + 跨会话禁令
  [4] ai_tools：ToolCtx 透传 default_workflow；_wf_path 在「没给名字 / 名字不存在 /
      名字正确」三种情况下的行为（默认走绑定工作流，但不能覆盖正确的显式指定）
  [5] run_agent：profile 段真的拼进了 system 提示
  [6] UI 纯逻辑：会话列表标签带用途标；_save_chat 在「只有设定没消息」时也落盘；
      _open_chat 换会话时 profile/skills 一起换

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_chat_profile.py
"""
import os
import sys
import tempfile
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
    from app import ai_skills, ai_chat_store, ai_tools, ai_agent
    from app.ui.pages import ai_page

    # ------------------------------------------------ 1. norm_profile
    print("[1] norm_profile / is_empty")
    p = ai_skills.norm_profile({"topic": "  H3 视频  ", "workflow": "h3.json",
                                "model": "", "junk": "x", "inst_id": None})
    check("去空白 + 丢空值", p == {"topic": "H3 视频", "workflow": "h3.json"}, f"{p}")
    check("未知字段被丢弃", "junk" not in p, f"{p}")
    check("空配置 is_empty", ai_skills.is_empty({}) and ai_skills.is_empty(None)
          and ai_skills.is_empty({"topic": "  "}), "")
    check("有配置 not empty", not ai_skills.is_empty({"topic": "x"}), "")

    # ------------------------------------------------ 2. chat store
    print("[2] chat store profile 存取")
    os.environ["COMFYHUB_CHAT_DIR"] = tempfile.mkdtemp(prefix="chub_prof_")
    try:
        cid = ai_chat_store.new_id()
        prof = {"topic": "H3 视频", "workflow": "h3_i2v.json",
                "model": "h3.safetensors", "inst_id": "i-1"}
        ai_chat_store.save(cid, [{"role": "user", "content": "出个片"}],
                           skills=["prompt-tuning"], profile=prof)
        d = ai_chat_store.load(cid)
        check("profile 存进会话", d.get("profile") == prof, f"{d.get('profile')}")
        check("skills 与 profile 共存", d.get("skills") == ["prompt-tuning"], "")

        # None = 沿用旧值（普通保存不该把设置清掉）
        ai_chat_store.save(cid, [{"role": "user", "content": "再来一版"}])
        check("profile=None 沿用旧值", ai_chat_store.load(cid).get("profile") == prof,
              f"{ai_chat_store.load(cid).get('profile')}")

        # 旧存档没有 profile 字段 → load 出 {}
        old = ai_chat_store.new_id()
        import json
        with open(ai_chat_store._path(old), "w", encoding="utf-8") as f:
            json.dump({"id": old, "title": "旧", "messages": []}, f)
        check("旧存档兼容（无 profile → {}）",
              ai_chat_store.load(old).get("profile") == {}, "")

        # list_chats 要带 profile（列表上标用途靠它）
        lst = {c["id"]: c for c in ai_chat_store.list_chats()}
        check("list_chats 返回 profile", lst.get(cid, {}).get("profile") == prof,
              f"{lst.get(cid, {}).get('profile')}")

        # {} = 显式清空
        ai_chat_store.save(cid, [{"role": "user", "content": "x"}], profile={})
        check("profile={} 清空", ai_chat_store.load(cid).get("profile") == {}, "")
    finally:
        os.environ.pop("COMFYHUB_CHAT_DIR", None)

    # ------------------------------------------------ 3. render_profile
    print("[3] render_profile 注入内容")
    check("空配置不注入", ai_skills.render_profile({}) == ""
          and ai_skills.render_profile(None) == "", "")
    txt = ai_skills.render_profile(
        {"topic": "H3 视频制作", "workflow": "h3_i2v.json",
         "model": "minimax-music.safetensors", "inst_id": "i-1"}, "主力实例")
    check("含工作台头", ai_skills.PROFILE_HEADER in txt, f"{txt[:40]}")
    check("钉住用途", "H3 视频制作" in txt, "")
    check("钉住工作流", "h3_i2v.json" in txt, "")
    check("钉住模型", "minimax-music.safetensors" in txt, "")
    check("带实例名", "主力实例" in txt, "")
    check("有跨会话禁令", "不要沿用别的会话" in txt, f"{txt[-80:]}")
    only_wf = ai_skills.render_profile({"workflow": "a.json"})
    check("只绑工作流也能注入", "a.json" in only_wf and "用途：" not in only_wf,
          f"{only_wf}")

    # ------------------------------------------------ 4. ai_tools 默认工作流
    print("[4] ToolCtx / _wf_path 默认工作流兜底")
    wd = tempfile.mkdtemp(prefix="chub_wf_")
    for n in ("h3_i2v.json", "qwen_image.json"):
        with open(os.path.join(wd, n), "w", encoding="utf-8") as f:
            f.write("{}")
    ctx = ai_tools.ToolCtx({}, {"comfyui_path": tempfile.mkdtemp(prefix="chub_ci_")},
                           profile={"workflow": "h3_i2v.json"})
    ctx.workflows_dir = wd
    check("ToolCtx 透传 default_workflow", ctx.default_workflow == "h3_i2v.json",
          f"{ctx.default_workflow}")
    check("ToolCtx 透传 default_model", ctx.default_model == "", "")

    def wf_res(c, name):
        """跑 _wf_path 并把异常转成 (文件名, 错误串)，避免测试自己崩栈"""
        try:
            return os.path.basename(ai_tools._wf_path(c, name)), ""
        except Exception as e:
            return "", f"{type(e).__name__}: {e}"

    got, err = wf_res(ctx, "")
    check("没给名字 → 用绑定工作流", got == "h3_i2v.json", f"{got or err}")

    got, err = wf_res(ctx, "模型编造的名字.json")
    check("名字不存在 → 回落绑定工作流", got == "h3_i2v.json", f"{got or err}")

    got, err = wf_res(ctx, "qwen_image")
    check("显式指定存在的名字 → 尊重用户", got == "qwen_image.json", f"{got or err}")

    # 没绑定工作流时保持原行为：空名字要报错
    ctx2 = ai_tools.ToolCtx({}, {}, profile=None)
    ctx2.workflows_dir = wd
    check("无绑定时 default_workflow 为空", ctx2.default_workflow == "", "")
    got, err = wf_res(ctx2, "")
    check("无绑定且没给名字 → 仍报错（不静默乱跑）",
          got == "" and err.startswith("ValueError"), f"{got} {err}")

    # 绑定的工作流在目录里不存在 → 不硬塞，走原有报错路径
    ctx3 = ai_tools.ToolCtx({}, {}, profile={"workflow": "gone.json"})
    ctx3.workflows_dir = wd
    got, err = wf_res(ctx3, "")
    check("绑定文件已不存在 → 仍报错（不假装能跑）",
          got == "" and err.startswith("ValueError"), f"{got} {err}")

    # ------------------------------------------------ 5. run_agent 注入
    print("[5] run_agent system 提示注入 profile 段")
    seen = {}

    def fake_round(settings, messages, tools, extra=None, max_tokens=2000,
                   emit=None, log=print, timeout=180):
        seen["sys"] = messages[0]["content"]
        return {"role": "assistant", "content": "好的", "_finish": "stop"}

    real = ai_agent._round_chat
    ai_agent._round_chat = fake_round
    try:
        cfg = {"settings": {"ai_enabled": True, "ai_base_url": "http://x/v1",
                            "ai_api_key": "k", "ai_model": "gpt-4o"}}
        ai_agent.run_agent(cfg, {"name": "实例A", "port": 8188,
                                 "comfyui_path": tempfile.mkdtemp(prefix="chub_r_")},
                           "出个视频", [], emit=lambda *a, **k: None,
                           profile={"topic": "H3 视频", "workflow": "h3_i2v.json"})
    finally:
        ai_agent._round_chat = real
    sys_txt = seen.get("sys") or ""
    check("system 里有工作台段", ai_skills.PROFILE_HEADER in sys_txt, f"{sys_txt[-120:]}")
    check("system 里有绑定工作流", "h3_i2v.json" in sys_txt, "")
    check("system 里有实例名", "实例A" in sys_txt, "")

    # ------------------------------------------------ 6. UI 纯逻辑
    print("[6] UI 会话标签 / 保存 / 切换")
    lb = ai_page.chat_label({"title": "A 对话", "count": 2,
                             "profile": {"topic": "H3 视频"}})
    check("标签带用途", "A 对话" in lb and "H3 视频" in lb and "2 条" in lb, lb)

    lb2 = ai_page.chat_label({"title": "B 对话", "count": 0,
                              "profile": {"workflow": "qwen_image.json"}})
    check("无用途时用工作流名（去 .json）",
          "qwen_image" in lb2 and ".json" not in lb2, lb2)

    lb3 = ai_page.chat_label({"title": "C 对话", "count": 0, "profile": {}})
    check("无配置时不加尾巴", lb3 == "C 对话", lb3)

    # _save_chat：只有设定、没有消息也要落盘
    os.environ["COMFYHUB_CHAT_DIR"] = tempfile.mkdtemp(prefix="chub_ui_")
    try:
        cid = ai_chat_store.new_id()
        fake = types.SimpleNamespace(
            _chat_id=cid, _history=[], _summary="", _summary_upto=0,
            _chat_skills=[], _profile={"topic": "minimax 音乐"})
        ai_page.AiPage._save_chat(fake)
        d = ai_chat_store.load(cid)
        check("纯设定也落盘", d.get("profile", {}).get("topic") == "minimax 音乐",
              f"{d.get('profile')}")
        check("空历史时用用途当标题", d.get("title") == "minimax 音乐",
              f"{d.get('title')}")

        # 空会话、空设定 → 不产生垃圾记录
        cid2 = ai_chat_store.new_id()
        fake2 = types.SimpleNamespace(
            _chat_id=cid2, _history=[], _summary="", _summary_upto=0,
            _chat_skills=[], _profile={})
        ai_page.AiPage._save_chat(fake2)
        check("全空不落盘", not os.path.isfile(ai_chat_store._path(cid2)), "")

        # 手动改名不被自动标题覆盖（多会话场景下名字就是身份）
        cid4 = ai_chat_store.new_id()
        ai_chat_store.save(cid4, [{"role": "user", "content": "第一条消息内容"}])
        ai_chat_store.rename(cid4, "H3 视频专用")
        ai_chat_store.save(cid4, [{"role": "user", "content": "第一条消息内容"},
                                  {"role": "assistant", "content": "好"}])
        check("手动改名不被首条消息覆盖",
              ai_chat_store.load(cid4).get("title") == "H3 视频专用",
              f"{ai_chat_store.load(cid4).get('title')}")

        # _open_chat：附件也要清（否则视频会话的图会跟到生图会话）
        cid5 = ai_chat_store.new_id()
        ai_chat_store.save(cid5, [{"role": "user", "content": "写首歌"}],
                           profile={"topic": "minimax 音乐"})
        opened2 = types.SimpleNamespace(
            _chat_id="", _history=[], _summary="", _summary_upto=0,
            _chat_skills=[], _profile={}, _pending_options=[],
            _last_user="", _atts=[{"kind": "image"}],
            _save_chat=lambda: None, _clear_flow=lambda: None,
            _greet=lambda: None, _update_session_btn=lambda: None,
            _refresh_atts=lambda: None,
            _user_bubble=lambda t: None, _ai_card=lambda t: None)
        ai_page.AiPage._open_chat(opened2, cid5)
        check("切会话清空待发附件", opened2._atts == [], f"{opened2._atts}")
        cid3 = ai_chat_store.new_id()
        ai_chat_store.save(cid3, [{"role": "user", "content": "出图"}],
                           skills=["error-diagnosis"],
                           profile={"topic": "Qwen 生图", "workflow": "qwen_image.json"})
        opened = types.SimpleNamespace(
            _chat_id="", _history=[], _summary="", _summary_upto=0,
            _chat_skills=["prompt-tuning"], _profile={"topic": "旧用途"},
            _pending_options=["旧选项"], _last_user="", _atts=[],
            _save_chat=lambda: None,
            _clear_flow=lambda: None,
            _greet=lambda: opened_bits.append("greet"),
            _update_session_btn=lambda: None,
            _refresh_atts=lambda: None,
            _user_bubble=lambda t: None, _ai_card=lambda t: None)
        opened_bits = []
        ai_page.AiPage._open_chat(opened, cid3)
        check("切会话后 profile 换新", opened._profile.get("topic") == "Qwen 生图",
              f"{opened._profile}")
        check("切会话后 skills 换新", opened._chat_skills == ["error-diagnosis"],
              f"{opened._chat_skills}")
        check("切会话后旧选项清空", opened._pending_options == [], "")
    finally:
        os.environ.pop("COMFYHUB_CHAT_DIR", None)

    print()
    print(f"PASS {_passed} / FAIL {len(_failed)}")
    if _failed:
        for f in _failed:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
