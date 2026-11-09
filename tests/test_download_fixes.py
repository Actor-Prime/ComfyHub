# -*- coding: utf-8 -*-
"""下载链三修复回归（2026-10-31）

覆盖：
1. _resolve_dests：分类名 → <comfyui>/models/<分类> 绝对目录；
   绝对路径/带分隔符路径原样保留；空值默认 checkpoints
2. download_file 截断显式失败（不静默改名，保留 .part）
3. download_file 续传 206 校验：返 200 时截断重来，不追加损坏
4. 进度信号 >2GB 字节数不溢出（object 承载）

用法：.venv_build/Scripts/python.exe tests/test_download_fixes.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication([])

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app import model_store as ms
from app.ui.pages.model_market_widget import ModelMarketWidget
from app.ui.download_center import _QueueWorker

# -------------------------------------------------- 1. _resolve_dests
items = [("lykon/dreamshaper", "DreamShaper_2.52.safetensors", "checkpoints")]
out = ModelMarketWidget._resolve_dests(items, r"F:\ComfyUI-aki-v3\ComfyUI")
check("分类名 → models 绝对目录",
      out[0][2] == os.path.join(r"F:\ComfyUI-aki-v3\ComfyUI", "models", "checkpoints"),
      out[0][2])

items = [("a/b", "x.safetensors", r"D:\dl\dest")]
out = ModelMarketWidget._resolve_dests(items, r"F:\C")
check("绝对路径原样保留", out[0][2] == r"D:\dl\dest", out[0][2])

items = [("a/b", "x.safetensors", "custom/sub")]
out = ModelMarketWidget._resolve_dests(items, r"F:\C")
check("带分隔符路径原样保留", out[0][2] == "custom/sub", out[0][2])

items = [("a/b", "x.safetensors", None)]
out = ModelMarketWidget._resolve_dests(items, r"F:\C")
check("空值默认 checkpoints",
      out[0][2] == os.path.join(r"F:\C", "models", "checkpoints"), out[0][2])

items = [("a/b", "x.safetensors", "loras")]
out = ModelMarketWidget._resolve_dests(items, r"F:\C")
check("loras 分类同样转换",
      out[0][2] == os.path.join(r"F:\C", "models", "loras"), out[0][2])

# -------------------------------------------------- fake urlopen
class _FakeResp:
    def __init__(self, body, status=200, headers=None):
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n):
        # 模拟“中途断线”：_stop_at 字节后返回 b''（不抛异常的静默截断）
        stop = getattr(self, "_stop_at", len(self._body))
        if self._pos >= stop:
            return b""
        end = min(self._pos + n, stop)      # ★ 首刀也要被 stop_at 截住
        chunk = self._body[self._pos:end]
        self._pos += len(chunk)
        return chunk


def _with_fake(fake_fn, fn, *a, **kw):
    orig = ms.urllib.request.urlopen
    ms.urllib.request.urlopen = fake_fn
    try:
        return fn(*a, **kw)
    finally:
        ms.urllib.request.urlopen = orig


BODY = b"0123456789" * 100     # 1000 字节

# -------------------------------------------------- 2. 截断显式失败
with tempfile.TemporaryDirectory() as td:
    def fake_trunc(req, timeout=60, context=None):
        r = _FakeResp(BODY, 200, {"Content-Length": str(len(BODY))})
        r._stop_at = 300          # 只吐 300 字节就“断线”
        return r

    err = None
    try:
        _with_fake(fake_trunc, ms.download_file, "a/b", "m.safetensors", td)
    except IOError as e:
        err = e
    part = os.path.join(td, "m.safetensors.part")
    dest = os.path.join(td, "m.safetensors")
    check("截断抛 IOError", err is not None and "下载中断" in str(err), repr(err))
    check("截断保留 .part", os.path.isfile(part) and os.path.getsize(part) == 300)
    check("截断不产生成品文件", not os.path.isfile(dest))

# -------------------------------------------------- 3a. 续传 206 正常追加
with tempfile.TemporaryDirectory() as td:
    part = os.path.join(td, "m.safetensors.part")
    with open(part, "wb") as f:
        f.write(BODY[:400])       # 已下 400 字节

    seen = {}

    def fake_206(req, timeout=60, context=None):
        seen["range"] = req.get_header("Range") or req.headers.get("Range")
        return _FakeResp(BODY[400:], 206, {"Content-Length": str(len(BODY) - 400)})

    dest = _with_fake(fake_206, ms.download_file, "a/b", "m.safetensors", td)
    data = open(dest, "rb").read()
    check("206 续传 Range 头正确", seen["range"] == "bytes=400-", seen["range"])
    check("206 续传内容完整", data == BODY, f"len={len(data)}")

# -------------------------------------------------- 3b. 返 200 时截断重来
with tempfile.TemporaryDirectory() as td:
    part = os.path.join(td, "m.safetensors.part")
    with open(part, "wb") as f:
        f.write(BODY[:400])

    def fake_200(req, timeout=60, context=None):
        return _FakeResp(BODY, 200, {"Content-Length": str(len(BODY))})

    dest = _with_fake(fake_200, ms.download_file, "a/b", "m.safetensors", td)
    data = open(dest, "rb").read()
    check("服务器无视 Range 返 200 → 重下不追加", data == BODY,
          f"len={len(data)} (期望 {len(BODY)}，追加损坏会是 {400 + len(BODY)})")

# -------------------------------------------------- 4. 进度信号 >2GB 不溢出
BIG = 7703274889      # DreamShaper_2.52 真实字节数，超 int32 上限
got = []
w = _QueueWorker([])
w.sig_progress.connect(lambda tid, d, t: got.append((tid, d, t)))
try:
    w.sig_progress.emit("t1", BIG, BIG)
    check(f"进度信号 {BIG} 字节 emit 不溢出", got == [("t1", BIG, BIG)], str(got))
except OverflowError as e:
    check(f"进度信号 {BIG} 字节 emit 不溢出", False, repr(e))

# _TaskRow.set_progress 大值不炸 Qt int 参数
from app.ui.download_center import _TaskRow
row = _TaskRow("t1", "big.safetensors")
try:
    row.set_progress(5449973760, BIG)
    check("进度条大值 set_progress 不溢出",
          row.pb.value() == min(1000, int(5449973760 * 1000 / BIG)),
          f"pb={row.pb.value()}")
except (OverflowError, TypeError) as e:
    check("进度条大值 set_progress 不溢出", False, repr(e))

# -------------------------------------------------- 5. 弹窗默认不勾（防全仓库巨量下载）
from PySide6.QtCore import Qt
from app.ui.pages.model_market_widget import DownloadDialog
dlg = DownloadDialog(None, "a/b", [
    {"path": "m1.safetensors", "size": 1000, "category": "checkpoints"},
    {"path": "m2.safetensors", "size": 900, "category": "loras"},
])
states = [dlg.tree.topLevelItem(i).checkState(0)
          for i in range(dlg.tree.topLevelItemCount())]
check("弹窗默认全部不勾", states == [Qt.Unchecked, Qt.Unchecked], str(states))

# -------------------------------------------------- 汇总
fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"下载链修复回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
if fails:
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
sys.exit(1 if fails else 0)
