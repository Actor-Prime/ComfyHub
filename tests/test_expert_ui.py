# -*- coding: utf-8 -*-
"""专家模式整体移除的回归（2026-10-28 用户决定砍掉该功能）

验证点：
  1. comfyui_page 不再有专家模式开关/面板/全局参数控件
  2. 实例页「保存设置」清掉旧配置残留 expert_mode/launch
  3. build_command 不消费 settings.launch（即使旧配置里还存着）
测试结束恢复 config 原状，不污染真实设置。
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

# 弹窗拦截（_save 里的提示框会阻塞离屏测试）
QMessageBox.information = staticmethod(lambda *a, **k: None)

qapp = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow                  # noqa: E402
from app.config import load_config, save_config            # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def pump(sec=0.5):
    import time
    t = time.time()
    while time.time() - t < sec:
        qapp.processEvents()
        time.sleep(0.02)


# ---- 备份真实 config，注册临时实例，并预埋专家模式残留 ----
cfg = load_config()
orig_settings = json.dumps(cfg.get("settings", {}), ensure_ascii=False)
cfg_path = os.path.join(os.environ["APPDATA"], "ComfyHub", "config.json")
bak_path = cfg_path + ".bak_expert_ui"
if os.path.isfile(cfg_path):
    shutil.copy2(cfg_path, bak_path)

TMP = r"F:\_expert_ui_test\ComfyUI"
os.makedirs(TMP, exist_ok=True)
inst = {"id": "inst_euitest", "name": "EUITEST", "comfyui_path": TMP,
        "python_path": sys.executable, "port": 8412, "vram_mode": "自动",
        "extra_args": "", "auto_open_browser": True}
cfg.setdefault("instances", []).append(inst)
# 模拟旧版本用户的配置残留
cfg.setdefault("settings", {})["expert_mode"] = True
cfg["settings"]["launch"] = {
    "vram": "--highvram", "attn": "", "cache": "--cache-classic",
    "upcast": "", "flags": ["--disable-xformers"],
    "extra": "--reserve-vram 3"}
save_config(cfg)

try:
    win = MainWindow()
    win.show()
    pump(1.0)

    print("=== 1. 实例页无专家模式痕迹 ===")
    p = win.comfyui_page
    for i in range(win.cb_inst.count()):
        if win.cb_inst.itemData(i) == "inst_euitest":
            win.cb_inst.setCurrentIndex(i)
            break
    p.on_show()
    pump(0.8)
    check("无 chk_expert", not hasattr(p, "chk_expert"))
    check("无参数面板", not hasattr(p, "panel"))
    check("无全局下拉 gx_cache/gx_upcast",
          not hasattr(p, "gx_cache") and not hasattr(p, "gx_upcast"))
    check("无 flag 复选组", not hasattr(p, "_flag_boxes"))
    check("无全局自定义参数框", not hasattr(p, "ed_global_extra"))

    print("\n=== 2. 保存时清理旧配置残留 ===")
    p._save()                                      # 弹窗已拦截
    cfg2 = load_config()
    st = cfg2["settings"]
    check("expert_mode 残留被清", "expert_mode" not in st, str(st.get("expert_mode")))
    check("launch 残留被清", "launch" not in st, str(st.get("launch")))
    check("实例设置照常保存", any(i.get("id") == "inst_euitest"
                                and i.get("port") == 8412
                                for i in cfg2["instances"]))

    print("\n=== 3. build_command 不吃 settings.launch ===")
    # 即使把残留塞回去，启动命令也不得消费（防止无 UI 参数静默生效）
    cfg2["settings"]["expert_mode"] = True
    cfg2["settings"]["launch"] = {
        "vram": "--highvram", "cache": "--cache-classic",
        "flags": ["--disable-xformers"], "extra": "--reserve-vram 3"}
    save_config(cfg2)
    from app.process import build_command
    inst2 = next(i for i in cfg2["instances"]
                 if i.get("id") == "inst_euitest")
    cmd = build_command(inst2)
    print("    cmd:", " ".join(cmd))
    check("命令不含 --highvram", "--highvram" not in cmd, " ".join(cmd[-6:]))
    check("命令不含 --cache-classic", "--cache-classic" not in cmd)
    check("命令不含 --disable-xformers", "--disable-xformers" not in cmd)
    check("命令不含全局自定义参数", "--reserve-vram" not in cmd)
finally:
    # ---- 还原 config（实例列表 + settings 原值）----
    cfgf = load_config()
    cfgf["instances"] = [i for i in cfgf.get("instances", [])
                         if i.get("id") != "inst_euitest"]
    cfgf["settings"] = json.loads(orig_settings)
    save_config(cfgf)
    if os.path.isfile(bak_path):
        try:
            os.remove(bak_path)
        except OSError:
            pass
    shutil.rmtree(r"F:\_expert_ui_test", ignore_errors=True)

print("\n" + "=" * 44)
bad = [n for n, ok, _ in res if not ok]
print(f"结果: {len(res) - len(bad)}/{len(res)} 通过")
for n in bad:
    print("  ❌", n)
sys.exit(1 if bad else 0)
