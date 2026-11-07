# -*- coding: utf-8 -*-
"""实例设置页「挂载模型目录」回归（2026-11-08）

用户要求：模型目录那一栏卡片拉高，下面加「启用挂载模型目录」开关 + 目录选择。
覆盖：控件存在与高度 / 开关联动置灰 / 选目录自动勾选并落库 / 保存校验 /
切实例各自独立 / 启动参数带挂载 yaml。

用法：.venv_build/Scripts/python.exe tests/test_instance_mount_ui.py
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog

app = QApplication([])

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.25):
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
    def __init__(self, cfg, insts):
        self.cfg = cfg
        self.insts = insts
        self.cur = insts[0]
        self.procs = FakeProcs()

    def current_inst(self):
        return self.cur

    def _sync_instances(self):
        pass


def main():
    from app import config as ACFG
    from app.ui.pages import comfyui_page as CP

    root = tempfile.mkdtemp(prefix="ch_mountui_")
    mount = os.path.join(root, "ext_models")
    os.makedirs(os.path.join(root, "models", "checkpoints"), exist_ok=True)
    os.makedirs(os.path.join(mount, "loras"), exist_ok=True)
    open(os.path.join(mount, "loras", "x.safetensors"), "wb").write(b"0")

    inst_a = {"id": "ia", "name": "实例A", "comfyui_path": root,
              "python_path": "python.exe", "port": 8188}
    inst_b = {"id": "ib", "name": "实例B", "comfyui_path": root,
              "python_path": "python.exe", "port": 8189,
              "mount_enabled": True, "mount_dir": mount}
    cfg = {"instances": [inst_a, inst_b], "settings": {}}
    fake = FakeMain(cfg, [inst_a, inst_b])

    # 不打真实配置文件、不弹模态框
    saved = {"n": 0}
    real_save = ACFG.save_config
    ACFG.save_config = lambda c: saved.__setitem__("n", saved["n"] + 1)
    CP.save_config = ACFG.save_config
    real_info, real_warn = QMessageBox.information, QMessageBox.warning
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    real_pick = QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    try:
        page = CP.ComfyuiPage(cfg, fake)
        page.show()
        pump(0.3)

        # 1. 控件就位 + 卡片被拉高（原来一行 ~50px，加挂载行后 ≥74）
        check("1a 挂载开关存在", page.ck_mount.text() == "启用挂载模型目录",
              page.ck_mount.text())
        check("1b 挂载目录框 + 选择按钮存在",
              hasattr(page, "ed_mount") and page.b_mount.text() == "选择目录")
        h = page.ed_models.parentWidget().height()
        check("1c 模型目录卡片高度 ≥ 74px", h >= 74, f"h={h}")

        # 2. 实例 A 没启用 → 输入框与按钮置灰
        check("2a 未启用时目录框置灰", not page.ed_mount.isEnabled())
        check("2b 未启用时选择按钮置灰", not page.b_mount.isEnabled())

        # 3. 勾选开关 → 解禁
        page.ck_mount.setChecked(True)
        pump(0.1)
        check("3 勾选后目录框可编辑", page.ed_mount.isEnabled()
              and page.b_mount.isEnabled())

        # 4. 选目录 → 自动回填 + 落库 + 开关保持勾选
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: mount)
        page._pick_mount()
        pump(0.1)
        check("4a 目录回填", os.path.normpath(page.ed_mount.text()) == os.path.normpath(mount),
              page.ed_mount.text())
        check("4b 自动勾选开关", page.ck_mount.isChecked())
        check("4c 已写入实例配置", inst_a.get("mount_dir") == os.path.normpath(mount)
              and inst_a.get("mount_enabled") is True, str(inst_a))

        # 5. 保存 → 字段落到实例；实例 B 的值不被串
        from app.ui import changes_dialog
        real_confirm = changes_dialog.confirm_changes
        changes_dialog.confirm_changes = lambda *a, **k: True
        try:
            page._save()
            pump(0.1)
        finally:
            changes_dialog.confirm_changes = real_confirm
        check("5a 保存后实例A仍有挂载",
              inst_a.get("mount_enabled") is True
              and os.path.normpath(inst_a.get("mount_dir")) == os.path.normpath(mount))
        check("5b 实例B不受影响", inst_b.get("mount_dir") == mount)

        # 6. 切到实例 B → 控件反映 B 的配置（各自独立）
        fake.cur = inst_b
        page.load_current()
        pump(0.2)
        check("6 切实例后控件跟着走",
              page.ck_mount.isChecked()
              and os.path.normpath(page.ed_mount.text()) == os.path.normpath(mount),
              f"{page.ck_mount.isChecked()} / {page.ed_mount.text()}")

        # 7. 勾了但目录空 → 保存被拦下（不写空挂载）
        fake.cur = inst_a
        page.load_current()
        pump(0.1)
        page.ck_mount.setChecked(True)
        page.ed_mount.clear()
        page.ed_name.setText("实例A改")
        before = dict(inst_a)
        page._save()
        pump(0.1)
        check("7 空目录被拦下不落库",
              inst_a.get("mount_dir") == before.get("mount_dir")
              and inst_a.get("name") == before.get("name"), str(inst_a))

        page.close()
    finally:
        ACFG.save_config = real_save
        CP.save_config = real_save
        QMessageBox.information, QMessageBox.warning = real_info, real_warn
        QFileDialog.getExistingDirectory = real_pick
        shutil.rmtree(root, ignore_errors=True)

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 ==")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
