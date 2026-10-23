# -*- coding: utf-8 -*-
"""精确复现用户操作：选 F:\\ComfyHub → 点候选 → 上下切换两个实例"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QWizard

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow
from app.ui.setup_wizard import SetupWizard
from app import bootstrap

win = MainWindow()
wiz = SetupWizard(win)
wiz.show()
t = time.time()
while time.time() - t < 1.5:
    app.processEvents()
    time.sleep(0.05)

print("=== 步骤1：选择 F:\\ComfyHub ===", flush=True)
wiz.ed_dir.setText(r"F:\ComfyHub")
wiz._detect_existing()
t = time.time()
while time.time() - t < 10:
    app.processEvents()
    time.sleep(0.1)

print(f"候选数量: {wiz.cb_found.count()}")
print(f"候选: {[wiz.cb_found.itemText(i) for i in range(wiz.cb_found.count())]}")
print(f"_scan_candidates: {wiz._scan_candidates}", flush=True)
print(f"_py_map keys: {list(wiz._py_map.keys()) if wiz._py_map else '空'}", flush=True)
for k, v in (wiz._py_map or {}).items():
    print(f"  {k} → {v}", flush=True)
print(f"共享 python: {os.path.join(bootstrap.tools_root(), 'python', 'python.exe')} "
      f"存在={os.path.isfile(os.path.join(bootstrap.tools_root(), 'python', 'python.exe'))}", flush=True)

print("\n=== 步骤2：依次点击每个候选（模拟上下切换）===", flush=True)
for i in range(wiz.cb_found.count()):
    wiz.cb_found.setCurrentIndex(i)
    t = time.time()
    while time.time() - t < 6:
        app.processEvents()
        time.sleep(0.1)
    cur = wiz._scan_candidates[min(max(0, wiz.cb_found.currentIndex()), len(wiz._scan_candidates) - 1)]
    cands = wiz._py_map.get(cur) or wiz._py_map.get(os.path.normpath(cur)) or []
    print(f"\n[点第 {i + 1} 个: {wiz.cb_found.currentText()}]", flush=True)
    print(f"  comfy = {cur}", flush=True)
    print(f"  cands = {cands}", flush=True)
    print(f"  状态  = {wiz.lb_scan.text().replace(chr(10), ' | ')[:120]}", flush=True)
    print(f"  完成按钮={wiz.button(QWizard.FinishButton).isEnabled()} "
          f"下一步={wiz.button(QWizard.NextButton).isEnabled()}", flush=True)

import shutil
shutil.rmtree(r"F:\_targettest", ignore_errors=True)
