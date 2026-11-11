# -*- coding: utf-8 -*-
"""标签页（QTabBar）样式回归（2026-11-12 用户报障「环境管理页 tab 像 UI 缺失」）。

事故：未选中 tab 是 background: transparent + 透明边框，深色页面底色上只剩几行
灰字，整条标签栏看不出是个控件 —— 用户以为 UI 丢了。修复给未选中态补了
「比页面亮一档」的底 + 细边框，选中态保持与面板同色 + 2px 蓝色指示条。

断言（改 QSS 时最容易踩的四个点）：
  A. 未选中 tab 不得是 background: transparent，且要有底色 + 边框
  B. 选中 tab 必须是面板色 + 蓝色底部指示条（与下方 pane 连通的视觉）
  C. 新增颜色必须都在 _LIGHT_MAP 里（否则浅色主题残留深色块，主题铁律）
  D. 浅色主题下 tab 配色确实换成浅色系

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_tab_style.py
"""
import os
import re
import sys

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


def block(qss: str, selector: str) -> str:
    """抓某个选择器的大括号内容（够用即可，不追求完整 CSS 解析）"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
    return m.group(1) if m else ""


def main():
    from app.ui import theme as th

    dark = th.build_qss("dark")
    tab = block(dark, "QTabBar::tab")
    sel = block(dark, "QTabBar::tab:selected")

    print("== A. 未选中 tab 的可辨识性 ==")
    check("未选中 tab 不再是 transparent",
          "background: transparent" not in tab, tab.strip()[:80])
    check("未选中 tab 有底色（#171c23）", "#171c23" in tab, tab.strip()[:120])
    check("未选中 tab 有边框（#2c3442）", "#2c3442" in tab, tab.strip()[:120])
    check("未选中 tab 有圆角", "border-top-left-radius" in tab and
          "border-top-right-radius" in tab, tab.strip()[:120])

    print("== B. 选中 tab 与面板连通 ==")
    check("选中 tab 底色=面板色 #1b2029", "#1b2029" in sel, sel.strip()[:120])
    check("选中 tab 有蓝色底部指示条",
          "#4c8dff" in sel and "border-bottom" in sel, sel.strip()[:120])
    pane = block(dark, "QTabWidget::pane")
    check("pane 与选中 tab 同底色", "#1b2029" in pane, pane.strip()[:80])

    print("== C. 新颜色都在 _LIGHT_MAP（主题铁律）==")
    used = set(re.findall(r"#[0-9a-fA-F]{6}", tab + sel))
    missing = [c for c in used if c.lower() not in
               {k.lower() for k in th._LIGHT_MAP}]
    check("tab 用色全部有浅色映射", not missing, str(missing))

    print("== D. 浅色主题真的换了色 ==")
    light = th.build_qss("light")
    ltab = block(light, "QTabBar::tab")
    lsel = block(light, "QTabBar::tab:selected")
    check("浅色下未选中 tab 不再是深色 #171c23", "#171c23" not in ltab, ltab.strip()[:80])
    check("浅色下未选中 tab 是浅灰", th._LIGHT_MAP["#171c23"] in ltab,
          ltab.strip()[:80])
    check("浅色下选中 tab 是白色面板", th._LIGHT_MAP["#1b2029"] in lsel,
          lsel.strip()[:80])

    print(f"\n===== 标签页样式 =====\n通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
