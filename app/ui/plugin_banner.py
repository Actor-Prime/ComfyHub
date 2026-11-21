# -*- coding: utf-8 -*-
"""plugin_banner.py - 插件加载失败提示条（2026-11-14）

为什么要有：插件缺依赖时 ComfyUI 只在启动日志里打几行，界面上一片正常，
用户要等到「工作流提交报 HTTP 400」才知道出事了（2026-11-14 现场：
XB_ToolBox 缺 cv2 → 子图节点没注册 → 校验失败）。这条横幅把日志里的
失败直接翻成一句话，并给一个能点的补装入口。

设计取舍：
  · 和更新提示条一样放主窗口，任何页面都看得见（用户启动后多半去控制台/
    工作流页，不会留在实例页）
  · 非模态、不打断 —— 插件缺依赖往往不影响出图，别把用户从任务里拽出来
  · 只报「明确缺模块」的：单纯 IMPORT FAILED 但解析不出模块名时，文案降级
    成提示去看依赖自检，不硬推补装（可能是插件跟内核不兼容）
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton)

# 横幅里的按钮固定 26px 高，而全局 QPushButton 有 padding:7px 15px —— 内容区只剩
# 10px，12px 字（行高 15px）上下会被裁切（2026-11-22 客户报「文字显示不全」）。
# 只覆盖内边距，颜色/边框仍走全局 QSS，主题翻转不受影响。
_TIGHT_BTN = ('QPushButton{padding:0 12px;}'
              'QPushButton[accent="true"]{padding:0 12px;}')


class PluginBanner(QFrame):
    """主窗口顶栏下方的一条告警（一键补装 / 日志 / ✕）"""

    sig_fix = Signal()          # 用户请求去补装
    sig_close = Signal()        # 手动关掉（本批失败不再提示）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PluginBanner")
        self.setFixedHeight(38)
        # 底色/描边/文字色都在 theme.py 的全局 QSS（#PluginBanner）里，
        # 浅色主题由 _LIGHT_MAP 换色 —— 这里再写一份就会跟主题打架
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 0, 12, 0)
        h.setSpacing(10)
        self.lb = QLabel("")
        self.lb.setStyleSheet("background:transparent; font-size:12px;")
        self.lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(self.lb)
        h.addStretch(1)
        self.b_fix = QPushButton("一键补装")
        self.b_fix.setProperty("accent", True)
        self.b_fix.setFixedHeight(26)
        # 同 update_banner：全局 QPushButton 有 padding:7px 15px，压到 26px 高后
        # 内容区只剩 10px，12px 字（行高 15px）会被上下裁切。只覆盖内边距。
        self.b_fix.setStyleSheet(_TIGHT_BTN)
        self.b_fix.clicked.connect(self.sig_fix.emit)
        h.addWidget(self.b_fix)
        b_note = QPushButton("日志")
        b_note.setFixedHeight(26)
        b_note.setStyleSheet(_TIGHT_BTN)
        b_note.setToolTip("看 ComfyUI 启动日志里报错的那几行")
        b_note.clicked.connect(self._show_lines)
        h.addWidget(b_note)
        b_close = QPushButton("✕")
        b_close.setFixedSize(24, 26)
        b_close.setToolTip("关闭（本批插件失败不再提示）")
        b_close.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#93a3b8;padding:0;}")
        b_close.clicked.connect(self._on_close)
        h.addWidget(b_close)
        self._lines = []
        self.hide()

    # ---------- 对外 ----------
    def show_hit(self, text: str, lines=None, deps_hint: bool = True,
                 tip: str = ""):
        """显示一条告警；deps_hint=False 时把「一键补装」降级成「去自检」"""
        self.lb.setText(text)
        self._lines = list(lines or [])
        # 一行文案塞不下所有插件，全文进 tooltip（比点开日志更快看到自己关心的那个）
        self.lb.setToolTip(tip or text)
        self.b_fix.setText("一键补装" if deps_hint else "打开依赖自检")
        self.b_fix.setToolTip(
            "按缺失的包补装（只装没装的，已装版本一个都不动）" if deps_hint
            else "解析不出缺哪个包，去依赖自检看具体情况")
        self.show()

    def _on_close(self):
        self.hide()
        self.sig_close.emit()

    def _show_lines(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("插件加载失败的日志")
        dlg.setIcon(QMessageBox.Warning)
        dlg.setText(self.lb.text())
        dlg.setDetailedText("\n".join(self._lines) or "（没有留下原文）")
        dlg.setStandardButtons(QMessageBox.Ok)
        dlg.exec()
