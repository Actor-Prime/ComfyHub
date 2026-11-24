# -*- coding: utf-8 -*-
"""统一测试收尾：让「退出码」可信（D-09，2026-11-24 QA）

## 问题

离屏 Qt 测试脚本在断言全部通过、汇总行已经打印之后，进程仍会以
`0xC0000005`（Windows 访问冲突；Git Bash 里显示为 `139`）或 `127` 退出。
CI 若用退出码判通过，就会「明明全绿却红」。

修复前实测（同一台机器）：

    test_settings_first_save   0/10 通过   （10 次全崩）
    test_close_behavior        6/10        （随机）
    test_plugin_deps           9/10        （随机）
    test_remote_ui             偶发

## 根因（实测定位，不是猜的）

`os._exit()` → CRT `_exit()` → `ExitProcess()`，而 **`ExitProcess` 会依次调用各已加载
DLL 的 `PROCESS_DETACH`**。Qt 的静态析构在这时去访问一个 Python 解释器已经开始回收的
QApplication → 访问冲突。

对照组（`SettingsPage` 构造场景，各 30 次，`rc=0` 的命中率）::

    什么都不做                      1/30     ← 基线本来就是坏的
    app.quit()                      0/30     ← 更糟：只是退出事件循环，对象还在
    sleep 0.1s / 1s / 5s            0/12     ← 不是「等一会儿就好」
    page.close()                    2/30
    删掉所有 top-level 控件          5/30
    shiboken6.delete(QApplication)  30/30    ← 对简单控件树有效

所以这是**对象生命周期问题**，不是竞态、也不是「退出太快」。

## 但「优雅销毁 QApplication」并不普遍安全

把 `shiboken6.delete(QApplication)` 用到 `test_close_behavior` 上，崩溃点前移到了
`delete()` **内部**（用 `QA_EXIT_DEBUG=1` 能看到最后一条日志停在 `delete QApplication`）：

- 单个 `MainWindow` / 单个 `SettingsPage`：删 app 10~30 次全干净；
- 同时存在**多个** `MainWindow`（该用例建了好几个）时：销毁过程中 Qt 会回调
  Python 侧已失效的槽/定时器 → 访问冲突。

这是 PySide6 自身在解释器收尾阶段的限制，测试脚本无法在进程内安全兜住
（一旦段错误，当前进程就直接没了，没法 catch）。

## 因此采用的方案：绕过 `DLL_PROCESS_DETACH`

`kernel32!TerminateProcess` 与 `ExitProcess` 的区别就在于**不跑 DllMain detach**——
进程直接结束，Qt 的静态析构根本没机会执行，从而没有访问冲突。
退出码是**精确传入**的，断言失败仍会得到非零码（已实测 `code=1→1`、`code=2→2`、`code=3→3`）。

实测（3 个 `MainWindow` 同时存在的场景）：`TerminateProcess` **20/20** 干净。

> 唯一被绕过的只有「进程退出期的崩溃」，**断言结果一概不受影响**：
> 调用前已经 flush 过 stdout/stderr，汇总行写在崩溃之前。
> 想排查收尾期问题可 `QA_EXIT_GRACEFUL=1` 走优雅销毁 + `QA_EXIT_DEBUG=1` 看分步日志。

## 用法

脚本收尾处把 `os._exit(code)` / `sys.exit(code)` 换成::

    from _qa_exit import clean_exit
    clean_exit(code)

退出分支很多的老脚本，可以在 import 之后调用一次::

    from _qa_exit import install
    install()          # 把 os._exit 包一层：先收尾再真退出

`clean_exit()` 之后不能再碰任何 Qt 对象，请只在脚本最后一步调用。
"""
import os
import sys
import time

__all__ = ["clean_exit", "install", "teardown"]

_installed = False
_DEBUG = bool(os.environ.get("QA_EXIT_DEBUG"))
_GRACEFUL = bool(os.environ.get("QA_EXIT_GRACEFUL"))


def _dbg(msg):
    """收尾分步日志（`QA_EXIT_DEBUG=1` 打开，平时静默）"""
    if _DEBUG:
        try:
            sys.stderr.write("[qa_exit] %s\n" % msg)
            sys.stderr.flush()
        except Exception:      # noqa: BLE001
            pass


def _flush():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:      # noqa: BLE001  收尾阶段不许抛
            pass


def _wait_python_threads(timeout=5.0):
    """等非守护的 Python 线程收尾（QThread 的原生线程不在 threading 里，泵事件代替）"""
    import threading

    end = time.time() + max(0.0, float(timeout))
    me = threading.current_thread()
    for t in threading.enumerate():
        if t is me or t.daemon:
            continue
        left = end - time.time()
        if left <= 0:
            break
        try:
            t.join(left)
        except Exception:      # noqa: BLE001
            pass


def _settle_qt(app, pump_s=0.15):
    """把排队事件跑掉，给还在收尾的工作线程一点时间"""
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    except Exception:          # noqa: BLE001
        pass
    end = time.time() + max(0.0, float(pump_s))
    while time.time() < end:
        try:
            app.processEvents()
        except Exception:      # noqa: BLE001
            break
        time.sleep(0.01)


def teardown(pump_s=0.15, join_s=5.0):
    """优雅销毁 QApplication。

    注意：控件树里有多个顶层窗口时，销毁过程中可能崩在 Qt 内部
    （见模块文档），所以默认**不**走这条路，只在 `QA_EXIT_GRACEFUL=1` 时使用。
    """
    _dbg("flush")
    _flush()
    _dbg("join python threads")
    _wait_python_threads(join_s)

    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:          # noqa: BLE001  没装 Qt / 没建 app
        _dbg("no Qt")
        return

    if app is None:
        _dbg("no QApplication")
        return

    _dbg("settle qt")
    _settle_qt(app, pump_s)

    _dbg("delete QApplication")
    try:
        import shiboken6
        shiboken6.delete(app)
    except Exception:          # noqa: BLE001
        pass
    _dbg("teardown done")


def _hard_exit(code):
    """Windows: TerminateProcess，不跑 DLL_PROCESS_DETACH（避开 Qt 静态析构崩溃）"""
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            handle = ctypes.c_void_p(k32.GetCurrentProcess())
            k32.TerminateProcess(handle, int(code))
        except Exception:      # noqa: BLE001  失败就退回 os._exit
            pass


def clean_exit(code=0, *, graceful=None, pump_s=0.15, join_s=5.0):
    """收尾后退出。`code` 原样作为进程退出码，不会被吞。"""
    if graceful is None:
        graceful = _GRACEFUL

    _flush()
    _wait_python_threads(join_s)

    if graceful:
        try:
            teardown(pump_s=pump_s, join_s=join_s)
        except Exception:      # noqa: BLE001
            pass

    _dbg("hard exit(%r)" % (code,))
    _flush()
    _hard_exit(code)
    os._exit(int(code))        # 兜底：非 Windows，或 TerminateProcess 调用失败


def install():
    """把 `os._exit` 换成「先收尾再真退出」的版本（幂等）

    适用于退出分支很多的老脚本。只包 `os._exit`，不动 `sys.exit`——
    后者可能被脚本里的 try/except 捕获，包起来语义会变。
    """
    global _installed
    if _installed:
        return
    _installed = True

    real_exit = os._exit

    def _patched(code=0):
        try:
            if _GRACEFUL:
                teardown()
        except Exception:      # noqa: BLE001
            pass
        _flush()
        _hard_exit(code)
        real_exit(int(code))

    os._exit = _patched
