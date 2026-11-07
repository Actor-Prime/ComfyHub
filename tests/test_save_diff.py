# -*- coding: utf-8 -*-
"""保存前改动确认：空字段 = 默认值，不该算改动（2026-11-08 用户报障）

现象：老配置里没有 cross_attn / preview_method / precision 等字段时，界面会显示
默认值，但点保存却弹出「本次将保存 14 处修改：（空）→ 自动」。
本测试锁定：未动过 → 无改动；只改一项 → 只报那一项，旧值显示的是界面上的默认值。

用法：.venv_build/Scripts/python.exe tests/test_save_diff.py
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox

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


class FakeProcs:
    def is_running(self, inst):
        return False

    def get(self, inst):
        return None


class FakeMain:
    def __init__(self, cfg, inst):
        self.cfg = cfg
        self.inst = inst
        self.procs = FakeProcs()

    def current_inst(self):
        return self.inst

    def _sync_instances(self):
        pass


def main():
    from app import config as ACFG
    from app.ui import changes_dialog
    from app.ui.pages import comfyui_page as CP

    root = tempfile.mkdtemp(prefix="ch_savediff_")
    os.makedirs(os.path.join(root, "models", "checkpoints"), exist_ok=True)
    # 故意只给最少字段：模拟 0.8.06 之前存下来的老配置
    inst = {"id": "sd", "name": "老实例", "comfyui_path": root,
            "python_path": "C:\\py\\python.exe", "port": 8188}
    cfg = {"instances": [inst], "settings": {}}

    real_save = ACFG.save_config
    ACFG.save_config = lambda c: None
    CP.save_config = ACFG.save_config
    real_info, real_warn = QMessageBox.information, QMessageBox.warning
    msgs = []
    QMessageBox.information = staticmethod(
        lambda *a, **k: (msgs.append(str(a[2]) if len(a) > 2 else ""), QMessageBox.Ok)[1])
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

    captured = {"n": 0, "changes": None}
    real_confirm = changes_dialog.confirm_changes

    def _fake_confirm(parent, title, changes, save_label="保存"):
        captured["n"] += 1
        captured["changes"] = changes
        return False                     # 一律取消：本测试只看对比结果，不落盘

    changes_dialog.confirm_changes = _fake_confirm
    try:
        page = CP.ComfyuiPage(cfg, FakeMain(cfg, inst))
        page.show()
        pump(0.3)

        # 1. 什么都没动 → 直接「没有改动」，连确认框都不弹
        page._save()
        pump(0.1)
        check("1a 未改动不弹确认框", captured["n"] == 0, str(captured["n"]))
        check("1b 提示没有改动", any("没有改动" in m for m in msgs), str(msgs))

        # 2. 只改一项 → 只报这一项，旧值是界面上的默认值（不是「（空）」）
        page.cb_prec.setCurrentText("强制 FP16")
        pump(0.1)
        page._save()
        pump(0.1)
        ch = captured["changes"] or []
        check("2a 只报一处改动", len(ch) == 1, str(ch))
        check("2b 旧值显示默认值「自动」", ch and ch[0][1] == "自动", str(ch))
        check("2c 新值正确", ch and ch[0][2] == "强制 FP16", str(ch))

        # 3. 取消后基准不变，再点保存还是同一处改动
        captured["n"] = 0
        page._save()
        pump(0.1)
        check("3 取消后仍只报那一处", (captured["changes"] or [])[:1]
              and len(captured["changes"]) == 1, str(captured["changes"]))

        # 4. 确认保存后基准更新 → 再点保存「没有改动」
        changes_dialog.confirm_changes = lambda *a, **k: True
        msgs.clear()
        page._save()
        pump(0.1)
        check("4a 保存后旧值写进配置", inst.get("precision") == "强制 FP16",
              str(inst.get("precision")))
        msgs.clear()
        page._save()
        pump(0.1)
        check("4b 保存后基准已更新（无改动）",
              any("没有改动" in m for m in msgs), str(msgs))

        # 5. 模型目录清空 → 对比框里写「默认（<根目录>/models）」而不是「（空）」
        #    （注意别用 <根目录>/models 本身：那条会被 load_current 当成默认值净化成空）
        other = os.path.join(root, "models_custom")
        os.makedirs(os.path.join(other, "checkpoints"), exist_ok=True)
        inst["models_dir"] = other
        page.load_current()
        pump(0.2)
        changes_dialog.confirm_changes = _fake_confirm
        captured["changes"] = None
        page.ed_models.clear()
        page._save()
        pump(0.1)
        _row = [c for c in (captured["changes"] or []) if c[0] == "模型目录"]
        check("5 清空模型目录显示「默认」而非「（空）」",
              _row and _row[0][2].startswith("默认"), str(captured["changes"]))

        page.close()
    finally:
        ACFG.save_config = real_save
        CP.save_config = real_save
        QMessageBox.information, QMessageBox.warning = real_info, real_warn
        changes_dialog.confirm_changes = real_confirm
        shutil.rmtree(root, ignore_errors=True)

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 ==")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
