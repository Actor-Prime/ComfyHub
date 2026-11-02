# -*- coding: utf-8 -*-
"""真机 UI 冒烟测试（不是无头测试！）

为什么要有它：offscreen 测试里 Qt 不派发任何 OS 层事件，**托盘、系统菜单、
任务栏这些路径完全测不到**。2026-11-01 用户报的「收进托盘后点退出没反应」
就是这类 bug —— 根因是 Qt 的 quitOnLastWindowClosed 只统计可见窗口，关一个
隐藏窗口不会触发应用退出，无头测试永远抓不到。

本脚本用真实鼠标点击（SetCursorPos + mouse_event）+ UIA 定位控件，
跑一遍托盘全链路：

    启动 → 点标题栏 X 收托盘 → 任务栏溢出区左键唤回
         → 二次启动（单实例守卫应唤回而不是开第二个） → 托盘右键退出

依赖（仅开发机需要，不打进产物）：
    python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ uiautomation pywin32

用法：
    python tests/ui_realtest.py                 # 测 dist\\ComfyHub\\ComfyHub.exe
    python tests/ui_realtest.py --exe 路径      # 测指定 exe
    python tests/ui_realtest.py --pause         # 每步暂停等你观察

⚠️ 运行期间鼠标会被脚本接管，别同时操作电脑。
⚠️ 前提：设置里「关闭主窗口时」= 最小化到托盘（默认 ask 也行，脚本点 X 后
   若弹出选择框会自动点「最小化到托盘」并勾「记住我的选择」）。
"""
import argparse
import os
import subprocess
import sys
import time

try:
    import uiautomation as auto
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:
    sys.exit("缺依赖：pip install -i https://mirrors.aliyun.com/pypi/simple/ uiautomation pywin32")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = os.path.join(ROOT, "dist", "ComfyHub", "ComfyHub.exe")

AP = argparse.ArgumentParser()
AP.add_argument("--exe", default=DEFAULT_EXE)
AP.add_argument("--pause", action="store_true", help="每步暂停，供人工观察")
ARGS = AP.parse_args()
EXE = os.path.abspath(ARGS.exe)
EXE_DIR = os.path.dirname(EXE)

R = []


def chk(name, cond, extra=""):
    R.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if ARGS.pause:
        input("       … 回车继续")


def pids():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ComfyHub.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, encoding="gbk", errors="ignore")
    out = set()
    for line in r.stdout.splitlines():
        p = [x.strip('"') for x in line.split('","')]
        if len(p) > 1 and p[0].lower() == "comfyhub.exe":
            out.add(int(p[1]))
    return out


def main_win(pid):
    res = []

    def cb(w, _):
        try:
            if (win32process.GetWindowThreadProcessId(w)[1] == pid
                    and win32gui.IsWindowVisible(w)
                    and "ComfyHub" in win32gui.GetWindowText(w)):
                res.append(w)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return res[0] if res else None


def click(x, y, right=False):
    """真实鼠标点击（不是 PostMessage，走完整输入链路）"""
    win32api.SetCursorPos((x, y))
    time.sleep(0.25)
    d = win32con.MOUSEEVENTF_RIGHTDOWN if right else win32con.MOUSEEVENTF_LEFTDOWN
    u = win32con.MOUSEEVENTF_RIGHTUP if right else win32con.MOUSEEVENTF_LEFTUP
    win32api.mouse_event(d, 0, 0, 0, 0)
    time.sleep(0.12)
    win32api.mouse_event(u, 0, 0, 0, 0)


def launch():
    # 注意：explorer.exe 传绝对路径会静默失败，必须先 cd 到所在目录再传文件名；
    # 也不能直接 Popen（会被工具进程的 Job 连坐杀掉）
    subprocess.run(["explorer.exe", os.path.basename(EXE)], cwd=EXE_DIR)
    for _ in range(40):
        time.sleep(0.5)
        ps = pids()
        if ps:
            w = main_win(next(iter(ps)))
            if w:
                return w
    return None


def overflow_open():
    panels = []

    def cb(w, _):
        if win32gui.IsWindowVisible(w) and "TopLevelWindowForOverflow" in win32gui.GetClassName(w):
            panels.append(w)
        return True

    win32gui.EnumWindows(cb, None)
    return panels


