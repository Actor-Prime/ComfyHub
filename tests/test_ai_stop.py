# -*- coding: utf-8 -*-
"""「停止」与长工具可取消 — 2026-11-15

事故现场：助手调 control_instance(start) 等 ComfyUI HTTP 就绪（最长 180 秒），
用户点「停止」无效（工具根本不看 stop_flag），而「新建对话」在 worker 非空时
直接 return —— 用户看到的就是「软件卡死，连对话都建不了」。

本测试守四件事：
  A. 点停止 → 界面立刻放开（按钮/send/worker 引用）
  B. 停止后旧线程的事件全部丢弃，不会往新会话里插内容
  C. worker 在跑时点新建对话 → 自动先停，不会静默无反应
  D. control_instance / wait_job 能被打断，进程死了要立刻返回而不是等满超时
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

FAILS = []
_N = [0]


def check(name, ok, detail=""):
    _N[0] += 1
    print(("PASS " if ok else "FAIL ") + name
          + (("  [%s]" % (detail,)) if detail else ""))
    if not ok:
        FAILS.append(name)


app = QApplication.instance() or QApplication([])


def pump(sec=0.2):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.01)


def _finish(page):
    """关页面前把它的 QThread 收干净。

    页面把废弃 worker 放进 _zombies 保引用，但没人等它们跑完——直接 close
    会在进程退出时把还在跑的 QThread 一起销毁，Windows 上进程以
    0xC0000409 (STATUS_STACK_BUFFER_OVERRUN) 结束，22/22 全绿却让外层判成失败。

    排查记录（2026-11-16）：先怀疑退出方式，实测 sys.exit / os._exit /
    ctypes.ExitProcess 全都崩；真因是「销毁还在跑的 QThread」，不是退出路径。
    在这里 wait 干净后走 sys.exit 就稳定 rc=0——别再回头改退出方式。
    """
    for t in list(getattr(page, "_zombies", None) or []):
        try:
            if hasattr(t, "wait"):
                t.wait(3000)
        except Exception:
            pass
    w = getattr(page, "_worker", None)
    try:
        if w is not None and hasattr(w, "wait"):
            w.wait(3000)
    except Exception:
        pass
    page.close()


# ---------------------------------------------------------------- 桩
class _FakeWin(QObject):
    """AI 页面需要 current_inst()；工具需要 procs"""

    def __init__(self):
        super().__init__()
        self.procs = _FakeProcs()

    def current_inst(self):
        return {"id": "t1", "name": "T", "port": 8188,
                "comfyui_path": r"F:\fake\ComfyUI"}


class _FakeProc:
    def __init__(self):
        self.pid = 4242

    def start(self, inst, open_browser=True):
        pass

    def stop(self):
        pass


class _FakeProcs:
    """is_running 第一次 False（触发启动分支），之后 True（进程起来了）"""

    def __init__(self):
        self.calls = 0

    def is_running(self, inst):
        self.calls += 1
        return self.calls > 1

    def get(self, inst):
        return _FakeProc()

    def actual_port(self, inst):
        return 8188

    def tail(self, iid, n):
        return [(0, "启动中…")]


def part_a():
    print("== A. 停止立刻放开界面 ==")
    from app.ui.pages.ai_page import AiPage
    import app.ai_agent as agent

    page = AiPage({}, _FakeWin())
    page.show()

    # 让 run_agent 变成长阻塞（不发网络请求），模拟「卡在工具里」
    orig = agent.run_agent

    def slow_run(*a, **k):
        for _ in range(60):
            if (k.get("stop_flag") is not None
                    and k["stop_flag"].is_set()):
                return
            time.sleep(0.05)

    agent.run_agent = slow_run
    try:
        page.ed.setPlainText("帮我启动实例")
        page._send()
        pump(0.3)
        # 2026-11-20：发送键与停止键合并成一个圆钮（用户要求）。
        # 不再有 b_stop；忙碌态的特征改为 _busy=True + tooltip 变成「停止生成」，
        # 而按钮本身始终可点（点了就是停止）。
        check("发送后进入忙碌态",
              page._worker is not None and page._busy is True
              and page.b_send.toolTip() == "停止生成")

        w = page._worker
        page._stop()
        pump(0.2)
        check("停止后 worker 引用摘除", page._worker is None)
        check("停止后可以继续输入", page.b_send.isEnabled())
        check("停止后按钮回到发送态",
              page._busy is False and page.b_send.toolTip() == "发送（回车）")
        check("停止后旧线程被标记 abandoned", getattr(w, "abandoned", False))
        check("旧线程被保住引用（在队列或已收尾）",
              w in page._zombies or w.isFinished())

        # 旧线程迟到的事件不能进界面
        n_before = page.flow.count() if hasattr(page.flow, "count") else -1
        page._on_event({"kind": "assistant", "text": "迟到的回答"}, w)
        pump(0.1)
        n_after = page.flow.count() if hasattr(page.flow, "count") else -1
        check("废弃线程的事件被丢弃", n_before == n_after,
              "%s → %s" % (n_before, n_after))
    finally:
        agent.run_agent = orig
        _finish(page)


def part_b():
    print("== B. 忙碌时点新建对话 ==")
    from app.ui.pages.ai_page import AiPage
    import app.ai_agent as agent
    page = AiPage({}, _FakeWin())
    page.show()
    orig = agent.run_agent

    def slow_run(*a, **k):
        time.sleep(3)

    agent.run_agent = slow_run
    try:
        old_cid = page._chat_id
        page.ed.setPlainText("做一次体检")
        page._send()
        pump(0.3)
        check("先确认在忙", page._worker is not None)
        page._new_chat()
        pump(0.2)
        check("新建对话不再被卡住", page._worker is None)
        check("确实换了新会话", page._chat_id != old_cid)
        check("输入区恢复可用", page.b_send.isEnabled())
    finally:
        agent.run_agent = orig
        _finish(page)


def part_c():
    print("== C. control_instance 可取消 ==")
    from app import ai_tools
    win = _FakeWin()
    ctx = ai_tools.build_ctx({"instances": []}, {"id": "t1", "port": 8188},
                             mainwin=win, log=lambda *a: None)
    ev = threading.Event()
    ctx.cancel = ev
    orig_probe = ai_tools._probe_ok
    ai_tools._probe_ok = lambda p, timeout=2: False      # HTTP 永远不就绪

    # 不取消：会一直等到超时（这里给 30s 下限，测试里只跑 1.5s 就撤）
    ev.set()                                            # 事先取消
    t0 = time.time()
    try:
        r = ai_tools.execute("control_instance", {"action": "start",
                                                  "timeout_s": 300}, ctx)
    finally:
        ai_tools._probe_ok = orig_probe
    dt = time.time() - t0
    check("取消后立刻返回", dt < 3.0, "%.2fs" % dt)
    check("返回里标明已取消", r.get("cancelled") is True, r)
    check("提示不误导（说明后台仍在启动）",
          "后台" in str(r.get("msg") or ""), r.get("msg"))


def part_c2():
    print("== C2. 进程启动后退出要立刻报错 ==")
    from app import ai_tools

    class _DeadProcs(_FakeProcs):
        def is_running(self, inst):
            self.calls += 1
            # 1: 检查是否已在跑 → False
            # 2: 等进程起来 → True
            # 3: 启动后的二次确认 → True
            # 4+: 等 HTTP 时发现已退出 → False
            return self.calls in (2, 3)

    win = _FakeWin()
    win.procs = _DeadProcs()
    ctx = ai_tools.build_ctx({"instances": []}, {"id": "t1", "port": 8188},
                             mainwin=win, log=lambda *a: None)
    orig_probe = ai_tools._probe_ok
    ai_tools._probe_ok = lambda p, timeout=2: False
    t0 = time.time()
    try:
        r = ai_tools.execute("control_instance", {"action": "start",
                                                  "timeout_s": 300}, ctx)
    finally:
        ai_tools._probe_ok = orig_probe
    dt = time.time() - t0
    check("进程死了不等满 300 秒", dt < 6.0, "%.2fs" % dt)
    check("明确说是「进程退出」而不是「未就绪」",
          r.get("ok") is False and "退出" in str(r.get("error") or ""),
          str(r.get("error"))[:60])
    check("带出日志尾部", "启动中" in str(r.get("error")))


def part_d():
    print("== D. wait_job 可取消 ==")
    from app import ai_tools
    ctx = ai_tools.build_ctx({"instances": []}, {"id": "t1", "port": 8188},
                             mainwin=_FakeWin(), log=lambda *a: None)
    ev = threading.Event()
    ev.set()
    ctx.cancel = ev
    t0 = time.time()
    r = ai_tools.execute("wait_job", {"prompt_id": "pid-x", "timeout_s": 600}, ctx)
    dt = time.time() - t0
    check("立刻返回", dt < 2.0, "%.2fs" % dt)
    check("状态是 cancelled", r.get("state") == "cancelled", r.get("state"))
    check("提示可用 get_job 复查", "get_job" in str(r.get("note") or ""))


def part_e():
    print("== E. 接线检查 ==")
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app", "ai_agent.py"), encoding="utf-8").read()
    check("run_agent 把 stop_flag 传给工具层", "cancel=stop_flag" in src)
    ui = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app", "ui", "pages", "ai_page.py"),
        encoding="utf-8").read()
    check("停止不再只是置标志（会摘掉 worker 引用）",
          "self._worker = None" in ui and "abandoned" in ui)


def main():
    part_a()
    part_b()
    part_c()
    part_c2()
    part_d()
    part_e()
    print("\n%d/%d 通过" % (_N[0] - len(FAILS), _N[0]))
    if FAILS:
        print("失败：", FAILS)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
