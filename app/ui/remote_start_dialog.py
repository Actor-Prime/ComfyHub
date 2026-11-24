# -*- coding: utf-8 -*-
"""远程启动 / 停止 进度对话框（2026-12-01，0.8.40 T05）

交互照「插件依赖自检」的老习惯（PRD 5.1.2 对话框规格）：

  · 标题 `远程启动 — <实例名>`
  · 上部一行说明：命令在远端执行，输出实时回显。关掉这个窗口不会中断任务
  · 中部 `QPlainTextEdit(objectName=LogConsole)` 只读，逐行追加，超过 5000 行
    截断头部（`setMaximumBlockCount` 天然只保留尾部）
  · 下部状态行（会变）：正在连接 SSH… → 等待就绪 N 秒（最多 5 分钟）→ ✅ 已就绪
  · 底部按钮：「停止等待」（中止本轮轮询，**不杀远端进程**）/「关闭」

**本对话框不持有线程**：worker 归 `ComfyuiPage`（`swap_worker(self, "_ops_worker", w)`），
所以关窗后任务继续跑、日志继续写控制台 —— 这正是 PRD 要的「关掉窗口不中断任务」。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from app.i18n import T

MAX_LINES = 5000


class RemoteStartDialog(QDialog):
    """只负责显示；所有信号由 ComfyuiPage 连进来"""

    sig_cancel_wait = Signal()      # 用户点「停止等待」

    def __init__(self, parent, inst: dict, action: str = "start",
                 channel: str = "ssh"):
        super().__init__(parent)
        inst = inst or {}
        self._action = "stop" if str(action).lower() == "stop" else "start"
        self._channel = str(channel or "ssh")
        self._finished = False
        name = str(inst.get("name") or inst.get("id") or "远端")
        verb = "远程停止" if self._action == "stop" else "远程启动"
        self.setWindowTitle(f"{verb} — {name}")
        self.resize(720, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        if self._channel == "cloud":
            lead = "请求发往云平台接口，进度实时回显。关掉这个窗口不会中断任务"
        else:
            lead = "命令在远端执行，输出实时回显。关掉这个窗口不会中断任务"
        lb_lead = QLabel(lead)
        lb_lead.setWordWrap(True)
        lb_lead.setStyleSheet("color:#888; background:transparent;")
        v.addWidget(lb_lead)

        self.txt = QPlainTextEdit()
        self.txt.setObjectName("LogConsole")
        self.txt.setReadOnly(True)
        self.txt.setMaximumBlockCount(MAX_LINES)
        v.addWidget(self.txt, 1)

        self.lb_state = QLabel("正在准备…")
        self.lb_state.setWordWrap(True)
        self.lb_state.setStyleSheet("color:#7fa8d9; background:transparent;")
        v.addWidget(self.lb_state)

        h = QHBoxLayout()
        h.addStretch(1)
        self.b_stop_wait = QPushButton("停止等待")
        self.b_stop_wait.setToolTip("只中止本轮就绪轮询，不会杀掉远端进程")
        self.b_stop_wait.clicked.connect(self._on_stop_wait)
        self.b_close = QPushButton("关闭")
        self.b_close.clicked.connect(self.close)
        h.addWidget(self.b_stop_wait)
        h.addWidget(self.b_close)
        v.addLayout(h)

    # ---------------------------------------------------------------- 对外
    def append_log(self, text: str) -> None:
        line = str(text or "")
        if not line:
            return
        self.txt.appendPlainText(line)

    def set_state(self, text: str) -> None:
        self.lb_state.setText(str(text or ""))

    def finish(self, ok: bool, text: str = "") -> None:
        """任务结束：状态行定格，按钮只剩「关闭」"""
        self._finished = True
        if text:
            self.lb_state.setText(text)
        else:
            self.lb_state.setText(T("✅ 已就绪") if ok else T("已结束"))
        self.b_stop_wait.setEnabled(False)

    # ---------------------------------------------------------------- 内部
    def _on_stop_wait(self):
        self.b_stop_wait.setEnabled(False)
        self.lb_state.setText(T("正在停止等待…"))
        self.sig_cancel_wait.emit()

    def closeEvent(self, event):
        """关窗只是关窗：worker 归 ComfyuiPage，任务继续、日志改由控制台承接"""
        if not self._finished:
            self.setAttribute(Qt.WA_DeleteOnClose, False)
        super().closeEvent(event)
