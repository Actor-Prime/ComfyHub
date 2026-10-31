# -*- coding: utf-8 -*-
"""模型市场搜索/下载增强回归（2026-10-31）

覆盖：
1.  SearchResult list 子类兼容（迭代/len/解包；纯 list 打桩无游标）
2.  HF Link header cursor 解析（URL 解码 / rel=prev / 多 rel）
3.  search_models cursor 进 URL + 返回 next_cursor（page 参数禁用）
4.  search_civitai cursor 进 URL + metadata.nextCursor
5.  download_file URL 直传（repo_id=http 直链原样下载）
6.  derive_mirror_queries（ControlNet 文件名 → 实义词组检索）
7.  MirrorFallbackWorker 多策略合并去重
8.  CivitaiDialog 文件行操作列（⬇ 下载回调 / 🔗 复制直链）
9.  _civitai_download → dl_center 管道（guess_category → models/<分类>）
10. 加载更多：追加渲染 + 去重 + cursor 传递 + 末页收尾 + 旧打桩兼容

用法：.venv_build/Scripts/python.exe tests/test_market_search.py
"""
import json
import os
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


def wait_worker(get_w, timeout=6):
    """等 worker 线程收尾 + 信号投递"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        w = get_w()
        if w is None or not w.isRunning():
            break
        time.sleep(0.02)
    pump(0.3)


class FakeMain:
    def __init__(self, root):
        self.root = root

    def current_inst(self):
        return {"name": "测试实例", "comfyui_path": self.root}


from app import model_store as ms
from app.ui.pages import model_market_widget as mmw

# -------------------------------------------------- fake urlopen
class _FakeResp:
    def __init__(self, body, status=200, headers=None):
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        if n is None or n < 0:
            chunk = self._body[self._pos:]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _with_fake(fake_fn, fn, *a, **kw):
    orig = ms.urllib.request.urlopen
    ms.urllib.request.urlopen = fake_fn
    try:
        return fn(*a, **kw)
    finally:
        ms.urllib.request.urlopen = orig


# -------------------------------------------------- 1. SearchResult 兼容
sr = ms.SearchResult([{"id": "a"}, {"id": "b"}], "cur-1")
check("1a SearchResult len/迭代", len(sr) == 2 and [x["id"] for x in sr] == ["a", "b"])
a, b = sr
check("1b SearchResult 解包", a["id"] == "a" and b["id"] == "b")
check("1c next_cursor 属性", sr.next_cursor == "cur-1")
check("1d 纯 list 打桩无游标→None", getattr([1, 2], "next_cursor", None) is None)
check("1e 空游标表示没有下一页",
      ms.SearchResult([1], None).next_cursor is None)

# -------------------------------------------------- 2. Link header cursor 解析
link = '<https://hf-mirror.com/api/models?cursor=abc123>; rel="next"'
check("2a rel=next 提取 cursor", ms._next_cursor_from_link(link) == "abc123",
      str(ms._next_cursor_from_link(link)))
link = '<https://h.co/api/models?search=x&cursor=ab%2Cd&limit=30>; rel="next"'
check("2b URL 编码解码", ms._next_cursor_from_link(link) == "ab,d",
      str(ms._next_cursor_from_link(link)))
check("2c rel=prev 不算下一页",
      ms._next_cursor_from_link('<x?cursor=1>; rel="prev"') is None)
check("2d 多 rel 取 next 的 cursor",
      ms._next_cursor_from_link('<x?cursor=1>; rel="prev", <y?cursor=2>; rel="next"') == "2")
check("2e 空 Link 头", ms._next_cursor_from_link("") is None)

# -------------------------------------------------- 3. search_models cursor
seen = {}
HF_BODY = json.dumps([
    {"id": "t/flux-dev", "downloads": 9, "likes": 2, "pipeline_tag": "text-to-image"},
]).encode()


def fake_hf(req, timeout=30, context=None):
    seen["url"] = req.full_url
    return _FakeResp(HF_BODY, 200,
                     {"Link": '<https://hf-mirror.com/api/models?cursor=nxt>; rel="next"'})


rows = _with_fake(fake_hf, ms.search_models, "flux", 30, "cur+1")
check("3a cursor 进 URL（URL 编码）", "cursor=cur%2B1" in seen["url"], seen["url"])
check("3b page 参数禁用", "page=" not in seen["url"], seen["url"])
check("3c 返回行解析", rows[0]["id"] == "t/flux-dev" and rows[0]["pipeline"] == "text-to-image")
check("3d next_cursor 取自 Link 头", rows.next_cursor == "nxt", str(rows.next_cursor))

seen.clear()
rows = _with_fake(fake_hf, ms.search_models, "flux")
check("3e 首页 URL 不带 cursor", "cursor=" not in seen["url"], seen["url"])

# -------------------------------------------------- 4. search_civitai cursor
CIV_BODY = json.dumps({
    "items": [{"id": 7, "name": "ControlNet 1.1 Models", "type": "ControlNet",
               "stats": {"downloadCount": 3, "favoriteCount": 1}}],
    "metadata": {"nextCursor": "5"},
}).encode()


def fake_civ(req, timeout=30, context=None):
    seen["url"] = req.full_url
    return _FakeResp(CIV_BODY, 200, {})


rows = _with_fake(fake_civ, ms.search_civitai, "controlnet", 20, "5")
check("4a cursor 进 URL", "cursor=5" in seen["url"], seen["url"])
check("4b page 参数禁用（civitai.red 报 400）", "page=" not in seen["url"], seen["url"])
check("4c 返回行解析", rows[0]["id"] == "7" and rows[0]["type"] == "ControlNet")
check("4d next_cursor 取 metadata.nextCursor", rows.next_cursor == "5")

seen.clear()
rows = _with_fake(fake_civ, ms.search_civitai, "controlnet")
check("4e 首页 URL 不带 cursor", "cursor=" not in seen["url"], seen["url"])

# -------------------------------------------------- 5. download_file URL 直传
with tempfile.TemporaryDirectory() as td:
    def fake_dl(req, timeout=60, context=None):
        seen["url"] = req.full_url
        return _FakeResp(b"DATA", 200, {"Content-Length": "4"})

    dest = _with_fake(fake_dl, ms.download_file,
                      "https://civitai.com/api/download/models/99", "tileE.safetensors", td)
    check("5a 直链原样请求", seen["url"] == "https://civitai.com/api/download/models/99",
          seen["url"])
    check("5b 文件名取自 file_path", dest.endswith("tileE.safetensors"), dest)
    check("5c 内容完整落盘", open(dest, "rb").read() == b"DATA")

# -------------------------------------------------- 6. derive_mirror_queries
q = ms.derive_mirror_queries("ControlNet 1.1 Models",
                             ["controlnet11Models_tileE.safetensors",
                              "controlnet11Models_tileE.yaml"])
check("6a ControlNet 实测词组", "controlnet tile" in q, str(q))
check("6b 模型名原样为首查", q[0] == "ControlNet 1.1 Models", str(q))
check("6c 停用词/数字/扩展名过滤（词组策略）",
      all("models" not in x and "1.1" not in x and "safetensors" not in x
          for x in q[1:]), str(q))
check("6d 去重保序", len(q) == len(set(x.lower() for x in q)), str(q))
check("6e 无文件名只剩模型名", ms.derive_mirror_queries("Flux") == ["Flux"])

# -------------------------------------------------- 7. MirrorFallbackWorker 合并去重
got = {}
real_search = ms.search_models


def fake_pages(kw, limit=30, cursor=None):
    got.setdefault("queries", []).append(kw)
    return [{"id": "u/a", "name": "a", "downloads": 1, "likes": 0, "pipeline": ""},
            {"id": "u/b", "name": "b", "downloads": 1, "likes": 0, "pipeline": ""}]


ms.search_models = fake_pages
try:
    w = mmw.MirrorFallbackWorker("ControlNet 1.1 Models",
                                 ["controlnet11Models_tileE.safetensors"])
    out = []
    w.sig_done.connect(lambda rows, err: out.append((rows, err)))
    w.run()                      # 同线程直跑：信号直连，无竞态
    rows7, err7 = out[-1]
    expect_q = ms.derive_mirror_queries("ControlNet 1.1 Models",
                                        ["controlnet11Models_tileE.safetensors"])
    check("7a 多策略逐词检索", got.get("queries") == expect_q,
          f"{got.get('queries')} != {expect_q}")
    check("7b 合并去重（3 查×2 行→2）",
          not err7 and [m["id"] for m in rows7] == ["u/a", "u/b"],
          f"err={err7} rows={[m.get('id') for m in rows7]}")
finally:
    ms.search_models = real_search

# -------------------------------------------------- 8. CivitaiDialog 操作列
URL = "https://civitai.com/api/download/models/1234"
FNAME = "controlnet11Models_tileE.safetensors"
files = [{"name": FNAME, "size_mb": 689.0, "type": "Model", "downloadUrl": URL}]
dl_got = []
ms.search_models = lambda kw, limit=30, cursor=None: []    # 兜底检索离线空结果
try:
    dlg = mmw.CivitaiDialog(None, 7, "ControlNet 1.1 Models", files,
                            on_download=lambda u, f: dl_got.append((u, f)))
    check("8a 文件表 4 列（含操作列）", dlg.tb_files.columnCount() == 4,
          str(dlg.tb_files.columnCount()))
    ops = dlg.tb_files.cellWidget(0, 3)
    btns = ops.findChildren(QPushButton) if ops else []
    texts = sorted(b.text() for b in btns)
    check("8b 操作列 ⬇下载+🔗复制两按钮", texts == ["⬇ 下载", "🔗 复制"], str(texts))
    b_dl = next(b for b in btns if "下载" in b.text())
    b_cp = next(b for b in btns if "复制" in b.text())
    b_dl.click()
    check("8c ⬇ 下载回调收到直链+文件名", dl_got == [(URL, FNAME)], str(dl_got))
    check("8d 按钮即时反馈", b_dl.text() == "已入队 ✓", b_dl.text())
    b_cp.click()
    check("8e 🔗 复制直链进剪贴板", QApplication.clipboard().text() == URL,
          QApplication.clipboard().text())
    wait_worker(lambda: dlg._hf_worker)   # 兜底检索 worker 收尾防串场
    check("8f 兜底空结果文案", "暂未找到" in dlg.lb_hf.text(), dlg.lb_hf.text())
finally:
    ms.search_models = real_search

# -------------------------------------------------- 9. _civitai_download 管道
root = tempfile.mkdtemp(prefix="ch_mkt_")


class FakeDC:
    def __init__(self):
        self.items = []

    def enqueue(self, items):
        self.items.extend(items)


market = mmw.ModelMarketWidget({}, FakeMain(root), lambda: set())
market.dl_center = FakeDC()
market._civitai_download(URL, FNAME)
items9 = market.dl_center.items
dest9 = os.path.join(root, "models", ms.guess_category(FNAME))
check("9a 直链+文件名入队", len(items9) == 1 and items9[0][0] == URL
      and items9[0][1] == FNAME, str(items9))
check("9b 分类按文件名预判→models/<分类>", items9[0][2] == dest9, str(items9))
check("9c 预判分类在合法分类表", ms.guess_category(FNAME) in ms.CATEGORIES,
      ms.guess_category(FNAME))
market._civitai_download("", FNAME)       # 无直链元数据：不入队
check("9d 无直链不入队", len(market.dl_center.items) == 1)

# -------------------------------------------------- 10. 加载更多（追加+去重）
PAGE = {
    None: ms.SearchResult([
        {"id": "a/1", "name": "1", "downloads": 1, "likes": 0, "pipeline": ""},
        {"id": "a/2", "name": "2", "downloads": 1, "likes": 0, "pipeline": ""}], "c-1"),
    "c-1": ms.SearchResult([
        {"id": "a/2", "name": "2", "downloads": 1, "likes": 0, "pipeline": ""},
        {"id": "a/3", "name": "3", "downloads": 1, "likes": 0, "pipeline": ""}], None),
}
calls = []


def fake_page(kw, limit=30, cursor=None):
    calls.append(cursor)
    return PAGE.get(cursor, [])


market.show()
pump(0.2)
market.dl_center = None                   # 走面板自身链路，避开下载中心
ms.search_models = fake_page
try:
    market.run_search("mirror", "flux")
    wait_worker(lambda: market._search_worker)
    check("10a 首页渲染", market.grid.count() == 2, f"count={market.grid.count()}")
    check("10b 有下一页→加载更多行可见",
          market.more_row.isVisible() and market.btn_more.isEnabled())
    market.btn_more.click()
    wait_worker(lambda: market._search_worker)
    check("10c 翻页传 cursor", calls == [None, "c-1"], str(calls))
    check("10d 追加+去重（2+2→3）", market.grid.count() == 3,
          f"count={market.grid.count()}")
    check("10e 末页收尾（行隐藏+计数）",
          not market.more_row.isVisible() and market.lb_more.text() == "已加载 3 个",
          f"vis={market.more_row.isVisible()} lb={market.lb_more.text()}")
    check("10f 末页状态文案", "已加载 3 个" in market.lb_stat.text()
          and "末页" in market.lb_stat.text(), market.lb_stat.text())

    # 旧打桩兼容：纯 list 无游标 → 正常渲染且无「加载更多」
    ms.search_models = lambda kw, limit=30: [
        {"id": "p/1", "name": "1", "downloads": 1, "likes": 0, "pipeline": ""}]
    market.run_search("mirror", "vae")
    wait_worker(lambda: market._search_worker)
    check("10g 纯 list 旧打桩兼容", market.grid.count() == 1
          and not market.more_row.isVisible(),
          f"count={market.grid.count()} vis={market.more_row.isVisible()}")
finally:
    ms.search_models = real_search

import shutil
shutil.rmtree(root, ignore_errors=True)

# -------------------------------------------------- 汇总
fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"模型市场搜索/下载增强回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
if fails:
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
sys.exit(1 if fails else 0)
