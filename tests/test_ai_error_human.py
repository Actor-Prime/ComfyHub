# -*- coding: utf-8 -*-
"""AI 报错「人话化」回归（2026-11-12 用户反馈：看不懂 HTTPError 401 是什么）

断言：
  A. describe_error 把 4xx/5xx 翻成带动作建议的中文，不出现异常类名
  B. 服务端原文只在有信息量时附上（401/403 不附）
  C. 网络类（超时 / 拒绝 / DNS / 证书）各自有对应文案
  D. one_line=True 去换行（设置页状态栏只能显示一行）
  E. 英文模式下走英文词表
  F. open_request 把 HTTPError 包成 ApiError（不再裸抛 urllib 异常）
  G. ai_agent 的错误事件：text 是人话、raw 保留原始异常

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ai_error_human.py
"""
import io
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


from app import ai_assist as A  # noqa: E402
from app import i18n  # noqa: E402

# ============================================================ A. 4xx / 5xx
print("[A] HTTP 状态码 → 人话")
for code, must in ((401, "认证失败"), (403, "拒绝访问"), (404, "地址不对"),
                   (429, "限流"), (402, "余额"), (400, "请求被拒绝")):
    msg = A.describe_error(A.ApiError(code, "boom"))
    check(f"{code} 命中「{must}」", must in msg, msg[:60])
    check(f"{code} 不暴露异常类名", "HTTPError" not in msg and "ApiError" not in msg,
          msg[:60])
    check(f"{code} 带排查动作", "系统设置" in msg or "控制台" in msg or "重试" in msg
          or "测试接口" in msg or "换" in msg or "续订" in msg, msg[:80])

m401 = A.describe_error(A.ApiError(401, "Unauthorized"))
check("401 点明套餐/按量 Key 不能混用", "套餐 Key" in m401, m401[:80])
check("401 不给无意义的服务端原文", "Unauthorized" not in m401, m401[-60:])

m500 = A.describe_error(A.ApiError(500, "internal boom"))
check("5xx 说是厂商侧问题", "服务端出错" in m500 and "厂商" in m500, m500[:60])

m404 = A.describe_error(A.ApiError(404, "model not found"))
check("404 附服务端原文", "model not found" in m404, m404[-60:])

# ============================================================ B/C. 网络层
print("[B] 网络类异常")
check("超时 → 请求超时",
      A.describe_error(TimeoutError("timed out")).startswith("请求超时"),
      A.describe_error(TimeoutError("timed out"))[:40])
check("超时提示代理/直连",
      "代理" in A.describe_error(TimeoutError("timed out")), "")
check("连接被拒绝",
      "拒绝" in A.describe_error(urllib.error.URLError(
          ConnectionRefusedError(10061, "refused"))), "")
check("DNS 失败 → 连不上服务器",
      "连不上服务器" in A.describe_error(urllib.error.URLError(
          OSError(11001, "getaddrinfo failed"))), "")
check("证书问题有专条",
      "证书" in A.describe_error(ValueError(
          "certificate verify failed (ssl)")), "")

# ============================================================ D. 单行
print("[D] 单行模式")
one = A.describe_error(A.ApiError(401, "x"), one_line=True)
check("单行模式不含换行", "\n" not in one, repr(one[:40]))
check("单行模式限长 200", len(one) <= 200, len(one))

# ============================================================ E. 英文
print("[E] 英文模式")
i18n.set_lang("en")
try:
    en = A.describe_error(A.ApiError(401, "Unauthorized"))
    check("英文模式给英文", en.startswith("Authentication failed"), en[:40])
    check("英文模式无中文残留", "认证" not in en and "套餐" not in en, en[:60])
    en404 = A.describe_error(A.ApiError(404, "nope"))
    check("英文 404 命中", en404.startswith("Wrong endpoint"), en404[:40])
finally:
    i18n.set_lang("zh")

# ============================================================ F. 包装 HTTPError
print("[F] open_request 包装")


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self):
        super().__init__("http://x/y", 401, "Unauthorized", {}, io.BytesIO(
            b'{"error":{"message":"invalid api key"}}'))


def _raise(*a, **k):
    raise _FakeHTTPError()


_old = A.urllib.request.urlopen
A.urllib.request.urlopen = _raise
try:
    try:
        A.open_request(A.make_request("http://x/y", "k"))
        check("HTTPError 被包成 ApiError", False, "没抛异常")
    except A.ApiError as e:
        check("HTTPError 被包成 ApiError", e.status == 401, e.status)
        check("服务端 message 被抠出来", "invalid api key" in e.detail, e.detail)
    except Exception as e:
        check("HTTPError 被包成 ApiError", False, f"{type(e).__name__}: {e}")
finally:
    A.urllib.request.urlopen = _old

# ============================================================ G. agent 事件
print("[G] ai_agent 错误事件")
from app import ai_agent  # noqa: E402

captured = []


def _fake_round(*a, **k):
    raise A.ApiError(401, "Unauthorized")


_old_round = ai_agent._round_chat
ai_agent._round_chat = _fake_round
try:
    cfg = {"settings": {"ai_enabled": True, "ai_base_url": "http://x",
                        "ai_api_key": "k", "ai_model": "m"}}
    ai_agent.run_agent(cfg, {"id": "a", "port": 8188, "comfyui_path": "C:/tmp"},
                       "hi", [], lambda kind, **kw: captured.append((kind, kw)),
                       log=lambda *a: None)
finally:
    ai_agent._round_chat = _old_round

errs = [kw for kind, kw in captured if kind == "error"]
check("有 error 事件", len(errs) == 1, len(errs))
if errs:
    text = errs[0].get("text") or ""
    check("text 是人话", "认证失败" in text, text[:60])
    check("text 不含异常类名", "HTTPError" not in text and "ApiError" not in text,
          text[:60])
    check("raw 保留原始异常", "401" in (errs[0].get("raw") or ""),
          errs[0].get("raw"))

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
