# -*- coding: utf-8 -*-
"""远程启停编排（2026-12-01，0.8.40）：状态机 + 三道闸 + 就绪轮询

**本模块不含任何 UI**（禁止 import PySide6 / proc_manager）：
UI 通过 `on_state(text)` / `on_log(line)` 两个回调拿进度，QThread 包装在 workers.py，
对话框在 ui/remote_start_dialog.py（都属第二批）。

状态机（ARCH §5.1）::

    idle ──start──▶ connecting ──ok──▶ executing ──dispatched──▶ waiting_ready ──ok──▶ ready
                        │                  │                          │
                        └──SshError──────▶ └──code!=0──────────────▶ └──300s────────▶ failed(kind)
                                                                       └──cancel────▶ cancelled
    idle ──stop──▶ stopping ──probe False──▶ idle
                        └──probe True(60s)──▶ failed("still_reachable")

三道闸（为什么非要有）：
  ① **预探针**：命令下发前先看实例是不是本来就在跑 → `pre_ready=True`。
     不是多此一举：远端 ComfyUI 早就起好了，用户点「远程启动」，命令 exit 0、
     端口也通，如果不区分就会谎报「启动成功」（PRD A7 明令禁止）。
  ② **退出码只作快速失败信号**：`code != 0` 立刻失败；`code == 0` **不算成功**，
     真正的成功只看探针。
  ③ **快速就绪复核**：就绪耗时 < 5 秒且预探针未就绪 → 3 秒后再探一次，
     仍就绪才接受。挡住「探针读到缓存/半开连接」造成的秒成功。

错误只用 `kind` 枚举（ARCH §3.3），UI 禁止匹配错误字符串：
  `SshError.kind`（连接认证）/ `cmd`（执行失败）/ `sudo`（要提权）/
  `not_ready`（5 分钟没就绪）/ `still_reachable`（停止后仍可达）/ `cancelled`。

两条通道（PRD A1/A10）：
  · `ssh`   —— 连接 → 执行 `ssh_start_cmd` / `ssh_stop_cmd` → 轮询就绪
  · `cloud` —— 打云平台 HTTP 钩子（`cloud_hooks`）→ **2xx 只表示受理**，
               之后照样轮询就绪（PRD 5.2.2：`平台已受理，等待实例就绪 N 秒`）。
               钩子失败的 kind 原样透传（`url_invalid`/`http`/`network`/`timeout`），
               UI 按 `cloud_hooks.describe()` 查 5.2.3 的逐字文案。
"""
import threading
import time

# ---------------------------------------------------------------- 状态枚举
ST_IDLE = "idle"
ST_CONNECTING = "connecting"
ST_EXECUTING = "executing"
ST_WAITING_READY = "waiting_ready"
ST_READY = "ready"
ST_STOPPING = "stopping"
ST_FAILED = "failed"
ST_CANCELLED = "cancelled"

# ---------------------------------------------------------------- 错误 kind
ERR_NOT_READY = "not_ready"
ERR_STILL_REACHABLE = "still_reachable"
ERR_NO_CHANNEL = "no_channel"
ERR_BUSY = "busy"
ERR_CANCELLED = "cancelled"
ERR_CMD = "cmd"
ERR_SUDO = "sudo"

# 通道
CH_SSH = "ssh"
CH_CLOUD = "cloud"
CHANNELS = (CH_SSH, CH_CLOUD)

# 云平台钩子请求超时（PRD 5.2.3 第 4 条：30 秒没响应就放弃本次请求）
HOOK_TIMEOUT = 30.0

READY_TIMEOUT = 300.0        # 就绪轮询上限：5 分钟（PRD A7）
POLL_INTERVAL = 2.0          # 每 2 秒探一次（PRD A7）
QUICK_READY_WINDOW = 5.0     # 「就绪异常快」的判定窗口
RECHECK_DELAY = 3.0          # 快速就绪后的复核延迟
STOP_CONFIRM_TIMEOUT = 60.0  # 停止后等待不可达的上限
SETTLE_TIMEOUT = 30.0        # 命令静默多久就 detach（见 remote_ssh.exec_stream）
TAIL_LINES = 20              # E4 文案要贴的最后 20 行

