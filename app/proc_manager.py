# -*- coding: utf-8 -*-
"""实例进程注册表：跨页面共享 ComfyProcess 与统一日志缓冲
（对标管理大师的全局实例状态：工作台/控制台/ComfyUI 页看到的都是同一份状态）
"""
from collections import deque

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
        return bool(p and p.is_running)

    def actual_port(self, inst: dict) -> "int | None":
        """实例进程实际监听的 HTTP 端口；未运行/探测失败返回 None

        启动时配置端口被占会自动顺延（见 ComfyProcess.start），
        打开网页等入口必须用本方法拿真实端口，而不是 inst["port"]。
        """
        from app.process import actual_port_for_pid
        p = self._procs.get(inst["id"])
        if not (p and p.is_running and p.pid):
            return None
        return actual_port_for_pid(int(p.pid), inst.get("port"))

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
