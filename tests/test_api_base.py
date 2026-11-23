# -*- coding: utf-8 -*-
"""远程实例支持（0.8.39）：基址推导 / 认证头 / 连通探测 / 远端取图

用标准库 http.server 起一个假 ComfyUI，验证：
  · 本地实例走 127.0.0.1:{port}，远程走 base_url
  · 四种认证头拼装正确
  · /system_stats 有 system/devices 才算 ComfyUI（端口被别的程序占着不算）
  · /view 下载到本地，同名同尺寸不重复下载
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import api_base as apib

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


print("=== 1. 基址推导 ===")
check("本地默认端口", apib.api_base({"port": 8188}) == "http://127.0.0.1:8188")
check("本地自定义端口", apib.api_base({"kind": "local", "port": 8199})
      == "http://127.0.0.1:8199")
check("远程补 http://", apib.api_base(
    {"kind": "remote", "base_url": "192.168.1.20:8188"})
      == "http://192.168.1.20:8188")
check("远程保留 https 并去尾斜杠", apib.api_base(
    {"kind": "remote", "base_url": "https://x.platform.com/"})
      == "https://x.platform.com")
check("远程未填地址 → 空串", apib.api_base({"kind": "remote", "base_url": ""}) == "")
check("隧道场景（远程指向 127.0.0.1）", apib.api_base(
    {"kind": "remote", "base_url": "127.0.0.1:8189"})
      == "http://127.0.0.1:8189")

print("=== 2. 认证头 ===")
check("none 无头", apib.auth_headers({"auth_type": "none"}) == {})
check("bearer", apib.auth_headers(
    {"auth_type": "bearer", "auth_value": "abc"})
      == {"Authorization": "Bearer abc"})
_u = base64.b64encode(b"u:p").decode()
check("basic", apib.auth_headers(
    {"auth_type": "basic", "auth_user": "u", "auth_pass": "p"})
      == {"Authorization": "Basic " + _u})
check("自定义头", apib.auth_headers(
    {"auth_type": "header", "auth_header_name": "X-API-KEY", "auth_value": "k1"})
      == {"X-API-KEY": "k1"})
check("凭证为空不发送空头", apib.auth_headers({"auth_type": "bearer"}) == {})
check("headername 缺省回落 X-API-KEY", apib.auth_headers(
    {"auth_type": "header", "auth_value": "v"}) == {"X-API-KEY": "v"})
check("headers() 带 UA", apib.headers({}).get("User-Agent") == "ComfyHub")

print("=== 3. 远端产物目录 ===")
d = apib.remote_output_dir({"name": "云机 A/B"}, {"settings": {}})
check("非法字符被剔除", "云机 AB" in d and "/" not in os.path.basename(d), d)
d2 = apib.remote_output_dir({"name": "x"}, {"settings": {"remote_output_dir": "D:/tmp/out"}})
check("配置可覆盖根目录", d2.replace("\\", "/").startswith("D:/tmp/out"), d2)


# ---------------------------------------------------------------- 假 ComfyUI
class _H(BaseHTTPRequestHandler):
    seen_auth = []
    mode = "comfy"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _H.seen_auth.append(self.headers.get("Authorization") or
                            self.headers.get("X-API-KEY") or "")
        if self.path.startswith("/system_stats"):
            if _H.mode == "notcomfy":
                return self._json({"hello": "world"})
            return self._json({"system": {"comfyui_version": "0.3"},
                               "devices": [{"name": "fake"}]})
        if self.path.startswith("/view"):
            body = b"\x89PNG-FAKE-DATA"
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "not found"}, 404)


srv = HTTPServer(("127.0.0.1", 0), _H)
_port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

print("=== 4. 连通探测 ===")
inst = {"kind": "remote", "base_url": "127.0.0.1:%d" % _port}
check("真 ComfyUI → 就绪", apib.probe(inst, timeout=3) is True)
ok, why = apib.probe_reason(inst, timeout=3)
check("probe_reason 成功无原因", ok and not why, why)
_H.mode = "notcomfy"
check("非 ComfyUI 响应 → 不算就绪", apib.probe(inst, timeout=3) is False)
ok2, why2 = apib.probe_reason(inst, timeout=3)
check("probe_reason 说明不是 ComfyUI", (not ok2) and "ComfyUI" in why2, why2)
_H.mode = "comfy"
check("死端口 → 不就绪", apib.probe(
    {"kind": "remote", "base_url": "127.0.0.1:1"}, timeout=1) is False)
check("未填地址 → 不就绪", apib.probe({"kind": "remote"}, timeout=1) is False)

print("=== 5. 认证真的被带上 ===")
_H.seen_auth.clear()
apib.probe({"kind": "remote", "base_url": "127.0.0.1:%d" % _port,
            "auth_type": "bearer", "auth_value": "tok123"}, timeout=3)
check("服务端收到 Bearer", "Bearer tok123" in _H.seen_auth, str(_H.seen_auth[-1:]))

print("=== 6. /view 取图 ===")
tmp = tempfile.mkdtemp(prefix="comfyhub_view_")
_vi = {"name": "fake", "kind": "remote", "base_url": "127.0.0.1:%d" % _port}
try:
    p1 = apib.fetch_view(_vi, "a.png", "", "output", dest_dir=tmp)
    check("下载到本地", os.path.isfile(p1), p1)
    check("内容正确", open(p1, "rb").read() == b"\x89PNG-FAKE-DATA")
    m1 = os.path.getmtime(p1)
    p2 = apib.fetch_view(_vi, "a.png", "", "output", dest_dir=tmp)
    check("同名同尺寸复用不重复下载", p2 == p1 and os.path.getmtime(p2) == m1)
    check("没有残留 .part 文件",
          not any(x.endswith(".part") for x in os.listdir(tmp)))
    check("空文件名报错", _raises(lambda: apib.fetch_view(
        _vi, "", "", "output", dest_dir=tmp)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

srv.shutdown()

print()
bad = [n for n, ok, _ in res if not ok]
print(f"结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
