# -*- coding: utf-8 -*-
"""实例 API 基址与认证（2026-11-23，0.8.39）

背景：0.8.38 及以前，所有 HTTP 调用都硬编码 `http://127.0.0.1:{port}`，
散落在 12 处（ai_tools / batch_runner / task_watch / health_check / cli / UI）。
一旦用户想连另一台机器或云镜像（仙宫云、千里云这类），改一处不够、漏一处就
出现「状态是运行中但操作报拒绝连接」的分裂表现。

本模块把「实例 → 基址 + 认证头 + opener」收敛成唯一入口：

  · kind="local"（或缺省，老配置）→ http://127.0.0.1:{port}
  · kind="remote"                 → base_url（http/https 均可）

两处必须区别对待的坑：
  1. **代理**：127.0.0.1 走系统代理会 10054（0.8.2x 实测过），所以回环地址一律
     挂空 ProxyHandler；远程反而应该沿用系统代理——国内访问云镜像经常需要。
  2. **TLS**：云平台 / 自建反代常用自签证书，tls_verify=False 时退化为不校验。

认证按 auth_type 分四型（none / bearer / basic / header），密文字段走
secret_store 的 DPAPI，落盘不落明文（见 config.py 的 _protect_instance）。
"""
import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

from app import secret_store

# 回环判定：这些 host 一律不经过系统代理
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

# 认证类型（UI 下拉与配置文件共用这一份）
AUTH_TYPES = ("none", "bearer", "basic", "header")

UA = "ComfyHub"


# ---------------------------------------------------------------- 实例属性
def is_remote(inst: dict) -> bool:
    return str((inst or {}).get("kind") or "local") == "remote"


def normalize_url(u: str) -> str:
    """补协议、去尾斜杠；留空返回空串（调用方据此判「没配置」）"""
    u = str(u or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    return u.rstrip("/")


def api_base(inst: dict) -> str:
    """实例的 HTTP 基址；远程没填地址时返回空串"""
    if is_remote(inst):
        return normalize_url((inst or {}).get("base_url") or "")
    return "http://127.0.0.1:%d" % _port(inst)


def _port(inst: dict) -> int:
    try:
        return int((inst or {}).get("port") or 8188)
    except (TypeError, ValueError):
        return 8188


def _is_loopback(base: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(base).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK


# ---------------------------------------------------------------- 认证
def _plain(v: str) -> str:
    """密文解成明文；解不开（换机器/换用户）当没填过，绝不把密文发出去"""
    v = str(v or "")
    if not v:
        return ""
    if secret_store.is_encrypted(v):
        return secret_store.unprotect(v) or ""
    return v


def auth_headers(inst: dict) -> dict:
    kind = str((inst or {}).get("auth_type") or "none").lower()
    if kind not in AUTH_TYPES or kind == "none":
        return {}
    if kind == "bearer":
        tok = _plain(inst.get("auth_value"))
        return {"Authorization": "Bearer " + tok} if tok else {}
    if kind == "basic":
        user = _plain(inst.get("auth_user"))
        pwd = _plain(inst.get("auth_pass"))
        if not (user or pwd):
            return {}
        raw = ("%s:%s" % (user, pwd)).encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
    # header：平台自定义头（X-API-KEY / X-Token 之类）
    name = str(inst.get("auth_header_name") or "").strip() or "X-API-KEY"
    val = _plain(inst.get("auth_value"))
    return {name: val} if val else {}


def headers(inst: dict, extra: dict | None = None) -> dict:
    h = {"User-Agent": UA}
    h.update(auth_headers(inst))
    if extra:
        h.update(extra)
    return h


# ---------------------------------------------------------------- 传输
def _opener(inst: dict):
    base = api_base(inst)
    if _is_loopback(base) or not is_remote(inst):
        # 本机（含 SSH 隧道映射到 127.0.0.1）绝不走代理
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    handlers = []
    if not bool(inst.get("tls_verify", True)):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers) if handlers \
        else urllib.request.build_opener()


class ApiError(RuntimeError):
    """带 HTTP 状态的错误，UI/AI 侧据此翻译成人话"""

    def __init__(self, msg: str, status: int = 0):
        self.status = status
        super().__init__(msg)


def open_url(inst: dict, url: str, data: bytes | None = None,
             timeout: int = 30) -> bytes:
    """用实例的认证/代理/TLS 设置去请求一个**完整 URL**（批量执行器这类自己拼
    路径的老代码走这里，避免重复实现 opener 与认证头）"""
    h = headers(inst, {"Content-Type": "application/json"} if data else None)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with _opener(inst).open(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}", e.code) from e
    except urllib.error.URLError as e:
        raise ApiError(_translate_net(e), 0) from e


def request_json(inst: dict, path: str, data: dict | None = None,
                 timeout: int = 30) -> dict:
    """GET/POST 一个 JSON 端点（data 非空即 POST）"""
    base = api_base(inst)
    if not base:
        raise ApiError("远程实例未填写访问地址", 0)
    url = base + path
    body = json.dumps(data).encode("utf-8") if data is not None else None
    h = headers(inst, {"Content-Type": "application/json"} if body else None)
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with _opener(inst).open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise ApiError(f"HTTP {e.code}：{detail or e.reason}", e.code) from e
    except urllib.error.URLError as e:
        raise ApiError(_translate_net(e), 0) from e


def request_bytes(inst: dict, path: str, timeout: int = 60) -> bytes:
    base = api_base(inst)
    if not base:
        raise ApiError("远程实例未填写访问地址", 0)
    req = urllib.request.Request(base + path, headers=headers(inst))
    try:
        with _opener(inst).open(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}", e.code) from e
    except urllib.error.URLError as e:
        raise ApiError(_translate_net(e), 0) from e


def _translate_net(e) -> str:
    """把 urlopen 的底层报错翻成能看懂的话（远程与本地语境不同）"""
    reason = getattr(e, "reason", e)
    txt = str(reason)
    low = txt.lower()
    if "10061" in txt or "refused" in low or "积极拒绝" in txt:
        return "连接被拒绝：目标没在监听（实例未启动或端口不对）"
    if "getaddrinfo" in low or "name or service not known" in low:
        return "域名解析失败：检查地址是否写对、当前网络能否访问"
    if "certificate" in low or "ssl" in low:
        return "TLS 证书校验失败：自签证书请在实例设置里关闭「校验 HTTPS 证书」"
    if "timed out" in low or "timeout" in low:
        return "连接超时：检查防火墙 / 平台是否放行该端口"
    if "401" in txt or "403" in txt:
        return "认证失败：检查 Token / 账号密码 / 自定义请求头"
    return txt[:200]


# ---------------------------------------------------------------- 连通探测
def probe(inst: dict, timeout: float = 4) -> bool:
    """是否是一个活着的 ComfyUI（不只是端口有人监听）

    0.8.39：作为「外部已启动实例」的判定依据。只探 TCP 会把「端口被别的程序
    占着」误判成实例运行中，所以必须 GET /system_stats 且返回里有 system/devices
    这类 ComfyUI 特征字段。
    """
    try:
        d = request_json(inst, "/system_stats", timeout=timeout)
    except Exception:
        return False
    return isinstance(d, dict) and ("system" in d or "devices" in d)


def probe_reason(inst: dict, timeout: float = 4) -> tuple:
    """(是否就绪, 失败原因) —— UI 的「测试连接」按钮要用原因做提示"""
    try:
        d = request_json(inst, "/system_stats", timeout=timeout)
    except ApiError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)[:200]
    if not isinstance(d, dict):
        return False, "返回不是 JSON 对象"
    if "system" not in d and "devices" not in d:
        return False, "该地址有响应但不是 ComfyUI（缺少 system/devices 字段）"
    return True, ""


