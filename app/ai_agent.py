# -*- coding: utf-8 -*-
"""ai_agent.py - AI 助手对话循环（2026-11-03）

OpenAI 兼容 /chat/completions + function calling，驱动 app/ai_tools.py 的工具层。
与 ai_assist.py（扫描器 AI 兜底）共用连接层（_v1_base/_request/_ssl_ctx）与配置键
（ai_enabled / ai_base_url / ai_api_key / ai_model），不新增任何设置项。

事件模型（emit 回调，UI 线程用信号转发）：
  emit("assistant", text=...)         助手正文
  emit("tool", name=..., args=...)    开始调用工具
  emit("tool_result", name=..., ok=..., brief=...)  工具返回（brief 为单行摘要）
  emit("error", text=...)             本轮失败原因
"""
import json
import time
import os

from app import ai_assist
from app import ai_context
from app import ai_env
from app import ai_models
from app import ai_persona
from app import ai_skills
from app import ai_tools
from app.i18n import T

MAX_ROUNDS = 8          # 单次提问最多工具往返数
MAX_TOOL_RESULT = 3000  # 单个工具结果喂回模型的字符上限（越小越快：每轮都要重新 prefill）

# 单轮失败自动重试（2026-11-14）：网络抖一下就整次提问作废太浪费——
# 8 轮跑到第 5 轮断线，前面四轮的结论全没了，用户得重问一遍。
RETRY_MAX = 2                 # 同一轮最多重试 2 次（共 3 次尝试）
RETRY_WAITS_S = (1.5, 4.0)    # 退避：先等 1.5 秒，再等 4 秒


def _norm_usage(u) -> dict:
    """把各家的 usage 归一化；关键字段全缺就返回 {}（交给估算兜底）

    各家字段名不统一：OpenAI 系 prompt/completion_tokens，
    部分自建网关给 input/output_tokens；缓存命中 DeepSeek 用
    prompt_cache_hit_tokens，OpenAI 用 prompt_tokens_details.cached_tokens。
    """
    if not isinstance(u, dict) or not u:
        return {}
    try:
        p = u.get("prompt_tokens") or u.get("input_tokens") or 0
        c = u.get("completion_tokens") or u.get("output_tokens") or 0
        cached = u.get("prompt_cache_hit_tokens") or 0
        det = u.get("prompt_tokens_details")
        if not cached and isinstance(det, dict):
            cached = det.get("cached_tokens") or 0
        if not p and not c:
            return {}
        return {"prompt": int(p or 0), "completion": int(c or 0),
                "cached": int(cached or 0), "exact": True}
    except Exception:
        return {}


def _est_usage(messages: list, msg: dict) -> dict:
    """服务商没返回 usage 时的兜底估算（界面会标「估算」）"""
    try:
        p = sum(ai_context.est_tokens(m.get("content") or "")
                for m in (messages or []))
        out = ai_context.est_tokens((msg or {}).get("content") or "")
        return {"prompt": int(p), "completion": int(out),
                "cached": 0, "exact": False}
    except Exception:
        return {"prompt": 0, "completion": 0, "cached": 0, "exact": False}


def _is_retryable(e) -> bool:
    """这个错误重试有意义吗

    只认「临时性」故障：超时、连接被重置/中断、5xx、429。
    401/403/404/400 是配置或参数问题，重试只是再浪费一次调用和几秒等待。
    """
    try:
        status = int(getattr(e, "status", 0) or 0)
    except Exception:
        status = 0
    if status:
        return status == 429 or 500 <= status < 600

    try:
        import socket as _socket
        import urllib.error as _ue
        if isinstance(e, _ue.HTTPError):
            return 500 <= int(getattr(e, "code", 0) or 0) < 600
        # 精确列类型，不用裸 OSError —— FileNotFoundError 也是 OSError，
        # 那个重试一百次也没用
        net = isinstance(e, (TimeoutError, ConnectionError, _socket.timeout,
                             _ue.URLError))
    except Exception:
        net = isinstance(e, (TimeoutError, ConnectionError))
    if net:
        txt = f"{type(e).__name__} {e}".lower()
        if "refused" in txt or "10061" in txt:
            return False        # 端口没人听 → 让模型去 control_instance，重试没意义
        return True

    txt = f"{type(e).__name__} {e}".lower()
    return any(k in txt for k in ("timed out", "timeout", "connection reset",
                                  "connection abort", "remote end closed",
                                  "temporarily unavailable"))

