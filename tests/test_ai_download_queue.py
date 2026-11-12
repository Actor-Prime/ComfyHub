# -*- coding: utf-8 -*-
"""AI 助手触发的模型下载必须进模型库下载中心，且能在任意线程安全入队

背景（用户反馈 2026-11-12）：「AI 帮忙下载模型时，模型库的下载任务列表里
看不到任务，也不知道下载进度」。

根因：ai_tools._t_model_download 跑在 _AgentWorker 线程里，直接调
DownloadCenter.enqueue()，会在非 GUI 线程 new 出 _TaskRow（QWidget），
对象归属该工作线程、layout 不接管 → 行永远不显示。

断言：
  A. 工作线程里直接 enqueue：行归属工作线程（复现 bug 的判据）
  B. 工作线程里 request_download：行归属 GUI 线程且能入队（修复判据）
  C. 来源徽标：AI 触发的行显示 AI 徽标，页面内下载不显示
  D. _t_model_download 必须走 request_download（而不是直接 enqueue）
  E. 无 GUI（MCP 模式）时同步下载分支不传非法参数（历史 bug：log= 形参不存在）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_ai_download_queue.py
"""
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


app = QApplication.instance() or QApplication([])

from app import ai_tools  # noqa: E402
from app import model_store  # noqa: E402
from app.ui.download_center import DownloadCenter  # noqa: E402


def pump(n=30):
    for _ in range(n):
        app.processEvents()


def run_in_thread(fn):
    t = threading.Thread(target=fn)
    t.start()
    t.join()


# ============================================================ A/B. 线程归属
print("[A] 工作线程直接 enqueue（bug 复现判据）")
dl = DownloadCenter()
dl._pump = lambda: None                  # 不打真网络

run_in_thread(lambda: dl.enqueue([("a/b", "raw_thread.safetensors", "C:/tmp")]))
pump()
rows = list(dl._rows.values())
check("工作线程 enqueue 确实建了行对象", len(rows) == 1, len(rows))
if rows:
    check("工作线程 enqueue 的行归属工作线程（这就是不显示的根因）",
          rows[0].thread() is not app.thread(),
          f"thread={rows[0].thread()}")

print("[B] 工作线程 request_download（修复后路径）")
_box = {}
run_in_thread(lambda: _box.update(
    n=dl.request_download([("a/b", "ai_queued.safetensors", "C:/tmp")], source="ai")))
pump()
n = _box.get("n")
check("request_download 返回入队条数", n == 1, n)
check("行数增加到 2", len(dl._rows) == 2, len(dl._rows))
ai_rows = [r for r in dl._rows.values() if r.fname == "ai_queued.safetensors"]
check("AI 任务行已创建", len(ai_rows) == 1, [r.fname for r in dl._rows.values()])
if ai_rows:
    check("AI 任务行归属 GUI 线程（能被布局接管、能显示）",
          ai_rows[0].thread() is app.thread(), f"thread={ai_rows[0].thread()}")
    check("AI 任务行已挂上面板布局",
          ai_rows[0].parent() is dl._rows_host, ai_rows[0].parent())
    check("AI 任务进了下载队列", any(it[3] == ai_rows[0].task_id for it in dl._pending),
          dl._pending)
check("徽标计数含 AI 任务", dl.text() == "⬇ 2", dl.text())

# ============================================================ C. 来源徽标
print("[C] 来源徽标")
check("AI 任务显示 AI 徽标", ai_rows and not ai_rows[0].lb_src.isHidden(), "应可见")
ui_rows = [r for r in dl._rows.values() if r.fname == "raw_thread.safetensors"]
check("页面内下载不显示 AI 徽标", ui_rows and ui_rows[0].lb_src.isHidden(), "应隐藏")
check("AI 任务 source 记录正确", ai_rows and ai_rows[0].source == "ai",
      ai_rows[0].source if ai_rows else None)

# ============================================================ D. 工具接线
print("[D] _t_model_download 走线程安全入口")


class _FakeModelsPage:
    def __init__(self, dl):
        self.dl_center = dl


class _FakeMainWin:
    def __init__(self, dl):
        self.models_page = _FakeModelsPage(dl)


