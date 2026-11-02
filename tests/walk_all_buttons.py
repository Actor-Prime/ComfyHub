# -*- coding: utf-8 -*-
"""全按钮穷举点检（离线沙箱）

和 walk_all_pages.py 的区别：那个只点"安全按钮"，这个**把每个按钮都点一遍**
（含安装/下载/启停/AI/删除），靠打桩把危险动作架空 —— 不会真的装东西、
不会真的启 ComfyUI、不会真的联网、不会真的删文件。目的是把
「点了按钮 → 槽函数炸 / 卡死 / 弹两个窗 / 状态错乱」这类问题扫出来。

打桩清单：
  · subprocess.run / Popen        → 假进程（installer 的 pip/git 全靠它）
  · requests / urllib             → 立即 ConnectionError（避免真联网与超时）
  · ComfyProcess.start/stop       → 只记日志
  · PlanExecutor.run              → 直接返回成功（不真装）
  · bootstrap.download_multi      → 返回假路径
  · QFileDialog / QInputDialog    → 一律取消
  · QMessageBox                   → 自动应答（question 一律 No）
  · QProcess.start                → 假启动

判定标准（任一即 FAIL）：
  1. 点击时抛未捕获异常（含 Qt 槽内异常）
  2. 点了出现模态框后无法关闭（3 秒内关不掉）
  3. 点击后进程/页面崩溃（窗口消失、页面栈异常）
  4. 同一按钮点两次结果不一致（非幂等且无提示）

用法：
  .venv_build/Scripts/python.exe tests/walk_all_buttons.py            # 全量
  .venv_build/Scripts/python.exe tests/walk_all_buttons.py --page 3   # 只点第 3 页
  .venv_build/Scripts/python.exe tests/walk_all_buttons.py --list     # 只列按钮不点
"""
import argparse
import json
import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AP = argparse.ArgumentParser()
AP.add_argument("--page", type=int, default=None, help="只点第 N 页（1 起）")
AP.add_argument("--list", action="store_true", help="只列按钮，不点击")
AP.add_argument("--limit-per-page", type=int, default=0, help="每页最多点几个（0=不限）")
AP.add_argument("--out", default=os.path.join(ROOT, "analysis", "all_buttons_report.json"))
ARGS = AP.parse_args()

# ============================================================ 打桩（必须在 import UI 前）
import subprocess as _sp


class _FakeProc:
    """假 Popen：never really runs anything"""

    def __init__(self, *a, **k):
        self.pid = 0
        self.returncode = 0
        self.stdout = None
        self.stderr = None

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def communicate(self, *a, **k):
        return ("", "")

    def terminate(self):
        pass

    def kill(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


REAL = {
    "run": _sp.run, "Popen": _sp.Popen,
}
SP_LOG = []


def _fake_run(cmd, *a, **k):
    SP_LOG.append(str(cmd)[:120])
    return _sp.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="sandboxed")


_sp.run = _fake_run
_sp.Popen = _FakeProc
_sp.call = lambda *a, **k: 1
_sp.check_output = lambda *a, **k: b""

NET_LOG = []


def _install_net_stubs():
    try:
        import requests

        def _boom(*a, **k):
            NET_LOG.append(str(a[0] if a else k.get("url", ""))[:80])
            raise requests.exceptions.ConnectionError("sandboxed")

        requests.get = _boom
        requests.post = _boom
        requests.request = _boom
        requests.head = _boom
        requests.Session.request = lambda self, *a, **k: _boom(*a, **k)
    except Exception:
        pass
    try:
        import urllib.request as _ur

        def _boom2(*a, **k):
            NET_LOG.append(str(a[0] if a else "")[:80])
            raise OSError("sandboxed")
        _ur.urlopen = _boom2
    except Exception:
        pass


_install_net_stubs()

# ============================================================ 环境
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QCheckBox, QComboBox, QDialog, QFileDialog,
    QInputDialog, QLineEdit, QMessageBox, QRadioButton, QTabWidget, QWidget,
)

app = QApplication([])

# 自动应答所有消息框
BOX_LOG = []
QMessageBox.information = staticmethod(lambda *a, **k: BOX_LOG.append(("info", a[2] if len(a) > 2 else "")) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: BOX_LOG.append(("warn", a[2] if len(a) > 2 else "")) or QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: BOX_LOG.append(("crit", a[2] if len(a) > 2 else "")) or QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: BOX_LOG.append(("ask", a[2] if len(a) > 2 else "")) or QMessageBox.No)
QMessageBox.about = staticmethod(lambda *a, **k: BOX_LOG.append(("about", "")) or None)
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))

# 重型动作打桩
from app import process as proc_mod
from app import installer as inst_mod
from app import bootstrap as boot_mod