# ---------------------------------------------------------------- 远端取图
def view_path(filename: str, subfolder: str = "", type_: str = "output") -> str:
    q = urllib.parse.urlencode({
        "filename": filename,
        "subfolder": (subfolder or "").replace("\\", "/").strip("/"),
        "type": type_ or "output",
    })
    return "/view?" + q


def remote_output_dir(inst: dict, cfg: dict | None = None) -> str:
    """远程产物落地目录：%LOCALAPPDATA%\\ComfyHub\\remote\\<实例名>

    cfg.settings.remote_output_dir 可整体覆盖（留空=默认）。0.8.39 起启用。
    """
    st = (cfg or {}).get("settings") or {}
    root = str(st.get("remote_output_dir") or "").strip()
    if not root:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        root = os.path.join(base, "ComfyHub", "remote")
    name = str((inst or {}).get("name") or (inst or {}).get("id") or "remote")
    safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "remote"
    return os.path.join(root, safe)


def fetch_view(inst: dict, filename: str, subfolder: str = "",
               type_: str = "output", dest_dir: str = "") -> str:
    """把远端 /view 的产物下载到本地，返回本地绝对路径（失败抛 ApiError）

    为什么必须下载而不是返回 remote:// 路径：AI 助手要拿路径去 tell 用户、
    「打开文件」要 os.startfile、二次处理要读字节——全都要求真实本地文件。
    同名文件已存在且大小一致时直接复用，避免同一张图反复下载。
    """
    name = os.path.basename(str(filename or ""))
    if not name:
        raise ApiError("远端返回的文件名为空", 0)
    dest = dest_dir or remote_output_dir(inst)
    os.makedirs(dest, exist_ok=True)
    target = os.path.join(dest, name)
    data = request_bytes(inst, view_path(name, subfolder, type_), timeout=120)
    if not data:
        raise ApiError("远端 /view 返回空内容", 0)
    if os.path.isfile(target) and os.path.getsize(target) == len(data):
        return target
    stem, ext = os.path.splitext(target)
    n = 1
    while os.path.isfile(target) and os.path.getsize(target) != len(data):
        target = f"{stem}_{n}{ext}"
        n += 1
    tmp = target + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)
    return target
