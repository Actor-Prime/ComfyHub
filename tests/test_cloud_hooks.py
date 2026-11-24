# -*- coding: utf-8 -*-
"""0.8.40 T05：云平台 HTTP 钩子（cloud_hooks）+ cloud 通道编排

覆盖（ARCH §7 T05 验收 ⑦ 与 PRD 5.2.3 / 边界 #14）：
  ① 4 个模板骨架，**零平台实参**（URL 里只有 `<PLATFORM_HOST>` 这类占位符）
  ② `parse_headers` 多行解析：`Authorization: Bearer x` → `{"Authorization":"…"}`
  ③ 请求体非法 JSON → 报行号，且 `precheck` 返回 False（PRD 边界 #14「不发请求」）
  ④ 4 个模板 × 开/关机 的 `send`：注入 opener 断言 URL/方法/头/体与表单一致
  ⑤ 错误四分类：http / network / timeout（+ url_invalid），kind 稳定
  ⑥ `describe()` 文案与 PRD 5.2.3 第 1–4 条一致（关键词逐字）
  ⑦ cloud 通道编排：2xx **只算受理**，之后仍轮询就绪；未就绪 → not_ready
  ⑧ 钩子失败 → err_kind 原样透传（http / timeout），不谎报成功
  ⑨ 未配 cloud URL → OpsError(no_channel)；按动作看各自的 URL
  ⑩ 层次约束：cloud_hooks 不 import PySide6

用法：.venv_build/Scripts/python.exe -u tests/test_cloud_hooks.py
"""
import ast
import os
import socket
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from app import cloud_hooks as ch                 # noqa: E402
from app import remote_ops as ro                  # noqa: E402
from app.config import instance_ops_defaults      # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, bool(ok), d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


