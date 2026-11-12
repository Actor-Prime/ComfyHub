# -*- coding: utf-8 -*-
"""AI 服务商预设（2026-11-12 用户需求：能选就别手填，选不到再自定义）。

设计要点
    · 预设只提供「接口地址 + 候选模型 + 申请入口提示」，API Key 永远手填；
    · 地址一律给到版本段（/v1、/v4、/v3），ai_assist._v1_base 对已带版本段的
      地址原样使用（智谱是 /v4、火山方舟是 /v3，不能盲补 /v1）；
    · **分国内 / 国际两组**：同一家厂商常有 .cn/.ai、bigmodel/z.ai、
      dashscope/dashscope-intl、.com/.io 两套端点，Key 也不通用，必须分开选；
    · 候选模型给「已知确定」的；不确定的留空——用户点「测试接口」会拉该 Key 的
      真实 /models 清单自动填，留错值反而误导；
    · 判定当前配置属于哪家：**按 base_url 域名匹配**，不按模型名——
      模型可跨平台托管（硅基流动就托管 DeepSeek/Qwen），按模型名会误判。

收录标准（2026-11 逐个 /models 探活）：401/403 视为端点存在；
410（零一万物）、404（商汤 compatible-mode）等已下线/改路径的一律不收。
国内厂商的海外站归到「国际」组。
"""

CUSTOM_ID = "custom"

GROUPS = ["国内", "国际", "Token/编程套餐"]

