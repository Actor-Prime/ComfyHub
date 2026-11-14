"""title_bar.py - 无边框主窗口的自绘标题栏（2026-11-14）。

背景：系统标题栏（左上角 logo + 「ComfyHub — ComfyUI 多实例管理器」文字）
被用户点名去掉，但**应用顶栏布局保持不变**：页面标题 + 实例切换 +
＋添加 ComfyUI。主窗口转 Frameless 后，这条栏就是标题栏——布局照旧，
只是在行尾补了最小化/最大化/关闭三个窗口键，底色透明（和内容区完全一致）。

注意：
- 拖动走 startSystemMove()（Windows 原生移动，自带 AERO 贴边分屏）；
  双击标题栏空白处切换最大化。
- 最大化状态下不响应拖动（frameless 下「拖动还原 + 光标跟随」需要
  窗口几何数学，收益小风险大，先不做）。
- Win11 悬停最大化按钮的 Snap Layouts 飞出菜单只有原生按钮才有，
  自绘按钮没有——无边框方案的已知代价，双击标题栏仍可最大化。
- 窗口边缘缩放由 install_window_grips() 的 8 个隐形热区负责，贴
  startSystemResize() 走系统级缩放（带最小尺寸约束、AERO Snap 约束）。
"""
from PySide6.QtCore import Qt, QEvent, QRectF, QPointF, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget


