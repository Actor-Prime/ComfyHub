# -*- coding: utf-8 -*-
"""SSH 通道：连接 / 认证失败分类 / 流式执行 / direct-tcpip（2026-12-01，0.8.40）

职责边界（ARCH §1.2 分层约束）：
  · 本模块**只做传输**：连接、认证、执行命令、开转发通道、主机指纹校验；
  · **禁止 import PySide6**（离屏 / 无 Qt 环境必须能跑单测）；
  · **禁止 import proc_manager**（避免层反转：procs.invalidate 由 main_window 编排）；
  · 不认识「实例状态」「就绪」这些业务概念，那些在 remote_ops / tunnel 里。

四个必须讲清的坑（都付过代价）：
  1. **认证失败要分五类**（PRD 5.1.3 E1/E2/E2p/E2k/E2b）。用户看到「认证失败」四个字
     没有任何信息量——是密码错、口令错、远端不认这把密钥，还是根本没读到文件？
     分类信息全部落在 `SshError.kind` 上，UI 只按 kind 查文案，**绝不匹配错误字符串**。
  2. **`nohup` 后没重定向 stdout 时 channel 永不 EOF**（远端还在写同一个 tty）。
     所以 `exec_stream` 有 `settle_timeout`：超过该时长没有新输出就 detach 到后台
     排空线程，主流程继续去轮询就绪（ARCH §11 风险 4）。不 detach 就是「假死」。
  3. **selectors 读 channel**：paramiko 的 `Channel` 支持 `fileno()`，用 selectors 等
     可读能同时兼顾「不带数据」「要取消」「要超时」三种退出条件；`finally` 必须
     `chan.close()`，否则每次失败都漏一个远端会话。
  4. **主机指纹只认我们自己的 known_hosts.json**（见 remote_fingerprint），
     绝不读写 `~/.ssh/known_hosts`。

会话策略（ARCH §0 结论 3）：每实例两条会话
  · **空闲会话**：`SshManager` 池里那条，「测试 SSH」和启停命令共用，空闲 120s 自动关；
  · **隧道独占会话**：`tunnel.TunnelJob` 自己 `SshSession(inst)` 建一条，隧道重连
    只会关掉它自己那条，不会打断正在跑的启动命令。
"""
import selectors
import socket
import threading
import time
from collections import deque

from app import remote_fingerprint

# ---------------------------------------------------------------- 错误分类枚举
# 跨文件唯一约定（ARCH §3.3）：UI 只按 kind 查文案，新增 kind 必须同时补 i18n。
KIND_UNREACHABLE = "unreachable"      # E1 连不上
KIND_AUTH_PASSWORD = "auth_password"  # E2 密码/用户名错
KIND_AUTH_PASSPHRASE = "auth_passphrase"  # E2p 私钥口令错
KIND_AUTH_KEY = "auth_key"            # E2k 远端不接受这把密钥
KIND_KEY_MISSING = "key_missing"      # E2b 读不到密钥文件（含 .ppk）
KIND_FINGERPRINT = "fingerprint"      # E5 主机指纹变了
KIND_SUDO = "sudo"                    # E6 需要交互式提权
KIND_TIMEOUT = "timeout"              # 兜底：超时
KIND_UNKNOWN = "unknown"              # 兜底：其它

CONNECT_KINDS = (KIND_UNREACHABLE, KIND_AUTH_PASSWORD, KIND_AUTH_PASSPHRASE,
                 KIND_AUTH_KEY, KIND_KEY_MISSING, KIND_FINGERPRINT,
                 KIND_SUDO, KIND_TIMEOUT, KIND_UNKNOWN)

# ExecResult.err_kind 取值（'' = 命令正常结束）
EXEC_OK = ""
EXEC_CMD = "cmd"        # 退出码非 0（E3）
EXEC_SUDO = "sudo"      # 输出里出现 sudo 提权要求（E6）

# sudo 需要交互式密码的特征串（远端输出里出现即判定，PRD E6）
_SUDO_MARKS = (
    "sudo: no tty present",
    "sudo: a terminal is required",
    "sudo: a password is required",
    "sudo: unable to",
    "[sudo] password for",
)

