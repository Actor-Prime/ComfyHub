# -*- coding: utf-8 -*-
"""QA 独立验证 V3：隧道「假活」（架构标注的最高风险，暴力验证）

「假活」的定义：本地端口还在 listen、UI 显示已连接/运行中、实际一个 TCP 都打不通。
工程师的测试断言了 `is_active()==False`；但 `is_active()` 是**注册表的自述**，
注册表说自己没覆盖 ≠ 端口真的空出来了。所以本文件的核心断言是：

    FAILED 之后，**用另一个 socket 真的把那个本地端口 bind 回来**。

bind 得回来 = 操作系统确认没人监听 = 端口没有假活。这条断言无法被「改状态字符串」骗过。

覆盖：
  V3-1 正常：api_base 走回环 / saved_api_base 走真地址 / inst["base_url"] 未被改写
  V3-2 首次连接就失败 → FAILED，端口可重新 bind，无覆盖残留
  V3-3 连上后链路被切断 → 5 次重连用尽 → FAILED，端口可重新 bind，无覆盖残留
      （同时断言退避实测序列 = [1,2,4,8,16]）
  V3-4 中途断线期间（RECONNECTING）基址已回落 base_url，不打一个已关的本地端口
  V3-5 端口冲突：本地端口被占 → 报错 + 给建议值（不静默失败、不注册覆盖）
  V3-6 实例改名 / 删除：注册表按 id 计价，改名不失效；删除后无死覆盖
  V3-7 stop() 幂等、退出路径无异常

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_tunnel.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_tunnel_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP

from app import api_base as apib                       # noqa: E402
from app import remote_registry as rr                  # noqa: E402
from app import tunnel                                 # noqa: E402
from fake_ssh import FakeFactory, SequenceFactory, make_inst   # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


def free_port() -> int:
    """让系统给一个当前空闲的端口号（拿到就立刻释放，随后由被测代码 bind）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def can_bind(port: int) -> bool:
    """关键断言工具：这个端口现在还能不能被**别人** bind"""
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


def wait_until(fn, timeout=4.0, step=0.02):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if fn():
            return True
        time.sleep(step)
    return False


rr.registry.reset()

# =====================================================================
print("== V3-1 正常路径：覆盖生效但不改写用户的 base_url ==")
p1 = free_port()
inst1 = make_inst(id="t1", tunnel_local_port=p1, base_url="http://192.168.1.20:8188")
fac1 = FakeFactory()
job1 = tunnel.TunnelJob(inst1, transport_factory=fac1, local_port=p1,
                        sleep_fn=lambda s: None)
ok1, txt1 = job1.start()
check("V3-1a start() 返回 True", ok1, txt1)
check("V3-1b 等状态到 CONNECTED",
      wait_until(lambda: job1.state == tunnel.ST_CONNECTED), job1.state)
check("V3-1c api_base(inst) = http://127.0.0.1:<本地端口>",
      apib.api_base(inst1) == "http://127.0.0.1:%d" % p1, apib.api_base(inst1))
check("V3-1d saved_api_base(inst) 仍是用户填的真地址",
      apib.saved_api_base(inst1) == "http://192.168.1.20:8188",
      apib.saved_api_base(inst1))
check("V3-1e inst['base_url'] 一字未改",
      inst1["base_url"] == "http://192.168.1.20:8188", inst1["base_url"])
check("V3-1f effective_base_info 的 via_tunnel=True / is_insecure=False",
      (lambda i: i["via_tunnel"] and not i["is_insecure"])(
          apib.effective_base_info(inst1)),
      apib.effective_base_info(inst1))
check("V3-1g 本地端口此刻**不可**被别人 bind（隧道真的在监听）",
      not can_bind(p1))
job1.stop("测试收尾")
check("V3-1h stop() 后端口立即可以被别人 bind", wait_until(lambda: can_bind(p1)))
check("V3-1i stop() 后 api_base 回落真地址",
      apib.api_base(inst1) == "http://192.168.1.20:8188", apib.api_base(inst1))

# =====================================================================
print("== V3-2 首次连接就失败（最容易被忽略的假活口）==")
p2 = free_port()
inst2 = make_inst(id="t2", tunnel_local_port=p2)
fac2 = FakeFactory(connect_error=OSError("Connection refused"))
job2 = tunnel.TunnelJob(inst2, transport_factory=fac2, local_port=p2,
                        sleep_fn=lambda s: None)
ok2, txt2 = job2.start()
check("V3-2a start() 先返回 True（连接在后台）", ok2 is True)
check("V3-2b 最终状态 = FAILED",
      wait_until(lambda: job2.state == tunnel.ST_FAILED), job2.state)
check("V3-2c registry.is_active() == False", not rr.registry.is_active("t2"),
      rr.registry.snapshot())
check("V3-2d ★本地端口可以被别人重新 bind（没有假活）",
      wait_until(lambda: can_bind(p2), timeout=2.0), "bind ok")
