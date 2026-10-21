# -*- coding: utf-8 -*-
"""端到端安装流程自动化：程序化驱动向导走完 4 步，全程记录状态。

用法（用带 PySide6 的解释器）：
    .venv_build/Scripts/python.exe tools/e2e_install.py [目标目录]

用途：复现"安装流程中的崩溃/卡死"，在源码环境能看到完整 Python 栈
（打包版的 C++ 层崩溃在这里会退化成可读异常）。
每 2 秒打印一次状态快照：步骤 / 进度 / 新日志行。
"""
import os
import sys
import time
import traceback

# 段错误/崩溃栈捕获（C 层崩溃也能看到 Python 调用链）
import faulthandler

_fh_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "_e2e_crash.log")
_fh = open(_fh_path, "a", encoding="utf-8", buffering=1)
faulthandler.enable(file=_fh, all_threads=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET = sys.argv[1] if len(sys.argv) > 1 else r"F:\_e2e_test\ComfyUI"
MAX_MINUTES = 25


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    from app.ui.main_window import MainWindow
    from app.ui.setup_wizard import SetupWizard

    win = MainWindow()
    win.show()
    wiz = SetupWizard(win)
    wiz.show()

    # 填目标目录（不等待自动探测结果，直接设）
    os.makedirs(TARGET, exist_ok=True)
    wiz.ed_dir.setText(TARGET)
    wiz.ed_name.setText("E2E测试")

    state = {"last_log": 0, "step": 0, "t0": time.time(), "done": False}

    def dump():
        try:
            if state["done"]:
                return
            p2 = wiz.p2
            try:
                text = p2.log.toPlainText()
            except Exception:
                text = ""
            lines = text.splitlines()
            new = lines[state["last_log"]:]
            if new:
                for ln in new[-8:]:
                    print("LOG>", ln[:150], flush=True)
                state["last_log"] = len(lines)
            print(f"[{int(time.time() - state['t0'])}s] 步骤 {p2.cur + 1}/{p2.state.get('total', 4)} "
                  f"| 进度 {p2.bar.value()} | 状态: {p2.lb_state.text()[:60]}", flush=True)
            if os.path.isdir(os.path.join(TARGET, "main.py")):
                pass
            torch_dir = os.path.join(os.environ.get("APPDATA", ""), "ComfyHub")
            if int(time.time() - state["t0"]) > MAX_MINUTES * 60:
                print("⏱ E2E 超时，终止", flush=True)
                state["done"] = True
                app.quit()
        except Exception:
            traceback.print_exc()

    # 2 秒后点「下一步」进入安装页
    def go():
        print(f"→ 设置目标: {TARGET}", flush=True)
        wiz.next()

    QTimer.singleShot(2000, go)
    poll = QTimer()
    poll.timeout.connect(dump)
    poll.start(2000)

    # 全程异常钩子（C++ 层崩不出来，但 Python 层异常能看到）
    def excepthook(t, v, tb):
        print("=== PYTHON EXCEPTION ===", flush=True)
        traceback.print_exception(t, v, tb)
        state["done"] = True
    sys.excepthook = excepthook

    print("=== E2E 启动（源码环境，可看到完整栈）===", flush=True)
    rc = app.exec()
    print(f"=== E2E 结束 rc={rc} ===", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