# 云通道状态行（PRD 5.2.2 第 3–4 步逐字；带秒数的那句拆片段，不整句入 i18n）
TXT_CLOUD_STARTING = "正在请求平台开机…"
TXT_CLOUD_STOPPING = "正在请求平台关机…"
TXT_CLOUD_ACCEPTED = "平台已受理，等待实例就绪 {n} 秒（最多 5 分钟）"
TXT_CLOUD_STOP_SENT = "平台已受理关机，正在确认实例已停止…"


def log_prefix(action: str, inst: dict) -> str:
    """控制台日志前缀：`[远程启动 <实例名>]` / `[远程停止 <实例名>]`

    本模块只**生成**前缀，实际写控制台由 UI 侧（`console_page.append_line`）负责
    —— 纯逻辑模块不认识 UI，层次约束见 ARCH §9。
    """
    name = str((inst or {}).get("name") or (inst or {}).get("id") or "远端")
    return f"[{action} {name}]"


class OpsError(Exception):
    """**前置条件**不满足（不是运行期失败）：busy / no_channel

    运行期失败（not_ready / still_reachable / cancelled / cmd / SshError.kind）
    一律走 `OpsResult.err_kind`，因为调用方需要同时拿到 last_lines 等上下文。
    """

    def __init__(self, kind: str, detail: str = ""):
        self.kind = str(kind or "unknown")
        self.detail = str(detail or "")
        super().__init__(f"{self.kind}: {self.detail}" if self.detail else self.kind)


class OpsResult:
    """一次启停的结果（成功与失败都用它，失败看 `err_kind`）"""

    __slots__ = ("ok", "state", "channel", "err_kind", "detail", "pre_ready",
                 "elapsed", "last_lines", "code", "detached", "cancelled")

    def __init__(self, channel: str = ""):
        self.ok = False
        self.state = ST_IDLE
        self.channel = str(channel or "")
        self.err_kind = ""
        self.detail = ""
        self.pre_ready = False
        self.elapsed = 0.0
        self.last_lines = []
        self.code = None
        self.detached = False
        self.cancelled = False

    def __repr__(self) -> str:
        return (f"OpsResult(ok={self.ok}, state={self.state!r}, "
                f"err_kind={self.err_kind!r}, pre_ready={self.pre_ready}, "
                f"elapsed={self.elapsed:.1f}s, code={self.code})")


def _iid(inst: dict) -> str:
    iid = str((inst or {}).get("id") or "").strip()
    if iid:
        return iid
    host = str((inst or {}).get("ssh_host") or "").strip()
    return host or "remote"


def _chan_of(inst: dict, key: str) -> dict:
    v = (inst or {}).get(key)
    return v if isinstance(v, dict) else {}


