# -*- coding: utf-8 -*-
"""下载中心：全局下载队列 + 右上角任务指示器（下拉面板显示每个任务的进度）

用法（models_page）：
    self.dl_center = DownloadCenter()
    head.addWidget(self.dl_center)              # 指示器按钮放进标题栏
    self.dl_center.sig_queue_done.connect(self.refresh_stats)

入队：
    self.dl_center.enqueue([(repo_id, file_path, dest_dir), ...])
"""
import itertools
import os

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QToolButton, QMenu, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSizePolicy, QWidgetAction,
)

from app import model_store as ms
from app.workers import swap_worker


class _QueueWorker(QThread):
    """顺序下载队列。items = [(repo_id, file_path, dest_dir, task_id), ...]"""
    sig_progress = Signal(str, int, int)          # task_id, done, total
    sig_state = Signal(str, str)                  # task_id, state(下载中/完成/失败/已取消)
    sig_queue_done = Signal(int, int)             # ok, bad

    def __init__(self, items):
        super().__init__()
        self.items = items
        self._cancel_task = None                  # 当前要取消的 task_id
        self._cancel_all = False

    def cancel_task(self, task_id):
        self._cancel_task = task_id

    def cancel_all(self):
        self._cancel_all = True
        if self.items:
            self._cancel_task = self.items[0][3]

    def run(self):
        ok = bad = 0
        for repo_id, file_path, dest_dir, task_id in self.items:
            if self._cancel_all:
                self.sig_state.emit(task_id, "已取消")
                continue
            fname = os.path.basename(file_path)
            self.sig_state.emit(task_id, f"下载中 {fname}")
            try:
                ms.download_file(
                    repo_id, file_path, dest_dir,
                    progress_cb=lambda d, t, tid=task_id: self.sig_progress.emit(tid, d, t),
                    cancel_flag=lambda tid=task_id: self._cancel_task == tid,
                )
                self.sig_state.emit(task_id, "完成")
                ok += 1
            except KeyboardInterrupt:
                self.sig_state.emit(task_id, "已取消（保留 .part）")
                bad += 1
            except Exception as e:
                self.sig_state.emit(task_id, f"失败：{str(e)[:60]}")
                bad += 1
        self.sig_queue_done.emit(ok, bad)


class _TaskRow(QWidget):
    """面板里的单任务行：文件名 + 进度条 + 状态"""

    def __init__(self, task_id, fname, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(2)
        h = QHBoxLayout()
        self.lb_name = QLabel(fname)
        self.lb_name.setStyleSheet("color:#dbe4f0;")
        h.addWidget(self.lb_name, 1)
        self.lb_state = QLabel("排队中")
        self.lb_state.setStyleSheet("color:#9aa4b2;")
        h.addWidget(self.lb_state)
        v.addLayout(h)
        self.pb = QProgressBar()
        self.pb.setFixedHeight(8)
        self.pb.setTextVisible(False)
        self.pb.setRange(0, 100)
        v.addWidget(self.pb)

    def set_progress(self, done, total):
        if total > 0:
            self.pb.setMaximum(total)
            self.pb.setValue(done)
        self.lb_state.setText(f"{done / 1048576:,.0f}/{total / 1048576:,.0f} MB")

    def set_state(self, text, color="#9aa4b2"):
        self.lb_state.setText(text)
        self.lb_state.setStyleSheet(f"color:{color};")
        if text == "完成":
            self.pb.setValue(self.pb.maximum())


class DownloadCenter(QToolButton):
    """右上角下载指示器按钮 + 下拉任务面板 + 全局下载队列"""

    sig_queue_done = Signal(int, int)             # 转发：队列结束（页面刷新列表）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("⬇ 0")
        self.setToolTip("下载任务")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setFixedHeight(30)

        self._menu = QMenu(self)
        self._panel = QWidget()
        self._panel_v = QVBoxLayout(self._panel)
        self._panel_v.setContentsMargins(4, 4, 4, 4)
        self._panel_v.setSpacing(2)
        self._empty = QLabel("暂无下载任务")
        self._empty.setStyleSheet("color:#9aa4b2; padding:8px;")
        self._panel_v.addWidget(self._empty)
        self._menu.setMinimumWidth(340)
        act = QWidgetAction(self._menu)
        act.setDefaultWidget(self._panel)     # QMenu 嵌入自定义面板（QWidgetAction）
        self._menu.addAction(act)
        self.setMenu(self._menu)

        self._rows = {}                           # task_id -> _TaskRow
        self._worker = None
        self._pending = []                        # 等入队的任务
        self._n_done = 0

    # -------------------------------------------------- 入队
    def enqueue(self, items):
        """items = [(repo_id, file_path, dest_dir), ...]"""
        import itertools
        for repo_id, file_path, dest_dir in items:
            task_id = f"t{next(self._ids)}"
            fname = os.path.basename(file_path)
            row = _TaskRow(task_id, fname)
            self._panel_v.addWidget(row)
            self._rows[task_id] = row
            self._pending.append((repo_id, file_path, dest_dir, task_id))
        self._empty.setVisible(False)
        self._update_badge()
        self._pump()

    _ids = itertools.count(1)

    def _pump(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._pending:
            return
        w = _QueueWorker(self._pending)
        self._pending = []
        w.sig_progress.connect(self._on_progress)
        w.sig_state.connect(self._on_state)
        w.sig_queue_done.connect(self._on_queue_done)
        swap_worker(self, "_worker", w)
        w.start()

    # -------------------------------------------------- 队列信号
    def _on_progress(self, task_id, done, total):
        row = self._rows.get(task_id)
        if row:
            row.set_progress(done, total)

    def _on_state(self, task_id, state):
        row = self._rows.get(task_id)
        if row:
            color = ("#7ee787" if state == "完成"
                     else "#ff7b72" if state.startswith("失败")
                     else "#9aa4b2")
            row.set_state(state, color)
        self._update_badge()

    def _on_queue_done(self, ok, bad):
        self._pump()                              # 继续处理后续入队
        if not self._pending and not (self._worker and self._worker.isRunning()):
            QTimer.singleShot(8000, self._maybe_clear)   # 8 秒后清空已完成面板
        self.sig_queue_done.emit(ok, bad)

    def _maybe_clear(self):
        if self._worker and self._worker.isRunning():
            return
        for tid, row in list(self._rows.items()):
            if row.lb_state.text() in ("完成", "已取消"):
                row.setParent(None)
                self._rows.pop(tid, None)
        if not self._rows:
            self._empty.setVisible(True)
        self._update_badge()

    def _update_badge(self):
        active = sum(1 for r in self._rows.values()
                     if r.lb_state.text().startswith(("下载中", "排队")))
        self.setText(f"⬇ {active}" if active else "⬇ 0")
