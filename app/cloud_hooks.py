# -*- coding: utf-8 -*-
"""云平台开关机 HTTP 钩子（2026-12-01，0.8.40 T05）

**本模块不含任何 UI**（禁止 import PySide6）：模板骨架、请求构造、预检、发送、
错误归一到 `HookError.kind`，全部可在离屏/无 Qt 环境跑单测。

解决的问题（PRD B）：很多云镜像只给 Web API 不给 SSH，开关机就是「打一个
带 Token 的 HTTP 请求」。平台之间路径/参数名/鉴权方式各不相同，所以这里
**只提供骨架模板，不预置任何真实平台参数**（PRD 决策 2：宁可少而准，也不要
填错参数让用户以为能用）。

配置结构（`inst["cloud_start"]` / `inst["cloud_stop"]`，见 config._INST_DEFAULTS）::

    {"url": "https://host/path", "method": "POST",
     "headers": "Authorization: Bearer xxx\\nContent-Type: application/json",
     "body": "{\\"instance_id\\": \\"i-123\\"}"}

`headers` / `body` 一律**存文本**（不是 dict）：请求头是「一行一个 Key: Value」的
多行编辑框，请求体是 JSON 文本编辑框，用户在界面上看到什么就存什么，
不会出现「配置里是 dict、界面上是字符串」两套真相。

错误只有四类（ARCH §3.3，UI 只按 kind 查 5.2.3 的逐字文案）：
  `url_invalid` —— URL 不合法（空 / 缺协议 / 非 http(s)）
  `http`        —— 平台返回非 2xx
  `network`     —— 请求发不出去（DNS / 拒绝连接 / 代理不通）
  `timeout`     —— 30 秒没有响应

⚠ 请求体不是合法 JSON **不在这里抛**：UI 在发送前调 `precheck()` 拦下并提示
「请求体不是合法 JSON（第 n 行）」（PRD 边界 #14「不发请求」），所以 `send()`
永远不会因为请求体格式报错——它拿到的 body 已经被预检过。
"""
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- 常量
KIND_URL = "url_invalid"
KIND_HTTP = "http"
KIND_NETWORK = "network"
KIND_TIMEOUT = "timeout"
KINDS = (KIND_URL, KIND_HTTP, KIND_NETWORK, KIND_TIMEOUT)

METHODS = ("GET", "POST", "PUT", "PATCH")
DEFAULT_METHOD = "POST"

HOOK_TIMEOUT = 30.0          # PRD 5.2.3 第 4 条：平台接口 30 秒没有响应就放弃
BODY_PREVIEW = 200           # 失败文案里贴的响应体长度
UA = "ComfyHub"

# 模板骨架里一律用 ASCII 占位符（`<INSTANCE_ID>` 这类），不放任何真实平台域名/
# 路径/参数名（PRD 决策 2）。用 ASCII 还有一个好处：英文界面下模板内容零中文残留。
_T_PLACEHOLDER_TIP = "模板只是骨架，参数（实例 ID、区域、Token）请按平台 API 文档核对后再保存"


# ---------------------------------------------------------------- 模板骨架
def _block(url="", method=DEFAULT_METHOD, headers="", body=""):
    return {"url": url, "method": method, "headers": headers, "body": body}


