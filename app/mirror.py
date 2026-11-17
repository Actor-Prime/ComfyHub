# -*- coding: utf-8 -*-
"""模型镜像端点解析中心（2026-11-16）

背景：HF 端点此前硬编码在五处——model_store.MIRROR、component_packs._HF、
local_llm.MODELS 里的 urls、tools.Downloader.download 的字符串替换、
bootstrap 的 SSL 宽松名单与分段并发白名单。用户想换自建反代 / 公司内网镜像 /
ModelScope 中转只能改代码重编译，所以收敛到这一个模块。

配置落点：config["settings"]["hf_endpoint"]，留空 = DEFAULT_ENDPOINT。
读取策略：首次使用时惰性读一次 config 并缓存；设置页保存后 set_endpoint()
立即覆盖缓存，不必重启。

为什么不在每次调用时都 load_config()：config.load_config() 要走 DPAPI 解密
和实例自愈（还会顺手 save_config），而 endpoint() 落在下载分段、搜索翻页这类
热路径上，反复读会把界面拖慢。
"""
import re
import time
import urllib.parse
import urllib.request

# 默认端点：hf-mirror.com（HuggingFace 国内全量镜像，免登录）
DEFAULT_ENDPOINT = "https://hf-mirror.com"
# 官方端点：留作「真·直连」与 is_official() 判断
OFFICIAL_ENDPOINT = "https://huggingface.co"
OFFICIAL_HOSTS = ("huggingface.co", "www.huggingface.co", "hf.co")

SETTINGS_KEY = "hf_endpoint"

_UA = "ComfyHub"

# 缓存：_loaded 标记「已读过一次 config」，之后靠 set_endpoint 维护
_current = ""
_loaded = False


