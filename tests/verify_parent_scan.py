# -*- coding: utf-8 -*-
"""验证：路径不存在时扫父目录能否找到实例（复现用户场景）"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication([])
from app.ui.main_window import MainWindow
from app.ui.setup_wizard import SetupWizard

win = MainWindow()
wiz = SetupWizard(win)
wiz.show()

# 模拟用户输入：一个不存在的路径，其父目录里有 ComfyUIImage 实例
TEST = r"F:\ComfyHub\ComfyUIImage\ComfyUI"
wiz.ed_dir.setText(TEST)


def run():
    print(f"输入路径: {TEST}", flush=True)
    print(f"该路径存在: {os.path.isdir(TEST)}", flush=True)
    print(f"父目录: {os.path.dirname(TEST)} (存在: {os.path.isdir(os.path.dirname(TEST))})", flush=True)
    wiz._detect_existing()


def check():
    cb = wiz.cb_found
    print("\n=== 3 秒后结果 ===", flush=True)
    print(f"候选下拉可见: {cb.isVisible()}", flush=True)
    print(f"候选数量: {cb.count()}", flush=True)
    print(f"候选项: {[cb.itemText(i) for i in range(cb.count())]}", flush=True)
    print(f"扫描提示文字: {wiz.lb_scan.text()[:100]}", flush=True)
    print(f"输入框锁定: {not wiz.ed_dir.isEnabled()}", flush=True)
    print(f"路径框已同步为: {wiz.ed_dir.text()}", flush=True)
    print(f"实例名已同步为: {wiz.ed_name.text()}", flush=True)
    app.quit()


QTimer.singleShot(500, run)
QTimer.singleShot(4000, check)
app.exec()
