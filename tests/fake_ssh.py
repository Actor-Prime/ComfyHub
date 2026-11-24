# -*- coding: utf-8 -*-
"""SSH / 隧道测试桩（2026-12-01，0.8.40）

为什么要有这个文件：本机没有 sshd，任何「真连一次」的测试都不可复现。
所以把 paramiko 的**最小子集**脚本化：认证失败、断链、命令输出洪泛、退出码、
「nohup 不重定向导致 channel 永不 EOF」，全部能用数据描述出来。

接口清单严格按 ARCH §10.1，`remote_ssh` / `tunnel` 只依赖这些成员：

    FakeTransport：connect / start_client / auth_password / auth_publickey /
                   is_active / is_authenticated / get_remote_server_key /
                   open_session / open_channel / set_keepalive / close
    FakeChannel  ：exec_command / recv_ready / recv / recv_stderr_ready /
                   recv_stderr / exit_status_ready / recv_exit_status /
                   close / fileno / setblocking / closed
    FakeKey      ：get_name / get_base64

用法：

    fac = FakeFactory(script=[("out", "Starting server\\n"), ("exit", 0)])
    sess = remote_ssh.SshSession(inst, transport_factory=fac)
    sess.connect()

    # 五类认证失败
    FakeFactory(auth_error=paramiko.AuthenticationException("Authentication failed."))

    # 隧道中途断链
    FakeFactory(fail_after_bytes=100)
"""
import base64
import os
import threading
import time


# ---------------------------------------------------------------- 主机密钥
class FakeKey:
    """paramiko.PKey 的最小替身：只要 get_name / get_base64"""

    def __init__(self, name: str = "ssh-ed25519", seed: str = "seed-1"):
        self._name = str(name)
        self._blob = f"{self._name}:{seed}".encode("utf-8")

    def get_name(self) -> str:
        return self._name

    def get_base64(self) -> str:
        return base64.b64encode(self._blob).decode("ascii")

    def __repr__(self) -> str:
        return f"FakeKey({self._name},{self._blob.hex()[:8]})"


# ---------------------------------------------------------------- exec 通道
class FakeChannel:
    """脚本化的 exec channel

    script 元素：
      ("out", "文本")   追加标准输出
      ("err", "文本")   追加标准错误
      ("delay", 1.5)    之后的事件整体延后 1.5 秒（用单调时钟，测试真能等到）
      ("exit", 0)       设置退出码（通常放最后）
      ("raw", b"\\xff")  原始字节（测乱码解码）
    """

    def __init__(self, script=None, *, transport=None, echo: bool = False):
        self.script = list(script or [("exit", 0)])
        self.transport = transport
        self.command = ""
        self.closed = False
        self.blocking = True
        self.echo = bool(echo)
        self._out = bytearray()
        self._err = bytearray()
        self._exit = None
        self._t0 = 0.0
        self._pos = 0
        self._events = []
        rfd, wfd = os.pipe()
        self._rfd, self._wfd = rfd, wfd
        self._lock = threading.Lock()

    # ---- 生命周期 ----
    def exec_command(self, cmd):
        self.command = str(cmd)
        self._events = self._compile(self.script)
        self._t0 = time.monotonic()
        self._pos = 0

    @staticmethod
    def _compile(script) -> list:
        """把 script 编译成 [(相对到期时间, 类型, 负载)]"""
        out, due = [], 0.0
        for item in script or []:
            kind = str(item[0])
            payload = item[1] if len(item) > 1 else None
            if kind == "delay":
                due += float(payload)
                continue
            if kind == "out":
                out.append((due, "out", str(payload).encode("utf-8")))
            elif kind == "err":
                out.append((due, "err", str(payload).encode("utf-8")))
            elif kind == "raw":
                data = payload if isinstance(payload, bytes) else bytes(payload)
                out.append((due, "out", data))
            elif kind == "exit":
                out.append((due, "exit", int(payload if payload is not None else 0)))
            else:                                    # 未知类型直接忽略，别让测试炸
                continue
        return out

    def _advance(self):
        if self._exit is not None or not self._events:
            return
        now = time.monotonic() - self._t0
        while self._pos < len(self._events):
            due, kind, payload = self._events[self._pos]
            if now < due:
                break
            self._pos += 1
            if kind == "out":
                self._out += payload
            elif kind == "err":
                self._err += payload
            elif kind == "exit":
                self._exit = int(payload)

    # ---- paramiko Channel 接口 ----
    def setblocking(self, flag):
        self.blocking = bool(flag)

    def recv_ready(self) -> bool:
        self._advance()
        return bool(self._out)

    def recv(self, n: int = 65536) -> bytes:
        self._advance()
        with self._lock:
            chunk = bytes(self._out[:int(n)])
            del self._out[:int(n)]
            return chunk

    def recv_stderr_ready(self) -> bool:
        self._advance()
        return bool(self._err)

    def recv_stderr(self, n: int = 65536) -> bytes:
        self._advance()
        with self._lock:
            chunk = bytes(self._err[:int(n)])
            del self._err[:int(n)]
            return chunk

    def exit_status_ready(self) -> bool:
        self._advance()
        return self._exit is not None

    def recv_exit_status(self) -> int:
        self._advance()
        return int(self._exit if self._exit is not None else 0)

    def fileno(self) -> int:
        return self._rfd

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd in (self._rfd, self._wfd):
            try:
                os.close(fd)
            except OSError:
                pass
        self._rfd = self._wfd = -1

    def __repr__(self) -> str:
        return f"FakeChannel(cmd={self.command!r}, closed={self.closed})"