# ---------------------------------------------------------------- 规范化
def normalize(text: str) -> str:
    """把用户输入规范成可用端点；非法或留空一律返回空串。

    合法示例：
        hf-mirror.com                → https://hf-mirror.com
        https://hf-mirror.com/       → https://hf-mirror.com
        https://my.proxy/hf/         → https://my.proxy/hf   （保留路径前缀）
    拒绝：非 http(s)、缺主机、带 query/fragment（拼接时会错位）。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    if "://" not in t:
        t = "https://" + t
    try:
        p = urllib.parse.urlsplit(t)
    except ValueError:
        return ""
    if p.scheme not in ("http", "https"):
        return ""
    if not p.hostname:
        return ""
    # 带查询串/锚点的地址拼 /api/... 会拼错位置，直接判非法让用户改
    if p.query or p.fragment:
        return ""
    host = p.netloc
    if not re.match(r"^[A-Za-z0-9._:\-\[\]]+$", host):
        return ""
    path = (p.path or "").rstrip("/")
    return "%s://%s%s" % (p.scheme, host, path)


def validate(text: str) -> tuple:
    """UI 用：(ok, normalized_or_default, error_msg)"""
    raw = str(text or "").strip()
    if not raw:
        return True, DEFAULT_ENDPOINT, ""
    norm = normalize(raw)
    if not norm:
        return False, "", "地址格式不对，要像 https://hf-mirror.com 这样"
    if "://" not in raw:
        # 用户没写协议，正常补全，不算错
        return True, norm, ""
    return True, norm, ""


def host_of(url: str) -> str:
    """取 URL 的 hostname（小写）；解析失败返回空串"""
    t = str(url or "").strip()
    if not t:
        return ""
    if "://" not in t:
        t = "https://" + t
    try:
        return (urllib.parse.urlsplit(t).hostname or "").lower()
    except ValueError:
        return ""


# ---------------------------------------------------------------- 端点读写
def _read_config_endpoint() -> str:
    try:
        from app import config
        st = (config.load_config() or {}).get("settings") or {}
        return normalize(st.get(SETTINGS_KEY, ""))
    except Exception:
        return ""


def endpoint() -> str:
    """当前生效端点（自定义 > 默认）"""
    global _current, _loaded
    if not _loaded:
        _loaded = True
        _current = _read_config_endpoint()
    return _current or DEFAULT_ENDPOINT


def set_endpoint(text: str) -> str:
    """设置页保存后调用，立即生效。返回实际生效端点。"""
    global _current, _loaded
    _loaded = True
    _current = normalize(text)
    return _current or DEFAULT_ENDPOINT


def refresh_from_config() -> str:
    """丢掉缓存，重新从 config 读（测试/换配置文件后用）"""
    global _current, _loaded
    _loaded = False
    _current = ""
    return endpoint()


def is_custom() -> bool:
    """当前端点不是默认镜像（含用户填了官方站的情况）。

    语义就是 is_default() 的反面，界面上用它决定要不要标「（默认）」。
    """
    return bool(endpoint() != DEFAULT_ENDPOINT)


def is_official() -> bool:
    return host_of(endpoint()) in OFFICIAL_HOSTS


def display() -> str:
    """给界面显示用：只返回地址本身。

    「（默认）」这类标注由 UI 层按 T() 拼，不在这里拼——本模块要保证
    返回值不含待翻译文案，否则英文模式下会漏译。
    """
    return endpoint()


def is_default() -> bool:
    return endpoint() == DEFAULT_ENDPOINT


def extra_hosts() -> tuple:
    """自定义端点的 host，供 SSL 宽松名单 / 分段并发白名单动态追加。

    只在「确实改过端点」时返回：默认端点的 host 早已写死在那两份白名单里，
    再返回一遍是冗余。官方域名也不返回——它要严格校验 + 单连接，
    不该被并进「加速域名」。
    """
    h = host_of(endpoint())
    if not h or h in OFFICIAL_HOSTS or h == host_of(DEFAULT_ENDPOINT):
        return ()
    return (h,)


# ---------------------------------------------------------------- 地址构造
def hf_url(repo_id: str, file_path: str = "") -> str:
    """拼模型文件直链：{endpoint}/{repo}/resolve/main/{path}"""
    repo = str(repo_id or "").strip().strip("/")
    if not file_path:
        return "%s/%s" % (endpoint(), repo)
    return "%s/%s/resolve/main/%s" % (endpoint(), repo, str(file_path).lstrip("/"))


def api_url(path: str) -> str:
    """拼 API 路径：{endpoint}/api/... （path 可带可不带前导斜杠）"""
    p = str(path or "").lstrip("/")
    return "%s/%s" % (endpoint(), p) if p else endpoint()


# 官方站的各种写法（用户直贴的链接来源不一）
_SCHEME_HOSTS = ("https://huggingface.co/", "http://huggingface.co/",
                 "//huggingface.co/", "https://hf.co/", "http://hf.co/")

# 默认镜像的写法。本地模式表（local_llm.MODELS 等）里写的都是默认镜像直链，
# 用户换端点后这些地址必须跟着换，否则「换了端点却不生效」——2026-11-16 实测踩到。
_DEFAULT_HOSTS = ("https://hf-mirror.com/", "http://hf-mirror.com/",
                  "//hf-mirror.com/")


def rewrite(url: str) -> str:
    """把指向官方站 / 默认镜像的直链改写到当前端点；其余原样返回。

    只改写 host 前缀，不动仓库路径 —— hf-mirror / 自建反代都是路径同构的。
    端点就是默认镜像时改写结果与原值相等，天然幂等。
    兜底：官方域名出现在 URL 中段（旧版 tools.download 的行为）也替换，
    但当前端点自身已含官方域名时不动，避免改出两层前缀。
    """
    u = str(url or "")
    if not u:
        return u
    ep = endpoint()
    low = u.lower()
    for src in _SCHEME_HOSTS + _DEFAULT_HOSTS:
        if low.startswith(src):
            return ep + "/" + u[len(src):]
    if "huggingface.co/" in low and host_of(ep) not in OFFICIAL_HOSTS:
        return re.sub(r"huggingface\.co/", host_of(ep) + "/", u, flags=re.I)
    return u


# ---------------------------------------------------------------- 连通性自检
def describe_error(e: Exception) -> str:
    """把网络异常翻成人话（用户看得懂才有意义）。

    返回值是**固定短语**，不带动态值：界面层要用 T() 查表翻译，
    带占位符的句子进不了词表。表外的异常退回英文异常名（本来就不译）。
    """
    txt = str(e) or type(e).__name__
    low = txt.lower()
    name = type(e).__name__.lower()
    if "timed out" in low or "timeout" in name:
        return "连接超时（网络不通或地址有误）"
    if "certificate" in low or ("ssl" in low and "verify" in low):
        return "证书校验失败"
    if "getaddrinfo" in low or "name or service not known" in low or "nodename" in low:
        return "域名解析失败（地址拼错了？）"
    if "connection refused" in low:
        return "连接被拒绝（端口/服务没起？）"
    return type(e).__name__


def probe(base: str = "", timeout: float = 8.0) -> tuple:
    """连通性自检：GET {base}/api/models?limit=1。

    返回 (ok, msg, ms)：msg 是固定短语（供 UI 翻译），ms 是耗时（0=没测成）。
    base 留空测当前端点；传值测「用户刚填但还没保存」的地址。
    **会阻塞**，界面里必须丢到后台线程调用。
    """
    target = normalize(base) if str(base or "").strip() else endpoint()
    if not target:
        return False, "地址格式不对", 0
    url = target + "/api/models?limit=1"
    try:
        from app import bootstrap
        ctx = bootstrap._ssl_ctx_for(url)
        t0 = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        kw = {"timeout": timeout}
        if ctx is not None:
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            code = int(getattr(r, "status", 0) or r.getcode() or 0)
            r.read(2048)            # 读一点就够，避免大响应挂住
        ms = int((time.time() - t0) * 1000)
        if code == 200:
            return True, "可用", ms
        return False, "返回 HTTP %s" % code, ms
    except Exception as e:
        return False, describe_error(e), 0