def _glyph_pixmap(kind, color, dpr=1.0, box=12):
    """画 12×12 的标准窗口符号（— □ ❐ ✕），1px 细线，形态对齐 Windows 原生。

    2026-11-14 用户反馈：之前用「—／□／✕」文本当图标，字号一变就大小不一。
    改成矢量绘制 —— 三类符号统一 12px 画布、统一 1px 线宽、统一视觉重量。
    抗锯齿恒开：横竖线靠 0.5px 坐标偏移保持锐利，斜线（✕）没有 AA 会出锯齿。
    """
    pm = QPixmap(int(box * dpr), int(box * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    pen = QPen(QColor(color))
    pen.setWidthF(1.0)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if kind == "min":
        p.drawLine(QPointF(2.5, 6.0), QPointF(9.5, 6.0))
    elif kind == "max":
        p.drawRect(QRectF(2.5, 2.5, 7.0, 7.0))
    elif kind == "restore":
        # 还原：后框只露上/右两条边，前框完整 —— 系统「叠两个方框」的简化画法
        p.drawLine(QPointF(4.5, 2.5), QPointF(9.5, 2.5))
        p.drawLine(QPointF(9.5, 2.5), QPointF(9.5, 7.5))
        p.drawLine(QPointF(9.5, 7.5), QPointF(7.5, 7.5))
        p.drawRect(QRectF(2.5, 4.5, 5.0, 5.0))
    else:   # close
        p.drawLine(QPointF(3.0, 3.0), QPointF(9.0, 9.0))
        p.drawLine(QPointF(9.0, 3.0), QPointF(3.0, 9.0))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class TitleBar(QFrame):
    """自绘标题栏：布局与旧 TopBar 一致（页面标题 | 实例控件），行尾窗口键。

    MainWindow 把实例切换控件注入 self.host；页面标题用 self.lb_page。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 8, 0)
        lay.setSpacing(10)

        # 页面标题（与旧 TopBar 同款：#AppLogo，_on_nav 切页时改字）
        self.lb_page = QLabel("工作台")
        self.lb_page.setObjectName("AppLogo")
        lay.addWidget(self.lb_page)
        lay.addStretch(1)

        # ---- 应用控件宿主：当前实例 + ＋添加 ComfyUI（由 MainWindow 注入，
        #      位置与旧 TopBar 相同：右侧）----
        self.host = QHBoxLayout()
        self.host.setSpacing(10)
        lay.addLayout(self.host)
        lay.addSpacing(6)

        # ---- 窗口控制（顺序：最小化 / 最大化 / 关闭，关停用 closeEvent 的
        #      「最小化到托盘」逻辑，和原生 X 行为一致）----
        self.b_min = self._btn("min", "TbMin", "最小化",
                               lambda: self.window().showMinimized())
        self.b_max = self._btn("max", "TbMax", "最大化",
                               self._toggle_max)
        self.b_close = self._btn("close", "TbClose", "关闭",
                                 lambda: self.window().close())
        for b in (self.b_min, self.b_max, self.b_close):
            lay.addWidget(b)
        self.b_max.setProperty("win_state", "max")

        # 最大化/还原切换时同步 □/❐ 图标
        self.window().installEventFilter(self)

    # ---------- 窗口控制 ----------
    def _win_icon(self, kind):
        """同一符号两态：常态灰、悬停亮（关闭键悬停白——底色是红的）"""
        from app.ui import theme
        dpr = self.devicePixelRatioF() or 1.0
        normal = theme.fg("#9aa4b2")
        hover = "#ffffff" if kind in ("close",) else theme.fg("#e6edf7")
        ic = QIcon()
        ic.addPixmap(_glyph_pixmap(kind, normal, dpr), QIcon.Normal)
        ic.addPixmap(_glyph_pixmap(kind, hover, dpr), QIcon.Active)
        ic.addPixmap(_glyph_pixmap(kind, hover, dpr), QIcon.Selected)
        return ic

    def _btn(self, kind, name, tip, on_click):
        b = QPushButton(self)
        b.setObjectName(name)
        b.setFixedSize(44, 30)
        b.setIconSize(QSize(12, 12))
        b.setIcon(self._win_icon(kind))
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.setToolTip(tip)
        b.clicked.connect(on_click)
        b._glyph_kind = kind          # 主题切换时按需重画
        return b

    def _toggle_max(self):
        w = self.window()
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    def _sync_max_icon(self):
        kind = "restore" if self.window().isMaximized() else "max"
        if self.b_max.property("win_state") != kind:
            self.b_max.setIcon(self._win_icon(kind))
            self.b_max.setProperty("win_state", kind)

    def eventFilter(self, obj, ev):
        if obj is self.window() and ev.type() == QEvent.Type.WindowStateChange:
            self._sync_max_icon()
        return super().eventFilter(obj, ev)

    def refresh_theme(self):
        """主题切换后重画窗口符号：浅色下换成深灰，别在米白底上糊成一片。

        _apply_theme 会对窗口内所有带 refresh_theme() 的控件回调（2026-11-14）。
        """
        self.b_min.setIcon(self._win_icon("min"))
        self.b_close.setIcon(self._win_icon("close"))
        self._sync_max_icon()

    # ---------- 拖动 / 双击 ----------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self.window().isMaximized():
            h = self.window().windowHandle()
            if h is not None:
                h.startSystemMove()
            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        # 落在窗口控制按钮/实例下拉上的双击不该触发最大化（子控件自己消费，
        # 但空白区归标题栏）
        if ev.button() == Qt.LeftButton and not self.childAt(ev.position().toPoint()):
            self._toggle_max()
            return
        super().mouseDoubleClickEvent(ev)


# ================= 窗口边缘缩放热区 =================
_CURSORS = {
    Qt.LeftEdge: Qt.SizeHorCursor,
    Qt.RightEdge: Qt.SizeHorCursor,
    Qt.TopEdge: Qt.SizeVerCursor,
    Qt.BottomEdge: Qt.SizeVerCursor,
    Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor,
    Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor,
    Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor,
    Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor,
}


class _Grip(QWidget):
    """贴在窗口最外圈的隐形热区：按住向对应方向做系统级缩放。"""

    def __init__(self, win, edges, parent=None):
        super().__init__(parent or win)
        self._win = win
        self._edges = edges
        self.setCursor(_CURSORS[edges])

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self._win.isMaximized():
            h = self._win.windowHandle()
            if h is not None:
                h.startSystemResize(self._edges)
            return
        super().mousePressEvent(ev)


_GRIP_KEYS = ("l", "r", "t", "b", "tl", "tr", "bl", "br")


def install_window_grips(win, thick=6):
    """给无边框主窗口装 8 个边缘缩放热区（左右上下 + 四角）。"""
    g = {"_thick": thick}
    for key, edges in (
        ("l", Qt.LeftEdge), ("r", Qt.RightEdge),
        ("t", Qt.TopEdge), ("b", Qt.BottomEdge),
        ("tl", Qt.LeftEdge | Qt.TopEdge),
        ("tr", Qt.RightEdge | Qt.TopEdge),
        ("bl", Qt.LeftEdge | Qt.BottomEdge),
        ("br", Qt.RightEdge | Qt.BottomEdge),
    ):
        g[key] = _Grip(win, edges)
        g[key].raise_()          # 必须压在内容之上，否则热区点不到
    win._grips = g
    layout_grips(win)


def layout_grips(win):
    """窗口尺寸变化后重摆热区（MainWindow.resizeEvent 里调用）。"""
    g = getattr(win, "_grips", None)
    if not g:
        return
    W, H = win.width(), win.height()
    T = g["_thick"]
    C = T * 2       # 角热区边长，比直边厚一倍好抓
    g["l"].setGeometry(0, C, T, max(0, H - 2 * C))
    g["r"].setGeometry(W - T, C, T, max(0, H - 2 * C))
    g["t"].setGeometry(C, 0, max(0, W - 2 * C), T)
    g["b"].setGeometry(C, H - T, max(0, W - 2 * C), T)
    g["tl"].setGeometry(0, 0, C, C)
    g["tr"].setGeometry(W - C, 0, C, C)
    g["bl"].setGeometry(0, H - C, C, C)
    g["br"].setGeometry(W - C, H - C, C, C)


def set_grips_visible(win, visible):
    """最大化/还原时隐藏/恢复热区（还原图钉光标会误导用户）。"""
    g = getattr(win, "_grips", None)
    if not g:
        return
    for key in _GRIP_KEYS:
        g[key].setVisible(visible)