# group: 分组；id: 稳定标识（存配置用，勿改）；base: OpenAI 兼容端点；
# models: 候选（空 = 靠「测试接口」拉清单）；hint: 申请入口/注意事项
PROVIDERS = [
    # ---------------- 国内 ----------------
    {"id": "deepseek", "group": "国内", "name": "DeepSeek 官方",
     "base": "https://api.deepseek.com/v1",
     "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"],
     "hint": "platform.deepseek.com 申请 Key。对话模型无思考开关"},
    {"id": "mimo", "group": "国内", "name": "小米 MiMo",
     "base": "https://api.xiaomimimo.com/v1",
     "models": ["mimo-v2.6-pro", "mimo-v2.6-flash"],
     "hint": "platform.xiaomimimo.com 申请 Key。thinking.type 开关实测有效"},
    {"id": "qwen", "group": "国内", "name": "阿里百炼（通义千问）",
     "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen3.8-max", "qwen3.8-plus", "qwen3.8-flash"],
     "hint": "bailian.console.aliyun.com 申请 Key。按量付费用「兼容模式」地址；买了 Coding Plan / Token Plan 请选下面套餐组里的条目"},
    {"id": "zhipu", "group": "国内", "name": "智谱 GLM",
     "base": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4.7", "glm-4.7-flash", "glm-5"],
     "hint": "bigmodel.cn 申请 Key。这是按量付费端点；买了 GLM Coding Plan 请选套餐组里的条目"},
    {"id": "kimi", "group": "国内", "name": "月之暗面 Kimi",
     "base": "https://api.moonshot.cn/v1",
     "models": ["kimi-k2.6", "kimi-k2-turbo-preview"],
     "hint": "platform.moonshot.cn 申请 Key（开放平台按量付费）。Kimi Code 会员请选套餐组里的「Kimi Code 会员」"},
    {"id": "minimax", "group": "国内", "name": "MiniMax",
     "base": "https://api.minimaxi.com/v1",
     "models": ["MiniMax-M3", "MiniMax-M2.7"],
     "hint": "platform.minimaxi.com 申请 Key（海外站 api.minimax.io，Key 不通用）。Token Plan 订阅 Key（sk-cp-…）与按量 Key（sk-api-…）共用这个 /v1 地址，别配到别家端点，否则 401"},
    {"id": "siliconflow", "group": "国内", "name": "硅基流动 SiliconFlow",
     "base": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-V3",
                "Qwen/Qwen3-32B"],
     "hint": "cloud.siliconflow.cn 申请 Key。模型名带「厂商/」前缀"},
    {"id": "ark", "group": "国内", "name": "火山方舟（豆包）",
     "base": "https://ark.cn-beijing.volces.com/api/v3",
     "models": ["doubao-seed-2-0-pro"],
     "hint": "console.volcengine.com/ark 创建「推理接入点」后用其 ID 作模型名。买了方舟 Coding Plan 请选套餐组里的条目"},
    {"id": "hunyuan", "group": "国内", "name": "腾讯混元 Hunyuan",
     "base": "https://api.hunyuan.cloud.tencent.com/v1",
     "models": ["hunyuan-turbos-latest"],
     "hint": "console.cloud.tencent.com/hunyuan 开通后申请 Key（OpenAI 兼容模式）。腾讯云 Coding Plan 请选套餐组里的条目"},
    {"id": "qianfan", "group": "国内", "name": "百度千帆（文心）",
     "base": "https://qianfan.baidubce.com/v2",
     "models": ["ernie-5.0"],
     "hint": "console.bce.baidu.com/qianfan 申请 Key（v2 接口是 OpenAI 兼容的）"},
    {"id": "spark", "group": "国内", "name": "讯飞星火 Spark",
     "base": "https://spark-api-open.xf-yun.com/v1",
     "models": [],
     "hint": "console.xfyun.cn 申请 APIPassword 当 Key；点「测试接口」自动拉模型清单"},
    {"id": "stepfun", "group": "国内", "name": "阶跃星辰 StepFun",
     "base": "https://api.stepfun.com/v1",
     "models": ["step-2-16k"],
     "hint": "platform.stepfun.com 申请 Key"},
    {"id": "baichuan", "group": "国内", "name": "百川智能 Baichuan",
     "base": "https://api.baichuan-ai.com/v1",
     "models": [],
     "hint": "platform.baichuan-ai.com 申请 Key；点「测试接口」自动拉模型清单"},
    {"id": "modelscope", "group": "国内", "name": "魔搭 ModelScope",
     "base": "https://api-inference.modelscope.cn/v1",
     "models": [],
     "hint": "modelscope.cn 绑定阿里云账号拿免费额度；模型名形如 Qwen/Qwen3-32B"},

    # ---------------- 国际 ----------------
    {"id": "openai", "group": "国际", "name": "OpenAI 官方",
     "base": "https://api.openai.com/v1",
     "models": ["gpt-5.4-mini", "gpt-5.4"],
     "hint": "境外服务，需能直连或走代理（下方「直连」勾选要取消）"},
    {"id": "gemini", "group": "国际", "name": "Google Gemini",
     "base": "https://generativelanguage.googleapis.com/v1beta/openai",
     "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
     "hint": "aistudio.google.com 申请 Key。走 Gemini 的 OpenAI 兼容端点"},
    {"id": "anthropic", "group": "国际", "name": "Anthropic Claude",
     "base": "https://api.anthropic.com/v1",
     "models": ["claude-sonnet-4-6"],
     "hint": "console.anthropic.com 申请 Key。走 OpenAI 兼容层，部分参数不生效"},
    {"id": "xai", "group": "国际", "name": "xAI Grok",
     "base": "https://api.x.ai/v1",
     "models": ["grok-4"],
     "hint": "console.x.ai 申请 Key。境外服务，需代理"},
    {"id": "mistral", "group": "国际", "name": "Mistral AI",
     "base": "https://api.mistral.ai/v1",
     "models": ["mistral-large-latest"],
     "hint": "console.mistral.ai 申请 Key。境外服务，需代理"},
    {"id": "groq", "group": "国际", "name": "Groq",
     "base": "https://api.groq.com/openai/v1",
     "models": ["llama-3.3-70b-versatile"],
     "hint": "console.groq.com 申请 Key。超快推理，模型多为开源件"},
    {"id": "together", "group": "国际", "name": "Together AI",
     "base": "https://api.together.xyz/v1",
     "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
     "hint": "api.together.ai 申请 Key。开源模型托管，模型名带组织前缀"},
    {"id": "openrouter", "group": "国际", "name": "OpenRouter",
     "base": "https://openrouter.ai/api/v1",
     "models": [],
     "hint": "openrouter.ai 一个 Key 通吃各家；模型名形如 openai/gpt-5.4"},
    {"id": "deepinfra", "group": "国际", "name": "DeepInfra",
     "base": "https://api.deepinfra.com/v1/openai",
     "models": ["meta-llama/Meta-Llama-3.1-8B-Instruct"],
     "hint": "deepinfra.com 申请 Key。开源模型按量计费"},
    {"id": "cerebras", "group": "国际", "name": "Cerebras",
     "base": "https://api.cerebras.ai/v1",
     "models": [],
     "hint": "cloud.cerebras.ai 申请 Key；点「测试接口」自动拉模型清单"},
    {"id": "nvidia", "group": "国际", "name": "NVIDIA NIM",
     "base": "https://integrate.api.nvidia.com/v1",
     "models": ["meta/llama-3.3-70b-instruct"],
     "hint": "build.nvidia.com 申请 Key，有免费额度"},
    {"id": "hf", "group": "国际", "name": "HuggingFace Router",
     "base": "https://router.huggingface.co/v1",
     "models": [],
     "hint": "huggingface.co/settings/tokens 申请 Token；模型名形如 Qwen/Qwen3-32B"},
    {"id": "novita", "group": "国际", "name": "Novita AI",
     "base": "https://api.novita.ai/openai",
     "models": [],
     "hint": "novita.ai 申请 Key；点「测试接口」自动拉模型清单"},
    {"id": "fireworks", "group": "国际", "name": "Fireworks AI",
     "base": "https://api.fireworks.ai/inference/v1",
     "models": [],
     "hint": "fireworks.ai 申请 Key；模型名形如 accounts/fireworks/models/…"},

    # 国内厂商的海外站（Key 与国内站不通用，故单列）
    {"id": "kimi_intl", "group": "国际", "name": "Kimi 国际版",
     "base": "https://api.moonshot.ai/v1",
     "models": ["kimi-k2.6"],
     "hint": "platform.moonshot.ai 申请 Key（与国内 platform.moonshot.cn 账号不通用）"},    {"id": "zhipu_intl", "group": "国际", "name": "智谱 GLM 国际版（z.ai）",
     "base": "https://api.z.ai/api/paas/v4",
     "models": ["glm-4.7", "glm-5"],
     "hint": "z.ai 申请 Key（与国内 bigmodel.cn 账号不通用）"},
    {"id": "qwen_intl", "group": "国际", "name": "通义国际版（DashScope Intl）",
     "base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen3.8-max", "qwen3.8-flash"],
     "hint": "新加坡站点，与国内百炼账号不通用。用「兼容模式」地址"},
    {"id": "minimax_intl", "group": "国际", "name": "MiniMax 国际版",
     "base": "https://api.minimax.io/v1",
     "models": ["MiniMax-M3"],
     "hint": "platform.minimax.io 申请 Key（与国内站账号不通用）"},
    {"id": "siliconflow_intl", "group": "国际", "name": "硅基流动国际版",
     "base": "https://api.siliconflow.com/v1",
     "models": ["deepseek-ai/DeepSeek-V3"],
     "hint": "siliconflow.com 申请 Key（与国内 .cn 账号不通用）"},

    # ---------------- Token / 编程套餐（2026-11-12 用户反馈：买了套餐却选不到）----------------
    # 背景：智谱 / Kimi / 阿里 / 火山 / 腾讯 的「Coding Plan」「Token Plan」是**订阅制**，
    # 多数另发一套**专属端点**，而且套餐 Key 打到按量端点会直接 401（官方 FAQ 明确写了）。
    # 所以套餐必须单独成条；只收**提供 OpenAI 兼容端点**的——本程序的 AI 接入只走
    # /chat/completions，纯 Anthropic 端点（…/anthropic）在这里用不了。
    {"id": "zhipu_plan", "group": "Token/编程套餐", "name": "智谱 GLM · 编程套餐",
     "base": "https://open.bigmodel.cn/api/coding/paas/v4",
     "models": ["glm-5.2", "glm-5", "glm-4.7"],
     "hint": "bigmodel.cn 的 GLM Coding Plan 专属端点。套餐 Key 打按量端点会 401，"
             "按量 Key 打这里也会 401 —— 两个 Key 不能混用"},
    {"id": "zhipu_plan_intl", "group": "Token/编程套餐",
     "name": "智谱 GLM 国际 · 编程套餐",
     "base": "https://api.z.ai/api/coding/paas/v4",
     "models": ["glm-5.2", "glm-5"],
     "hint": "z.ai 订阅的 Coding Plan 专用端点（与国内 bigmodel.cn 套餐不通用）"},
    {"id": "kimi_plan", "group": "Token/编程套餐", "name": "Kimi Code 会员",
     "base": "https://api.kimi.com/coding/v1",
     "models": ["kimi-for-coding", "k3"],
     "hint": "Kimi Code 会员在 Kimi Code 控制台建 Key。会员 Key 只能配这个地址，"
             "打到开放平台 api.moonshot.cn 会 401"},
    {"id": "kimi_plan_intl", "group": "Token/编程套餐",
     "name": "Kimi Code 会员 · 海外",
     "base": "https://api.kimi.ai/coding/v1",
     "models": ["kimi-for-coding", "k3"],
     "hint": "海外会员端点（api.kimi.ai/coding/v1），与国内 api.kimi.com 账号不通用"},
    {"id": "qwen_plan", "group": "Token/编程套餐", "name": "阿里百炼 · Coding Plan",
     "base": "https://coding.dashscope.aliyuncs.com/v1",
     "models": ["qwen3.7-plus", "qwen3-coder-plus"],
     "hint": "百炼 Coding Plan 专属端点。注意不是按量付费的兼容模式地址"},
    {"id": "qwen_plan_intl", "group": "Token/编程套餐",
     "name": "阿里百炼 · Coding Plan 国际",
     "base": "https://coding-intl.dashscope.aliyuncs.com/v1",
     "models": ["qwen3.7-plus"],
     "hint": "新加坡地域的 Coding Plan 端点（coding-intl.dashscope…），与国内套餐不通用"},
    {"id": "qwen_token_plan", "group": "Token/编程套餐",
     "name": "阿里百炼 · Token Plan",
     "base": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
     "models": [],
     "hint": "Token Plan 订阅（北京地域）端点；新加坡账号把域名换成 "
             "token-plan.ap-southeast-1.maas.aliyuncs.com。模型名可填 auto 或点「测试接口」拉清单"},
    {"id": "minimax_plan", "group": "Token/编程套餐", "name": "MiniMax · Token Plan",
     "base": "https://api.minimaxi.com/v1",
     "models": ["MiniMax-M3", "MiniMax-M2.7"],
     "hint": "Token Plan 订阅 Key（sk-cp-…）在 platform.minimaxi.com 的 Token Plan 页建；"
             "与按量 Key 共用这个 /v1 地址，区别只是计费资源（先扣套餐额度，再走余额）。"
             "不是套餐用户选上面的「MiniMax」即可"},
    {"id": "ark_plan", "group": "Token/编程套餐", "name": "火山方舟 · Coding Plan",
     "base": "https://ark.cn-beijing.volces.com/api/coding/v3",
     "models": [],
     "hint": "方舟 Coding Plan 端点（/api/coding/v3）。按量付费的是 /api/v3，两者 Key 不通用"},
    {"id": "hunyuan_plan", "group": "Token/编程套餐", "name": "腾讯云 · Coding Plan",
     "base": "https://api.lkeap.cloud.tencent.com/coding/v3",
     "models": ["hunyuan-2.0-instruct", "hunyuan-2.0-thinking"],
     "hint": "腾讯云 LKEAP Coding Plan 端点（/coding/v3）；按量付费走 /v1，Key 不同"},

    # ---------------- 自定义 ----------------
    {"id": CUSTOM_ID, "group": "", "name": "自定义（手动填写）",
     "base": "", "models": [],
     "hint": "填任意 OpenAI 兼容端点：中转站、Azure（…/openai/v1 + 部署名）、自建 vLLM / Ollama"},
]

