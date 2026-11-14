# -*- coding: utf-8 -*-
"""自绘标题栏回归（2026-11-14 用户反馈「顶栏融到下面，别两层横条显廉价」）

要守住的东西：
  A 主窗口必须 Frameless，标题栏 = TitleBar 一条（logo + 实例切换 +
    ＋添加 ComfyUI + 窗口控制），不再有独立 TopBar / 分隔线
  B 实例控件真的挂在 TitleBar.host 里（不是又被塞回某个页面/顶栏）
  C 窗口状态联动：最大化时边缘热区隐藏、□/❐ 图标同步；还原后恢复
  D 边缘缩放热区 8 个都在窗口边界上（layout_grips 没摆出界）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_title_bar.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print(" FAIL " + name + ("  ← " + str(detail) if detail else ""))


def main():
    from app.ui.main_window import MainWindow
    from app.ui.title_bar import _GRIP_KEYS

    win = MainWindow()

    print("== A. Frameless + 布局不变的顶栏 ==")
    check("窗口是 Frameless", bool(win.windowFlags() & Qt.FramelessWindowHint))
    check("TitleBar 存在且是 title_bar", win.title_bar is not None
          and win.title_bar.objectName() == "TitleBar")
    check("页面标题在标题栏里（工作台）",
          win.title_bar.lb_page.text() == "工作台"
          and win.title_bar.lb_page.objectName() == "AppLogo",
          win.title_bar.lb_page.text())

    print("== B. 实例控件挂在 TitleBar.host ==")
    host_widgets = []
    for i in range(win.title_bar.host.count()):
        w = win.title_bar.host.itemAt(i).widget()
        if w is not None:
            host_widgets.append(w)
    check("host 里有实例下拉", win.cb_inst in host_widgets, host_widgets)
    check("host 里有「＋ 添加 ComfyUI」按钮", any(
        getattr(w, "property", lambda k: None)("accent") for w in host_widgets))
    check("host 里有「当前实例」说明标签", any(
        getattr(w, "text", lambda: "")() == "当前实例" for w in host_widgets))
    check("窗口控制三键都在", all(
        hasattr(win.title_bar, a) for a in ("b_min", "b_max", "b_close")))

    print("== C. 最大化联动（热区隐藏 / □❐ 图标同步） ==")
    win.show()
    app.processEvents()
    check("普通态热区可见", all(
        win._grips[k].isVisible() for k in _GRIP_KEYS))
    check("普通态是「最大化」图标",
          win.title_bar.b_max.property("win_state") == "max",
          win.title_bar.b_max.property("win_state"))
    win.showMaximized()
    app.processEvents()
    check("最大化后热区隐藏", all(
        not win._grips[k].isVisible() for k in _GRIP_KEYS))
    check("最大化后是「还原」图标",
          win.title_bar.b_max.property("win_state") == "restore",
          win.title_bar.b_max.property("win_state"))
    win.showNormal()
    app.processEvents()
    check("还原后热区恢复", all(
        win._grips[k].isVisible() for k in _GRIP_KEYS))
    check("还原后回「最大化」图标",
          win.title_bar.b_max.property("win_state") == "max")

    print("== C2. 窗口键是矢量图标（不是文字字形） ==")
    for b in (win.title_bar.b_min, win.title_bar.b_max, win.title_bar.b_close):
        check(b.objectName() + " 有图标", not b.icon().isNull())
        check(b.objectName() + " 文字为空（不靠字形撑大小）", b.text() == "",
              b.text())
        check(b.objectName() + " 图标尺寸统一 12×12",
              b.iconSize().width() == 12 and b.iconSize().height() == 12)

    print("== D. 边缘热区贴着窗口边界 ==")
    win.resize(1200, 760)
    app.processEvents()
    W, H = win.width(), win.height()
    g = win._grips
    check("右热区贴右边", g["r"].geometry().right() >= W - 1,
          g["r"].geometry())
    check("下热区贴下边", g["b"].geometry().bottom() >= H - 1,
          g["b"].geometry())
    check("左上角热区在原点", g["tl"].geometry().topLeft().manhattanLength() == 0)
    check("右下角热区贴右下角", g["br"].geometry().bottomRight()
          .x() >= W - 1 and g["br"].geometry().bottomRight().y() >= H - 1)
    win.close()

    print()
    print("PASS %d / FAIL %d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
