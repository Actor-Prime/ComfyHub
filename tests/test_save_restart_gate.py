# -*- coding: utf-8 -*-
"""保存「需重启提示」的运行态门控 + 异常防护回归（offscreen，FakeProcs 桩）

锁定 comfyui_page._save() 的提示门控与异常兜底（2026-12 用户报障 + 需求修正）：
  1. 实例运行中（is_running=True）+ 改 lan_access  → 弹「需重启」清单并列中文标签
  2. 实例没在运行（is_running=False）+ 改 lan_access → 只弹「✅ 设置已保存」原样
  3. is_running 抛异常 → 保存**仍然成功落盘**（写回 + save_config 都执行），
     弹窗仍是原样——提示类逻辑不许阻断保存主流程。依据：proc_manager.is_running
     走 _ext_alive → api_base 远程探活链，页面/实例状态不一致时可能抛异常；
     这行取在写回循环之前，一旦抛出会中断 _save()（用户点了确认却一个字段
     都没落盘）。判不出来一律按「没在运行」处理：宁可少提示，不可错提示。
  4. 运行中 + 只改 name（无启动类改动）→ 弹窗原样，不打扰

用法: .venv_build/Scripts/python.exe tests/test_save_restart_gate.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication(sys.argv[:1])

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.15):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


class FakeProcs:
    """is_running 按用例切三种态：true / false / 抛异常（boom 恒抛）"""

    def __init__(self):
        self.mode = "false"          # idle 默认按未运行

    def is_own(self, inst):
        return False

    def is_running(self, inst):
        if self.mode == "boom":
            raise RuntimeError("simulated ext-probe failure")
        return self.mode == "true"

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

    inst = {"id": "g1", "name": "门控实例", "kind": "local",
            "comfyui_path": "C:/fake/ComfyUI", "python_path": "C:/fake/python.exe",
            "port": 8188, "vram_mode": "自动", "extra_args": "",
            "lan_access": False}
    cfg = {"instances": [inst], "settings": {}}

    # 落盘桩：记录每次 save_config 收到的 cfg（真写盘在测试机上不该发生）
    saved = []
    real_acfg_save, real_cp_save = ACFG.save_config, CP.save_config
    ACFG.save_config = CP.save_config = lambda c: saved.append(c)

    real_info, real_warn = QMessageBox.information, QMessageBox.warning
    msgs = []
    QMessageBox.information = staticmethod(
        lambda *a, **k: (msgs.append(str(a[2]) if len(a) > 2 else ""),
                         QMessageBox.Ok)[1])
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

    real_confirm = changes_dialog.confirm_changes
    changes_dialog.confirm_changes = lambda *a, **k: True   # 保存确认框放行

    try:
        mainwin = FakeMain(cfg, inst)
        page = CP.ComfyuiPage(cfg, mainwin)
        page.show()
        page.load_current()
        pump(0.2)
        page._insecure_modal = lambda i, n: "ok"            # http 告警放行

        # ---- 1. 运行中 + 改 lan_access → 弹「需重启」清单 ----
        mainwin.procs.mode = "true"
        page.ck_lan.setChecked(True)
        page._save()
        pump(0.1)
        m1 = msgs[-1] if msgs else ""
        check("1 运行中改 lan_access → 弹需重启提示",
              m1.startswith("✅ 设置已保存") and "需重启实例才生效" in m1
              and "局域网访问" in m1 and "停止并重新启动" in m1, m1)

        # ---- 2. 未运行 + 改 lan_access → 只弹原样 ----
        mainwin.procs.mode = "false"
        page.ck_lan.setChecked(False)                       # 改回 False 构成改动
        msgs.clear()
        page._save()
        pump(0.1)
        check("2 未运行改 lan_access → 只弹原样",
              msgs and msgs[-1] == "✅ 设置已保存", str(msgs))

        # ---- 3. is_running 抛异常 → 保存仍落盘 + 弹窗原样 ----
        mainwin.procs.mode = "boom"
        page.ck_lan.setChecked(True)
        msgs.clear()
        saved.clear()
        page._save()
        pump(0.1)
        check("3a is_running 抛异常 → 弹窗仍是原样",
              msgs and msgs[-1] == "✅ 设置已保存", str(msgs))
        check("3b 抛异常 → 字段仍写回 inst",
              inst.get("lan_access") is True, str(inst.get("lan_access")))
        check("3c 抛异常 → save_config 仍执行（落盘）",
              bool(saved) and saved[-1]["instances"][0].get("lan_access") is True,
              f"save_config 调用 {len(saved)} 次")
        mainwin.procs.mode = "false"                        # 复位，避免影响后续

        # ---- 4. 运行中 + 只改 name → 弹窗原样（无启动类改动）----
        mainwin.procs.mode = "true"
        page.ed_name.setText("改名了")
        msgs.clear()
        page._save()
        pump(0.1)
        check("4 运行中只改 name → 弹窗原样",
              msgs and msgs[-1] == "✅ 设置已保存", str(msgs))

        page.close()
    finally:
        ACFG.save_config, CP.save_config = real_acfg_save, real_cp_save
        QMessageBox.information, QMessageBox.warning = real_info, real_warn
        changes_dialog.confirm_changes = real_confirm

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nsave_restart_gate：{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("FAILED:")
        for n in bad:
            print("  - " + n)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
