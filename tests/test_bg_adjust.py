"""背景图调整（拖动/缩放）回归测试 — 2026-11-14。

覆盖：cover_geometry 数学（cover 基准、短边等比放大、偏移夹取、永不露边）、
预览窗交互（拖动改偏移、滚轮改缩放且围绕中心）、对话框结果、_Backdrop 参数夹取。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPointF, QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.ui.bg_adjust import (BgAdjustDialog, ZOOM_MAX, ZOOM_MIN,
                              _Preview, clamp, cover_geometry)

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  [%s]" % (detail,)) if detail != "" else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, eps=1e-6):
    return abs(a - b) <= eps


def wheel(widget, dy):
    QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, dy),
                Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    # PySide6 不让丢弃返回值构造后再手动分发？——直接构造事件并 sendEvent
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    QApplication.sendEvent(widget, ev)


def drag(widget, dx, dy):
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(100, 100),
                        QPointF(100, 100), Qt.LeftButton, Qt.LeftButton,
                        Qt.NoModifier)
    QApplication.sendEvent(widget, press)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(100 + dx, 100 + dy),
                       QPointF(100 + dx, 100 + dy), Qt.NoButton,
                       Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(widget, move)
    rel = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(100 + dx, 100 + dy),
                      QPointF(100 + dx, 100 + dy), Qt.LeftButton, Qt.NoButton,
                      Qt.NoModifier)
    QApplication.sendEvent(widget, rel)


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    print("== A. cover_geometry 数学 ==")
    # 竖图 800x1200 放进 1440x900：cover 基准 = 1440x2160（短边=宽贴合）
    dw, dh, dx, dy = cover_geometry(1440, 900, 800, 1200)
    check("cover 基准短边贴合", near(dw, 1440) and near(dh, 2160), f"{dw}x{dh}")
    check("cover 基准水平贴边（偏移被夹 0）", near(dx, 0), dx)
    check("cover 基准垂直居中裁切", near(dy, (900 - 2160) / 2), dy)
    # 放大 2 倍：以短边为基准等比 → 2880x4320
    dw, dh, dx, dy = cover_geometry(1440, 900, 800, 1200, zoom=2.0)
    check("zoom=2 短边等比放大", near(dw, 2880) and near(dh, 4320))
    # 偏移极限：横向最多 ±0.25（(2880-1440)/2/2880）
    _, _, dx, _ = cover_geometry(1440, 900, 800, 1200, zoom=2.0, off_x=0.9)
    check("偏移越界夹取：0.9 → 图左缘齐视口左缘", near(dx, 0), dx)
    _, _, _, dy2 = cover_geometry(1440, 900, 800, 1200, zoom=2.0, off_y=-0.9)
    check("纵向负偏移夹到图下缘齐视口下缘", near(dy2 + cover_geometry(1440, 900, 800, 1200, zoom=2.0)[1], 900), dy2)
    # zoom 下限：任何小值都夹回 1.0（绝不小于 cover，不露边）
    dw, dh, _, _ = cover_geometry(1440, 900, 800, 1200, zoom=0.1)
    check("zoom 下限夹回 1.0", near(dw, 1440))
    # zoom 上限 4.0
    dw, _, _, _ = cover_geometry(1440, 900, 800, 1200, zoom=99)
    check("zoom 上限夹到 4.0", near(dw, 1440 * 4))
    # 任何合法组合都不露边（扫描 5x5 网格）
    exposed = False
    for z in (1.0, 1.3, 2.0, 3.5, 4.0):
        for ox in (-0.5, -0.2, 0, 0.2, 0.5):
            for oy in (-0.5, 0, 0.5):
                w2, h2, x2, y2 = cover_geometry(1440, 900, 800, 1200, z, ox, oy)
                if x2 > 0 or y2 > 0 or x2 + w2 < 1440 or y2 + h2 < 900:
                    exposed = True
    check("5x5x3 组合扫描：图片始终盖满视口", not exposed)
    # 横图放进竖视口（另一方向）
    dw, dh, dx, dy = cover_geometry(900, 1440, 1920, 1080)
    check("横图进竖视口：高度贴合", near(dh, 1440) and dh >= 1440)
    # 退化输入
    check("空图返回全 0", cover_geometry(100, 100, 0, 100) == (0.0, 0.0, 0.0, 0.0))

    print("== B. clamp 工具 ==")
    check("clamp 正常", near(clamp(5, 0, 3), 3) and near(clamp(-1, 0, 3), 0))

    print("== C. 预览窗交互 ==")
    # 合成 640x960 竖图
    pm = QPixmap(640, 960)
    pm.fill(Qt.darkBlue)
    pv = _Preview(pm, 1.0, 0.0, 0.0)
    pv.resize(720, 480)
    z0, ox0, oy0 = pv.values()
    check("初值", near(z0, 1.0) and near(ox0, 0.0))
    # 滚轮放大：zoom 按步进上升，围绕中心 off 同比缩放（0 保持 0）
    wheel(pv, 120)
    z1, ox1, oy1 = pv.values()
    check("滚轮上滚 → 放大", z1 > 1.0, z1)
    check("放大后仍不超上限", z1 <= 4.0)
    check("居中时缩放不漂移", near(ox1, 0.0) and near(oy1, 0.0))
    # 连滚 50 次也不超上限
    for _ in range(50):
        wheel(pv, 120)
    check("狂滚 50 次封顶 4.0", pv.values()[0] == ZOOM_MAX, pv.values()[0])
    for _ in range(200):
        wheel(pv, -120)
    check("反向狂滚落回下限 1.0", pv.values()[0] == ZOOM_MIN)
    # 拖动：放大后拖 40px，偏移应增加
    wheel(pv, 120)          # zoom > 1
    z2, _, _ = pv.values()
    drag(pv, 40, -30)
    _, ox2, oy2 = pv.values()
    # 40px / 绘制宽 → 归一化增量；只验证方向与上界
    check("右拖 → off_x 增加", ox2 > 0, ox2)
    check("上拖 → off_y 减少", oy2 < 0, oy2)
    check("拖动后仍在 [-0.5, 0.5]", -0.5 <= ox2 <= 0.5 and -0.5 <= oy2 <= 0.5)
    # 未按下时移动鼠标不产生偏移
    _, ox3, oy3 = pv.values()
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(300, 300),
                       QPointF(300, 300), Qt.NoButton, Qt.NoButton,
                       Qt.NoModifier)
    QApplication.sendEvent(pv, move)
    check("未按住拖动无效", near(ox3, pv.values()[1]) and near(oy3, pv.values()[2]))
    # 重置
    pv.reset()
    check("重置回 1.0/0/0", pv.values() == (1.0, 0.0, 0.0))

    print("== D. 对话框 ==")
    dlg = BgAdjustDialog("", 2.0, 0.3, -0.3)          # 路径无效
    check("图片加载失败 → values 走默认", dlg.values() == (1.0, 0.0, 0.0))
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "comfyhub_bg_test.png")
    pm.save(tmp)
    dlg2 = BgAdjustDialog(tmp, 1.5, 0.1, -0.1)
    check("有效图保留初值", dlg2.values() == (1.5, 0.1, -0.1), dlg2.values())
    os.remove(tmp)

    print("== E. _Backdrop 参数夹取 ==")
    from app.ui.main_window import _Backdrop
    bd = _Backdrop()
    bd.set_background("", 0.72, "dark", 0, zoom=99, off_x=9, off_y=-9)
    check("zoom 夹取", near(bd._zoom, 4.0), bd._zoom)
    check("off 夹取", near(bd._off_x, 0.5) and near(bd._off_y, -0.5))
    bd.set_background("", 0.72, "dark", 0, zoom="abc", off_x=None, off_y=None)
    check("非法类型回落默认", near(bd._zoom, 1.0) and near(bd._off_x, 0.0))

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
