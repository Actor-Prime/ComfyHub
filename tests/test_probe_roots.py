# -*- coding: utf-8 -*-
"""探测根通配化（2026-11-21 客户反馈）

背景：以前 _PROBE_ROOTS 写死了 `F:\\Comfy-Desktop\\ComfyUI-Installs\\...`，
只有 F 盘装官方 ComfyUI Desktop 的客户能被自动探测；官方桌面版默认结构
`<盘>:\\Comfy-Desktop\\ComfyUI-Installs\\ComfyUI\\ComfyUI` 装在其它盘
就漏掉。现改为按本机实际盘符通配生成。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    print("%s  %s %s" % (tag, name, extra))
    if not cond:
        FAILS.append(name)


def main():
    from app import config

    # ---------- A. 真实环境 ----------
    print("== A. 真实环境 ==")
    roots = config._probe_roots()
    check("返回非空列表", isinstance(roots, list) and len(roots) > 0)
    check("无重复", len(roots) == len(set(os.path.normcase(r) for r in roots)))
    check("始终包含桌面", any(r.endswith("ComfyUI") and "Desktop" in r for r in roots))
    # 每个真实存在的盘都应该生成 3 个候选
    import string
    real = [c for c in string.ascii_uppercase if os.path.isdir(c + ":\\")]
    expect = 1 + 3 * len(real)
    check("候选数 = 1 + 3×盘数 (%d 盘 → %d)" % (len(real), expect),
          len(roots) == expect, "实际 %d" % len(roots))
    for d in real[:3]:
        p = os.path.join(d + ":\\", "Comfy-Desktop", "ComfyUI-Installs",
                         "ComfyUI", "ComfyUI")
        check("%s 盘含官方桌面结构" % d, p in roots)

    # ---------- B. 伪造环境：逻辑正确性 ----------
    print("== B. 伪造环境（isdir 桩）==")
    real_isdir = os.path.isdir
    fake_drives = {"C:\\", "X:\\"}

    def fake_isdir(p):
        if p in fake_drives:
            return True
        if isinstance(p, str) and len(p) == 3 and p.endswith(":\\"):
            return False          # 其余盘符一律不存在
        return real_isdir(p)      # 非盘根路径走真实判断（如 expanduser 不受影响）

    os.path.isdir = fake_isdir
    try:
        roots2 = config._probe_roots()
    finally:
        os.path.isdir = real_isdir
    check("只按存在的盘生成",
          all(r[:3] in ("C:\\", "X:\\") or "Desktop" in r for r in roots2),
          "得到 %d 个" % len(roots2))
    xp = os.path.join("X:\\", "Comfy-Desktop", "ComfyUI-Installs",
                      "ComfyUI", "ComfyUI")
    check("X 盘（非常见盘）的官方桌面结构在列", xp in roots2, xp)
    check("X 盘裸 ComfyUI 在列", os.path.join("X:\\", "ComfyUI") in roots2)
    check("X 盘 AI/ComfyUI 在列", os.path.join("X:\\", "AI", "ComfyUI") in roots2)

    # ---------- C. default_instance 的预填逻辑 ----------
    print("== C. default_instance ==")
    inst = config.default_instance()
    check("返回含 comfyui_path 字段", "comfyui_path" in inst)
    # 预填的路径若非空，必须真的有 main.py（不许探测函数返回幻觉路径）
    if inst["comfyui_path"]:
        check("预填路径真实存在 main.py",
              os.path.isfile(os.path.join(inst["comfyui_path"], "main.py")))
    else:
        check("无命中时预填为空（由向导兜底）", True)

    print()
    print("共 %d 项，失败 %d" % (
        9 + (1 if inst["comfyui_path"] else 0), len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("  失败:", f)
        sys.exit(1)


if __name__ == "__main__":
    # 显式硬退出，避免 Qt/环境收尾阶段改写退出码
    try:
        main()
    finally:
        sys.exit(0) if not FAILS else None
