# -*- coding: utf-8 -*-
"""模型库 v0.8.06 重构回归测试（12 项）

覆盖：本地搜索语义（全部视图搜文件名）/ 空输入反馈 / 中文换词 /
CivitAI 自动降级 / 范围切换 / chips / 卡片生成与「已装」badge / 快捷词联动。

用法：.venv_build/Scripts/python.exe tests/test_models_page.py
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QPushButton

app = QApplication([])

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.3):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


def wait_idle(page, timeout=6):
    """等市场面板搜索线程收尾，防上一个任务抢跑断言"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        w = page.market._search_worker
        if w is None or not w.isRunning():
            return
        time.sleep(0.02)


class FakeMain:
    def __init__(self, root):
        self.root = root

    def current_inst(self):
        return {"name": "测试实例", "comfyui_path": self.root}


def main():
    from app import model_store as ms
    from app.ui.pages import model_market_widget as mmw
    from app.ui.pages.models_page import ModelsPage

    # ---------- 环境：假实例（QSettings 隔离，防上次运行残留的范围记忆污染）
    from PySide6.QtCore import QSettings
    QSettings("LeiMengStudio", "ComfyHub").setValue("model_scope", "local")
    root = tempfile.mkdtemp(prefix="ch_models_")
    for sub in ("checkpoints", "loras"):
        os.makedirs(os.path.join(root, "models", sub), exist_ok=True)
    open(os.path.join(root, "models", "checkpoints", "sdxl-base-1.0.safetensors"), "wb").write(b"0")
    open(os.path.join(root, "models", "checkpoints", "realistic-vision.safetensors"), "wb").write(b"0")
    open(os.path.join(root, "models", "loras", "style-lora.safetensors"), "wb").write(b"0")

    page = ModelsPage({}, FakeMain(root))
    page.show()
    pump(0.2)

    # ---------- 1. translate_keyword
    en, notes = ms.translate_keyword("写实少女")
    check("1 translate_keyword 写实少女→realistic girl", en == "realistic girl", en)
    check("1b notes 两条换词记录", len(notes) == 2, str(notes))

    # ---------- 2. 全部视图 + 文件名搜索（bug#1 核心）
    page.ed_search.setText("sdxl")
    pump(0.2)
    rows = page.tb.rowCount()
    cols = page.tb.columnCount()
    found = any(page.tb.item(r, 0) and "sdxl" in page.tb.item(r, 0).text().lower()
                for r in range(rows))
    check("2 全部视图搜文件名命中", cols == 4 and found, f"rows={rows} cols={cols}")

    # ---------- 3. 全部视图 + 类别名搜索
    page.ed_search.setText("loras")
    pump(0.2)
    found = any(page.tb.item(r, 1) and page.tb.item(r, 1).text() == "loras"
                for r in range(page.tb.rowCount()))
    check("3 全部视图类别名命中→列文件", found, f"rows={page.tb.rowCount()}")

    # ---------- 4. chips 类别过滤
    page.chip_group.button(2).setChecked(True)   # loras
    page.ed_search.setText("style")
    pump(0.2)
    ok4 = page.tb.rowCount() == 1 and "style-lora" in page.tb.item(0, 0).text()
    check("4 chips=loras 搜 style 命中", ok4, f"rows={page.tb.rowCount()}")
    page.ed_search.setText("zzz")
    pump(0.2)
    ok4b = page.tb.rowCount() == 1 and "暂无文件" in page.tb.item(0, 0).text()
    check("4b 无命中显示占位提示", ok4b)

    # ---------- 5. 全部视图统计
    page.ed_search.setText("")                 # 先清词，防 chips 切换触发在线自动搜索
    page.chip_group.button(0).setChecked(True)
    pump(0.2)
    ok5 = page.tb.columnCount() == 3 and page.tb.rowCount() >= 2
    check("5 全部视图无搜索→分类统计", ok5, f"rows={page.tb.rowCount()}")

    # ---------- 6. 范围切换
    page.scope_group.button(1).setChecked(True)   # HF 镜像
    pump(0.2)
    ok6 = (page.stack.currentIndex() == 1
           and "HuggingFace" in page.ed_search.placeholderText())
    check("6 切 HF 镜像→在线面板+占位符", ok6, page.ed_search.placeholderText())
    page.scope_group.button(0).setChecked(True)
    pump(0.2)
    check("6b 切回本地→本地表", page.stack.currentIndex() == 0)

    # ---------- 7. 在线空输入反馈（bug#2）
    page.scope_group.button(1).setChecked(True)
    pump(0.1)
    page.ed_search.setText("")
    page._do_search()
    pump(0.1)
    st = page.market.lb_stat.text()
    ok7 = "请先输入关键词" in st and "#e8a33d" in page.market.dot.styleSheet()
    check("7 空输入→黄点提示", ok7, st)

    # ---------- 8. 中文换词走真实搜索链路（bug#3）
    wait_idle(page)
    got = {}
    real_search = ms.search_models

    def fake_search(kw, limit=30):
        got["kw"] = kw
        return [{"id": "t/realistic-girl", "name": "realistic-girl", "author": "t",
                 "downloads": 100, "likes": 5, "pipeline": "text-to-image"}]
    ms.search_models = fake_search
    try:
        page.ed_search.setText("写实少女")
        page._do_search()
        t0 = time.time()
        while time.time() - t0 < 5:
            app.processEvents()
            if got.get("kw"):
                break
            time.sleep(0.02)
        pump(0.3)
        check("8 中文→英文检索词生效", got.get("kw") == "realistic girl", str(got))
        ok8b = page.market.grid.count() == 1
        check("8b 结果渲染为卡片", ok8b, f"count={page.market.grid.count()}")
    finally:
        ms.search_models = real_search

    # ---------- 9. 0 结果提示含换词建议
    wait_idle(page)
    ms.search_models = lambda kw, limit=30: []
    try:
        page.ed_search.setText("xyzzy")
        page._do_search()
        pump(0.5)
        st = page.market.lb_stat.text()
        check("9 零结果→换词引导", "没有搜到结果" in st and "realistic" in st, st)
    finally:
        ms.search_models = real_search

    # ---------- 10. civitai 失败自动降级（bug#4）
    calls = {}
    real_civ = ms.search_civitai

    def fake_civ(kw, limit=20):
        calls["civ"] = kw
        raise OSError("SSL record layer failure")
    ms.search_civitai = fake_civ
    ms.search_models = fake_search
    try:
        wait_idle(page)
        page.ed_search.setText("vae")          # 先填词再切范围（切换即自动搜索）
        page.scope_group.button(2).setChecked(True)   # CivitAI
        t0 = time.time()
        while time.time() - t0 < 5:
            app.processEvents()
            if calls.get("hf"):
                break
            time.sleep(0.02)
        pump(0.3)
        st = page.market.lb_stat.text()
        # fake_search 把检索词写进 got（fallback 也走它）；civ 记录在 calls
        ok10 = calls.get("civ") == "vae" and got.get("kw") == "vae"
        ok10 = ok10 and ("hf-mirror" in st or "结果" in st)
        check("10 civitai 失败→自动转 hf-mirror", ok10, f"calls={calls} got={got} st={st}")
    finally:
        ms.search_civitai = real_civ
        ms.search_models = real_search

    # ---------- 11. 卡片「已装」badge 启发式
    ms.search_models = real_search
    page.market._fill_cards(
        [{"id": "x/sdxl-base-1.0", "name": "sdxl-base-1.0", "author": "x",
          "downloads": 1, "likes": 1, "pipeline": ""},
         {"id": "y/never-installed-zz", "name": "never-installed-zz", "author": "y",
          "downloads": 1, "likes": 1, "pipeline": ""}], "mirror")
    cards = [page.market.grid.itemAt(i).widget() for i in range(page.market.grid.count())]
    inst = {c.repo_id: c.installed for c in cards}
    check("11 已装 badge 判定",
          inst.get("x/sdxl-base-1.0") and not inst.get("y/never-installed-zz"), str(inst))

    # ---------- 12. 快捷词联动搜索框
    wait_idle(page)
    page.ed_search.setText("")                 # 清词防切换范围自动搜索
    page.scope_group.button(1).setChecked(True)
    pump(0.2)
    qbtn = None
    for b in page.market.empty_w.findChildren(QPushButton):
        if b.text() == "vae":
            qbtn = b
    got2 = {}
    ms.search_models = lambda kw, limit=30: (got2.setdefault("kw", kw), [])[1] or []
    try:
        if qbtn:
            qbtn.click()
            pump(0.5)
        check("12 快捷词→搜索框+触发", qbtn is not None and
              page.ed_search.text() == "vae" and got2.get("kw") == "vae",
              f"text={page.ed_search.text()} got={got2}")
    finally:
        ms.search_models = real_search

    # ---------- 13. 双击行跳转（2026-11-07 用户要求）
    wait_idle(page)
    page.ed_search.setText("")
    page.scope_group.button(0).setChecked(True)   # 回「本地」
    pump(0.2)
    page.chip_group.button(0).setChecked(True)    # 回「全部」→ 类别统计
    pump(0.3)
    row_ck = next((r for r in range(page.tb.rowCount())
                   if page.tb.item(r, 0) and page.tb.item(r, 0).text() == "checkpoints"), -1)
    check("13a 类别统计表里有 checkpoints 行",
          row_ck >= 0 and page._tb_mode == "stats", f"row={row_ck} mode={page._tb_mode}")
    page._on_row_dbl(row_ck, 0)
    pump(0.3)
    check("13b 双击类别行 → 下钻到该类别",
          page._cur_cat() == "checkpoints" and page._tb_mode == "files"
          and page.tb.horizontalHeaderItem(0).text() == "文件名",
          f"cat={page._cur_cat()} mode={page._tb_mode}")

    from app import instance as _inst_mod
    opened = []
    _real_open = _inst_mod.open_in_explorer
    _inst_mod.open_in_explorer = lambda p: opened.append(p)
    try:
        page._on_row_dbl(0, 0)
        pump(0.2)
    finally:
        _inst_mod.open_in_explorer = _real_open
    check("13c 双击文件行 → 资源管理器定位该文件",
          bool(opened) and opened[0].lower().endswith(".safetensors")
          and "checkpoints" in opened[0].replace("\\", "/"), str(opened))

    shutil.rmtree(root, ignore_errors=True)

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 ==")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
