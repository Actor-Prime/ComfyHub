# -*- coding: utf-8 -*-
"""插件市场热度数据回归（2026-11-10 下载量列空窗事故）。

事故：12h 缓存过期后旧数据被丢弃、后台全量重拉 65 页，网络不稳时下载量列
长时间全显"—"。修复 = stale-while-revalidate（旧缓存先顶上显示，后台静默刷新）
         + 连续 8 页拉取失败熔断（防 65 页 × 25s 干等）。

断言：
  A. offline_only：只读缓存不发网络，过期缓存照样返回旧数据
  B. 新鲜缓存正常路径秒回、不发网络
  C. 熔断：连续失败 8 页提前收工，快速返回已取部分
  D. UI：缓存过期时 _ensure_stats 先把旧热度顶上（_downloads 已挂、状态栏提示）
用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_plugin_stats.py
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

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


class FakeResp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode("utf-8")

    def read(self, n=-1):
        if n is None or n < 0:
            b, self._b = self._b, b""
            return b
        b, self._b = self._b[:n], self._b[n:]
        return b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_cache(path, fetched_at, stats):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": fetched_at, "stats": stats}, f)


def main():
    from app import plugin_store as store

    tmp = tempfile.mkdtemp(prefix="statstest_")
    cache = os.path.join(tmp, "registry_stats.json")
    orig_path, orig_uo = store.stats_cache_path, urllib.request.urlopen
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise OSError("网络被禁（测试桩）")

    try:
        store.stats_cache_path = lambda: cache

        # ---- A. offline_only 只读缓存 ----
        print("== A. offline_only 只读缓存 ==")
        old_stats = {"foo/bar": {"downloads": 7777, "stars": 5, "rating": 0, "name": "T"}}
        make_cache(cache, fetched_at=time.time() - 999 * 3600, stats=old_stats)  # 过期
        urllib.request.urlopen = boom
        calls["n"] = 0
        got = store.load_registry_stats(offline_only=True)
        check("过期缓存照样返回旧数据", got.get("foo/bar", {}).get("downloads") == 7777, str(got))
        check("未发任何网络请求", calls["n"] == 0, f"calls={calls['n']}")
        got2 = store.load_registry_stats(offline_only=True)
        check("无缓存文件时返回 {}（不炸）",
              (os.remove(cache), store.load_registry_stats(offline_only=True))[-1] == {})

        # ---- B. 新鲜缓存秒回 ----
        print("== B. 新鲜缓存秒回 ==")
        make_cache(cache, fetched_at=time.time() - 60, stats=old_stats)
        calls["n"] = 0
        got = store.load_registry_stats()
        check("新鲜缓存命中", got.get("foo/bar", {}).get("downloads") == 7777)
        check("未发任何网络请求", calls["n"] == 0)

        # ---- C. 连续失败熔断 ----
        print("== C. 连续失败熔断 ==")
        os.remove(cache)

        def flaky(req, timeout=None, context=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp({"total": 3000, "totalPages": 30,
                                 "nodes": [{"repository": "https://github.com/a/first",
                                            "downloads": 100, "github_stars": 1,
                                            "rating": 0, "name": "First"}]})
            raise OSError("page down（测试桩）")

        urllib.request.urlopen = flaky
        calls["n"] = 0
        logs = []
        t0 = time.time()
        got = store.load_registry_stats(log=logs.append)
        dt = time.time() - t0
        check("快速返回（<5s，无熔断最坏 29 页 × 25s）", dt < 5, f"{dt:.1f}s")
        check("已取部分保留（首页数据在）", got.get("a/first", {}).get("downloads") == 100, str(got))
        check("日志有提前结束提示",
              any("提前结束" in l for l in logs), " | ".join(logs))
        check("熔断后不再继续请求（1 次成功 + 8 次失败）", calls["n"] == 9, f"calls={calls['n']}")

        # ---- D. UI stale-while-revalidate ----
        print("== D. UI 旧缓存先顶上 ==")
        from PySide6.QtWidgets import QApplication, QMessageBox
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        app = QApplication.instance() or QApplication(sys.argv)
        from app.ui.main_window import MainWindow
        from app.ui.pages.plugins_page import StatsWorker

        win = MainWindow()
        win.show()
        t = time.time()
        while time.time() - t < 2.5:
            app.processEvents()
            time.sleep(0.03)
        page = win.stack.widget(2)      # 插件管理页
        assert page is not None and hasattr(page, "_ensure_stats"), "插件页装配异常"

        # 桩：禁真网络 worker + 过期假缓存 + 假 catalog
        orig_run = StatsWorker.run
        StatsWorker.run = lambda self: None
        make_cache(cache, fetched_at=time.time() - 999 * 3600, stats=old_stats)
        try:
            page._stats = {}
            page._catalog = [{"title": "T", "reference": "https://github.com/foo/bar.git",
                              "author": "foo", "description": "d"}]
            page._ensure_stats()
            check("旧热度已顶上（_stats 非空）", bool(page._stats), str(page._stats))
            check("catalog 已挂 _downloads=7777",
                  page._catalog[0].get("_downloads") == 7777, str(page._catalog[0]))
            check("状态栏提示缓存+后台刷新",
                  "缓存" in page.lb_market_stat.text(), page.lb_market_stat.text())
            # 进度文案语义：有数据显示时说"更新"
            page._on_stats_progress(22, 65)
            check("进度文案为「正在更新」", "更新" in page.lb_market_stat.text(),
                  page.lb_market_stat.text())
        finally:
            StatsWorker.run = orig_run
        win.close()
    finally:
        store.stats_cache_path, urllib.request.urlopen = orig_path, orig_uo

    print(f"\n{PASS} ok, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