# 日志里这些词代表"真正要看的东西"——裁剪时必须保住（2026-11-13）
_LOG_HITS = ("error", "traceback", "exception", "failed", "critical",
             "out of memory", "not found", "cannot ", "unable ", "refus")


def _clip_lines(lines: list, budget: int):
    """日志行裁剪：**保尾部 + 保报错行**，返回 (保留的行, 省略行数)

    旧实现把整段 JSON 从头切 `[:3000]`，实测真实日志尾部 50 行是 4099 字符，
    被截掉的正好是 traceback 收尾和真正的报错行（在数组最后几项）——
    排查场景里最需要的信息反而被"省 token"省掉了。这里改成尾部优先、
    并且把含 error/traceback 的行额外抢回来。
    """
    lines = [str(x) for x in (lines or [])]
    if not lines:
        return [], 0
    if sum(len(x) + 3 for x in lines) <= budget:
        return lines, 0

    keep, used = set(), 0
    # 1) 从最新一行往前，先吃掉 70% 预算（最新日志永远最相关）
    tail_budget = int(budget * 0.7)
    for i in range(len(lines) - 1, -1, -1):
        cost = len(lines[i]) + 3
        if used + cost > tail_budget:
            break
        keep.add(i)
        used += cost
    # 2) 剩余预算抢回报错行（越靠尾部越优先）
    for i in range(len(lines) - 1, -1, -1):
        if i in keep:
            continue
        if not any(k in lines[i].lower() for k in _LOG_HITS):
            continue
        cost = len(lines[i]) + 3
        if used + cost > budget:
            break
        keep.add(i)
        used += cost
    # 3) 兜底：单行就超预算时至少留最后一行（自己再截一下）
    if not keep:
        keep.add(len(lines) - 1)
        lines[-1] = lines[-1][:max(0, budget - 40)] + "…"

    out = [lines[i] for i in sorted(keep)]
    omitted = len(lines) - len(out)
    if omitted:
        out.insert(0, f"…(省略较早的 {omitted} 行)")
    return out, omitted


# 这些键下的列表是「枚举类信息」——截断就等于丢答案，不能按通用规则砍
# （2026-11-14 实测踩到：inspect_workflow 返回 33 个节点，被裁到只剩 5 个，
#  模型看不到报错的 787:78 是哪个节点，只能回一句「信息不足」）
KEEP_LIST_KEYS = frozenset({"nodes", "workflows", "files", "items", "models"})

# 带节点清单的结果放宽预算：全量节点比「前 5 个 + 共 N 项」有用得多
BIG_RESULT = 6000


def _shrink(o, depth: int = 0, keep=None):
    """结构裁剪：长列表只留前 5、长字符串截 300，**保证裁完仍是合法 JSON**

    逐字符硬切会把 JSON 切成半截，模型拿到非法 JSON 只能瞎猜字段。
    keep 里的键名（nodes/workflows/…）例外：这些列表不截断。
    """
    keep = KEEP_LIST_KEYS if keep is None else keep
    if depth >= 4:
        return "…"
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k in keep and isinstance(v, list):
                out[k] = [_shrink(x, depth + 1, keep) for x in v]   # 枚举全留
            else:
                out[k] = _shrink(v, depth + 1, keep)
        return out
    if isinstance(o, list):
        if len(o) > 6:
            return [_shrink(x, depth + 1, keep) for x in o[:5]] + [f"…共 {len(o)} 项"]
        return [_shrink(x, depth + 1, keep) for x in o]
    if isinstance(o, str) and len(o) > 300:
        return o[:300] + "…"
    return o


def _clip(obj) -> str:
    """工具结果 → 喂回模型的字符串（保结构、保关键信息）

    三级降级：① 日志类按行裁剪（保尾部+报错行）→ ② 结构裁剪（列表截断、
    长串截断，仍是合法 JSON）→ ③ 仍然超长才硬切。
    """
    budget = MAX_TOOL_RESULT
    # 带节点清单的结果给更大预算（2026-11-14）：33 个节点全量约 3.8k 字符，
    # 按 3000 硬砍只剩 5 个节点，模型没法定位问题节点，反而要多来回几轮
    if isinstance(obj, dict) and isinstance(obj.get("nodes"), list):
        budget = max(budget, BIG_RESULT)
    if isinstance(obj, dict) and isinstance(obj.get("lines"), list):
        obj = dict(obj)
        keep, omitted = _clip_lines(obj.get("lines"), int(budget * 0.85))
        obj["lines"] = keep
        if omitted:
            obj["omitted_lines"] = omitted
    s = json.dumps(obj, ensure_ascii=False)
    if len(s) <= budget:
        return s
    s2 = json.dumps(_shrink(obj), ensure_ascii=False)
    if len(s2) <= budget:
        return s2
    return s2[:budget] + "…(截断)"


