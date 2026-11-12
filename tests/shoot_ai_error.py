# -*- coding: utf-8 -*-
"""AI 报错人话化效果验证（401 场景）

用法：cd F:/ComfyHub && .venv_build/Scripts/python.exe tests/shoot_ai_error.py [status]
输出：analysis/ai_error.png
"""
import os
import sys
import time

_TMP = os.path.join(os.environ.get("TEMP", "."), "ch_err_shot")
os.environ["COMFYHUB_CHAT_DIR"] = os.path.join(_TMP, "chats")
os.environ["COMFYHUB_SKILL_DIR"] = os.path.join(_TMP, "skills")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

app = QApplication(sys.argv)

from app import ai_assist  # noqa: E402
from app.ui.pages.ai_page import AiPage  # noqa: E402

status = int(sys.argv[1]) if len(sys.argv) > 1 else 401
detail = {401: "Unauthorized", 404: "The model `mimo-v9` does not exist",
          429: "rate limit exceeded"}.get(status, "error")


def _boom(*a, **k):
    raise ai_assist.ApiError(status, detail)


ai_assist.open_request = _boom       # 模拟服务端 4xx


class _MW:
    def __init__(self):
        self.cfg = {
            "instances": [{"id": "i1", "name": "ComfyUI",
                           "comfyui_path": "C:/tmp/fake_comfy", "port": 8188}],
            "settings": {"ai_enabled": True,
                         "ai_base_url": "https://api.xiaomimimo.com/v1",
                         "ai_api_key": "sk-fake", "ai_model": "mimo-v2.6-flash",
                         "ai_direct": True},
        }

    def current_inst(self):
        return self.cfg["instances"][0]


page = AiPage(_MW().cfg, _MW())
page.resize(1100, 800)
page.show()


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


pump(0.8)
page._quick("做一次实例体检，简要汇报状态")
for _ in range(120):                 # 等 worker 结束（最多 ~12s）
    pump(0.1)
    if page._worker is None:
        break
pump(0.5)

os.makedirs(os.path.join(ROOT, "analysis"), exist_ok=True)
out = os.path.join(ROOT, "analysis", "ai_error.png")
page.grab().save(out)
print("saved", out)
page.close()
