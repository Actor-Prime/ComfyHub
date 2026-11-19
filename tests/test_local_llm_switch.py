# -*- coding: utf-8 -*-
"""本地模型「运行中切换模型」回归测试 — 2026-11-19

用户背景（客户原话）：「启动了之后，可以切换模型吗？还是说要先保存，然后再重启服务？」
原实现：运行中切下拉只 refresh()，服务照跑旧模型、界面零提示；而且切到未下载的模型时
「下载模型」按钮仍显示「模型已就绪」且点不动（因为判定用的是「正在跑的」而不是「选中的」）。

现在：运行中切到「已下载」的另一个模型 → 按钮变「应用并重启」，一次点击完成停+启；
切到「未下载」的模型 → 明确提示先下载。

覆盖（离线，不打桩整块 UI）：
    A. pending_model_switch 纯函数：各组合的判定
    B. 真实卡片：按钮文案 / 下载按钮可用性 / 提示文案
    C. _on_toggle_server：待切换时先停再启；无变更时只停
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []
_N = [0]


def check(label, cond, detail=""):
    _N[0] += 1
    if cond:
        print("[PASS] %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("[FAIL] %s %s" % (label, detail))


from PySide6.QtWidgets import QApplication          # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from app import local_llm as L                       # noqa: E402
from app.i18n import T                               # noqa: E402
from app.ui.pages.local_llm_card import (            # noqa: E402
    LocalLlmCard, pending_model_switch,
)

VL = "qwen3-vl-2b"
Q35 = "qwen3.5-4b-uncensored"


def t_pure():
    """A. 纯函数"""
    check("未运行 → 不算待切换",
          pending_model_switch(VL, Q35, False, True) is False)
    check("同一个模型 → 不算待切换",
          pending_model_switch(Q35, Q35, True, True) is False)
    check("运行中换已下载的模型 → 待切换",
          pending_model_switch(VL, Q35, True, True) is True)
    check("运行中换未下载的模型 → 不算待切换（重启也没用）",
          pending_model_switch(VL, Q35, True, False) is False)
    check("下拉为空 → 不算待切换",
          pending_model_switch("", Q35, True, True) is False)
    check("当前加载为空（未知）→ 不算待切换",
          pending_model_switch(VL, "", True, True) is False)
    check("两侧空白容错",
          pending_model_switch("  " + VL + " ", Q35, True, True) is True)


class FakeServer:
    """冒充 LocalServer：只提供卡片会读的字段"""

    def __init__(self, running=False, mid="", info=None, gpu=""):
        self.running = running
        self.model_id = mid
        self.info = info or {}
        self.gpu_name = gpu
        self.base_url = "http://127.0.0.1:8089/v1"
        self.stopped = 0

    def stop(self):
        self.stopped += 1
        self.running = False
        self.model_id = ""
        self.info = {}


def make_card(ready, server, cfg=None):
    """造一张卡片：文件系统与 SERVER 全打桩，不下不启"""
    real = {k: getattr(L, k) for k in ("is_installed", "has_model", "SERVER",
                                       "orphans", "model_total")}
    L.is_installed = lambda: True
    L.has_model = lambda mid: str(mid) in ready
    L.SERVER = server
    L.orphans = lambda: []
    L.model_total = lambda mid: 1000000
    card = LocalLlmCard(cfg or {"settings": {}})
    return card, real


def restore(real):
    for k, v in real.items():
        setattr(L, k, v)


def select(card, mid):
    """把下拉切到指定模型（触发 currentIndexChanged → refresh）"""
    idx = card.cb_model.findData(mid)
    card.cb_model.setCurrentIndex(idx)


def t_card():
    """B. 真实卡片的按钮与提示"""
    # --- 场景 1：未运行，选中的模型已下载 ---
    srv = FakeServer(running=False)
    card, real = make_card({VL, Q35}, srv)
    try:
        select(card, Q35)
        card.refresh()
        check("未运行：按钮是「启动本地服务」",
              card.b_server.text() == T("启动本地服务"), card.b_server.text())
        check("未运行：下载按钮显示「模型已就绪」",
              card.b_model.text() == T("模型已就绪"), card.b_model.text())
        check("未运行：下载按钮不可点", card.b_model.isEnabled() is False)
    finally:
        restore(real)

    # --- 场景 2：运行中，下拉与当前加载一致 ---
    srv = FakeServer(running=True, mid=Q35,
                     info={"name": "Q35", "vision": True, "ctx": 8192})
    card, real = make_card({VL, Q35}, srv)
    try:
        select(card, Q35)
        card.refresh()
        check("同模型：按钮是「停止本地服务」",
              card.b_server.text() == T("停止本地服务"), card.b_server.text())
        check("同模型：不算待切换", card._pending is False)
    finally:
        restore(real)

    # --- 场景 3：运行中 4B，切到已下载的 VL-2B ---
    srv = FakeServer(running=True, mid=Q35,
                     info={"name": "Q35", "vision": True, "ctx": 8192})
    card, real = make_card({VL, Q35}, srv)
    try:
        select(card, VL)
        card.refresh()
        check("换已下载模型：按钮变「应用并重启（约 15 秒）」",
              card.b_server.text() == T("应用并重启（约 15 秒）"),
              card.b_server.text())
        check("换已下载模型：pending 标记为真", card._pending is True)
        check("换已下载模型：提示里出现「已选择」",
              "已选择" in card.lb_hint.text(), card.lb_hint.text())
        check("换已下载模型：提示里带新模型名",
              L.get_model(VL)["name"] in card.lb_hint.text(), card.lb_hint.text())
        check("换已下载模型：底部状态仍是「运行中」",
              card.lb_state.text() == T("本地服务运行中"), card.lb_state.text())
        check("换已下载模型：当前加载行不跟着下拉变（诚实显示在跑的）",
              card.lb_loaded.text() == "Q35", card.lb_loaded.text())
    finally:
        restore(real)

    # --- 场景 4：运行中 4B，切到**未下载**的 VL-2B ---
    srv = FakeServer(running=True, mid=Q35,
                     info={"name": "Q35", "vision": True, "ctx": 8192})
    card, real = make_card({Q35}, srv)          # 只有 4B 下载过
    try:
        select(card, VL)
        card.refresh()
        check("换未下载模型：不算待切换", card._pending is False)
        check("换未下载模型：按钮仍是「停止本地服务」",
              card.b_server.text() == T("停止本地服务"), card.b_server.text())
        check("换未下载模型：下载按钮变「下载模型」且可点",
              card.b_model.text() == T("下载模型") and card.b_model.isEnabled(),
              "%s enabled=%s" % (card.b_model.text(), card.b_model.isEnabled()))
        check("换未下载模型：提示要求先下载",
              "下载" in card.lb_hint.text(), card.lb_hint.text())
        check("换未下载模型：底部仍显示运行中（不被「组件已就绪」盖掉）",
              card.lb_state.text() == T("本地服务运行中"), card.lb_state.text())
    finally:
        restore(real)


def t_toggle():
    """C. 点击按钮的行为：待切换 = 停 + 启；无变更 = 只停"""
    started = []

    class FakeWorker:
        def __init__(self, mid, port, ctx, parent=None):
            started.append(mid)

        class _Sig:
            def connect(self, *a):
                pass

        sig_done = _Sig()

        def start(self):
            pass

    real = {k: getattr(L, k) for k in ("is_installed", "has_model", "SERVER",
                                       "orphans", "model_total")}
    import app.ui.pages.local_llm_card as CARD
    real_worker = CARD._ServerWorker
    ctor_args = []

    class _W(FakeWorker):
        def __init__(self, mid, port=0, ctx=0, parent=None):
            ctor_args.append((mid, port, ctx))

        class _Sig:
            def connect(self, *a):
                pass

        sig_done = _Sig()

        def start(self):
            started.append(ctor_args[-1][0])

    try:
        L.is_installed = lambda: True
        L.has_model = lambda mid: True
        L.orphans = lambda: []
        L.model_total = lambda mid: 1000000
        CARD._ServerWorker = _W

        # 1) 运行中且无变更 → 只停止，不启动
        srv = FakeServer(running=True, mid=Q35, info={"name": "Q35", "ctx": 8192})
        L.SERVER = srv
        card = LocalLlmCard({"settings": {}})
        select(card, Q35)
        card.refresh()
        started.clear()
        card._on_toggle_server()
        check("无变更：调用了 stop", srv.stopped == 1, srv.stopped)
        check("无变更：没有起新进程", started == [], started)

        # 2) 运行中且换了模型 → 先停再起，且起的是**新选的**模型
        srv2 = FakeServer(running=True, mid=Q35, info={"name": "Q35", "ctx": 8192})
        L.SERVER = srv2
        card2 = LocalLlmCard({"settings": {}})
        select(card2, VL)
        card2.refresh()
        check("换模型：按钮确实显示「应用并重启」",
              card2.b_server.text() == T("应用并重启（约 15 秒）"),
              card2.b_server.text())
        started.clear()
        card2._on_toggle_server()
        check("换模型：先 stop 了旧服务", srv2.stopped == 1, srv2.stopped)
        check("换模型：随后用新模型启动了服务", started == [VL], started)
    finally:
        CARD._ServerWorker = real_worker
        for k, v in real.items():
            setattr(L, k, v)


def main():
    print("=" * 62)
    t_pure()
    print("-" * 62)
    t_card()
    print("-" * 62)
    t_toggle()
    print("=" * 62)
    print("结果: %d/%d 通过" % (_N[0] - len(FAILS), _N[0]))
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