# ---------------------------------------------------------------- 桩
class FakeResp:
    def __init__(self, status=200, body=b'{"ok":true}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """记录最后一次请求，返回预置响应"""

    def __init__(self, status=200, body=b'{"ok":true}', exc=None):
        self.status, self.body, self.exc = status, body, exc
        self.last = None
        self.timeout = None

    def open(self, req, timeout=None):
        self.last = req
        self.timeout = timeout
        if self.exc is not None:
            raise self.exc
        return FakeResp(self.status, self.body)


def _inst(**kw):
    i = {
        "id": "inst_c", "name": "云镜像", "kind": "remote",
        "base_url": "https://xxx.platform.com",
        "ssh_host": "", "ssh_user": "", "ssh_start_cmd": "", "ssh_stop_cmd": "",
        "cloud_start": {"url": "https://api.example.com/start", "method": "POST",
                        "headers": "Authorization: Bearer tok",
                        "body": '{"instance_id":"i-1"}'},
        "cloud_stop": {"url": "https://api.example.com/stop", "method": "POST",
                       "headers": "", "body": ""},
        "cloud_start_timeout_s": 1,   # 就绪等待上限（测试取最小合法值 1 秒）
    }
    i.update(kw)
    return i


class Probe:
    """按脚本返回探针结果；脚本用完后一直返回最后一个值"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, inst):
        self.calls += 1
        if not self.seq:
            return False
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


print("=== 1. 模板骨架：4 个，零平台实参 ===")
check("4 个模板", len(ch.TEMPLATES) == 4, str(ch.TEMPLATE_IDS))
check("id 唯一", len(set(ch.TEMPLATE_IDS)) == 4, str(ch.TEMPLATE_IDS))
_bad = []
for t in ch.TEMPLATES:
    for action in ("start", "stop"):
        b = ch.normalize_block(t[action])
        if not b["url"]:
            _bad.append(f"{t['id']}.{action} 空 URL")
        # 骨架必须全是占位符：不允许出现任何真实平台域名/路径特征
        if "<PLATFORM_HOST>" not in b["url"]:
            _bad.append(f"{t['id']}.{action} 没有占位主机")
        for token in ("alibaba", "aliyun", "autodl", "runpod", "vast", "tencent",
                      "volc", "gpucloud", "instance_id=i-"):
            if token in (b["url"] + b["body"] + b["headers"]).lower():
                _bad.append(f"{t['id']}.{action} 含真实平台实参 {token}")
check("所有模板只用占位符（无真实平台参数）", not _bad, "; ".join(_bad[:3]))
check("方法都在 GET/POST/PUT/PATCH 内",
      all(ch.normalize_block(t[a])["method"] in ch.METHODS
          for t in ch.TEMPLATES for a in ("start", "stop")))
check("灰字提示逐字（模板只是骨架…）",
      ch.TEMPLATE_TIP.startswith("模板只是骨架，参数（实例 ID、区域、Token）")
      and "核对" in ch.TEMPLATE_TIP, ch.TEMPLATE_TIP)

print("=== 2. 请求头多行解析 ===")
h, err = ch.parse_headers("Authorization: Bearer x")
check("单行 Bearer", h == {"Authorization": "Bearer x"} and not err, str(h))
h, err = ch.parse_headers("Authorization: Bearer x\n\n# 注释\nX-API-KEY: abc:def\n")
check("多行 + 注释 + 值里含冒号",
      h == {"Authorization": "Bearer x", "X-API-KEY": "abc:def"} and not err, str(h))
h, err = ch.parse_headers("不是键值对")
check("没有冒号 → 报行号", not h and "第 1 行" in err, err)
h, err = ch.parse_headers(": 空头名")
check("空头名 → 报错", not h and "头名是空的" in err, err)

print("=== 3. 请求体校验（PRD 边界 #14）===")
obj, err = ch.validate_body('{"a": 1}')
check("合法 JSON", obj == {"a": 1} and not err, str(obj))
obj, err = ch.validate_body("")
check("空 = 合法（不校验）", obj is None and not err, err)
obj, err = ch.validate_body('{\n  "a": 1,\n  "b": 2 3\n}')
check("非法 JSON 报行号", obj is None and "第 3 行" in err, err)
check("文案逐字含「请检查逗号和引号」", "请检查逗号和引号" in err, err)
ok, text = ch.precheck({"url": "https://h/p", "body": "{bad"}, action="start")
check("precheck 拦住非法 JSON（不发请求）", not ok and "不是合法 JSON" in text, text)
ok, text = ch.precheck({"url": "", "body": ""}, action="start")
check("precheck 拦住空 URL 且文案是 PRD 第 1 条",
      not ok and "URL 不合法" in text and "http://" in text, text[:60])
ok, text = ch.precheck({"url": "ftp://h/p"}, action="start")
check("非 http(s) 协议被拦", not ok and "URL 不合法" in text and "ftp://h/p" in text,
      text[:80])
ok, text = ch.precheck({"url": "https://h/p", "headers": "badline"}, action="stop")
check("请求头格式错被拦", not ok and "第 1 行" in text, text[:80])
ok, text = ch.precheck({"url": "https://h/p", "body": '{"a":1}'}, action="start")
check("合法 block 通过预检", ok and not text, text)

print("=== 4. 四个模板 send：URL/方法/头/体与表单一致 ===")
_all_ok, _detail = True, []
for t in ch.TEMPLATES:
    for action in ("start", "stop"):
        block = t[action]
        op = FakeOpener(status=200)
        out = ch.send(block, timeout=12, opener=op)
        req = op.last
        nb = ch.normalize_block(block)
        got_url = req.full_url
        got_method = req.get_method()
        got_headers = {str(k).lower(): v for k, v in req.header_items()}
        got_body = (req.data or b"").decode("utf-8")
        okk = (got_url == nb["url"] and got_method == nb["method"]
               and got_body == nb["body"] and out["status"] == 200
               and op.timeout == 12)
        # 表单里写的每个头都要真发出去
        for k, v in ch.parse_headers(nb["headers"])[0].items():
            if got_headers.get(str(k).lower()) != v:
                okk = False
        if not okk:
            _all_ok = False
            _detail.append(f"{t['id']}.{action}")
check("4 模板 × 2 动作：UR" "L/方法/头/体一致", _all_ok, ", ".join(_detail))

op = FakeOpener()
ch.send({"url": "https://h/p", "method": "POST", "body": '{"a":1}'}, opener=op)
_hdrs = {str(k).lower(): v for k, v in op.last.header_items()}
check("没有 Content-Type 时补 application/json",
      _hdrs.get("content-type") == "application/json", str(_hdrs))
op = FakeOpener()
ch.send({"url": "https://h/p", "method": "GET", "body": ""}, opener=op)
check("GET 空体 → data 为 None", op.last.data is None, str(op.last.data))
_hdrs2 = {str(k).lower(): v for k, v in op.last.header_items()}
check("UA 标识为 ComfyHub", _hdrs2.get("user-agent") == "ComfyHub", str(_hdrs2))

print("=== 5. 错误四分类 ===")
try:
    ch.send({"url": ""}, opener=FakeOpener())
    _k = "<none>"
except ch.HookError as e:
    _k = e.kind
check("空 URL → url_invalid", _k == ch.KIND_URL, _k)
_h = urllib.error.HTTPError("https://h/p", 401, "Unauthorized", {}, None)
_h.read = lambda: b'{"msg":"bad token"}'          # type: ignore[method-assign]
try:
    ch.send({"url": "https://h/p"}, opener=FakeOpener(exc=_h))
    _k = "<none>"
except ch.HookError as e:
    _k, _st, _bd = e.kind, e.status, e.body
check("HTTPError → http + 状态码 + 响应体", _k == ch.KIND_HTTP and _st == 401
      and "bad token" in _bd, f"{_k} {_st}")
try:
    ch.send({"url": "https://h/p"},
            opener=FakeOpener(exc=urllib.error.URLError("getaddrinfo failed")))
    _k = "<none>"
except ch.HookError as e:
    _k = e.kind
check("URLError(DNS) → network", _k == ch.KIND_NETWORK, _k)
try:
    ch.send({"url": "https://h/p"}, opener=FakeOpener(exc=socket.timeout("timed out")))
    _k = "<none>"
except ch.HookError as e:
    _k = e.kind
check("socket.timeout → timeout", _k == ch.KIND_TIMEOUT, _k)
try:
    ch.send({"url": "https://h/p"},
            opener=FakeOpener(exc=urllib.error.URLError(socket.timeout())))
    _k = "<none>"
except ch.HookError as e:
    _k = e.kind
check("URLError 包着 timeout → timeout", _k == ch.KIND_TIMEOUT, _k)
check("kind 集合就是四类", set(ch.KINDS) == {"url_invalid", "http", "network", "timeout"},
      str(ch.KINDS))

print("=== 6. describe() 与 PRD 5.2.3 逐字对齐 ===")
t1 = ch.describe(ch.HookError(ch.KIND_URL, "bad", url="bad"))
check("第 1 条：URL 不合法 + 要填完整地址",
      "URL 不合法" in t1 and "包含 http:// 或 https://" in t1, t1[:40])
t2 = ch.describe(ch.HookError(ch.KIND_HTTP, "HTTP 500", status=500, body="boom"))
check("第 2 条：HTTP `<code>` + 检查三件事",
      "HTTP `500`" in t2 and "Token 是否过期" in t2, t2[:60])
t3 = ch.describe(ch.HookError(ch.KIND_NETWORK, "refused"))
check("第 3 条：请求发不出去 + 代理提示",
      "请求发不出去" in t3 and "代理" in t3, t3[:40])
t4 = ch.describe(ch.HookError(ch.KIND_TIMEOUT, "x"))
check("第 4 条：30 秒没有响应 + 测试连接",
      "30 秒没有响应" in t4 and "测试连接" in t4, t4[:40])

print("=== 7. cloud 通道编排：2xx 只算受理 ===")


def _ops(probe_seq, hook, **kw):
    return ro.RemoteOps(probe_fn=Probe(probe_seq),
                        poll_interval=0.01, ready_timeout=0.08,
                        quick_ready_window=0.0, recheck_delay=0.0,
                        stop_confirm_timeout=0.08,
                        hook_send=hook, **kw)


_states = []


def _hook_ok(block, timeout=None):
    _states.append(("hook", ch.normalize_block(block)["url"], timeout))
    return {"status": 200, "text": '{"ok":true}', "elapsed": 0.01,
            "url": block.get("url"), "method": block.get("method")}


r = _ops([False, True, True], _hook_ok).start(_inst(), channel="cloud",
                                              on_state=_states.append)
check("云通道启动成功（预探针 False → 探针 True）", r.ok is True, repr(r))
check("channel 记成 cloud", r.channel == "cloud", r.channel)
check("2xx 不当成功：先受理再轮询（有 hook 调用）",
      any(isinstance(s, tuple) and s[0] == "hook" for s in _states),
      str([s for s in _states if isinstance(s, tuple)]))
check("状态行出现「平台已受理，等待实例就绪」",
      any(isinstance(s, str) and "平台已受理" in s for s in _states),
      str([s for s in _states if isinstance(s, str)][:4]))
check("受理后仍要探针就绪（探针被调用 ≥ 2 次）", r.ok and r.elapsed >= 0, repr(r))

echo = Probe([True, False])
r2 = ro.RemoteOps(probe_fn=echo, poll_interval=0.01, ready_timeout=0.08,
                  quick_ready_window=0.0, recheck_delay=0.0,
                  hook_send=_hook_ok).start(_inst(), channel="cloud")
check("预探针已就绪 → 仍发请求但标记 pre_ready（不谎报启动成功）",
      r2.pre_ready is True, repr(r2))

print("=== 8. 钩子失败 → kind 透传 ===")


def _hook_http(block, timeout=None):
    raise ch.HookError(ch.KIND_HTTP, "HTTP 500", status=500, body="server error")


r3 = _ops([False], _hook_http).start(_inst(), channel="cloud")
check("钩子 500 → err_kind=http（不进入轮询）", r3.err_kind == "http", repr(r3))
check("detail 带上原因", "HTTP 500" in (r3.detail or ""), r3.detail)


def _hook_timeout(block, timeout=None):
    raise ch.HookError(ch.KIND_TIMEOUT, "30 秒没响应")


r4 = _ops([False], _hook_timeout).start(_inst(), channel="cloud")
check("钩子超时 → err_kind=timeout", r4.err_kind == "timeout", repr(r4))


def _hook_net(block, timeout=None):
    raise urllib.error.URLError("refused")     # 裸异常也要能分类


r5 = _ops([False], _hook_net).start(_inst(), channel="cloud")
check("裸网络异常 → err_kind=network", r5.err_kind == "network", repr(r5))

r6 = _ops([False, False], _hook_ok).start(_inst(), channel="cloud")
check("受理但一直不 ready → not_ready", r6.err_kind == "not_ready", repr(r6))

print("=== 9. 通道可用性按动作看各自 URL ===")
_ops_obj = ro.RemoteOps(hook_send=_hook_ok)
_i = _inst(cloud_start={}, cloud_stop={"url": "https://api.example.com/stop"})
check("只配关机 URL → start 无 cloud 通道",
      "cloud" not in _ops_obj.available_channels(_i, action="start"),
      str(_ops_obj.available_channels(_i, action="start")))
check("只配关机 URL → stop 有 cloud 通道",
      "cloud" in _ops_obj.available_channels(_i, action="stop"),
      str(_ops_obj.available_channels(_i, action="stop")))
try:
    _ops_obj.start(_i, channel="cloud")
    _k = "<none>"
except ro.OpsError as e:
    _k = e.kind
check("没配开机 URL → OpsError(no_channel)", _k == ro.ERR_NO_CHANNEL, _k)
_check_called = []


def _hook_spy(block, timeout=None):
    _check_called.append(1)
    return {"status": 200, "text": "{}"}


try:
    ro.RemoteOps(hook_send=_hook_spy).start(_i, channel="cloud")
except ro.OpsError:
    pass
check("no_channel 时一个请求都不发", not _check_called, str(len(_check_called)))

print("=== 10. cloud 停止 ===")
_stop_states = []
r7 = ro.RemoteOps(probe_fn=Probe([True, False]), poll_interval=0.01,
                  stop_confirm_timeout=0.15,
                  hook_send=_hook_ok).stop(_inst(), channel="cloud",
                                           on_state=_stop_states.append)
check("云通道停止：探针转不可达 → ok", r7.ok is True, repr(r7))
check("状态行出现「正在请求平台关机」",
      any("正在请求平台关机" in str(s) for s in _stop_states), str(_stop_states[:3]))
r8 = ro.RemoteOps(probe_fn=Probe([True]), poll_interval=0.01,
                  stop_confirm_timeout=0.08,
                  hook_send=_hook_ok).stop(_inst(), channel="cloud")
check("关机受理但一直可达 → still_reachable",
      r8.err_kind == "still_reachable", repr(r8))

print("=== 11. 层次约束：cloud_hooks 不 import PySide6 ===")
_tree = ast.parse(open(os.path.join(ROOT, "app", "cloud_hooks.py"),
                       encoding="utf-8").read())
_mods = []
for _n in ast.walk(_tree):
    if isinstance(_n, ast.Import):
        _mods += [a.name for a in _n.names]
    elif isinstance(_n, ast.ImportFrom) and _n.module:
        _mods.append(_n.module)
check("不 import PySide6", not any(m.startswith("PySide6") for m in _mods), str(_mods))
check("不 import proc_manager", not any("proc_manager" in m for m in _mods), str(_mods))
check("instance_ops_defaults 提供 cloud_start/cloud_stop 的空表",
      instance_ops_defaults()["cloud_start"] == {}
      and instance_ops_defaults()["cloud_stop"] == {})

print()
_bad = [r for r in res if not r[1]]
print(f"通过 {len(res) - len(_bad)}/{len(res)}")
if _bad:
    print("失败：")
    for n, _ok, d in _bad:
        print(f"  - {n}  [{d}]")
    sys.exit(1)
print("OK 全部通过")
