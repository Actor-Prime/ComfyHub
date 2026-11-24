# -*- coding: utf-8 -*-
"""SSH 主机指纹存储与比对（2026-12-01，0.8.40）

为什么不用系统 `~/.ssh/known_hosts`：
  1. 用户既有脏条目 / 旧指纹会直接把「首次连接」挡死，而我们的信任模型和 ssh.exe
     并不一样（云平台经常换机器复用同一公网 IP，ssh.exe 那边反而更吵）；
  2. 系统文件里混着 hashed host / cert-authority / @revoked 等写法，解析出错面大。
代价：与 ssh.exe 不共享信任（已列入 ARCH §12 待确认 #2，属已知取舍）。

存储：`<data_root>/remote/known_hosts.json`（data_root 走 app.paths，便携模式自动
落到程序目录），键 = `host:port`（同一台机器不同端口视为不同目标）：

    {"version": 1,
     "hosts": {"192.168.1.20:22": {"type": "ssh-ed25519",
                                   "fingerprint": "SHA256:AbCd...",
                                   "first_seen": "2026-12-01T10:00:00Z",
                                   "last_seen": "2026-12-02T09:11:00Z"}}}

写入：`tmp + os.replace` 原子写，文件损坏一律当空表（下次连接重新记录），绝不崩。
写盘失败（目录不可写）只在内存记并返回 False —— 不阻断连接（ARCH §11 风险 7）。

本模块**禁止 import PySide6 / proc_manager**（离屏、无 Qt 环境可测）。
"""
import base64
import hashlib
import json
import os
import threading
import time

STORE_VERSION = 1

# 指纹校验结论（verify 返回的 status）
ST_MATCH = "match"      # 与记录一致
ST_NEW = "new"          # 首次记录（已写入）
ST_CHANGED = "changed"  # 与记录不一致 → 调用方必须拒绝连接
ST_ERROR = "error"      # 拿不到对端密钥或本地存储异常

_LOCK = threading.RLock()


def store_path() -> str:
    """指纹文件绝对路径：<data_root>/remote/known_hosts.json"""
    from app.paths import data_root
    return os.path.join(data_root(), "remote", "known_hosts.json")


def host_key(host: str, port: int) -> str:
    """存储键：host:port（端口默认 22，避免 22 与非 22 被当成同一目标）"""
    h = str(host or "").strip().lower()
    try:
        p = int(port or 22)
    except (TypeError, ValueError):
        p = 22
    return f"{h}:{p}"


def fingerprint_of(key) -> tuple:
    """(算法名, "SHA256:...") —— 与 OpenSSH `ssh-keygen -l` 的 SHA256 形式一致"""
    try:
        name = str(key.get_name() or "unknown")
    except Exception:                       # noqa: BLE001 - 第三方 PKey 可能没有该方法
        name = "unknown"
    try:
        blob = base64.b64decode(str(key.get_base64() or ""))
    except Exception:                       # noqa: BLE001
        return name, ""
    if not blob:
        return name, ""
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")
    return name, "SHA256:" + digest.rstrip("=")


def load_store(path: str | None = None) -> dict:
    """读整张表；文件不存在 / 损坏 / 结构不对 一律返回空表"""
    p = path or store_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": STORE_VERSION, "hosts": {}}
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, dict):
        return {"version": STORE_VERSION, "hosts": {}}
    out = {}
    for k, v in hosts.items():
        if isinstance(v, dict) and str(v.get("fingerprint") or ""):
            out[str(k)] = {
                "type": str(v.get("type") or ""),
                "fingerprint": str(v.get("fingerprint") or ""),
                "first_seen": str(v.get("first_seen") or ""),
                "last_seen": str(v.get("last_seen") or ""),
            }
    return {"version": STORE_VERSION, "hosts": out}


def _write_store(store: dict, path: str | None = None) -> bool:
    """原子写；失败返回 False（调用方只记日志，不阻断连接）"""
    p = path or store_path()
    tmp = p + ".tmp"
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except OSError:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _now_iso() -> str:
    """时间戳用 time.time()（文件/缓存语义），不与 monotonic 混用"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))


def remember(host: str, port: int, key, path: str | None = None) -> bool:
    """记录/更新一条指纹（首次写入或刷新 last_seen）；失败返回 False"""
    name, fp = fingerprint_of(key)
    if not fp:
        return False
    hk = host_key(host, port)
    with _LOCK:
        store = load_store(path)
        hosts = store.setdefault("hosts", {})
        old = hosts.get(hk) or {}
        now = _now_iso()
        hosts[hk] = {
            "type": name,
            "fingerprint": fp,
            "first_seen": old.get("first_seen") or now,
            "last_seen": now,
        }
        return _write_store(store, path)


def forget(host: str, port: int, path: str | None = None) -> bool:
    """删掉一条指纹（用户点「信任新指纹」时用）；返回是否真的改动过"""
    hk = host_key(host, port)
    with _LOCK:
        store = load_store(path)
        hosts = store.setdefault("hosts", {})
        if hk not in hosts:
            return False
        hosts.pop(hk, None)
        return _write_store(store, path)


def recorded(host: str, port: int, path: str | None = None) -> dict:
    """已记录的条目（没有则空 dict），供 UI 显示「上次记录于 …」"""
    return dict(load_store(path).get("hosts", {}).get(host_key(host, port)) or {})


def verify(host: str, port: int, key, path: str | None = None,
           accept_new: bool = True) -> dict:
    """比对对端主机密钥。

    返回 {"status", "type", "fingerprint", "expected"}：
      · ST_MATCH   —— 一致（顺手刷新 last_seen）
      · ST_NEW     —— 首次连接，已记录（accept_new=False 时只返回不记录）
      · ST_CHANGED —— **与记录不一致，调用方必须拒绝连接**（expected 是旧指纹）
      · ST_ERROR   —— 拿不到对端密钥（key 为 None / 无 base64）
    """
    name, fp = fingerprint_of(key) if key is not None else ("", "")
    if not fp:
        return {"status": ST_ERROR, "type": name, "fingerprint": "",
                "expected": ""}
    hk = host_key(host, port)
    with _LOCK:
        store = load_store(path)
        old = (store.get("hosts") or {}).get(hk) or {}
        expected = str(old.get("fingerprint") or "")
        if expected and expected != fp:
            return {"status": ST_CHANGED, "type": name, "fingerprint": fp,
                    "expected": expected}
        if not expected:
            if accept_new:
                remember(host, port, key, path)
            return {"status": ST_NEW, "type": name, "fingerprint": fp,
                    "expected": ""}
        # 一致：只刷新 last_seen（失败不影响连接）
        old["last_seen"] = _now_iso()
        old["type"] = name or old.get("type") or ""
        store["hosts"][hk] = old
        _write_store(store, path)
        return {"status": ST_MATCH, "type": name, "fingerprint": fp,
                "expected": expected}