DEFAULT_PORT = 22
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_SETTLE_TIMEOUT = 30.0
KEEPALIVE_S = 15
MAX_LINES = 5000            # 尾部截断（ARCH §11 风险 5）
MAX_LINE_CHARS = 2000       # 单行截断


class SshError(Exception):
    """SSH 层统一异常：`kind` 是稳定枚举，`detail` 是给人看的一句话"""

    def __init__(self, kind: str, detail: str = ""):
        self.kind = str(kind or KIND_UNKNOWN)
        self.detail = str(detail or "")
        super().__init__(f"{self.kind}: {self.detail}" if self.detail else self.kind)

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}" if self.detail else self.kind


class ExecResult:
    """一次远端命令执行的结果

    code=None 表示拿不到退出码（detached / cancelled / timed_out）。
    **退出码只作为「快速失败」信号**：code==0 不代表实例就绪，就绪由 remote_ops
    的探针轮询判定（PRD A7 / ARCH §5.1 三道闸）。
    """

    __slots__ = ("code", "detached", "cancelled", "timed_out", "lines",
                 "err_kind", "detail")

    def __init__(self):
        self.code = None
        self.detached = False
        self.cancelled = False
        self.timed_out = False
        self.lines = deque(maxlen=MAX_LINES)
        self.err_kind = EXEC_OK
        self.detail = ""

    def tail(self, n: int = 20) -> list:
        """尾部 n 行（E4 文案要贴最后 20 行远端输出）"""
        items = list(self.lines)
        return items[-int(n):] if n else []

    def __repr__(self) -> str:
        return (f"ExecResult(code={self.code}, detached={self.detached}, "
                f"cancelled={self.cancelled}, err_kind={self.err_kind!r}, "
                f"lines={len(self.lines)})")


# ---------------------------------------------------------------- 异常分类工具
def _exc_names(exc: BaseException) -> set:
    """异常类名集合（含父类）。用类名而不是 isinstance(paramiko.X)，
    这样本模块在 paramiko 缺失时仍能导入（离屏环境友好），也不怕版本改名。"""
    try:
        return {c.__name__ for c in type(exc).__mro__}
    except Exception:                       # noqa: BLE001
        return set()


def _conn_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:400]


def classify_connect_error(exc: BaseException) -> tuple:
    """TCP 连接阶段异常 → (kind, detail)"""
    names = _exc_names(exc)
    txt = str(exc)
    low = txt.lower()
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in low \
            or "timeout" in low:
        return KIND_TIMEOUT, _conn_text(exc)
    if "getaddrinfo" in low or "name or service not known" in low \
            or "nodename nor servname" in low:
        return KIND_UNREACHABLE, f"域名解析失败：{_conn_text(exc)}"
    if "refused" in low or "10061" in txt or "unreachable" in low \
            or "no route to host" in low or "10065" in txt:
        return KIND_UNREACHABLE, _conn_text(exc)
    if "banner" in low or "connect" in low.lower():
        return KIND_UNREACHABLE, _conn_text(exc)
    if "NoValidConnectionsError" in names:
        return KIND_UNREACHABLE, _conn_text(exc)
    if "AuthenticationException" in names or "BadAuthenticationType" in names:
        return KIND_AUTH_PASSWORD, _conn_text(exc)
    return KIND_UNKNOWN, _conn_text(exc)


def classify_auth_error(exc: BaseException, method: str) -> tuple:
    """认证阶段异常 → (kind, detail)；method ∈ {'password','key'}"""
    names = _exc_names(exc)
    if "PasswordRequiredException" in names:
        return KIND_AUTH_PASSPHRASE, _conn_text(exc)
    if "BadHostKeyException" in names:
        return KIND_FINGERPRINT, _conn_text(exc)
    if "AuthenticationException" in names or "BadAuthenticationType" in names \
            or "PartialAuthentication" in names:
        return (KIND_AUTH_PASSWORD if method == "password"
                else KIND_AUTH_KEY), _conn_text(exc)
    low = str(exc).lower()
    if "passphrase" in low or "password" in low and "protected" in low:
        return KIND_AUTH_PASSPHRASE, _conn_text(exc)
    return classify_connect_error(exc)


