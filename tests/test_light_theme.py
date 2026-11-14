# -*- coding: utf-8 -*-
"""浅色主题回归（2026-11-14 用户反馈「太白刺眼、按钮没色差」）

三条要守住的东西：
  A 浅色底必须是**暖米白**，不能出现纯白大色块（#ffffff 只在强调按钮上作前景色）
  B 控件级样式表 / rgba 面板 / 运行期 QColor 都要跟着主题走 ——
    以前浅色下会话列表、日志、状态卡数值全是深色残留
  C 正文/次要/链接/语义色在米白底上对比度 ≥ 4.5（WCAG AA 正文），
    按钮底色与卡片底色有可见色差

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_light_theme.py
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.ui import theme  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print(" FAIL " + name + ("  ← " + str(detail) if detail else ""))


def lum(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def rule_bg(qss, selector):
    """取某选择器块里的 background 颜色（浅色主题下用来判断底色亮度）"""
    ms = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
    if not ms:
        return None
    # 级联：同选择器后写的规则生效，取最后一条
    b = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{6})", ms[-1])
    return b.group(1).lower() if b else None


def rule_bg_exact(qss, selector):
    """只认选择器完全等于 selector 的块（先剥注释，注释会粘在选择器前面）"""
    clean = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", clean):
        if sel.strip() != selector:
            continue
        b = re.search(r"background(?:-color)?:\s*(#[0-9a-fA-F]{6})", body)
        if b:
            return b.group(1).lower()
    return None


def main():
    light = theme.build_qss("light")
    dark = theme.build_qss("dark")

    print("== A. 浅色底是暖米白，不是冷白 ==")
    check("浅色 QSS 与深色不同", light != dark)
    page = rule_bg(light, "QWidget")
    check("页面底色存在", bool(page), page)
    check("页面底色不是纯白", page != "#ffffff", page)
    check("页面底亮度不过曝（<0.95）", lum(page) < 0.95, "%.3f" % lum(page))
    # 暖调：红 >= 绿 >= 蓝（米白偏黄，冷白是蓝最高）
    r, g, b = (int(page[i:i + 2], 16) for i in (1, 3, 5))
    check("底色调是暖的（R>=G>=B，米白）", r >= g >= b, (r, g, b))
    card = rule_bg(light, "#Card")
    check("卡片色不是纯白", card and card != "#ffffff", card)
    check("卡片比页面更亮一档（层次）", card and lum(card) > lum(page),
          "%s vs %s" % (card, page))
    check("浅色里没有纯白大底（background: #ffffff）",
          "background: #ffffff" not in light.lower())

    print("== B. 控件级样式表 / rgba / 运行期色跟随主题 ==")
    theme._CUR_MODE = "light"
    check("hex 会换成浅色",
          theme.tint("color:#1b2029") == "color:#faf8f2",
          theme.tint("color:#1b2029"))
    check("rgba 面板会换成浅色",
          theme.tint("background: rgba(27,32,41,0.92)") ==
          "background: rgba(250,248,242,0.92)",
          theme.tint("background: rgba(27,32,41,0.92)"))
    check("运行期色 fg() 换成深色",
          theme.fg("#4cd47c") == theme._LIGHT_MAP["#4cd47c"], theme.fg("#4cd47c"))
    theme._CUR_MODE = "dark"
    check("深色模式下 tint 原样返回",
          theme.tint("background:#1b2029") == "background:#1b2029")

    # 真实控件：切主题后已设过的样式要重染
    theme.apply_theme(app, {"settings": {"theme": "dark"}}, force=True)
    w = QWidget()
    w.setStyleSheet("QWidget{background:#1b2029; color:#e6edf7;}")
    check("深色下控件样式保持原样", "#1b2029" in w.styleSheet(), w.styleSheet())
    theme.apply_theme(app, {"settings": {"theme": "light"}}, force=True)
    check("切浅色后控件样式被重染",
          "#faf8f2" in w.styleSheet() and "#1b2029" not in w.styleSheet(),
          w.styleSheet())
    theme.apply_theme(app, {"settings": {"theme": "dark"}}, force=True)
    check("切回深色样式复原", "#1b2029" in w.styleSheet(), w.styleSheet())

    print("== C. 对比度与按钮色差 ==")
    M = theme._LIGHT_MAP
    pairs = [
        ("正文 on 卡片", M["#e6edf7"], card, 7.0),
        ("次要文字 on 卡片", M["#94a2b4"], card, 4.5),
        ("链接/强调 on 卡片", M["#4c8dff"], card, 4.5),
        ("成功色 on 卡片", M["#4cd47c"], card, 4.5),
        ("危险色 on 卡片", M["#f0616d"], card, 4.5),
        ("弱提示 on 页面", M["#8a93a3"], page, 4.5),
    ]
    for name, fg_, bg_, need in pairs:
        c = contrast(fg_, bg_)
        check("%s 对比度 ≥ %.1f（实测 %.2f）" % (name, need, c), c >= need,
              "%.2f" % c)

    btn = rule_bg_exact(light, "QPushButton")
    check("按钮底色与卡片底色不同", btn and btn != card, "%s vs %s" % (btn, card))
    check("按钮比卡片深一档（看得出是控件）",
          btn and lum(btn) < lum(card),
          "%s vs %s" % (btn, card))

    print("== D. 白字压在浅底上的老坑 ==")
    _ms = re.findall(r"#StatValue\s*\{([^}]*)\}", light)
    m = type("M", (), {"group": lambda self, i, _t=_ms[-1]: _t})() if _ms else None
    check("状态卡大数值改成深色", m and "#ffffff" not in m.group(1), m.group(1) if m else None)
    _ms2 = re.findall(r"QTableWidget::item:selected[^{]*\{([^}]*)\}", light)
    m2 = type("M", (), {"group": lambda self, i, _t=_ms2[-1]: _t})() if _ms2 else None
    check("表格选中行文字改成深色", m2 and "#ffffff" not in m2.group(1),
          m2.group(1) if m2 else None)

    print("\nPASS %d / FAIL %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