def _system_prompt(ctx, skills_text: str = "", profile_text: str = "",
                   tools_text: str = "") -> str:
    inst = ctx.inst
    # 助手设定（2026-11-06）：设置页可改；没写就用出厂默认，**不留空**
    raw = ""
    try:
        raw = str((getattr(ctx, "cfg", {}) or {}).get("settings", {})
                  .get("ai_persona") or "")
    except Exception:
        raw = ""
    persona = ai_persona.resolve(raw)
    head = ai_persona.PERSONA_HEADER + "\n" + persona[:1500] + "\n\n"
    # 环境画像（2026-11-13）：显卡/torch/ComfyUI 版本/磁盘/最近崩溃。
    # 只放慢变量并带 5 分钟缓存 —— system 是前缀缓存的命门，动态值会把它打碎。
    try:
        env = ai_env.profile_text(getattr(ctx, "cfg", {}) or {}, inst)
    except Exception:
        env = ""
    return head + (
        "你是 ComfyHub 内置的 ComfyUI 管理助手，帮助用户查看实例状态、排查报错、"
        "管理模型与工作流、提交生成任务。回答用简体中文，简洁直接，"
        "关键数字（版本/显存/队列深度）原样引用工具返回值。\n"
        f"当前实例：{inst.get('name') or '未命名'}（端口 {inst.get('port')}，"
        f"目录 {inst.get('comfyui_path') or '未知'}）。\n"
        + env +
        (tools_text + "\n" if tools_text else "") +
        "注意事项：\n"
        "1. 提交生成前先 get_health 确认实例在跑；实例没跑就 control_instance(action='start') "
        "自己拉起来（用户说「帮我启动」也用它），报错时用 get_logs 找 traceback；\n"
        "2. 跑新工作流前建议先 check_workflow 看缺什么；\n"
        "3. 下载大模型用 download_model（自动走后台队列），不要催进度，让用户看下载中心；\n"
        "4. 工具报错时如实转述 error 字段，不要编造原因；\n"
        "5. 出图结果只认 wait_job / get_job 返回的 images 数组里的路径，"
        "**逐字复制，禁止自己推算文件名或编号**；"
        "images 为空、或只返回 missing_images / stale_images 时，"
        "如实说明「这次没有生成新文件」并给出对应原因（文件不存在 / 命中缓存），"
        "不许说「出图完成」；\n"
        "6. patch_workflow 的 ops 必须是对象数组（node/index/value），"
        "参数名不支持时先 list_workflows 看真实文件名再重试。\n"
        "7. 需要用户在 2~4 个明确选项里选择/确认时（如「要不要启动」），调 ask_user 工具，"
        "选项按钮会出现在你的问题下方，用户点了自动回复；开放性问题直接文字提问。\n"
        "\n【剧本 A：根据用户给的图片生成同风格图】\n"
        "1) 先看图，用一句话提炼「风格 + 主体 + 配色 + 构图 + 画质词」，这是给生成模型的提示词；\n"
        "2) list_workflows 看有哪些工作流（**不要凭空编造工作流名**），必要时 check_workflow 确认能跑；\n"
        "3) inspect_workflow 找到提示词节点（hint=提示词）与尺寸节点（hint=画布尺寸）；\n"
        "4) patch_workflow 把提炼的提示词写进提示词节点（**改副本，不覆盖原文件**）；\n"
        "5) run_workflow 跑副本 → wait_job 等出图 → 把图片路径告诉用户。\n"
        "\n【剧本 B：用户给一段 JSON 要求出图】\n"
        "1) import_workflow 保存 JSON（自动识别 UI / API 格式）；\n"
        "2) inspect_workflow 看结构 → patch_workflow 按用户的外观要求改写提示词（可同时改尺寸/步数）；\n"
        "3) run_workflow → wait_job → 回报图片路径。若 JSON 不是工作流（例如只是参数清单），\n"
        "   说明它缺什么，并以现有工作流为底改提示词。\n"
        "\n原则：一次只改必要的东西；失败时读返回的 error 字段如实说明，不要猜。"
    ) + (("\n\n" + profile_text) if profile_text else "") \
      + (("\n\n" + skills_text) if skills_text else "")