TEMPLATES = [
    {
        "id": "generic",
        "name": "通用（自己填）",
        "start": _block(
            url="https://<PLATFORM_HOST>/<START_PATH>",
            method="POST",
            headers="Authorization: Bearer <TOKEN>",
            body='{\n  "instance_id": "<INSTANCE_ID>"\n}'),
        "stop": _block(
            url="https://<PLATFORM_HOST>/<STOP_PATH>",
            method="POST",
            headers="Authorization: Bearer <TOKEN>",
            body='{\n  "instance_id": "<INSTANCE_ID>"\n}'),
    },
    {
        "id": "json_bearer",
        "name": "JSON + Bearer Token",
        "start": _block(
            url="https://<PLATFORM_HOST>/v1/instances/<INSTANCE_ID>/start",
            method="POST",
            headers="Authorization: Bearer <TOKEN>\nContent-Type: application/json",
            body='{\n  "action": "start"\n}'),
        "stop": _block(
            url="https://<PLATFORM_HOST>/v1/instances/<INSTANCE_ID>/stop",
            method="POST",
            headers="Authorization: Bearer <TOKEN>\nContent-Type: application/json",
            body='{\n  "action": "stop"\n}'),
    },
    {
        "id": "form_post",
        "name": "表单 POST",
        "start": _block(
            url="https://<PLATFORM_HOST>/<START_PATH>",
            method="POST",
            headers=("Content-Type: application/x-www-form-urlencoded\n"
                     "Authorization: Bearer <TOKEN>"),
            body="instance_id=<INSTANCE_ID>&action=start"),
        "stop": _block(
            url="https://<PLATFORM_HOST>/<STOP_PATH>",
            method="POST",
            headers=("Content-Type: application/x-www-form-urlencoded\n"
                     "Authorization: Bearer <TOKEN>"),
            body="instance_id=<INSTANCE_ID>&action=stop"),
    },
    {
        "id": "get_noauth",
        "name": "无鉴权 GET",
        "start": _block(
            url=("https://<PLATFORM_HOST>/<START_PATH>"
                 "?instance_id=<INSTANCE_ID>&action=start"),
            method="GET", headers="", body=""),
        "stop": _block(
            url=("https://<PLATFORM_HOST>/<STOP_PATH>"
                 "?instance_id=<INSTANCE_ID>&action=stop"),
            method="GET", headers="", body=""),
    },
]

# 模板清单的 id 集合（UI 下拉与测试共用）
TEMPLATE_IDS = tuple(t["id"] for t in TEMPLATES)

# UI 灰字提示（与 PRD 5.2.1 逐字一致）
TEMPLATE_TIP = _T_PLACEHOLDER_TIP


def template_of(tpl_id: str) -> dict:
    """按 id 取模板（找不到返回 None；深拷贝交给调用方）"""
    for t in TEMPLATES:
        if t.get("id") == str(tpl_id or ""):
            return t
    return None


def template_names() -> list:
    """下拉用：[(显示名, id), ...]"""
    return [(t["name"], t["id"]) for t in TEMPLATES]


# ---------------------------------------------------------------- 错误类型
class HookError(Exception):
    """钩子请求失败；`kind` ∈ `KINDS`（UI 只按 kind 查文案）"""

    def __init__(self, kind: str, detail: str = "", *, status: int = 0,
                 body: str = "", url: str = ""):
        self.kind = str(kind or KIND_NETWORK)
        self.detail = str(detail or "")
        self.status = int(status or 0)
        self.body = str(body or "")
        self.url = str(url or "")
        super().__init__(f"{self.kind}: {self.detail}" if self.detail else self.kind)


# ---------------------------------------------------------------- 归一
def _as_headers_text(value) -> str:
    """配置里可能被手改成 dict → 还原成「一行一个 Key: Value」的文本"""
    if isinstance(value, dict):
        return "\n".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x) for x in value)
    return str(value or "")


def _as_body_text(value) -> str:
    """配置里可能被手改成 dict/list → 还原成 JSON 文本（缩进两格，和界面一致）"""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return ""
    return str(value or "")


def normalize_block(block) -> dict:
    """把可能残缺的 `cloud_start` / `cloud_stop` 归一成四字段

    永远返回新 dict（绝不改写调用方传进来的配置对象）。
    """
    b = block if isinstance(block, dict) else {}
    method = str(b.get("method") or DEFAULT_METHOD).strip().upper()
    if method not in METHODS:
        method = DEFAULT_METHOD           # 脏值回落，不阻断保存
    return {
        "url": str(b.get("url") or "").strip(),
        "method": method,
        "headers": _as_headers_text(b.get("headers")),
        "body": _as_body_text(b.get("body")),
    }


