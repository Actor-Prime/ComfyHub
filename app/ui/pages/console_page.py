# -*- coding: utf-8 -*-
"""控制台页：全局运行日志 + 语义化颜色规范
色彩规范：
  🔴 红   #e8635a  [ERROR] / Traceback / Exception / failed —— 必须关注的错误
  🟠 橙黄 #e8a33d  [WARNING] / warn / deprecated —— 警告
  🔵 蓝   #7fa8d9  [INFO] —— 常规信息
  🟢 绿   #4cd47c  Prompt executed / got prompt / Starting server —— 成功与出图完成
  🟣 橙   #e8871e  [ComfyHub] / [计划] / [插件] / [工具] —— 本软件自身操作
  ⚫ 暗灰 #6b7480  [safe-delete] / SAFE_DELETE_ —— 外部环境删除防护层噪声（降噪显示，非错误）
  ⚪ 灰   #b8c2ce  其余普通输出
"""
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor, QTextCharFormat, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QCheckBox,
)

from app.proc_manager import ProcManager
from app.ui import theme

_COLOR_RULES = [
    (re.compile(r"\[ERROR\]|Traceback|Exception|failed|Cannot|错误", re.I), "#e8635a"),
    (re.compile(r"\[WARNING\]|warning|deprecated", re.I), "#e8a33d"),
    (re.compile(r"Prompt executed|got prompt|Starting server|To see the GUI|成功|完成"), "#4cd47c"),
    (re.compile(r"\[INFO\]"), "#7fa8d9"),
]
# 0.8.40：`[远程启动 xx]` / `[远程停止 xx]` / `[隧道 xx]` 也归「本软件操作」色，
# 免得远程任务的输出被当成远端 ComfyUI 的日志（前缀里有实例名，用 [^\]]* 带过）
_SELF_PREFIX = re.compile(r"^\[(ComfyHub|计划|插件|工具|救援|远程[^\]]*|隧道[^\]]*)\]")
# 外部环境注入的删除防护层日志（如 WorkBuddy safe-delete shim），不是本软件错误——降噪显示
_ENV_NOISE = re.compile(r"\[safe-delete\]|SAFE_DELETE_", re.I)


def classify_color(line: str) -> str:
    """按行内容返回颜色（优先级：环境噪声 > 自身日志 > 错误 > 警告 > 成功 > 信息 > 默认）"""
    if _ENV_NOISE.search(line):
        return "#6b7480"
    if _SELF_PREFIX.search(line):
        return "#e8871e"
    for pat, color in _COLOR_RULES:
        if pat.search(line):
            return color
    return "#d8dee6"


class ConsolePage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._last_seq = 0
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)

        bar = QHBoxLayout()
        lb = QLabel("全局运行日志（全部实例）")
        lb.setStyleSheet("color:#9aa4b2; font-size:13px;")
        from app.ui import theme
        bar.addWidget(lb)
        bar.addStretch()
        # 颜色图例
        legend = QLabel(self._legend_html())
        legend.setStyleSheet("background:transparent; font-size:11px;")
        self._legend = legend
        bar.addWidget(legend)
        bar.addStretch()
        self.ck_follow = QCheckBox("自动滚动")
        self.ck_follow.setChecked(True)
        bar.addWidget(self.ck_follow)
        b_clear = QPushButton("清屏")
        b_clear.clicked.connect(self._clear)
        b_copy = QPushButton("复制全部")
        b_copy.clicked.connect(self._copy)
        bar.addWidget(b_clear)
        bar.addWidget(b_copy)
        root.addLayout(bar)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("LogConsole")
        f = QFont("Consolas", 9)
        f.setStyleHint(QFont.Monospace)
        self.console.setFont(f)
        self.console.setMaximumBlockCount(5000)
        root.addWidget(self.console, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._pull)
        self._timer.start()

    def inst_name(self, iid):
        for i in self.cfg["instances"]:
            if i.get("id") == iid:
                return i.get("name", iid)
        return iid

    def _append_colored(self, text: str):
        """按颜色规范插入一行"""
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(theme.fg(classify_color(text))))
        cursor.insertText(text + "\n", fmt)

    def _pull(self):
        buf = self.mainwin.procs.log_buffer
        total = len(buf)
        if total <= self._last_seq:
            return
        if self._last_seq == 0:
            items = list(buf)
        else:
            items = list(buf)[-(total - self._last_seq):]
        self._last_seq = total
        show_prefix = len(self.cfg["instances"]) > 1
        for iid, line in items:
            if show_prefix:
                # 实例名前缀用暗灰，正文按规范着色
                cursor = self.console.textCursor()
                cursor.movePosition(QTextCursor.End)
                pf = QTextCharFormat()
                pf.setForeground(QColor(theme.fg("#5a636f")))
                cursor.insertText(f"[{self.inst_name(iid)}] ", pf)
                self._append_colored(line)
            else:
                self._append_colored(line)
        if self.ck_follow.isChecked():
            self.console.moveCursor(QTextCursor.End)

    def _legend_html(self) -> str:
        """图例用运行期色：浅色主题下这几块粉彩色在米白底上几乎看不见"""
        items = (("错误", "#e8635a"), ("警告", "#e8a33d"), ("信息", "#7fa8d9"),
                 ("成功/出图", "#4cd47c"), ("ComfyHub", "#e8871e"))
        return "&nbsp;&nbsp;".join(
            "<span style='color:%s;'>■ %s</span>" % (theme.fg(c), n)
            for n, c in items)

    def refresh_theme(self):
        """切主题后重刷日志与图例

        日志颜色是运行期写进 QTextCharFormat 的，QSS 重染摸不到；
        这里清屏后从缓冲区整段重放（_last_seq=0 时 _pull 会全量渲染）。
        """
        try:
            if getattr(self, "_legend", None) is not None:
                self._legend.setText(self._legend_html())
            self.console.clear()
            self._last_seq = 0
            self._pull()
        except Exception:
            pass

    def on_show(self):
        self._pull()

    def _clear(self):
        self.console.clear()
        self.mainwin.procs.clear()
        self._last_seq = 0

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.console.toPlainText())

    def append_line(self, text: str):
        """供其他页面直接写日志（保持颜色规范）"""
        self._append_colored(text)
