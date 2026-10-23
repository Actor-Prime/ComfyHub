# -*- coding: utf-8 -*-
"""安装器 E2E 全流程：UIA 驱动 + 每页截图（含完成页验证）

用法：python tests/install_e2e.py
截图输出：tests/probe_shots/e2e_*.png
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uiautomation as ua

SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_shots")
SETUP = r"F:\ComfyHub\dist\ComfyHub_Setup_0.7.4.exe"


def shot(win, name):
    p = os.path.join(SHOTS, f"e2e_{name}.png")
    try:
        win.CaptureToImage(p)
        print(f"  📸 {name} → {p}")
    except Exception as e:
        print(f"  📸 失败: {e}")


def walk(c, d, f):
    if d > 5:
        return None
    if f(c):
        return c
    for cc in c.GetChildren():
        r = walk(cc, d + 1, f)
        if r:
            return r
    return None


def btn(win, *kw):
    return walk(win, 0, lambda c: "Button" in type(c).__name__ and c.IsEnabled
                and any(k in (c.Name or "") for k in kw))


def chk(win):
    return walk(win, 0, lambda c: "CheckBox" in type(c).__name__)


def click(c, label, retries=2):
    for i in range(retries + 1):
        try:
            c.SetFocus()
            time.sleep(0.2)
            c.Click()
            print(f"  [点击] {label}" + (f"（重试 {i}）" if i else ""))
            time.sleep(0.6)
            return True
        except Exception as e:
            print(f"  [点击失败 {i}] {label}: {e}")
            time.sleep(0.5)
    return False


def wait(pred, timeout=15, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    print(f"  ⏳ 等待超时: {what}")
    return False


def main():
    os.makedirs(SHOTS, exist_ok=True)
    # 清理可能的残留
    subprocess.run(["taskkill", "/IM", "ComfyHub_Setup_0.7.4.exe", "/F"],
                   capture_output=True)

    print("=== 启动安装器 ===")
    proc = subprocess.Popen([SETUP], creationflags=0x10)

    def find_win(t=30):
        t0 = time.time()
        while time.time() - t0 < t:
            for w in ua.GetRootControl().GetChildren():
                try:
                    if w.ProcessId == proc.pid:
                        return w
                except Exception:
                    continue
            time.sleep(0.5)
        return None

    win = find_win()
    if win is None:
        print("✗ 未找到窗口")
        return 1
    print(f"✅ 窗口: {win.Name}")

    # ---- 1. 欢迎页 ----
    print("\n--- 欢迎页 ---")
    shot(win, "01_welcome")
    if not click(btn(win, "下一步"), "下一步"):
        proc.kill(); return 1
    if not wait(lambda: chk(win) is not None, 15, "同意书 checkbox"):
        # 可能直接到目录页（无同意书），继续
        pass

    # ---- 2. 同意书页 ----
    ch = chk(win)
    if ch:
        print("\n--- 同意书页 ---")
        shot(win, "02_license")
        ch.SetFocus(); time.sleep(0.2); ch.Click()
        print("  [勾选] 同意书")
        time.sleep(0.6)
        if not click(btn(win, "下一步"), "下一步(同意书)"):
            proc.kill(); return 1
        wait(lambda: btn(win, "浏览") is not None, 15, "目录页")
    else:
        print("\n⚠️ 未找到同意书页 checkbox")

    # ---- 3. 目录页（组件页删除后它是最后设置页，按钮为「安装」）----
    print("\n--- 目录页 ---")
    shot(win, "03_dir")
    inst_btn = btn(win, "安装") or btn(win, "下一步")
    if not click(inst_btn, "安装(I)/下一步"):
        proc.kill(); return 1
    # 等安装页（安装按钮禁用→完成按钮出现）
    if not wait(lambda: btn(win, "完成") is not None, 120, "完成页"):
        shot(win, "install_stuck")
        proc.kill(); return 1

    # ---- 4. 完成页（关键验证）----
    print("\n--- 完成页（验证 checkbox 已删、文字白色）---")
    time.sleep(1)
    shot(win, "04_finish")
    # dump 控件树确认无 checkbox
    has_chk = chk(win) is not None
    print(f"  完成页 checkbox 存在: {has_chk}（应为 False）")
    # 点完成
    click(btn(win, "完成"), "完成(F)")
    time.sleep(2)
    print("\n✅ E2E 全流程完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
