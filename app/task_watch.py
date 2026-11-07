# -*- coding: utf-8 -*-
"""ComfyUI 任务完成监听 + 系统通知

原理（零第三方依赖）：
  每 2 秒 GET /queue  →  queue_running / queue_pending
  发现"上一轮有任务在跑，现在队列空了"→ GET /history?max_items=1
  若出现新的 prompt_id，判定为一次任务结束 → 发通知

为什么不用 websocket：启动器里没有 aiohttp/websocket-client，为这一个功能加依赖不划算；
HTTP 轮询 2 秒一次，开销可忽略，且能兼容所有 ComfyUI 版本。

2026-11-02 线程化：原实现是 UI 线程 QTimer + 同步 urllib——在「关闭端口
connect 不秒拒而是吃满超时」的机器上（实测 4s 超时打满/次），ComfyUI 启动
期间每 2 秒堵 UI 线程 4 秒，管理器直接卡成幻灯片。现在 HTTP 全部在本
QThread 内阻塞（无所谓），结果经信号跨线程回 UI（Qt 自动排队）。
"""
import json
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal


def _get_json(url: str, timeout=4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


class TaskWatcher(QThread):
    """监听单个实例的任务完成事件（后台线程轮询）"""

    sig_task_done = Signal(str, str)      # (实例名, 摘要文本)
    sig_task_error = Signal(str, str)     # (实例名, 错误摘要)

    def __init__(self, inst_name: str, port: int, parent=None):
        super().__init__(parent)
        self.inst_name = inst_name
        self.base = f"http://127.0.0.1:{int(port)}"
        self._had_task = False
        self._seen_ids = set()
        self._stop = threading.Event()

    # ---------------- 生命周期 ----------------
    def stop(self):
        """非阻塞置位：线程最多再跑完当前一拍（含 HTTP 超时）就退出。
        调用方（UI 线程）绝不 wait——这正是当年界面冻结的根源。"""
        self._stop.set()

    def run(self):
        # 启动时先记一份现有历史，避免把上一次会话的旧任务当成"刚完成"
        h = _get_json(self.base + "/history?max_items=50")
        if isinstance(h, dict):
            self._seen_ids = set(h.keys())
        idle_ticks = 0
        while not self._stop.wait(2.0):   # 2 秒一拍；stop() 可立即打断等待
            q = _get_json(self.base + "/queue")
            if not isinstance(q, dict):
                idle_ticks += 1
                if idle_ticks > 15:      # 30 秒连不上 → 停（实例已停）
                    return
                continue
            idle_ticks = 0
            running = len(q.get("queue_running") or [])
            pending = len(q.get("queue_pending") or [])
            if running > 0 or pending > 0:
                self._had_task = True
                continue
            if not self._had_task:
                continue                  # 一直空闲，不处理
            # 从"有任务"变为"队列空" → 查历史找新条目
            self._had_task = False
            h = _get_json(self.base + "/history?max_items=8")
            if not isinstance(h, dict) or not h:
                continue
            new_ids = [pid for pid in h.keys() if pid not in self._seen_ids]
            if not new_ids:
                continue
            for pid in new_ids:
                self._seen_ids.add(pid)
            latest = h.get(new_ids[-1], {})
            self._emit_result(latest)

    def _emit_result(self, entry: dict):
        try:
            status = (entry.get("status") or {})
            status_str = status.get("status_str") or "success"
            outputs = entry.get("outputs") or {}
            images = 0
            for node_out in outputs.values():
                if isinstance(node_out, dict):
                    images += len(node_out.get("images") or [])
            msgs = status.get("messages") or []
            if status_str == "error":
                err_msg = "执行出错"
                for m in msgs:
                    if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
                        info = m[1] if isinstance(m[1], dict) else {}
                        err_msg = (info.get("exception_message") or "执行出错")[:120]
                        break
                self.sig_task_error.emit(self.inst_name, err_msg)
                return
            if images:
                self.sig_task_done.emit(self.inst_name, f"任务完成，生成 {images} 张图")
            else:
                self.sig_task_done.emit(self.inst_name, "任务完成")
        except Exception:
            self.sig_task_done.emit(self.inst_name, "任务完成")