def block_url(block) -> str:
    """URL 是否算「已配置」（空 = 该通道未配置，PRD B1）"""
    return normalize_block(block)["url"]


# ---------------------------------------------------------------- 预检
def url_error(url: str) -> str:
    """URL 校验：返回错误文案，合法返回空串

    要求：非空、带 `http://` / `https://`、有主机名。不校验路径是否真存在
    （那要靠平台文档核对，本版不做猜测）。
    """
    raw = str(url or "").strip()
    if not raw:
        return "URL 是空的"
    if "://" not in raw:
        return f"`{raw}` 缺少协议头（要写 http:// 或 https://）"
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return f"`{raw}` 解析不出来"
    if parts.scheme.lower() not in ("http", "https"):
        return f"`{raw}` 的协议是 `{parts.scheme}`，只支持 http / https"
    if not (parts.hostname or "").strip():
        return f"`{raw}` 里没有主机名"
    return ""


def parse_headers(text: str) -> tuple:
    """多行 `Key: Value` → (dict, 错误文案)

    · 空行与 `#` 开头的行忽略（便于用户留注释）
    · 值里允许出现冒号（`Authorization: Bearer x`）→ 只按**第一个**冒号切
    · 没有冒号的行报错并带行号：不猜用户想发什么头
    """
    out = {}
    raw = str(text or "")
    for i, line in enumerate(raw.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            return {}, (f"请求头第 {i} 行不是 `Key: Value` 格式：`{s[:60]}`")
        name, _, value = s.partition(":")
        name = name.strip()
        if not name:
            return {}, f"请求头第 {i} 行的头名是空的"
        out[name] = value.strip()
    return out, ""


def validate_body(text: str) -> tuple:
    """请求体校验：空 = 合法（不校验）；否则必须是合法 JSON

    返回 `(对象或 None, 错误文案)`。非法时的文案逐字照 PRD 边界 #14：
    `请求体不是合法 JSON（第 n 行）：请检查逗号和引号`
    """
    raw = str(text or "").strip()
    if not raw:
        return None, ""
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as e:
        line = int(getattr(e, "lineno", 0) or 1)
        return None, f"请求体不是合法 JSON（第 {line} 行）：请检查逗号和引号"
    except ValueError:
        return None, "请求体不是合法 JSON（第 1 行）：请检查逗号和引号"


def precheck(block, *, action: str = "start") -> tuple:
    """发送前全量预检：返回 `(ok, 错误文案)`

    PRD 边界 #14 要求「发送前校验，不发出请求」，UI 点「发开机/关机请求」和
    保存前的校验都走这一个入口，保证两处口径一致。
    """
    b = normalize_block(block)
    label = "关机" if str(action or "").lower() == "stop" else "开机"
    err = url_error(b["url"])
    if err:
        return False, (f"{label}请求的 URL 不合法：{b['url'] or '（空）'}。\n"
                       "要填完整地址，包含 http:// 或 https://，以及平台要求的路径。")
    _h, h_err = parse_headers(b["headers"])
    if h_err:
        return False, h_err
    _o, b_err = validate_body(b["body"])
    if b_err:
        return False, b_err
    return True, ""


# ---------------------------------------------------------------- 发送
def build_request(block) -> urllib.request.Request:
    """构造 Request（不做网络 IO，便于单测断言 URL/方法/头/体）"""
    b = normalize_block(block)
    err = url_error(b["url"])
    if err:
        raise HookError(KIND_URL, err, url=b["url"])
    headers, h_err = parse_headers(b["headers"])
    if h_err:
        # 预检已经拦过，这里只是兜底（正常路径走不到）
        raise HookError(KIND_URL, h_err, url=b["url"])
    # GET 也允许带 body（少数平台的怪癖），有内容就发，不做假设
    data = b["body"].encode("utf-8") if b["body"].strip() else None
    h = {"User-Agent": UA}
    h.update(headers)
    if data is not None and not any(k.lower() == "content-type" for k in h):
        h["Content-Type"] = "application/json"
    return urllib.request.Request(b["url"], data=data, headers=h,
                                  method=b["method"])


def send(block, timeout: float = HOOK_TIMEOUT, opener=None) -> dict:
    """发一次钩子请求；成功（2xx）返回 `{status,text,elapsed,url,method}`

    失败抛 `HookError`（kind ∈ 四类）。`opener` 可注入（测试传假 opener，
    ARCH §10 注入点）；默认按 **URL 自身是否回环** 决定代理（PRD B5）。
    """
    b = normalize_block(block)
    req = build_request(b)
    op = opener
    if op is None:
        from app import api_base as apib
        op = apib.opener_for_url(b["url"])
    t0 = time.monotonic()
    try:
        with op.open(req, timeout=float(timeout)) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", "replace")
            return {"status": int(getattr(resp, "status", 0) or 0),
                    "text": text, "elapsed": time.monotonic() - t0,
                    "url": b["url"], "method": b["method"]}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:BODY_PREVIEW]
        except Exception:               # noqa: BLE001 - 读失败也要报原状态码
            body = ""
        raise HookError(KIND_HTTP, f"HTTP {e.code}", status=int(e.code or 0),
                        body=body, url=b["url"]) from e
    except urllib.error.URLError as e:
        kind = classify(e)
        raise HookError(kind, str(getattr(e, "reason", e))[:BODY_PREVIEW],
                        url=b["url"]) from e
    except (socket.timeout, TimeoutError) as e:
        raise HookError(KIND_TIMEOUT, "接口 30 秒没有响应", url=b["url"]) from e
    except OSError as e:
        kind = classify(e)
        raise HookError(kind, str(e)[:BODY_PREVIEW], url=b["url"]) from e