def _chat_tools(settings: dict, messages: list, tools: list,
                extra: dict = None, max_tokens: int = 2000,
                timeout: int = 120) -> dict:
    """带 tools 的 /chat/completions；返回整个响应 dict。
    extra 为厂商私有字段（思考开关等），由 app/ai_models.py 决定形态"""
    payload = {
        "model": settings.get("ai_model") or "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": int(max_tokens or 2000),
    }
    if tools:                       # 收尾轮不带工具时不能塞空数组
        payload["tools"] = tools
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    resp = ai_assist._request(
        ai_assist._v1_base(settings["ai_base_url"]) + "/chat/completions",
        settings["ai_api_key"], data=body, timeout=timeout,
        direct=bool(settings.get("ai_direct", True)))
    if isinstance(resp, dict):
        u = _norm_usage(resp.get("usage"))
        if u:
            resp["_usage_norm"] = u        # 用量统计（2026-11-14），不参与业务逻辑
    return resp


class _StreamUnsupported(Exception):
    """服务端不支持 stream（返回了非 SSE 内容）→ 调用方回落普通请求"""


def _sse_lines(resp):
    """逐行读 SSE，产出 data 载荷字符串；[DONE] 结束"""
    for raw in resp:
        try:
            line = raw.decode("utf-8", "ignore").strip()
        except AttributeError:
            line = str(raw).strip()
        if not line:
            continue
        if line.startswith(":"):
            continue                      # SSE 注释/心跳
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            yield payload
        elif line.startswith("{"):
            # 有的兼容层直接吐 JSON（不支持 stream）→ 交给调用方回落
            raise _StreamUnsupported(line)


def _merge_tool_calls(acc: dict, delta_calls: list):
    """工具调用是分片到达的：按 index 聚合 id / name / arguments"""
    for tc in delta_calls or []:
        i = int(tc.get("index") or 0)
        slot = acc.setdefault(i, {"id": "", "name": "", "args": ""})
        if tc.get("id"):
            slot["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["args"] += fn["arguments"]


def _stream_chat(settings: dict, messages: list, tools: list,
                 extra: dict = None, max_tokens: int = 2000,
                 emit=None, timeout: int = 180) -> dict:
    """流式一轮对话；边收边 emit("delta"/"reasoning")。
    返回归一化的 assistant message（content + tool_calls）。
    服务端不支持流式时抛 _StreamUnsupported，由 _round_chat 回落。"""
    payload = {
        "model": settings.get("ai_model") or "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": int(max_tokens or 2000),
        "stream": True,
    }
    if tools:                       # 空数组会让部分接口报错（收尾轮不带工具）
        payload["tools"] = tools
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    req = ai_assist.make_request(
        ai_assist._v1_base(settings["ai_base_url"]) + "/chat/completions",
        settings["ai_api_key"], data=body)
    req.add_header("Accept", "text/event-stream")

    content_parts, calls, reasoning_chars, finish = [], {}, 0, ""
    usage = {}                                          # 流式最后一块才带 usage
    _direct = bool(settings.get("ai_direct", True))     # 默认直连，避开本地代理抖动
    _sock_timeout = min(timeout, 45)                    # 45s 没数据就当网络不通，别干等
    try:
        _resp = ai_assist.open_request(req, timeout=_sock_timeout, direct=_direct)
    except ai_assist.ApiError:
        raise        # 401/403/404 这类是配置问题，原样上抛——伪装成超时只会更迷惑
    except Exception as e:
        raise TimeoutError(f"等待模型响应超时或连接失败（{_sock_timeout}s）："
                           f"{type(e).__name__}") from e
    with _resp as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype and "event-stream" not in ctype:
            raise _StreamUnsupported(ctype)
        for payload_s in _sse_lines(resp):
            try:
                obj = json.loads(payload_s)
            except json.JSONDecodeError:
                continue
            ch = (obj.get("choices") or [{}])[0]
            # 用量统计（2026-11-14）：流式默认不返回，服务商开了才会在
            # 最后一块（choices 为空）给出；有就抓，没有就走估算兜底。
            if obj.get("usage"):
                usage = _norm_usage(obj["usage"]) or usage
            delta = ch.get("delta") or {}
            # 思考内容：只通报进度，不写进正文
            rc = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if rc:
                reasoning_chars += len(rc)
                if emit:
                    emit("reasoning", chars=reasoning_chars)
            c = delta.get("content")
            if isinstance(c, list):      # 少数兼容层给分段数组
                c = "".join((p.get("text") or "") for p in c if isinstance(p, dict))
            if c:
                content_parts.append(c)
                if emit:
                    emit("delta", text=c)
            if delta.get("tool_calls"):
                _merge_tool_calls(calls, delta["tool_calls"])
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]

    msg = {"role": "assistant", "content": "".join(content_parts) or ""}
    if calls:
        msg["tool_calls"] = [
            {"id": v["id"] or f"call_{i}", "type": "function",
             "function": {"name": v["name"], "arguments": v["args"] or "{}"}}
            for i, v in sorted(calls.items())]
    msg["_finish"] = finish
    msg["_usage"] = usage
    return msg


