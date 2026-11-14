# -*- coding: utf-8 -*-
"""状态卡矢量小图标（2026-11-15）。

用户反馈：工作台状态卡用 emoji 当图标，「内存=🧠（脑子）」「处理器=⚙（齿轮）」
与内容不符、且各卡风格不统一（emoji 各家字体渲染差异大）。

改成 QPainter 矢量线稿：四类符号统一 16 单位画布、统一线宽与圆角端点，
颜色在 paintEvent 里实时走 theme.fg()（浅色主题自动换深色，无需手动刷新 ——
与 home_page 顶部 logo 的做法一致，见 home_page.py 的 paintEvent）。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame

from app.ui import theme


def _paint(p, kind, color):
    """在 16×16 逻辑坐标里画第 kind 类符号。调用方已备好画笔环境。"""
    pen = QPen(color, 1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    if kind == "ram":
        # 内存条：横向 PCB + 内部颗粒分隔 + 底部金手指
        p.drawRoundedRect(QRectF(1.8, 3.8, 12.4, 6.6), 1.2, 1.2)
        for x in (5.4, 8.4, 11.0):
            p.drawLine(x, 5.6, x, 8.6)
        for x in (3.6, 6.6, 9.6, 12.4):
            p.drawLine(x, 10.4, x, 12.8)
    elif kind == "cpu":
        # 处理器：芯片基板 + 核心 + 四边引脚
        p.drawRoundedRect(QRectF(3.4, 3.4, 9.2, 9.2), 1.4, 1.4)
        p.drawRect(QRectF(6.2, 6.2, 3.6, 3.6))
        for x in (5.6, 8.0, 10.4):
            p.drawLine(x, 1.2, x, 3.0)
            p.drawLine(x, 13.0, x, 14.8)
            p.drawLine(1.2, x, 3.0, x)
            p.drawLine(13.0, x, 14.8, x)
    elif kind == "gpu":
        # 显卡：卡体 + 散热风扇 + 尾部挡板（16px 下装饰线条会糊，从简）
        p.drawRoundedRect(QRectF(1.5, 4.5, 11.5, 7.0), 1.2, 1.2)
        p.drawEllipse(QRectF(4.2, 6.1, 3.8, 3.8))
        p.drawLine(10.4, 6.4, 10.4, 9.6)
        p.drawLine(14.8, 4.0, 14.8, 12.0)  # 机箱挡板
    elif kind == "disk":
        # 硬盘：盘体 + 数据分隔线 + 指示灯（经典 HDD 符号）
        p.drawRoundedRect(QRectF(1.8, 3.8, 12.4, 8.4), 1.4, 1.4)
        p.drawLine(1.8, 8.6, 14.2, 8.6)
        p.drawEllipse(QRectF(10.9, 9.9, 1.5, 1.5))
    else:
        p.drawRect(QRectF(3, 3, 10, 10))


class StatIcon(QFrame):
    """30×30 圆角底板（QSS #StatIcon 照常生效）+ 居中矢量符号。"""

    def __init__(self, kind="ram", parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setObjectName("StatIcon")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(30, 30)

    def set_kind(self, kind):
        if kind != self._kind:
            self._kind = kind
            self.update()

    def kind(self):
        return self._kind

    def paintEvent(self, ev):
        # 先让 QSS 画圆角底板，再叠加符号
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(theme.fg("#e6edf7"))
        # 16 单位画布缩放到控件内圈（留 7px 呼吸边）
        side = min(self.width(), self.height()) - 14.0
        sc = side / 16.0
        p.translate((self.width() - 16 * sc) / 2.0,
                    (self.height() - 16 * sc) / 2.0)
        p.scale(sc, sc)
        try:
            _paint(p, self._kind, color)
        finally:
            p.end()