def classify_key_error(exc: BaseException, path: str, has_passphrase: bool) -> tuple:
    """加载私钥文件阶段异常 → (kind, detail)

    为什么要单独分类：`paramiko.PKey.from_path` 对「加密了没给口令」「口令错」
    「根本不是私钥」「PuTTY .ppk」抛的都是 ValueError/TypeError，靠消息区分不可靠，
    所以再看两个已知条件：有没有给口令、文件后缀/内容是不是 .ppk。
    """
    p = str(path or "")
    names = _exc_names(exc)
    low = str(exc).lower()
    if "PasswordRequiredException" in names:
        return KIND_AUTH_PASSPHRASE, f"私钥需要口令：{p}"
    if "UnknownKeyType" in names:
        return KIND_KEY_MISSING, f"不认识的私钥格式：{p}"
    if isinstance(exc, FileNotFoundError):
        return KIND_KEY_MISSING, f"文件不存在：{p}"
    if isinstance(exc, PermissionError):
        return KIND_KEY_MISSING, f"没有读取权限：{p}"
    if isinstance(exc, OSError):
        return KIND_KEY_MISSING, f"读取失败：{p}（{_conn_text(exc)}）"
    if p.lower().endswith(".ppk") or "putty" in low:
        return KIND_KEY_MISSING, f"这是 PuTTY 的 .ppk，本版不支持：{p}"
    if has_passphrase:
        return KIND_AUTH_PASSPHRASE, f"私钥口令不对：{p}"
    if "password-protected" in low or "encrypted" in low:
        return KIND_AUTH_PASSPHRASE, f"私钥加密了，需要口令：{p}"
    return KIND_KEY_MISSING, f"不像是 OpenSSH 私钥：{p}（{_conn_text(exc)}）"


def detect_sudo(text: str) -> bool:
    """远端输出里是否出现「需要交互式提权」的特征（PRD E6）"""
    low = str(text or "").lower()
    return any(m in low for m in _SUDO_MARKS)


def default_transport_factory(host: str, port: int, timeout: float):
    """真实环境：TCP 连上去，返回一个**尚未握手**的 paramiko.Transport

    刻意不用 `SSHClient`：我们需要在「握手完成、还没发任何凭据」的窗口里校验主机
    指纹（`Transport.get_remote_server_key()`），`connect()` 一步到位就没这个窗口了。
    """
    import paramiko                        # 延迟导入：本模块在无 paramiko 时也能导入
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        return paramiko.Transport(sock)
    except Exception:                      # noqa: BLE001
        try:
            sock.close()
        except OSError:
            pass
        raise


def _as_port(value) -> int:
    try:
        p = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return p if 1 <= p <= 65535 else DEFAULT_PORT


def _safe_close(obj) -> None:
    if obj is None:
        return
    try:
        obj.close()
    except Exception:                      # noqa: BLE001 - 关闭失败不影响主流程
        pass


