# -*- coding: utf-8 -*-
"""卡片圆角回归（2026-11-12 用户报障「圆角缺失、框边缘缺失」）。

事故：QFrame/QWidget 只 setObjectName + QSS（background + border-radius）时，
**不开 WA_StyledBackground 就只画直边、不填背景、不裁圆角** —— 用户看到的是
「卡片变成直角色块，边框像缺了一截」。工作台目录卡、实例设置行卡、环境检测卡
全都中了；工作台状态卡因为设了该属性所以是好的（对照实验）。

断言：
  A. QSS 里每个「带 background + border-radius」的 objectName 都要被
     theme._CARD_IDS（靠 WA_StyledBackground 的）或 _SELF_PAINTED（按钮/标签/
     进度条等 Qt 自己绘制、无需该属性的）覆盖 —— 防止新增卡片 ID 漏登记
  B. polish_styled_bg()：给光秃秃的 QFrame/#RowCard 能补上属性并 repolish
  C. 真实主窗里所有卡片类控件的 WA_StyledBackground 都为 True（源码+兜底双重保险）
  D. 五个曾经出问题的位置构造出来的控件都带该属性（防回退）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_styled_bg.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


# Qt 自带绘制、不需要 WA_StyledBackground 的控件 ID（按钮/标签/进度条/文本框）
_SELF_PAINTED = {
    "ScopeBtn", "SaveBtn", "DeleteBtn", "FixBtn",   # QPushButton
    "StatBar", "FixProgress",                        # QProgressBar
    "LogConsole",                                    # QPlainTextEdit
    "BannerScrim",                                   # 自绘 QFrame（QPainterPath）
}


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui import theme as th

    # ---------- A. QSS 卡片 ID 登记完整 ----------
    print("== A. QSS 卡片 ID 全登记 ==")
    qss = th.build_qss("dark")
    ids = set()
    for m in re.finditer(r"#([A-Za-z]\w*)\s*\{([^}]*)\}", qss):
        name, body = m.group(1), m.group(2)
        if "border-radius" in body and "background" in body:
            ids.add(name)
    unregistered = sorted(ids - th._CARD_IDS - _SELF_PAINTED)
    check("带圆角的 #ID 都归了类（卡片 or 自绘）", not unregistered,
          f"未登记: {unregistered}")

    # ---------- B. polish_styled_bg 能修 ----------
    print("== B. 运行时兜底 ==")
    from PySide6.QtWidgets import QFrame, QWidget
    from PySide6.QtCore import Qt

    host = QWidget()
    bare = QFrame(host)
    bare.setObjectName("RowCard")
    other = QFrame(host)
    other.setObjectName("NotACard")
    check("初始未带 WA_StyledBackground",
          not bare.testAttribute(Qt.WA_StyledBackground))
    n = th.polish_styled_bg(host)
    check("兜底补开了 1 个（只动卡片 ID）", n == 1, str(n))
    check("RowCard 已带属性", bare.testAttribute(Qt.WA_StyledBackground))
    check("非卡片不受影响", not other.testAttribute(Qt.WA_StyledBackground))
    check("重复调用幂等（第二次 0）", th.polish_styled_bg(host) == 0)

    # ---------- C. 真实主窗全绿 ----------
    print("== C. 主窗内全部卡片 ==")
    from app.ui.main_window import MainWindow
    win = MainWindow()
    win._update_timer.stop()
    from PySide6.QtWidgets import QTabWidget
    bad = []
    for page_idx in range(win.stack.count()):
        page = win.stack.widget(page_idx)
        for w in page.findChildren(QWidget):
            if w.objectName() in th._CARD_IDS and not w.testAttribute(
                    Qt.WA_StyledBackground):
                bad.append(f"{page.__class__.__name__}#{w.objectName()}")
    check("各页面卡片全部带 WA_StyledBackground", not bad, str(bad[:5]))

    # ---------- D. 五处历史漏点 ----------
    print("== D. 历史漏点逐个复查 ==")
    home = win.stack.widget(0)
    # 工作台目录卡：至少 6 张 #RowCard
    rows = [w for w in home.findChildren(QFrame) if w.objectName() == "RowCard"]
    check("工作台目录卡 ≥6 且都有圆角属性",
          len(rows) >= 6 and all(w.testAttribute(Qt.WA_StyledBackground) for w in rows),
          f"count={len(rows)}")

    # 实例设置行卡（comfyui 页第 1 个 tab）
    from app.ui.pages.comfyui_page import _Row
    r = _Row("标题", "说明", QWidget())
    check("实例设置 _Row 自带属性", r.testAttribute(Qt.WA_StyledBackground))

    # 环境检测卡：按类型定位环境页（nav 索引会随导航增减变化，别写死）
    from app.ui.pages.env_page import EnvPage
    env = next((win.stack.widget(i) for i in range(win.stack.count())
                if isinstance(win.stack.widget(i), EnvPage)), None)
    assert env is not None, "找不到环境管理页"
    win.stack.setCurrentWidget(env)
    app.processEvents()
    checks = [w for w in env.findChildren(QFrame) if w.objectName() == "CheckRow"]
    check("环境检测卡 ≥7 且都有圆角属性",
          len(checks) >= 7 and all(w.testAttribute(Qt.WA_StyledBackground)
                                   for w in checks), f"count={len(checks)}")

    # 模型市场卡片类
    from app.ui.pages.model_market_widget import ModelCard
    import inspect
    src = inspect.getsource(ModelCard.__init__)
    check("ModelCard 源码里设了 WA_StyledBackground",
          "WA_StyledBackground" in src)

    win.close()

    print(f"\n===== 卡片圆角 =====\n通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
