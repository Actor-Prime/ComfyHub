# -*- coding: utf-8 -*-
"""延迟更新提醒回归（2026-11-12 用户需求）。

需求：启动满一小时若发现更新就提醒一次；可「不再提醒此版本」或「延后提醒」，
延后时长可选（1 小时 / 4 小时 / 1 天 / 3 天 / 1 周）。

断言：
  A. update_notify 纯逻辑：1 小时门槛、延后窗口、节流、时长记账与默认值
  B. UpdateReminderDialog：三个动作的返回值、下拉默认=上次选择
  C. 主窗接线：满 1 小时才检查、有更新才弹、延后/不再提醒写对设置、
     同一版本不重复弹、模态期间不重入
  D. 设置页开关：默认开、取消勾选立即落盘

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_update_reminder.py
"""
import os
import sys
import time

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


class FakeMain:
    """假主窗：只给 UpdateReminderDialog 需要的 cfg（不是 QWidget，
    弹窗若把它当 parent 会报错——所以调用处只在需要 cfg 时用它）"""

    def __init__(self, cfg):
        self.cfg = cfg


def main():
    from app import update_notify as un

    # ---------- A. 纯逻辑 ----------
    print("== A. 时间判断 ==")
    now = 10_000_000.0
    check("默认开启", un.enabled({}))
    check("关掉开关就不提醒", not un.should_remind(
        {"update_delayed_check": False}, now - 7200, now))
    check("未满 1 小时不提醒", not un.should_remind({}, now - 3599, now))
    check("满 1 小时开始提醒", un.should_remind({}, now - 3600, now))
    check("超过 1 小时也提醒", un.should_remind({}, now - 7200, now))

    st = {}
    un.snooze(st, 4, now)
    check("延后写 until（4 小时）", abs(un.snooze_until(st) - (now + 4 * 3600)) < 1)
    check("延后写时长记忆", st.get("update_snooze_hours") == 4.0)
    check("延后窗口内不提醒",
          not un.should_remind(st, now - 7200, now + 3600))
    check("延后到期后重新提醒",
          un.should_remind(st, now - 7200, now + 4 * 3600 + 1))
    check("到期时间取 max(启动+1h, 延后至)",
          un.due_at(st, now - 7200) == now + 4 * 3600, str(un.due_at(st, now - 7200)))

    st2 = {}
    un.snooze(st2, 0.01, now)          # 过小 → 夹到 0.25 小时
    check("异常时长的下限夹紧", abs(st2["update_snooze_hours"] - 0.25) < 1e-6)
    st3 = {}
    un.snooze(st3, "bad", now)
    check("非法时长回退默认 4 小时", st3["update_snooze_hours"] == 4.0)

    check("未检查过时允许立刻检查", un.can_recheck({}, now))
    check("30 分钟内不重复联网检查",
          not un.can_recheck({"update_last_check": now - 60}, now))
    check("超过 30 分钟允许再查",
          un.can_recheck({"update_last_check": now - 1900}, now))
    check("延后时长下拉共 5 档", len(un.DELAY_CHOICES) == 5, str(len(un.DELAY_CHOICES)))

    # ---------- B. 弹窗 ----------
    print("== B. 提醒弹窗 ==")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.update_reminder import UpdateReminderDialog

    info = {"current": "0.8.26", "latest": "0.8.27", "notes": "修了个 bug"}

    from PySide6.QtWidgets import QWidget

    class Host(QWidget):
        """QDialog 的 parent 必须是 QWidget；cfg 挂在这上面"""

        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg

    host1 = Host({"settings": {"update_snooze_hours": 24}})   # 必须留引用：
    # 临时 parent 会被 Python 回收，连带销毁 QDialog（父子所有权）
    dlg = UpdateReminderDialog(info, host1)
    labels = [w.text() for w in dlg.findChildren(
        __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    check("显示 旧→新 版本号", any("0.8.26" in t and "0.8.27" in t for t in labels),
          str(labels[:4]))
    check("下拉默认=上次选择（1 天）", dlg.cb_delay.currentData() == 24.0,
          str(dlg.cb_delay.currentData()))
    dlg.cb_delay.setCurrentIndex(dlg.cb_delay.findData(1.0))
    dlg._on_later()
    check("点延后 → action=later", dlg.action == "later", dlg.action)
    check("延后取当前下拉时长 1 小时", dlg.delay_hours == 1.0, str(dlg.delay_hours))

    host2 = Host({"settings": {}})
    dlg2 = UpdateReminderDialog(info, host2)
    check("无历史时下拉默认 4 小时", dlg2.cb_delay.currentData() == 4.0,
          str(dlg2.cb_delay.currentData()))
    dlg2._finish("never")
    check("点不再提醒 → action=never", dlg2.action == "never")
    dlg3 = UpdateReminderDialog(info, None)
    dlg3._finish("update")
    check("点立即更新 → action=update", dlg3.action == "update")

    # ---------- C. 主窗接线 ----------
    print("== C. 主窗接线 ==")
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    from PySide6.QtWidgets import QMainWindow
    from app.ui.main_window import MainWindow
    import app.ui.update_banner as ub

    win = MainWindow()
    win._update_timer.stop()            # 手动驱动，别让真实 timer 干扰
    calls = []
    win.check_updates = lambda manual=False, mode="": calls.append(mode)

    # C1 未满 1 小时：不查
    win._update_started = time.time()
    win._update_info = None
    win._update_tick()
    check("未满 1 小时不触发检查", calls == [], str(calls))

    # C2 满 1 小时 + 无延后 → 触发 delayed 检查
    win._update_started = time.time() - 3700
    win.cfg.setdefault("settings", {}).pop("update_snooze_until", None)
    win.cfg["settings"]["update_last_check"] = 0
    win.cfg["settings"]["update_delayed_check"] = True
    win._update_tick()
    check("满 1 小时触发 delayed 检查", calls == ["delayed"], str(calls))
    calls.clear()

    # C3 关掉开关 → 不查
    win.cfg["settings"]["update_delayed_check"] = False
    win._update_tick()
    check("开关关闭后不检查", calls == [], str(calls))
    win.cfg["settings"]["update_delayed_check"] = True

    # C4 已有新版本 → 直接弹窗（不再联网）
    win._update_info = {"has_update": True, "latest": "9.9.9", "current": "0.8.26"}
    win.cfg["settings"]["update_last_check"] = time.time()
    shown = []

    class FakeDlg:
        action = "later"
        delay_hours = 4.0

        def __init__(self, info, parent=None):
            shown.append(info.get("latest"))

        def exec(self):
            return 1

    import app.ui.update_reminder as ur
    _orig_dlg = ur.UpdateReminderDialog
    ur.UpdateReminderDialog = FakeDlg
    saved = []
    import app.ui.main_window as mw
    _orig_save = mw.save_config
    mw.save_config = lambda cfg: saved.append(dict(cfg["settings"]))
    try:
        win._update_tick()
        check("有新版且未提醒 → 弹窗一次", shown == ["9.9.9"], str(shown))
        check("延后写入 snooze_until",
              float(win.cfg["settings"].get("update_snooze_until") or 0) > time.time())
        check("延后后已保存配置", bool(saved))
        # 清掉延后，才能验证「记账拦重复」本身（否则延后窗口把 tick 挡住了，
        # 删掉 _update_reminded 记账这条断言也照样能过 → 抓漏失效）
        win.cfg["settings"].pop("update_snooze_until", None)
        check("同一版本不再重复弹（靠提醒记账，不靠延后窗口）",
              win._update_tick() is None and len(shown) == 1, str(shown))

        # C5 「不再提醒此版本」→ 写 skipped
        shown.clear()
        win._update_reminded.clear()
        win.cfg["settings"].pop("update_snooze_until", None)

        class NeverDlg(FakeDlg):
            action = "never"

        ur.UpdateReminderDialog = NeverDlg
        win._update_tick()
        check("不再提醒写入 update_skipped_version",
              win.cfg["settings"].get("update_skipped_version") == "9.9.9",
              str(win.cfg["settings"].get("update_skipped_version")))

        # C6 「立即更新」→ 走更新对话框入口
        shown.clear()
        win._update_reminded.clear()
        win.cfg["settings"].pop("update_snooze_until", None)
        win.cfg["settings"].pop("update_skipped_version", None)
        hit = []
        win._on_update_clicked = lambda: hit.append(1)

        class GoDlg(FakeDlg):
            action = "update"

        ur.UpdateReminderDialog = GoDlg
        win._update_tick()
        check("立即更新走 _on_update_clicked", hit == [1], str(hit))

        # C7 模态期间不重入
        win._reminder_open = True
        shown.clear()
        win._update_reminded.clear()
        win.cfg["settings"].pop("update_snooze_until", None)
        win._update_tick()
        check("模态期间 tick 不重入", shown == [], str(shown))
        win._reminder_open = False
    finally:
        ur.UpdateReminderDialog = _orig_dlg
        mw.save_config = _orig_save

    # C8 check_updates 的 mode 开关
    win._check_worker = None
    real_calls = []
    win.check_updates = MainWindow.check_updates.__get__(win, MainWindow)

    class FakeWorker:
        def __init__(self, cfg, force=False, parent=None):
            real_calls.append(force)

        def isRunning(self):
            return False

        def start(self):
            pass

        sig_done = type("S", (), {"connect": lambda *a, **k: None})()

    ub.CheckWorker = FakeWorker
    win.cfg["settings"]["update_auto_check"] = False
    win.check_updates(manual=False)                  # auto → 被开关拦住
    check("auto 模式受「启动时自动检查」约束", real_calls == [], str(real_calls))
    win.check_updates(mode="delayed")                # delayed 不受该开关约束
    check("delayed 模式不受 auto 开关影响", real_calls == [False], str(real_calls))
    win.cfg["settings"]["update_delayed_check"] = False
    win.check_updates(mode="delayed")
    check("delayed 开关关闭后不检查", real_calls == [False], str(real_calls))

    win.close()

    # ---------- D. 设置页开关 ----------
    print("== D. 设置页 ==")
    from app.ui.pages.settings_page import SettingsPage
    import app.ui.pages.settings_page as sp
    sp.save_config = lambda cfg: None
    cfg = {"settings": {}}
    pg = SettingsPage(cfg, None)
    check("设置页有「启动一小时后提醒」开关", hasattr(pg, "ck_upd_delayed"))
    check("默认勾选（用户要的是默认开启）", pg.ck_upd_delayed.isChecked())
    pg.ck_upd_delayed.setChecked(False)
    check("取消勾选立即落盘 update_delayed_check=False",
          cfg["settings"].get("update_delayed_check") is False,
          str(cfg["settings"].get("update_delayed_check")))
    cfg2 = {"settings": {"update_delayed_check": False}}
    pg2 = SettingsPage(cfg2, None)
    check("存档 False 时开关显示为未勾选", not pg2.ck_upd_delayed.isChecked())

    print(f"\n===== 延迟更新提醒 =====\n通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