def _round_chat(settings: dict, messages: list, tools: list,
                extra: dict = None, max_tokens: int = 2000,
                emit=None, log=print, timeout: int = 180) -> dict:
    """一轮对话（统一入口）：优先流式，不支持/失败则回落非流式。
    返回归一化 message dict。"""
    if emit is not None:
        try:
            return _stream_chat(settings, messages, tools, extra=extra,
                                max_tokens=max_tokens, emit=emit, timeout=timeout)
        except _StreamUnsupported:
            log("[AI] 服务端不支持流式，回落普通请求")
        except Exception as e:
            log(f"[AI] 流式中断（{type(e).__name__}: {e}），回落普通请求")
    resp = _chat_tools(settings, messages, tools, extra=extra,
                       max_tokens=max_tokens, timeout=timeout)
    msg = dict((resp.get("choices") or [{}])[0].get("message") or {})
    msg["_finish"] = (resp.get("choices") or [{}])[0].get("finish_reason") or ""
    msg["_usage"] = resp.get("_usage_norm") or {}
    return msg


def _user_content(user_text: str, attachments=None):
    """有图片时按多模态数组构造 content；纯文字保持字符串（兼容性最好）"""
    atts = attachments or []
    imgs = [a for a in atts if a.get("kind") == "image" and a.get("b64")]
    texts = [a for a in atts if a.get("kind") == "text" and a.get("text")]
    if not imgs and not texts:
        return user_text
    parts = [{"type": "text", "text": user_text or ""}]
    for t in texts:
        label = t.get("label") or "附件"
        parts.append({"type": "text",
                      "text": f"\n[{label}]\n```json\n{t['text'][:12000]}\n```"})
    for im in imgs:
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:{im.get('mime') or 'image/png'};base64,{im['b64']}"}})
    return parts


