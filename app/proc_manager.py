# -*- coding: utf-8 -*-
"""实例进程注册表：跨页面共享 ComfyProcess 与统一日志缓冲
（对标管理大师的全局实例状态：工作台/控制台/ComfyUI 页看到的都是同一份状态）
"""
from collections import deque
import threading
import time

from app.process import ComfyProcess


class ProcManager:
    """每个实例 id 对应一个 ComfyProcess；日志统一进缓冲供控制台页显示"""

    def __init__(self):
        self._procs = {}          # inst_id -> ComfyProcess
        self.log_buffer = deque(maxlen=4000)
        self._seq = 0             # 日志序号（控制台增量拉取用）
        # 插件加载失败回调：callable(inst_id, hit)。UI 层（main_window）挂上去，
        # 进程层只管解析，不认识弹窗/横幅这类东西。
        self.on_plugin_failed = None
        self._dep_seen = {}       # inst_id -> {签名}，同一批失败只报一次
        self._ext_cache = {}      # (inst_id, api_base) -> (是否存活, 时间戳)
        self._ext_pending = set()  # 正在探的 key，防并发堆积线程

    # ---------- 进程 ----------
    def get(self, inst: dict) -> ComfyProcess:
        iid = inst["id"]
        if iid not in self._procs:
            proc = ComfyProcess()
            proc.log_line.connect(
                lambda line, iid=iid: self.append_log(iid, line))
            # 服务就绪后再看日志：插件 import 失败在启动早期就打完了，
            # 就绪时信息已全；延迟 2 秒是给「就绪那一刻还在刷尾巴」留余量。
            proc.ready.connect(lambda _port, iid=iid: self._on_ready(iid))
            # 每次重新启动都允许再报一次：用户可能是关了横幅才没看到
            proc.state_changed.connect(
                lambda running, iid=iid: self.reset_dep_checks(iid) if running else None)
            self._procs[iid] = proc
        return self._procs[iid]

    def _on_ready(self, iid: str):
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.check_plugin_failures(iid))
        except Exception:
            pass

    def reset_dep_checks(self, iid: str | None = None):
        """清掉「已提示」记录（实例重启后同一批失败要能再报一次）"""
        if iid is None:
            self._dep_seen.clear()
        else:
            self._dep_seen.pop(iid, None)

    def check_plugin_failures(self, iid: str):
        """分析该实例本轮日志 → 有插件加载失败就回调 UI（同一批只报一次）"""
        from app import plugin_log
        try:
            hit = plugin_log.analyze([ln for _i, ln in self.tail(iid, 4000)])
        except Exception:
            return None
        if not hit.get("count"):
            return hit
        sig = plugin_log.signature(hit)
        seen = self._dep_seen.setdefault(iid, set())
        if sig in seen:
            return hit
        seen.add(sig)
        cb = self.on_plugin_failed
        if callable(cb):
            try:
                cb(iid, hit)
            except Exception:
                pass
        return hit


    def is_running(self, inst: dict) -> bool:
        p = self._procs.get(inst["id"])
        if p and p.is_running:
            return True
        return self._ext_alive(inst)

    def is_own(self, inst: dict) -> bool:
        """是否由本管理器拉起（决定「停止」按钮能不能点）"""
        p = self._procs.get(inst["id"])
        return bool(p and p.is_running)

    # ---------- 外部实例探活（2026-11-23，0.8.39）----------
    # 背景：以前只认自己 Popen 的进程，用户自己 `python main.py` 起的 ComfyUI、
    # 或经 SSH 隧道映射过来的远端实例，一律显示「未运行」，按钮全灰 → 没法用。
    # 约束：is_running() 在 UI 线程的周期路径上被调用，**绝不能同步发 HTTP**
    # （0.8.2x 血案：关闭端口 connect 不秒拒，每 2 秒堵 UI 4 秒）。
    # 做法：只读缓存，缓存过期时丢给后台线程刷，UI 最多晚一拍（≤2s）才看到状态。
    EXT_TTL = 2.0

    def _ext_alive(self, inst: dict) -> bool:
        iid = str(inst.get("id") or "")
        if not iid:
            return False
        key = (iid, self._ext_key(inst))
        now = time.time()
        hit = self._ext_cache.get(key)
        if hit and now - hit[1] < self.EXT_TTL:
            return hit[0]
        # 过期或没缓存：先按「不可用」返回，同时后台探一次（不阻塞 UI）
        if key not in self._ext_pending:
            self._ext_pending.add(key)
            threading.Thread(target=self._probe_worker, args=(inst, key),
                             daemon=True).start()
        return bool(hit and hit[0])

    @staticmethod
    def _ext_key(inst: dict) -> str:
        from app.api_base import api_base
        return api_base(inst)

    def _probe_worker(self, inst: dict, key):
        ok = False
        try:
            from app.api_base import probe
            snapshot = dict(inst)
            ok = probe(snapshot, timeout=2.5)
        except Exception:
            ok = False
        finally:
            self._ext_cache[key] = (ok, time.time())
            self._ext_pending.discard(key)

    def invalidate(self, inst: dict | None = None):
        """配置改动/启停后清缓存，避免沿用旧判定"""
        if inst is None:
            self._ext_cache.clear()
            self._ext_pending.clear()
        else:
            self._ext_cache.pop((str(inst.get("id") or ""), self._ext_key(inst)), None)

    def actual_port(self, inst: dict) -> "int | None":
        """实例进程实际监听的 HTTP 端口；未运行/探测失败返回 None

        启动时配置端口被占会自动顺延（见 ComfyProcess.start），
        打开网页等入口必须用本方法拿真实端口，而不是 inst["port"]。
        """
        from app.process import actual_port_for_pid
        p = self._procs.get(inst["id"])
        if p and p.is_running and p.pid:
            return actual_port_for_pid(int(p.pid), inst.get("port"))
        # 外部/远程实例（0.8.39）：远程没有「本机端口」这个概念，返回 None
        if self._ext_alive(inst):
            from app.api_base import is_remote
            return None if is_remote(inst) else int(inst.get("port") or 8188)
        return None

    def web_url(self, inst: dict) -> str:
        """「打开网页」该去哪个地址：远程=配置的 base_url；本地=实际端口"""
        from app.api_base import api_base, is_remote
        if is_remote(inst):
            return api_base(inst)
        port = self.actual_port(inst) or int(inst.get("port") or 8188)
        return "http://127.0.0.1:%d" % int(port)

    def running_pids(self) -> set:
        """本管理器启动且仍在运行的实例 PID 集合（孤儿检测排除用）"""
        return {p.pid for p in self._procs.values()
                if p.is_running and p.pid}

    def stop_all(self):
        for p in self._procs.values():
            if p.is_running:
                p.stop()

    # ---------- 日志 ----------
    def append_log(self, inst_id: str, line: str):
        self.log_buffer.append((inst_id, line))
        self._seq += 1

    def tail(self, inst_id: str | None = None, last_n: int = 500):
        """取尾部日志；inst_id=None 取全部实例"""
        items = [x for x in self.log_buffer
                 if inst_id is None or x[0] == inst_id]
        return items[-last_n:]

    def clear(self):
        self.log_buffer.clear()
