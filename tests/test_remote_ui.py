# -*- coding: utf-8 -*-
"""远程实例 UI 冒烟（0.8.39）：离屏渲染实例设置页，验证连接方式切换不炸

做法：把 APPDATA 指到临时目录（config.CONFIG_PATH 在导入期计算，必须先设环境），
写一份含「本地 + 远程」两个实例的配置，然后离屏建主窗、截两张图。
产物：analysis/remote_ui_local.png / remote_ui_remote.png
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ★ 必须在 import app.* 之前重定向数据目录
_TMP = tempfile.mkdtemp(prefix="comfyhub_ui_")
os.environ["APPDATA"] = _TMP
_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)
cfg = {
    "version": 1,
    "last_instance_id": "inst_r",
    "instances": [
        {"id": "inst_l", "name": "本机实例", "kind": "local",
         "comfyui_path": r"F:\ComfyUI-aki-v3\ComfyUI",
         "python_path": r"F:\ComfyUI-aki-v3\python\python.exe",
         "port": 8188, "vram_mode": "自动", "extra_args": ""},
        {"id": "inst_r", "name": "云镜像", "kind": "remote",
         "base_url": "https://xxx.platform.com", "auth_type": "bearer",
         "auth_value": "demo-token", "auth_header_name": "X-API-KEY",
         "tls_verify": True, "port": 8188, "comfyui_path": "",
         "python_path": "", "vram_mode": "自动", "extra_args": ""},
    ],
    "settings": {},
}
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication          # noqa: E402
from app.ui.main_window import MainWindow           # noqa: E402

OUT = os.path.join(ROOT, "analysis")
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
w = MainWindow()
w.resize(1680, 1020)
w.show()
app.processEvents()
w.stack.setCurrentWidget(w.comfyui_page)
page = w.comfyui_page

# 选远程实例（current_inst() 认的是顶部下拉的当前项）
_ids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_ids.index("inst_r"))
page.load_current()
app.processEvents()

assert page.cb_kind.currentIndex() == 1, "远程实例应选中「远程实例」"
assert page.ed_url.text() == "https://xxx.platform.com", page.ed_url.text()
assert page.cb_auth.currentIndex() == 1, "auth_type=bearer 应选中第 2 项"
assert page.row_url.isVisible(), "远程模式应显示访问地址行"
assert not page.row_basic.isVisible(), "bearer 模式不该显示账号密码行"
items = page._t1v._items
hidden_ok = all(getattr(items[i][1], "isVisible", lambda: True)() is False
                for i in range(page._local_start, page._local_end)
                if hasattr(items[i][1], "isVisible"))
print("远程模式：本机专属行已隐藏 =", hidden_ok)
p1 = os.path.join(OUT, "remote_ui_remote.png")
page.grab().save(p1)
print("截图:", p1)

# 切回本地实例
w.cb_inst.setCurrentIndex(_ids.index("inst_l"))
page.load_current()
app.processEvents()
assert page.cb_kind.currentIndex() == 0
assert not page.row_url.isVisible(), "本地模式不该显示访问地址行"
vis_local = sum(1 for i in range(page._local_start, page._local_end)
                if hasattr(items[i][1], "isVisible") and items[i][1].isVisible())
print("本地模式：可见的本机专属行 =", vis_local)
p2 = os.path.join(OUT, "remote_ui_local.png")
page.grab().save(p2)
print("截图:", p2)

# 新增实例对话框：远程模式
from app.ui.dialogs import InstanceDialog   # noqa: E402
dlg = InstanceDialog(w)
dlg.cb_kind.setCurrentIndex(1)
dlg.ed_url.setText("192.168.1.20:8188")
dlg.ed_name.setText("另一台")
dlg._accept()
data = dlg.result_data()
print("对话框返回:", {k: data[k] for k in ("kind", "name", "base_url", "comfyui_path")})
assert data["kind"] == "remote" and data["comfyui_path"] == ""
assert data["base_url"].startswith("http://"), data["base_url"]

print("OK 全部通过")
# 直接退出：主窗 closeEvent 可能弹「最小化到托盘」确认框（离屏无人点，会挂住），
# 内核信息线程也还在跑 git —— 测试脚本不欠它一个优雅收尾。
shutil.rmtree(_TMP, ignore_errors=True)
sys.stdout.flush()
os._exit(0)
