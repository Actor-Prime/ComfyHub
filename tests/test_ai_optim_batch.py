# -*- coding: utf-8 -*-
"""AI 助手四项优化回归（2026-11-14）

A. skill 声明工具 + 按会话收窄工具表
B. 单轮失败自动重试（只对可恢复错误）
C. 同参数只读工具结果复用
D. token 用量统计（有 usage 用精确值，没有走估算）

跑法：.venv_build/Scripts/python.exe tests/test_ai_optim_batch.py
"""
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 隔离数据目录：避免读到用户真实技能/配置（内置技能在 assets 下，不受影响）
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="ch_optim_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL = 0, 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


from app import ai_skills, ai_tools, ai_agent          # noqa: E402


# ------------------------------------------------------------------ A. 技能工具
def part_a():
    print("== A. skill 声明工具 → 工具表收窄 ==")
    have = {s["id"]: s for s in ai_skills.list_skills()}

    check("A1 内置「报错诊断」声明了工具",
          have.get("error-diagnosis", {}).get("tools") ==
          ["get_logs", "queue_list", "list_workflows", "check_workflow",
           "control_instance"],
          str(have.get("error-diagnosis", {}).get("tools")))
    check("A2 内置「提示词优化」声明为空集（纯文本技能）",
          have.get("prompt-tuning", {}).get("tools") == [],
          str(have.get("prompt-tuning", {}).get("tools")))
    check("A3 内置「工作流调参」声明了 5 个工具",
          len(have.get("workflow-tuning", {}).get("tools") or []) == 5,
          str(have.get("workflow-tuning", {}).get("tools")))

    # 元信息不能被注入正文（否则模型会看到一堆 HTML 注释）
    for sid in ("error-diagnosis", "prompt-tuning", "workflow-tuning"):
        body = have.get(sid, {}).get("body") or ""
        check(f"A4 {sid} 正文不含 tools 注释", "<!--" not in body, body[:60])
    check("A5 描述仍能正常抽取",
          (have.get("error-diagnosis", {}).get("desc") or "").startswith("按固定流程"),
          (have.get("error-diagnosis", {}).get("desc") or "")[:30])

    # 未声明 = 不限制（向后兼容：用户自定义技能大多不会写声明）
    d = tempfile.mkdtemp(prefix="ch_sk_")
    p = os.path.join(d, "my-skill.md")
    io.open(p, "w", encoding="utf-8").write("# 我的技能\n做点事情。\n")
    old = ai_skills.user_dir
    ai_skills.user_dir = lambda: d
    try:
        check("A6 自建技能未声明 → tools 为 None",
              ai_skills.get_skill("my-skill").get("tools") is None)
        check("A7 未声明技能的会话 → 不限制工具",
              ai_skills.tools_for(["my-skill"]) is None,
              str(ai_skills.tools_for(["my-skill"])))
        # 一条声明一条不声明 → 也退回不限制（保守，别把人卡死）
        check("A8 声明+未声明混勾 → 退回不限制",
              ai_skills.tools_for(["error-diagnosis", "my-skill"]) is None)
    finally:
        ai_skills.user_dir = old

    check("A9 不勾技能 → 不限制", ai_skills.tools_for([]) is None)
    check("A10 勾不存在的技能 → 不限制",
          ai_skills.tools_for(["no-such-skill"]) is None)

    only = ai_skills.tools_for(["error-diagnosis"])
    check("A11 单技能 → 返回其工具集", only == set(have["error-diagnosis"]["tools"]),
          str(only))
    both = ai_skills.tools_for(["error-diagnosis", "workflow-tuning"])
    check("A12 多技能 → 取并集",
          both == set(have["error-diagnosis"]["tools"]) |
          set(have["workflow-tuning"]["tools"]), str(both))

    # schemas 过滤
    full = ai_tools.schemas()
    check("A13 全量工具表 19 个", len(full) == 19, str(len(full)))
    sub = ai_tools.schemas(only)
    names = {t["function"]["name"] for t in sub}
    check("A14 收窄后只剩声明工具 + 元能力",
          names == only | set(ai_skills.BASE_TOOLS), str(names))
    check("A15 元能力恒在（get_health/ask_user/recall_history）",
          set(ai_skills.BASE_TOOLS) <= names, str(names))
    check("A16 空集收窄 → 只剩 3 个元能力",
          len(ai_tools.schemas(set())) == 3,
          str([t["function"]["name"] for t in ai_tools.schemas(set())]))
    check("A17 None → 不收窄", len(ai_tools.schemas(None)) == 19)

    note = ai_skills.tool_note(["error-diagnosis"])
    check("A18 tool_note 列出工具名", "get_logs" in note and "共 8 个" in note, note[:60])
    check("A19 tool_note 明确禁止调清单外工具", "不要调用清单外的工具" in note)
    check("A20 不限制时 tool_note 为空（不浪费 token）",
          ai_skills.tool_note([]) == "")