dl2 = DownloadCenter()
dl2._pump = lambda: None
calls = {"request": 0, "enqueue": 0}
_old_req, _old_enq = dl2.request_download, dl2.enqueue
dl2.request_download = lambda items, source="ai": (calls.__setitem__("request", calls["request"] + 1),
                                                  _old_req(items, source))[1]
dl2.enqueue = lambda items, source="ui": (calls.__setitem__("enqueue", calls["enqueue"] + 1),
                                          _old_enq(items, source))[1]

ctx = ai_tools.build_ctx({"instances": [{"comfyui_path": "C:/tmp/fake_comfy"}]},
                         {"comfyui_path": "C:/tmp/fake_comfy", "port": 8188},
                         mainwin=_FakeMainWin(dl2))
res = ai_tools.run_tool("download_model",
                        {"repo_id": "org/repo", "file_path": "loras/x.safetensors"},
                        ctx) if hasattr(ai_tools, "run_tool") else None
if res is None:                     # 没有统一 run_tool 就直接调函数表
    fn = next(t["fn"] for t in ai_tools.TOOLS if t["name"] == "download_model")
    res = fn({"repo_id": "org/repo", "file_path": "loras/x.safetensors"}, ctx)
pump()
check("工具调用成功", res and res.get("ok") is True, res)
check("走 request_download 而不是直接 enqueue", calls["request"] == 1, calls)
check("提示里写明进度去哪看", "⬇" in (res or {}).get("note", ""), (res or {}).get("note"))
check("入队文件落分类子目录", res and res.get("dest", "").endswith("loras"), res.get("dest"))

# ============================================================ E. 无 GUI 分支
print("[E] MCP 模式（无 mainwin）同步下载不传非法参数")
seen = {}


def _fake_download(rid, fp, dest, **kw):
    seen.update({"rid": rid, "fp": fp, "dest": dest, "kw": kw})
    return os.path.join(dest, os.path.basename(fp))


_real = model_store.download_file
model_store.download_file = _fake_download
try:
    ctx2 = ai_tools.build_ctx({"instances": [{"comfyui_path": "C:/tmp/fake_comfy"}]},
                              {"comfyui_path": "C:/tmp/fake_comfy", "port": 8188})
    fn = next(t["fn"] for t in ai_tools.TOOLS if t["name"] == "download_model")
    res2 = fn({"repo_id": "org/repo", "file_path": "vae/y.safetensors"}, ctx2)
finally:
    model_store.download_file = _real
check("无 GUI 时同步下载成功（不抛 TypeError）", res2.get("ok") is True, res2)
check("同步下载没有传非法 log= 形参", "log" not in seen.get("kw", {}), seen)

# ============================================================ F. 主窗口端到端
print("[F] 主窗口接线：AI 线程下载 → 模型库下载中心 + 状态栏提示")
from app.ui.main_window import MainWindow  # noqa: E402

win = MainWindow()
win.resize(1400, 900)
win.show()
pump()
dl3 = win.models_page.dl_center
_real_dl = model_store.download_file
model_store.download_file = lambda *a, **k: "stub"
try:
    ctx3 = ai_tools.build_ctx({"instances": [{"comfyui_path": "C:/tmp/fake_comfy"}]},
                              {"comfyui_path": "C:/tmp/fake_comfy", "port": 8188},
                              mainwin=win)
    fn = next(t["fn"] for t in ai_tools.TOOLS if t["name"] == "download_model")
    _box3 = {}

    def _go():
        _box3["r"] = fn({"repo_id": "org/repo",
                         "file_path": "checkpoints/z.safetensors"}, ctx3)

    run_in_thread(_go)
    pump(60)
    rows3 = [r for r in dl3._rows.values() if r.fname == "z.safetensors"]
    check("主窗口下载中心出现 AI 任务", len(rows3) == 1,
          [r.fname for r in dl3._rows.values()])
    if rows3:
        check("任务行在 GUI 线程（能真正刷新进度）",
              rows3[0].thread() is app.thread(), rows3[0].thread())
    check("状态栏提示了进度位置",
          "⬇" in win.statusBar().currentMessage(), win.statusBar().currentMessage())
finally:
    model_store.download_file = _real_dl
    win.close()

print(f"\nPASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
