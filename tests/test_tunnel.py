# -*- coding: utf-8 -*-
"""0.8.40 T04：隧道 + 运行期基址覆盖（本版最高风险）

覆盖（ARCH §7 T04 验收 ①-⑧）：
  ① `set_override` 后 `api_base(inst)` 返回回环、`saved_api_base` 返回原值、
     `inst["base_url"]` 一字未改
  ② `clear_override` 后 `api_base` **立即**回落
  ③ **FAILED 分支**：5 次重连用尽后 `registry.is_active()==False`，且本地端口
     可以重新 bind（没有「本地端口 listen 着、UI 显示运行中、实际打不通」的假活）
  ④ 端口占用预检：先占住 8189 → start 返回失败文案，不注册 override
  ⑤ 断线注入 → 状态序列 CONNECTED → RECONNECTING(1..5) → FAILED
  ⑥ 退避序列实测为 [1,2,4,8,16]
  ⑦ `ToolCtx.api` / `TaskWatcher.base` 在 set_override 后现算为回环地址
  ⑧ 注册表查表抛异常时 `api_base` 回落（绝不能把全仓 HTTP 带崩）

用法：.venv_build/Scripts/python.exe -u tests/test_tunnel.py
"""
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(ROOT, "build_tmp_tunnel_test"))

import paramiko                                  # noqa: E402

from app import api_base as apib                 # noqa: E402
from app import remote_registry as rr            # noqa: E402
from app import tunnel                           # noqa: E402
from fake_ssh import FakeFactory, SequenceFactory, make_inst  # noqa: E402

res = []
REG = rr.registry