def run_agent(cfg: dict, inst: dict, user_text: str, history: list,
              emit, stop_flag=None, log=print, attachments=None,
              mainwin=None, summary: str = "", summary_upto: int = 0,
              skills=None, profile=None, chat_id: str = "") -> None:
    """处理一次用户提问。history 为 UI 侧已有对话（仅 user/assistant 文本消息）。

    emit(kind, **kw) 事件回调；stop_flag 为 threading.Event，轮间检查。
    结束后由调用方把 assistant 文本追加进自己的 history。

    mainwin：GUI 模式下必须传主窗口——工具里的启停实例、下载队列都靠它
    （2026-11-06 修：之前没传，导致界面里点「帮忙启动实例」被误判成 --mcp 外挂模式）。
    summary / summary_upto：已压缩的早期对话摘要与覆盖到的条数（由会话存档提供）。
    skills：本会话勾选的技能 id 列表（2026-11-11，可空）。
    profile：本会话工作台设定（2026-11-11）——topic/inst_id/workflow/model，
    决定「这条会话干什么、用哪个工作流」。同时把绑定的工作流塞给工具层做默认值，
    省掉模型每次 list_workflows 挑工作流（挑错 = 跑错模态，用户最烦的点）。
    """
    st = cfg.get("settings") or {}
    if not (st.get("ai_enabled") and st.get("ai_base_url") and st.get("ai_api_key")):
        emit("error", text="AI 接入未启用：请到「系统设置 → AI 接入」填写接口地址与 API Key")
        return
    ctx = ai_tools.build_ctx(cfg, inst, mainwin=mainwin, log=log, profile=profile,
                             chat_id=chat_id)

    # ---- 模型画像：思考强度 / 输出上限 / 上下文条数（2026-11-03）----
    prof = ai_models.detect(str(st.get("ai_model") or ""), str(st.get("ai_base_url") or ""))
    think = ai_models.effective_think(prof, str(st.get("ai_think") or ""))
    extra, tip = ai_models.thinking_params(prof, think)
    max_tokens = ai_models.max_tokens_for(prof, think, int(st.get("ai_max_tokens") or 0))
    ctx_msgs = max(2, min(60, int(st.get("ai_ctx_msgs") or 12)))
    if tip:
        emit("notice", text=tip)

    messages = [{"role": "system", "content": _system_prompt(
        ctx, ai_skills.render(skills),
        ai_skills.render_profile(profile, str((inst or {}).get("name") or "")),
        ai_skills.tool_note(skills))}]

    # ---- 上下文压缩（2026-11-06 建；2026-11-14 改为按模型窗口占比触发）----
    hist = list(history or [])
    # 默认与设置页保持一致（原来是 "off"，用户界面上显示 mid 却根本没压，属于 bug）
    lvl = str(st.get("ai_compress") or "mid")
    window = int(getattr(prof, "ctx_tokens", 0) or 0)      # 未知模型给 0 → 走保守默认
    summary = (summary or "").strip()
    upto = max(0, int(summary_upto or 0))
    if ai_context.needed(hist, upto, lvl, window):
        cut = ai_context.split_point(hist, upto, lvl, window)
        chunk = hist[upto:cut]
        if chunk:
            emit("compress", count=len(chunk))
            # 原文落盘（2026-11-14）：摘要必然丢细节，落一份原文，
            # 模型可以用 recall_history 按关键词查回来
            try:
                from app import ai_chat_store
                ai_chat_store.save_part(chat_id, cut, chunk)
            except Exception:
                pass
            stamp = ai_context.make_stamp(inst)
            summary = stamp + ai_context.compress(
                summary, chunk, lvl,
                call=lambda msgs: (_round_chat(
                    st, msgs, [], extra=extra,
                    max_tokens=min(800, max_tokens), emit=None, log=log
                ) or {}).get("content") or "",
                log=log)
            upto = cut
            emit("compress_done", summary=summary, upto=upto, chars=len(summary))

    if summary:
        messages.append({"role": "system",
                         "content": "【较早对话的压缩摘要】"
                                    "（原文已压缩，细节可用 recall_history 回查）\n"
                                    + summary})
    for m in hist[upto:][-ctx_msgs:]:      # 上下文窗口（设置页可调，默认 12 条）
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            messages.append({"role": role, "content": str(m["content"])[:4000]})
    messages.append({"role": "user", "content": _user_content(user_text, attachments)})

    # 工具子集（2026-11-14）：勾了技能且技能声明了 tools → 只带这些 + 元能力。
    # 19 个工具的 schema 约 2800 token 且每轮都要带，收窄后既省前缀也让选择更准。
    _only = ai_skills.tools_for(skills)
    tools = ai_tools.schemas(_only)
    if _only is not None:
        log(f"[AI] 按技能收窄工具表：{len(tools)}/{len(ai_tools.TOOLS)} 个")
    repeat_seen = {}            # (工具名, 参数) → 次数：拦住模型拿同一参数死循环

    # ---- token 用量累计（2026-11-14）----
    # 服务商的 usage 字段不是都靠得住（部分中转站/自建网关不返回，流式默认也没有），
    # 所以「有就精确、没有就估算」，并在结果里标明 exact 让界面决定怎么显示。
    usage_total = {"prompt": 0, "completion": 0, "cached": 0,
                   "exact": True, "rounds": 0}

    def _acc_usage(u: dict):
        try:
            usage_total["prompt"] += int(u.get("prompt") or 0)
            usage_total["completion"] += int(u.get("completion") or 0)
            usage_total["cached"] += int(u.get("cached") or 0)
            usage_total["rounds"] += 1
            if u.get("exact") is False:
                usage_total["exact"] = False
        except Exception:
            usage_total["exact"] = False
        try:
            emit("usage", **dict(usage_total))
        except Exception:
            pass

    for round_i in range(MAX_ROUNDS):
        if stop_flag is not None and stop_flag.is_set():
            emit("error", text="已停止")
            return
        # 本轮输出追踪（2026-11-14）：重试只对「一个字都没吐出来」的失败生效。
        # 已经吐了半句再重试，用户会看到同一段话重复两遍。
        _said = {"n": 0}

        def _emit_r(k, **kw):
            if k in ("delta", "assistant", "reasoning"):
                _said["n"] += 1
            try:
                emit(k, **kw)
            except Exception:
                pass

        _err = None
        for _att in range(RETRY_MAX + 1):
            try:
                _r0 = time.time()
                msg = _round_chat(st, messages, tools, extra=extra,
                                  max_tokens=max_tokens, emit=_emit_r, log=log)
                _acc_usage(msg.get("_usage") or _est_usage(messages, msg))
                log(f"[AI] 第 {round_i + 1} 轮 · {time.time() - _r0:.1f}s · "
                    f"think={think} max_tokens={max_tokens} ctx={ctx_msgs}"
                    + (f" · 重试 {_att} 次" if _att else ""))
                _err = None
                break
            except Exception as e:
                _err = e
                _can = (_is_retryable(e) and _said["n"] == 0 and _att < RETRY_MAX)
                log(f"[AI] 第 {round_i + 1} 轮失败（第 {_att + 1} 次）："
                    f"{type(e).__name__}: {e}" + ("" if _can else " · 不再重试"))
                if not _can:
                    break
                _wait = RETRY_WAITS_S[min(_att, len(RETRY_WAITS_S) - 1)]
                emit("notice", text=f"连接中断，{_wait:.0f} 秒后自动重试"
                                    f"（第 {_att + 1}/{RETRY_MAX} 次）")
                time.sleep(_wait)

        if _err is not None:
            # 界面只给人话（describe_error），原始异常留在日志与气泡 tooltip 里
            msg = ai_assist.describe_error(_err)
            if "tools" in str(_err).lower() or "function" in str(_err).lower():
                msg += "\n" + T("（当前接口可能不支持 function calling，"
                                "请换支持工具调用的模型，如 deepseek-chat / glm-4.7 等）")
            emit("error", text=msg, raw=f"{type(_err).__name__}: {_err}")
            return
        calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if not calls:
            if content:
                emit("assistant", text=content)
            else:
                emit("error", text="接口返回空内容（若为推理型模型可加大 max_tokens 或换普通对话模型）")
            return

        # 把 assistant 的工具调用消息原样回填，再逐个执行
        messages.append({"role": "assistant",
                         "content": content or None,
                         "tool_calls": calls})
        for call in calls:
            if stop_flag is not None and stop_flag.is_set():
                emit("error", text="已停止")
                return
            name = (call.get("function") or {}).get("name") or ""
            raw = (call.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {}
            emit("tool", name=name, args=args)
            import time as _time
            _t0 = _time.time()
            result = ai_tools.execute(name, args, ctx)
            _ms = int((_time.time() - _t0) * 1000)
            ok = result.get("ok", True)
            brief = _brief(name, result)
            emit("tool_result", name=name, ok=ok, brief=brief, ms=_ms,
                 artifacts=_artifacts(name, args, result))
            _imgs = result.get("images") or []
            if _imgs:
                emit("image", paths=_imgs)

            # 同一工具+同一参数反复调用：结果不会变，第 3 次起明确告警，
            # 免得 8 轮全耗在一个查不到的搜索上（2026-11-06 实测踩到）
            sig = f"{name}|{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            repeat_seen[sig] = repeat_seen.get(sig, 0) + 1
            content_txt = _clip(result)
            if repeat_seen[sig] >= 3:
                content_txt += ("\n\n[系统提示] 你已经用完全相同的参数调用这个工具 "
                                f"{repeat_seen[sig]} 次，返回结果不会变。"
                                "请换关键词/参数，或直接根据现有信息作答并说明限制。")
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name,
                             "content": content_txt})

    # 轮数用尽：不再直接掐断，强制模型基于已有结果收尾（不带工具，
    # 保证用户至少拿到「查到了什么 / 还缺什么 / 下一步怎么办」）
    messages.append({
        "role": "user",
        "content": "（系统提示）工具调用轮数已用尽，请不要调用任何工具。"
                   "请立刻用现有信息给出结论：已经查到什么、还缺什么、"
                   "建议我下一步怎么做；不确定的地方要明说，不要编造。"})
    try:
        msg = _round_chat(st, messages, [], extra=extra,
                          max_tokens=max_tokens, emit=emit, log=log)
        content = (msg.get("content") or "").strip()
        if content:
            emit("assistant", text=content)
            return
    except Exception as e:
        log(f"[AI] 收尾轮失败：{type(e).__name__}: {e}")
    emit("error", text=f"连续 {MAX_ROUNDS} 轮工具调用未得到最终回答，"
                       f"请把问题拆小一点再问（例如先只说要看哪个模型类别）")


