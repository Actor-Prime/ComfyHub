# -*- coding: utf-8 -*-
"""AI 接入（2026-10-31 扫描器增强，可选功能）

定位：确定性查不到（核心节点表 / Manager 映射 / repack 仓库）的疑难条目，
才交给大模型推断来源。**结果一律标「待验证」**——ComfyUI 生态周更，
模型知识滞后可能给出不存在的仓库，必须人工确认后才可使用。

接口：任意 OpenAI 兼容 /chat/completions（DeepSeek / 智谱 GLM / 通义 Qwen /
Kimi / OpenAI 均可），配置存 cfg["settings"]：
  ai_enabled / ai_base_url / ai_api_key / ai_model
本模块只读设置、单次调用、解析校验；不落任何缓存、不上传工作流内容
（只发送节点名/文件名这一个词，隐私面最小化）。
"""
import json
import re
import ssl
import urllib.request

# 推断结果只接受这些站点的链接（防幻觉域名）
_HOST_WHITELIST = (
    "github.com", "gitee.com", "gitlab.com",
    "huggingface.co", "hf-mirror.com", "modelscope.cn",
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

# 清单里混着语音/向量类模型（如 mimo-v2.5-asr / tts），chat 不能用的先跳过，
# 否则「不填模型」时会默认选中 asr，测试必然失败（2026-11-01 实测踩到）
_NON_CHAT_HINTS = ("asr", "tts", "voiceclone", "voicedesign", "whisper",
                   "embedding", "embed", "rerank", "ocr")


def pick_chat_model(models: list, prefer: str = "") -> str:
    """从模型清单里挑一个可用于对话的：优先用户指定 → 首个非语音/向量模型"""
    models = [m for m in (models or []) if m]
    if prefer and prefer in models:
        return prefer
    for m in models:
        low = m.lower()
        if not any(k in low for k in _NON_CHAT_HINTS):
            return m
    return models[0] if models else ""


def enabled(settings: dict) -> bool:
    st = settings or {}
    return bool(st.get("ai_enabled") and st.get("ai_base_url")
                and st.get("ai_api_key"))


def _v1_base(base_url: str) -> str:
    """统一成 https://host/v1（用户常只填到域名，或连 /v1 都省）"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("接口地址为空")
    if not base.endswith("/v1") and "/chat/completions" not in base:
        base += "/v1"
    return base


def _ssl_ctx():
    # 国内某些网关证书链不全，与现有 _chat 同口径：不校验证书
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _request(url: str, api_key: str, data: bytes = None, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (api_key or ""),
        "User-Agent": "ComfyHub",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def list_models(base_url: str, api_key: str, timeout: int = 15) -> list:
    """GET /v1/models → 模型 id 列表（按字母序）"""
    data = _request(_v1_base(base_url) + "/models", api_key, timeout=timeout)
    ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    return sorted(ids)


def _post_chat(base_url: str, api_key: str, model: str,
               system: str, user: str, timeout: int = 40,
               max_tokens: int = 200, temperature: float = 0) -> str:
    """发一次对话；遇到「思维链吃光 token 导致正文为空」自动加倍重试。

    2026-11-01 实测 mimo-v2.6-pro：max_tokens=200 时 finish_reason=length、
    content 为空（200 个 token 全花在 reasoning_content 上），加大到 800 才拿到结论。
    这类推理型模型（DeepSeek-R1 系、mimo-v2.6 系）必须留足额度。
    """
    last = ""
    for mt in (max_tokens, max(max_tokens * 4, 800)):
        body = json.dumps({
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": mt,
        }).encode("utf-8")
        data = _request(_v1_base(base_url) + "/chat/completions", api_key,
                        data=body, timeout=timeout)
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if content.strip():
            return content
        last = content
        if choice.get("finish_reason") != "length":
            break        # 不是被截断，那就是真的没内容，重试无意义
    return last


def test_connection(base_url: str, api_key: str, model: str = "",
                    timeout: int = 25) -> dict:
    """测试接口：拉模型清单 + 用选中模型发一条极短对话。

    返回 {ok, models, model, latency_ms, reply, msg}
    设置页「测试接口」按钮就用这个（UI 侧放线程里跑，避免卡界面）。
    """
    import time
    t0 = time.time()
    try:
        models = list_models(base_url, api_key, timeout=min(timeout, 15))
    except Exception as e:
        return {"ok": False, "models": [], "model": "", "latency_ms": 0,
                "reply": "", "msg": "拉取模型列表失败：%s: %s" % (type(e).__name__, e)}
    use = pick_chat_model(models, (model or "").strip())
    if not use:
        return {"ok": False, "models": models, "model": "",
                "latency_ms": int((time.time() - t0) * 1000),
                "reply": "", "msg": "接口没有返回任何模型"}
    try:
        reply = _post_chat(base_url, api_key, use, "", "只回答两个字：你好",
                           timeout=timeout, max_tokens=32)
    except Exception as e:
        return {"ok": False, "models": models, "model": use,
                "latency_ms": int((time.time() - t0) * 1000), "reply": "",
                "msg": "对话请求失败：%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "models": models, "model": use,
            "latency_ms": int((time.time() - t0) * 1000),
            "reply": (reply or "").strip()[:40],
            "msg": "连接正常：%d 个模型，用时 %.1fs" % (len(models),
                                                    (time.time() - t0))}


def _chat(system: str, user: str, settings: dict, timeout=40) -> str:
    return _post_chat(settings["ai_base_url"], settings["ai_api_key"],
                      settings.get("ai_model") or "deepseek-chat",
                      system, user, timeout=timeout)


def _valid_url(u: str) -> str:
    """白名单域名才收；顺手去掉尾部标点"""
    u = u.rstrip(".,;:。，")
    try:
        host = urllib.request.urlparse(u).netloc.lower()
    except Exception:
        return ""
    if any(host == h or host.endswith("." + h) for h in _HOST_WHITELIST):
        return u
    return ""


def _first_url(text: str) -> str:
    for m in _URL_RE.findall(text or ""):
        u = _valid_url(m)
        if u:
            return u
    return ""


def ask_node_origin(node: str, settings: dict, log=print,
                    timeout: int = 25) -> str:
    """问 AI：节点类属于哪个仓库。返回可信 URL 或 ''。"""
    try:
        ans = _chat(
            "你是 ComfyUI 生态专家。用户给你一个 ComfyUI 节点类名，"
            "你判断它属于哪个 GitHub/HuggingFace/ModelScope 仓库。"
            "只输出一个仓库 URL，不确定就只输出 UNKNOWN。",
            f"ComfyUI 节点类名：{node}", settings, timeout=timeout)
    except Exception as e:
        log(f"[AI 接入] 调用失败: {e}")
        return ""
    if "unknown" in ans.lower():
        return ""
    u = _first_url(ans)
    if u:
        log(f"[AI 接入] {node} → {u}（待验证）")
    return u


def ask_model_source(filename: str, settings: dict, log=print,
                     timeout: int = 25) -> dict:
    """问 AI：模型文件在哪个仓库的什么路径。
    返回 {repo, path} 或 None。"""
    try:
        ans = _chat(
            "你是 ComfyUI 模型专家。用户给你一个模型权重文件名，"
            "你给出它所在的仓库 id 与仓库内路径。"
            "只输出两行：第一行仓库 id（如 Comfy-Org/Qwen-Image-2.1），"
            "第二行仓库内完整路径。不确定就只输出 UNKNOWN。",
            f"模型文件名：{filename}", settings, timeout=timeout)
    except Exception as e:
        log(f"[AI 接入] 调用失败: {e}")
        return None
    if "unknown" in ans.lower():
        return None
    lines = [ln.strip() for ln in ans.splitlines() if ln.strip()]
    if len(lines) >= 2 and "/" in lines[0] and "/" in lines[1]:
        repo = lines[0].split()[0]
        path = lines[1].split()[0]
        if re.match(r"^[\w.\-]+/[\w.\-]+$", repo):
            log(f"[AI 接入] {filename} → {repo}:{path}（待验证）")
            return {"repo": repo, "path": path}
    return None
