# -*- coding: utf-8 -*-
"""向导默认安装位置（2026-11-22 客户反馈：一添加就锁到 F 盘现成实例）

现场：客户机器 F 盘装了官方 ComfyUI Desktop，点「添加 ComfyUI」向导直接预填
`F:\\Comfy-Desktop\\ComfyUI-Installs\\ComfyUI\\ComfyUI` 并开始验证 —— 可他其实想
新建一个独立的。根因有两处：
  1. 第 1 页输入框默认值写死 `F:\\ComfyUI`
  2. 打开向导就全盘探测，命中即填入并自动验证

设计（2026-11-22 与用户确认）：
  · 默认位置恒为 <程序目录>\\ComfyUI 的全新安装
  · 探测到的现成实例只进候选下拉，默认仍选「新建新实例」
"""
import os
import sys
import tempfile
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
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from app.ui import setup_wizard as sw

    # ---------- A. 默认目录本身 ----------
    print("== A. 默认安装目录 ==")
    d = sw.default_install_dir()
    check("以 ComfyUI 结尾", os.path.basename(d.rstrip("\\/")) == "ComfyUI", d)
    check("不再写死 F:\\ComfyUI",
          os.path.normpath(d) != os.path.normpath("F:\\ComfyUI"), d)
    check("基于程序目录",
          os.path.normpath(os.path.dirname(d)) == os.path.normpath(sw._program_base()), d)

    # ---------- B. 探测到现成实例时不被锁定 ----------
    print("== B. 探测命中现成实例 ==")
    fake = tempfile.mkdtemp(prefix="wizprobe_hit_")
    inst = os.path.join(fake, "ComfyUI")
    os.makedirs(inst, exist_ok=True)
    open(os.path.join(inst, "main.py"), "w").close()

    import app.config as cfg
    _orig_probe = cfg._probe_roots
    cfg._probe_roots = lambda: [inst]
    try:
        wiz = sw.SetupWizard(None)
        wiz.show()
        pump(app, 2.5)          # 自动探测 + 合成扫描结果 + 进入 fresh 形态

        check("路径没被改成探测到的实例",
              os.path.normpath(wiz.ed_dir.text().strip()) != os.path.normpath(inst),
              wiz.ed_dir.text())
        check("路径 = 默认全新安装目录",
              os.path.normpath(wiz.ed_dir.text().strip()) == os.path.normpath(d),
              wiz.ed_dir.text())

        items = [wiz.cb_found.itemText(i) for i in range(wiz.cb_found.count())]
        check("下拉里有那个现成实例", any(inst in x for x in items), str(items))
        check("下拉第 0 项是「新建新实例」",
              bool(items) and "新建新实例" in items[0], str(items[:1]))
        check("默认选中「新建新实例」", wiz.cb_found.currentIndex() == 0,
              str(wiz.cb_found.currentIndex()))
        check("模式是全新安装（不是添加已有）",
              "添加已有实例" not in wiz.lb_mode.text(), repr(wiz.lb_mode.text()))

        # ---------- B2. Qt 自带按钮必须是中文 ----------
        # 客户 2026-11-22 截图底部出现「Back / Finish / Cancel」：
        # setFinalPage() 会把 QWizard 自带按钮文案重置回英文（Qt 6.12 实测）
        from PySide6.QtWidgets import QWizard
        check("Finish 文案是中文「完成」",
              wiz.button(QWizard.FinishButton).text() == "完成",
              repr(wiz.button(QWizard.FinishButton).text()))
        check("Back 文案是中文「< 上一步」",
              wiz.button(QWizard.BackButton).text() == "< 上一步",
              repr(wiz.button(QWizard.BackButton).text()))
        check("Cancel 文案是中文「取消」",
              wiz.button(QWizard.CancelButton).text() == "取消",
              repr(wiz.button(QWizard.CancelButton).text()))

        # ---------- C. 用户主动选现成实例 → 切回 existing ----------
        print("== C. 主动切换到现成实例 ==")
        wiz.cb_found.setCurrentIndex(1)
        pump(app, 2.5)
        check("路径切到实例", os.path.normpath(wiz.ed_dir.text().strip())
              == os.path.normpath(inst), wiz.ed_dir.text())
        # 切进 existing（会走 setFinalPage）后，按钮文案不能回落成英文
        check("existing 态 Finish 仍是「完成」",
              wiz.button(QWizard.FinishButton).text() == "完成",
              repr(wiz.button(QWizard.FinishButton).text()))
        check("existing 态 Back 仍是「< 上一步」",
              wiz.button(QWizard.BackButton).text() == "< 上一步",
              repr(wiz.button(QWizard.BackButton).text()))
        check("existing 态 Cancel 仍是「取消」",
              wiz.button(QWizard.CancelButton).text() == "取消",
              repr(wiz.button(QWizard.CancelButton).text()))

        # ---------- D. 模拟 Qt 把按钮重置回英文，封装要能补回中文 ----------
        print("== D. setFinalPage 后文案不回落 ==")
        wiz.setButtonText(QWizard.BackButton, "&Back")
        wiz.setButtonText(QWizard.FinishButton, "&Finish")
        wiz.setButtonText(QWizard.CancelButton, "Cancel")
        wiz._set_final_page(wiz.page(0), True)
        check("finalPage 后 Back 回中文",
              wiz.button(QWizard.BackButton).text() == "< 上一步",
              repr(wiz.button(QWizard.BackButton).text()))
        check("finalPage 后 Finish 回中文",
              wiz.button(QWizard.FinishButton).text() == "完成",
              repr(wiz.button(QWizard.FinishButton).text()))
        check("finalPage 后 Cancel 回中文",
              wiz.button(QWizard.CancelButton).text() == "取消",
              repr(wiz.button(QWizard.CancelButton).text()))
        wiz.close()
    finally:
        cfg._probe_roots = _orig_probe

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
