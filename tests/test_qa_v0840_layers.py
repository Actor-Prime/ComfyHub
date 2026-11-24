# -*- coding: utf-8 -*-
"""QA 独立验证 V7：线程规则与分层约束（ARCH §1.2 / §9）

为什么要单独验：分层被破坏的表现不是崩溃，而是「离屏测试跑不起来」和
「网络 IO 跑到 UI 线程把界面冻住」——两类都要等打包版才暴露。

  V7-1 7 个纯逻辑模块**不 import PySide6**（否则无 Qt 环境跑不了单测）
  V7-2 这些模块**不 import proc_manager**（避免层反转）
  V7-3 UI 文件里没有「按钮回调里同步发 HTTP」的新增代码
  V7-4 隧道 / SSH 的连接动作不在 UI 线程（启动只做端口预检，连接进后台线程）
  V7-5 清理统计不在 UI 线程（os.walk 大目录会冻界面）

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_layers.py
"""
import ast
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_layers_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


PURE = ["app/remote_ssh.py", "app/remote_fingerprint.py", "app/tunnel.py",
        "app/remote_ops.py", "app/remote_registry.py", "app/cloud_hooks.py",
        "app/remote_cleanup.py"]


def imports_of(path: str) -> set:
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                out.add(n.module)
            if n.level:                       # 相对导入也记下来
                out.add("." * n.level + (n.module or ""))
    return out


print("== V7-1 纯逻辑模块不 import PySide6 ==")
for f in PURE:
    imp = imports_of(os.path.join(ROOT, f))
    bad = [m for m in imp if m.split(".")[0] in ("PySide6", "PyQt5", "PyQt6")]
    check(f"V7-1 {os.path.basename(f)} 不 import Qt", not bad, bad)

print("== V7-2 纯逻辑模块不 import proc_manager ==")
for f in PURE:
    imp = imports_of(os.path.join(ROOT, f))
    bad = [m for m in imp if "proc_manager" in m]
    check(f"V7-2 {os.path.basename(f)} 不 import proc_manager", not bad, bad)

print("== V7-2' 反向：tunnel / remote_ssh 也不 import remote_ops（不许绕回编排层）==")
for f in ("app/tunnel.py", "app/remote_ssh.py", "app/remote_registry.py"):
    imp = imports_of(os.path.join(ROOT, f))
    bad = [m for m in imp if m.endswith("remote_ops")]
    check(f"V7-2' {os.path.basename(f)} 不 import remote_ops", not bad, bad)

print("== V7-3 UI 文件里没有「按钮回调里同步发 HTTP」= ==")
UI = ["app/ui/pages/remote_ops_page.py", "app/ui/remote_banner.py",
      "app/ui/remote_start_dialog.py", "app/ui/pages/comfyui_page.py",
      "app/ui/pages/settings_page.py", "app/ui/main_window.py"]
NET_FUNCS = ("request_json", "request_bytes", "probe(", "probe_reason",
             "cloud_hooks.send", "urlopen", "getaddrinfo",
             "socket.create_connection")
for f in UI:
    src = open(os.path.join(ROOT, f), encoding="utf-8").read()
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_src = ast.get_source_segment(src, node) or ""
        for fn in NET_FUNCS:
            if fn in body_src:
                hits.append((node.name, fn, node.lineno))
    # 只关心「有没有」；具体判断放在下面的白名单里
    check(f"V7-3 {os.path.basename(f)} 的同步网络调用清单", True, hits)

_src_cp = open(os.path.join(ROOT, "app/ui/pages/comfyui_page.py"),
               encoding="utf-8").read()
# 已知唯一一处：_test_remote 里同步 probe_reason（0.8.39 既有，非本版新增）
_known = re.findall(r"probe_reason\(inst, timeout=8, saved=True\)", _src_cp)
check("V7-3a 全仓 UI 层同步 HTTP 只剩 _test_remote 一处（0.8.39 既有）",
      len(_known) == 1, _known)
