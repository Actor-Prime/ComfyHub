"""背景图位置/缩放调整（2026-11-14 用户需求）。

用户反馈：选背景图后没法控制「看图片的哪一块」——竖图当横屏壁纸时
人物经常被裁掉。这里提供选图后的调整对话框：

- 拖动：鼠标按住图片拖到想要的位置
- 缩放：滚轮缩放。基准是 cover（短边贴边铺满），放大时**以短边为基准
  等比例放大**——短边永远 ≥ 视口短边，任何缩放级别都不会露出边缘
- 结果（zoom + 归一化偏移）存进 settings，主窗 _Backdrop 用同一套
  几何函数绘制，保证「所见即所得」

几何数学集中在 cover_geometry()（纯函数、无 Qt 依赖），预览窗和
主窗背景层共用，测试直接打数学不用起 GUI。
"""

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QWidget

ZOOM_MIN = 1.0     # 1.0 = cover 基准（短边刚好贴边）
ZOOM_MAX = 4.0
OFF_LIMIT = 0.5    # 归一化偏移上限（相对绘制尺寸）
ZOOM_STEP = 1.12   # 滚轮每格缩放系数


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def cover_geometry(vw, vh, iw, ih, zoom=1.0, off_x=0.0, off_y=0.0):
    """计算背景图在视口内的绘制几何。

    :param vw/vh: 视口宽高（px）
    :param iw/ih: 图片原始宽高（px）
    :param zoom: 缩放倍数，1.0 = cover 基准（短边贴合视口短边）
    :param off_x/off_y: 归一化偏移，相对绘制尺寸的比例（-0.5~0.5），
        0 = 居中。存归一化值而不是像素，窗口 resize 后相对位置不跑。
    :return: (dw, dh, dx, dy) 绘制尺寸与左上角位置（可为负 = 裁掉）

    越界保护：zoom 夹到 [1, 4]；偏移夹到「图片仍盖满视口」的范围内
    （图片边缘最多齐视口边，绝不露底色）。
    """
    if iw <= 0 or ih <= 0 or vw <= 0 or vh <= 0:
        return 0.0, 0.0, 0.0, 0.0
    zoom = clamp(float(zoom), ZOOM_MIN, ZOOM_MAX)
    base = max(vw / iw, vh / ih)          # cover：短边贴合
    dw, dh = iw * base * zoom, ih * base * zoom
    # 偏移极限：图片中心最多挪到「图片边 = 视口边」
    mx = (dw - vw) / 2.0 / dw if dw > vw else 0.0
    my = (dh - vh) / 2.0 / dh if dh > vh else 0.0
    off_x = clamp(float(off_x), -mx, mx)
    off_y = clamp(float(off_y), -my, my)
    dx = (vw - dw) / 2.0 + off_x * dw
    dy = (vh - dh) / 2.0 + off_y * dh
    return dw, dh, dx, dy


class _Preview(QWidget):
    """预览画布：画图 + 鼠标拖动 + 滚轮缩放。"""

    def __init__(self, pix: QPixmap, zoom, off_x, off_y, parent=None):
        super().__init__(parent)
        self._pix = pix
        self._zoom = clamp(float(zoom), ZOOM_MIN, ZOOM_MAX)
        self._ox = clamp(float(off_x), -OFF_LIMIT, OFF_LIMIT)
        self._oy = clamp(float(off_y), -OFF_LIMIT, OFF_LIMIT)
        self._last = QPoint()
        self._dragging = False
        self.setMinimumSize(560, 360)
        self.setMouseTracking(False)
        self.setCursor(Qt.OpenHandCursor)

    # ---- 结果 ----
    def values(self):
        return self._zoom, self._ox, self._oy

    def reset(self):
        self._zoom, self._ox, self._oy = 1.0, 0.0, 0.0
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, _ev):
        if self._pix.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        p.fillRect(r, Qt.black)
        iw, ih = self._pix.width(), self._pix.height()
        dw, dh, dx, dy = cover_geometry(r.width(), r.height(), iw, ih,
                                        self._zoom, self._ox, self._oy)
        p.drawPixmap(QRectF(dx, dy, dw, dh), self._pix,
                     QRectF(0, 0, iw, ih))

    # ---- 交互 ----
    def _geom(self):
        r = self.rect()
        return cover_geometry(r.width(), r.height(),
                              self._pix.width(), self._pix.height(),
                              self._zoom, self._ox, self._oy)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self._pix.isNull():
            self._dragging = True
            self._last = ev.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)

    def mouseMoveEvent(self, ev):
        if not self._dragging:
            return
        pos = ev.position().toPoint()
        dw, dh, _dx, _dy = self._geom()
        if dw <= 0 or dh <= 0:
            return
        # 鼠标往右拖 → 图片跟着往右 → 中心偏移增大
        self._ox = clamp(self._ox + (pos.x() - self._last.x()) / dw,
                         -OFF_LIMIT, OFF_LIMIT)
        self._oy = clamp(self._oy + (pos.y() - self._last.y()) / dh,
                         -OFF_LIMIT, OFF_LIMIT)
        self._last = pos
        self.update()

    def wheelEvent(self, ev):
        if self._pix.isNull():
            return
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        new_zoom = clamp(self._zoom * factor, ZOOM_MIN, ZOOM_MAX)
        if new_zoom == self._zoom:
            return
        # 围绕视口中心缩放：图片中心相对视口中心的偏移按比例缩放，
        # 放大时看到的中心内容保持不动（标准图片查看器手感）
        ratio = new_zoom / self._zoom
        self._ox = clamp(self._ox * ratio, -OFF_LIMIT, OFF_LIMIT)
        self._oy = clamp(self._oy * ratio, -OFF_LIMIT, OFF_LIMIT)
        self._zoom = new_zoom
        self.update()


class BgAdjustDialog(QDialog):
    """背景图调整对话框：预览 + 提示 + 重置/确定/取消。"""

    def __init__(self, image_path: str, zoom=1.0, off_x=0.0, off_y=0.0,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("背景图调整")
        self.setModal(True)
        self.setMinimumSize(640, 500)

        pix = QPixmap(image_path) if image_path else QPixmap()
        self.preview = _Preview(pix, zoom, off_x, off_y, self)

        tip = QLabel("拖动调整位置 · 滚轮缩放（以短边为基准等比放大，不会露边）")
        tip.setObjectName("BgAdjustTip")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a94a4; background:transparent;")

        b_reset = QPushButton("重置")
        b_reset.clicked.connect(self._on_reset)
        b_ok = QPushButton("确定")
        b_ok.setProperty("accent", True)
        b_ok.clicked.connect(self.accept)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(b_reset)
        btns.addWidget(b_ok)
        btns.addWidget(b_cancel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)
        lay.addWidget(self.preview, 1)
        lay.addWidget(tip)
        lay.addLayout(btns)

        self._pix = pix

    def _on_reset(self):
        self.preview.reset()

    def values(self):
        """(zoom, off_x, off_y)；图片加载失败时返回默认值。"""
        if self._pix.isNull():
            return 1.0, 0.0, 0.0
        return self.preview.values()