class RemoteOps:
    """远程启停编排器（一个进程一个；每实例一把互斥锁）

    可注入点（ARCH §10.2 + 本文件的 session_factory）：
      probe_fn         探针，默认 api_base.probe（测试传假探针）
      now_fn / sleep_fn 时钟与睡眠（超时/退避一律 monotonic，不混 time.time）
      poll_interval / ready_timeout / recheck_delay / stop_confirm_timeout
      session_factory  (inst) -> 会话对象；默认走 remote_ssh.SshManager
      ssh_manager      空闲会话池（测试可换成自己的）
      hook_send        (block, timeout=..., opener=...) -> dict，默认 cloud_hooks.send
    """

    def __init__(self, *, ssh_manager=None, probe_fn=None,
                 now_fn=time.monotonic, sleep_fn=time.sleep,
                 poll_interval: float = POLL_INTERVAL,
                 ready_timeout: float = READY_TIMEOUT,
                 quick_ready_window: float = QUICK_READY_WINDOW,
                 recheck_delay: float = RECHECK_DELAY,
                 stop_confirm_timeout: float = STOP_CONFIRM_TIMEOUT,
                 settle_timeout: float = SETTLE_TIMEOUT,
                 session_factory=None, transport_factory=None, log=None,
                 hook_send=None, hook_timeout: float = HOOK_TIMEOUT):
        self._mgr = ssh_manager
        self._probe_fn = probe_fn
        self._now = now_fn
        self._sleep_fn = sleep_fn
        self._poll = float(poll_interval)
        self._ready_timeout = float(ready_timeout)
        self._quick_window = float(quick_ready_window)
        self._recheck = float(recheck_delay)
        self._stop_timeout = float(stop_confirm_timeout)
        self._settle = float(settle_timeout)
        self._session_factory = session_factory
        self._transport_factory = transport_factory
        self._log = log or (lambda _s: None)
        self._has_custom_log = log is not None
        self._hook_send = hook_send
        self._hook_timeout = float(hook_timeout)
        self._locks = {}
        self._meta = threading.RLock()      # 只保护两个 dict，短持有、绝不做 IO
        self._state = {}

    # ------------------------------------------------------------ 小工具
    def _lock_for(self, iid: str) -> threading.Lock:
        with self._meta:
            lk = self._locks.get(iid)
            if lk is None:
                lk = threading.Lock()
                self._locks[iid] = lk
            return lk

    def _set_state(self, iid: str, state: str, channel: str = "",
                   err_kind: str = "") -> None:
        with self._meta:
            self._state[iid] = {
                "state": state,
                "channel": channel or (self._state.get(iid, {}) or {}).get("channel", ""),
                "t0": (self._state.get(iid, {}) or {}).get("t0") or self._now(),
                "err_kind": err_kind,
                "last_lines": (self._state.get(iid, {}) or {}).get("last_lines", []),
            }

    def _set_lines(self, iid: str, lines) -> None:
        with self._meta:
            st = self._state.setdefault(iid, {})
            st["last_lines"] = list(lines or [])[-TAIL_LINES:]

    def state_of(self, inst) -> dict:
        """内存态快照（不落盘）：{state, channel, t0, err_kind, last_lines}"""
        with self._meta:
            st = self._state.get(_iid(inst)) or {}
            return {k: (list(v) if isinstance(v, list) else v) for k, v in st.items()}

    def is_busy(self, inst) -> bool:
        iid = _iid(inst)
        lk = self._lock_for(iid)
        if lk.acquire(False):
            lk.release()
            return False
        return True

    def _probe(self, inst: dict) -> bool:
        fn = self._probe_fn
        if fn is None:
            from app import api_base as apib
            fn = apib.probe
        try:
            return bool(fn(inst))
        except Exception:                   # noqa: BLE001 - 探针异常 = 未就绪
            return False

    def _sleep(self, seconds: float, cancel=None) -> bool:
        """可打断的睡眠；返回 False 表示被取消"""
        left = float(seconds)
        while left > 0:
            if cancel is not None and cancel():
                return False
            step = min(0.1, left)
            self._sleep_fn(step)
            left -= step
        return not (cancel is not None and cancel())

    def _ssh_session(self, inst: dict):
        if self._session_factory is not None:
            return self._session_factory(inst)
        if self._mgr is None:
            # 懒建在锁内，避免两个线程各建一个池（会各开一条 SSH 会话给同一实例）
            with self._meta:
                if self._mgr is None:
                    from app import remote_ssh
                    if self._transport_factory is not None or self._has_custom_log:
                        # 注入了工厂 / 日志 → 自建池（测试与 DI 路径）
                        self._mgr = remote_ssh.SshManager(
                            transport_factory=self._transport_factory, log=self._log)
                    else:
                        # 生产路径共用模块级单例：和「测试 SSH」用同一条空闲会话，
                        # 不要为同一实例开两条连接（ARCH §3.2 每实例一条空闲会话）
                        self._mgr = remote_ssh.manager
        return self._mgr.session(inst)

    @staticmethod
    def _emit(cb, text: str) -> None:
        if cb is None:
            return
        try:
            cb(text)
        except Exception:                   # noqa: BLE001 - 回调异常不打断编排
            pass

    # ------------------------------------------------------------ 通道
    def available_channels(self, inst: dict, action: str = "start") -> list:
        """能用的启停通道（UI 用它决定按钮能不能点 / 是否显示通道下拉）

        · ssh：主机 + 用户名 + **对应动作的命令** 齐了才算
          （start 看 ssh_start_cmd，stop 看 ssh_stop_cmd；PRD A4 的空值拦截在 _save）
        · cloud：**对应动作的钩子 URL** 非空才算（开机看 cloud_start，关机看 cloud_stop）
        """
        out = []
        i = inst or {}
        stop = str(action or "").lower() == "stop"
        cmd_key = "ssh_stop_cmd" if stop else "ssh_start_cmd"
        if str(i.get("ssh_host") or "").strip() \
                and str(i.get("ssh_user") or "").strip() \
                and str(i.get(cmd_key) or "").strip():
            out.append(CH_SSH)
        if self._hook_url(i, "stop" if stop else "start"):
            out.append(CH_CLOUD)
        return out

    @staticmethod
    def _hook_url(inst: dict, action: str = "start") -> str:
        """取对应动作的钩子 URL（空 = 该通道未配置）"""
        key = "cloud_stop" if str(action or "").lower() == "stop" else "cloud_start"
        block = _chan_of(inst, key)
        return str(block.get("url") or "").strip()

    def insecure_reason(self, inst: dict) -> str:
        """http 明文的提示文案素材（PRD D1）：返回生效基址，非明文/已忽略返回空串

        判定用**生效基址**：隧道激活时是 127.0.0.1 → 不告警（隧道本身就是加密的）。
        """
        if bool((inst or {}).get("insecure_warn_muted")):
            return ""
        try:
            from app.api_base import effective_base_info
            info = effective_base_info(inst)
        except Exception:                   # noqa: BLE001
            return ""
        return str(info.get("base") or "") if info.get("is_insecure") else ""

    # ------------------------------------------------------------ 启动
    def start(self, inst: dict, channel: str = CH_SSH, *, on_state=None,
              on_log=None, cancel=None) -> OpsResult:
        """远程启动：连接 → 下发命令 → 轮询就绪

        抛 `OpsError("busy")` / `OpsError("no_channel")` 属**前置检查**；
        其余失败都在返回值的 `err_kind` 里（调用方据此查文案 + 贴 last_lines）。
        """
        iid = _iid(inst)
        chan = self._check_channel(inst, channel)
        lock = self._lock_for(iid)
        if not lock.acquire(False):
            raise OpsError(ERR_BUSY, "该实例已有一次远程操作在进行，等它结束再试")
        res = OpsResult(chan)
        try:
            if chan == CH_CLOUD:
                return self._cloud_start(inst, res, iid, on_state=on_state,
                                         on_log=on_log, cancel=cancel)
            self._set_state(iid, ST_CONNECTING, chan)
            self._emit(on_state, "正在连接 SSH…")
            # 闸 ①：预探针（本来就在跑 → 记 pre_ready，绝不谎报启动成功）
            res.pre_ready = self._probe(inst)
            if res.pre_ready:
                self._emit(on_log, "实例当前已经可达（预探针就绪）。仍会执行启动命令，"
                                   "但不会因为退出码 0 就报「启动成功」。")
            try:
                sess = self._ssh_session(inst)
            except Exception as e:          # noqa: BLE001 - SshError 或注入桩的异常
                return self._fail(res, iid, _kind_of(e), _detail_of(e))
            if cancel is not None and cancel():
                return self._cancelled(res, iid)
            self._set_state(iid, ST_EXECUTING, chan)
            self._emit(on_state, "命令已下发…")
            cmd = str((inst or {}).get("ssh_start_cmd") or "")
            _exec_lines = []                 # 命令输出（E4 超时要贴最后 20 行）

            def _on_cmd_line(text: str) -> None:
                _exec_lines.append(text)
                self._emit(on_log, text)

            try:
                er = sess.exec_stream(cmd, _on_cmd_line, cancel,
                                      settle_timeout=self._settle)
            except Exception as e:          # noqa: BLE001
                return self._fail(res, iid, _kind_of(e), _detail_of(e))
            res.code = er.code
            res.detached = er.detached
            if er.cancelled:
                return self._cancelled(res, iid)
            if er.err_kind == "sudo" or getattr(er, "err_kind", "") == ERR_SUDO:
                res.last_lines = er.tail(TAIL_LINES)
                return self._fail(res, iid, ERR_SUDO,
                                  "命令需要交互式 sudo 密码，ComfyHub 不做交互式提权")
            if not er.detached and er.code not in (0, None):
                res.last_lines = er.tail(TAIL_LINES)
                # 闸 ②：退出码只作快速失败信号
                return self._fail(res, iid, ERR_CMD,
                                  _stderr_hint(res.last_lines) or f"退出码 {er.code}")
            # 闸 ③（在 wait_ready 里）：就绪轮询 + 快速就绪复核
            self._set_state(iid, ST_WAITING_READY, chan)
            self._emit(on_state, "命令已执行，等待就绪 0 秒（最多 5 分钟）")
            wr = self.wait_ready(inst, self._now() + self._ready_timeout,
                                 cancel=cancel, on_state=on_state,
                                 pre_ready=res.pre_ready, on_log=on_log,
                                 exec_lines=_exec_lines)
            wr.channel = chan
            wr.pre_ready = res.pre_ready
            wr.code = res.code
            wr.detached = res.detached
            if wr.ok:
                self._set_state(iid, ST_READY, chan)
                self._emit(on_state, "✅ 已就绪")
            else:
                self._set_state(iid, ST_FAILED, chan, wr.err_kind)
                self._set_lines(iid, wr.last_lines)
            return wr
        finally:
            lock.release()

    # ------------------------------------------------------------ 云通道
    def _send_hook(self, inst: dict, action: str = "start") -> dict:
        """打一次云平台钩子；失败抛 `HookError`（kind 原样透传给 UI）"""
        from app import cloud_hooks
        key = "cloud_stop" if str(action or "").lower() == "stop" else "cloud_start"
        block = cloud_hooks.normalize_block(_chan_of(inst, key))
        fn = self._hook_send or cloud_hooks.send
        return fn(block, timeout=self._hook_timeout)

    def _hook_detail(self, inst: dict, action: str = "start") -> str:
        """日志用的请求摘要（**绝不打请求头**：里面有 Token，PRD 边界 #8 同理）"""
        key = "cloud_stop" if str(action or "").lower() == "stop" else "cloud_start"
        b = _chan_of(inst, key)
        method = str(b.get("method") or "POST").upper()
        return f"{method} {str(b.get('url') or '').strip()}"

    def _cloud_start(self, inst: dict, res: OpsResult, iid: str, *,
                     on_state=None, on_log=None, cancel=None) -> OpsResult:
        """云通道启动：发开机请求 → 2xx 只算「受理」→ 照样轮询就绪（PRD 5.2.2）

        闸 ① 同样有效：预探针已就绪时仍发请求（用户点了就发），但结果里
        `pre_ready=True`，UI 不会因为 2xx 就报「启动成功」。
        """
        res.pre_ready = self._probe(inst)
        self._emit(on_state, TXT_CLOUD_STARTING)
        self._emit(on_log, "请求：" + self._hook_detail(inst, "start"))
        try:
            r = self._send_hook(inst, "start")
        except Exception as e:              # noqa: BLE001 - HookError 或注入桩异常
            from app import cloud_hooks
            kind = cloud_hooks.classify(e)
            return self._fail(res, iid, kind,
                              str(getattr(e, "detail", "") or e)[:300])
        status = int((r or {}).get("status") or 0)
        body = str((r or {}).get("text") or "")[:200]
        self._emit(on_log, f"响应：HTTP {status} {body}".rstrip())
        if cancel is not None and cancel():
            return self._cancelled(res, iid)
        # 2xx 只表示「平台受理了」，真正的成功仍然只看探针（闸 ②/③ 同 SSH）
        self._set_state(iid, ST_WAITING_READY, res.channel)
        timeout_s = self._cloud_timeout(inst)
        self._set_lines(iid, [])
        # PRD 5.2.2 第 4 步：状态行立刻改成「平台已受理，等待实例就绪 0 秒…」
        # （不能等第一次轮询才改：探针立刻通时用户会看不到这句）
        self._emit(on_state, TXT_CLOUD_ACCEPTED.format(n=0))
        wr = self.wait_ready(inst, self._now() + timeout_s,
                             cancel=cancel, on_state=on_state,
                             pre_ready=res.pre_ready, on_log=on_log,
                             exec_lines=[], accepted=True)
        wr.channel = res.channel
        wr.pre_ready = res.pre_ready
        wr.code = status
        if wr.ok:
            self._set_state(iid, ST_READY, res.channel)
            self._emit(on_state, "✅ 已就绪")
        else:
            self._set_state(iid, ST_FAILED, res.channel, wr.err_kind)
        return wr

    @staticmethod
    def _cloud_timeout(inst: dict) -> float:
        """就绪等待上限（`cloud_start_timeout_s`，本版固定 300，PRD A7）"""
        try:
            v = float((inst or {}).get("cloud_start_timeout_s") or READY_TIMEOUT)
        except (TypeError, ValueError):
            v = READY_TIMEOUT
        return max(1.0, v)

    # ------------------------------------------------------------ 就绪轮询
    def wait_ready(self, inst: dict, deadline: float, cancel=None, on_state=None,
                   pre_ready: bool = True, on_log=None,
                   exec_lines=None, accepted: bool = False) -> OpsResult:
        """轮询 `/system_stats` 直到就绪 / 超时 / 取消（每 poll_interval 一次）

        exec_lines：命令输出（超时时取尾部 20 行放进 last_lines，PRD E4）。
        accepted：云通道专用——平台已经受理，状态行改 PRD 5.2.2 第 4 步的说法。
        """
        res = OpsResult()
        t0 = self._now()
        rechecked = False
        while True:
            if cancel is not None and cancel():
                res.ok = False
                res.cancelled = True
                res.err_kind = ERR_CANCELLED
                res.state = ST_CANCELLED
                return res
            if self._probe(inst):
                elapsed = self._now() - t0
                if elapsed < self._quick_window and not pre_ready and not rechecked:
                    # 闸 ③：太快就绪 → 3 秒后复核，仍就绪才接受
                    rechecked = True
                    self._emit(on_log, f"就绪异常快（{elapsed:.1f}s），"
                                       f"{self._recheck:g} 秒后复核探针确认")
                    self._emit(on_state, "正在复核就绪状态…")
                    if not self._sleep(self._recheck, cancel):
                        continue                 # 回到循环顶部按取消处理
                    if not self._probe(inst):
                        self._emit(on_log, "复核未通过（探针又不可达），继续等待")
                        continue
                    elapsed = self._now() - t0
                res.ok = True
                res.state = ST_READY
                res.elapsed = elapsed
                return res
            if self._now() >= deadline:
                res.ok = False
                res.state = ST_FAILED
                res.err_kind = ERR_NOT_READY
                res.elapsed = self._now() - t0
                # E4 文案要贴「远端输出最后 20 行」
                res.last_lines = list(exec_lines or [])[-TAIL_LINES:]
                return res
            waited = max(0.0, self._now() - t0)
            if accepted:
                # PRD 5.2.2 第 4 步逐字（带秒数 → 拆片段，整句不入 i18n 词表）
                self._emit(on_state, TXT_CLOUD_ACCEPTED.format(n=int(waited)))
            else:
                self._emit(on_state, f"等待就绪 {int(waited)} 秒（最多 5 分钟）")
            if not self._sleep(self._poll, cancel):
                continue                         # 顶部按取消处理

    # ------------------------------------------------------------ 停止
    def stop(self, inst: dict, channel: str = CH_SSH, *, on_state=None,
             on_log=None, cancel=None) -> OpsResult:
        """远程停止：下发停止命令 → 轮询确认不可达

        「停止等待」中途取消只中止轮询，**不杀远端进程**（PRD 对话框规格）。
        """
        iid = _iid(inst)
        chan = self._check_channel(inst, channel, action="stop")
        lock = self._lock_for(iid)
        if not lock.acquire(False):
            raise OpsError(ERR_BUSY, "该实例已有一次远程操作在进行，等它结束再试")
        res = OpsResult(chan)
        try:
            if chan == CH_CLOUD:
                return self._cloud_stop(inst, res, iid, on_state=on_state,
                                        on_log=on_log, cancel=cancel)
            self._set_state(iid, ST_STOPPING, chan)
            self._emit(on_state, "正在连接 SSH…")
            try:
                sess = self._ssh_session(inst)
            except Exception as e:          # noqa: BLE001
                return self._fail(res, iid, _kind_of(e), _detail_of(e))
            if cancel is not None and cancel():
                return self._cancelled(res, iid)
            self._emit(on_state, "正在发送停止命令…")
            cmd = str((inst or {}).get("ssh_stop_cmd") or "")
            try:
                er = sess.exec_stream(cmd, lambda s: self._emit(on_log, s),
                                      cancel, settle_timeout=min(self._settle, 15.0))
            except Exception as e:          # noqa: BLE001
                return self._fail(res, iid, _kind_of(e), _detail_of(e))
            res.code = er.code
            res.detached = er.detached
            if er.cancelled:
                return self._cancelled(res, iid)
            if er.err_kind == ERR_SUDO:
                res.last_lines = er.tail(TAIL_LINES)
                return self._fail(res, iid, ERR_SUDO,
                                  "停止命令需要交互式 sudo 密码，ComfyHub 不做交互式提权")
            self._emit(on_state, "命令已执行，正在确认实例已停止…")
            deadline = self._now() + self._stop_timeout
            while self._now() < deadline:
                if cancel is not None and cancel():
                    # 只中止轮询：远端进程可能已经停了，也可能还在停，不替用户做决定
                    return self._cancelled(res, iid)
                if not self._probe(inst):
                    res.ok = True
                    res.state = ST_IDLE
                    return res
                waited = self._stop_timeout - max(0.0, deadline - self._now())
                self._emit(on_state, f"等待实例停止 {int(waited)} 秒")
                if not self._sleep(self._poll, cancel):
                    continue
            res.last_lines = er.tail(TAIL_LINES)
            return self._fail(res, iid, ERR_STILL_REACHABLE,
                              "命令返回成功，但实例仍然可达，停止命令可能没匹配到进程")
        finally:
            lock.release()

    # ------------------------------------------------------------ 云通道停止
    def _cloud_stop(self, inst: dict, res: OpsResult, iid: str, *,
                    on_state=None, on_log=None, cancel=None) -> OpsResult:
        """云通道停止：发关机请求 → 轮询确认不可达（与 SSH 停止同一套确认逻辑）"""
        self._set_state(iid, ST_STOPPING, res.channel)
        self._emit(on_state, TXT_CLOUD_STOPPING)
        self._emit(on_log, "请求：" + self._hook_detail(inst, "stop"))
        try:
            r = self._send_hook(inst, "stop")
        except Exception as e:              # noqa: BLE001
            from app import cloud_hooks
            kind = cloud_hooks.classify(e)
            return self._fail(res, iid, kind,
                              str(getattr(e, "detail", "") or e)[:300])
        status = int((r or {}).get("status") or 0)
        self._emit(on_log, f"响应：HTTP {status} "
                           f"{str((r or {}).get('text') or '')[:200]}".rstrip())
        res.code = status
        if cancel is not None and cancel():
            return self._cancelled(res, iid)
        self._emit(on_state, TXT_CLOUD_STOP_SENT)
        deadline = self._now() + self._stop_timeout
        while self._now() < deadline:
            if cancel is not None and cancel():
                return self._cancelled(res, iid)
            if not self._probe(inst):
                res.ok = True
                res.state = ST_IDLE
                return res
            waited = self._stop_timeout - max(0.0, deadline - self._now())
            self._emit(on_state, f"等待实例停止 {int(waited)} 秒")
            if not self._sleep(self._poll, cancel):
                continue
        return self._fail(res, iid, ERR_STILL_REACHABLE,
                          "平台已受理关机，但实例仍然可达，可能还在关机过程中")

    # ------------------------------------------------------------ 内部
    def _check_channel(self, inst: dict, channel: str,
                       action: str = "start") -> str:
        chan = str(channel or CH_SSH).strip().lower()
        if chan not in CHANNELS:
            raise OpsError(ERR_NO_CHANNEL, f"不认识的通道：{channel}")
        if chan not in self.available_channels(inst, action=action):
            if chan == CH_CLOUD:
                raise OpsError(ERR_NO_CHANNEL,
                               "这个实例还没配云平台开关机接口（要把请求 URL 填上）")
            raise OpsError(ERR_NO_CHANNEL,
                           "这个实例还没配好远程启动方式（主机/用户名/启动命令）")
        return chan

    def _fail(self, res: OpsResult, iid: str, kind: str, detail: str = "") -> OpsResult:
        res.ok = False
        res.state = ST_FAILED
        res.err_kind = str(kind or "unknown")
        res.detail = str(detail or "")
        self._set_state(iid, ST_FAILED, res.channel, res.err_kind)
        self._set_lines(iid, res.last_lines)
        return res

    def _cancelled(self, res: OpsResult, iid: str) -> OpsResult:
        res.ok = False
        res.state = ST_CANCELLED
        res.cancelled = True
        res.err_kind = ERR_CANCELLED
        self._set_state(iid, ST_CANCELLED, res.channel, ERR_CANCELLED)
        return res


# ---------------------------------------------------------------- 异常归一
def _kind_of(exc: BaseException) -> str:
    kind = getattr(exc, "kind", "")
    return str(kind or "unknown")


def _detail_of(exc: BaseException) -> str:
    detail = getattr(exc, "detail", "")
    return str(detail or str(exc) or "")[:300]


def _stderr_hint(lines) -> str:
    """E3 文案要贴的「stderr 前 200 字」——这里用尾部第一行能用的证据"""
    for ln in list(lines or [])[-5:]:
        if str(ln).strip():
            return str(ln).strip()[:200]
    return ""


# 模块级单例：UI（第二批）直接用 `remote_ops.ops`；测试自己 new 一个新的
ops = RemoteOps()