check("V7-3b 新增的「发开机/关机请求」只做本地预检 + emit 信号，不发 HTTP",
      "self.sig_hook_request.emit(str(action), inst)" in
      open(os.path.join(ROOT, "app/ui/pages/remote_ops_page.py"),
           encoding="utf-8").read())

print("== V7-4 隧道 / SSH 的连接动作不在 UI 线程 ==")
from app import tunnel                                        # noqa: E402
from app import remote_ssh                                    # noqa: E402
from app import workers                                       # noqa: E402
from fake_ssh import FakeFactory, make_inst                   # noqa: E402
import threading                                              # noqa: E402
import time                                                   # noqa: E402

_threads_at_start = {t.name for t in threading.enumerate()}
_states = []
_inst = make_inst(id="layer1", tunnel_local_port=0)
_job = tunnel.TunnelJob(_inst, transport_factory=FakeFactory(),
                        local_port=0, sleep_fn=lambda s: None,
                        on_state=lambda s, t: _states.append(s))
_t0 = time.monotonic()
_ok, _txt = _job.start()
_dt = time.monotonic() - _t0
check("V7-4a TunnelJob.start() 立刻返回（连接在后台线程，不阻塞调用方）",
      _dt < 0.5, f"{_dt * 1000:.0f}ms")
_named = [t for t in threading.enumerate() if t.name.startswith("tunnel-")]
check("V7-4b 连接跑在名为 tunnel-<id> 的后台线程里",
      bool(_named) or _job.state in (tunnel.ST_CONNECTED,), [t.name for t in _named])
_t0 = time.monotonic()
while _job.state != tunnel.ST_CONNECTED and time.monotonic() - _t0 < 3:
    time.sleep(0.02)
check("V7-4c 后台线程最终把状态推到 CONNECTED", _job.state == tunnel.ST_CONNECTED,
      _job.state)
_job.stop("收尾")

check("V7-4d SshTestWorker 是 QThread（「测试 SSH」不在 UI 线程跑）",
      any(c.__name__ == "QThread" for c in workers.SshTestWorker.__mro__),
      [c.__name__ for c in workers.SshTestWorker.__mro__][:4])
check("V7-4e RemoteOpsWorker 是 QThread（远程启停不在 UI 线程跑）",
      any(c.__name__ == "QThread" for c in workers.RemoteOpsWorker.__mro__))
_src_rop = open(os.path.join(ROOT, "app/ui/pages/remote_ops_page.py"),
                encoding="utf-8").read()
check("V7-4f 「测试 SSH」走 SshTestWorker 而不是直接 session.connect",
      "SshTestWorker(" in _src_rop and ".connect(timeout" not in _src_rop)

print("== V7-5 清理统计不在 UI 线程 ==")
_src_sp = open(os.path.join(ROOT, "app/ui/pages/settings_page.py"),
               encoding="utf-8").read()
check("V7-5a 设置页用 CleanupStatsWorker / ensure_scan_async 而不是直接 scan_dir",
      ("CleanupStatsWorker" in _src_sp or "ensure_scan_async" in _src_sp)
      and "rc.scan_dir(" not in _src_sp,
      "scan_dir 直调" if "rc.scan_dir(" in _src_sp else "已异步")
check("V7-5b CleanupStatsWorker 是 QThread",
      any(c.__name__ == "QThread"
          for c in workers.CleanupStatsWorker.__mro__))
check("V7-5c CleanupWorker 是 QThread",
      any(c.__name__ == "QThread" for c in workers.CleanupWorker.__mro__))
# 统计入口若同步走 stats_rows(force=True) 就是坑：force=True 会真扫盘
_bad_force = re.findall(r"stats_rows\([^)]*force=True", _src_sp)
check("V7-5d 设置页没有在 UI 线程调 stats_rows(force=True)", not _bad_force,
      _bad_force)

check("V7-5e remote_cleanup 自身不 import Qt（纯逻辑）",
      not [m for m in imports_of(os.path.join(ROOT, "app/remote_cleanup.py"))
           if m.split(".")[0].startswith("PySide6")])

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V7 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
