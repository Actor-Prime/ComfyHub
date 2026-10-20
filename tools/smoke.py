# -*- coding: utf-8 -*-
"""ComfyHub 冒烟测试：一条命令验证全部模块可导入 + 主窗口/向导真实构造。

用法（必须用带 PySide6 的解释器，如 .venv_build）：
    .venv_build/Scripts/python.exe tools/smoke.py

全部通过输出 SMOKE PASS 并退出码 0；任何失败输出 SMOKE FAIL + 明细并退出码 1。
每次代码改动后先跑这个，启动类崩溃在打包前就会暴露。
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(name, fn):
    try:
        fn()
        print(f"  ✅ {name}")
    except Exception:
        FAILURES.append((name, traceback.format_exc()))
        print(f"  ❌ {name}")


def main():
    print("=== ComfyHub 冒烟测试 ===")

    # ---- 1. 纯逻辑模块导入 ----
    def _imports():
        import app.config          # noqa: F401
        import app.bootstrap       # noqa: F401
        import app.proc_manager    # noqa: F401
        import app.process         # noqa: F401
        import app.sysinfo         # noqa: F401
        import app.tools           # noqa: F401
        import app.winsub          # noqa: F401
        import app.workflow_scan   # noqa: F401
        import app.installer       # noqa: F401
        import app.instance        # noqa: F401
    check("core 模块导入", _imports)

    # ---- 2. GUI 模块导入（含 QPixmap/QTimer 等历史踩坑点）----
    def _gui_imports():
        from PySide6.QtWidgets import QApplication  # noqa: F401
        import app.ui.theme                # noqa: F401
        import app.ui.dialogs              # noqa: F401
        import app.ui.main_window          # noqa: F401
        import app.ui.setup_wizard         # noqa: F401
        # import app.workers  # 步骤② 创建后启用
        import app.ui.pages.home_page      # noqa: F401
        import app.ui.pages.comfyui_page   # noqa: F401
        import app.ui.pages.console_page   # noqa: F401
        import app.ui.pages.env_page       # noqa: F401
        import app.ui.pages.models_page    # noqa: F401
        import app.ui.pages.plugins_page   # noqa: F401
        import app.ui.pages.workflow_page  # noqa: F401
        import app.ui.pages.settings_page  # noqa: F401
        import app.ui.tools_page           # noqa: F401
        import app.ui.instance_page        # noqa: F401
    check("ui 模块导入", _gui_imports)

    # ---- 3. 真实构造（今天三次启动崩溃都发生在这里）----
    def _construct():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        win = MainWindow()
        win.show()
        assert win.cb_inst is not None
        assert win.home_page is not None
        win.close()
        return app
    app = None
    try:
        check("MainWindow 构造", _construct)
    except Exception:
        pass

    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        win = MainWindow()
        from app.ui.setup_wizard import SetupWizard
        wiz = SetupWizard(win)
        assert hasattr(wiz, "cb_found"), "cb_found 缺失"
        assert hasattr(wiz, "cb_python"), "cb_python 缺失"
        assert hasattr(wiz, "ed_name"), "ed_name 缺失"
        wiz.close()
        win.close()
    except Exception:
        FAILURES.append(("SetupWizard 构造", traceback.format_exc()))
        print("  ❌ SetupWizard 构造")

    # ---- 4. 关键属性抽查（历史崩溃点）----
    def _attrs():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow
        win = MainWindow()
        page = win.comfyui_page
        for attr in ("b_del", "ed_name", "ed_path", "ed_models"):
            assert hasattr(page, attr), f"comfyui_page 缺 {attr}"
        for attr in ("b_start", "b_stop", "b_web"):
            assert hasattr(win.home_page, attr), f"home_page 缺 {attr}"
        win.close()
    check("关键控件属性抽查", _attrs)

    print("===")
    if FAILURES:
        print(f"SMOKE FAIL：{len(FAILURES)} 项失败")
        for name, tb in FAILURES:
            print(f"\n--- {name} ---\n{tb}")
        sys.exit(1)
    print("SMOKE PASS")


if __name__ == "__main__":
    main()
