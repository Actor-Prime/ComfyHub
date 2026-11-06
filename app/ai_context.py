# -*- coding: utf-8 -*-
"""上下文压缩（2026-11-06）

问题：多轮对话后历史越来越长，纯截断（只留最近 N 条）会把"用户最初的目标、
已经确认过的结论"整段丢掉，AI 就会反复问同样的事。

做法：到阈值时，把**较早的那一段**交给模型压成一段要点摘要，之后每轮只带
「摘要 + 最近若干条原文」。摘要会随对话继续增量合并，不用每次重算。

三档强度（设置页可选）：
  light  24 条触发，保留最近 14 条，摘要 ≤400 字
  mid    16 条触发，保留最近 9 条， 摘要 ≤260 字
  strong 10 条触发，保留最近 5 条， 摘要 ≤160 字
压缩失败（断网/模型不听话）一律回落到机械截断，绝不因为压缩把提问卡死。
"""
LEVELS = {
    "light": {"trigger": 24, "keep": 14, "chars": 400},
    "mid": {"trigger": 16, "keep": 9, "chars": 260},
    "strong": {"trigger": 10, "keep": 5, "chars": 160},
}

PROMPT = (
    "你在压缩一段 AI 助手与用户的对话历史。请保留：用户的目标与约束、"
    "已经确认的事实与数字（版本号/路径/参数）、做过的决定、还没做完的事项。"
    "丢掉客套、重复解释和失败的重试细节。输出简洁的要点文本，不要寒暄。"
)


def params(level: str):
    """取某档参数；off / 未知值返回 None（表示不压缩）"""
    return LEVELS.get(str(level or "off").strip().lower())


def pending(messages: list, upto: int = 0) -> int:
    """还有多少条消息没被摘要覆盖"""
    return max(0, len(messages or []) - max(0, int(upto or 0)))


def needed(messages: list, upto: int, level: str) -> bool:
    p = params(level)
    if not p:
        return False
    return pending(messages, upto) > p["trigger"]


def split_point(messages: list, upto: int, level: str) -> int:
    """压缩到哪一条为止（这条之前的都进摘要，之后的留原文）"""
    p = params(level) or {"keep": 8}
    return max(0, len(messages or []) - p["keep"])


def _render(chunk: list, limit: int = 4000) -> str:
    out, used = [], 0
    for m in chunk:
        role = "用户" if m.get("role") == "user" else "助手"
        text = " ".join(str(m.get("content") or "").split())
        line = f"{role}：{text[:600]}"
        used += len(line)
        if used > limit:
            break
        out.append(line)
    return "\n".join(out)


def make_prompt(old_summary: str, chunk: list, chars: int) -> list:
    body = _render(chunk)
    prev = (old_summary or "").strip() or "（无）"
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content":
            f"【已有摘要】\n{prev}\n\n【需要并入的新对话】\n{body}\n\n"
            f"要求：把两者合并成一段不超过 {chars} 字的中文摘要，按主题列要点，"
            f"保留具体数字与文件名。"},
    ]


def _fallback(old_summary: str, chunk: list, chars: int) -> str:
    """压缩失败的兜底：机械拼接 + 截断，至少不丢"最初目标" """
    parts = []
    if (old_summary or "").strip():
        parts.append(old_summary.strip())
    for m in chunk:
        text = " ".join(str(m.get("content") or "").split())
        if text:
            parts.append(("用户：" if m.get("role") == "user" else "助手：") + text[:120])
    joined = "；".join(parts)
    return joined[:chars]


def compress(old_summary: str, chunk: list, level: str, call, log=print) -> str:
    """把 chunk 并入摘要。call(messages)->str 由调用方提供（复用同学模型的请求通道）。"""
    p = params(level) or {"chars": 260}
    chars = p["chars"]
    if not chunk:
        return (old_summary or "")[:chars]
    try:
        text = (call(make_prompt(old_summary, chunk, chars)) or "").strip()
    except Exception as e:
        log(f"[AI] 上下文压缩失败，回落机械摘要：{type(e).__name__}: {e}")
        text = ""
    if not text:
        return _fallback(old_summary, chunk, chars)
    return text[:chars * 2]        # 模型偶尔超字数，硬性兜一层


def describe(level: str) -> str:
    p = params(level)
    if not p:
        return "关闭（只保留最近若干条，不摘要）"
    return (f"{p['trigger']} 条以上开始压缩，保留最近 {p['keep']} 条原文，"
            f"摘要 ≤{p['chars']} 字")
