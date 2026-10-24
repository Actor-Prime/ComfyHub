# -*- coding: utf-8 -*-
"""安装器 E2E 全流程工具（轮询式，防时序问题）

用法：
    python tests/install_e2e.py                # 全流程 + 逐步截图
    python tests/install_e2e.py --dump-finish  # 跑到完成页 dump 控件树/像素位置

截图输出：tests/probe_shots/
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import uiautomation as ua

SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_shots")
SETUP = r"F:\ComfyHub\dist\ComfyHub_Setup_0.7.4.exe"


# -------------------------------------------------- 基础
def shot(win, name):
    p = os.path.join(SHOTS, f"{name}.png")
    try:
        win.CaptureToImage(p)
        print(f"  📸 {name}")
    except Exception as e:
        print(f"  📸 失败: {e}")


def walk(c, d, f, maxd=5):
    if d > maxd:
        return None
    try:
        if f(c):
            return c
    except Exception:
        return None
    for cc in c.GetChildren():
        r = walk(cc, d + 1, f, maxd)
        if r:
            return r
    return None


def find_win(pid, t=30):
    t0 = time.time()
    while time.time() - t0 < t:
        for w in ua.GetRootControl().GetChildren():
            try:
                if w.ProcessId == pid:
                    return w
            except Exception:
                continue
        time.sleep(0.5)
    return None


def click(c, label):
    try:
        c.SetFocus()
        time.sleep(0.2)
        c.Click()
        print(f"  [点击] {label}")
        time.sleep(1.0)          # 页面切换缓冲
        return True
    except Exception as e:
        print(f"  [点击失败] {label}: {e}")
        return False


def wait_for(win, pred, timeout=20, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = pred(win)
            if r:
                return r
        except Exception:
            pass
        time.sleep(0.5)
    print(f"  ⏳ 超时: {what}")
    return None


# -------------------------------------------------- 控件查找
def btn(win, *kw):
    return walk(win, 0, lambda c: "Button" in type(c).__name__ and c.IsEnabled
                and any(k in (c.Name or "") for k in kw))


def chkbox(win):
    return walk(win, 0, lambda c: "CheckBox" in type(c).__name__)


def by_id(win, cid):
    return walk(win, 0, lambda c: getattr(c, "AutomationId", "") == str(cid))


def rect(win, c):
    wr = win.BoundingRectangle
    r = c.BoundingRectangle
    return dict(left=r.left - wr.left, top=r.top - wr.top, w=r.width, h=r.height)


# -------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-finish", action="store_true")
    args = ap.parse_args()

    os.makedirs(SHOTS, exist_ok=True)
    subprocess.run(["taskkill", "/IM", "ComfyHub_Setup_0.7.4.exe", "/F"], capture_output=True)
    time.sleep(1)

    print("=== 启动安装器 ===")
    proc = subprocess.Popen([SETUP], creationflags=0x10)
    win = find_win(proc.pid)
    if win is None:
        print("✗ 未找到窗口")
        return 1
    win.SetTopmost(True)
    print(f"✅ 窗口置顶: {win.Name}")

    # 1. 欢迎页
    print("\n--- 欢迎页 ---")
    shot(win, "01_welcome")
    click(btn(win, "下一步"), "下一步")

    # 2. 同意书（轮询 checkbox）
    ch = wait_for(win, chkbox, 20, "同意书 checkbox")
    if ch:
        print("\n--- 同意书页 ---")
        shot(win, "02_license")
        ch.SetFocus(); time.sleep(0.2); ch.Click()
        print("  [勾选] 同意书")
        time.sleep(0.6)
        click(wait_for(win, lambda w: btn(w, "下一步"), 15, "下一步"), "下一步")
    else:
        print("⚠️ 无同意书页")

    # 3. 目录页（最后设置页 → 按钮「安装」）
    ib = wait_for(win, lambda w: btn(w, "安装"), 20, "安装(I)")
    if ib is None:
        print("✗ 未到达目录页")
        proc.kill(); return 1
    print("\n--- 目录页 ---")
    shot(win, "03_dir")
    click(ib, "安装(I)")

    # 4. 等完成页
    if wait_for(win, lambda w: btn(w, "完成"), 120, "完成页") is None:
        shot(win, "stuck")
        proc.kill(); return 1
    time.sleep(1)

    print("\n--- 完成页 ---")
    shot(win, "04_finish")
    print(f"  checkbox 残留: {chkbox(win) is not None}（应 False）")
    # 完成页文字
    texts = []
    def collect(c):
        try:
            if "Text" in type(c).__name__ and (c.Name or "").strip():
                texts.append((c.Name or "").strip()[:70])
            for cc in c.GetChildren():
                collect(cc)
        except Exception:
            pass
    collect(win)
    for t in texts[:4]:
        print(f"   · {t}")
    # 像素位置
    if args.dump_finish:
        print("  像素位置:")
        for cid, name in ((1201, "标题"), (1202, "正文"), (1203, "checkbox")):
            c = by_id(win, cid)
            if c:
                r = rect(win, c)
                print(f"    {name}({cid}): {r}")

    # 点完成（取消可能的勾选，避免启动）
    for c in [chkbox(win)]:
        if c and c.IsEnabled:
            try:
                c.Click(); print("  [取消勾选] 立即运行")
            except Exception:
                pass
    click(wait_for(win, lambda w: btn(w, "完成"), 10, "完成"), "完成(F)")
    print("\n✅ E2E 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
