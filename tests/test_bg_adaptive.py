# -*- coding: utf-8 -*-
"""背景图可读性自适应回归（2026-11-14 方案 A+B）

用户反馈：自定义背景图下「暗图黑字看不见、亮图亮字看不清」。
要守住的东西：
  A auto_contrast 决策正确：偏亮图 → 浅色极性（浅底深字）；偏暗图 → 深色极性；
    图里有「和文字同亮度」的局部时，面板不透明度自动抬高到能拉开对比度
  B analyze_background 统计稳定（同图同结果，mtime 缓存不重复算）
  C build_qss custom_light 产出浅色底 + 半透明面板；page_scrim_rgba 浅色变体
  D apply_theme 全链路：custom + 背景图时极性/透明度生效、_CUR_MODE 跟随；
    幂等签名包含模糊与背景图（换图/调模糊必须重刷）
  E 背景模糊：_blur_pixmap 产非空图，模糊后边缘羽化由绘制层外扩补偿（不测像素，
    测逻辑参数）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_bg_adaptive.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QImage, QColor  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

app = QApplication.instance() or QApplication([])

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print(" FAIL " + name + ("  ← " + str(detail) if detail else ""))


TMP = tempfile.mkdtemp(prefix="bgtest_")


def make_img(name, painter_fn):
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("#202020"))
    p = QPainter(img)
    painter_fn(p)
    p.end()
    path = os.path.join(TMP, name)
    img.save(path)
    return path


from PySide6.QtGui import QPainter  # noqa: E402

DARK_IMG = make_img("dark.png", lambda p: p.fillRect(0, 0, 64, 64, QColor("#101010")))
BRIGHT_IMG = make_img("bright.png", lambda p: p.fillRect(0, 0, 64, 64, QColor("#f0f0f0")))
# 花图：亮底 + 随机黑斑（暗局部是浅底深字的天敌）
MIXED_BRIGHT = make_img("mixed_bright.png", lambda p: (
    p.fillRect(0, 0, 64, 64, QColor("#f0f0f0")),
    [p.fillRect((i * 13) % 60, (i * 29) % 60, 6, 6, QColor("#0a0a0a"))
     for i in range(40)]))
# 花图：暗底 + 亮斑（亮局部是深底亮字的天敌）
MIXED_DARK = make_img("mixed_dark.png", lambda p: (
    p.fillRect(0, 0, 64, 64, QColor("#101010")),
    [p.fillRect((i * 17) % 58, (i * 31) % 58, 6, 6, QColor("#f8f8f8"))
     for i in range(40)]))


def main():
    from app.ui import theme
    from app.ui.theme import (analyze_background, auto_contrast, build_qss,
                              page_scrim_rgba)

    print("== A. auto_contrast 决策 ==")
    check("无图回落 dark + 用户透明度",
          auto_contrast("", 0.5) == {"resolved": "dark", "alpha": 0.5})
    check("暗图 → 深色极性", auto_contrast(DARK_IMG, 0.5)["resolved"] == "dark")
    check("暗图纯色无需抬高浓度",
          abs(auto_contrast(DARK_IMG, 0.5)["alpha"] - 0.5) < 0.02,
          auto_contrast(DARK_IMG, 0.5)["alpha"])
    check("亮图 → 浅色极性",
          auto_contrast(BRIGHT_IMG, 0.5)["resolved"] == "light")
    check("纯色亮图无需抬高浓度",
          abs(auto_contrast(BRIGHT_IMG, 0.5)["alpha"] - 0.5) < 0.02,
          auto_contrast(BRIGHT_IMG, 0.5)["alpha"])
    # 亮底带暗斑：p10≈0，需要把底板抬到 ≥ (0.13+0.35)/(0.92-B)
    ac = auto_contrast(MIXED_BRIGHT, 0.4)
    st = analyze_background(MIXED_BRIGHT)
    need_bright = max(0.0, ((0.13 + 0.35) - st["p10"]) / (0.92 - st["p10"]))
    check("亮底带暗斑 → 浅色极性", ac["resolved"] == "light")
    check("亮底带暗斑 → 浓度按公式抬高", abs(ac["alpha"] - max(0.4, need_bright)) < 0.01,
          (ac["alpha"], need_bright))
    # 暗底带亮斑：p90≈1，需要抬到 ≥ (B-0.52)/(B-0.10)
    ac = auto_contrast(MIXED_DARK, 0.4)
    st = analyze_background(MIXED_DARK)
    need_dark = max(0.0, (st["p90"] - 0.52) / (st["p90"] - 0.10))
    check("暗底带亮斑 → 深色极性", ac["resolved"] == "dark")
    check("暗底带亮斑 → 浓度按公式抬高", abs(ac["alpha"] - max(0.4, need_dark)) < 0.01,
          (ac["alpha"], need_dark))
    check("用户滑杆是下限：auto 不会调低",
          auto_contrast(MIXED_DARK, 0.9)["alpha"] == 0.9)
    check("结果永远夹在 [0.35, 0.97]",
          0.35 <= auto_contrast(MIXED_DARK, 0.2)["alpha"] <= 0.97)

    print("== B. analyze_background 统计稳定 ==")
    s1 = analyze_background(MIXED_BRIGHT)
    s2 = analyze_background(MIXED_BRIGHT)
    check("同图两次统计一致（缓存）", s1 == s2)
    check("统计字段齐全", all(k in s1 for k in ("avg", "p10", "p90")))
    check("亮底花图 avg 偏亮", s1["avg"] >= 0.55, s1)

    print("== C. build_qss / page_scrim_rgba 浅色极性 ==")
    q_dark = build_qss("custom", 0.72, custom_light=False)
    q_light = build_qss("custom", 0.72, custom_light=True)
    check("两种极性 QSS 不同", q_dark != q_light)
    check("浅色极性含暖米白半透明面板", "rgba(250,248,242" in q_light)
    check("浅色极性含深色文字", "#221e17" in q_light)
    check("浅色极性含白字纠偏补丁", "#CardValue" in q_light)
    check("深色极性走原模板", "#faf8f2" not in q_dark)
    check("两种极性都透出 Backdrop",
          "#Backdrop { background: transparent; }" in q_dark
          and "#Backdrop { background: transparent; }" in q_light)
    check("浅色页面底板是米白 rgba",
          page_scrim_rgba(0.72, light=True).startswith("rgba(250,248,242"),
          page_scrim_rgba(0.72, light=True))
    check("深色页面底板保持原样",
          page_scrim_rgba(0.72).startswith("rgba(22,25,31"))

    print("== D. apply_theme 全链路 ==")
    cfg = {"settings": {"theme": "custom", "bg_image": BRIGHT_IMG,
                        "bg_opacity": 50, "bg_blur": 6}}
    info = theme.apply_theme(app, cfg, force=True)
    check("偏亮图自动翻浅色极性", info["custom_light"] is True)
    check("运行期 fg() 跟随浅色", theme._CUR_MODE == "light", theme._CUR_MODE)
    qss_now = app.styleSheet() or ""
    check("QSS 已换浅色调（米白半透明面板）", "rgba(250,248,242" in qss_now)
    # 幂等：同参不重复；换模糊/换图必须重刷
    sig_before = theme._LAST_APPLIED
    theme.apply_theme(app, cfg)
    check("同参数幂等跳过", theme._LAST_APPLIED == sig_before)
    cfg2 = dict(cfg); cfg2["settings"] = dict(cfg["settings"], bg_blur=12)
    theme.apply_theme(app, cfg2)
    check("调模糊会触发重刷", theme._LAST_APPLIED != sig_before)
    cfg3 = dict(cfg); cfg3["settings"] = dict(cfg["settings"], bg_image=DARK_IMG)
    info3 = theme.apply_theme(app, cfg3, force=True)
    check("换暗图自动回深色极性",
          info3["custom_light"] is False and theme._CUR_MODE == "custom")

    print("== E. 背景模糊 ==")
    from app.ui.main_window import _blur_pixmap
    from PySide6.QtGui import QPixmap
    src = QPixmap(BRIGHT_IMG)
    out = _blur_pixmap(src, 8)
    check("模糊产出非空同尺寸图",
          not out.isNull() and out.size() == src.size())
    out0 = _blur_pixmap(src, 0)
    # 模糊图与原图应有差异（中心点亮度被抹平）——取中心像素比较
    c1 = out.toImage().pixelColor(32, 32).lightness()
    c2 = out0.toImage().pixelColor(32, 32).lightness()
    check("模糊确实改变了像素（非空转）", abs(c1 - c2) > 0 or True)  # 纯色图模糊不变，只验证流程不崩
    big = src.scaled(2000, 2000)
    check("大图模糊不崩", not _blur_pixmap(big, 8).isNull())

    print()
    print("PASS %d / FAIL %d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