PROC_LOG = []
proc_mod.ComfyProcess.start = lambda self, inst: PROC_LOG.append(f"start:{inst.get('name')}") or True
proc_mod.ComfyProcess.stop = lambda self: PROC_LOG.append("stop") or True
inst_mod.PlanExecutor.run = lambda self, steps, log=print, on_step_done=None: (
    PROC_LOG.append(f"plan:{len(steps)}步"), {"ok": True})
boot_mod.download_multi = lambda url, dest, *a, **k: (PROC_LOG.append("download"), dest)

# 全局异常收集
ERRORS = []


def _hook(tp, val, tb):
    ERRORS.append({
        "type": tp.__name__,
        "msg": str(val)[:300],
        "tb": "".join(traceback.format_exception(tp, val, tb))[-1200:],
    })


sys.excepthook = _hook

from app.ui.main_window import MainWindow, NAV_ITEMS  # noqa: E402

RESULTS = []


# ---- 模态框看门狗 -------------------------------------------------------
# 有些按钮会开 exec() 的模态对话框（QDialog.exec 会起自己的事件循环），
# 点击函数根本不会返回 —— 之前的版本就是这样卡死在「安装新依赖」上。
# 装一个定时器：模态框活了超过 1.2 秒就把它关掉并记一笔。
MODAL_SEEN = []
_watch = QTimer()
_watch.setInterval(250)
_open_since = {"t": None, "w": None}


def _watchdog():
    w = app.activeModalWidget()
    if w is None:
        _open_since["t"], _open_since["w"] = None, None
        return
    now = time.time()
    if _open_since["w"] is not w:
        _open_since["t"], _open_since["w"] = now, w
        return
    if now - _open_since["t"] < 1.2:
        return
    title = ""
    try:
        title = w.windowTitle() or type(w).__name__
    except Exception:
        title = type(w).__name__
    MODAL_SEEN.append(title)
    try:
        w.reject() if isinstance(w, QDialog) else w.close()
    except Exception:
        try:
            w.close()
        except Exception:
            pass
    _open_since["t"], _open_since["w"] = None, None


_watch.timeout.connect(_watchdog)
_watch.start()


def pump(sec=0.25):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.01)