check("V3-2e api_base 回落到 base_url",
      apib.api_base(inst2) == "http://192.168.1.20:8188", apib.api_base(inst2))
check("V3-2f 状态文案 = 「隧道重连失败，已停止」",
      job2.state_text == tunnel.TXT_FAILED, job2.state_text)

# =====================================================================
print("== V3-3 连上后链路被切断 → 5 次重连用尽 → FAILED ==")
p3 = free_port()
inst3 = make_inst(id="t3", tunnel_local_port=p3)
# 第 1 次能连上，之后每次都连不上
fac3 = SequenceFactory([{}, {"connect_error": OSError("Connection refused")}])
states3 = []
SLEEPS3 = []


def _rec_state(s, t):
    states3.append(s)


def _sleep3(s):
    SLEEPS3.append((s, job3.state, rr.registry.is_active("t3"), apib.api_base(inst3)))


job3 = tunnel.TunnelJob(inst3, transport_factory=fac3, local_port=p3,
                        on_state=_rec_state, sleep_fn=_sleep3,
                        probe_interval=0.05)
job3.start()
check("V3-3a 先到 CONNECTED",
      wait_until(lambda: job3.state == tunnel.ST_CONNECTED), job3.state)
check("V3-3b 断链前 api_base 是回环",
      apib.api_base(inst3) == "http://127.0.0.1:%d" % p3, apib.api_base(inst3))
# 手动切断链路（等价于远端拔网线）
fac3.transports[0].drop()
check("V3-3c 最终 FAILED（5 次重连用尽）",
      wait_until(lambda: job3.state == tunnel.ST_FAILED, timeout=6.0), job3.state)
check("V3-3d 状态序列含 CONNECTED → RECONNECTING → FAILED",
      tunnel.ST_CONNECTED in states3 and tunnel.ST_RECONNECTING in states3
      and states3[-1] == tunnel.ST_FAILED, states3)
check("V3-3e 退避实测序列 = [1,2,4,8,16]",
      [int(s) for s, _st, _a, _b in SLEEPS3] == [1, 2, 4, 8, 16],
      [s for s, _st, _a, _b in SLEEPS3])
check("V3-3f registry.is_active() == False", not rr.registry.is_active("t3"),
      rr.registry.snapshot())
check("V3-3g ★本地端口可以被别人重新 bind（没有假活）",
      wait_until(lambda: can_bind(p3), timeout=2.0), "bind ok")
check("V3-3h api_base 回落 base_url",
      apib.api_base(inst3) == "http://192.168.1.20:8188", apib.api_base(inst3))
check("V3-3i 重连次数上限 = 5（backoff_n 归 5）",
      job3.backoff_n == 5, job3.backoff_n)

print("== V3-4 中途断线（RECONNECTING）期间基址已回落，不打已关的本地端口 ==")
# 每次 sleep 都在「已经 emit RECONNECTING、还没连上」的时刻被调用
_recon = [r for r in SLEEPS3 if r[1] == tunnel.ST_RECONNECTING]
check("V3-4a RECONNECTING 期间 registry 已清空（无覆盖）",
      bool(_recon) and all(r[2] is False for r in _recon),
      [(r[0], r[2]) for r in _recon])
check("V3-4b RECONNECTING 期间 api_base 已回落 base_url（不是本地端口）",
      bool(_recon) and all(r[3] == "http://192.168.1.20:8188" for r in _recon),
      sorted({r[3] for r in _recon}))

# =====================================================================
print("== V3-5 端口冲突：报错 + 给建议值，不静默失败 ==")
p5 = free_port()
blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
blocker.bind(("127.0.0.1", p5))
blocker.listen(4)
inst5 = make_inst(id="t5", tunnel_local_port=p5)
job5 = tunnel.TunnelJob(inst5, transport_factory=FakeFactory(), local_port=p5,
                        sleep_fn=lambda s: None)
ok5, txt5 = job5.start()
check("V3-5a 端口被占 → start() 返回 False", ok5 is False, txt5)
check("V3-5b 文案含「已被占用」", "已被占用" in txt5, txt5.replace("\n", " "))
check("V3-5c 文案给出建议端口（port+1 / port+99）",
      str(p5 + 1) in txt5 and str(p5 + 99) in txt5, txt5.replace("\n", " "))
check("V3-5d 不注册覆盖（失败分支绝不留 override）",
      not rr.registry.is_active("t5"), rr.registry.snapshot())
check("V3-5e 状态置 FAILED（不留在 connecting）", job5.state == tunnel.ST_FAILED,
      job5.state)
check("V3-5f api_base 仍是 base_url", apib.api_base(inst5)
      == "http://192.168.1.20:8188", apib.api_base(inst5))
blocker.close()

