# -*- coding: utf-8 -*-
"""异步渲染复验：内核版本列表（缓存兜底）+ 插件市场清单（网络轮换）等待渲染后截图

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/walk_async_recheck.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "analysis", "walkthrough")


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.03)


def pump_until(app, cond, timeout):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        time.sleep(0.1)
        try:
            if cond():
                return True
        except Exception:
            pass
    return False


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from app.ui.main_window import MainWindow
    from app.ui.nav import NAV

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    pump(app, 2.0)

    # ---- 1) 内核版本：切到「内核版本」子 tab 触发刷新，等版本表填充 ----
    win.goto(NAV.COMFYUI)
    pump(app, 1.0)
    page = win.stack.widget(NAV.COMFYUI)
    from PySide6.QtWidgets import QTabWidget as _TT
    for _tab in page.findChildren(_TT):
        if _tab.count() >= 2:
            _tab.setCurrentIndex(1)   # 0=实例设置, 1=内核版本（_on_tab_changed 触发刷新）
            break
    pump(app, 1.0)
    tb = getattr(page, "tb_kver", None)
    ok = pump_until(app, lambda: tb is not None and tb.rowCount() > 0, 150)
    print(f"内核版本表行数: {tb.rowCount() if tb else 'N/A'}  渲染{'成功' if ok else '超时/失败'}")
    win.grab().save(os.path.join(OUT, "recheck_kernel_versions.png"))

    # ---- 2) 插件市场：等清单表格填充（多源轮换，允许 90s） ----
    win.goto(NAV.PLUGINS)
    pump(app, 1.0)
    page = win.stack.widget(NAV.PLUGINS)
    # 插件页可能有 tab（已安装/插件市场），切到市场 tab
    from PySide6.QtWidgets import QTabWidget
    for tab in page.findChildren(QTabWidget):
        if tab.count() >= 2:
            tab.setCurrentIndex(1)
            break
    pump(app, 1.0)
    rows = None
    for tb2 in page.findChildren(type(tb)) if tb else []:
        pass
    from PySide6.QtWidgets import QTableWidget
    tables = page.findChildren(QTableWidget)
    def biggest():
        return max((t.rowCount() for t in tables), default=0)
    ok2 = pump_until(app, lambda: biggest() > 0, 120)
    print(f"插件市场最大表行数: {biggest()}  渲染{'成功' if ok2 else '超时/失败'}")
    win.grab().save(os.path.join(OUT, "recheck_plugin_market.png"))

    win.cfg.setdefault("settings", {})["close_behavior"] = "close"   # 测试直退，防关闭询问框挂死
    win.close()
    app.processEvents()
    sys.exit(0 if (ok and ok2) else 1)


if __name__ == "__main__":
    main()