def check(n, ok, d=""):
    res.append((n, bool(ok), d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def wait_for(fn, timeout=5.0, step=0.02) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(step)
    return False


def can_bind(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


INST = make_inst(id="inst_tun", name="隧道实例", tunnel_local_port=19189)


def job(inst=None, **kw):
    kw.setdefault("sleep_fn", lambda _s: None)     # 退避不真等
    kw.setdefault("probe_interval", 0.01)
    kw.setdefault("local_port", 0)                 # 让系统分配，避免撞端口
    return tunnel.TunnelJob(inst or INST, **kw)


print("=== 1. 注册表语义：覆盖 / 回落 / base_url 不变 ===")
REG.reset()
_base_saved = INST["base_url"]
REG.set_override(INST["id"], "http://127.0.0.1:19189", 19189)
check("api_base 走回环", apib.api_base(INST) == "http://127.0.0.1:19189",
      apib.api_base(INST))
check("saved_api_base 走原值",
      apib.saved_api_base(INST) == "http://192.168.1.20:8188", apib.saved_api_base(INST))
check("inst['base_url'] 没被改写", INST["base_url"] == _base_saved, INST["base_url"])
_info = apib.effective_base_info(INST)
check("effective_base_info 标出 via_tunnel", _info["via_tunnel"] is True, str(_info))
check("隧道回环 → 不算明文告警", _info["is_insecure"] is False, str(_info))
check("隧道回环 → is_loopback=True", _info["is_loopback"] is True)
check("registry.is_active 认得出",
      REG.is_active(INST["id"]) is True and len(REG) == 1)

REG.clear_override(INST["id"])
check("clear 后**立即**回落 base_url",
      apib.api_base(INST) == "http://192.168.1.20:8188", apib.api_base(INST))
check("clear 后 via_tunnel=False", apib.effective_base_info(INST)["via_tunnel"] is False)
check("is_active 变 False", REG.is_active(INST["id"]) is False)

print("=== 2. 注册表异常绝不能把 api_base 带崩（致命风险）===")
_orig_override = rr.RemoteRegistry.override_for


def _boom(self, inst):
    raise RuntimeError("registry 内部炸了")


rr.RemoteRegistry.override_for = _boom
try:
    _got = apib.api_base(INST)
    _got_local = apib.api_base({"kind": "local", "port": 8199})
    _ok = True
    _err = ""
except Exception as e:                           # noqa: BLE001
    _ok, _got, _got_local, _err = False, "", "", f"{type(e).__name__}: {e}"
finally:
    rr.RemoteRegistry.override_for = _orig_override
check("registry 抛异常时 api_base 回落原值", _ok and _got == "http://192.168.1.20:8188",
      f"{_got} {_err}")
check("本机实例照样正常", _got_local == "http://127.0.0.1:8199", _got_local)

print("=== 3. 端口占用预检：不建隧道、不注册覆盖 ===")
_busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_busy.bind(("127.0.0.1", 0))
_busy.listen(4)
_busy_port = _busy.getsockname()[1]
try:
    _inst_busy = make_inst(id="inst_busy", tunnel_local_port=_busy_port)
    _j = tunnel.TunnelJob(_inst_busy, transport_factory=FakeFactory(),
                          sleep_fn=lambda _s: None)
    _ok, _text = _j.start()
    check("端口被占用 → start 返回失败", _ok is False)
    check("文案含「已被占用」", "已被占用" in _text, _text.replace("\n", " ⏎ "))
    check("文案给出建议端口", str(_busy_port + 1) in _text, _text.replace("\n", " ⏎ "))
    check("没注册 override", REG.is_active("inst_busy") is False)
    check("state=FAILED", _j.state == tunnel.ST_FAILED, _j.state)
finally:
    _busy.close()

print("=== 4. 正常建立：CONNECTED + override + 双向转发 ===")
REG.reset()
_fac = FakeFactory()                             # 默认 echo 通道
_j = job(transport_factory=_fac)
_states = []
_j._on_state_cb = lambda s, t: _states.append(s)
_ok, _text = _j.start()
check("start() 立即返回 True", _ok is True)
_landed = wait_for(lambda: _j.state == tunnel.ST_CONNECTED, 3.0)
check("进入 CONNECTED", _landed, _j.state)
_port = _j.local_port
check("拿到本地端口", _port > 0, str(_port))
check("状态文案是 PRD 原文格式",
      _text_or := _j.state_text, _text_or)
check("文案含箭头与本地/远端",
      "隧道已连接" in _j.state_text and "→" in _j.state_text
      and "127.0.0.1:%d" % _port in _j.state_text, _j.state_text)
check("override 指向回环端口",
      apib.api_base(INST) == "http://127.0.0.1:%d" % _port, apib.api_base(INST))
check("base_url 仍未改写", INST["base_url"] == _base_saved)
check("远端目标来自实例配置",
      (_j.remote_host, _j.remote_port) == ("127.0.0.1", 8188))

# 真的穿过去一次（验证双向 relay）
_got = b""
try:
    _c = socket.create_connection(("127.0.0.1", _port), timeout=3.0)
    _c.sendall(b"hello-tunnel")
    _c.settimeout(3.0)
    for _ in range(20):
        _chunk = _c.recv(4096)
        if not _chunk:
            break
        _got += _chunk
        if b"hello-tunnel" in _got:
            break
    _c.close()
except Exception as e:                           # noqa: BLE001
    _got = f"<{type(e).__name__}: {e}>".encode()
check("本地端口 → 远端 → 原路返回", b"hello-tunnel" in _got, str(_got)[:80])
check("走的是 direct-tcpip 通道（不是拉 ssh.exe 子进程）",
      getattr(_fac.last, "last_channel_kind", "") == "direct-tcpip",
      str(getattr(_fac.last, "last_channel_kind", "")))

print("=== 5. 断线 → 重连状态序列 → FAILED（重点：假活检查）===")
_err = paramiko.SSHException("Unable to connect to port 22")
_fac2 = SequenceFactory([{}, {"raise": _err}])   # 首次 OK，之后永远连不上
_j2 = job(transport_factory=_fac2)
_seq = []
_j2._on_state_cb = lambda s, t: _seq.append(s)
_j2.start()
wait_for(lambda: _j2.state == tunnel.ST_CONNECTED, 3.0)
check("先连上", _j2.state == tunnel.ST_CONNECTED, _j2.state)
_port2 = _j2.local_port
_fac2.last.drop()                                # 模拟链路被切断
_failed = wait_for(lambda: _j2.state == tunnel.ST_FAILED, 6.0)
check("5 次重连用尽 → FAILED", _failed, _j2.state)
check("状态序列 CONNECTED → RECONNECTING×5 → FAILED",
      [s for s in _seq if s != tunnel.ST_CONNECTING]
      == [tunnel.ST_CONNECTED] + [tunnel.ST_RECONNECTING] * 5 + [tunnel.ST_FAILED],
      str(_seq))
check("重连次数上限 5", len(_fac2.calls) == 1 + 5, f"{len(_fac2.calls)} 次连接")
check("退避序列实测 [1,2,4,8,16]", _j2.sleeps == [1, 2, 4, 8, 16], str(_j2.sleeps))
check("★ FAILED 后 registry 已清空（假活判定 1）",
      REG.is_active(INST["id"]) is False, str(REG.snapshot()))
check("★ FAILED 后 api_base 回落 base_url（假活判定 2）",
      apib.api_base(INST) == _base_saved, apib.api_base(INST))
check("★ FAILED 后本地端口可以重新 bind（假活判定 3）", can_bind(_port2) is True,
      f"port={_port2}")
check("FAILED 文案是 PRD 原文", _j2.state_text == tunnel.TXT_FAILED, _j2.state_text)

print("=== 6. RECONNECTING 期间先回落 base_url ===")
_fac3 = SequenceFactory([{}, {"raise": _err}, {}])
_j3 = job(transport_factory=_fac3)
_hist = []


def _watch(state, _t):
    _hist.append((state, apib.api_base(INST)))


_j3._on_state_cb = _watch
_j3.start()
wait_for(lambda: _j3.state == tunnel.ST_CONNECTED, 3.0)
_fac3.last.drop()
wait_for(lambda: any(s == tunnel.ST_RECONNECTING for s, _b in _hist), 3.0)
_rec = [b for s, b in _hist if s == tunnel.ST_RECONNECTING]
check("进入 RECONNECTING 时基址已回落（不打已关闭的本地端口）",
      bool(_rec) and all(b == _base_saved for b in _rec), str(_rec[:3]))
wait_for(lambda: _j3.state in (tunnel.ST_CONNECTED, tunnel.ST_FAILED), 4.0)
check("第二次重连成功后 override 重新登记",
      _j3.state == tunnel.ST_CONNECTED and REG.is_active(INST["id"]) is True,
      f"{_j3.state} {REG.snapshot()}")
_j3.stop()
check("stop() 后 override 清空", REG.is_active(INST["id"]) is False)
check("stop() 后状态是已断开", _j3.state_text == tunnel.TXT_DISCONNECTED,
      _j3.state_text)
check("stop() 后端口可重新 bind", can_bind(_j3.local_port) is True)

print("=== 7. TunnelManager 与 stop_all ===")
REG.reset()
_mgr = tunnel.TunnelManager(transport_factory=FakeFactory(),
                            sleep_fn=lambda _s: None)
_ok, _txt = _mgr.start(INST)
check("manager.start 返回 True", _ok is True, _txt)
check("manager.is_active 认得出",
      wait_for(lambda: _mgr.is_active(INST["id"]), 3.0) is True,
      str(_mgr.state_of(INST["id"])))
_mgr.stop(INST["id"])
check("manager.stop 清 override", REG.is_active(INST["id"]) is False)
_ok2, _ = _mgr.start(INST)
wait_for(lambda: _mgr.is_active(INST["id"]), 3.0)
_mgr.stop_all()
check("stop_all 后 registry 空", len(REG) == 0, str(REG.snapshot()))

print("=== 8. ToolCtx.api / TaskWatcher.base 现算 ===")
from app.ai_tools import ToolCtx                     # noqa: E402
from app.task_watch import TaskWatcher               # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

_app = QApplication.instance() or QApplication([])

_ctx = ToolCtx({}, INST)
_before = _ctx.api
REG.set_override(INST["id"], "http://127.0.0.1:19189", 19189)
_after = _ctx.api
REG.clear_override(INST["id"])
_back = _ctx.api
check("ToolCtx.api 建好后先是非隧道地址",
      _before == "http://192.168.1.20:8188", _before)
check("ToolCtx.api 隧道上线后现算为回环（属性化生效）",
      _after == "http://127.0.0.1:19189", _after)
check("ToolCtx.api 断开后回落", _back == "http://192.168.1.20:8188", _back)

_tw = TaskWatcher("隧道实例", 8188, inst=INST)
_tw_before = _tw.base
REG.set_override(INST["id"], "http://127.0.0.1:19189", 19189)
_tw_after = _tw.base
REG.clear_override(INST["id"])
_tw_back = _tw.base
check("TaskWatcher.base 建好后先是非隧道地址",
      _tw_before == "http://192.168.1.20:8188", _tw_before)
check("TaskWatcher.base 隧道上线后现算为回环", _tw_after == "http://127.0.0.1:19189",
      _tw_after)
check("TaskWatcher.base 断开后回落", _tw_back == "http://192.168.1.20:8188", _tw_back)
_tw_local = TaskWatcher("本机", 8199)
check("无 inst 的 watcher 仍用 127.0.0.1:port", _tw_local.base == "http://127.0.0.1:8199",
      _tw_local.base)

print("=== 9. 明文告警口径 & opener_for_url ===")
_http_inst = {"id": "i1", "kind": "remote", "base_url": "http://192.168.1.20:8188"}
_https_inst = {"id": "i2", "kind": "remote", "base_url": "https://x.platform.com"}
check("http 远程 → is_insecure", apib.effective_base_info(_http_inst)["is_insecure"] is True)
check("https 远程 → 不告警",
      apib.effective_base_info(_https_inst)["is_insecure"] is False)
check("本机 http 127.0.0.1 → 不告警",
      apib.effective_base_info({"kind": "local", "port": 8188})["is_insecure"] is False)
# B5：钩子请求「回环直连、其余走系统代理」。直接看 handler 列表不可靠
# （build_opener 对 ProxyHandler 不放进 .handlers），改成真打一次：
# 把 http_proxy 指到一个没人监听的端口，回环请求仍然必须成功。
import threading                                      # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class _H(BaseHTTPRequestHandler):
    def do_GET(self):                                 # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_a):
        return


_httpd = HTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=_httpd.serve_forever, daemon=True).start()
_lp = _httpd.server_address[1]
os.environ["http_proxy"] = "http://127.0.0.1:1"       # 死代理
os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
try:
    with apib.opener_for_url("http://127.0.0.1:%d/" % _lp).open(
            "http://127.0.0.1:%d/" % _lp, timeout=3) as _r:
        _loop_ok = _r.read() == b"ok"
    _loop_err = ""
except Exception as e:                                # noqa: BLE001
    _loop_ok, _loop_err = False, f"{type(e).__name__}: {e}"
finally:
    os.environ.pop("http_proxy", None)
    os.environ.pop("HTTP_PROXY", None)
    _httpd.shutdown()
check("opener_for_url 回环直连（http_proxy 是死代理也照样通）", _loop_ok, _loop_err)

from app import remote_ops as ro                     # noqa: E402

check("insecure_reason 对明文远程非空",
      ro.RemoteOps().insecure_reason(_http_inst) == "http://192.168.1.20:8188",
      ro.RemoteOps().insecure_reason(_http_inst))
_muted = dict(_http_inst, insecure_warn_muted=True)
check("勾了「不再提示」→ 空串", ro.RemoteOps().insecure_reason(_muted) == "")
REG.set_override("i1", "http://127.0.0.1:19189", 19189)
check("隧道激活 → 不告警（D1）", ro.RemoteOps().insecure_reason(_http_inst) == "")
REG.reset()

print("=== 10. 层次约束 ===")
import ast                                            # noqa: E402


def _imports(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


for _mod in ("tunnel.py", "remote_registry.py"):
    _imp = _imports(os.path.join(ROOT, "app", _mod))
    check(f"{_mod} 不 import PySide6",
          not [m for m in _imp if m.split(".")[0] == "PySide6"], str(sorted(_imp)))
    check(f"{_mod} 不 import proc_manager",
          not [m for m in _imp if "proc_manager" in m])
check("proc_manager 有 invalidate_all",
      hasattr(__import__("app.proc_manager", fromlist=["x"]).ProcManager,
              "invalidate_all"))
with open(os.path.join(ROOT, "app", "ui", "pages", "settings_page.py"),
          encoding="utf-8") as f:
    _sp = f.read()
check("MCP 片段导出用 saved_api_base（不把临时隧道端口粘给别人）",
      "apib.saved_api_base(inst)" in _sp)

print()
bad = [n for n, ok, _ in res if not ok]
print(f"结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
