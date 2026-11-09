# -*- coding: utf-8 -*-
"""修复按钮接线冒烟测试（2026-11-09 事故回归）。

背景：`_start_fix` 曾漏 `w.start()`——FixWorker 装好信号却永不执行，
对话框永停「1/5 准备 / 0% / 空日志」。直驱控件/解析器的自测走不到
「真点按钮」的接线链路，回归全绿也照样漏掉它。

本测试走完整 click→slot→QThread 链路：
  · monkeypatch FixWorker.run   —— 防止真去卸装 torch（破坏现场）
  · monkeypatch EnvPage._run_detect —— 收尾自动重检测，不跑真探测
  · 断言：线程真的启动（isRunning 被观测到）、对话框日志收到首行、
    finish 成功态被调用、按钮复位、收尾重检测被触发
漏了 w.start() 时前 4 项必红。

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_fix_start.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.03)


def pump_until(app, cond, timeout=8):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        time.sleep(0.05)
        try:
            if cond():
                return True
        except Exception:
            pass
    return False


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    # 无头跑绝不能让模态弹窗阻塞（同 walk_all_pages 的约定）
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

    app = QApplication(sys.argv)
    from app.ui.main_window import MainWindow
    from app.ui.pages.env_page import EnvPage, FixWorker

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    pump(app, 2.5)   # 等首轮异步落定（同 walk_all_pages）

    page = win.stack.widget(3)      # 环境管理页（walk_all_pages 页序）
    assert page is not None and hasattr(page, "fix_btns"), "env 页装配异常"

    # ---- 桩：假 FixWorker.run（防真装 torch）+ 假重检测 ----
    state = {"started": False, "detect": 0}

    def fake_run(self):
        state["started"] = True
        time.sleep(0.8)             # 留窗口让主线程观测到 isRunning
        self.sig_log.emit("smoke-log：接线冒烟测试首行")
        self.sig_done.emit(self.idx, True, "smoke 完成")

    def fake_detect(self):
        state["detect"] += 1

    orig_run, orig_detect = FixWorker.run, EnvPage._run_detect
    FixWorker.run = fake_run
    EnvPage._run_detect = fake_detect
    checks = []
    try:
        btn = page.fix_btns[3]      # PyTorch CUDA 行的「修复」
        checks.append(("修复按钮存在", "修复" in btn.text()))
        btn.setEnabled(True)
        btn.click()                 # ★ 真点按钮：click→signal→_start_fix→worker

        ok_live = pump_until(app, lambda: (
            getattr(page, "_fix_worker", None) is not None
            and page._fix_worker.isRunning()), timeout=5)
        checks.append(("worker.isRunning() 观测到运行态", ok_live))
        checks.append(("worker.run 被执行（线程真的启动了）", state["started"]))
        ok_done = pump_until(app, lambda: (
            "完成" in page._fix_dlg.lb_head.text()), timeout=8)
        pump(app, 0.3)
        checks.append(("finish 成功态被调用", ok_done))
        checks.append(("对话框日志收到首行",
                       "smoke-log" in page._fix_dlg.log.toPlainText()))
        checks.append(("修复按钮恢复可点", btn.isEnabled()))
        checks.append(("收尾自动重检测被触发", state["detect"] >= 1))
    finally:
        FixWorker.run = orig_run
        EnvPage._run_detect = orig_detect

    print("\n===== _start_fix 接线冒烟 =====")
    bad = 0
    for name, res in checks:
        print(f"  [{'PASS' if res else 'FAIL'}] {name}")
        bad += 0 if res else 1
    print(f"结果：{len(checks) - bad}/{len(checks)} 通过")

    win.cfg.setdefault("settings", {})["close_behavior"] = "close"   # 防关闭询问框挂死
    win.close()
    app.processEvents()
    try:
        if getattr(win, "_theme_timer", None):
            win._theme_timer.stop()
        if getattr(win, "tray", None):
            win.tray.hide()
            win.tray.setVisible(False)
    except Exception:
        pass
    app.processEvents()
    sys.stdout.flush()
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
