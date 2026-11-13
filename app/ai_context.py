# -*- coding: utf-8 -*-
"""上下文压缩（2026-11-06 建，2026-11-14 改为按窗口占比触发）

问题：多轮对话后历史越来越长，纯截断（只留最近 N 条）会把"用户最初的目标、
已经确认过的结论"整段丢掉，AI 就会反复问同样的事。

**2026-11-14 的两处改法**（参考 Octop 的摘要 middleware）：

1. 触发条件从「消息条数」改成「**模型上下文窗口的占比**」。
   同样是 16 条消息，套在 8k 窗口的模型上早就爆了，套在 128k 上纯属浪费 ——
   按条数判断根本不成立。默认 85% 触发、保留最近 12% 原文。

2. 摘要可以压得狠，因为**原文会另外落盘**（`ai_chat_store.save_part`），
   AI 需要细节时用 `recall_history` 工具回查。这是 Octop 最巧的一条：
   压缩不等于丢失。

档位（设置页可选，数值都是"窗口占比"）：
  light   90% 触发，保留 20% 原文，摘要 ≤400 字
  mid     85% 触发，保留 12% 原文，摘要 ≤300 字（默认）
  strong  70% 触发，保留  6% 原文，摘要 ≤200 字
  off     不压缩

压缩失败（断网 / 模型不听话）一律回落到机械拼接，绝不因为压缩把提问卡死。
"""
import re
import time

LEVELS = {
    "light": {"trigger_ratio": 0.90, "keep_ratio": 0.20, "chars": 400},
    "mid": {"trigger_ratio": 0.85, "keep_ratio": 0.12, "chars": 300},
    "strong": {"trigger_ratio": 0.70, "keep_ratio": 0.06, "chars": 200},
}

DEFAULT_WINDOW = 32768       # 拿不到模型画像时的保守窗口（大多数对话模型 ≥ 32k）
MIN_KEEP_MSGS = 2            # 无论怎么压，至少留最近 2 条原文

PROMPT = (
    "你在压缩一段 AI 助手与用户的对话历史。请保留：用户的目标与约束、"
    "已经确认的事实与数字（版本号/路径/参数）、做过的决定、还没做完的事项。"
    "丢掉客套、重复解释和失败的重试细节。输出简洁的要点文本，不要寒暄。"
)

# 摘要头部的时间戳/实例标记（2026-11-14）。它必须**稳定**：
# 这段时间戳如果每轮重算，system 前缀每次都变，provider 的前缀缓存全废。
_STAMP_RE = re.compile(r"^（截至 [^）]*）\r?\n")


def params(level: str):
    """取某档参数；off / 未知值返回 None（表示不压缩）"""
    return LEVELS.get(str(level or "").strip().lower())


def est_tokens(text) -> float:
    """粗估 token：中文 0.75/字、其余 0.3/字

    只用来判断"有没有到窗口的 85%"，误差 30% 不影响结论，别拿它算钱。
    """
    s = str(text or "")
    cjk = 0
    for ch in s:
        if "\u4e00" <= ch <= "\u9fff":
            cjk += 1
    return cjk * 0.75 + (len(s) - cjk) * 0.3


def hist_tokens(messages: list, start: int = 0) -> float:
    """从 start 起的消息总 token 估算"""
    msgs = messages or []
    return sum(est_tokens(m.get("content")) for m in msgs[max(0, start):])


def pending(messages: list, upto: int = 0) -> int:
    """还有多少条消息没被摘要覆盖"""
    return max(0, len(messages or []) - max(0, int(upto or 0)))


def needed(messages: list, upto: int, level: str, window: int = 0) -> bool:
    """是否该压缩：未摘要部分的 token 用量超过窗口的 trigger_ratio"""
    p = params(level)
    if not p:
        return False
    w = int(window or DEFAULT_WINDOW)
    return hist_tokens(messages, upto) > w * p["trigger_ratio"]


def split_point(messages: list, upto: int, level: str, window: int = 0) -> int:
    """压缩到哪一条为止：保留最近 keep_ratio 窗口的原文，之前的进摘要"""
    p = params(level) or {"keep_ratio": 0.12}
    w = int(window or DEFAULT_WINDOW)
    budget = w * p["keep_ratio"]
    msgs = messages or []
    base = max(0, int(upto or 0))
    cut, used = len(msgs), 0.0
    while cut > base:
        cost = est_tokens(msgs[cut - 1].get("content"))
        # 至少留 MIN_KEEP_MSGS 条，否则模型连上一句问的是什么都看不到
        if used + cost > budget and (len(msgs) - cut) >= MIN_KEEP_MSGS:
            break
        used += cost
        cut -= 1
    return max(base, cut)


def make_stamp(inst: dict = None, ts: float = None) -> str:
    """摘要头：实例名 + 生成时间（换实例后旧摘要会被丢弃，见 ai_page）"""
    name = str((inst or {}).get("name") or "未命名")
    when = time.strftime("%m-%d %H:%M",
                         time.localtime(float(ts or time.time())))
    return f"（截至 {when}，实例 {name}）\n"


def strip_stamp(summary: str) -> str:
    """去掉旧时间戳再送去压缩，避免同一行越叠越多"""
    return _STAMP_RE.sub("", str(summary or ""), count=1)


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
        return strip_stamp(old_summary)[:chars]
    try:
        text = (call(make_prompt(strip_stamp(old_summary), chunk, chars)) or "").strip()
    except Exception as e:
        log(f"[AI] 上下文压缩失败，回落机械摘要：{type(e).__name__}: {e}")
        text = ""
    if not text:
        return _fallback(strip_stamp(old_summary), chunk, chars)
    return text[:chars * 2]        # 模型偶尔超字数，硬性兜一层


def describe(level: str) -> str:
    p = params(level)
    if not p:
        return "关闭（只保留最近若干条，不摘要）"
    return (f"历史用量超过模型窗口 {int(p['trigger_ratio'] * 100)}% 时压缩，"
            f"保留最近 {int(p['keep_ratio'] * 100)}% 原文，摘要 ≤{p['chars']} 字")