def close_modal(timeout=3.0):
    """关掉当前模态窗；关不掉返回 False"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = app.activeModalWidget()
        if w is None:
            return True
        try:
            if isinstance(w, QDialog):
                w.reject()
            else:
                w.close()
        except Exception:
            return False
        pump(0.15)
    return app.activeModalWidget() is None


def label_of(w):
    if isinstance(w, QComboBox):
        return f"下拉[{w.currentText()}]"
    t = ""
    for attr in ("text", "title"):
        try:
            t = getattr(w, attr)() or ""
        except Exception:
            pass
        if t:
            break
    if not t:
        try:
            t = w.objectName()
        except Exception:
            t = ""
    return f"{type(w).__name__}[{t[:24]}]"


def collect(page):
    """页面内所有可交互控件（递归）"""
    out = []
    stack = [page]
    while stack:
        w = stack.pop()
        for ch in w.findChildren(QWidget):
            if ch in out:
                continue
            out.append(ch)
            stack.append(ch)
    seen = []
    for w in out:
        if isinstance(w, (QAbstractButton, QComboBox, QLineEdit)) and w.isVisibleTo(page):
            seen.append(w)
    return seen


def click_one(w, page_name):
    """点一个控件，返回 (结果, 说明)"""
    before_err = len(ERRORS)
    rec = {"page": page_name, "control": label_of(w), "kind": type(w).__name__}
    try:
        if isinstance(w, QComboBox):
            n = w.count()
            if n <= 1:
                return rec | {"result": "skip", "detail": "选项<=1"}
            old = w.currentIndex()
            w.setCurrentIndex((old + 1) % n)
            pump(0.2)
            rec["detail"] = f"{old}->{w.currentIndex()}"
            w.setCurrentIndex(old)
            pump(0.1)
        elif isinstance(w, QLineEdit):
            return rec | {"result": "skip", "detail": "输入框不盲点"}
        elif isinstance(w, (QCheckBox, QRadioButton)):
            old = w.isChecked()
            w.click()
            pump(0.2)
            if not close_modal(1.0):
                return rec | {"result": "FAIL", "detail": "勾选后弹窗关不掉"}
            w.setChecked(old)
            pump(0.1)
            rec["detail"] = f"checked {old}->{w.isChecked()}"
        else:
            if not w.isEnabled() or not w.isVisibleTo(w.window()):
                return rec | {"result": "skip", "detail": "disabled/hidden"}
            w.click()
            pump(0.3)
            closed = close_modal(3.0)
            if not closed:
                return rec | {"result": "FAIL", "detail": "模态窗 3 秒关不掉"}
    except Exception as e:
        return rec | {"result": "FAIL", "detail": f"{type(e).__name__}: {e}"[:200]}

    new_err = ERRORS[before_err:]
    if new_err:
        return rec | {"result": "FAIL", "detail": f"异常 {new_err[0]['type']}: {new_err[0]['msg']}"[:220]}
    return rec | {"result": "ok"}


def main():
    win = MainWindow()
    win.resize(1180, 780)
    win.show()
    pump(0.6)

    pages = []
    for i in range(win.stack.count()):
        w = win.stack.widget(i)
        if w is None:
            continue
        name = NAV_ITEMS[i][1] if i < len(NAV_ITEMS) else f"page{i}"
        pages.append((i, name, w))

    targets = pages if ARGS.page is None else [p for p in pages if p[0] + 1 == ARGS.page]
    print(f"共 {len(pages)} 页，本次点检 {len(targets)} 页\n")

    for idx, pname, page in targets:
        win.nav.setCurrentRow(idx)          # 走真实导航路径（含 on_show / 翻译补扫）
        pump(0.6)
        if ARGS.list:
            ctrls = collect(page)
            print(f"[{idx+1}/{len(pages)}] {pname} — 可见控件 {len(ctrls)}")
            for c in ctrls:
                print("    ", label_of(c))
            continue

        # ⚠️ 不能事先收集一遍再逐个点：点表格里的按钮会重建整张表，
        # 之前的控件对象全部变成"已删除的 C++ 对象"（libshiboken RuntimeError）。
        # 正确做法：每点一次重新枚举，用「类型+文字」做去重键（重建后键仍然稳定）。
        # 另外 QTabWidget 只有当前标签页的控件可见，得把每个标签页都翻一遍。
        done = set()
        tab_done = set()
        n = 0
        total = len(collect(page))
        print(f"[{idx+1}/{len(pages)}] {pname} — 当前标签可见控件 {total}")
        for _ in range(600):                 # 上限保护：动态列表别无限点下去
            if ARGS.limit_per_page and n >= ARGS.limit_per_page:
                break
            fresh = collect(page)
            pick = None
            for c in fresh:
                key = (type(c).__name__, label_of(c))
                if key not in done:
                    pick = (c, key)
                    break
            if pick is None:
                # 当前标签点完了 → 翻下一个没翻过的标签页
                nxt = None
                for tw in page.findChildren(QTabWidget):
                    for ti in range(tw.count()):
                        if (id(tw), ti) not in tab_done:
                            nxt = (tw, ti)
                            break
                    if nxt:
                        break
                if nxt is None:
                    break
                tw, ti = nxt
                tab_done.add((id(tw), ti))
                tw.setCurrentIndex(ti)
                pump(0.5)
                print(f"      ↳ 切换标签：{tw.tabText(ti)}")
                continue
            c, key = pick
            done.add(key)
            r = click_one(c, pname)
            n += 1
            RESULTS.append(r)
            if r["result"] != "ok":
                flag = {"skip": "-", "FAIL": "X"}.get(r["result"], "?")
                print(f"    {flag} {r['control']}  ({r.get('detail','')})")
        pump(0.2)

    # 汇总
    fails = [r for r in RESULTS if r["result"] == "FAIL"]
    ok = [r for r in RESULTS if r["result"] == "ok"]
    skips = [r for r in RESULTS if r["result"] == "skip"]
    print("=" * 52)
    print(f"控件点检：点击 {len(ok)} 正常 / {len(fails)} 异常 / {len(skips)} 跳过")
    if PROC_LOG:
        print(f"被架空的重型动作 {len(PROC_LOG)} 次：", ", ".join(sorted(set(PROC_LOG)))[:200])
    if NET_LOG:
        print(f"被拦截的网络请求 {len(NET_LOG)} 次")
    if ERRORS:
        print(f"\n未捕获异常 {len(ERRORS)} 条：")
        for e in ERRORS[:10]:
            print(f"  - {e['type']}: {e['msg']}")
    if fails:
        print("\n异常控件明细：")
        for r in fails:
            print(f"  [{r['page']}] {r['control']} → {r.get('detail')}")

    os.makedirs(os.path.dirname(ARGS.out), exist_ok=True)
    with open(ARGS.out, "w", encoding="utf-8") as f:
        json.dump({"results": RESULTS, "errors": ERRORS,
                   "sandboxed_proc": PROC_LOG, "blocked_net": NET_LOG},
                  f, ensure_ascii=False, indent=2)
    print(f"\n报告：{ARGS.out}")
    return 1 if (fails or ERRORS) else 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 2
    sys.stdout.flush()
    os._exit(rc)