# ------------------------------------------------------------------ B. 重试
def part_b():
    print("== B. 单轮失败自动重试 ==")
    import urllib.error

    check("B1 超时可重试", ai_agent._is_retryable(TimeoutError("timed out")))
    check("B2 连接重置可重试",
          ai_agent._is_retryable(ConnectionResetError(10054, "reset")))
    check("B3 5xx 可重试",
          ai_agent._is_retryable(urllib.error.HTTPError("u", 503, "busy", None, None)))
    check("B4 429 可重试",
          ai_agent._is_retryable(urllib.error.HTTPError("u", 429, "slow", None, None)))
    check("B5 401 不重试（Key 问题重试没用）",
          not ai_agent._is_retryable(
              urllib.error.HTTPError("u", 401, "no", None, None)))
    check("B6 404 不重试",
          not ai_agent._is_retryable(
              urllib.error.HTTPError("u", 404, "nf", None, None)))
    check("B7 端口拒绝不重试（该去 control_instance）",
          not ai_agent._is_retryable(ConnectionRefusedError(10061, "refused")))
    check("B8 FileNotFoundError 不重试（不是网络问题）",
          not ai_agent._is_retryable(FileNotFoundError("x")))
    check("B9 ApiError 401 不重试",
          not ai_agent._is_retryable(ai_agent.ai_assist.ApiError(401, "x")))
    check("B10 ApiError 500 可重试",
          ai_agent._is_retryable(ai_agent.ai_assist.ApiError(500, "x")))

    # 真跑一遍：前两次超时，第三次成功
    ST = {"settings": {"ai_enabled": True, "ai_base_url": "http://x",
                       "ai_api_key": "k", "ai_model": "t", "ai_compress": "off"}}
    tries = {"n": 0}
    orig_round = ai_agent._round_chat
    orig_sleep = ai_agent.time.sleep
    orig_detect = ai_agent.ai_models.detect
    ai_agent.time.sleep = lambda s: None          # 别真等
    ai_agent.ai_models.detect = lambda m, b="": ai_agent.ai_models.Profile(
        "t", "t", "none", 131072, True, "off", 4000, "")

    def flaky(*a, **kw):
        tries["n"] += 1
        if tries["n"] <= 2:
            raise TimeoutError("等待模型响应超时或连接失败（45s）：TimeoutError")
        return {"role": "assistant", "content": "好了", "_usage": {}}

    ev = []
    ai_agent._round_chat = flaky
    try:
        ai_agent.run_agent(ST, {"port": 8188, "comfyui_path": "F:/X"}, "在吗",
                           [], lambda k, **kw: ev.append({"kind": k, **kw}))
    finally:
        ai_agent._round_chat = orig_round
        ai_agent.time.sleep = orig_sleep
        ai_agent.ai_models.detect = orig_detect
    kinds = [e["kind"] for e in ev]
    check("B11 超时两次后自动重试成功", tries["n"] == 3, str(tries["n"]))
    check("B12 成功后给出回答", "assistant" in kinds, str(kinds))
    check("B13 重试过程有 notice 提示（用户知道在等）",
          kinds.count("notice") == 2, str(kinds))
    check("B14 没有误报 error", "error" not in kinds, str(kinds))

    # 已经吐过字再失败 → 不重试（否则同一段话重复两遍）
    tries2 = {"n": 0}

    def half_then_fail(*a, **kw):
        tries2["n"] += 1
        emit = kw.get("emit")
        if emit:
            emit("delta", text="我觉得")
        raise TimeoutError("等待模型响应超时")

    ev2 = []
    ai_agent._round_chat = half_then_fail
    ai_agent.time.sleep = lambda s: None
    ai_agent.ai_models.detect = lambda m, b="": ai_agent.ai_models.Profile(
        "t", "t", "none", 131072, True, "off", 4000, "")
    try:
        ai_agent.run_agent(ST, {"port": 8188, "comfyui_path": "F:/X"}, "在吗",
                           [], lambda k, **kw: ev2.append({"kind": k, **kw}))
    finally:
        ai_agent._round_chat = orig_round
        ai_agent.time.sleep = orig_sleep
        ai_agent.ai_models.detect = orig_detect
    check("B15 已输出正文后失败不重试", tries2["n"] == 1, str(tries2["n"]))
    check("B16 这种情况仍然报错给用户",
          "error" in [e["kind"] for e in ev2], str([e["kind"] for e in ev2]))

    # 不可恢复错误 → 一次就停
    tries3 = {"n": 0}

    def bad_key(*a, **kw):
        tries3["n"] += 1
        raise ai_agent.ai_assist.ApiError(401, "Unauthorized")

    ev3 = []
    ai_agent._round_chat = bad_key
    ai_agent.ai_models.detect = lambda m, b="": ai_agent.ai_models.Profile(
        "t", "t", "none", 131072, True, "off", 4000, "")
    try:
        ai_agent.run_agent(ST, {"port": 8188, "comfyui_path": "F:/X"}, "在吗",
                           [], lambda k, **kw: ev3.append({"kind": k, **kw}))
    finally:
        ai_agent._round_chat = orig_round
        ai_agent.ai_models.detect = orig_detect
    check("B17 401 只尝试一次", tries3["n"] == 1, str(tries3["n"]))
    err = next((e.get("text") for e in ev3 if e["kind"] == "error"), "")
    check("B18 401 仍是人话说明", "认证失败" in err, err[:50])


