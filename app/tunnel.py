# -*- coding: utf-8 -*-
"""内置 SSH 隧道（2026-12-01，0.8.40）：进程内转发 + 退避重连 + 基址覆盖

为什么是「进程内转发」而不是拉一个 `ssh.exe -L`：
  · 子进程要带着密码跑（密码必须进命令行/环境变量，可见）；
  · 退出时容易留孤儿进程（PRD C5 明确要求「进程管理器里没有 ssh 残留」）；
  · 我们已经有 paramiko 会话与主机指纹校验，`direct-tcpip` 通道天然复用这套认证。
  所以：本地 `socket.listen` → 每条连接 `Transport.open_channel("direct-tcpip")` →
  两个线程做双向 relay。进程内没有可残留的 OS 对象。

**本版最脏的故障形态（ARCH §11 风险 2，优先级最高）**：
  隧道 5 次重连用尽后，如果只把状态改成「失败」而不清掉基址覆盖、不关本地监听，
  就会出现「本地端口还在 listen、UI 显示运行中、实际一个 TCP 都打不通」的假活。
  所以下面三个位置**强制** `clear_override + srv.close()`：
    · 重连期间（先回落 base_url，别让请求打一个已关闭的本地端口）；
    · 重连用尽 → FAILED；
    · stop() / 主窗退出 / 删除实例。

禁止 import PySide6 / proc_manager（层约束，ARCH §1.2）。
"""
import socket
import threading
import time

from app import remote_registry as registry_mod
from app.remote_ssh import SshSession

# ---------------------------------------------------------------- 状态
ST_IDLE = "idle"
ST_CONNECTING = "connecting"
ST_CONNECTED = "connected"
ST_RECONNECTING = "reconnecting"
ST_FAILED = "failed"
ST_STOPPED = "stopped"

# PRD 5.3.1 四态原文（UI 直接显示这些字符串；含全角空格 U+3000，别改成普通空格）
TXT_CONNECTED = "隧道已连接\u3000{local} → {remote}"
TXT_DISCONNECTED = "隧道已断开"
TXT_RECONNECTING = "重连中（第 {n}/{total} 次）…"
TXT_FAILED = "隧道重连失败，已停止"
TXT_CONNECTING = "正在建立隧道…"
TXT_PORT_BUSY = ("本地端口 `{port}` 已被占用，隧道没建。\n"
                 "换一个试试（{alt1} / {alt2}），或先关掉占用它的程序。")

BACKOFF = (1, 2, 4, 8, 16)
PROBE_INTERVAL = 2.0        # 每 2 秒看一次 transport 是否还活着
CONNECT_TIMEOUT = 15.0
ACCEPT_TIMEOUT = 0.2
BUF = 65536


def local_base(port: int) -> str:
    return "http://127.0.0.1:%d" % int(port)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """端口是否已被占用（严格 bind 探测）

    不用 SO_REUSEADDR：Windows 下带 REUSEADDR 可以「抢绑」已被监听的端口，会误判成
    「没占用」。剥掉它以后 bind 冲突就是 WinError 10048，判定可靠。
    也不用 netstat 子进程（tunnel 要能在离屏/纯 Python 环境跑）。
    """
    p = int(port)
    if p <= 0:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, p))
        return False
    except OSError:
        return True
    finally:
        try:
            s.close()
        except OSError:
            pass


def _bind_listener(port: int, host: str = "127.0.0.1"):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind((host, int(port)))
        srv.listen(16)
        srv.settimeout(ACCEPT_TIMEOUT)
        return srv
    except OSError:
        try:
            srv.close()
        except OSError:
            pass
        raise


