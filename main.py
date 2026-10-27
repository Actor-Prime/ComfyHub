# -*- coding: utf-8 -*-
"""ComfyHub - ComfyUI 多实例启动管理器 入口

支持命令行模式（不启动界面）：
    ComfyHub.exe list / start / stop / status / info / logs
无参数时启动图形界面。
"""
import sys
import os
import time
import traceback

# 高 DPI 支持（PySide6 6.4+ 默认开启，这里兜底）
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

# ★ CLI 模式必须在导入 PySide6 之前判断，避免无谓加载 Qt
_CLI_CMDS = {"list", "start", "stop", "status", "info", "logs", "batch", "-h", "--help"}


def _maybe_cli() -> int | None:
    """若首个参数是 CLI 子命令 → 走命令行，返回退出码；否则返回 None 表示进 GUI"""
    if len(sys.argv) > 1 and sys.argv[1] in _CLI_CMDS:
        try:
            from app.cli import main as cli_main
            return cli_main()
        except Exception as e:
            print(f"CLI 执行失败: {e}", file=sys.stderr)
            return 1
    return None


_cli_code = _maybe_cli()
if _cli_code is not None:
    sys.exit(_cli_code)

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFont, QPalette, QColor

from app.ui.theme import DARK_QSS
from app.ui.main_window import MainWindow

CRASH_LOG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                         "ComfyHub", "crash.log")


def _write_crash(title: str, text: str):
    """黑匣子：任何异常落盘 %APPDATA%\\ComfyHub\\crash.log"""
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n"
                    f"{text}\n{'-' * 70}\n")
    except OSError:
        pass


def _excepthook(tp, val, tb):
    """Python 层全局异常钩子（windowed 打包下 stderr 不可见，落盘保命）"""
    _write_crash("PYTHON EXCEPTION",
                 "".join(traceback.format_exception(tp, val, tb)))
    sys.__excepthook__(tp, val, tb)


def _qt_message_handler(mode, ctx, message):
    """Qt 层警告/错误收集（含 C++ 侧 critical）"""
    if mode >= 1:   # Warning 及以上
        _write_crash(f"QT MSG(level={mode}) {ctx.file}:{ctx.line}", message)


def _enable_faulthandler():
    """捕获 C 层段错误（Qt/VCRUNTIME 崩溃）并写栈到 crash.log"""
    try:
        import faulthandler
        _p = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          "ComfyHub", "crash.log")
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _f = open(_p, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_f)
    except Exception:
        pass


def main():
    sys.excepthook = _excepthook
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass

    app = QApplication(sys.argv)
    from PySide6.QtGui import QIcon
    _assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    _icon = QIcon(os.path.join(_assets, "icon.ico"))
    if _icon.isNull():
        _icon = QIcon(os.path.join(_assets, "logo.png"))
    app.setWindowIcon(_icon)
    app.setApplicationName("ComfyHub")
    app.setOrganizationName("ComfyHub")
    # 中文界面统一字体
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    app.setStyleSheet(DARK_QSS)
    # 接管调色板：QSS 只覆盖了 ::item:selected，current 格等回落路径仍用系统
    # palette（暗色系统下 highlight=黑）→ 用户实测「单击单元格变黑看不清」
    #（2026-10-27）。统一为主题蓝底白字。
    _pal = app.palette()
    _pal.setColor(QPalette.Highlight, QColor("#2f4a75"))
    _pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    _pal.setColor(QPalette.Base, QColor("#181c22"))
    _pal.setColor(QPalette.AlternateBase, QColor("#1c212a"))
    _pal.setColor(QPalette.Text, QColor("#d8dee6"))
    _pal.setColor(QPalette.WindowText, QColor("#d8dee6"))
    app.setPalette(_pal)

    try:
        win = MainWindow()
        win.show()
        code = app.exec()
    except SystemExit:
        raise
    except Exception:
        _write_crash("FATAL IN MAINLOOP", traceback.format_exc())
        QMessageBox.critical(None, "ComfyHub 遇到错误",
                             "程序遇到未处理的错误已退出。\n"
                             f"详细日志：{CRASH_LOG}")
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
