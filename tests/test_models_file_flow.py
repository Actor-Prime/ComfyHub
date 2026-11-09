# -*- coding: utf-8 -*-
"""模型库 · 删文件 / 刷新 / 重新导入 功能闭环（2026-11-02）

为什么单独一个脚本：
  windows-MCP 的元素树对 ComfyHub 每个窗口只暴露约 20 个节点，页面内部的分类 chip、
  导入按钮、表格都够不着；所以这条「删除 → 刷新消失 → 重新导入 → 列表回来」的闭环
  用 uiautomation 直接驱动，每步都是真实点击 + 真实文件系统操作。

关键技巧（踩过的坑）：
  · 分类 chip 是 QCheckBox，要点**左边复选框方块**（left+8），点中间文字有时不生效
  · QTableWidget 的行在 UIA 里有，但**单元格文本读不到** → 断言改用「行数」：
    选中 checkpoints 后行数=文件数，删掉文件刷新后行数归零，导入回来又变 1
  · 「全部」分类下表格列的是各分类汇总，不是文件明细，测试必须先选具体分类
  · 未选分类时点「导入」只会弹「请先在分类 chips 选择一个具体类别」提示框

覆盖：
  A 磁盘有测试文件 → 选 checkpoints → 表格行数 ≥1
  B 删掉文件 → 点 🔄 刷新 → 行数归零
  C 点 📥 导入 → 文件对话框输入路径 → 确定 → 选「📋 复制」→ 文件回到磁盘
  D 再刷新 → 行数回到 ≥1（闭环成立）

用法：
  python tests/test_models_file_flow.py
  python tests/test_models_file_flow.py --comfy F:\\ComfyUI --keep
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

import uiautomation as auto
import win32gui
import win32process

AP = argparse.ArgumentParser()
AP.add_argument("--exe", default=r"F:\ComfyHub\dist\ComfyHub\ComfyHub.exe")
AP.add_argument("--comfy", default=r"F:\_fake_comfy")
AP.add_argument("--keep", action="store_true")
ARGS = AP.parse_args()

EXE = os.path.abspath(ARGS.exe)
COMFY = os.path.abspath(ARGS.comfy)
CHECK_DIR = os.path.join(COMFY, "models", "checkpoints")
TEST_FILE = "flow_test_model.safetensors"
TEST_PATH = os.path.join(CHECK_DIR, TEST_FILE)
SRC_PATH = os.path.join(os.environ.get("TEMP", "."), TEST_FILE)

RESULTS = []


def chk(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{extra}]" if extra and not ok else ""),
          flush=True)


def pids():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ComfyHub.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, encoding="gbk", errors="ignore")
    out = set()
    for line in r.stdout.splitlines():
        p = [x.strip('"') for x in line.split('","')]
        if len(p) > 1 and p[0].lower() == "comfyhub.exe":
            out.add(int(p[1]))
    return out


def wait_window(timeout=45):
    end = time.time() + timeout
    while time.time() < end:
        ps = pids()
        if ps:
            box = {}

            def cb(w, _):
                try:
                    if win32process.GetWindowThreadProcessId(w)[1] in ps \
                            and win32gui.IsWindowVisible(w) and "ComfyHub" in win32gui.GetWindowText(w):
                        box["h"] = w
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(cb, None)
            if "h" in box:
                return box["h"]
        time.sleep(0.5)
    return None


def all_nodes(node, depth=18):
    out, stack = [], [(node, 0)]
    while stack:
        n, d = stack.pop()
        if d > depth:
            continue
        for ch in n.GetChildren():
            out.append((d, ch))
            stack.append((ch, d + 1))
    return out


def find(root, text, ctype=None, depth=18):
    for _d, ch in all_nodes(root, depth):
        try:
            if text in (ch.Name or "") and (ctype is None or ch.ControlTypeName == ctype):
                return ch
        except Exception:
            pass
    return None


def click_ctrl(ctrl, dx=None):
    r = ctrl.BoundingRectangle
    x = r.left + (dx if dx is not None else (r.right - r.left) // 2)
    auto.Click(x, (r.top + r.bottom) // 2)
    time.sleep(1.2)


def row_count(root):
    """模型库表格行数（单元格文本读不到，但行数可靠）

    坑：侧栏导航也是个 ListControl（8 行），必须按**宽度**挑出真正的表格
    （表格横跨整个窗口，侧栏只有 48px 宽）。
    """
    best, best_w = -1, 0
    for _d, ch in all_nodes(root):
        try:
            if ch.ControlTypeName not in ("ListControl", "TableControl"):
                continue
            r = ch.BoundingRectangle
            w = r.right - r.left
            kids = ch.GetChildren()
            if not kids or w < 400:
                continue
            if w > best_w:
                best, best_w = len(kids), w
        except Exception:
            pass
    return best


def find_dialog(timeout=10):
    """等文件选择对话框出现（UIA 顶层窗口，类名 #32770 或标题含 打开/导入）"""
    end = time.time() + timeout
    while time.time() < end:
        for w in auto.GetRootControl().GetChildren():
            try:
                nm = w.Name or ""
                if w.ControlTypeName != "WindowControl":
                    continue
                if "打开" in nm or "导入" in nm or "选择" in nm:
                    return w
            except Exception:
                pass
        time.sleep(0.4)
    return None