# 并发同一实例重复 start → 不应再 bind 第二个端口
p5b = free_port()
tm5 = tunnel.TunnelManager(transport_factory=FakeFactory(), sleep_fn=lambda s: None)
inst5b = make_inst(id="t5b", tunnel_local_port=p5b)
ok5b, _t = tm5.start(inst5b)
wait_until(lambda: tm5.job("t5b") is not None
           and tm5.job("t5b").state == tunnel.ST_CONNECTED)
job_a = tm5.job("t5b")
ok5c, _t2 = tm5.start(inst5b)
check("V3-5g 同实例二次 start 复用同一个 job（不会再抢一个端口）",
      tm5.job("t5b") is job_a, id(tm5.job("t5b")))
tm5.stop_all()

# =====================================================================
print("== V3-6 实例改名 / 删除：注册表不残留死覆盖 ==")
# ★ 曾经有个注入点脚枪：RemoteRegistry 定义了 __len__，空表就是 falsy，
#   于是 `registry or registry_mod.registry` 会把「传入的空注册表」静默换成全局单例。
#   D-06 已修（`if registry is None` 显式判空），这里改成**守着它别回来**。
_empty = rr.RemoteRegistry()
_j_probe = tunnel.TunnelJob(make_inst(id="probe"), registry=_empty)
check("V3-6-0 注入空 RemoteRegistry 不再被 `or` 换成全局单例（D-06 已修）",
      _j_probe._registry is _empty and _j_probe._registry is not rr.registry,
      f"传入 {id(_empty)} 实际 {id(_j_probe._registry)} 全局 {id(rr.registry)}")
_empty.set_override("sanity", "http://127.0.0.1:1", 1)
_j_probe2 = tunnel.TunnelJob(make_inst(id="probe2"), registry=_empty)
check("V3-6-0' 非空则尊重传入的注册表", _j_probe2._registry is _empty)

# 后面的断言走全局注册表（=生产实际形态），避免被上面那条脚枪干扰
rr.registry.reset()
p6 = free_port()
inst6 = make_inst(id="t6", name="原名", tunnel_local_port=p6)
job6 = tunnel.TunnelJob(inst6, transport_factory=FakeFactory(), local_port=p6,
                        sleep_fn=lambda s: None, registry=rr.registry)
job6.start()
check("V3-6a 覆盖已登记",
      wait_until(lambda: rr.registry.is_active("t6"), timeout=3.0),
      rr.registry.snapshot())
renamed = dict(inst6)
renamed["name"] = "改过的名字"
check("V3-6b 改名后 override_for 仍命中（键是 id 不是名字）",
      rr.registry.override_for(renamed) == "http://127.0.0.1:%d" % p6,
      rr.registry.override_for(renamed))
_old_inst = {"id": "t6", "base_url": "http://192.168.1.20:8188",
             "kind": "remote"}
rr.registry.clear_override("t6")
check("V3-6c 删除实例后清覆盖 → 立即回落",
      rr.registry.override_for(_old_inst) == ""
      and not rr.registry.is_active("t6"))
job6.stop("删除实例")

# TunnelManager.stop 在「job 已不存在」时也要清一次覆盖（重启后残留场景）
tm6 = tunnel.TunnelManager()
rr.registry.set_override("ghost", "http://127.0.0.1:19999", 19999)
tm6.stop("ghost")
check("V3-6d job 不存在时 stop() 仍清掉残留覆盖（幽灵覆盖）",
      not rr.registry.is_active("ghost"), rr.registry.snapshot())

# =====================================================================
print("== V3-7 stop 幂等 / 退出路径不抛异常 ==")
job7 = tunnel.TunnelJob(make_inst(id="t7", tunnel_local_port=free_port()),
                        transport_factory=FakeFactory(), local_port=free_port(),
                        sleep_fn=lambda s: None)
job7.start()
wait_until(lambda: job7.state in (tunnel.ST_CONNECTED, tunnel.ST_FAILED))
_err = None
try:
    job7.stop("第一次")
    job7.stop("第二次")
    job7.stop()
except Exception as e:                        # noqa: BLE001
    _err = e
check("V3-7a stop() 幂等、不抛异常", _err is None, _err)
tm7 = tunnel.TunnelManager(sleep_fn=lambda s: None)
_err2 = None
try:
    tm7.stop_all()
    tm7.stop_all()
    tm7.stop("不存在的实例")
except Exception as e:                        # noqa: BLE001
    _err2 = e
check("V3-7b TunnelManager.stop_all() 空跑不抛异常", _err2 is None, _err2)

# 残留线程检查：所有隧道线程都应是 daemon 且能自然退出
_th = [t.name for t in threading.enumerate() if t.name.startswith("tunnel")]
check("V3-7c 收尾后残留隧道线程数 = 0（daemon 也会自然退出）",
      wait_until(lambda: not [t for t in threading.enumerate()
                              if t.name.startswith("tunnel")], timeout=3.0),
      _th)

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V3 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
