# -*- coding: utf-8 -*-
"""模型库 · 删文件 / 刷新 / 导入 业务闭环（Qt 层直测，2026-11-02）

为什么不让 UIA 做：Qt 的 QTableWidget 在 UIA 里只有行、没有单元格文本，
分类 chip 是 QCheckBox 但状态读回来不可靠 —— 真机自动化验证不了"列表里到底有什么"。
所以这条链路改成在 Qt 层直接调页面方法 + 打桩文件对话框，验证的是真实业务代码。

覆盖：
  A 选中 checkpoints 分类 → 表格行 = 磁盘文件数
  B 删掉文件 → 刷新 → 行数归零
  C 点「导入」→（文件对话框打桩返回路径）→ 选「复制」→ 文件真的被复制回 models/checkpoints
  D 刷新 → 行数回到 1，且文件名正确
  E 选中不存在的分类 → 刷新不炸（边界）

用法：.venv_build/Scripts/python.exe tests/test_models_page_flow.py
"""
import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QDialog

app = QApplication([])

# 测试夹具放在临时目录，脚本自己建（不依赖机器上已有的假实例）
COMFY = os.environ.get("COMFY_TEST_DIR") or os.path.join(
    os.environ.get("TEMP", "."), "comfyhub_flow_fixture")
CHECK_DIR = os.path.join(COMFY, "models", "checkpoints")
for _sub in ("comfy", "custom_nodes", "models"):
    os.makedirs(os.path.join(COMFY, _sub), exist_ok=True)
os.makedirs(CHECK_DIR, exist_ok=True)
for _name, _body in (("main.py", 'print("fixture")\n'),
                     ("nodes.py", "NODE_CLASS_MAPPINGS = {}\n"),
                     ("folder_paths.py", "def f(x): return []\n")):
    _p = os.path.join(COMFY, _name)
    if not os.path.isfile(_p):
        with open(_p, "w", encoding="utf-8") as _fh:
            _fh.write(_body)
TEST_FILE = "flow_test_model.safetensors"
TEST_PATH = os.path.join(CHECK_DIR, TEST_FILE)
SRC = os.path.join(os.environ.get("TEMP", "."), TEST_FILE)

RESULTS = []


def chk(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{extra}]" if extra and not ok else ""),
          flush=True)


def pump(n=30):
    for _ in range(n):
        app.processEvents()


# 配置文件指向测试实例
import json  # noqa: E402
from app import config as cfgmod  # noqa: E402

REAL_CFG = cfgmod.CONFIG_PATH
BAK = REAL_CFG + ".flow_bak"
shutil.copy2(REAL_CFG, BAK)
cfg = json.load(open(REAL_CFG, encoding="utf-8"))
cfg["instances"] = [{"id": "inst_flow", "name": "闭环测试实例", "comfyui_path": COMFY,
                     "python_path": sys.executable, "port": 8199}]
cfg["last_instance_id"] = "inst_flow"
json.dump(cfg, open(REAL_CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

from app.ui.theme import apply_theme          # noqa: E402
from app.ui.main_window import MainWindow     # noqa: E402

apply_theme(app, cfg)
win = MainWindow()
win.show()
pump(30)
page = win.models_page
win.goto(4)
pump(30)

try:
    # ---------- A 选中 checkpoints
    # 清空 checkpoints（测试目录，里面都是历史测试产物），保证行数断言是绝对的
    for old in os.listdir(CHECK_DIR):
        try:
            os.remove(os.path.join(CHECK_DIR, old))
        except OSError:
            pass
    with open(SRC, "w", encoding="utf-8") as f:
        f.write("flow payload")
    shutil.copy2(SRC, TEST_PATH)

    cats = [page.chip_group.button(i).text() for i in range(1, 5) if page.chip_group.button(i)]
    print("  分类 chip：", cats, flush=True)
    idx = None
    for i in range(1, page.chip_group.buttons().__len__() + 1):
        b = page.chip_group.button(i)
        if b is None:
            continue
        if b.text().strip().lower() in ("checkpoints", "检查点"):
            idx = i
            break
    chk("1 找到 checkpoints 分类 chip", idx is not None, str(cats))
    page.chip_group.button(idx).setChecked(True)
    page.refresh_stats()
    pump()
    chk("2 选中分类后表格列出文件", page.tb.rowCount() == 1,
        f"行数={page.tb.rowCount()}")
    if page.tb.rowCount():
        chk("3 行内文件名正确", page.tb.item(0, 0).text() == TEST_FILE,
            page.tb.item(0, 0).text())

    # ---------- B 删文件 → 刷新
    os.remove(TEST_PATH)
    page.refresh_stats()
    pump()
    # 空目录不是 0 行：页面会插一行「（此类别暂无文件）」提示（设计如此）
    txt = page.tb.item(0, 0).text() if page.tb.rowCount() else ""
    chk("4 删文件后刷新：只剩「暂无文件」提示行",
        page.tb.rowCount() == 1 and "暂无" in txt, f"行数={page.tb.rowCount()} 首行={txt!r}")

    # ---------- C 导入（文件对话框 + 导入方式框都打桩）
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([SRC], ""))
    picked = {}

    def fake_exec(self):
        for b in self.findChildren(QPushButton):
            if "复制" in b.text():
                picked["mode"] = b.text()
                b.click()
                break
        return 1
    QMessageBox.exec = fake_exec
    page._import_files()
    pump(60)
    chk("5 导入对话框选中「复制」", "复制" in picked.get("mode", ""), str(picked))
    chk("6 导入后文件回到磁盘", os.path.exists(TEST_PATH), TEST_PATH)

    # ---------- D 刷新 → 回来
    page.refresh_stats()
    pump()
    chk("7 刷新后表格重新列出该文件", page.tb.rowCount() == 1, f"行数={page.tb.rowCount()}")

    # ---------- E 边界：未选分类（全部视图）不炸
    page.chip_group.button(0).setChecked(True) if page.chip_group.button(0) else None
    page.refresh_stats()
    pump()
    chk("8 「全部」视图刷新不抛异常", page.tb.rowCount() >= 0, f"行数={page.tb.rowCount()}")

    # ---------- F 无实例时不炸
    win.cfg["instances"] = []
    win.cfg["last_instance_id"] = None
    page.refresh_stats()
    pump()
    chk("9 无实例时刷新不炸（空态）", page.tb.rowCount() >= 0, f"行数={page.tb.rowCount()}")
finally:
    shutil.move(BAK, REAL_CFG)
    for p in (TEST_PATH, SRC):
        try:
            os.remove(p)
        except OSError:
            pass

fails = [r for r in RESULTS if not r[1]]
print("=" * 52)
print(f"模型库业务闭环：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
for n, _, d in fails:
    print(f"  FAIL {n}  [{d}]")
sys.stdout.flush()
os._exit(1 if fails else 0)