class TunnelJob:
    """一条隧道（一个实例一个）

    可注入点：`transport_factory` / `clock` / `backoff` / `probe_interval` /
    `local_port`（0 = 让系统分配，测试用）/ `sleep_fn` / `on_state` / `on_log`。
    `start()` 立即返回（真正的连接在后台线程），`stop()` 幂等。
    """

    def __init__(self, inst: dict, *, transport_factory=None,
                 clock=time.monotonic, backoff=BACKOFF,
                 probe_interval: float = PROBE_INTERVAL,
                 local_port=None, on_state=None, on_log=None,
                 sleep_fn=time.sleep, registry=None,
                 connect_timeout: float = CONNECT_TIMEOUT):
        self._inst = dict(inst or {})
        self._factory = transport_factory
        self._clock = clock
        self._backoff = tuple(backoff or BACKOFF)
        self._probe_interval = float(probe_interval)
        self._sleep_fn = sleep_fn
        self._on_state_cb = on_state
        self._on_log_cb = on_log
        # 空注册表是 falsy：`or` 会把测试注入的实例静默换成全局单例
        # （D-06），显式判 None 才能尊重调用方传进来的空注册表。
        self._registry = registry if registry is not None else registry_mod.registry
        self._connect_timeout = float(connect_timeout)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._srv = None
        self._sess = None
        self._th = None
        self._relays = []
        self.state = ST_IDLE
        self.state_text = TXT_DISCONNECTED
        self.backoff_n = 0
        self.sleeps = []                 # 退避实测序列（测试断言 [1,2,4,8,16]）
        self._want_local = local_port
        self.local_port = int(local_port or 0)

    # ------------------------------------------------------------ 属性
    @property
    def inst_id(self) -> str:
        return str(self._inst.get("id") or "").strip() or "remote"

    @property
    def remote_host(self) -> str:
        return str(self._inst.get("tunnel_remote_host") or "127.0.0.1").strip()

    @property
    def remote_port(self) -> int:
        try:
            return int(self._inst.get("tunnel_remote_port") or 8188)
        except (TypeError, ValueError):
            return 8188

    def _target_port(self) -> int:
        if self._want_local is not None:
            try:
                return int(self._want_local)
            except (TypeError, ValueError):
                return 0
        try:
            return int(self._inst.get("tunnel_local_port") or 8189)
        except (TypeError, ValueError):
            return 8189

    # ------------------------------------------------------------ 回调
    def _emit_state(self, state: str, text: str) -> None:
        self.state = state
        self.state_text = text
        cb = self._on_state_cb
        if cb is None:
            return
        try:
            cb(state, text)
        except Exception:                # noqa: BLE001 - UI 回调异常不影响隧道
            pass

    def _log(self, text: str) -> None:
        cb = self._on_log_cb
        if cb is None:
            return
        try:
            cb(text)
        except Exception:                # noqa: BLE001
            pass

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        try:
            self._sleep_fn(seconds)
        except Exception:                # noqa: BLE001
            pass

    # ------------------------------------------------------------ 启动
    def start(self) -> tuple:
        """建立隧道；返回 (ok, 状态文案)。连接在后台线程里做，本方法立即返回。"""
        ok, text = self._prepare()
        if not ok:
            return ok, text
        self._th = threading.Thread(target=self._main, daemon=True,
                                   name=f"tunnel-{self.inst_id}")
        self._th.start()
        return True, self.state_text

    def _prepare(self) -> tuple:
        """端口预检 + 绑定 + 置「正在建立」；返回 (ok, 文案)"""
        with self._lock:
            if self.state in (ST_CONNECTING, ST_CONNECTED, ST_RECONNECTING):
                return True, self.state_text
            self._stop.clear()
            self.backoff_n = 0
            self._emit_state(ST_CONNECTING, TXT_CONNECTING)
        want = self._target_port()
        if want and port_in_use(want):
            text = TXT_PORT_BUSY.format(port=want, alt1=want + 1, alt2=want + 99)
            self._emit_state(ST_FAILED, text)
            self._log(text.replace("\n", " "))
            return False, text
        try:
            srv = _bind_listener(want)
        except OSError as e:
            text = TXT_PORT_BUSY.format(port=want, alt1=want + 1, alt2=want + 99)
            self._emit_state(ST_FAILED, text)
            self._log(f"本地端口绑定失败：{e}")
            return False, text
        with self._lock:
            self._srv = srv
            self.local_port = int(srv.getsockname()[1])
        return True, self.state_text

    def _main(self) -> None:
        try:
            self._connect_once()
        except Exception as e:           # noqa: BLE001 - SshError 或注入桩异常
            kind = str(getattr(e, "kind", "") or "unknown")
            detail = str(getattr(e, "detail", "") or e)
            self._log(f"建立隧道失败（{kind}）：{detail}")
            self._fail(TXT_FAILED if kind != "unreachable" else TXT_FAILED)
            return
        self._serve()

    # ------------------------------------------------------------ 连接
    def _connect_once(self) -> None:
        sess = SshSession(self._inst, transport_factory=self._factory,
                          clock=self._clock, log=self._log)
        sess.connect(timeout=self._connect_timeout)
        with self._lock:
            self._sess = sess
            port = self.local_port
        # 连上了才登记覆盖：失败分支绝不能留下 override（否则就是假活）
        self._registry.set_override(self.inst_id, local_base(port), port)
        self._emit_state(ST_CONNECTED, TXT_CONNECTED.format(
            local="127.0.0.1:%d" % port,
            remote="%s:%d" % (self.remote_host, self.remote_port)))
        self._log(f"隧道已建立 127.0.0.1:{port} → "
                  f"{self.remote_host}:{self.remote_port}")

    def _session_alive(self) -> bool:
        with self._lock:
            sess = self._sess
        if sess is None:
            return False
        try:
            return bool(sess.is_alive())
        except Exception:                # noqa: BLE001
            return False

    def _close_session(self) -> None:
        with self._lock:
            sess, self._sess = self._sess, None
        if sess is None:
            return
        try:
            sess.close()
        except Exception:                # noqa: BLE001
            pass

    # ------------------------------------------------------------ 服务循环
    def _serve(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                srv = self._srv
            if srv is None:
                return
            if not self._session_alive():
                if not self._reconnect():
                    return               # _fail 里已经把 override/srv 都收拾了
                continue
            try:
                conn, _addr = srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return                   # 监听被关掉（stop/失败）
            self._spawn_relay(conn)

    def _spawn_relay(self, conn) -> None:
        try:
            chan = self._sess.open_direct_tcpip(self.remote_host, self.remote_port)
        except Exception as e:           # noqa: BLE001
            self._log(f"转发失败（{getattr(e, 'kind', 'unknown')}）：{e}")
            try:
                conn.close()
            except OSError:
                pass
            return
        t1 = threading.Thread(target=self._pump_sock_to_chan, args=(conn, chan),
                              daemon=True, name="tunnel-relay-up")
        t2 = threading.Thread(target=self._pump_chan_to_sock, args=(chan, conn),
                              daemon=True, name="tunnel-relay-down")
        with self._lock:
            # 顺手清掉已经关掉的旧连接，避免长会话下列表无限增长
            self._relays = [(c, ch) for (c, ch) in self._relays
                            if not getattr(ch, "closed", False)]
            self._relays.append((conn, chan))
        t1.start()
        t2.start()

    def _pump_sock_to_chan(self, conn, chan) -> None:
        try:
            while not self._stop.is_set():
                data = conn.recv(BUF)
                if not data:
                    break
                chan.sendall(data)
        except Exception:                # noqa: BLE001 - 连接断了就退出
            pass
        finally:
            _safe_close(chan)
            _safe_close(conn)

    def _pump_chan_to_sock(self, chan, conn) -> None:
        try:
            while not self._stop.is_set():
                data = chan.recv(BUF)
                if not data:
                    break
                conn.sendall(data)
        except Exception:                # noqa: BLE001
            pass
        finally:
            _safe_close(chan)
            _safe_close(conn)

    # ------------------------------------------------------------ 重连
    def _reconnect(self) -> bool:
        """退避重连；返回 True 表示已恢复，False 表示用尽（已置 FAILED）"""
        # 重连期间先回落 base_url：不要让请求打一个可能已关闭的本地端口
        self._registry.clear_override(self.inst_id)
        self._close_session()
        total = len(self._backoff)
        for i, delay in enumerate(self._backoff, start=1):
            if self._stop.is_set():
                return False
            self.backoff_n = i
            self._emit_state(ST_RECONNECTING, TXT_RECONNECTING.format(n=i, total=total))
            self._log(f"隧道断开，{delay} 秒后第 {i}/{total} 次重连")
            self._sleep(delay)
            if self._stop.is_set():
                return False
            try:
                self._connect_once()
                self.backoff_n = 0
                return True
            except Exception as e:       # noqa: BLE001
                self._log(f"第 {i}/{total} 次重连失败："
                          f"{getattr(e, 'kind', '') or type(e).__name__}")
        self._fail(TXT_FAILED)
        return False

    # ------------------------------------------------------------ 失败 / 停止
    def _fail(self, text: str) -> None:
        """FAILED 分支：**必须**清 override + 关监听（否则就是「假活」）"""
        self._registry.clear_override(self.inst_id)
        self._close_session()
        self._close_listener()
        self._close_relays()
        self._emit_state(ST_FAILED, text)
        self._log(text)

    def stop(self, reason: str = "") -> None:
        """断开并清理（幂等）：clear_override → 关监听 → 关通道 → 关会话"""
        self._stop.set()
        self._registry.clear_override(self.inst_id)
        self._close_relays()
        self._close_listener()
        self._close_session()
        if self.state != ST_FAILED:
            self._emit_state(ST_STOPPED, TXT_DISCONNECTED)
        if reason:
            self._log(f"隧道已断开（{reason}）")

    def _close_listener(self) -> None:
        with self._lock:
            srv, self._srv = self._srv, None
        if srv is None:
            return
        try:
            srv.close()
        except OSError:
            pass

    def _close_relays(self) -> None:
        with self._lock:
            relays, self._relays = self._relays, []
        for conn, chan in relays:
            _safe_close(chan)
            _safe_close(conn)

    # ------------------------------------------------------------ 状态
    def state_info(self) -> dict:
        return {
            "state": self.state,
            "text": self.state_text,
            "local_port": int(self.local_port or 0),
            "remote_host": self.remote_host,
            "remote_port": self.remote_port,
            "backoff_n": int(self.backoff_n),
            "registry_active": bool(self._registry.is_active(self.inst_id)),
        }


def _safe_close(obj) -> None:
    if obj is None:
        return
    try:
        obj.close()
    except Exception:                    # noqa: BLE001
        pass


class TunnelManager:
    """`{inst_id: TunnelJob}` 的薄封装（UI 与 main_window 只跟它打交道）"""

    def __init__(self, *, transport_factory=None, clock=time.monotonic,
                 registry=None, sleep_fn=time.sleep):
        self._jobs = {}
        self._lock = threading.RLock()
        self._factory = transport_factory
        self._clock = clock
        # 空注册表是 falsy：`or` 会把测试注入的实例静默换成全局单例
        # （D-06），显式判 None 才能尊重调用方传进来的空注册表。
        self._registry = registry if registry is not None else registry_mod.registry
        self._sleep_fn = sleep_fn

    def start(self, inst: dict, on_state=None, on_log=None) -> tuple:
        """返回 (ok, 文案)。端口被占用时返回 (False, PRD 5.3.3 原文) 且不注册覆盖。"""
        iid = str((inst or {}).get("id") or "").strip() or "remote"
        with self._lock:
            job = self._jobs.get(iid)
        if job is not None and job.state in (ST_CONNECTED, ST_CONNECTING,
                                             ST_RECONNECTING):
            return True, job.state_text
        job = TunnelJob(inst, transport_factory=self._factory, clock=self._clock,
                        on_state=on_state, on_log=on_log,
                        sleep_fn=self._sleep_fn, registry=self._registry)
        with self._lock:
            self._jobs[iid] = job
        ok, text = job.start()
        if not ok:
            with self._lock:
                self._jobs.pop(iid, None)
            # 兜底：失败分支绝不能留 override
            self._registry.clear_override(iid)
        return ok, text

    def stop(self, inst_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(str(inst_id or ""), None)
        if job is not None:
            job.stop("用户断开")
        # 就算没有 job，也清一次（删实例 / 重启后残留）
        self._registry.clear_override(inst_id)

    def stop_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            try:
                job.stop("程序退出")
            except Exception:            # noqa: BLE001 - 退出路径绝不允许抛异常
                pass

    def job(self, inst_id: str):
        with self._lock:
            return self._jobs.get(str(inst_id or ""))

    def state_of(self, inst_id: str) -> dict:
        job = self.job(inst_id)
        if job is None:
            return {"state": ST_IDLE, "text": TXT_DISCONNECTED, "local_port": 0,
                    "remote_host": "", "remote_port": 0, "backoff_n": 0,
                    "registry_active": False}
        return job.state_info()

    def is_active(self, inst_id: str) -> bool:
        job = self.job(inst_id)
        return bool(job is not None and job.state == ST_CONNECTED
                    and self._registry.is_active(inst_id))
