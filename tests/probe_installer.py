# -*- coding: utf-8 -*-
"""Win32 安装器抓钩子演示：UIA 驱动 NSIS 安装向导（非 Qt 程序）

能力：
  · 枚举安装器窗口的控件树（按钮/文本/面板）
  · 截图各界面（CaptureToImage）
  · 自动点击按钮（下一步/取消/安装…），验证页面流转

用法：
    python tests/probe_installer.py --exe F:/ComfyHub/dist/ComfyHub_Setup_0.7.3.exe
"""
import argparse
import os
import subprocess
import sys
import time

import uiautomation as ua

OUT_DIR = r"F:\_installer_probe"


def find_installer_window(timeout=30):
    """等安装器主窗口出现（NSIS 用标准对话框类 #32770）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for w in ua.GetRootControl().GetChildren():
            name = w.Name or ""
            if "ComfyHub" in name and ("安装" in name or "Setup" in name):
                return w
        time.sleep(0.5)
    return None


def dump_tree(ctrl, depth=0, max_depth=5, lines=None):
    if lines is None:
        lines = []
    if depth > max_depth:
        return lines
    try:
        t = type(ctrl).__name__
        name = (ctrl.Name or "").strip()[:50]
        aid = ctrl.AutomationId or ""
        enabled = ctrl.IsEnabled
        lines.append(f"{'  ' * depth}{t} name={name!r} id={aid!r} enabled={enabled}")
        for c in ctrl.GetChildren():
            dump_tree(c, depth + 1, max_depth, lines)
    except Exception:
        pass
    return lines


def find_button(win, *keywords):
    """在窗口里找名称含关键字的按钮"""
    for ctrl in win.GetChildren():
        if isinstance(ctrl, ua.ButtonControl) or "Button" in type(ctrl).__name__:
            n = ctrl.Name or ""
            if any(k in n for k in keywords):
                return ctrl
    # 深度搜
    for ctrl in win.GetChildren():
        for c in getattr(ctrl, "GetChildren", lambda: [])():
            if "Button" in type(c).__name__:
                n = c.Name or ""
                if any(k in n for k in keywords):
                    return c
    return None


def click(ctrl, label=""):
    try:
        ctrl.SetFocus()
        time.sleep(0.2)
        ctrl.Click()
        print(f"    [点击] {label or (ctrl.Name or '')!r}")
        return True
    except Exception as e:
        print(f"    [点击失败] {label}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=r"F:\ComfyHub\dist\ComfyHub_Setup_0.7.3.exe")
    ap.add_argument("--keep-open", action="store_true", help="演示完不退出安装器")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"启动安装器: {a.exe}")
    proc = subprocess.Popen([a.exe], creationflags=0x00000010)   # CREATE_NEW_CONSOLE
    try:
        win = find_installer_window()
        if win is None:
            print("❌ 未找到安装器窗口（30 秒超时）")
            return 1
        print(f"\n✅ 找到窗口: {win.Name!r}  位置={win.BoundingRectangle}")

        print("\n===== 控件树 =====")
        for line in dump_tree(win):
            print(line)

        shot1 = os.path.join(OUT_DIR, "01_welcome.png")
        win.CaptureToImage(shot1)
        print(f"\n[截图] 欢迎页 → {shot1}")

        # 遍历「下一步」点击若干次，观察页面流转
        for step in range(2, 5):   # 只点 3 次下一步，不触发真正安装
            nxt = find_button(win, "下一步", "Next")
            if nxt is None:
                print(f"\n[第 {step} 步] 没有「下一步」按钮，页面如下：")
                for line in dump_tree(win, max_depth=3):
                    print(line)
                break
            if not click(nxt, f"下一步（第 {step-1} 次）"):
                break
            pump_until_idle(win, 2.0)
            shot = os.path.join(OUT_DIR, f"{step:02d}_step{step-1}.png")
            try:
                win.CaptureToImage(shot)
                print(f"    [截图] {shot}")
            except Exception as e:
                print(f"    [截图失败] {e}")
            time.sleep(0.3)

        # 找「取消」退出
        if not a.keep_open:
            cancel = find_button(win, "取消", "Cancel")
            if cancel:
                click(cancel, "取消")
                time.sleep(0.5)
                confirm = find_button(win, "是", "Yes")
                if confirm:
                    click(confirm, "确认退出")
            else:
                print("  [警告] 未找到取消按钮，将强制结束")
                proc.kill()
        print(f"\n完成。截图保存在 {OUT_DIR}")
        return 0
    finally:
        if proc.poll() is None and not a.keep_open:
            proc.kill()


def pump_until_idle(win, seconds):
    t = time.time()
    while time.time() - t < seconds:
        time.sleep(0.1)
        try:
            _ = win.Name
        except Exception:
            return


if __name__ == "__main__":
    sys.exit(main())
