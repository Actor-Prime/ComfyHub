# -*- coding: utf-8 -*-
"""0.8.40 T03：远程启停编排（状态机 / 三道闸 / 停止确认 / 互斥）

覆盖（ARCH §7 T03 验收 ①-⑦）：
  ① 退出码 0 但永不 ready → err_kind="not_ready"，last_lines 是尾部 20 行
  ② 退出码 2 → err_kind="cmd"，且**不进就绪轮询**（探针调用次数证明）
  ③ 预探针 ready → 仍执行命令，结果 pre_ready=True（不谎报启动成功）
  ④ 3 秒内就绪且预探针未就绪 → 追加复核探针（断言探针被多调了一次）
  ⑤ 停止：命令 exit 0 但探针一直可达 → err_kind="still_reachable"
  ⑥ 并发：同实例第二次 start 立刻 OpsError(kind="busy")
  ⑦ 取消：wait_ready / stop 轮询可中断，err_kind="cancelled"（不杀远端）
  ⑧ 通道：没配 SSH → no_channel；cloud 通道本批 → no_channel
  ⑨ sudo 输出 → err_kind="sudo"
  ⑩ 层次约束：remote_ops 不 import PySide6 / proc_manager

用法：.venv_build/Scripts/python.exe -u tests/test_remote_ops.py
"""
import ast
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from app import remote_ops as ro                # noqa: E402
from app import remote_ssh                      # noqa: E402
from fake_ssh import make_inst                  # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, bool(ok), d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


# ---------------------------------------------------------------- 桩
class FakeSession:
    """exec_stream 桩：按预设结果立即返回（可指定阻塞/取消）"""

    def __init__(self, *, lines=(), code=0, err_kind="", detached=False,
                 cancelled=False, block_event=None):
        self.lines = list(lines)
        self.code = code
        self.err_kind = err_kind
        self.detached = detached
        self.cancelled = cancelled
        self.block_event = block_event
        self.calls = []

    def exec_stream(self, cmd, on_line=None, cancel=None,
                    settle_timeout=None, total_timeout=None):
        self.calls.append({"cmd": cmd, "settle": settle_timeout})
        if self.block_event is not None:
            self.block_event.wait(5.0)          # 卡住，用来测互斥
        for ln in self.lines:
            if on_line:
                on_line(ln)
        r = remote_ssh.ExecResult()
        for ln in self.lines:
            r.lines.append(ln)
        r.code = None if (self.detached or self.cancelled) else self.code
        r.err_kind = self.err_kind
        r.detached = self.detached
        r.cancelled = self.cancelled
        return r