class SshSession:
    """一条 SSH 会话（一个 Transport + 若干 Channel）

    生命周期：`connect()` → `exec_stream()` / `open_direct_tcpip()` → `close()`。
    任何阶段失败都抛 `SshError`，`kind` 决定 UI 文案。
    """

    def __init__(self, inst: dict, *, transport_factory=None,
                 clock=time.monotonic, log=None):
        self.inst = dict(inst or {})
        self._factory = transport_factory or default_transport_factory
        self._clock = clock
        self._log = log or (lambda _s: None)
        self._tr = None
        self._lock = threading.RLock()
        self._last_used = self._clock()
        self._last_error = ("", "")

    # ---------------- 基本信息 ----------------
    @property
    def host(self) -> str:
        return str(self.inst.get("ssh_host") or "").strip()

    @property
    def port(self) -> int:
        return _as_port(self.inst.get("ssh_port"))

    @property
    def user(self) -> str:
        return str(self.inst.get("ssh_user") or "").strip()

    @property
    def last_error(self) -> tuple:
        return self._last_error

    def touch(self) -> None:
        """标记刚用过（空闲池按它算 TTL）"""
        self._last_used = self._clock()

    def idle_for(self) -> float:
        return max(0.0, self._clock() - self._last_used)

    def config_error(self) -> tuple:
        """(kind, 说明)；kind 为空串表示连接参数齐全

        为什么不一律归 unreachable：缺密钥路径属于「读不到密钥文件」（PRD E2b），
        归到 E1「连不上」会把用户引到错误的排查方向。
        """
        if not self.host:
            return KIND_UNREACHABLE, "还没填 SSH 主机地址"
        if not self.user:
            return KIND_UNREACHABLE, "还没填 SSH 用户名"
        auth = str(self.inst.get("ssh_auth") or "password").lower()
        if auth == "key" and not str(self.inst.get("ssh_key_path") or "").strip():
            return KIND_KEY_MISSING, "认证方式选了「密钥文件」，但没填密钥路径"
        return "", ""

    def config_missing(self) -> str:
        """配置缺项说明（空串 = 连接参数齐全）。UI 用它决定「按钮能不能点」。"""
        return self.config_error()[1]

    # ---------------- 连接 ----------------
    def connect(self, timeout: float | None = None) -> None:
        """建连 + 握手 + 指纹校验 + 认证；失败抛 SshError(kind)

        顺序是刻意的：**指纹校验在认证之前**，绝不把密码发给一个身份存疑的主机。
        """
        tmo = float(timeout or DEFAULT_CONNECT_TIMEOUT)
        kind, missing = self.config_error()
        if kind:
            self._raise(kind, missing)
        pkey = self._load_key()            # 可能抛 key_missing / auth_passphrase
        host, port = self.host, self.port
        try:
            tr = self._factory(host, port, tmo)
        except Exception as e:             # noqa: BLE001
            self._raise(*classify_connect_error(e))
            return                          # pragma: no cover - _raise 必抛
        try:
            self._handshake(tr, tmo)
            self._check_fingerprint(tr, host, port)
            self._authenticate(tr, pkey, tmo)
        except SshError:
            _safe_close(tr)
            raise
        except Exception as e:             # noqa: BLE001
            _safe_close(tr)
            self._raise(*classify_connect_error(e))
            return                          # pragma: no cover
        try:
            tr.set_keepalive(KEEPALIVE_S)
        except Exception:                  # noqa: BLE001
            pass
        with self._lock:
            self._tr = tr
        self.touch()
        self._last_error = ("", "")
        self._log(f"已连接 {host}:{port}（{self.user}）")

    def _raise(self, kind: str, detail: str = ""):
        self._last_error = (kind, detail)
        raise SshError(kind, detail)

    def _handshake(self, tr, timeout: float) -> None:
        """只做 SSH 协议握手（不做认证），好让指纹校验插在中间"""
        fn = getattr(tr, "start_client", None)
        if callable(fn):
            fn(timeout=timeout)

    def _load_key(self):
        """按 ssh_auth 加载私钥；口令错 / 文件读不到分别归类"""
        auth = str(self.inst.get("ssh_auth") or "password").lower()
        if auth != "key":
            return None
        path = str(self.inst.get("ssh_key_path") or "").strip()
        if not path:
            self._raise(KIND_KEY_MISSING, "没填密钥文件路径")
        passphrase = str(self.inst.get("ssh_key_passphrase") or "")
        if path.lower().endswith(".ppk"):
            self._raise(KIND_KEY_MISSING,
                        f"这是 PuTTY 的 .ppk，本版只支持 OpenSSH 私钥：{path}")
        try:
            import paramiko
        except Exception as e:             # noqa: BLE001
            self._raise(KIND_UNKNOWN, f"缺少 paramiko 依赖：{e}")
        try:
            if passphrase:
                return paramiko.PKey.from_path(path, passphrase.encode("utf-8"))
            return paramiko.PKey.from_path(path)
        except Exception as e:             # noqa: BLE001
            self._raise(*classify_key_error(e, path, bool(passphrase)))
            return None                     # pragma: no cover

    def _check_fingerprint(self, tr, host: str, port: int) -> None:
        try:
            key = tr.get_remote_server_key()
        except Exception as e:             # noqa: BLE001
            self._raise(KIND_UNKNOWN, f"拿不到主机密钥：{_conn_text(e)}")
            return                          # pragma: no cover
        res = remote_fingerprint.verify(host, port, key)
        status = res.get("status")
        if status == remote_fingerprint.ST_CHANGED:
            self._raise(KIND_FINGERPRINT,
                        f"{host}:{port} 的指纹从 {res.get('expected')} 变成了 "
                        f"{res.get('fingerprint')}")
        elif status == remote_fingerprint.ST_NEW:
            self._log(f"已记录 {host}:{port} 的主机指纹 {res.get('fingerprint')}")
        elif status == remote_fingerprint.ST_ERROR:
            # 拿不到密钥指纹不阻断连接（极少数老服务端/代理不返回），但要留痕
            self._log(f"warn: {host}:{port} 未能取得主机指纹，本次跳过校验")

    def _authenticate(self, tr, pkey, timeout: float) -> None:
        auth = str(self.inst.get("ssh_auth") or "password").lower()
        user = self.user
        if auth == "key":
            method = "key"
            fn = getattr(tr, "auth_publickey", None)
            try:
                if callable(fn):
                    fn(user, pkey)
                else:
                    tr.connect(username=user, pkey=pkey, timeout=timeout,
                               banner_timeout=timeout, auth_timeout=timeout)
            except Exception as e:         # noqa: BLE001
                self._raise(*classify_auth_error(e, method))
                return                      # pragma: no cover
        else:
            method = "password"
            password = str(self.inst.get("ssh_password") or "")
            fn = getattr(tr, "auth_password", None)
            try:
                if callable(fn):
                    fn(user, password)
                else:
                    tr.connect(username=user, password=password, timeout=timeout,
                               banner_timeout=timeout, auth_timeout=timeout)
            except Exception as e:         # noqa: BLE001
                self._raise(*classify_auth_error(e, method))
                return                      # pragma: no cover
        ok = True
        try:
            checker = getattr(tr, "is_authenticated", None)
            if callable(checker):
                ok = bool(checker())
        except Exception:                  # noqa: BLE001
            ok = True
        if not ok:
            self._raise(KIND_AUTH_PASSWORD if method == "password" else KIND_AUTH_KEY,
                        "认证未通过（服务端未确认登录）")

    # ---------------- 探活 / 复用 ----------------
    def is_alive(self) -> bool:
        tr = self._tr
        if tr is None:
            return False
        try:
            return bool(tr.is_active())
        except Exception:                  # noqa: BLE001
            return False

    def ensure_alive(self, timeout: float | None = None) -> None:
        """空闲池复用前调用：死了就重连（配置问题照样抛 kind）"""
        if self.is_alive():
            self.touch()
            return
        self.close()
        self.connect(timeout=timeout)

    def close(self) -> None:
        with self._lock:
            tr, self._tr = self._tr, None
        _safe_close(tr)

    def _require_alive(self):
        if not self.is_alive():
            self._raise(KIND_UNREACHABLE, "SSH 会话已断开")
        return self._tr

    # ---------------- 执行命令（流式） ----------------
    def exec_stream(self, cmd: str, on_line=None, cancel=None,
                    settle_timeout: float = DEFAULT_SETTLE_TIMEOUT,
                    total_timeout: float | None = None) -> ExecResult:
        """执行命令并逐行回调输出。

        settle_timeout：**没有新输出**多久就 detach（不是「总时长」）。用户命令常常是
        `nohup ... &` 且忘了重定向，channel 永不 EOF；用「静默超时」而不是「总时长」
        可以既救这种情况，又不误伤正在刷日志的长命令（ARCH §11 风险 4）。
        total_timeout：可选硬上限（None = 不限制）。
        cancel：callable() -> bool，返回 True 表示用户中断；中止后 channel 会被关闭
        （**不杀远端进程**，PRD 明确）。
        """
        tr = self._require_alive()
        res = ExecResult()
        chan = None
        try:
            chan = tr.open_session(timeout=DEFAULT_CONNECT_TIMEOUT)
        except Exception as e:             # noqa: BLE001
            self._raise(*classify_connect_error(e))
        try:
            try:
                chan.setblocking(0)        # selectors 读循环的前提
            except Exception:              # noqa: BLE001
                pass
            try:
                chan.exec_command(cmd)
            except Exception as e:         # noqa: BLE001
                res.err_kind = KIND_UNKNOWN
                res.detail = _conn_text(e)
                self._emit(res, on_line, f"[执行失败] {res.detail}")
                return res
            self._read_loop(chan, res, on_line, cancel, settle_timeout,
                            total_timeout)
            if res.detached:
                chan = None                # 所有权交给后台排空线程，别在这里关
            return res
        finally:
            _safe_close(chan)
            self.touch()

    def _read_loop(self, chan, res: ExecResult, on_line, cancel,
                   settle_timeout: float, total_timeout: float | None) -> None:
        bufs = {"out": b"", "err": b""}
        t0 = self._clock()
        last_data = t0
        sel = None
        fd = None
        try:
            fd = chan.fileno()
        except Exception:                  # noqa: BLE001
            fd = None
        if fd is not None and fd >= 0:
            try:
                sel = selectors.DefaultSelector()
                sel.register(fd, selectors.EVENT_READ)
            except Exception:              # noqa: BLE001
                sel = None
        try:
            while True:
                now = self._clock()
                if cancel is not None and cancel():
                    res.cancelled = True
                    self._flush(bufs, res, on_line)
                    return
                if total_timeout is not None and (now - t0) >= total_timeout:
                    res.timed_out = True
                    self._flush(bufs, res, on_line)
                    return
                if self._pump(chan, res, on_line, bufs):
                    last_data = now
                if self._is_finished(chan):
                    self._pump(chan, res, on_line, bufs)      # 排空尾巴
                    self._flush(bufs, res, on_line)
                    res.code = self._exit_status(chan)
                    if res.code not in (0, None) and res.err_kind == EXEC_OK:
                        res.err_kind = EXEC_CMD
                    return
                if (now - last_data) >= float(settle_timeout):
                    res.detached = True
                    self._flush(bufs, res, on_line)
                    self._detach(chan, on_line, bufs)
                    return
                self._wait_readable(sel, fd, 0.2)
        finally:
            if sel is not None:
                try:
                    sel.close()
                except Exception:          # noqa: BLE001
                    pass

    def _wait_readable(self, sel, fd, timeout: float) -> None:
        if sel is not None:
            try:
                sel.select(timeout)
                return
            except (OSError, ValueError):
                return
        time.sleep(min(0.05, timeout))

    def _pump(self, chan, res: ExecResult | None, on_line, bufs: dict,
              collect: bool = True) -> int:
        """把当前可读的 stdout/stderr 读干；返回读到的字节数"""
        total = 0
        for ready_name, recv_name, key in (
                ("recv_ready", "recv", "out"),
                ("recv_stderr_ready", "recv_stderr", "err")):
            try:
                if not bool(getattr(chan, ready_name)()):
                    continue
            except Exception:              # noqa: BLE001
                continue
            while True:
                try:
                    chunk = getattr(chan, recv_name)(65536)
                except Exception:          # noqa: BLE001
                    break
                if not chunk:
                    break
                total += len(chunk)
                bufs[key] += chunk
                self._split(bufs, key, res, on_line, collect)
                try:
                    if not bool(getattr(chan, ready_name)()):
                        break
                except Exception:          # noqa: BLE001
                    break
        return total

    def _split(self, bufs: dict, key: str, res, on_line, collect: bool) -> None:
        """按 \\n 切行，残行留在 buffer 里等下一批"""
        data = bufs[key]
        if b"\n" not in data:
            return
        parts = data.split(b"\n")
        bufs[key] = parts.pop()            # 最后一段可能是不完整的行
        for raw in parts:
            line = raw.rstrip(b"\r").decode("utf-8", "replace")
            self._emit(res, on_line, line, collect=collect)

    def _flush(self, bufs: dict, res, on_line, collect: bool = True) -> None:
        """把两个 stream 的残行吐出来（结束/取消/超时时调用）"""
        for key in ("out", "err"):
            data = bufs.get(key) or b""
            bufs[key] = b""
            if not data:
                continue
            for raw in data.split(b"\n"):
                line = raw.rstrip(b"\r").decode("utf-8", "replace")
                if line:
                    self._emit(res, on_line, line, collect=collect)

    def _emit(self, res, on_line, line: str, collect: bool = True) -> None:
        text = line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"
        if collect and res is not None:
            res.lines.append(text)
            if detect_sudo(text):
                res.err_kind = EXEC_SUDO
        if on_line:
            try:
                on_line(text)
            except Exception:              # noqa: BLE001 - UI 回调异常不能打断执行
                pass

    @staticmethod
    def _is_finished(chan) -> bool:
        try:
            if not chan.exit_status_ready():
                return False
        except Exception:                  # noqa: BLE001
            return True
        try:
            if chan.recv_ready() or chan.recv_stderr_ready():
                return False               # 还有残余输出，下一轮再退
        except Exception:                  # noqa: BLE001
            pass
        return True

    @staticmethod
    def _exit_status(chan):
        try:
            if hasattr(chan, "recv_exit_status") and chan.exit_status_ready():
                return int(chan.recv_exit_status())
        except Exception:                  # noqa: BLE001
            pass
        return None

    def _detach(self, chan, on_line, bufs: dict) -> None:
        """把 channel 交给后台 daemon 线程排空（不阻塞主流程、不泄漏 channel）

        注意：后台线程**只往 on_line 回吐输出**，不再改 ExecResult
        —— 主流程此时已经拿着 res 去轮询就绪了，跨线程改同一个对象会埋数据竞争。
        """
        self._log("命令未在预期时间内结束，已转到后台继续收集输出（不影响启动流程）")

        def _bg():
            sel = None
            fd = None
            try:
                fd = chan.fileno()
            except Exception:              # noqa: BLE001
                fd = None
            if fd is not None and fd >= 0:
                try:
                    sel = selectors.DefaultSelector()
                    sel.register(fd, selectors.EVENT_READ)
                except Exception:          # noqa: BLE001
                    sel = None
            try:
                while True:
                    self._pump(chan, None, on_line, bufs, collect=False)
                    if self._is_finished(chan):
                        self._flush(bufs, None, on_line, collect=False)
                        return
                    self._wait_readable(sel, fd, 0.5)
            except Exception:              # noqa: BLE001
                return
            finally:
                if sel is not None:
                    try:
                        sel.close()
                    except Exception:      # noqa: BLE001
                        pass
                _safe_close(chan)

        th = threading.Thread(target=_bg, name="ssh-drain", daemon=True)
        th.start()

    # ---------------- direct-tcpip（隧道用） ----------------
    def open_direct_tcpip(self, remote_host: str, remote_port: int,
                          src_host: str = "127.0.0.1", src_port: int = 0):
        """开一条转发通道（paramiko 的 direct-tcpip），供 tunnel 双侧 relay 用"""
        tr = self._require_alive()
        try:
            chan = tr.open_channel(
                "direct-tcpip", (str(remote_host), int(remote_port)),
                (str(src_host), int(src_port)))
            return chan
        except Exception as e:             # noqa: BLE001
            kind, detail = classify_connect_error(e)
            if "administratively prohibited" in str(e).lower() or \
                    "open failed" in str(e).lower():
                kind = KIND_UNREACHABLE
                detail = f"远端拒绝转发到 {remote_host}:{remote_port}（{detail}）"
            self._raise(kind, detail)
            return None                     # pragma: no cover