# ------------------------------------------------------------------ C. 缓存
def part_c():
    print("== C. 只读工具结果复用 ==")
    ctx = ai_tools.ToolCtx({}, {"port": 8188, "comfyui_path": "F:/X"})
    hits = {"n": 0}

    def install(name, ret, counter=None):
        """把某个工具临时换成计数器；counter 用于把不同工具的调用分开计"""
        box = hits if counter is None else counter
        t = next(x for x in ai_tools.TOOLS if x["name"] == name)
        orig = t["fn"]

        def fake(args, c):
            box["n"] += 1
            return dict(ret)
        t["fn"] = fake
        return t, orig

    t_logs, orig_logs = install("get_logs", {"ok": True, "lines": ["a"]})
    try:
        r1 = ai_tools.execute("get_logs", {"lines": 20}, ctx)
        r2 = ai_tools.execute("get_logs", {"lines": 20}, ctx)
        check("C1 同参数第二次直接复用（没再调底层）", hits["n"] == 1, str(hits["n"]))
        check("C2 复用结果带 _cached 标记", r2.get("_cached") is True, str(r2))
        check("C3 原结果不带标记", "_cached" not in r1, str(r1))
        check("C4 缓存命中计数", ctx.cache_hits == 1, str(ctx.cache_hits))

        r3 = ai_tools.execute("get_logs", {"lines": 50}, ctx)
        check("C5 参数不同不命中", hits["n"] == 2, str(hits["n"]))
        check("C6 参数顺序不影响判定（sort_keys）",
              True if hits["n"] == 2 else False)

        # 复用结果被改不能污染缓存
        r2["lines"].append("脏数据")
        r4 = ai_tools.execute("get_logs", {"lines": 20}, ctx)
        check("C7 深拷贝：改返回值不影响缓存",
              r4.get("lines") == ["a"], str(r4.get("lines")))
    finally:
        t_logs["fn"] = orig_logs

    # 写操作清空缓存（换新 ctx：上面 C1 已经缓存过同参数，会干扰计数）
    ctx2 = ai_tools.ToolCtx({}, {"port": 8188, "comfyui_path": "F:/X"})
    log_hits = {"n": 0}                    # 只数 get_logs，别把 control 混进来
    t_logs, orig_logs = install("get_logs", {"ok": True, "lines": ["a"]}, log_hits)
    t_ctl, orig_ctl = install("control_instance", {"ok": True, "state": "running"})
    try:
        ai_tools.execute("get_logs", {"lines": 20}, ctx2)
        ai_tools.execute("get_logs", {"lines": 20}, ctx2)
        check("C8 写操作前复用正常", log_hits["n"] == 1, str(log_hits["n"]))
        ai_tools.execute("control_instance", {"action": "start"}, ctx2)
        ai_tools.execute("get_logs", {"lines": 20}, ctx2)
        check("C9 写操作后缓存失效（重新读）", log_hits["n"] == 2, str(log_hits["n"]))
    finally:
        t_logs["fn"] = orig_logs
        t_ctl["fn"] = orig_ctl

    # 非只读工具本身不缓存
    hits["n"] = 0
    t_ctl, orig_ctl = install("control_instance", {"ok": True, "state": "running"})
    try:
        ai_tools.execute("control_instance", {"action": "start"}, ctx)
        ai_tools.execute("control_instance", {"action": "start"}, ctx)
        check("C10 写操作不缓存（两次都真执行）", hits["n"] == 2, str(hits["n"]))
    finally:
        t_ctl["fn"] = orig_ctl

    # 失败结果不缓存（否则一次网络抖动会卡住 30 秒）
    hits["n"] = 0
    t_models, orig_models = install("list_models", {"ok": False, "error": "网络不通"})
    try:
        ai_tools.execute("list_models", {}, ctx)
        ai_tools.execute("list_models", {}, ctx)
        check("C11 失败结果不缓存（可立刻重试）", hits["n"] == 2, str(hits["n"]))
    finally:
        t_models["fn"] = orig_models

    # 过期后重取
    hits["n"] = 0
    t_logs, orig_logs = install("get_logs", {"ok": True, "lines": ["a"]})
    orig_ttl = ai_tools.CACHE_TTL_S
    try:
        ai_tools.execute("get_logs", {"lines": 33}, ctx)
        ai_tools.CACHE_TTL_S = -1          # 立刻过期
        ai_tools.execute("get_logs", {"lines": 33}, ctx)
        check("C12 超过 TTL 重新取", hits["n"] == 2, str(hits["n"]))
    finally:
        ai_tools.CACHE_TTL_S = orig_ttl
        t_logs["fn"] = orig_logs

    # 隔离：不同 ctx 不共享缓存
    hits["n"] = 0
    t_logs, orig_logs = install("get_logs", {"ok": True, "lines": ["a"]})
    try:
        c2 = ai_tools.ToolCtx({}, {"port": 8188, "comfyui_path": "F:/X"})
        ai_tools.execute("get_logs", {"lines": 77}, c2)
        ai_tools.execute("get_logs", {"lines": 77}, ctx)
        check("C13 不同 ctx 不串缓存", hits["n"] == 2, str(hits["n"]))
    finally:
        t_logs["fn"] = orig_logs


