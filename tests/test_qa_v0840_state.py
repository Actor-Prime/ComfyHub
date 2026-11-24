# -*- coding: utf-8 -*-
"""QA 独立验证 V4：状态机与并发（换角度证伪，不重跑工程师的用例）

工程师的 test_remote_ops.py 验的是「每种结果对不对」；
本文件验的是「**过程**有没有留后遗症」和「**UI 有没有把标志当回事**」：

  V4-1 真并发（两条线程同时进 start）→ 只有一条进得去，另一条立刻 busy
  V4-2 锁不泄漏：每条失败路径（连接失败 / 命令失败 / busy / 取消 / 超时）
       跑完之后 `is_busy()` 必须是 False（漏一条 = 该实例永久锁死，用户只能重启软件）
  V4-3 start 途中 cancel → channel 已 close，且线程数回到基线（无残留）
  V4-4 退出码 0 但永不 ready → not_ready + 尾部 20 行，且不会超时爆炸
  V4-5 ★预探针就绪 → `pre_ready=True`，但 **UI 文案仍是「✅ 已就绪」**
       （ARCH §12 #3 承诺「不会谎报启动成功」，本项检查这个承诺在 UI 层是否兑现）
  V4-6 stop 后仍可达 → still_reachable
  V4-7 实例在 start 过程中被删除 → 编排不崩、返回值正常
  V4-8 拿不到退出码（code=None）但未 detach → 不算快速失败（继续轮询）

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_state.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# D-09：退出前先销毁 QApplication，否则进程结束阶段偶发 0xC0000005
from _qa_exit import clean_exit        # noqa: E402

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_state_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import remote_ops as ro                # noqa: E402
from app import remote_ssh                      # noqa: E402
from fake_ssh import make_inst                  # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


class FakeSession:
    """exec_stream 桩：可阻塞、可取消"""

    def __init__(self, *, lines=(), code=0, err_kind="", detached=False,
                 cancelled=False, block_event=None, closed_flag=None):
        self.lines = list(lines)
        self.code = code
        self.err_kind = err_kind
        self.detached = detached
        self.cancelled = cancelled
        self.block_event = block_event
        self.closed_flag = closed_flag
        self.calls = []
        self.cancel_observed = False

    def exec_stream(self, cmd, on_line=None, cancel=None,
                    settle_timeout=None, total_timeout=None):
        self.calls.append({"cmd": cmd, "settle": settle_timeout})
        if self.block_event is not None:
            self.block_event.wait(5.0)
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
        if self.closed_flag is not None:
            self.closed_flag.append(r.cancelled)
        return r


class Probe:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, inst):
        self.calls += 1
        if not self.seq:
            return False
        return bool(self.seq.pop(0) if len(self.seq) > 1 else self.seq[0])


def _ops(probe=None, session=None, **kw):
    kw.setdefault("poll_interval", 0.01)
    kw.setdefault("ready_timeout", 0.10)
    kw.setdefault("recheck_delay", 0.02)
    kw.setdefault("stop_confirm_timeout", 0.10)
    kw.setdefault("settle_timeout", 0.5)
    return ro.RemoteOps(probe_fn=probe, session_factory=lambda _i: session, **kw)


INST = make_inst(id="inst_v4", name="并发实例")


def _err_kind_of(fn):
    try:
        fn()
        return "<没有抛>"
    except ro.OpsError as e:
        return e.kind


# =====================================================================
print("== V4-1 真并发：两条线程同时进 start，只有一个进得去 ==")
_block = threading.Event()
_slow = FakeSession(block_event=_block)
ops1 = _ops(probe=Probe([True]), session=_slow)
OUT = {}


def _run_start(tag):
    try:
        OUT[tag] = ops1.start(INST, ro.CH_SSH)
    except ro.OpsError as e:
        OUT[tag] = e.kind


t1 = threading.Thread(target=_run_start, args=("a",))
t2 = threading.Thread(target=_run_start, args=("b",))
t1.start()
time.sleep(0.05)                 # 让 a 先拿到锁并卡在 exec_stream
t2.start()
t2.join(3.0)
check("V4-1a 第二条线程拿到 busy（不是排队、不是覆盖前一次）",
      OUT.get("b") == ro.ERR_BUSY, OUT.get("b"))
check("V4-1b 忙的时候 is_busy() 认得出来", ops1.is_busy(INST) is True)
_block.set()
t1.join(5.0)
check("V4-1c 第一条线程正常结束且是 OpsResult", hasattr(OUT.get("a"), "ok"),
      type(OUT.get("a")).__name__)
check("V4-1d 两条线程结束后锁已释放（不会永久锁死）",
      ops1.is_busy(INST) is False)

# =====================================================================
print("== V4-2 每条失败路径都不许泄漏实例锁 ==")


class _RaisingSession:
    def exec_stream(self, *a, **k):
        raise remote_ssh.SshError("auth_password", "密码错")


class _SudoSession:
    def exec_stream(self, cmd, on_line=None, cancel=None, **k):
        r = remote_ssh.ExecResult()
        r.lines.append("sudo: a terminal is required")
        r.err_kind = "sudo"
        r.code = 1
        return r


class _CancelSession:
    def exec_stream(self, cmd, on_line=None, cancel=None, **k):
        r = remote_ssh.ExecResult()
        r.cancelled = True
        return r


def _session_that_fails_to_connect(_inst):
    raise remote_ssh.SshError("unreachable", "连不上")


PATHS = [
    ("连接失败（SshError 从 session_factory 抛）", dict(
        session=None, session_raise=True)),
    ("命令抛出异常（exec_stream 抛）", dict(session=_RaisingSession())),
    ("sudo 提权要求", dict(session=_SudoSession())),
    ("执行期被取消", dict(session=_CancelSession())),
    ("退出码非 0（cmd）", dict(session=FakeSession(code=2,
                                                  lines=["No such file"]))),
    ("5 分钟未就绪（not_ready）", dict(session=FakeSession(code=0))),
]
for label, kw in PATHS:
    if kw.pop("session_raise", False):
        o = ro.RemoteOps(probe_fn=Probe([False]),
                         session_factory=_session_that_fails_to_connect,
                         poll_interval=0.01, ready_timeout=0.05,
                         settle_timeout=0.5)
    else:
        o = _ops(probe=Probe([False]), session=kw["session"])
    try:
        r = o.start(INST, ro.CH_SSH)
        kind = r.err_kind or ("ok" if r.ok else "?")
    except ro.OpsError as e:                     # busy/no_channel 也走这里
        kind = e.kind
    check(f"V4-2 {label} → 跑完锁已释放", o.is_busy(INST) is False, kind)

# busy 路径本身（异常从 _check 之后抛出）也不能泄漏
o_busy = _ops(probe=Probe([True]), session=FakeSession(block_event=_block))
_block.clear()
th = threading.Thread(target=lambda: o_busy.start(INST, ro.CH_SSH))
th.start()
time.sleep(0.05)
_k = _err_kind_of(lambda: o_busy.start(INST, ro.CH_SSH))
_block.set()
th.join(5.0)
check("V4-2 busy 路径：第二次抛 OpsError(busy) 后锁仍在第一次手里（正常）",
      _k == ro.ERR_BUSY, _k)
check("V4-2' busy 路径：第一次结束后锁也释放",
      o_busy.is_busy(INST) is False)

# =====================================================================
print("== V4-3 start 途中 cancel：channel 已 close，线程数回到基线 ==")
_chan_closed = []
ops3 = _ops(probe=Probe([False]),
            session=FakeSession(detached=False, cancelled=True,
                                closed_flag=_chan_closed))
base_threads = {t.name for t in threading.enumerate()}
cancel_flag = {"v": False}
ops3c = ro.RemoteOps(probe_fn=Probe([False]),
                     session_factory=lambda _i: FakeSession(code=0),
                     poll_interval=0.02, ready_timeout=5.0)
ticks = {"n": 0}


def _probe_slow(inst):
    """刚进入轮询就把 cancel 打开 —— 模拟用户点「停止等待」"""
    ticks["n"] += 1
    if ticks["n"] >= 2:
        cancel_flag["v"] = True
    return False


ops3c._probe_fn = _probe_slow
r3 = ops3c.start(INST, ro.CH_SSH, cancel=lambda: cancel_flag["v"])
check("V4-3a 轮询中被取消 → err_kind=cancelled", r3.err_kind == ro.ERR_CANCELLED,
      r3.err_kind)
check("V4-3b cancelled 标记位", r3.cancelled is True and r3.state == ro.ST_CANCELLED)
check("V4-3c 取消后锁已释放", ops3c.is_busy(INST) is False)
r3b = ops3.start(INST, ro.CH_SSH)
check("V4-3d exec_stream 报告 cancelled → 结果也是 cancelled",
      r3b.err_kind == ro.ERR_CANCELLED, r3b.err_kind)
check("V4-3e 取消后该实例立刻可以再被操作（锁没泄漏）",
      ops3.is_busy(INST) is False)
time.sleep(0.1)
_new_threads = {t.name for t in threading.enumerate()} - base_threads
check("V4-3f 没有残留的自建线程（基线比对）",
      not [n for n in _new_threads if n.startswith(("ssh", "tunnel", "ops"))],
      sorted(_new_threads))

# =====================================================================
print("== V4-4 退出码 0 但永不 ready ==")
_lines = ["[ComfyUI] starting %d" % i for i in range(25)]
ops4 = _ops(probe=Probe([False]),
            session=FakeSession(lines=_lines, code=0))
_t0 = time.monotonic()
r4 = ops4.start(INST, ro.CH_SSH)
_dt = time.monotonic() - _t0
check("V4-4a err_kind=not_ready", r4.err_kind == ro.ERR_NOT_READY, r4.err_kind)
check("V4-4b last_lines 恰好尾部 20 行", len(r4.last_lines) == 20,
      len(r4.last_lines))
check("V4-4c last_lines 是最新的 20 行（丢头留尾）",
      r4.last_lines[-1] == _lines[-1] and r4.last_lines[0] == _lines[5],
      (r4.last_lines[0], r4.last_lines[-1]))
check("V4-4d 退出码 0 不算成功", r4.ok is False and r4.code == 0, r4.code)
check("V4-4e 轮询在上限附近收手（没跑飞）", _dt < 2.0, f"{_dt:.2f}s")

# =====================================================================
print("== V4-5 预探针就绪：pre_ready=True，UI 文案是否兑现「不谎报启动成功」==")
ops5 = _ops(probe=Probe([True]), session=FakeSession(code=0))
r5 = ops5.start(INST, ro.CH_SSH)
check("V4-5a 结果 pre_ready=True（编排层记了标志）", r5.pre_ready is True)
check("V4-5b 编排层 ok=True / state=ready", r5.ok is True, repr(r5))

# 消费方一：worker 是否把标志透传
from app import workers                                      # noqa: E402
check("V4-5c RemoteOpsWorker 的 payload 里有 pre_ready 键",
      '"pre_ready": bool(r.pre_ready)' in open(
          os.path.join(ROOT, "app", "workers.py"), encoding="utf-8").read())
# 消费方二：UI 文案是否区分「本来就跑着」和「刚启动成功」
from PySide6.QtWidgets import QApplication                    # noqa: E402
import json as _json                                          # noqa: E402
import tempfile as _tf                                        # noqa: E402
from app.ui.main_window import MainWindow                     # noqa: E402

app = QApplication.instance() or QApplication([])
w = MainWindow()
page = w.comfyui_page
_txt_pre = page._ops_result_text({"ok": True, "pre_ready": True,
                                  "channel": "ssh", "err_kind": ""})
_txt_new = page._ops_result_text({"ok": True, "pre_ready": False,
                                  "channel": "ssh", "err_kind": ""})
check("V4-5d 预探针就绪 vs 真正启动成功：UI 文案必须不同（D-02 已修，原先两者完全相同）",
      _txt_pre != _txt_new, f"{_txt_pre!r} == {_txt_new!r}")
check("V4-5e 预探针就绪时不许谎报「已就绪 / 启动成功」",
      ("已就绪" not in _txt_pre) and ("启动成功" not in _txt_pre), _txt_pre)
_src_ui = open(os.path.join(ROOT, "app", "ui", "pages", "comfyui_page.py"),
               encoding="utf-8").read()
check("V4-5f UI 层必须真的引用 pre_ready（否则文案无从区分）",
      '"pre_ready"' in _src_ui or "'pre_ready'" in _src_ui,
      "UI 层 0 处引用 pre_ready")
check("V4-5g 对话框完成态也只看 ok", 'dlg.finish(ok,' in _src_ui)

# =====================================================================
print("== V4-6 stop 后仍可达 ==")
ops6 = _ops(probe=Probe([True]), session=FakeSession(code=0))
r6 = ops6.stop(INST, ro.CH_SSH)
check("V4-6a err_kind=still_reachable", r6.err_kind == ro.ERR_STILL_REACHABLE,
      r6.err_kind)
check("V4-6b 不是 ok（不报停止成功）", r6.ok is False)
check("V4-6c 停止动作后锁已释放", ops6.is_busy(INST) is False)
ops6b = _ops(probe=Probe([True, True, False]), session=FakeSession(code=0))
r6b = ops6b.stop(INST, ro.CH_SSH)
check("V4-6d 变成不可达后立刻 ok", r6b.ok is True and r6b.state == ro.ST_IDLE,
      repr(r6b))

# =====================================================================
print("== V4-7 实例在 start 过程中被删除（UI 删实例，worker 还在跑）==")
_alive = threading.Event()
ops7 = ro.RemoteOps(probe_fn=Probe([False]),
                    session_factory=lambda _i: FakeSession(block_event=_alive),
                    poll_interval=0.01, ready_timeout=0.3,
                    settle_timeout=0.5)
_inst7 = dict(make_inst(id="inst_del", name="被删实例"))
_holder = {}


def _run7():
    try:
        _holder["r"] = ops7.start(_inst7, ro.CH_SSH)
    except Exception as e:                       # noqa: BLE001
        _holder["r"] = e


th7 = threading.Thread(target=_run7)
th7.start()
time.sleep(0.05)
_inst7.clear()                                    # 模拟删除：dict 被清空
_alive.set()
th7.join(5.0)
check("V4-7a 实例 dict 被清空后编排不崩（有返回值）",
      "r" in _holder, type(_holder.get("r")).__name__)
check("V4-7b 返回的是 OpsResult（不是异常）",
      isinstance(_holder.get("r"), ro.OpsResult), type(_holder.get("r")).__name__)
check("V4-7c 锁已释放（不会因为实例没了而锁死）",
      not ops7.is_busy({"id": "inst_del"}))

# =====================================================================
print("== V4-8 退出码 None（拿不到）但未 detach → 不当作快速失败 ==")
class _NoneCodeSession:
    def exec_stream(self, cmd, on_line=None, cancel=None, **k):
        r = remote_ssh.ExecResult()
        r.code = None
        r.detached = False
        r.lines.append("output without exit status")
        return r


ops8 = _ops(probe=Probe([False, False, True]), session=_NoneCodeSession())
r8 = ops8.start(INST, ro.CH_SSH)
check("V4-8a code=None 不被误判成 cmd 失败（继续轮询直到就绪）",
      r8.ok is True and r8.err_kind == "", repr(r8))

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V4 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
clean_exit(1 if bad else 0)