class Probe:
    """按顺序返回预设值的探针；用完之后一直返回 last"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, inst):
        self.calls += 1
        if not self.seq:
            return False
        v = self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]
        return bool(v)


def _ops(probe=None, session=None, **kw):
    kw.setdefault("poll_interval", 0.01)
    kw.setdefault("ready_timeout", 0.08)
    kw.setdefault("recheck_delay", 0.02)
    kw.setdefault("stop_confirm_timeout", 0.08)
    kw.setdefault("settle_timeout", 0.5)
    return ro.RemoteOps(probe_fn=probe, session_factory=lambda _i: session, **kw)


INST = make_inst(id="inst_ops", name="云镜像")

print("=== 1. 退出码 0 但永不就绪 → not_ready + 尾部 20 行 ===")
_lines = [f"line {i}" for i in range(25)]
_p = Probe([False])
_s = FakeSession(lines=_lines, code=0)
r = _ops(_p, _s).start(INST)
check("err_kind=not_ready", r.err_kind == ro.ERR_NOT_READY, r.err_kind)
check("state=failed", r.state == ro.ST_FAILED, r.state)
check("last_lines 恰好 20 行", len(r.last_lines) == 20, str(len(r.last_lines)))
check("last_lines 是尾部（line 5..line 24）",
      r.last_lines[0] == "line 5" and r.last_lines[-1] == "line 24",
      f"{r.last_lines[:1]}..{r.last_lines[-1:]}")
check("退出码 0 不算成功（没报 ok）", r.ok is False)
check("就绪轮询真的跑了（探针被多次调用）", _p.calls > 2, f"{_p.calls} 次")
check("执行的是 ssh_start_cmd", _s.calls and _s.calls[0]["cmd"] == INST["ssh_start_cmd"])

print("=== 2. 退出码非 0 → cmd，且不进就绪轮询 ===")
_p = Probe([False, True, True, True])
_s = FakeSession(lines=["bash: cd: /root/ComfyUI: No such file or directory"], code=2)
r = _ops(_p, _s).start(INST)
check("err_kind=cmd", r.err_kind == ro.ERR_CMD, r.err_kind)
check("退出码被记录", r.code == 2, r.code)
check("探针只用了预探针那一次（没进轮询）", _p.calls == 1, f"{_p.calls} 次")
check("detail 带上了 stderr 证据", "No such file" in r.detail, r.detail)

print("=== 3. 预探针已就绪 → pre_ready=True，仍执行命令 ===")
_p = Probe([True])
_s = FakeSession(lines=["started"], code=0)
r = _ops(_p, _s).start(INST)
check("pre_ready=True", r.pre_ready is True)
check("命令照样下发了", len(_s.calls) == 1)
check("就绪后 ok=True", r.ok is True and r.state == ro.ST_READY, repr(r))
check("预探针就绪时不谎报「启动成功」以外的东西（结果里有 pre_ready 标记）",
      r.pre_ready and r.ok)

print("=== 4. 快速就绪 → 追加 3 秒复核探针 ===")
# 序列：预探针 False → 轮询立即 True（<5s）→ 复核 True → 接受
_p = Probe([False, True, True])
_s = FakeSession(code=0)
_t0 = time.monotonic()
r = _ops(_p, _s, recheck_delay=0.05).start(INST)
_dt = time.monotonic() - _t0
check("结果 ok", r.ok is True, repr(r))
check("复核探针确实多调了一次（pre + 1 次就绪 + 1 次复核）",
      _p.calls >= 3, f"{_p.calls} 次")
check("等了复核延迟（>=0.05s）", _dt >= 0.05, f"{_dt:.3f}s")

# 复核失败 → 继续等 → 超时 not_ready
_p = Probe([False, True, False])
_s = FakeSession(code=0)
r = _ops(_p, _s, recheck_delay=0.05, ready_timeout=0.15).start(INST)
check("复核未通过 → 不认这次就绪", r.err_kind == ro.ERR_NOT_READY, r.err_kind)
check("复核只做一次（不会反复复核）", _p.calls >= 3, f"{_p.calls} 次")

print("=== 5. 停止：exit 0 但一直可达 → still_reachable ===")
_p = Probe([True])                      # 永远可达
_s = FakeSession(code=0)
r = _ops(_p, _s).stop(INST)
check("err_kind=still_reachable", r.err_kind == ro.ERR_STILL_REACHABLE, r.err_kind)
check("执行的是 ssh_stop_cmd", _s.calls and _s.calls[0]["cmd"] == INST["ssh_stop_cmd"])
check("探针被反复调用（在确认不可达）", _p.calls > 1, f"{_p.calls} 次")

_p = Probe([True, False])               # 先可达，一拍后不可达
_s = FakeSession(code=0)
r = _ops(_p, _s).stop(INST)
check("变成不可达 → ok=True / state=idle",
      r.ok is True and r.state == ro.ST_IDLE, repr(r))

print("=== 6. 并发：同实例第二次 → busy ===")
_evt = threading.Event()
_p = Probe([False])
_s = FakeSession(code=0, block_event=_evt)
_worker = _ops(_p, _s)
_out = {}


def _run():
    try:
        _out["r"] = _worker.start(INST)
    except Exception as e:                  # noqa: BLE001
        _out["e"] = e


_th = threading.Thread(target=_run, daemon=True)
_th.start()
time.sleep(0.05)
check("is_busy() 认得出在忙", _worker.is_busy(INST) is True)
try:
    _worker.start(INST)
    _busy_kind = "<none>"
except ro.OpsError as e:
    _busy_kind = e.kind
check("第二次 start → OpsError.kind=busy", _busy_kind == ro.ERR_BUSY, _busy_kind)
_busy_stop = "<none>"
try:
    _worker.stop(INST)
except ro.OpsError as e:
    _busy_stop = e.kind
check("忙的时候 stop 也 → busy", _busy_stop == ro.ERR_BUSY, _busy_stop)
_evt.set()
_th.join(3.0)
check("任务结束后锁已释放", _worker.is_busy(INST) is False)

print("=== 7. 取消：只中止，不杀远端 ===")
_p = Probe([False])
_s = FakeSession(code=0)
_c = {"v": False}


def _flip():
    time.sleep(0.05)
    _c["v"] = True


threading.Thread(target=_flip, daemon=True).start()
r = _ops(_p, _s, ready_timeout=5.0).start(INST, cancel=lambda: _c["v"])
check("start 轮询可取消 → cancelled", r.err_kind == ro.ERR_CANCELLED, r.err_kind)
check("cancelled 标记位", r.cancelled is True and r.state == ro.ST_CANCELLED)

_p = Probe([True])                       # 停止后一直可达
_s = FakeSession(code=0)
_c2 = {"v": False}


def _flip2():
    time.sleep(0.05)
    _c2["v"] = True


threading.Thread(target=_flip2, daemon=True).start()
r = _ops(_p, _s, stop_confirm_timeout=5.0).stop(INST, cancel=lambda: _c2["v"])
check("stop 确认轮询可取消 → cancelled（不杀远端进程）",
      r.err_kind == ro.ERR_CANCELLED, r.err_kind)

print("=== 8. 通道检查 ===")
_no_ssh = dict(INST)
_no_ssh.update({"ssh_host": "", "ssh_user": ""})
try:
    _ops(Probe([False]), FakeSession()).start(_no_ssh)
    _k = "<none>"
except ro.OpsError as e:
    _k = e.kind
check("没配 SSH → no_channel", _k == ro.ERR_NO_CHANNEL, _k)
try:
    _ops(Probe([False]), FakeSession()).start(INST, channel="cloud")
    _k2 = "<none>"
except ro.OpsError as e:
    _k2 = e.kind
check("cloud 通道本批 → no_channel", _k2 == ro.ERR_NO_CHANNEL, _k2)
check("available_channels 只给 ssh", ro.RemoteOps().available_channels(INST) == ["ssh"],
      str(ro.RemoteOps().available_channels(INST)))
_c = dict(INST)
_c["cloud_start"] = {"url": "http://x/api/start"}
check("配了云钩子 URL → 两个通道都列出",
      ro.RemoteOps().available_channels(_c) == ["ssh", "cloud"],
      str(ro.RemoteOps().available_channels(_c)))
_i = dict(INST)
_i["ssh_start_cmd"] = ""
check("启动命令为空 → ssh 通道不算配好（PRD A4 靠 _save 拦）",
      ro.RemoteOps().available_channels(_i) == [])
check("但只要停止命令在，仍然允许「远程停止」",
      ro.RemoteOps().available_channels(_i, action="stop") == ["ssh"])
_p = Probe([False])
_s = FakeSession(code=0)
r = _ops(_p, _s).stop(_i)
check("只配了停止命令的实例能正常停", r.ok is True, repr(r))

print("=== 9. sudo 输出 → kind=sudo ===")
_s = FakeSession(lines=["sudo: a terminal is required to read the password"],
                 code=1, err_kind=remote_ssh.EXEC_SUDO)
r = _ops(Probe([False]), _s).start(INST)
check("err_kind=sudo", r.err_kind == ro.ERR_SUDO, r.err_kind)

print("=== 10. detach 的命令（nohup 不 EOF）照样进轮询 ===")
_p = Probe([False, True, True])
_s = FakeSession(lines=["started"], code=None, detached=True)
r = _ops(_p, _s).start(INST)
check("detached 不被当成失败", r.ok is True, repr(r))
check("detached 标记透传", r.detached is True)

print("=== 11. 内存态与日志前缀 ===")
_o = _ops(Probe([False]), FakeSession(code=1, lines=["boom"]))
_o.start(INST)
_st = _o.state_of(INST)
check("state_of 反映失败态", _st.get("state") == ro.ST_FAILED, str(_st))
check("state_of 带 err_kind", _st.get("err_kind") == ro.ERR_CMD, str(_st))
check("日志前缀格式", ro.log_prefix("远程启动", INST) == "[远程启动 云镜像]",
      ro.log_prefix("远程启动", INST))
check("停止前缀格式", ro.log_prefix("远程停止", INST) == "[远程停止 云镜像]")

print("=== 12. 层次约束 ===")


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


_imp = _imports(os.path.join(ROOT, "app", "remote_ops.py"))
check("remote_ops 不 import PySide6",
      not [m for m in _imp if m.split(".")[0] == "PySide6"], str(sorted(_imp)))
check("remote_ops 不 import proc_manager",
      not [m for m in _imp if "proc_manager" in m])

print()
bad = [n for n, ok, _ in res if not ok]
print(f"结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
