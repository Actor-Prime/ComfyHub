# -*- coding: utf-8 -*-
"""ComfyHub 反馈中转（腾讯云函数 SCF · 函数URL 触发 · Python 3.10 · 纯标准库）

为什么需要这一层：
  金数据/金山文档/问卷星的 API 都要求密钥，而桌面客户端里放任何密钥都等于公开。
  这个函数是**唯一持有密钥的地方**，客户端只 POST 一个无密钥的公开地址。

环境变量（SCF 控制台 → 函数配置 → 环境变量）：
  JSJ_FORM_TOKEN   金数据表单 token（表单地址里 /f/ 后面那串）
  JSJ_TOKEN       金数据「个人 Access Token」（Bearer，勿泄露）
  JSJ_FIELD_SUMMARY / CONTACT / VERSION / REPORT
                   字段 API code 映射，默认 field_1..field_4（见 README 的表单建法）
  WECOM_WEBHOOK    选填：企业微信群机器人 webhook，配了就顺手推一条手机通知

安全：函数 URL 是公开的，任何人都能 POST。这里做了
  ① 请求体 ≤300KB ② report 长度下限 ③ 单 IP 5 次/分钟 ④ 全实例 200 次/小时
  （实例级近似限流，个人项目够用；要严格请挂 API 网关限流）
"""
import base64
import json
import os
import time
import urllib.request

JSJ_FORM_TOKEN = os.environ.get("JSJ_FORM_TOKEN", "")
JSJ_TOKEN = os.environ.get("JSJ_TOKEN", "")
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")

FIELD = {
    "summary": os.environ.get("JSJ_FIELD_SUMMARY", "field_1"),
    "contact": os.environ.get("JSJ_FIELD_CONTACT", "field_2"),
    "version": os.environ.get("JSJ_FIELD_VERSION", "field_3"),
    "report": os.environ.get("JSJ_FIELD_REPORT", "field_4"),
}

MAX_BODY = 300 * 1024
MIN_REPORT = 20
_HITS = {}          # ip -> [ts, ...]（实例内近似限流）
_GLOBAL = []        # 实例内全局调用时间戳


def _ip(event):
    h = event.get("headers") or {}
    for k in ("x-forwarded-for", "X-Forwarded-For", "x-real-ip", "X-Real-IP"):
        if h.get(k):
            return str(h[k]).split(",")[0].strip()
    return "unknown"


def _rate_ok(ip):
    now = time.time()
    hits = [t for t in _HITS.get(ip, []) if now - t < 60]
    if len(hits) >= 5:
        return False, "too many requests"
    hits.append(now)
    _HITS[ip] = hits
    glob = [t for t in _GLOBAL if now - t < 3600]
    if len(glob) >= 200:
        return False, "daily-ish quota reached"
    glob.append(now)
    _GLOBAL[:] = glob
    return True, ""


def _post_json(url, payload, headers, timeout=10):
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, (r.read(300) or b"").decode("utf-8", "replace")


def _reply(code, obj):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(obj, ensure_ascii=False),
        "isBase64Encoded": False,
    }


def main_handler(event, context):
    # 健康检查 / 浏览器误访问
    if (event.get("httpMethod") or "POST").upper() == "GET":
        return _reply(200, {"ok": True, "service": "comfyhub-feedback-relay"})

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw.encode("utf-8")) > MAX_BODY:
        return _reply(413, {"ok": False, "error": "body too large"})

    try:
        data = json.loads(raw)
    except Exception:
        return _reply(400, {"ok": False, "error": "invalid json"})

    report = (data.get("report") or "").strip()
    if len(report) < MIN_REPORT:
        return _reply(400, {"ok": False, "error": "report too short"})

    ip = _ip(event)
    ok, why = _rate_ok(ip)
    if not ok:
        return _reply(429, {"ok": False, "error": why})

    version = str(data.get("version") or "")[:40]
    contact = str(data.get("contact") or "")[:120]
    summary = (str(data.get("summary") or "").strip()
               or report.splitlines()[0][:80])

    if not (JSJ_FORM_TOKEN and JSJ_TOKEN):
        return _reply(500, {"ok": False, "error": "server not configured"})

    entry = {
        FIELD["summary"]: summary,
        FIELD["contact"]: contact,
        FIELD["version"]: version,
        FIELD["report"]: report[:MAX_BODY],
    }
    try:
        st, body = _post_json(
            f"https://jinshuju.net/api/v1/forms/{JSJ_FORM_TOKEN}/entries",
            entry,
            {"Authorization": f"Bearer {JSJ_TOKEN}",
             "Content-Type": "application/json",
             "Accept": "application/json"})
    except Exception as e:
        return _reply(502, {"ok": False, "error": f"jinshuju: {type(e).__name__}: {e}"})
    if not (200 <= st < 300):
        return _reply(502, {"ok": False, "error": f"jinshuju {st}: {body[:120]}"})

    # 顺手推手机通知（可选，失败不影响主流程——数据已经进表单了）
    if WECOM_WEBHOOK:
        try:
            _post_json(WECOM_WEBHOOK, {
                "msgtype": "text",
                "text": {"content": f"ComfyHub 新反馈 v{version}\n{summary[:120]}"},
            }, {"Content-Type": "application/json"})
        except Exception:
            pass

    return _reply(200, {"ok": True})
