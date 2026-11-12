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
import socket
import ssl
import urllib.error
import urllib.request

from app.i18n import T

# 推断结果只接受这些站点的链接（防幻觉域名）
_HOST_WHITELIST = (
    "github.com", "gitee.com", "gitlab.com",
    "huggingface.co", "hf-mirror.com", "modelscope.cn",
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

# 地址末尾的版本段：/v1 /v2 /v3 /v4（含 /api/paas/v4 这种多段路径）
_VER_TAIL = re.compile(r"/v\d+$")

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
    """统一成带版本段的 base。

    默认补 /v1；但**已带版本段的原样用**——智谱是 /api/paas/v4、火山方舟是
    /api/v3，盲补 /v1 会拼出 /v4/v1/chat/completions → 404
    （2026-11-12 加服务商预设时暴露的坑）。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("接口地址为空")
    if "/chat/completions" in base:
        return base
    # 已带版本段（/v1 /v2 /v3 /v4）或以 /openai 结尾（Gemini、DeepInfra、
    # Novita 的兼容层形态）→ 原样用，别盲补 /v1；智谱是 /v4、火山是 /v3
    if _VER_TAIL.search(base) or base.endswith("/openai"):
        return base
    return base + "/v1"


def _ssl_ctx():
    # 国内某些网关证书链不全，与现有 _chat 同口径：不校验证书
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------- 错误人话化
class ApiError(RuntimeError):
    """HTTP 4xx/5xx 包装：保留状态码 + 服务端原文，交给 describe_error 翻人话。

    为什么单独包一层（2026-11-12 用户反馈）：界面上原来直接甩
    「HTTPError: HTTP Error 401: Unauthorized」——用户既看不懂，也不知道该去改哪里。
    原始信息不丢（气泡 tooltip + 日志里都有），但展示必须先给人话。
    """

    def __init__(self, status: int, detail: str = "", url: str = ""):
        self.status = int(status or 0)
        self.detail = (detail or "").strip()[:400]
        self.url = url or ""
        super().__init__(f"HTTP {self.status}: {self.detail or '无返回内容'}")


def _read_error_body(e) -> str:
    """从 HTTPError 里抠出服务端给的原因（OpenAI 风格 {error:{message}}）"""
    try:
        raw = e.read() or b""
    except Exception:
        return ""
    txt = raw[:4000].decode("utf-8", "ignore").strip()
    try:
        d = json.loads(txt)
    except Exception:
        return txt[:400]
    if not isinstance(d, dict):
        return txt[:400]
    err = d.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("msg") or "")[:400]
    if isinstance(err, str):
        return err[:400]
    for k in ("message", "msg", "detail", "error_msg"):
        if d.get(k):
            return str(d[k])[:400]
    return txt[:400]


_STATUS_TIPS_ZH = {
    401: ("认证失败（401）：API Key 无效，或与所选服务商/套餐对不上。\n"
          "常见原因：① Key 没复制完整；② 套餐 Key 与按量 Key 不能混用"
          "（Coding Plan / Token Plan 的 Key 打到按量端点必 401，反之亦然）；"
          "③ Key 已停用或欠费。\n"
          "到「系统设置 → AI 接入」核对条目后点「测试接口」验证一次。"),
    403: ("拒绝访问（403）：这个 Key 没有该模型或套餐的权限。\n"
          "确认模型在当前套餐内、套餐没过期，或者换一个模型名再试。"),
    404: ("地址不对（404）：接口地址或模型名不存在。\n"
          "套餐端点和按量端点是两个不同地址（如 GLM 的 /api/coding/paas/v4 与 "
          "/api/paas/v4），确认服务商条目没选错；模型名可点「测试接口」自动拉取。"),
    429: ("请求被限流（429）：调用太频繁，或套餐额度 / 账户余额已用尽。\n"
          "稍等一会儿再试，或去厂商控制台看额度与频率限制。"),
    402: "余额不足（402）：账户欠费或本期额度已用完，去厂商控制台充值/续订。",
    400: ("请求被拒绝（400）：参数或模型名有问题。\n"
          "多数是模型名不存在，或该模型不支持工具调用（本助手需要 function calling）。"
          "到「系统设置 → AI 接入」核对模型名，或换一个支持工具调用的模型。"),
}


def describe_error(e, one_line: bool = False) -> str:
    """底层异常 → 用户看得懂的一段话（带下一步动作）。

    覆盖：HTTP 4xx/5xx（含服务端原文）、连不上 / 超时 / 代理问题、其余兜底。
    one_line=True 用于只能显示一行的位置（设置页「测试接口」状态栏）。
    """
    out = _describe_error(e)
    if one_line:
        out = " ".join(str(out).split())
        return out if len(out) <= 200 else out[:198] + "…"
    return out


def _describe_error(e) -> str:
    status = int(getattr(e, "status", 0) or getattr(e, "code", 0) or 0)
    detail = str(getattr(e, "detail", "") or "").strip()

    if status:
        if status in _STATUS_TIPS_ZH:
            msg = T(_STATUS_TIPS_ZH[status])
        elif 500 <= status < 600:
            msg = T("服务端出错（{s}）：厂商侧故障，不是你的配置问题。"
                    "稍后重试；持续出现就换个模型或联系厂商。").format(s=status)
        else:
            msg = T("请求失败（HTTP {s}）。").format(s=status)
        if detail and status not in (401, 403):
            # 401/403 的原文通常只有 Unauthorized/Forbidden，没有信息量
            msg += "\n" + T("服务端原文：") + detail[:160]
        return msg

    # ---- 网络层 ----
    reason = getattr(e, "reason", e)
    txt = f"{type(reason).__name__} {reason}".lower()
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in txt \
            or "timeout" in txt or "超时" in str(reason):
        return T("请求超时：网络慢，或者接口一直没响应。\n"
                 "境外服务需要能走代理（把「直连」取消勾选）；"
                 "国内接口一般勾上「直连」更快更稳。")
    if "certificate" in txt or "ssl" in txt:
        return T("证书校验失败：网络中间有拦截（公司网关 / 代理），"
                 "换个网络或关掉代理再试。")
    if isinstance(reason, urllib.error.URLError) or "getaddrinfo" in txt \
            or "name or service not known" in txt:
        return T("连不上服务器：{r}\n检查接口地址是否写错、网络能否访问该站点；"
                 "境外服务请确认代理已开启。").format(r=reason)
    if "10061" in txt or "refused" in txt or "积极拒绝" in str(reason):
        return T("连接被拒绝：目标端口没开（{r}）。检查接口地址和端口。").format(r=reason)
    if "10054" in txt or "reset" in txt:
        return T("连接被重置：代理或网络中途断开，重试一次；"
                 "反复出现请换网络或关闭代理。")

    return f"{type(e).__name__}: {e}"


def make_request(url: str, api_key: str, data: bytes = None):
    """构造请求（流式接口需要自己接管读取，故与 _request 拆开）"""
    return urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (api_key or ""),
        "User-Agent": "ComfyHub",
    })


_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_request(req, timeout: int = 20, direct: bool = False):
    """打开连接（证书策略与 _request 一致；国内网关证书链常不全）

    direct=True：绕过系统代理直连。2026-11-06 实测踩到——系统里挂了本地代理
    （127.0.0.1:xxxxx）时代理一抖动，请求会长时间无数据，界面就卡在「思考中」。
    国产接口直连通常更快更稳，国外接口则需要走代理。

    HTTP 4xx/5xx 统一包成 ApiError（2026-11-12）：带着状态码和服务端原因往上抛，
    调用方用 describe_error() 翻成人话再给界面。
    """
    try:
        if direct:
            return _DIRECT_OPENER.open(req, timeout=timeout)
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx())
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, _read_error_body(e), getattr(e, "url", "") or "") from e


def _request(url: str, api_key: str, data: bytes = None, timeout: int = 20,
             direct: bool = False) -> dict:
    with open_request(make_request(url, api_key, data), timeout=timeout,
                      direct=direct) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def list_models(base_url: str, api_key: str, timeout: int = 15) -> list:
    """GET /v1/models → 模型 id 列表（按字母序）"""
    data = _request(_v1_base(base_url) + "/models", api_key, timeout=timeout)
    ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    return sorted(ids)


def _post_chat(base_url: str, api_key: str, model: str,
               system: str, user: str, timeout: int = 40,
               max_tokens: int = 200, temperature: float = 0,
               extra: dict = None) -> str:
    """发一次对话；遇到「思维链吃光 token 导致正文为空」自动加倍重试。

    2026-11-01 实测 mimo-v2.6-pro：max_tokens=200 时 finish_reason=length、
    content 为空（200 个 token 全花在 reasoning_content 上），加大到 800 才拿到结论。
    这类推理型模型（DeepSeek-R1 系、mimo-v2.6 系）必须留足额度。
    """
    last = ""
    for mt in (max_tokens, max(max_tokens * 4, 800)):
        payload = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": mt,
        }
        if extra:
            payload.update(extra)      # 思考开关等厂商私有字段（app/ai_models.py 决定形态）
        body = json.dumps(payload).encode("utf-8")
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
                "reply": "", "msg": "拉取模型列表失败：" + describe_error(e, one_line=True)}
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
                "msg": "对话请求失败：" + describe_error(e, one_line=True)}
    return {"ok": True, "models": models, "model": use,
            "latency_ms": int((time.time() - t0) * 1000),
            "reply": (reply or "").strip()[:40],
            "msg": "连接正常：%d 个模型，用时 %.1fs" % (len(models),
                                                    (time.time() - t0))}


def _chat(system: str, user: str, settings: dict, timeout=40) -> str:
    """扫描器侧的短推断：同样走模型画像，避免推理模型把时间烧在思考上"""
    from app import ai_models
    prof = ai_models.detect(settings.get("ai_model") or "",
                            settings.get("ai_base_url") or "")
    think = ai_models.effective_think(prof, settings.get("ai_think") or "")
    extra, _tip = ai_models.thinking_params(prof, think)
    mt = 400 if think == "off" else 1200
    return _post_chat(settings["ai_base_url"], settings["ai_api_key"],
                      settings.get("ai_model") or "deepseek-chat",
                      system, user, timeout=timeout, max_tokens=mt,
                      extra=extra)


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