def classify(err: BaseException) -> str:
    """异常 → kind（`send()` 内部用；外部 catch 到裸异常时也可复用）"""
    kind = getattr(err, "kind", "")
    if kind in KINDS:
        return kind
    if isinstance(err, (socket.timeout, TimeoutError)):
        return KIND_TIMEOUT
    reason = getattr(err, "reason", None)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return KIND_TIMEOUT
    text = str(reason if reason is not None else err).lower()
    if "timed out" in text or "timeout" in text or "10060" in text:
        return KIND_TIMEOUT
    return KIND_NETWORK


# ---------------------------------------------------------------- 文案
def describe(err, *, timeout_note: str = "") -> str:
    """`HookError` → PRD 5.2.3 第 1–4 条的逐字文案

    `timeout_note` 留给调用方补一句上下文（例如「平台控制台里如果已经开机
    成功…」），本函数不拼动态句，保证 i18n 能整句命中词表。
    """
    kind = err if isinstance(err, str) else str(getattr(err, "kind", ""))
    if kind == KIND_URL:
        raw = str(getattr(err, "detail", "") or getattr(err, "url", ""))
        return (f"开机请求的 URL 不合法：`{raw}`。\n"
                "要填完整地址，包含 http:// 或 https://，以及平台要求的路径。")
    if kind == KIND_HTTP:
        code = int(getattr(err, "status", 0) or 0)
        body = str(getattr(err, "body", "") or "")
        return (f"平台接口返回 HTTP `{code}`：`{body[:BODY_PREVIEW]}`。\n"
                "检查：URL 路径是否正确；请求头里的 Token 是否过期；"
                "请求体参数名是否照平台文档写的。")
    if kind == KIND_TIMEOUT:
        return ("平台接口 30 秒没有响应，已放弃本次请求。\n"
                "平台控制台里如果已经开机成功，直接点「测试连接」确认，"
                "不用重复发请求。" + (("\n" + timeout_note) if timeout_note else ""))
    detail = str(getattr(err, "detail", "") or "")
    return (f"请求发不出去：`{detail[:BODY_PREVIEW]}`。\n"
            "如果你的网络要过代理，确认代理是通的；平台域名在境外时可能需要代理。")