_BY_ID = {p["id"]: p for p in PROVIDERS}

# 域名 → 预设 id；**顺序敏感**：先精确后宽泛，否则 dashscope-intl 会被
# dashscope 抢走、api.moonshot.ai 会被 moonshot 抢走
_HOST_KEYS = [
    ("openai.azure.com", CUSTOM_ID),    # 要填资源名/部署名，只能自定义
    # ---- 套餐端点：必须排在按量泛域名之前，否则会被厂商主域名抢走 ----
    # （套餐 Key 打到按量端点会 401，认错等于配好也用不了）
    ("token-plan", "qwen_token_plan"),          # 阿里 Token Plan（北京/新加坡）
    ("coding-intl.dashscope", "qwen_plan_intl"),
    ("coding.dashscope", "qwen_plan"),
    ("api.z.ai/api/coding", "zhipu_plan_intl"),   # 必须早于下面的 api/coding/paas
    ("api/coding/paas", "zhipu_plan"),          # open.bigmodel.cn/api/coding/paas/v4
    ("api.kimi.com/coding", "kimi_plan"),
    ("api.kimi.ai/coding", "kimi_plan_intl"),
    ("api/coding/v3", "ark_plan"),              # ark…/api/coding/v3
    ("lkeap.cloud.tencent.com", "hunyuan_plan"),
    # ---- 按量付费（原顺序，勿动）----
    ("dashscope-intl", "qwen_intl"),
    ("dashscope", "qwen"),
    ("api.z.ai", "zhipu_intl"),
    ("bigmodel", "zhipu"),
    ("moonshot.ai", "kimi_intl"),
    ("moonshot", "kimi"),
    ("minimax.io", "minimax_intl"),
    ("minimaxi", "minimax"),
    ("minimax.chat", "minimax"),        # 老域名
    ("siliconflow.com", "siliconflow_intl"),
    ("siliconflow", "siliconflow"),
    ("xiaomimimo", "mimo"),
    ("volces", "ark"),
    ("hunyuan", "hunyuan"),
    ("qianfan", "qianfan"),
    ("xf-yun", "spark"),
    ("stepfun", "stepfun"),
    ("baichuan", "baichuan"),
    ("modelscope", "modelscope"),
    ("deepseek", "deepseek"),
    ("anthropic", "anthropic"),
    ("generativelanguage", "gemini"),
    ("api.x.ai", "xai"),
    ("mistral", "mistral"),
    ("groq", "groq"),
    ("together", "together"),
    ("openrouter", "openrouter"),
    ("deepinfra", "deepinfra"),
    ("cerebras", "cerebras"),
    ("nvidia", "nvidia"),
    ("huggingface", "hf"),
    ("novita", "novita"),
    ("fireworks", "fireworks"),
    ("api.openai.com", "openai"),
]


def all_providers() -> list:
    return list(PROVIDERS)


def grouped() -> list:
    """[(组名, [项…]), …]；自定义组名空（UI 用分隔线 + 单项）"""
    out = [(g, [p for p in PROVIDERS if p.get("group") == g]) for g in GROUPS]
    custom = [p for p in PROVIDERS if p["id"] == CUSTOM_ID]
    if custom:
        out.append(("", custom))
    return out


def get(pid: str) -> dict:
    """取预设；未知 id 退回自定义（旧配置/手改 json 都安全）"""
    return _BY_ID.get(str(pid or "").strip().lower()) or _BY_ID[CUSTOM_ID]


def match(base_url: str) -> str:
    """按 base_url 反查预设 id；匹配不到返回 custom"""
    u = (base_url or "").strip().lower()
    if not u:
        return CUSTOM_ID
    for key, pid in _HOST_KEYS:
        if key in u:
            return pid
    return CUSTOM_ID


def is_custom(pid: str) -> bool:
    return get(pid)["id"] == CUSTOM_ID


def default_model(pid: str) -> str:
    p = get(pid)
    return p["models"][0] if p["models"] else ""


def hint(pid: str) -> str:
    return get(pid).get("hint", "")