# ---------------------------------------------------------------- 转发通道
class FakeEchoChannel:
    """direct-tcpip 通道替身：把收到的字节原样回送（用来验证双向 relay）"""

    def __init__(self, transport=None, dest=None):
        self.transport = transport
        self.dest = dest
        self.sent = bytearray()
        self.closed = False
        self._back = bytearray()
        self._lock = threading.Lock()
        self._rfd, self._wfd = os.pipe()

    def sendall(self, data):
        data = bytes(data)
        if self.closed:
            raise EOFError("channel closed")
        with self._lock:
            self.sent += data
            self._back += data           # 回声
        if self.transport is not None:
            self.transport.count_bytes(len(data))

    def send(self, data) -> int:
        self.sendall(data)
        return len(data)

    def recv(self, n: int = 65536) -> bytes:
        n = int(n)
        while True:
            if self.closed:
                return b""
            with self._lock:
                if self._back:
                    chunk = bytes(self._back[:n])
                    del self._back[:n]
                    return chunk
            if self.transport is not None and not self.transport.is_active():
                return b""               # 链路被切断 → 当成 EOF，relay 线程会退出
            time.sleep(0.005)

    def fileno(self) -> int:
        return self._rfd

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd in (self._rfd, self._wfd):
            try:
                os.close(fd)
            except OSError:
                pass
        self._rfd = self._wfd = -1


