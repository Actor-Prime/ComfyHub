# -*- coding: utf-8 -*-
"""插件依赖自检对话框截图（改这块 UI 后目视自检用）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/shoot_plugin_deps.py [dark|light]
输出：analysis/plugin_deps/dialog_<主题>.png

注意：不要设 QT_QPA_PLATFORM=offscreen —— CJK 会变豆腐块（记忆里的坑）。
"""
import os
import sys

# 不设 offscreen：CJK 会变豆腐块（记忆里的坑），直接真窗口 show()+grab()
sys.path.insert(0, r"F:\ComfyHub")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import plugin_deps  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.pages import env_page  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "dark"
theme.apply_theme(app, {"settings": {"theme": mode}}, force=True)

PY = os.path.join(os.environ["APPDATA"], "ComfyHub", "tools", "python", "python.exe")
COMFY = r"F:\ComfyUI-aki-v3\ComfyUI"
installed = plugin_deps.installed_packages(PY)
rows = plugin_deps.scan(COMFY, installed)
print("rows", len(rows), "missing", sum(len(r["missing"]) for r in rows))

inst = {"id": "i1", "name": "实例甲", "python_path": PY, "comfyui_path": COMFY}
dlg = env_page.PluginDepsDialog(None, inst, rows, installed)
dlg.resize(880, 600)
dlg.show()
app.processEvents()
dlg.log.appendPlainText("[示例] 正在校验（逐个 import）…")
app.processEvents()

out = os.path.join(r"F:\ComfyHub\analysis\plugin_deps", f"dialog_{mode}.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
dlg.grab().save(out)
print(out)
