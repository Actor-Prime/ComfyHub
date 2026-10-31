# -*- coding: utf-8 -*-
"""生成引擎统一下拉 UI 回归（2026-10-29 对齐绘世）

验证点：
  1. 实例页有 cb_eng（自动/CUDA i/DML/CPU），无旧 GPU/CPU 按钮
  2. DML 探测：本环境未装 torch-directml → 置灰提示项且不可停留
  3. Cross Attention 五档（含 Sage Attention 新增）
  4. 保存写 engine_backend/engine_index，清旧 device/cuda_device 残留
  5. 旧配置迁移：device=cpu → 引擎选中 CPU；cuda_device=1 → CUDA 1
测试结束恢复 config 原状，不污染真实设置。
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

qapp = QApplication(sys.argv[:1])
from app.ui.main_window import MainWindow                  # noqa: E402
from app.config import load_config, save_config            # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def pump(sec=0.5):
    t = time.time()
    while time.time() - t < sec:
        qapp.processEvents()
        time.sleep(0.02)


# ---- 备份真实 config，注册两个临时实例（一个旧字段、一个新字段）----
cfg = load_config()
cfg_path = os.path.join(os.environ["APPDATA"], "ComfyHub", "config.json")
bak_path = cfg_path + ".bak_engine_ui"
if os.path.isfile(cfg_path):
    shutil.copy2(cfg_path, bak_path)

TMP = r"F:\_engine_ui_test\ComfyUI"
os.makedirs(TMP, exist_ok=True)
# 旧字段实例（无 engine_backend，device/cpu + cuda_device）
inst_old = {"id": "inst_engold", "name": "ENGOLD", "comfyui_path": TMP,
            "python_path": sys.executable, "port": 8421, "vram_mode": "自动",
            "device": "cpu", "cuda_device": None,
            "extra_args": "", "auto_open_browser": True}
# 新字段实例（显式指定 CUDA 卡 0；本机 nvidia-smi 至少有卡 0）
inst_new = {"id": "inst_engnew", "name": "ENGNEW", "comfyui_path": TMP,
            "python_path": sys.executable, "port": 8422, "vram_mode": "自动",
            "engine_backend": "cuda", "engine_index": 0,
            "extra_args": "", "auto_open_browser": True}
cfg.setdefault("instances", []).extend([inst_old, inst_new])
save_config(cfg)

try:
    win = MainWindow()
    win.show()
    pump(1.0)
    p = win.comfyui_page

    def select_inst(iid):
        for i in range(win.cb_inst.count()):
            if win.cb_inst.itemData(i) == iid:
                win.cb_inst.setCurrentIndex(i)
                return True
        return False

    print("=== 1. 引擎下拉存在且结构正确 ===")
    check("有 cb_eng", hasattr(p, "cb_eng"))
    check("无旧 GPU/CPU 按钮", not hasattr(p, "b_gpu") and not hasattr(p, "b_cpu"))
    check("无旧 cb_gpu", not hasattr(p, "cb_gpu"))
    select_inst("inst_engnew")
    p.on_show()
    pump(0.5)
    # 等 DML 探测线程结束（未装 torch-directml → 置灰）
    t = time.time()
    while time.time() - t < 12:
        pump(0.1)
        w = getattr(p, "_dml_worker", None)
        if w is None or not w.isRunning():
            break
    texts = [p.cb_eng.itemText(i) for i in range(p.cb_eng.count())]
    datas = [p.cb_eng.itemData(i) for i in range(p.cb_eng.count())]
    check("首项=自动（推荐）", texts and "自动" in texts[0], str(texts[:1]))
    check("含 CUDA 条目", any(d and d[0] == "cuda" for d in datas), str(texts))
    check("含 DML 条目", any(d and d[0] == "dml" for d in datas), str(texts))
    check("含 CPU 条目", ("cpu", None) in datas, str(datas))
    # DML 未装环境：应为置灰提示项
    dml_rows = [i for i, d in enumerate(datas) if d and d[0] == "dml"]
    if dml_rows:
        it = p.cb_eng.model().item(dml_rows[0])
        check("DML 未装环境 → 置灰提示", it is not None and not it.isEnabled(),
              p.cb_eng.itemText(dml_rows[0]))
    else:
        check("DML 条目存在", False, "探测后无 DML 项")

    print("\n=== 2. 新字段实例恢复选中 ===")
    cur = p.cb_eng.currentData()
    check("恢复 engine_backend=cuda", cur and cur[0] == "cuda", str(cur))
    check("恢复 engine_index=0", cur and cur[1] == 0, str(cur))

    print("\n=== 3. 旧字段迁移（device=cpu）===")
    select_inst("inst_engold")
    p.on_show()
    pump(0.8)
    cur = p.cb_eng.currentData()
    check("device=cpu → 引擎选中 CPU", cur and cur[0] == "cpu", str(cur))
    check("CPU 时显存优化置灰", not p.cb_vram.isEnabled())

    print("\n=== 4. 保存写新字段、清旧残留 ===")
    p._save()   # 弹窗已拦截
    cfg2 = load_config()
    io = next(i for i in cfg2["instances"] if i.get("id") == "inst_engold")
    check("保存 engine_backend=cpu", io.get("engine_backend") == "cpu",
          str(io.get("engine_backend")))
    check("旧 device 残留被清", "device" not in io, str(io.get("device")))
    check("旧 cuda_device 残留被清", "cuda_device" not in io,
          str(io.get("cuda_device")))
    # 切到 CUDA 项再存
    for i in range(p.cb_eng.count()):
        d = p.cb_eng.itemData(i)
        if d and d[0] == "cuda":
            p.cb_eng.setCurrentIndex(i)
            break
    p._save()
    cfg3 = load_config()
    io = next(i for i in cfg3["instances"] if i.get("id") == "inst_engold")
    check("改选 CUDA 后保存 engine_backend=cuda",
          io.get("engine_backend") == "cuda", str(io.get("engine_backend")))
    check("CPU→CUDA 后显存优化恢复可用", p.cb_vram.isEnabled())

    print("\n=== 5. Cross Attention 五档 ===")
    cas = [p.cb_ca.itemText(i) for i in range(p.cb_ca.count())]
    check("五档齐全", len(cas) == 5, str(cas))
    check("含 Sage Attention", any("Sage" in x for x in cas), str(cas))
    check("首档 xFormers 优先", "xFormers" in cas[0], cas[0])
finally:
    # ---- 还原 config ----
    cfgf = load_config()
    cfgf["instances"] = [i for i in cfgf.get("instances", [])
                         if i.get("id") not in ("inst_engold", "inst_engnew")]
    save_config(cfgf)
    if os.path.isfile(bak_path):
        try:
            os.remove(bak_path)
        except OSError:
            pass
    shutil.rmtree(r"F:\_engine_ui_test", ignore_errors=True)

print("\n" + "=" * 44)
bad = [n for n, ok, _ in res if not ok]
print(f"结果: {len(res) - len(bad)}/{len(res)} 通过")
for n in bad:
    print("  ❌", n)
sys.exit(1 if bad else 0)