# ---------------------------------------------------------------- 传输层
class FakeTransport:
    """paramiko.Transport 的最小替身

    参数：
      script             exec 脚本（见 FakeChannel）
      auth_error         认证阶段抛出的异常（密码/密钥共用）
      key_error          仅密钥认证抛出的异常（没给则用 auth_error）
      connect_error      start_client 阶段抛出的异常（模拟连不上 / 握手超时）
      fail_after_bytes   累计通过转发通道的字节数超过该值就断链（模拟隧道中途断线）
      key                主机密钥（改它即可模拟「指纹变了」）
      sessions/chan      记录 open_session / open_channel 的产物，供断言
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 22,
                 timeout: float = 5.0, *, script=None, auth_error=None,
                 key_error=None, connect_error=None, fail_after_bytes=None,
                 key=None, echo: bool = True):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.script = list(script or [("exit", 0)])
        self.auth_error = auth_error
        self.key_error = key_error
        self.connect_error = connect_error
        self.fail_after_bytes = fail_after_bytes
        self.echo = bool(echo)
        self._key = key or FakeKey()
        self._active = True
        self._authenticated = False
        self.started = False
        self.keepalive = None
        self.connect_kwargs = {}
        self.bytes_sent = 0
        self.sessions = []
        self.channels = []
        self.closed = False

    # ---- 握手 / 认证 ----
    def start_client(self, event=None, timeout=None):
        if not self._active:
            raise EOFError("transport closed")
        if self.connect_error is not None:
            raise self.connect_error
        self.started = True

    def connect(self, username=None, password=None, pkey=None, timeout=None,
                banner_timeout=None, auth_timeout=None):
        """Arch 里列出的「一步到位」入口；remote_ssh 优先用 start_client 两段式"""
        self.connect_kwargs = {"username": username, "password": password,
                               "pkey": pkey, "timeout": timeout}
        self.start_client(timeout=timeout)
        if pkey is not None:
            self.auth_publickey(username, pkey)
        else:
            self.auth_password(username, password)

    def auth_password(self, username, password, event=None, fallback=True):
        if not self._active:
            raise EOFError("transport closed")
        if self.auth_error is not None:
            raise self.auth_error
        self.password = password
        self.username = username
        self._authenticated = True
        return []

    def auth_publickey(self, username, key, event=None):
        if not self._active:
            raise EOFError("transport closed")
        err = self.key_error if self.key_error is not None else self.auth_error
        if err is not None:
            raise err
        self.pkey = key
        self.username = username
        self._authenticated = True
        return []

    # ---- 状态 ----
    def is_active(self) -> bool:
        return self._active

    def is_authenticated(self) -> bool:
        return self._authenticated

    def get_remote_server_key(self):
        return self._key

    def set_keepalive(self, n):
        self.keepalive = int(n)

    def close(self):
        self.closed = True
        self._active = False
        for ch in list(self.channels) + list(self.sessions):
            try:
                ch.close()
            except Exception:            # noqa: BLE001
                pass

    # ---- 通道 ----
    def open_session(self, timeout=None, **kw):
        if not self._active:
            raise EOFError("transport closed")
        ch = FakeChannel(self.script, transport=self)
        self.sessions.append(ch)
        return ch

    def open_channel(self, kind, dest_addr=None, src_addr=None, **kw):
        if not self._active:
            raise EOFError("transport closed")
        self.last_channel_kind = kind
        self.last_dest = dest_addr
        ch = FakeEchoChannel(self, dest_addr) if self.echo else FakeChannel(
            self.script, transport=self)
        self.channels.append(ch)
        return ch

    # ---- 断链模拟 ----
    def count_bytes(self, n: int):
        self.bytes_sent += int(n)
        if self.fail_after_bytes is not None and \
                self.bytes_sent >= int(self.fail_after_bytes):
            self.drop()

    def drop(self):
        """模拟链路被切断：is_active 立刻变 False（隧道靠它检测断线）"""
        self._active = False
        self._authenticated = False

    def __repr__(self) -> str:
        return (f"FakeTransport({self.host}:{self.port}, active={self._active}, "
                f"auth={self._authenticated}, bytes={self.bytes_sent})")


# ---------------------------------------------------------------- 工厂
class FakeFactory:
    """可当 transport_factory 用：每次调用产出一条新的 FakeTransport"""

    def __init__(self, **defaults):
        self.defaults = dict(defaults)
        self.calls = []
        self.transports = []

    def __call__(self, host, port, timeout=None):
        self.calls.append((host, port, timeout))
        tr = FakeTransport(host, port, timeout or 5.0, **self.defaults)
        self.transports.append(tr)
        return tr

    @property
    def last(self):
        return self.transports[-1] if self.transports else None

    @property
    def count(self) -> int:
        return len(self.transports)


class SequenceFactory:
    """按次脚本化的工厂：第 n 次调用用 specs[n]

    specs 元素是 dict（FakeTransport 的 kwargs），或 {"raise": Exception(...)}
    用来模拟「第一次成功、之后连不上」这类重连场景。
    """

    def __init__(self, specs):
        self.specs = list(specs)
        self.calls = []
        self.transports = []

    def __call__(self, host, port, timeout=None):
        self.calls.append((host, port, timeout))
        if not self.specs:
            spec = {}
        else:
            spec = self.specs.pop(0) if len(self.specs) > 1 else self.specs[0]
        if isinstance(spec, dict) and spec.get("raise") is not None:
            raise spec["raise"]
        kw = dict(spec) if isinstance(spec, dict) else {}
        kw.pop("raise", None)
        tr = FakeTransport(host, port, timeout or 5.0, **kw)
        self.transports.append(tr)
        return tr

    @property
    def last(self):
        return self.transports[-1] if self.transports else None

    @property
    def count(self) -> int:
        return len(self.transports)


# ---------------------------------------------------------------- 常用实例
def make_inst(**kw) -> dict:
    """一份完整的远程实例配置（含 SSH 字段），测试里直接拿来用"""
    inst = {
        "id": "inst_fake", "name": "假远端", "kind": "remote",
        "base_url": "http://192.168.1.20:8188",
        "ssh_host": "192.168.1.20", "ssh_port": 22, "ssh_user": "root",
        "ssh_auth": "password", "ssh_password": "pw", "ssh_key_path": "",
        "ssh_key_passphrase": "",
        "ssh_start_cmd": "cd ~/ComfyUI && nohup python main.py > ~/c.log 2>&1 &",
        "ssh_stop_cmd": 'pkill -f "main.py --listen" || true',
        "ssh_host_fingerprint": "",
        "tunnel_local_port": 18189, "tunnel_remote_host": "127.0.0.1",
        "tunnel_remote_port": 8188,
    }
    inst.update(kw)
    return inst
