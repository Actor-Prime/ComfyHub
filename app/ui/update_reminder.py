# -*- coding: utf-8 -*-
"""update_reminder.py - 启动满一小时的更新提醒弹窗（2026-11-12）

与 UpdateBanner 的分工：
  · 启动时的自动检查 → 顶部横幅（不打断操作）
  · 启动满 1 小时后仍发现新版本 → 本弹窗主动提醒**一次**
    用户可：立即更新 / 延后提醒（1 小时 · 4 小时 · 1 天 · 3 天 · 1 周）/ 不再提醒此版本

结果用属性读取：dlg.action ∈ {"update", "later", "never", "close"}，dlg.delay_hours
"""
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from app import updater
from app import update_notify
from app.i18n import T


class UpdateReminderDialog(QDialog):
    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.action = "close"
        self.delay_hours = float(update_notify.DEFAULT_DELAY_HOURS)
        self.setWindowTitle(T("发现新版本"))
        self.setModal(True)
        self.setMinimumWidth(460)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        lb_title = QLabel(T("有新版本可用"))
        lb_title.setStyleSheet("font-size:15px; font-weight:bold;")
        v.addWidget(lb_title)

        cur = info.get("current") or updater.APP_VERSION
        latest = info.get("latest") or ""
        # 版本号单独成控件（i18n 铁律：动态值不进词表）
        lb_ver = QLabel(f"v{cur}  →  v{latest}")
        lb_ver.setStyleSheet("color:#5aa2ff; font-size:13px;")
        v.addWidget(lb_ver)

        notes = (info.get("notes") or "").strip()
        if notes:
            ed = QPlainTextEdit()
            ed.setPlainText(notes[:1200])
            ed.setReadOnly(True)
            ed.setFixedHeight(110)
            v.addWidget(ed)

        tip = QLabel(T("现在更新会关闭正在运行的实例。延后到点会立刻再提醒一次；"
                       "选「不再提醒」只在本次运行内静音，下次启动还会提示。"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a93a3; font-size:11px;")
        v.addWidget(tip)

        # ---- 操作行 ----
        h = QHBoxLayout()
        h.setSpacing(8)
        b_go = QPushButton(T("立即更新"))
        b_go.setProperty("accent", True)
        b_go.setDefault(True)
        b_go.clicked.connect(lambda: self._finish("update"))
        h.addWidget(b_go)

        self.cb_delay = QComboBox()
        for hours, label in update_notify.DELAY_CHOICES:
            self.cb_delay.addItem(T(label), hours)
        _prev = update_notify.last_delay_hours(
            (parent.cfg.get("settings") if getattr(parent, "cfg", None) else {}) or {})
        _i = self.cb_delay.findData(_prev)
        self.cb_delay.setCurrentIndex(_i if _i >= 0 else
                                      self.cb_delay.findData(
                                          update_notify.DEFAULT_DELAY_HOURS))
        h.addWidget(self.cb_delay)
        b_later = QPushButton(T("延后提醒"))
        b_later.clicked.connect(self._on_later)
        h.addWidget(b_later)

        h.addStretch(1)
        b_never = QPushButton(T("不再提醒此版本"))
        b_never.clicked.connect(lambda: self._finish("never"))
        h.addWidget(b_never)
        b_close = QPushButton(T("关闭"))
        b_close.clicked.connect(lambda: self._finish("close"))
        h.addWidget(b_close)
        v.addLayout(h)

    def _on_later(self):
        self.delay_hours = float(self.cb_delay.currentData()
                                 or update_notify.DEFAULT_DELAY_HOURS)
        self._finish("later")

    def _finish(self, action: str):
        self.action = action
        self.accept() if action in ("update", "later", "never") else self.reject()
