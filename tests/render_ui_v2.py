# -*- coding: utf-8 -*-
"""UI 渲染快照（当前代码 → 逐页逐 Tab 出图，不写任何配置）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/render_ui_v2.py [宽] [高]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "analysis", "ui_v2")
os.makedirs(OUT, exist_ok=True)


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


def main():
    from PySide6.QtWidgets import QApplication, QTabWidget, QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    app = QApplication(sys.argv)

    from app.ui.main_window import MainWindow

    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1600
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 1000

    win = MainWindow()
    win.resize(w, h)
    win.show()
    pump(app, 2.5)

    names = ["home", "comfyui", "plugins", "env", "models",
             "workflow", "ai", "console", "settings"]
    # 导航索引可能和 names 顺序不同 —— 用 nav 定义反查
    try:
        from app.ui.nav import NAV
        keys = [n[0] if isinstance(n, (tuple, list)) else n for n in NAV]
    except Exception:
        keys = names

    made = []
    for idx in range(win.stack.count()):
        win.goto(idx)
        pump(app, 1.2)
        page = win.stack.widget(idx)
        if page is None:
            continue
        key = keys[idx] if idx < len(keys) else str(idx)
        p = os.path.join(OUT, f"{idx:02d}_{key}.png")
        win.grab().save(p)
        made.append(p)
        for tab in page.findChildren(QTabWidget):
            for ti in range(tab.count()):
                tab.setCurrentIndex(ti)
                pump(app, 0.45)
                tn = tab.tabText(ti).replace("/", "_").replace(" ", "")[:14] or str(ti)
                p2 = os.path.join(OUT, f"{idx:02d}_{key}_t{ti}_{tn}.png")
                win.grab().save(p2)
                made.append(p2)

    print(f"OK {len(made)} 张 → {OUT}")
    for p in made:
        print("  ", os.path.basename(p))


if __name__ == "__main__":
    main()
