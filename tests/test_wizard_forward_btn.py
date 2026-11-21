# -*- coding: utf-8 -*-
"""向导前进键状态机（2026-11-21 客户反馈"Finish 安不下去"）

设计要求：扫描/验证没结束 → 前进键显示「准备中…/验证中…」且禁用；
结束后第 1 页只允许出现一个可点的前进键。Qt6.12 会把 FinishButton
顶出来与「下一步」并排（禁用态在 QSS 下几乎同色，用户以为能点）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    print("%s  %s %s" % (tag, name, extra))
    if not cond:
        FAILS.append(name)


def pump(app, sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()


def main():
    from PySide6.QtWidgets import QApplication, QWizard
    app = QApplication.instance() or QApplication([])

    from app.ui.setup_wizard import SetupWizard

    def fwd(wiz):
        return wiz.button(QWizard.NextButton)

    def fin(wiz):
        return wiz.button(QWizard.FinishButton)

    # ---------- A. 静态：打开即探测（本机无实例 → fresh） ----------
    print("== A. 真实向导打开 ==")
    wiz = SetupWizard(None)
    wiz.show()
    pump(app, 2.0)                       # 自动探测 + 扫描 + 兜底 fresh
    check("第 1 页 Finish 已隐藏", not fin(wiz).isVisibleTo(wiz))
    check("前进键已恢复可点", fwd(wiz).isEnabled(),
          "text=%r" % fwd(wiz).text())
    check("前进键文案已离开准备态",
          fwd(wiz).text() in ("下一步 >", "完成"), repr(fwd(wiz).text()))

    # ---------- B. 扫描中：前进键 = 准备中… 且禁用 ----------
    print("== B. 扫描进行中 ==")
    import tempfile
    tmp = tempfile.mkdtemp(prefix="wizprobe_")
    # 造几个空子目录拖慢扫描，保证能采到"准备中…"中间态
    for i in range(30):
        os.makedirs(os.path.join(tmp, "pad_%02d" % i), exist_ok=True)
    wiz.ed_dir.setText(tmp)
    wiz._start_scan(tmp)
    check("扫描中前进键禁用", not fwd(wiz).isEnabled())
    check("扫描中前进键 = 准备中…", fwd(wiz).text() == "准备中…",
          repr(fwd(wiz).text()))
    check("扫描中 Finish 仍隐藏", not fin(wiz).isVisibleTo(wiz))
    pump(app, 3.0)                       # 等扫描收尾
    check("扫描完成后前进键可点", fwd(wiz).isEnabled(),
          "text=%r" % fwd(wiz).text())
    check("扫描完成后 Finish 仍隐藏", not fin(wiz).isVisibleTo(wiz))

    # ---------- C. 验证中 → 通过：前进键 验证中… → 完成 ----------
    print("== C. 验证状态机 ==")
    from app import bootstrap
    fake_comfy = os.path.join(tmp, "MyComfy")
    os.makedirs(fake_comfy, exist_ok=True)
    open(os.path.join(fake_comfy, "main.py"), "w").write("# fake\n")
    real_verify = bootstrap.verify_comfyui_runnable
    # 真实签名返回 2 元组（_try_all 里 ok, info = ...），桩必须同构，
    # 否则解包异常会走"验证执行失败"分支而不是通过分支
    bootstrap.verify_comfyui_runnable = lambda py, cp: (True, "可运行(fake)")
    try:
        # 直接驱动 _on_scan_done：候选=[fake_comfy] → existing 模式 → _begin_verify
        wiz._scan_seq = getattr(wiz, "_scan_seq", 0) + 1
        wiz._on_scan_done(tmp, [fake_comfy])
        seen_verify = False
        for _ in range(40):              # 轮询 4 秒，抓"验证中…"中间态
            pump(app, 0.1)
            if not fwd(wiz).isEnabled() and fwd(wiz).text() == "验证中…":
                seen_verify = True
                break
            if fwd(wiz).text() == "完成":
                break                    # 太快没抓到也算过（下面单独断言终态）
        check("验证期间出现「验证中…」禁用态", seen_verify
              or fwd(wiz).text() == "完成",
              "text=%r" % fwd(wiz).text())
        pump(app, 3.0)                   # 等验证 worker 收尾
        check("验证通过后前进键 = 完成", fwd(wiz).text() == "完成",
              repr(fwd(wiz).text()))
        check("验证通过后前进键可点", fwd(wiz).isEnabled())
        check("验证通过后 Finish 仍隐藏（只有这一个「完成」）",
              not fin(wiz).isVisibleTo(wiz))
    finally:
        bootstrap.verify_comfyui_runnable = real_verify

    try:
        wiz.close()
    except Exception:
        pass
    print()
    print("共 11 项，失败 %d" % len(FAILS))
    if FAILS:
        for f in FAILS:
            print("  失败:", f)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(1) if FAILS else sys.exit(0)