def _artifacts(name: str, args: dict, result: dict) -> list:
    """工具产出的「文件类结果」→ 右侧概要栏的产物清单（2026-11-12）

    只收**落盘的东西**（生成的图、写出的工作流、下载的模型、日志导出），
    查询类的返回值不算产物。kind 用于列表图标；label 是给人看的短名。
    """
    if not isinstance(result, dict) or not result.get("ok", True):
        return []
    out, seen = [], set()

    def _push(kind, path, label=""):
        p = str(path or "").strip()
        if not p or p in seen:
            return
        seen.add(p)
        out.append({"kind": kind, "path": p,
                    "label": (label or os.path.basename(p.replace("\\", "/")))[:80]})

    for p in (result.get("images") or []):
        _push("image", p)
    # 工作流类工具把结果写在 name/path/file 上
    for key in ("path", "file", "saved_path"):
        v = result.get(key)
        if isinstance(v, str) and ("/" in v or "\\" in v):
            _push("workflow" if name.endswith("workflow") else "file", v)
    if name == "download_model" and result.get("dest"):
        base = os.path.basename(str(args.get("file_path") or "").replace("\\", "/"))
        _push("model", os.path.join(str(result["dest"]), base) if base
              else str(result["dest"]))
    if name == "get_logs" and result.get("log_file"):
        _push("log", result["log_file"])
    return out


