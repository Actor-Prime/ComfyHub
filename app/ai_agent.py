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

from app import ai_assist
from app import ai_models
from app import ai_tools

MAX_ROUNDS = 8          # 单次提问最多工具往返数
MAX_TOOL_RESULT = 3000  # 单个工具结果喂回模型的字符上限（越小越快：每轮都要重新 prefill）


def _clip(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= MAX_TOOL_RESULT else s[:MAX_TOOL_RESULT] + "…(截断)"


def _system_prompt(ctx) -> str:
    inst = ctx.inst
    return (
        "你是 ComfyHub 内置的 ComfyUI 管理助手，帮助用户查看实例状态、排查报错、"
        "管理模型与工作流、提交生成任务。回答用简体中文，简洁直接，"
        "关键数字（版本/显存/队列深度）原样引用工具返回值。\n"
        f"当前实例：{inst.get('name') or '未命名'}（端口 {inst.get('port')}，"
        f"目录 {inst.get('comfyui_path') or '未知'}）。\n"
        "注意事项：\n"
        "1. 提交生成前先 get_health 确认实例在跑；实例没跑就 control_instance(action='start') "
        "自己拉起来（用户说「帮我启动」也用它），报错时用 get_logs 找 traceback；\n"
        "2. 跑新工作流前建议先 check_workflow 看缺什么；\n"
        "3. 下载大模型用 download_model（自动走后台队列），不要催进度，让用户看下载中心；\n"
        "4. 工具报错时如实转述 error 字段，不要编造原因；\n"
        "5. 出图完成后把 get_job / wait_job 返回的图片完整路径告诉用户。\n"
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
    )


def _chat_tools(settings: dict, messages: list, tools: list,
                extra: dict = None, max_tokens: int = 2000,
                timeout: int = 120) -> dict:
    """带 tools 的 /chat/completions；返回整个响应 dict。
    extra 为厂商私有字段（思考开关等），由 app/ai_models.py 决定形态"""
    payload = {
        "model": settings.get("ai_model") or "deepseek-chat",
        "messages": messages,
        "tools": tools,
        "temperature": 0.3,
        "max_tokens": int(max_tokens or 2000),
    }
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    return ai_assist._request(
        ai_assist._v1_base(settings["ai_base_url"]) + "/chat/completions",
        settings["ai_api_key"], data=body, timeout=timeout)


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
        "tools": tools,
        "temperature": 0.3,
        "max_tokens": int(max_tokens or 2000),
        "stream": True,
    }
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    req = ai_assist.make_request(
        ai_assist._v1_base(settings["ai_base_url"]) + "/chat/completions",
        settings["ai_api_key"], data=body)
    req.add_header("Accept", "text/event-stream")

    content_parts, calls, reasoning_chars, finish = [], {}, 0, ""
    with ai_assist.open_request(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ctype and "event-stream" not in ctype:
            raise _StreamUnsupported(ctype)
        for payload_s in _sse_lines(resp):
            try:
                obj = json.loads(payload_s)
            except json.JSONDecodeError:
                continue
            ch = (obj.get("choices") or [{}])[0]
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
              emit, stop_flag=None, log=print, attachments=None) -> None:
    """处理一次用户提问。history 为 UI 侧已有对话（仅 user/assistant 文本消息）。

    emit(kind, **kw) 事件回调；stop_flag 为 threading.Event，轮间检查。
    结束后由调用方把 assistant 文本追加进自己的 history。
    """
    st = cfg.get("settings") or {}
    if not (st.get("ai_enabled") and st.get("ai_base_url") and st.get("ai_api_key")):
        emit("error", text="AI 接入未启用：请到「系统设置 → AI 接入」填写接口地址与 API Key")
        return
    ctx = ai_tools.build_ctx(cfg, inst, log=log)

    # ---- 模型画像：思考强度 / 输出上限 / 上下文条数（2026-11-03）----
    prof = ai_models.detect(str(st.get("ai_model") or ""), str(st.get("ai_base_url") or ""))
    think = ai_models.effective_think(prof, str(st.get("ai_think") or ""))
    extra, tip = ai_models.thinking_params(prof, think)
    max_tokens = ai_models.max_tokens_for(prof, think, int(st.get("ai_max_tokens") or 0))
    ctx_msgs = max(2, min(60, int(st.get("ai_ctx_msgs") or 12)))
    if tip:
        emit("notice", text=tip)

    messages = [{"role": "system", "content": _system_prompt(ctx)}]
    for m in (history or [])[-ctx_msgs:]:      # 上下文窗口（设置页可调，默认 12 条）
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            messages.append({"role": role, "content": str(m["content"])[:4000]})
    messages.append({"role": "user", "content": _user_content(user_text, attachments)})

    tools = ai_tools.schemas()
    for round_i in range(MAX_ROUNDS):
        if stop_flag is not None and stop_flag.is_set():
            emit("error", text="已停止")
            return
        try:
            _r0 = __import__("time").time()
            msg = _round_chat(st, messages, tools, extra=extra,
                              max_tokens=max_tokens, emit=emit, log=log)
            log(f"[AI] 第 {round_i + 1} 轮 · {__import__('time').time() - _r0:.1f}s · "
                f"think={think} max_tokens={max_tokens} ctx={ctx_msgs}")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if "tools" in str(e).lower() or "function" in str(e).lower():
                msg += "（当前接口可能不支持 function calling，"
                msg += "请换支持工具调用的模型，如 deepseek-chat / glm-4.5 等）"
            emit("error", text=msg)
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
            emit("tool_result", name=name, ok=ok, brief=brief, ms=_ms)
            _imgs = result.get("images") or []
            if _imgs:
                emit("image", paths=_imgs)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name,
                             "content": _clip(result)})
    emit("error", text=f"连续 {MAX_ROUNDS} 轮工具调用未得到最终回答，已停止（任务可能过于复杂）")


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
