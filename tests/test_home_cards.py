# -*- coding: utf-8 -*-
"""工作台状态卡回归（2026-11-10 用户需求）。

需求：①显卡卡下面显示显存（占用），不再是"驱动→推荐torch"文案
     ②磁盘卡显示当前实例所在盘的空间（此前硬编码 F:）

断言：
  A. gpu_vram_usage() 真机返回 used/total/percent 且二次调用命中缓存
  B. 显卡卡副标题=显存占用（含"显存"/"占用"、数值与"/"），大字=纯型号，
     驱动/torch 推荐降级到 tooltip（信息不丢）
  C. 磁盘卡标题跟当前实例根目录盘符走（monkeypatch 实例到 C 盘应变「C: 盘空间」），
     切换实例后 on_instance_changed 也会重刷磁盘卡
  D. 显存占用变化会进 _refresh_live（动态刷新链路）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_home_cards.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.03)


def main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

    app = QApplication(sys.argv)
    from app import sysinfo
    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    pump(app, 2.5)

    page = win.stack.widget(0)      # 工作台（walk_all_pages 页序）
    assert page is not None and hasattr(page, "card_gpu"), "工作台页装配异常"

    # ---- A. gpu_vram_usage ----
    print("== A. 显存占用接口 ==")
    v1 = sysinfo.gpu_vram_usage(max_age=3.0)
    check("真机返回 used/total/percent",
          v1 and all(k in v1 for k in ("used_mb", "total_mb", "percent"))
          and v1["total_mb"] > 0, str(v1))
    v2 = sysinfo.gpu_vram_usage(max_age=3.0)
    check("二次调用命中缓存（同一对象）", v1 is v2)

    # ---- B. 显卡卡 ----
    print("== B. 显卡卡：下面显示显存 ==")
    page._refresh()
    pump(app, 0.3)
    sub = page.card_gpu._sub.text()
    val = page.card_gpu._value.text()
    tip = page.card_gpu.toolTip()
    check("副标题含「显存」", "显存" in sub, sub)
    check("副标题含显存数值与 /", "/" in sub and "GB" in sub, sub)
    check("副标题含「占用」且带%", ("占用" in sub and "%" in sub) or "显存未知" in sub, sub)
    check("大字为纯型号（显存已下移，不含 GB）", "GB" not in val and val.strip() != "", val)
    check("驱动/torch 推荐降级到 tooltip", "torch" in tip and "驱动" in tip, tip)
    if v1:
        check("进度条=显存占用率", abs(page.card_gpu._bar.value() - round(v1["percent"])) <= 2
              and page.card_gpu._bar.isVisibleTo(page.card_gpu))

    # ---- C. 磁盘卡跟实例走 ----
    print("== C. 磁盘卡：实例所在盘 ==")
    t0 = page.card_disk._title.text()
    check("标题为「盘符 + 盘空间」", t0[:2] in ("C:", "D:", "E:", "F:", "G:") and "盘空间" in t0, t0)
    orig_ci = win.current_inst
    try:
        win.current_inst = lambda: {"id": "smoke", "name": "smoke", "comfyui_path": "C:\\"}
        page._refresh_disk_card()
        t1 = page.card_disk._title.text()
        check("实例换到 C: 后标题变 C: 盘空间", t1.startswith("C:") and "盘空间" in t1, t1)
        win.current_inst = lambda: {"id": "smoke2", "name": "smoke2", "comfyui_path": "E:\\comfy"}
        page.on_instance_changed()
        pump(app, 0.2)
        t2 = page.card_disk._title.text()
        check("on_instance_changed 重刷磁盘卡（E:）", t2.startswith("E:"), t2)
    finally:
        win.current_inst = orig_ci

    # ---- D. live 刷新链路带显存 ----
    print("== D. _refresh_live 刷显存 ==")
    page._refresh_gpu_card = lambda *a, **k: main.__dict__.__setitem__("live_gpu", True)
    main.__dict__["live_gpu"] = False
    page._refresh_live()
    check("_refresh_live 调用了显卡卡刷新", main.__dict__.get("live_gpu") is True)

    win.close()
    print(f"\n{PASS} ok, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
