# -*- coding: utf-8 -*-
"""交测图：模型库页 + AI 触发的下载任务面板

用法：cd F:/ComfyHub && .venv_build/Scripts/python.exe tests/shoot_dl_panel.py
输出：analysis/shot_models.png / analysis/shot_dl_panel.png
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

app = QApplication(sys.argv)

from app import model_store as ms  # noqa: E402

ms.download_file = lambda *a, **k: "stub"


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


from app.ui.main_window import MainWindow  # noqa: E402

win = MainWindow()
win.resize(1440, 900)
win.show()
pump(2.0)

# 切到模型库页
for i in range(win.stack.count()):
    if win.stack.widget(i) is win.models_page:
        win.goto(i)
        break
pump(1.2)

dl = win.models_page.dl_center
dl._pump = lambda: None                      # 别真跑队列，纯截图
dl.request_download([
    ("black-forest-labs/FLUX.1-dev", "flux1-dev.safetensors", "C:/m/checkpoints"),
    ("org/b", "animeinoutuneVA_16.safetensors", "C:/m/loras"),
    ("org/c", "vae-ft-mse-840000-ema-pruned.safetensors", "C:/m/vae"),
], source="ai")
pump(0.8)

tids = list(dl._rows.keys())
dl._rows[tids[0]].set_state("下载中")
dl._rows[tids[0]].set_progress(4_100_000_000, 11_900_000_000)
dl._rows[tids[1]].set_fail("连接超时（网络不通或镜像站无响应）",
                           "URLError: <urlopen error [WinError 10060] ...>")
dl._rows[tids[2]].set_state("完成")
dl._refresh_head()
pump(0.5)

os.makedirs(os.path.join(ROOT, "analysis"), exist_ok=True)
win.grab().save(os.path.join(ROOT, "analysis", "shot_models.png"))
print("saved shot_models.png")

dl._menu.adjustSize()
dl._menu.popup(dl.mapToGlobal(dl.rect().bottomLeft()))
dl._menu.adjustSize()
pump(0.8)
dl._menu.grab().save(os.path.join(ROOT, "analysis", "shot_dl_panel.png"))
print("saved shot_dl_panel.png", dl._menu.width(), "x", dl._menu.height())
win.close()
