# -*- coding: utf-8 -*-
"""ai_models.py - 模型画像与参数映射（2026-11-03）

背景：不同厂商的「思考强度 / 上下文」参数完全不同，硬编码一个参数名必然
在某些接口上 400（实测小米 MiMo 认 `thinking.type` 与 `chat_template_kwargs`，
不认顶层 `enable_thinking` 与 `reasoning_effort`）。这里按「模型名 + 域名」
识别族系，再翻译成该族系认识的那套参数。

三个用户可调项（设置页）：
  ai_think      思考强度：off / normal / deep
  ai_max_tokens 单次最大输出 token
  ai_ctx_msgs   带入对话上下文的历史消息条数

注意：无法关闭思考的模型（如 deepseek-reasoner）如实返回空参数并给提示，
不假装开关有效——用户看到的是真实行为。
"""
from urllib.parse import urlparse

# level → 各族系的取值
LEVELS = ("off", "normal", "deep")


class Profile:
    __slots__ = ("family", "label", "style", "ctx_tokens", "tools",
                 "default_think", "default_max_tokens", "note")

    def __init__(self, family, label, style, ctx_tokens, tools=True,
                 default_think="off", default_max_tokens=4000, note=""):
        self.family = family
        self.label = label
        self.style = style              # 思考参数形态
        self.ctx_tokens = ctx_tokens
        self.tools = tools
        self.default_think = default_think
        self.default_max_tokens = default_max_tokens
        self.note = note

    def __repr__(self):
        return f"<Profile {self.family} {self.style}>"


# ---------------------------------------------------------------- 族系表
# style 取值：
#   thinking.type       —— {"thinking": {"type": "enabled|disabled"}}（智谱 GLM-4.5+、小米 MiMo 实测有效）
#   chat_template       —— {"chat_template_kwargs": {"enable_thinking": bool}}（vLLM 通用兜底）
#   qwen                —— {"enable_thinking": bool, "thinking_budget": int}（DashScope 兼容模式）
#   reasoning_effort    —— {"reasoning_effort": "minimal|medium|high"}（OpenAI o 系 / gpt-5）
#   none                —— 无参数可调（deepseek-reasoner 等）
_TABLE = [
    # model 关键字, base_url 关键字, Profile
    (("mimo",), ("xiaomimimo",), Profile(
        "mimo", "小米 MiMo", "thinking.type", 131072, True, "off", 4000,
        "实测：thinking.type 与 chat_template_kwargs 均有效，顶层 enable_thinking 无效")),
    (("glm-4.5", "glm-4.6", "glm-4.7", "glm-4p", "glm-5", "glm-z1"),
     ("bigmodel", "zhipu"), Profile(
        "zhipu", "智谱 GLM", "thinking.type", 131072, True, "off", 4000,
        "GLM-4.5 起支持 thinking.type 开关")),
    (("qwen3", "qwen-3", "qwen2.5-72b", "qwen-max"), ("dashscope", "aliyun"), Profile(
        "qwen", "通义 Qwen3", "qwen", 131072, True, "off", 4096,
        "思考预算 2048/8192 对应标准/深度")),
    (("deepseek-reasoner", "deepseek-r1", "-r1"), ("deepseek",), Profile(
        "deepseek-r", "DeepSeek 推理版", "none", 65536, True, "normal", 8192,
        "该模型无法关闭思考，建议把最大输出调大，否则正文会被思考挤空")),
    (("deepseek",), ("deepseek",), Profile(
        "deepseek", "DeepSeek Chat", "none", 65536, True, "off", 4000,
        "对话模型无思考开关")),
    (("gpt-5", "o1", "o3", "o4"), ("openai",), Profile(
        "openai-o", "OpenAI 推理系", "reasoning_effort", 200000, True, "normal", 8000,
        "关闭=minimal，标准=medium，深度=high")),
    (("claude",), ("anthropic",), Profile(
        "claude", "Anthropic Claude", "none", 200000, True, "off", 4000,
        "通过兼容层时不支持思考参数")),
    (("kimi", "moonshot"), ("moonshot",), Profile(
        "kimi", "Kimi", "none", 131072, True, "off", 4000, "")),
]

_DEFAULT = Profile("unknown", "未知模型", "none", 0, True, "off", 4000,
                   "未知厂商：不注入任何私有参数以免 400，可用最大输出与上下文两项调节")


def detect(model: str, base_url: str = "") -> Profile:
    """按「模型名优先、域名兜底」识别族系"""
    m = (model or "").lower()
    host = ""
    try:
        host = (urlparse(base_url or "").netloc or "").lower()
    except Exception:
        pass
    for mkeys, hkeys, prof in _TABLE:
        if any(k in m for k in mkeys):
            return prof
    for mkeys, hkeys, prof in _TABLE:
        if hkeys and any(k in host for k in hkeys):
            return prof
    return _DEFAULT


def thinking_params(prof: Profile, think: str) -> tuple:
    """返回 (body 追加字段, 提示)。think ∈ off/normal/deep"""
    level = think if think in LEVELS else "off"
    if prof.style == "thinking.type":
        return {"thinking": {"type": "enabled" if level != "off" else "disabled"}}, ""
    if prof.style == "chat_template":
        return {"chat_template_kwargs": {"enable_thinking": level != "off"}}, ""
    if prof.style == "qwen":
        if level == "off":
            return {"enable_thinking": False}, ""
        return {"enable_thinking": True,
                "thinking_budget": 2048 if level == "normal" else 8192}, ""
    if prof.style == "reasoning_effort":
        eff = {"off": "minimal", "normal": "medium", "deep": "high"}[level]
        return {"reasoning_effort": eff}, ""
    if prof.family == "deepseek-r":
        return {}, "该模型不支持关闭思考（思考过程会计入 token），建议把最大输出调到 8000+"
    return {}, ""


def max_tokens_for(prof: Profile, think: str, user_max: int) -> int:
    """有效输出上限：用户显式值优先；开了思考要给足额度，否则正文被思考挤空"""
    val = int(user_max or 0)
    if val <= 0:
        val = prof.default_max_tokens
    if think in ("normal", "deep"):
        val = max(val, 8192)          # 推理模型的思考会吃掉正文额度
    return max(256, min(val, 32768))


def effective_think(prof: Profile, think: str) -> str:
    """用户没选时用画像默认值"""
    return think if think in LEVELS else prof.default_think


def describe(prof: Profile) -> str:
    """设置页展示用的一句话画像"""
    ctx = f"{prof.ctx_tokens // 1024}k" if prof.ctx_tokens else "未知"
    can = {
        "none": "思考不可调" if prof.family != "deepseek-r" else "无法关闭思考",
    }.get(prof.style, "思考可调")
    return (f"{prof.label} · 上下文约 {ctx} · {can} · "
            f"{'支持工具调用' if prof.tools else '可能不支持工具调用'}")