# ------------------------------------------------------------------ D. 用量
def part_d():
    print("== D. token 用量 ==")
    n = ai_agent._norm_usage
    check("D1 OpenAI 形态",
          n({"prompt_tokens": 1200, "completion_tokens": 80}) ==
          {"prompt": 1200, "completion": 80, "cached": 0, "exact": True})
    check("D2 DeepSeek 缓存字段",
          n({"prompt_tokens": 1200, "completion_tokens": 80,
             "prompt_cache_hit_tokens": 900})["cached"] == 900)
    check("D3 OpenAI details.cached_tokens",
          n({"prompt_tokens": 10, "completion_tokens": 2,
             "prompt_tokens_details": {"cached_tokens": 7}})["cached"] == 7)
    check("D4 兼容 input/output_tokens",
          n({"input_tokens": 5, "output_tokens": 3}) ==
          {"prompt": 5, "completion": 3, "cached": 0, "exact": True})
    check("D5 空 → {}（交给估算）", n({}) == {} and n(None) == {})
    check("D6 只有无关字段 → {}", n({"foo": 1}) == {})

    est = ai_agent._est_usage([{"content": "你好" * 100}], {"content": "收到"})
    check("D7 估算标记 exact=False", est["exact"] is False, str(est))
    check("D8 估算有非零输入", est["prompt"] > 0, str(est))

    # 流式：最后一块带 usage 被抓到
    resp = {"choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 4}}
    check("D9 _chat_tools 归一化挂到 _usage_norm",
          ai_agent._norm_usage(resp["usage"])["prompt"] == 30)

    # UI 文案
    from app.ui.pages.ai_page import _fmt_usage
    check("D10 精确值显示", _fmt_usage(
        {"prompt": 12000, "completion": 800, "cached": 0, "exact": True}) ==
        "输入 12.0k · 输出 800", _fmt_usage(
        {"prompt": 12000, "completion": 800, "cached": 0, "exact": True}))
    s5 = _fmt_usage({"prompt": 1000, "completion": 100, "cached": 620,
                     "exact": True})
    check("D11 有缓存命中时显示比例", "缓存命中 62%" in s5, s5)
    s6 = _fmt_usage({"prompt": 1000, "completion": 100, "cached": 0,
                     "exact": False})
    check("D12 估算值带 ≈ 与标注", s6.startswith("≈ ") and "估算" in s6, s6)
    check("D13 无数据 → 破折号", _fmt_usage(None) == "—" and _fmt_usage({}) == "—")


def part_e():
    print("== E. 端到端：技能收窄 + 用量事件 ==")
    ST = {"settings": {"ai_enabled": True, "ai_base_url": "http://x",
                       "ai_api_key": "k", "ai_model": "t", "ai_compress": "off"}}
    cap = {}
    orig_round = ai_agent._round_chat
    orig_detect = ai_agent.ai_models.detect
    ai_agent.ai_models.detect = lambda m, b="": ai_agent.ai_models.Profile(
        "t", "t", "none", 131072, True, "off", 4000, "")

    def fake(st, messages, tools, **kw):
        cap["tools"] = [t["function"]["name"] for t in tools]
        cap["system"] = messages[0]["content"]
        return {"role": "assistant", "content": "好",
                "_usage": {"prompt": 500, "completion": 20, "cached": 300,
                           "exact": True}}

    ai_agent._round_chat = fake
    ev = []
    try:
        ai_agent.run_agent(ST, {"id": "i1", "port": 8188, "comfyui_path": "F:/X"},
                           "诊断一下", [], lambda k, **kw: ev.append({"kind": k, **kw}),
                           skills=["error-diagnosis"])
    finally:
        ai_agent._round_chat = orig_round
        ai_agent.ai_models.detect = orig_detect

    check("E1 会话技能收窄了工具表", len(cap.get("tools") or []) == 8,
          str(cap.get("tools")))
    check("E2 收窄后不含生图工具", "run_workflow" not in (cap.get("tools") or []),
          str(cap.get("tools")))
    check("E3 system 里写了启用清单", "【本会话启用工具】" in (cap.get("system") or ""))
    u = next((e for e in ev if e["kind"] == "usage"), {})
    check("E4 用量事件带精确值", u.get("prompt") == 500 and u.get("exact") is True,
          str(u))
    check("E5 用量事件带缓存数", u.get("cached") == 300, str(u))

    # 不勾技能 → 全量 19
    cap.clear()
    ai_agent._round_chat = fake
    ai_agent.ai_models.detect = lambda m, b="": ai_agent.ai_models.Profile(
        "t", "t", "none", 131072, True, "off", 4000, "")
    try:
        ai_agent.run_agent(ST, {"id": "i1", "port": 8188, "comfyui_path": "F:/X"},
                           "随便聊聊", [], lambda k, **kw: None)
    finally:
        ai_agent._round_chat = orig_round
        ai_agent.ai_models.detect = orig_detect
    check("E6 不勾技能时保留全量工具", len(cap.get("tools") or []) == 19,
          str(len(cap.get("tools") or [])))
    check("E7 全量时不写启用清单（省 token）",
          "【本会话启用工具】" not in (cap.get("system") or ""))


def main():
    print("AI 助手四项优化回归（2026-11-14）\n")
    part_a()
    print()
    part_b()
    print()
    part_c()
    print()
    print("== D. token 用量 ==")
    part_d()
    print()
    part_e()
    print(f"\n共 {PASS + FAIL} 项，通过 {PASS}，失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