class SshManager:
    """每实例一条「空闲会话」的池（测试 SSH / 启停命令共用）

    · 空闲 `idle_ttl`（默认 120s）后由后台 tick 线程关掉，别让连接无限期挂着；
    · 隧道**不用**这个池（它自己建独占会话），所以隧道重连不会打断启动命令；
    · 锁只保护 dict，**连接动作在锁外**（禁止持锁做网络 IO）。
    """

    IDLE_TTL = 120.0
    TICK = 30.0

    def __init__(self, *, transport_factory=None, idle_ttl: float = IDLE_TTL,
                 clock=time.monotonic, tick: float = TICK, log=None):
        self._idle = {}
        self._lock = threading.RLock()
        self._factory = transport_factory
        self._idle_ttl = float(idle_ttl)
        self._clock = clock
        self._tick = float(tick)
        self._log = log or (lambda _s: None)
        self._stop = threading.Event()
        self._thread = None

    # ---------------- 取会话 ----------------
    def session(self, inst: dict, timeout: float | None = None) -> SshSession:
        iid = self._key(inst)
        with self._lock:
            sess = self._idle.get(iid)
        if sess is not None:
            try:
                sess.ensure_alive(timeout=timeout)
                sess.touch()
                return sess
            except SshError:
                # 复用失败（多半是远端重启/网络断了）：丢掉旧的，重建一条
                self.close(iid)
        sess = SshSession(inst, transport_factory=self._factory,
                          clock=self._clock, log=self._log)
        sess.connect(timeout=timeout)
        with self._lock:
            other = self._idle.get(iid)
            if other is not None and other is not sess and other.is_alive():
                # 并发下别的线程先建好了：用它的（关掉自己这条，避免两条会话抢同一实例）
                _safe_close(sess)
                other.touch()
                return other
            self._idle[iid] = sess
        self._ensure_ticker()
        return sess

    def new_session(self, inst: dict, timeout: float | None = None) -> SshSession:
        """建一条**不进池**的会话（隧道独占用；调用方自己负责 close）"""
        sess = SshSession(inst, transport_factory=self._factory,
                          clock=self._clock, log=self._log)
        sess.connect(timeout=timeout)
        return sess

    def release(self, inst_id: str) -> None:
        """归还（当前实现=保留在池里等 TTL；留接口给将来「用完就关」的策略）"""
        with self._lock:
            sess = self._idle.get(inst_id)
        if sess is not None:
            sess.touch()

    def close(self, inst_id: str) -> None:
        with self._lock:
            sess = self._idle.pop(inst_id, None)
        _safe_close(sess)

    def close_all(self) -> None:
        self._stop.set()
        with self._lock:
            items = list(self._idle.values())
            self._idle.clear()
        for s in items:
            _safe_close(s)

    def is_alive(self, inst_id: str) -> bool:
        with self._lock:
            sess = self._idle.get(inst_id)
        return bool(sess and sess.is_alive())

    def gc(self) -> int:
        """清理空闲超时 / 已断开的会话；返回关闭条数"""
        dead = []
        with self._lock:
            for iid, sess in list(self._idle.items()):
                if (not sess.is_alive()) or sess.idle_for() >= self._idle_ttl:
                    dead.append(iid)
            for iid in dead:
                self._idle.pop(iid, None)
        for iid in dead:
            self._log(f"关闭空闲 SSH 会话：{iid}")
        return len(dead)

    # ---------------- 内部 ----------------
    @staticmethod
    def _key(inst: dict) -> str:
        """池键 = 实例 id（改名不失效）；没有 id 时退化为 host:port@user"""
        iid = str((inst or {}).get("id") or "").strip()
        if iid:
            return iid
        host = str((inst or {}).get("ssh_host") or "").strip()
        port = _as_port((inst or {}).get("ssh_port"))
        user = str((inst or {}).get("ssh_user") or "").strip()
        return f"{host}:{port}@{user}"

    def _ticker(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                self.gc()
            except Exception:              # noqa: BLE001
                pass

    def _ensure_ticker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._ticker, name="ssh-idle-gc",
                                        daemon=True)
        self._thread.start()


# 模块级单例：UI 侧（第二批）直接用 `remote_ssh.manager`，Worker 里也能替换
manager = SshManager()
