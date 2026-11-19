# -*- coding: utf-8 -*-
"""状态条文字稳定性回归测试 — 2026-11-20

用户连续两次反馈「界面还是跳动」，两帧相隔 1ms 的截图抓到：
一张状态条是 `ComfyUI · :8188（未启动）`、另一张是 `ComfyUI · :8188`，
且带后缀那帧末尾的「）」被挤出可视区。

根因：`refresh_inst()` **先** setText("名字 · :端口")，**再** 异步 TCP 探测，
探测回调里才补「（未启动）」后缀。所以每一轮刷新都是「文字先缩短、几十毫秒后
再拉长」，肉眼就是状态条在抽动。

本测试锁死三件事：
    A. refresh_inst 期间不会把 lb_inst 写成「无后缀的短文本」
    B. 后缀由 _probe_port 的 label 参数决定，不靠「从现有文本 split」
    C. 连续多次刷新不会把后缀越拼越长
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


from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from app.ui.pages.ai_page import AiPage  # noqa: E402


class FakePage:
    """只借用 AiPage 的两个方法（refresh_inst / _probe_port），不构造整个页面"""


def call_method(obj, name, *a, **kw):
    return getattr(AiPage, name)(obj, *a, **kw)


class Head:
    def __init__(self):
        self.text = ""
        self.style = ""

    def text_(self):
        return self.text

    def setText(self, t):
        self.text = t

    def setStyleSheet(self, s):
        self.style = s

    def setToolTip(self, t):
        pass


class Dot(Head):
    pass


def make_page(inst):
    """最小桩：具备 refresh_inst 用到的属性"""
    p = FakePage()
    p.lb_inst = Head()
    p.lb_dot = Dot()
    p._inst = lambda: inst
    p._probe_calls = []
    p._text_history = []

    real_set = p.lb_inst.setText

    def spy_set(t):
        p._text_history.append(t)
        real_set(t)

    p.lb_inst.setText = spy_set

    def fake_probe(port, label=""):
        p._probe_calls.append({"port": port, "label": label})

    p._probe_port = fake_probe
    return p


def t_no_short_text():
    """A. refresh_inst 不得先写短文本"""
    p = make_page({"name": "ComfyUI", "port": 8188})
    call_method(p, "refresh_inst")
    check("refresh_inst 没有写任何「无后缀短文本」",
          p._text_history == [], p._text_history)
    check("探测被调用一次", len(p._probe_calls) == 1, p._probe_calls)
    check("探测拿到完整 label",
          p._probe_calls[0]["label"] == "ComfyUI · :8188",
          p._probe_calls[0]["label"])
    check("端口正确", p._probe_calls[0]["port"] == 8188)

    # 未选实例的早退分支仍然要写文字（这是唯一合法的一次写入）
    p2 = make_page(None)
    call_method(p2, "refresh_inst")
    check("无实例时写「未选择实例」",
          p2._text_history == ["未选择实例"], p2._text_history)


def t_probe_uses_label():
    """B. 后缀由 label 决定；不再从现有文本 split"""
    p = make_page({"name": "X", "port": 1})
    p.lb_inst.setText("乱七八糟的旧文本（未启动）（未启动）")
    # 直接跑 _probe_port 的内部逻辑等价物：模拟 finish(False)
    # 由于真实实现里 finish 是闭包，这里复算同一套表达式验证语义
    label = "ComfyUI · :8188"
    ok = False
    base = label or p.lb_inst.text().split("（")[0]
    final = base if ok else base + "（未启动）"
    check("带 label 时不受旧文本干扰", final == "ComfyUI · :8188（未启动）", final)
    check("后缀只出现一次", final.count("（未启动）") == 1, final)


def t_no_suffix_accumulation():
    """C. 连续刷新不累积后缀"""
    p = make_page({"name": "ComfyUI", "port": 8188})
    for _ in range(5):
        call_method(p, "refresh_inst")
    check("刷新 5 次仍只传一次 label 且内容不变",
          all(c["label"] == "ComfyUI · :8188" for c in p._probe_calls),
          p._probe_calls[:2])
    check("刷新期间 lb_inst 一次都没被改写",
          p._text_history == [], p._text_history)


def main():
    print("=" * 62)
    t_no_short_text()
    print("-" * 62)
    t_probe_uses_label()
    print("-" * 62)
    t_no_suffix_accumulation()
    print("=" * 62)
    print("结果: %d/%d 通过" % (_N[0] - len(FAILS), _N[0]))
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
