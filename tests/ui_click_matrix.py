# -*- coding: utf-8 -*-
"""ComfyHub 向导第一页 · 全路径点击与按钮状态矩阵测试

覆盖：
  A 添加已有实例（单实例 / 多实例切换 / 缓存 / 缺依赖 / 无 Python 环境）
  B 全新安装（空目录 / 不存在路径 / 含 comfyui 命名 / 盘根）
  C 按钮互斥（不该出现的按钮是否禁用、是否出现两个"完成"）
  D 零副作用（取消/检测是否创建文件夹）
  E 环境移除（备份 tools → 清空 → 验证按钮全禁用 → 恢复）

用法：
  .venv_build/Scripts/python.exe tests/ui_click_matrix.py
"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWizard

app = QApplication(sys.argv[:1])
from app import bootstrap
from app.ui.main_window import MainWindow
from app.ui.setup_wizard import SetupWizard

TOOLS = bootstrap.tools_root()
TOOLS_BAK = TOOLS.rstrip("\\/") + "_bak_test"
TMP = r"F:\_ui_matrix"

RESULTS = []


def log(msg):
    print(msg, flush=True)


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    log(f"  {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


def pump(seconds):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.05)


def new_wizard():
    """新建一个向导，并等自动探测跑完（避免它覆盖我们设的路径）"""
    win = MainWindow()
    wiz = SetupWizard(win)
    wiz.show()
    pump(1.3)
    return win, wiz


def state(wiz):
    """抓取当前界面关键状态"""
    return {
        "next_text": wiz.button(QWizard.NextButton).text(),
        "next_on": wiz.button(QWizard.NextButton).isEnabled(),
        "next_vis": wiz.button(QWizard.NextButton).isVisible(),
        "finish_on": wiz.button(QWizard.FinishButton).isEnabled(),
        "finish_vis": wiz.button(QWizard.FinishButton).isVisible(),
        "back_on": wiz.button(QWizard.BackButton).isEnabled(),
        "cancel_on": wiz.button(QWizard.CancelButton).isEnabled(),
        "install_shown": not wiz.install_opts.isHidden(),
        "cands": [wiz.cb_found.itemText(i) for i in range(wiz.cb_found.count())],
        "cand_vis": not wiz.cb_found.isHidden(),
        "text": wiz.lb_scan.text().splitlines()[0][:60] if wiz.lb_scan.text() else "",
        "path": wiz.ed_dir.text(),
        "name": wiz.ed_name.text(),
    }


def detect(wiz, path, wait=9):
    wiz.ed_dir.setText(path)
    wiz._detect_existing()
    pump(wait)
    return state(wiz)


# ============================================================
# A. 添加已有实例
# ============================================================
def scenario_multi_instance():
    log("\n【A1】多实例（F:\\ComfyHub 下两个实例）→ 切换候选")
    win, wiz = new_wizard()
    s = detect(wiz, r"F:\ComfyHub")
    record("A1.1 列出候选", len(s["cands"]) >= 2, f"{s['cands']}")
    record("A1.2 添加模式（安装参数隐藏）", not s["install_shown"])
    record("A1.3 完成按钮可用", s["next_on"] and s["next_text"] == "完成")
    record("A1.4 无独立 Finish 按钮", not s["finish_vis"])
    # 切换候选
    for i in range(len(s["cands"])):
        wiz.cb_found.setCurrentIndex(i)
        pump(6)
        st = state(wiz)
        record(f"A1.5 候选{i+1} 可完成（{st['cands'][i] if i < len(st['cands']) else '?'}）",
               st["next_on"] and "验证通过" in st["text"], st["text"][:28])
    wiz.close(); win.close()


def scenario_no_python():
    log("\n【A2】完全无 Python 环境（临时移走 tools）→ 应禁止添加")
    if os.path.isdir(TOOLS):
        shutil.rmtree(TOOLS_BAK, ignore_errors=True)
        shutil.move(TOOLS, TOOLS_BAK)
    try:
        win, wiz = new_wizard()
        s = detect(wiz, r"F:\ComfyHub\ComfyUIH3")
        record("A2.1 完成按钮禁用", not s["next_on"], f"on={s['next_on']}")
        record("A2.2 下一步也禁用（防误装覆盖）", not (s["next_on"] and s["next_text"].startswith("下一步")))
        record("A2.3 无可见的 Finish 按钮", not s["finish_vis"])
        record("A2.4 提示无 Python 环境", "没有可用的 Python" in s["text"], s["text"][:30])
        record("A2.5 取消仍可用（能退出）", s["cancel_on"])
        record("A2.6 不显示安装参数", not s["install_shown"])
        wiz.close(); win.close()
    finally:
        if os.path.isdir(TOOLS_BAK):
            shutil.rmtree(TOOLS, ignore_errors=True)
            shutil.move(TOOLS_BAK, TOOLS)
            log("  （tools 已恢复）")


# ============================================================
# B. 全新安装
# ============================================================
def scenario_fresh_install():
    log("\n【B1】空目录 → 全新安装模式")
    os.makedirs(os.path.join(TMP, "Empty"), exist_ok=True)
    win, wiz = new_wizard()
    s = detect(wiz, os.path.join(TMP, "Empty"), wait=6)
    record("B1.1 显示安装参数", s["install_shown"])
    record("B1.2 下一步可用", s["next_on"] and "下一步" in s["next_text"], s["next_text"])
    record("B1.3 无候选残留", len(s["cands"]) == 0, f"{s['cands']}")
    record("B1.4 完成不可点", not (s["next_text"] == "完成"))
    wiz.close(); win.close()


def scenario_nonexistent():
    log("\n【B2】不存在的路径（父目录无实例）→ 全新安装 + 补 \\ComfyUI")
    nested = os.path.join(TMP, "NoSuch", "Deep")
    win, wiz = new_wizard()
    s = detect(wiz, nested, wait=6)
    ok_path = s["path"].lower().endswith("comfyui")
    record("B2.1 自动补 \\ComfyUI", ok_path, s["path"])
    record("B2.2 显示安装参数", s["install_shown"])
    record("B2.3 目录未被创建（零副作用）", not os.path.isdir(nested), nested)
    wiz.close(); win.close()


def scenario_comfyui_named():
    log("\n【B3】目录名含 comfyui 且为空 → 不补子目录")
    d = os.path.join(TMP, "MyComfyUI")
    os.makedirs(d, exist_ok=True)
    win, wiz = new_wizard()
    s = detect(wiz, d, wait=6)
    record("B3.1 不重复套 \\ComfyUI", os.path.normpath(s["path"]) == os.path.normpath(d), s["path"])
    wiz.close(); win.close()


# ============================================================
# C. 取消零残留
# ============================================================
def scenario_cancel_no_side_effect():
    log("\n【C1】点取消 → 不留下任何文件夹")
    target = os.path.join(TMP, "WillCancel", "ComfyUI")
    win, wiz = new_wizard()
    detect(wiz, target, wait=5)
    existed_before = os.path.isdir(os.path.dirname(target))
    wiz.reject()
    pump(0.5)
    record("C1.1 取消后目录未创建", not os.path.isdir(os.path.dirname(target)),
           f"before={existed_before}")
    wiz.close(); win.close()


# ============================================================
# D. 中文路径警告
# ============================================================
def scenario_chinese_path():
    log("\n【D1】中文路径 → 应显示警告")
    d = os.path.join(TMP, "中文目录")
    os.makedirs(d, exist_ok=True)
    win, wiz = new_wizard()
    detect(wiz, d, wait=5)
    shown = not wiz.lb_warn.isHidden()
    record("D1.1 显示中文路径警告", shown, wiz.lb_warn.text()[:30] if shown else "未显示")
    wiz.close(); win.close()


# ============================================================
def main():
    log("=" * 70)
    log("ComfyHub 向导第一页 · 点击与按钮状态矩阵测试")
    log("=" * 70)
    os.makedirs(TMP, exist_ok=True)
    try:
        scenario_multi_instance()
        scenario_no_python()          # 含 tools 备份/恢复
        scenario_fresh_install()
        scenario_nonexistent()
        scenario_comfyui_named()
        scenario_cancel_no_side_effect()
        scenario_chinese_path()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        if os.path.isdir(TOOLS_BAK) and not os.path.isdir(TOOLS):
            shutil.move(TOOLS_BAK, TOOLS)

    log("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    log(f"结果：{passed}/{total} 通过")
    for name, ok, detail in RESULTS:
        if not ok:
            log(f"  ❌ {name}  [{detail}]")
    log("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
