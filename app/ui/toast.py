# -*- coding: utf-8 -*-
"""轻量消息提示条（toast）

2026-11-16 用户反馈：设置页「测试接口」失败时，长串报错被塞进表单下面那行
说明文字里，和操作结果混在一起，容易被当成页面文案忽略。

这里做成浮层提示：贴在主窗口底部居中、自动消失、不挡鼠标、同一时刻只留一条。
不打断操作（相比 QMessageBox 要点「确定」），又比塞进 Label 显眼。

用法：
    from app.ui.toast import show_toast
    show_toast(self.window(), "连接被拒绝…", level="error")

设计约束：
    · 父控件必须是**窗口**（self.window()），传页面会被 QScrollArea 裁掉；
    · 深色半透明底 + 白字，深/浅主题下都读得清（不依赖 theme.fg 映射）；
    · WA_TransparentForMouseEvents：提示条盖在按钮上也不能吃点击。
"""
from PySide6.QtCore import QEvent, QObject, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget,
)

# 语义色：与 theme 里的强调色保持一致
_COLORS = {
    "error": "#f0616d",
    "ok": "#3ecf8e",
    "info": "#4c8dff",
}
_ICON = {"error": "!", "ok": "✓", "info": "i"}
_MAX_W = 560          # 单行过长就换行，别横着铺满整个窗口
_MARGIN = 28          # 距窗口底部

_ACTIVE = None        # 同一时刻只留一条，新的顶掉旧的


class _Toast(QFrame):
    def __init__(self, parent: QWidget, text: str, level: str, ms: int):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        accent = _COLORS.get(level, _COLORS["info"])

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 9, 14, 9)
        h.setSpacing(9)

        mark = QLabel(_ICON.get(level, "i"))
        mark.setFixedSize(18, 18)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(
            "background:%s; color:#12161c; border-radius:9px;"
            " font-size:11px; font-weight:700;" % accent)
        h.addWidget(mark, 0, Qt.AlignTop)

        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.NoTextInteraction)
        lb.setStyleSheet("color:#eef3fa; font-size:12px;")
        # 宽度先钉住再量高度：不钉宽度的话 wordWrap 算出来的 sizeHint 是单行宽
        avail = max(220, min(_MAX_W, (parent.width() if parent else 900) - 96))
        lb.setFixedWidth(min(avail, max(180, lb.fontMetrics()
                                        .horizontalAdvance(text) + 4)))
        h.addWidget(lb, 1)

        self.setStyleSheet(
            "#Toast { background: rgba(22,26,33,0.97); border-radius: 10px;"
            " border: 1px solid %s; }" % accent)
        self._ms = max(1200, int(ms))
        self._fade_in = None
        self._fade_out = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    # -------------------------------------------------- 生命周期
    def popup(self):
        parent = self.parentWidget()
        self._place()
        if parent is not None:
            parent.installEventFilter(self)     # 窗口缩放时跟着重新居中
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        self.show()
        self.raise_()
        self._fade_in = QPropertyAnimation(eff, b"opacity", self)
        self._fade_in.setDuration(130)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.start()
        self._timer.start(self._ms)

    def dismiss(self):
        try:
            if self._timer.isActive():
                self._timer.stop()
            eff = self.graphicsEffect()
        except RuntimeError:          # 底层对象已被 Qt 回收
            return
        if eff is None:
            self.deleteLater()
            return
        self._fade_out = QPropertyAnimation(eff, b"opacity", self)
        self._fade_out.setDuration(200)
        self._fade_out.setStartValue(eff.opacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self.deleteLater)
        self._fade_out.start()

    def _place(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        w = min(self.width(), parent.width() - 2 * 16)
        h = self.sizeHint().height()
        self.resize(w, h)
        self.move(max(8, (parent.width() - w) // 2),
                  max(8, parent.height() - h - _MARGIN))

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() in (QEvent.Resize, QEvent.Move):
            self._place()
        return super().eventFilter(obj, ev)


def show_toast(parent, text: str, level: str = "info", ms: int = 0):
    """弹一条提示。parent 传窗口（self.window()），不是页面。

    ms=0 时按级别给默认时长：错误停留久一点（用户要看清），成功/普通提示短。
    """
    global _ACTIVE
    if parent is None or not str(text or "").strip():
        return None
    if ms <= 0:
        ms = 7000 if level == "error" else 3800
    old, _ACTIVE = _ACTIVE, None
    if old is not None:
        try:
            old._timer.stop()     # 别让它的超时回调打到已销毁的对象上
            old.hide()
            old.deleteLater()
        except RuntimeError:      # 底层对象已被 Qt 回收，忽略
            pass
    t = _Toast(parent, str(text), level, ms)
    _ACTIVE = t
    t.popup()
    return t
