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


def enabled(settings: dict) -> bool:
    st = settings or {}
    return bool(st.get("ai_enabled") and st.get("ai_base_url")
                and st.get("ai_api_key"))


def _chat(system: str, user: str, settings: dict, timeout=40) -> str:
    base = settings["ai_base_url"].strip().rstrip("/")
    if not base.endswith("/v1") and "/chat/completions" not in base:
        base += "/v1"
    url = base + "/chat/completions"
    body = json.dumps({
        "model": settings.get("ai_model") or "deepseek-chat",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 200,
    }).encode("utf-8")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + settings["ai_api_key"],
        "User-Agent": "ComfyHub",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


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


def ask_node_origin(node: str, settings: dict, log=print) -> str:
    """问 AI：节点类属于哪个仓库。返回可信 URL 或 ''。"""
    try:
        ans = _chat(
            "你是 ComfyUI 生态专家。用户给你一个 ComfyUI 节点类名，"
            "你判断它属于哪个 GitHub/HuggingFace/ModelScope 仓库。"
            "只输出一个仓库 URL，不确定就只输出 UNKNOWN。",
            f"ComfyUI 节点类名：{node}", settings)
    except Exception as e:
        log(f"[AI 接入] 调用失败: {e}")
        return ""
    if "unknown" in ans.lower():
        return ""
    u = _first_url(ans)
    if u:
        log(f"[AI 接入] {node} → {u}（待验证）")
    return u


def ask_model_source(filename: str, settings: dict, log=print) -> dict:
    """问 AI：模型文件在哪个仓库的什么路径。
    返回 {repo, path} 或 None。"""
    try:
        ans = _chat(
            "你是 ComfyUI 模型专家。用户给你一个模型权重文件名，"
            "你给出它所在的仓库 id 与仓库内路径。"
            "只输出两行：第一行仓库 id（如 Comfy-Org/Qwen-Image-2.1），"
            "第二行仓库内完整路径。不确定就只输出 UNKNOWN。",
            f"模型文件名：{filename}", settings)
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