def tray_icon():
    """返回 (控件, (x, y))；托盘图标常被折叠在溢出面板里，要先点「显示隐藏的图标」"""
    panels = overflow_open()
    if not panels:
        tb = auto.ControlFromHandle(win32gui.FindWindow("Shell_TrayWnd", None))
        holder = {}

        def find(node, depth=0):
            if depth > 6 or "c" in holder:
                return
            for ch in node.GetChildren():
                if "显示隐藏的图标" in (ch.Name or ""):
                    holder["c"] = ch
                    return
                find(ch, depth + 1)

        find(tb)
        if "c" not in holder:
            return None, None
        rr = holder["c"].BoundingRectangle
        click((rr.left + rr.right) // 2, (rr.top + rr.bottom) // 2)
        time.sleep(1.2)
        panels = overflow_open()
    for w in panels:
        stack = [auto.ControlFromHandle(w)]
        while stack:
            n = stack.pop()
            for ch in n.GetChildren():
                try:
                    if "ComfyHub" in (ch.Name or ""):
                        rr = ch.BoundingRectangle
                        return ch, ((rr.left + rr.right) // 2, (rr.top + rr.bottom) // 2)
                except Exception:
                    pass
                stack.append(ch)
    return None, None


def tray_menu_rect(pid, contains="退出"):
    """托盘右键菜单是 Qt popup 顶层窗口，按文字找菜单项矩形"""
    box = []

    def cb(w, _):
        try:
            if (win32gui.IsWindowVisible(w)
                    and win32process.GetWindowThreadProcessId(w)[1] == pid
                    and "QWindowPopup" in win32gui.GetClassName(w)):
                for ch in auto.ControlFromHandle(w).GetChildren():
                    if contains in (ch.Name or ""):
                        box.append(ch.BoundingRectangle)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return box[0] if box else None


def main():
    if not os.path.isfile(EXE):
        sys.exit(f"找不到 exe：{EXE}（先跑 packaging\\build_exe.bat）")
    print(f"被测程序：{EXE}\n")

    subprocess.run(["taskkill", "/IM", "ComfyHub.exe", "/F"], capture_output=True)
    time.sleep(1.5)

    # 1. 启动
    w = launch()
    chk("1 启动：进程 + 主窗口就绪", bool(pids()) and w is not None, f"pid={pids()} hwnd={w}")
    if not w:
        return report()
    time.sleep(2)

    # 2. 点 X → 收托盘（若弹选择框，点「最小化到托盘」）
    l, t, r_, b = win32gui.GetWindowRect(w)
    click(r_ - 30, t + 16)
    time.sleep(1.5)
    pid = next(iter(pids()))
    if main_win(pid):                       # 还可见 → 说明弹了关闭询问框
        box = None
        for cand in pids():
            def cb(w2, _):
                global box
                try:
                    if not win32gui.IsWindowVisible(w2):
                        return True
                    if win32process.GetWindowThreadProcessId(w2)[1] != cand:
                        return True
                    for ch in auto.ControlFromHandle(w2).GetChildren():
                        if "最小化到托盘" in (ch.Name or ""):
                            box = ch.BoundingRectangle
                            return True
                except Exception:
                    pass
                return True
            win32gui.EnumWindows(cb, None)
        if box:
            click((box.left + box.right) // 2, (box.top + box.bottom) // 2)
            time.sleep(1.5)
    chk("2 点 X → 主窗口隐藏、进程存活", bool(pids()) and main_win(pid) is None)

    # 3. 托盘左键唤回
    icon, xy = tray_icon()
    chk("3 托盘图标可见", icon is not None, str(xy))
    if not icon:
        return report()
    click(xy[0], xy[1])
    time.sleep(1.5)
    chk("4 左键托盘 → 窗口恢复可见", main_win(pid) is not None)

    # 4. 二次启动 → 单实例守卫应唤回而非新开
    l2, t2, r2, b2 = win32gui.GetWindowRect(w)
    click(r2 - 30, t2 + 16)
    time.sleep(1.2)
    launch()
    time.sleep(3)
    ps = pids()
    chk("5 二次启动 → 仍只有 1 个进程", len(ps) == 1, f"pids={ps}")
    chk("6 二次启动 → 已隐藏窗口被唤回", main_win(pid) is not None)
    chk("7 单实例守卫未误杀主实例", pid in ps, f"{pid} in {ps}")

    # 5. 托盘右键退出（核心回归点）
    icon, xy = tray_icon()
    click(xy[0], xy[1], right=True)
    time.sleep(1.2)
    rect = tray_menu_rect(pid)
    chk("8 托盘菜单出现「退出 ComfyHub」", rect is not None)
    if rect:
        click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        gone = None
        for i in range(20):
            time.sleep(0.5)
            if not pids():
                gone = (i + 1) * 0.5
                break
        chk("9 托盘退出 → 进程真消失", not pids(), f"{gone}s")
    return report()


def report():
    fails = [n for n, ok in R if not ok]
    print("=" * 46)
    print(f"真机测试：{len(R) - len(fails)}/{len(R)} 通过")
    for n in fails:
        print("  FAIL", n)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