def _brief(name: str, result: dict) -> str:
    """工具结果的 UI 单行摘要"""
    if not result.get("ok", True):
        if result.get("code") == "instance_down":
            return "实例未运行（可让我自动启动）"
        return "失败：" + str(result.get("error", ""))[:80]
    if name == "control_instance":
        if result.get("action") == "stop":
            return "已停止" if result.get("stopped") else str(result.get("msg", ""))[:60]
        if result.get("ready"):
            return f"已就绪 · 端口 {result.get('port')}"
        return str(result.get("msg", ""))[:60]
    if name == "get_health":
        return (f"{result.get('gpu')} · 显存 {result.get('vram_free_gb')}/"
                f"{result.get('vram_total_gb')}GB · 队列 {result.get('queue_running')}"
                f"跑/{result.get('queue_pending')}排")
    if name == "get_logs":
        return f"读取 {len(result.get('lines') or [])} 行日志"
    if name == "recall_history":
        return f"回查到 {result.get('matches') or 0} 条早期原文"
    if name == "queue_list":
        return f"{len(result.get('running') or [])} 跑 / {len(result.get('pending') or [])} 排"
    if name == "queue_cancel":
        return "已停止" if result.get("stopped") else "中断已发出，等步间生效"
    if name == "list_models":
        return f"{len(result.get('counts') or {})} 个分类"
    if name == "search_models":
        return f"搜到 {len(result.get('results') or [])} 个仓库"
    if name == "list_repo_files":
        return f"{len(result.get('files') or [])} 个文件"
    if name == "download_model":
        return "已入队" if result.get("queued") else f"已下载 → {result.get('dest')}"
    if name == "list_workflows":
        return f"{result.get('total')} 个工作流"
    if name == "check_workflow":
        miss = result.get("missing_or_unknown") or {}
        n = sum(len(v) for v in miss.values())
        return "全绿" if n == 0 else f"{n} 个节点待确认"
    if name == "run_workflow":
        return f"已提交 #{result.get('queue_number')} · {result.get('prompt_id', '')[:8]}"
    if name == "get_job":
        if result.get("state") == "success":
            return f"完成，{len(result.get('images') or [])} 张图"
        return str(result.get("state"))
    return "完成"