def main():
    if not os.path.isdir(CHECK_DIR):
        sys.exit(f"找不到 models/checkpoints：{CHECK_DIR}")

    with open(SRC_PATH, "w", encoding="utf-8") as f:
        f.write("flow test payload")
    shutil.copy2(SRC_PATH, TEST_PATH)

    subprocess.run(["taskkill", "/IM", "ComfyHub.exe", "/F"], capture_output=True)
    time.sleep(1.5)
    subprocess.run(["explorer.exe", os.path.basename(EXE)], cwd=os.path.dirname(EXE))
    h = wait_window()
    chk("0 启动并拿到主窗口", h is not None)
    if not h:
        return report()
    time.sleep(2.5)
    root = auto.ControlFromHandle(h)

    nav = find(root, "模型库", "ListItemControl", depth=8)
    chk("1 侧栏找到「模型库」", nav is not None)
    if not nav:
        return report()
    click_ctrl(nav)
    time.sleep(1.5)

    # 先切「本地」：在线镜像模式下导入走的是下载流程，不会弹文件选择框
    local = None
    for _d, ch in all_nodes(root, 12):
        try:
            if ch.ControlTypeName == "CheckBoxControl" and (ch.Name or "").strip() == "本地":
                local = ch
                break
        except Exception:
            pass
    if local:
        click_ctrl(local, dx=8)
        time.sleep(1.2)

    chip = None
    for _d, ch in all_nodes(root, 12):
        try:
            if ch.ControlTypeName == "CheckBoxControl" and (ch.Name or "").strip() == "checkpoints":
                chip = ch
                break
        except Exception:
            pass
    chk("2 找到 checkpoints 分类", chip is not None)
    if chip:
        click_ctrl(chip, dx=8)          # 点左侧方块，点文字有时不生效
        time.sleep(1.5)

    n1 = row_count(root)
    chk("3 选中分类后表格有文件行", n1 >= 1, f"行数={n1}")

    os.remove(TEST_PATH)
    chk("4 磁盘文件已删除", not os.path.exists(TEST_PATH))

    btn = find(root, "刷新", "ButtonControl")
    chk("5 找到「🔄 刷新」", btn is not None)
    if btn:
        click_ctrl(btn)
        time.sleep(1.8)
    n2 = row_count(root)
    chk("6 刷新后表格行数归零", n2 == 0, f"行数={n2}")

    imp = find(root, "导入", "ButtonControl")
    chk("7 找到「📥 导入」", imp is not None)
    imported = False
    if imp:
        click_ctrl(imp)
        dlg = find_dialog(10)
        chk("8 弹出文件选择对话框", dlg is not None)
        if dlg:
            edit = find(dlg, "文件名", depth=10) or find(dlg, "File name", depth=10)
            if edit:
                edit.Click()
                time.sleep(0.3)
                auto.SendKeys("{Ctrl}a", waitTime=0.05)
                auto.SendKeys(TEST_PATH, waitTime=0.05)
                time.sleep(0.5)
                auto.SendKeys("{Enter}", waitTime=0.3)
                time.sleep(2.5)
            else:
                auto.SendKeys(TEST_PATH, waitTime=0.05)
                auto.SendKeys("{Enter}", waitTime=0.3)
                time.sleep(2.5)
            # 「导入方式」对话框：选复制
            for name in ("📋 复制", "复制"):
                b = find(auto.GetRootControl(), name, "ButtonControl", depth=8)
                if b:
                    click_ctrl(b)
                    break
            time.sleep(3)
            imported = os.path.exists(TEST_PATH)
    chk("9 导入后文件回到磁盘", imported, TEST_PATH)

    btn = find(root, "刷新", "ButtonControl")
    if btn:
        click_ctrl(btn)
        time.sleep(2)
    n3 = row_count(root)
    chk("10 刷新后表格行数回到 ≥1（闭环）", n3 >= 1, f"行数={n3}")

    if not ARGS.keep:
        for p in (TEST_PATH, SRC_PATH):
            try:
                os.remove(p)
            except OSError:
                pass
    subprocess.run(["taskkill", "/IM", "ComfyHub.exe", "/F"], capture_output=True)
    return report()


def report():
    fails = [r for r in RESULTS if not r[1]]
    print("=" * 52)
    print(f"模型库删/导入闭环：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
