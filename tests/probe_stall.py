# -*- coding: utf-8 -*-
"""事件循环卡顿探针（诊断工具，不参与打包）

实测「启动 ComfyUI 期间管理器 UI 卡顿」的元凶：
  · 心跳 QTimer(50ms) 记录事件循环空档 >150ms 的卡顿事件
  · 计时包裹四个嫌疑点：task_watch._get_json（阻塞 HTTP）/ process.port_in_use
    / winsub.run（netstat 等子进程）/ console._pull（日志刷屏）/ ComfyProcess._on_output
  · 驱动真实 MainWindow（真实实例配置）启动 ComfyUI，观察 150 秒后停止并出报告

用法： .venv_build/Scripts/python.exe tests/probe_stall.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"F:\ComfyHub")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

STALLS = []                 # (时刻, gap_ms)
TIMERS = {}                 # label -> [次数, 总ms, 最大ms]
LOG_LINES = [0]
LAST = [time.perf_counter()]


def beat():
    now = time.perf_counter()
    gap = (now - LAST[0]) * 1000
    LAST[0] = now
    if gap > 150:
        STALLS.append((time.strftime("%H:%M:%S"), round(gap, 1)))
        print(f"[STALL] {time.strftime('%H:%M:%S')} gap={gap:.0f}ms", flush=True)


def _acc(label, dt_ms):
    s = TIMERS.setdefault(label, [0, 0.0, 0.0])
    s[0] += 1
    s[1] += dt_ms
    s[2] = max(s[2], dt_ms)


def wrap(mod, name, label):
    fn = getattr(mod, name)

    def w(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            _acc(label, (time.perf_counter() - t0) * 1000)

    setattr(mod, name, w)


# ---- 嫌疑点计时包裹 ----
import app.task_watch as tw
wrap(tw, "_get_json", "task_watch._get_json(阻塞HTTP)")

import app.process as pr
wrap(pr, "port_in_use", "process.port_in_use(socket)")
wrap(pr, "run", "process.run(win子进程)")        # netstat/taskkill 走这个

import app.winsub as ws
wrap(ws, "run", "winsub.run")

import app.ui.pages.console_page as cp
_orig_pull = cp.ConsolePage._pull


def _pull_w(self):
    t0 = time.perf_counter()
    try:
        return _orig_pull(self)
    finally:
        _acc("console._pull(刷日志)", (time.perf_counter() - t0) * 1000)


cp.ConsolePage._pull = _pull_w

_orig_out = pr.ComfyProcess._on_output


def _out_w(self):
    t0 = time.perf_counter()
    try:
        return _orig_out(self)
    finally:
        _acc("ComfyProcess._on_output", (time.perf_counter() - t0) * 1000)


pr.ComfyProcess._on_output = _out_w

import app.proc_manager as pm
_orig_log = pm.ProcManager.append_log


def _log_w(self, iid, line):
    LOG_LINES[0] += 1
    return _orig_log(self, iid, line)


pm.ProcManager.append_log = _log_w


def report(win, app_):
    print("\n========== 卡顿诊断报告 ==========", flush=True)
    print(f"观察期日志行数: {LOG_LINES[0]}", flush=True)
    print(f"卡顿事件(>150ms): {len(STALLS)} 次", flush=True)
    top = sorted(STALLS, key=lambda x: -x[1])[:15]
    for t, g in top:
        print(f"  {t}  {g:.0f}ms", flush=True)
    print("嫌疑点累计耗时:", flush=True)
    for label, (n, tot, mx) in sorted(TIMERS.items(), key=lambda kv: -kv[1][1]):
        print(f"  {label}: 次数={n} 总计={tot:.0f}ms 最大单次={mx:.1f}ms", flush=True)
    print("==================================\n", flush=True)
    try:
        win.procs.stop_all()
    except Exception:
        pass
    QTimer.singleShot(3000, app_.quit)


def main():
    app = QApplication(sys.argv)
    from app.ui.main_window import MainWindow
    win = MainWindow()
    win.show()

    hb = QTimer()
    hb.setInterval(50)
    hb.timeout.connect(beat)
    hb.start()

    QTimer.singleShot(2000, lambda: (print("[PROBE] 调用 comfyui_page.start()", flush=True),
                                     win.comfyui_page.start()))
    QTimer.singleShot(150000, lambda: report(win, app))
    print("[PROBE] 观察 150 秒（含真实 ComfyUI 启动）…", flush=True)
    app.exec()


if __name__ == "__main__":
    main()
