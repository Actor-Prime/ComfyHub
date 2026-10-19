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

    # ---------- 进程 ----------
    def get(self, inst: dict) -> ComfyProcess:
        iid = inst["id"]
        if iid not in self._procs:
            proc = ComfyProcess()
            proc.log_line.connect(
                lambda line, iid=iid: self.append_log(iid, line))
            self._procs[iid] = proc
        return self._procs[iid]

    def is_running(self, inst: dict) -> bool:
        p = self._procs.get(inst["id"])
        return bool(p and p.is_running)

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
