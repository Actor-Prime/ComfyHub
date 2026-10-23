# -*- coding: utf-8 -*-
"""插件页集成测试：假实例（4 插件）+ 模式选择 / 应用 / 冲突显示"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow
from app.config import load_config, save_config
from app import plugin_profiles as pp

BASE = r"F:\_pp_ui_test"
CP = os.path.join(BASE, "ComfyUI")
NODES = os.path.join(CP, "custom_nodes")
PLUGINS = {"plugA": ["SharedNode", "UniqueA"], "plugB": ["SharedNode"], "plugC": ["UniqueC"]}

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.04)


# ---- 造环境 ----
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(NODES, exist_ok=True)
open(os.path.join(CP, "main.py"), "w").write("")
open(os.path.join(CP, "folder_paths.py"), "w").write("")
os.makedirs(os.path.join(CP, "comfy"), exist_ok=True)
for name, nodes in PLUGINS.items():
    d = os.path.join(NODES, name)
    os.makedirs(d)
    body = ",\n".join(f'    "{k}": 1' for k in nodes)
    open(os.path.join(d, "__init__.py"), "w", encoding="utf-8").write(
        "NODE_CLASS_MAPPINGS = {\n" + body + "\n}\n")

# ---- 注册为实例（备份原 config）----
cfg_backup = os.path.join(os.environ["APPDATA"], "ComfyHub", "config.json.bak_test")
cfg_path = os.path.join(os.environ["APPDATA"], "ComfyHub", "config.json")
if os.path.isfile(cfg_path):
    shutil.copy2(cfg_path, cfg_backup)

cfg = load_config()
cfg.setdefault("instances", []).append({
    "id": "inst_pptest", "name": "PPTEST", "comfyui_path": CP,
    "python_path": sys.executable, "port": 8399, "vram_mode": "自动",
    "extra_args": "", "auto_open_browser": False,
    "plugin_profiles": [
        {"name": "全开", "enabled": ["plugA", "plugB", "plugC"], "args": ""},
        {"name": "只A", "enabled": ["plugA"], "args": ""},
    ],
    "last_profile": "全开",
})
save_config(cfg)

try:
    win = MainWindow()
    win.show()
    pump(1.5)
    # 切到 PPTEST 实例
    for i in range(win.cb_inst.count()):
        if win.cb_inst.itemData(i) == "inst_pptest":
            win.cb_inst.setCurrentIndex(i)
            break
    pump(1.0)
    p = win.plugins_page
    # 真实行为：切实例只通知"当前可见页"，所以先切到插件页再刷新
    p.on_show()
    pump(0.5)

    print("=== 1. 页面元素 ===")
    check("模式下拉有 2 项", p.cb_profile.count() == 2,
          str([p.cb_profile.itemText(i) for i in range(p.cb_profile.count())]))
    check("当前模式=全开", p.cb_profile.currentText() == "全开", p.cb_profile.currentText())
    check("插件表 3 行", p.tb.rowCount() == 3, str(p.tb.rowCount()))

    print("\n=== 2. 冲突检查 ===")
    QMessageBox.warning = staticmethod(lambda *a, **k: None)   # 屏蔽弹窗
    p._check_duplicates()
    pump(2.0)
    check("冲突映射含 plugA/plugB",
          "plugA" in p._conflict_map and "plugB" in p._conflict_map,
          str(p._conflict_map))
    check("表格第4列显示冲突",
          "SharedNode" in (p.tb.item(0, 3).text() or ""),
          p.tb.item(0, 3).text())

    print("\n=== 3. 切到「只A」并应用 ===")
    idx = p.cb_profile.findText("只A")
    print(f"    [debug] 只A 的索引={idx}, 当前={p.cb_profile.currentText()}")
    p.cb_profile.setCurrentIndex(idx)
    pump(0.4)
    p._apply_profile()
    # 等 worker 完成
    t = time.time()
    while time.time() - t < 8 and p._worker and p._worker.isRunning():
        pump(0.1)
    pump(0.5)
    state = {x["name"]: x["enabled"] for x in pp.list_plugins(CP)}
    check("plugA 启用", state.get("plugA") is True, str(state))
    check("plugB 停用", state.get("plugB") is False, str(state))
    check("plugC 停用", state.get("plugC") is False, str(state))
    # 直接验证"记录上次模式"的业务逻辑（不依赖 Qt 信号在测试环境下的触发时机）
    p.cb_profile.setCurrentIndex(p.cb_profile.findText("只A"))
    p._on_profile_changed(p.cb_profile.currentIndex())
    pump(0.4)
    saved = [i for i in load_config()["instances"] if i.get("id") == "inst_pptest"]
    lp = saved[0].get("last_profile") if saved else None
    check("last_profile 已记忆", lp == "只A", str(lp))

    print("\n=== 4. 切回「全开」并应用 ===")
    p.cb_profile.setCurrentIndex(p.cb_profile.findText("全开"))
    pump(0.4)
    p._apply_profile()
    t = time.time()
    while time.time() - t < 8 and p._worker and p._worker.isRunning():
        pump(0.1)
    pump(0.5)
    state2 = {x["name"]: x["enabled"] for x in pp.list_plugins(CP)}
    check("三个插件全部恢复启用", all(state2.values()), str(state2))

    win.close()
finally:
    # 恢复 config
    if os.path.isfile(cfg_backup):
        shutil.copy2(cfg_backup, cfg_path)
        os.remove(cfg_backup)
    shutil.rmtree(BASE, ignore_errors=True)
    print("\n（配置与测试目录已恢复）")

passed = sum(1 for _, ok, _ in res if ok)
print("=" * 50)
print(f"结果：{passed}/{len(res)} 通过")
for n, ok, d in res:
    if not ok:
        print(f"  ❌ {n}  [{d}]")
sys.exit(0 if passed == len(res) else 1)
