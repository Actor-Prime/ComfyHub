# -*- coding: utf-8 -*-
"""侧栏导航禁拖 + 共享外挂库迁到 ComfyUI 页（2026-11-08）

用户报障两件事：
1. 「为什么左边按钮可以拖动？」—— QListWidget 在 IconMode 下默认 DragDrop +
   movement=Free，导航项能被拖走；这里断言已经显式关掉。
2. 「共享外挂模型库放 ComfyUI 那个 UI 界面就行，设置这里不用这个」—— 从系统设置
   搬到 ComfyUI 页第三个 Tab；这里断言搬到位、旧位置已清干净、保存链路可用。

用法：.venv_build/Scripts/python.exe tests/test_nav_shared_ui.py
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QMessageBox, QFileDialog,
                               QListWidget, QAbstractItemView)

app = QApplication([])

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.2):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


def main():
    from app import config as ACFG
    from app import model_paths as MP

    root = tempfile.mkdtemp(prefix="ch_navshare_")
    share = os.path.join(root, "shared_models")
    os.makedirs(os.path.join(share, "checkpoints"), exist_ok=True)
    os.makedirs(os.path.join(share, "loras"), exist_ok=True)

    cfg = {"instances": [{"id": "ia", "name": "实例A", "comfyui_path": root,
                          "python_path": "python.exe", "port": 8188}],
           "settings": {}}
    cfgfile = os.path.join(root, "config.json")

    # 不落真实配置、不弹模态框、不弹目录选择
    real_save, real_load = ACFG.save_config, ACFG.load_config
    saved = {"n": 0}
    ACFG.save_config = lambda c: saved.__setitem__("n", saved["n"] + 1)
    real_info, real_warn = QMessageBox.information, QMessageBox.warning
    msgs = []
    QMessageBox.information = staticmethod(
        lambda *a, **k: (msgs.append(a[2] if len(a) > 2 else ""), QMessageBox.Ok)[1])
    QMessageBox.warning = staticmethod(
        lambda *a, **k: (msgs.append(a[2] if len(a) > 2 else ""), QMessageBox.Ok)[1])
    real_pick = QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    # 系统设置的 _save 会落盘到真实配置，测试里替换掉
    from app.ui.pages import settings_page as SP
    SP.save_config = ACFG.save_config
    from app.ui.pages import comfyui_page as CP
    CP.save_config = ACFG.save_config

    try:
        # ---------------- 1. 侧栏导航不可拖 ----------------
        from app.ui import main_window as mw
        mw.load_config = lambda: cfg
        win = mw.MainWindow()
        pump(0.3)
        nav = win.nav
        check("1a 导航拖放模式=NoDragDrop",
              nav.dragDropMode() == QAbstractItemView.NoDragDrop,
              str(nav.dragDropMode()))
        check("1b 导航 dragEnabled=False", nav.dragEnabled() is False)
        check("1c 导航 movement=Static",
              nav.movement() == QListWidget.Static, str(nav.movement()))
        bad = [nav.item(i).text() for i in range(nav.count())
               if nav.item(i).flags() & Qt.ItemIsDragEnabled]
        check("1d 所有导航项都不带 ItemIsDragEnabled", not bad, str(bad))
        check("1e 导航项仍可点选",
              all(nav.item(i).flags() & Qt.ItemIsSelectable
                  for i in range(nav.count())))

        # ---------------- 2. 共享外挂库搬到 ComfyUI 页 ----------------
        titles = [win.stack and win.stack.widget(i) for i in range(win.stack.count())]
        page = None
        for w in titles:
            if w.__class__.__name__ == "ComfyuiPage":
                page = w
                break
        check("2a 找到 ComfyUI 页", page is not None)
        tabs = page.tabs
        tab_names = [tabs.tabText(i) for i in range(tabs.count())]
        check("2b 第三个 Tab 是「📚 共享外挂库」",
              tabs.count() == 3 and tab_names[2] == "📚 共享外挂库", str(tab_names))
        check("2c 共享库控件就位",
              hasattr(page, "ck_share") and hasattr(page, "ed_share_root")
              and hasattr(page, "b_share") and hasattr(page, "lb_share_state"))

        # 系统设置页不该再有这套控件
        sp = None
        for w in titles:
            if w.__class__.__name__ == "SettingsPage":
                sp = w
                break
        check("2d 系统设置页已无共享库控件",
              sp is not None and not hasattr(sp, "ck_share")
              and not hasattr(sp, "ed_share_root"))

        # ---------------- 3. 联动与校验 ----------------
        check("3a 默认未启用 → 目录行置灰",
              not page.ck_share.isChecked()
              and not page.ed_share_root.isEnabled())
        page.ck_share.setChecked(True)
        pump(0.1)
        check("3b 勾选后目录行可编辑", page.ed_share_root.isEnabled())
        check("3c 勾了但没选目录 → 状态提示会拦下",
              "保存会被拦下" in page.lb_share_state.text(),
              page.lb_share_state.text())

        # 勾了但目录空 → 保存被拦
        page.ed_share_root.clear()
        before = dict(cfg["settings"])
        page._save_shared()
        pump(0.05)
        check("3d 空目录保存被拦下，配置未动",
              cfg["settings"] == before and "请先选择挂载根目录" in (msgs[-1] if msgs else ""),
              str(cfg["settings"]))

        # 选目录 → 自动启用 + 状态行报类别数
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: share)
        page.ck_share.setChecked(False)
        page._pick_share_root()
        pump(0.1)
        check("3e 选完目录自动勾选",
              page.ck_share.isChecked()
              and os.path.normpath(page.ed_share_root.text()) == os.path.normpath(share),
              page.ed_share_root.text())
        check("3f 状态行报出映射类别数",
              "2" in page.lb_share_state.text(), page.lb_share_state.text())

        # 保存 → 落 cfg + 生成托管 yaml
        page._save_shared()
        pump(0.1)
        check("4a 已写入系统设置",
              cfg["settings"].get("shared_models_enabled") is True
              and os.path.normpath(cfg["settings"].get("shared_models_root"))
              == os.path.normpath(share), str(cfg["settings"]))
        yml = MP.shared_config_path()
        check("4b 生成了托管挂载表", os.path.isfile(yml), yml)
        if os.path.isfile(yml):
            body = open(yml, encoding="utf-8").read()
            check("4c 挂载表含 checkpoints/loras 映射",
                  "checkpoints" in body and "loras" in body, body[:80])
        check("4d 保存走了一次 save_config", saved["n"] >= 1, str(saved["n"]))

        # 关掉 → yaml 删掉、字段置 False
        page.ck_share.setChecked(False)
        page._save_shared()
        pump(0.1)
        check("4e 关掉后配置为 False",
              cfg["settings"].get("shared_models_enabled") is False)
        check("4f 关掉后托管 yaml 被删除", not os.path.isfile(yml))

        # ---------------- 5. 系统设置页保存不再碰共享字段 ----------------
        cfg["settings"]["shared_models_enabled"] = False
        cfg["settings"].pop("shared_models_root", None)
        from app.ui import changes_dialog
        real_confirm = changes_dialog.confirm_changes
        changes_dialog.confirm_changes = lambda *a, **k: True
        try:
            sp.cb_mirror.setCurrentText(sp.cb_mirror.currentText())
            sp.ed_ghproxy.setText("http://127.0.0.1:9999/")
            sp._save()
            pump(0.05)
        finally:
            changes_dialog.confirm_changes = real_confirm
        check("5a 系统设置保存不动共享字段",
              "shared_models_root" not in cfg["settings"]
              and cfg["settings"].get("shared_models_enabled") is False,
              str(cfg["settings"]))

        win.cfg.setdefault("settings", {})["close_behavior"] = "close"
        win.close()
    finally:
        ACFG.save_config, ACFG.load_config = real_save, real_load
        SP.save_config = real_save
        CP.save_config = real_save
        QMessageBox.information, QMessageBox.warning = real_info, real_warn
        QFileDialog.getExistingDirectory = real_pick
        try:
            MP.sync_shared_config("", False)
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 ==")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
