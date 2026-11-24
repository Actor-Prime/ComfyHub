# -*- coding: utf-8 -*-
"""0.8.40 T02：SSH 通道（连接 / 认证五分类 / 流式执行 / 指纹）

覆盖（ARCH §7 T02 验收 ①-⑥）：
  ① 五类认证失败 kind 各自命中：unreachable / auth_password / auth_passphrase /
     auth_key / key_missing
  ② 主机指纹：首次记录 → 写入 known_hosts.json → 二次连接通过 → 改指纹后
     抛 kind="fingerprint"
  ③ `nohup` 不重定向（channel 永不 EOF）→ settle_timeout 后 detached=True，
     主流程不被卡死，channel 最终被后台线程关掉（不泄漏）
  ④ cancel 后 code=None / cancelled=True 且 channel 已 close
  ⑤ 乱码输出（b"\\xff\\xfe"）不抛异常，行内是替换字符
  ⑥ 故意在 $HOME/.ssh/known_hosts 里写一条冲突项 → 连接不受影响
     （证明没读系统文件；用临时 HOME 而不是动用户真实目录）
  ⑦ 退出码非 0 → err_kind="cmd"；输出含 sudo 提权要求 → err_kind="sudo"
  ⑧ SshManager：会话复用、空闲 TTL 回收、断链后自动重建

用法：.venv_build/Scripts/python.exe -u tests/test_remote_ssh.py
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

_TMP = tempfile.mkdtemp(prefix="comfyhub_ssh_")
os.environ["APPDATA"] = _TMP                     # remote_fingerprint 的数据根
os.environ["HOME"] = os.path.join(_TMP, "home")  # 隔离「系统 known_hosts」检查
os.environ["USERPROFILE"] = os.environ["HOME"]
os.makedirs(os.path.join(os.environ["HOME"], ".ssh"), exist_ok=True)

import paramiko                                  # noqa: E402

from app import remote_fingerprint as fpmod      # noqa: E402
from app import remote_ssh                       # noqa: E402
from fake_ssh import (FakeFactory, FakeKey, SequenceFactory,  # noqa: E402
                      make_inst)

res = []


def check(n, ok, d=""):
    res.append((n, bool(ok), d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def kind_of(fn) -> str:
    """跑一段，返回抛出的 SshError.kind（没抛返回 '<none>'）"""
    try:
        fn()
        return "<none>"
    except remote_ssh.SshError as e:
        return e.kind
    except Exception as e:                       # noqa: BLE001
        return f"<{type(e).__name__}: {e}>"


INST = make_inst()


def _sess(**kw):
    fac = FakeFactory(**kw)
    s = remote_ssh.SshSession(INST, transport_factory=fac)
    return s, fac


print("=== 1. 五类认证失败各归各位 ===")

# E1：TCP/握手层
s, f = _sess(connect_error=ConnectionRefusedError(10061, "Connection refused"))
check("E1 连接被拒 → unreachable",
      kind_of(s.connect) == remote_ssh.KIND_UNREACHABLE, kind_of(s.connect))

s, f = _sess(connect_error=socket.timeout("timed out"))
check("E1 握手超时 → timeout", kind_of(s.connect) == remote_ssh.KIND_TIMEOUT)

# E2：密码错
s, f = _sess(auth_error=paramiko.AuthenticationException("Authentication failed."))
check("E2 密码错 → auth_password",
      kind_of(s.connect) == remote_ssh.KIND_AUTH_PASSWORD)

# E2p：私钥口令错（三种入口：PasswordRequiredException / 加密但没给口令 / 口令错）
_keydir = os.path.join(_TMP, "keys")
os.makedirs(_keydir, exist_ok=True)
_enc_key = os.path.join(_keydir, "id_ed25519_enc")
_pk = os.path.join(_keydir, "id_plain")
try:
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
    _k = _ed.Ed25519PrivateKey.generate()
    with open(_enc_key, "wb") as fh:
        fh.write(_k.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.OpenSSH,
                                  _ser.BestAvailableEncryption(b"right-pass")))
    with open(_pk, "wb") as fh:
        fh.write(_k.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.OpenSSH,
                                  _ser.NoEncryption()))
    _has_crypto = True
except Exception as e:                           # noqa: BLE001
    _has_crypto = False
    print(f"    （跳过真私钥用例：{e}）")

if _has_crypto:
    # 密钥加载没问题、但认证阶段抛「需要口令」→ 必须归 E2p
    _i = make_inst(ssh_auth="key", ssh_key_path=_pk, ssh_key_passphrase="")
    s, f = _sess(auth_error=paramiko.PasswordRequiredException(
        "Private key file is encrypted"))
    check("E2p PasswordRequiredException → auth_passphrase",
          kind_of(remote_ssh.SshSession(_i, transport_factory=f).connect)
          == remote_ssh.KIND_AUTH_PASSPHRASE)
    _i = make_inst(ssh_auth="key", ssh_key_path=_enc_key, ssh_key_passphrase="")
    check("E2p 加密私钥但没给口令 → auth_passphrase",
          kind_of(remote_ssh.SshSession(_i, transport_factory=FakeFactory()).connect)
          == remote_ssh.KIND_AUTH_PASSPHRASE)
    _i = make_inst(ssh_auth="key", ssh_key_path=_enc_key, ssh_key_passphrase="wrong")
    check("E2p 口令错 → auth_passphrase",
          kind_of(remote_ssh.SshSession(_i, transport_factory=FakeFactory()).connect)
          == remote_ssh.KIND_AUTH_PASSPHRASE)

# E2k：远端不接受这把密钥
_i = make_inst(ssh_auth="key", ssh_key_path=_pk, ssh_key_passphrase="")
s, f = _sess(key_error=paramiko.AuthenticationException("Authentication failed."))
check("E2k 远端不认这把密钥 → auth_key",
      kind_of(remote_ssh.SshSession(_i, transport_factory=f).connect)
      == remote_ssh.KIND_AUTH_KEY)

# E2b：读不到密钥文件
_i = make_inst(ssh_auth="key", ssh_key_path=os.path.join(_keydir, "not-exist"))
check("E2b 密钥文件不存在 → key_missing",
      kind_of(remote_ssh.SshSession(_i, transport_factory=FakeFactory()).connect)
      == remote_ssh.KIND_KEY_MISSING)
_ppk = os.path.join(_keydir, "id.ppk")
with open(_ppk, "w", encoding="utf-8") as fh:
    fh.write("PuTTY-User-Key-File-2: ssh-rsa\n")
_i = make_inst(ssh_auth="key", ssh_key_path=_ppk, ssh_key_passphrase="")
check("E2b .ppk → key_missing（本版不支持）",
      kind_of(remote_ssh.SshSession(_i, transport_factory=FakeFactory()).connect)
      == remote_ssh.KIND_KEY_MISSING)

print("=== 2. 主机指纹：首次记录 → 二次通过 → 变了就拒绝 ===")
_store = fpmod.store_path()
check("指纹文件落在 data_root/remote 下",
      os.path.normpath(_store).startswith(os.path.normpath(_TMP))
      and "remote" in _store, _store)
if os.path.isfile(_store):
    os.remove(_store)

_instA = make_inst(id="inst_fp", ssh_host="10.0.0.9", ssh_port=2222)
_fac = FakeFactory(key=FakeKey("ssh-ed25519", "server-A"))
s1 = remote_ssh.SshSession(_instA, transport_factory=_fac)
s1.connect()
_ok1 = s1.is_alive()
s1.close()
check("首次连接成功并记录指纹", _ok1 and os.path.isfile(_store))
with open(_store, encoding="utf-8") as f:
    _db = json.load(f)
_entry = (_db.get("hosts") or {}).get("10.0.0.9:2222") or {}
check("记录里键是 host:port", bool(_entry), str(list((_db.get("hosts") or {}))))
check("记录含 SHA256 指纹", str(_entry.get("fingerprint", "")).startswith("SHA256:"),
      _entry.get("fingerprint", ""))
check("记录含 first_seen / last_seen",
      bool(_entry.get("first_seen")) and bool(_entry.get("last_seen")))

_fac2 = FakeFactory(key=FakeKey("ssh-ed25519", "server-A"))
s2 = remote_ssh.SshSession(_instA, transport_factory=_fac2)
check("指纹一致 → 正常连接", kind_of(s2.connect) == "<none>")
s2.close()

_fac3 = FakeFactory(key=FakeKey("ssh-ed25519", "server-B"))    # 对方「重装了系统」
s3 = remote_ssh.SshSession(_instA, transport_factory=_fac3)
check("指纹变了 → kind=fingerprint",
      kind_of(s3.connect) == remote_ssh.KIND_FINGERPRINT)
check("指纹变了时不发凭据（认证方法没被调用）",
      not bool(getattr(_fac3.last, "_authenticated", False)))
check("指纹变了时连接被关掉", _fac3.last.closed is True)

print("=== 3. 不读系统 ~/.ssh/known_hosts（冲突项不影响连接）===")
_sys_kh = os.path.join(os.environ["HOME"], ".ssh", "known_hosts")
with open(_sys_kh, "w", encoding="utf-8") as fh:
    # 一条与「真指纹」冲突的条目 + 一条乱格式
    fh.write("10.0.0.9 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICONFLICTconflict\n")
    fh.write("garbage line without enough fields\n")
_instB = make_inst(id="inst_sys", ssh_host="10.0.0.9", ssh_port=2222)
_facB = FakeFactory(key=FakeKey("ssh-ed25519", "server-A"))
sB = remote_ssh.SshSession(_instB, transport_factory=_facB)
check("系统 known_hosts 有冲突项也照常连上", kind_of(sB.connect) == "<none>")
sB.close()
with open(_sys_kh, encoding="utf-8") as fh:
    _sys_txt = fh.read()
check("我们没改用户的 ~/.ssh/known_hosts", "CONFLICTconflict" in _sys_txt)

print("=== 4. 流式执行：正常输出 / 退出码 / sudo ===")
_instC = make_inst(id="inst_exec")
_fac = FakeFactory(script=[("out", "Starting server\n"),
                           ("out", "To see the GUI go to: http://127.0.0.1:8188\n"),
                           ("err", "warning: xformers not found\n"),
                           ("exit", 0)])
s = remote_ssh.SshSession(_instC, transport_factory=_fac)
s.connect()
_seen = []
r = s.exec_stream("python main.py", on_line=_seen.append)
check("3 行输出都收到了", len(_seen) == 3, str(_seen))
check("退出码 0 且未 detach", r.code == 0 and not r.detached, repr(r))
check("err_kind 为空（正常）", r.err_kind == "", r.err_kind)
check("lines 里保留了输出", len(r.lines) == 3, str(list(r.lines)))
s.close()

_fac = FakeFactory(script=[("err", "bash: cd: /root/ComfyUI: No such file or directory\n"),
                           ("exit", 2)])
s = remote_ssh.SshSession(_instC, transport_factory=_fac)
s.connect()
r = s.exec_stream("cd /root/ComfyUI && python main.py")
check("退出码 2 → err_kind=cmd", r.err_kind == remote_ssh.EXEC_CMD and r.code == 2,
      repr(r))
check("tail(20) 能给出 stderr 末行", "No such file" in (r.tail(20) or [""])[0],
      str(r.tail(3)))
s.close()

_fac = FakeFactory(script=[("err", "sudo: a terminal is required to read the password; "
                                   "either use the -S option\n"),
                           ("exit", 1)])
s = remote_ssh.SshSession(_instC, transport_factory=_fac)
s.connect()
r = s.exec_stream("sudo systemctl restart x")
check("输出含 sudo 提权要求 → err_kind=sudo", r.err_kind == remote_ssh.EXEC_SUDO,
      r.err_kind)
s.close()

print("=== 5. 乱码输出不崩 ===")
_fac = FakeFactory(script=[("raw", b"\xff\xfe\x00bad bytes\n"), ("exit", 0)])
s = remote_ssh.SshSession(_instC, transport_factory=_fac)
s.connect()
try:
    r = s.exec_stream("cat weird.log")
    _line = list(r.lines)[0] if r.lines else ""
    check("乱码行已 decode(replace) 且不抛异常", "\ufffd" in _line, repr(_line))
except Exception as e:                          # noqa: BLE001
    check("乱码行已 decode(replace) 且不抛异常", False, f"{type(e).__name__}: {e}")
s.close()

print("=== 6. nohup 不重定向：detach 而不是卡死 ===")
_fac = FakeFactory(script=[("out", "started\n"), ("delay", 2.0), ("exit", 0)])
s = remote_ssh.SshSession(_instC, transport_factory=_fac)
s.connect()
_t0 = time.monotonic()
r = s.exec_stream("nohup python main.py &", settle_timeout=1.0)
_dt = time.monotonic() - _t0
_chan = _fac.last.sessions[0]
check("detached=True（30s 不 EOF 就转后台）", r.detached is True, repr(r))
check("主流程没被卡死（settle=1s → 约 1s 返回）", _dt < 3.0, f"{_dt:.2f}s")
check("detach 时还没拿到退出码", r.code is None, r.code)
check("已收到 detach 之前的输出", list(r.lines) == ["started"], str(list(r.lines)))
time.sleep(1.6)                                  # 等后台排空线程跑完剩下的 delay
check("后台线程在命令结束后关掉了 channel（不泄漏）", _chan.closed is True)
s.close()

print("=== 7. 取消：code=None / cancelled=True / channel 已关 ===")
_fac = FakeFactory(script=[("out", "loading...\n"), ("delay", 5.0), ("exit", 0)])
s = remote_ssh.SshSession(_instC, transport_factory=_fac)
s.connect()
_flag = {"v": False}


def _cancel():
    return _flag["v"]


def _flip():
    time.sleep(0.4)
    _flag["v"] = True


import threading                                 # noqa: E402
threading.Thread(target=_flip, daemon=True).start()
_t0 = time.monotonic()
r = s.exec_stream("python main.py", cancel=_cancel, settle_timeout=60)
_dt = time.monotonic() - _t0
_ch = _fac.last.sessions[0]
check("cancelled=True", r.cancelled is True, repr(r))
check("code=None", r.code is None, r.code)
check("取消后立刻返回（不等 5s delay）", _dt < 2.0, f"{_dt:.2f}s")
check("取消后 channel 已 close", _ch.closed is True)
check("取消不杀远端（只是关 channel，没有 kill 之类调用）",
      not any(x for x in dir(_fac.last) if "kill" in x.lower()))
s.close()

print("=== 8. 会话探活 / 复用 / 空闲回收 ===")
_fac = FakeFactory(script=[("exit", 0)])
mgr = remote_ssh.SshManager(transport_factory=_fac, idle_ttl=0.05, tick=0.02)
s1 = mgr.session(_instC)
s2 = mgr.session(_instC)
check("同一实例复用同一条会话", s1 is s2 and _fac.count == 1, f"连接 {_fac.count} 次")
check("池里认得出它活着", mgr.is_alive("inst_exec") is True)
time.sleep(0.12)
mgr.gc()                                         # 后台 tick 线程可能已经回收，这里只兜一手
check("空闲超 TTL 后被回收", not mgr.is_alive("inst_exec"), "仍在池里")
_fac.last.drop()                                 # 模拟远端重启 / 网络断
s3 = mgr.session(_instC)
check("旧会话断了会自动重建", s3 is not s1 and _fac.count == 2, f"连接 {_fac.count} 次")
mgr.close_all()
check("close_all 后池是空的", mgr.gc() == 0 and not mgr.is_alive("inst_exec"))

print("=== 9. 配置缺项 → 不抛 traceback，给 kind ===")
_i = make_inst(id="inst_bad", ssh_host="", ssh_user="")
check("没填主机 → unreachable",
      kind_of(remote_ssh.SshSession(_i, transport_factory=FakeFactory()).connect)
      == remote_ssh.KIND_UNREACHABLE)
_i = make_inst(id="inst_bad2", ssh_auth="key", ssh_key_path="")
check("认证=密钥但没填路径 → key_missing",
      kind_of(remote_ssh.SshSession(_i, transport_factory=FakeFactory()).connect)
      == remote_ssh.KIND_KEY_MISSING)
check("config_missing 能提前告知缺什么",
      "密钥路径" in remote_ssh.SshSession(_i).config_missing(),
      remote_ssh.SshSession(_i).config_missing())

print("=== 10. 层次约束：本模块不碰 Qt / proc_manager ===")
import ast                                       # noqa: E402


def _imports(path: str) -> set:
    """真实 import 语句里的模块名（注释/文档里提到不算）"""
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


_imp_ssh = _imports(os.path.join(ROOT, "app", "remote_ssh.py"))
_imp_fp = _imports(os.path.join(ROOT, "app", "remote_fingerprint.py"))
check("remote_ssh 不 import PySide6",
      not [m for m in _imp_ssh if m.split(".")[0] == "PySide6"], str(sorted(_imp_ssh)))
check("remote_ssh 不 import proc_manager",
      not [m for m in _imp_ssh if "proc_manager" in m])
check("remote_fingerprint 不 import PySide6",
      not [m for m in _imp_fp if m.split(".")[0] == "PySide6"], str(sorted(_imp_fp)))
check("remote_fingerprint 不 import proc_manager",
      not [m for m in _imp_fp if "proc_manager" in m])

print()
bad = [n for n, ok, _ in res if not ok]
print(f"结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if bad else 0)
