# -*- coding: utf-8 -*-
"""验证安装目标规则：
  1) 程序目录（无实例）→ 补 \ComfyUI
  2) 名字含 comfyui（无实例）→ 不补
  3) 已有实例 → 添加模式，路径同步为实例真实路径
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow
from app.ui.setup_wizard import SetupWizard

CASES = [
    (r"F:\_targettest\ProgramDir", "程序目录（无实例，无 comfyui 字样）→ 期望补 \\ComfyUI"),
    (r"F:\_targettest\MyComfyUI", "名字含 comfyui（无实例）→ 期望不补"),
    (r"F:\ComfyHub\ComfyUIImage\ComfyUI", "已有实例（父目录扫描）→ 期望添加模式"),
]

os.makedirs(r"F:\_targettest\ProgramDir", exist_ok=True)
os.makedirs(r"F:\_targettest\MyComfyUI", exist_ok=True)

for path, desc in CASES:
    win = MainWindow()
    wiz = SetupWizard(win)
    # ★ 先让 _auto_probe 跑完（它会覆盖路径），再设置被测路径
    t_wait = time.time()
    while time.time() - t_wait < 1.2:
        app.processEvents()
        time.sleep(0.05)
    wiz.ed_dir.setText(path)
    wiz._detect_existing()
    t0 = time.time()
    while time.time() - t0 < 6:
        app.processEvents()
        time.sleep(0.1)
    print(f"\n【{desc}】")
    print(f"  输入:      {path}")
    print(f"  路径框:    {wiz.ed_dir.text()}")
    print(f"  模式:      {wiz.lb_mode.text()[:40] if wiz.lb_mode.text() else '(未设置)'}")
    print(f"  安装选项:  {'隐藏' if wiz.install_opts.isHidden() else '显示'}")
    print(f"  候选:      {[wiz.cb_found.itemText(i) for i in range(wiz.cb_found.count())]}")
    wiz.close()
    win.close()

import shutil
shutil.rmtree(r"F:\_targettest", ignore_errors=True)
print("\n（测试目录已清理）")
