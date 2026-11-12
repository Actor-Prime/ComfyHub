# -*- coding: utf-8 -*-
"""服务商下拉出图 + 套餐条目联动自检

用法：cd F:/ComfyHub && .venv_build/Scripts/python.exe tests/shoot_provider_combo.py
输出：analysis/provider_combo.png（下拉展开图）
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

from app import ai_providers as prov  # noqa: E402
from app.ui.pages.settings_page import SettingsPage  # noqa: E402


class _MW:
    def __init__(self):
        self.cfg = {"instances": [], "settings": {}}

    def current_inst(self):
        return None


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


page = SettingsPage(_MW().cfg, _MW())
page.resize(1000, 760)
page.show()
pump(1.0)

combo = page.cb_ai_provider
print("条目数", combo.count())
titles = [combo.itemText(i) for i in range(combo.count())
          if combo.itemData(i) is None and combo.itemText(i)]
print("分组标题", titles)

# ---- 联动自检：选套餐条目，看填入的 base ----
bad = 0
for p in prov.all_providers():
    if p["group"] != "Token/编程套餐":
        continue
    idx = combo.findData(p["id"])
    if idx < 0:
        print(" MISS", p["id"])
        bad += 1
        continue
    combo.setCurrentIndex(idx)
    pump(0.15)
    got = page.ed_ai_base.text()
    ok = got.rstrip("/") == p["base"].rstrip("/")
    if not ok:
        bad += 1
    print(("  ok  " if ok else " FAIL "), p["id"], "→", got)

os.makedirs(os.path.join(ROOT, "analysis"), exist_ok=True)
combo.showPopup()
pump(0.5)
view = combo.view().window()
pm = view.grab()
out = os.path.join(ROOT, "analysis", "provider_combo.png")
pm.save(out)
print("saved", out, pm.width(), "x", pm.height(), "| 联动失败", bad)
combo.hidePopup()
page.close()
