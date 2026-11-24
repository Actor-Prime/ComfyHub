# -*- coding: utf-8 -*-
"""运行期基址覆盖注册表（2026-12-01，0.8.40）

问题：隧道激活后，所有 HTTP 调用应该打到 `http://127.0.0.1:<本地端口>`，但
**不能改写用户保存的 `base_url`**（PRD C3：断开后必须自动回落原值）。

做法：进程内单例注册表，`api_base(inst)` 查表优先：

    registry.set_override(iid, "http://127.0.0.1:8189", 8189)   # 隧道连上
    registry.clear_override(iid)                                # 隧道断开/失败

三条硬约束（ARCH §11 风险 1，**致命**）：
  1. **本模块零依赖、无 IO、单 dict + RLock**，几乎不存在抛点；
  2. `api_base` 里查表那段必须包 `try/except` → 异常一律当作「没有覆盖」，
     否则注册表的一个小 bug 会让**全仓 HTTP 全挂**（连本机实例都连不上）；
  3. 键用 `inst_id`（改名不失效），不用实例名。

禁止 import PySide6 / proc_manager。
"""
import threading
import time
from typing import NamedTuple


class TunnelEntry(NamedTuple):
    """一条覆盖记录：生效基址 / 本地端口 / 生效时刻（monotonic，只用于展示）"""

    base: str
    port: int
    since: float


def inst_id_of(inst) -> str:
    """从实例 dict（或 id 字符串）取注册表键"""
    if isinstance(inst, dict):
        return str(inst.get("id") or "").strip()
    return str(inst or "").strip()


class RemoteRegistry:
    """`dict[inst_id -> TunnelEntry]` + RLock（只保护 dict，绝不在锁内做 IO）"""

    def __init__(self, clock=time.monotonic):
        self._ov = {}
        self._lock = threading.RLock()
        self._clock = clock

    # ---------------- 写 ----------------
    def set_override(self, inst_id, base: str, port: int) -> None:
        iid = inst_id_of(inst_id)
        if not iid or not base:
            return
        entry = TunnelEntry(str(base), int(port or 0), self._clock())
        with self._lock:
            self._ov[iid] = entry

    def clear_override(self, inst_id) -> None:
        iid = inst_id_of(inst_id)
        if not iid:
            return
        with self._lock:
            self._ov.pop(iid, None)

    def reset(self) -> None:
        """清空整张表（测试 fixture / 退出清理用）"""
        with self._lock:
            self._ov.clear()

    # ---------------- 读 ----------------
    def override_for(self, inst) -> str:
        """生效基址；没有覆盖返回空串。**任何异常都吞掉**（见模块头约束 2）"""
        try:
            iid = inst_id_of(inst)
            if not iid:
                return ""
            with self._lock:
                entry = self._ov.get(iid)
            return entry.base if entry else ""
        except Exception:                   # noqa: BLE001 - 宁可回落，也不能让 HTTP 全挂
            return ""

    def entry_for(self, inst):
        try:
            iid = inst_id_of(inst)
            if not iid:
                return None
            with self._lock:
                return self._ov.get(iid)
        except Exception:                   # noqa: BLE001
            return None

    def is_active(self, inst_id) -> bool:
        try:
            iid = inst_id_of(inst_id)
            if not iid:
                return False
            with self._lock:
                return iid in self._ov
        except Exception:                   # noqa: BLE001
            return False

    def snapshot(self) -> dict:
        """`{inst_id: {"base","port","since"}}`（诊断/测试用，返回副本）"""
        with self._lock:
            return {k: {"base": v.base, "port": v.port, "since": v.since}
                    for k, v in self._ov.items()}

    def __len__(self) -> int:
        with self._lock:
            return len(self._ov)


# 模块级单例（整个 GUI 进程共用；单实例启动已由 single_instance.py 保证）
registry = RemoteRegistry()
