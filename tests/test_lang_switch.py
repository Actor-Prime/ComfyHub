# -*- coding: utf-8 -*-
"""语言切换回归（2026-11-02）

背景：语言包上线后发现两个问题，这里做回归：
  1. 侧栏导航项（QListWidget item）没进翻译扫描范围 —— 英文模式下左侧仍是中文，
     用户看着就是"切了没反应"。翻译现在按行拆（"🏠\\n工作台" → "🏠\\nWorkbench"）。
  2. 之前只支持「中文 → 英文」，反向没有映射表，切回中文只能重启（界面残留英文）。
     现在 _EN_REV 反向表 + apply_language 双向替换，设置页保存即生效。

覆盖：
  A 设置页保存切英文 → 导航/标题立即变英文
  B 再保存切回中文 → 立即恢复（不重启）
  C apply_language 直接双向调用
  D 词表反向表一致性（每条英文都能翻回中文，不出现空值）

用法：.venv_build/Scripts/python.exe tests/test_lang_switch.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox

app = QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: None)

from app import i18n                      # noqa: E402
from app.config import load_config        # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import apply_theme      # noqa: E402

OS_CFG = load_config()
apply_theme(app, OS_CFG)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def pump(n=40):
    for _ in range(n):
        app.processEvents()


win = MainWindow()
win.show()
pump(30)

sp = win.settings_page
lang_cb = None
for c in sp.findChildren(QComboBox):
    if any("English" in c.itemText(k) for k in range(c.count())):
        lang_cb = c
        break


def nav_text():
    return win.nav.item(0).text().replace("\n", " ")


# ---------------------------------------------------------- A/B 保存即生效
check("0a 语言下拉存在", lang_cb is not None)
if lang_cb:
    lang_cb.setCurrentIndex(1)          # English
    sp._save()
    pump()
    t = nav_text()
    check("1a 切英文：侧栏导航立即变英文", "Workbench" in t, t)
    check("1b 切英文：窗口标题变英文",
          "Multi-Instance" in win.windowTitle(), win.windowTitle())

    lang_cb.setCurrentIndex(0)          # 简体中文
    sp._save()
    pump()
    t = nav_text()
    check("2a 切回中文：侧栏导航立即恢复", "工作台" in t, t)
    check("2b 切回中文：窗口标题恢复", "多实例管理器" in win.windowTitle(), win.windowTitle())

# ---------------------------------------------------------- C 直接调用
i18n.apply_language(win, "en")
pump(20)
check("3a apply_language(en) 生效", "Workbench" in nav_text(), nav_text())
i18n.apply_language(win, "zh")
pump(20)
check("3b apply_language(zh) 反向恢复", "工作台" in nav_text(), nav_text())

# ---------------------------------------------------------- D 词表一致性
bad = [k for k, v in i18n._EN.items() if not isinstance(v, str) or not v.strip()]
check("4a 词表无空译文", not bad, str(bad[:3]))
missing = [v for v in i18n._EN.values() if v not in i18n._EN_REV]
check("4b 反向表覆盖全部译文", not missing, str(missing[:3]))
# 译文有重复时反向表只保留第一个键，所以按"往返一致"校验（v→k→v 必须回到 v）
check("4c 反向表往返一致",
      all(i18n._EN.get(i18n._EN_REV[v]) == v for v in list(i18n._EN.values())[:50]))

# 复原：语言回到中文并落盘
win.cfg.setdefault("settings", {})["lang"] = "zh"
try:
    from app.config import save_config
    save_config(win.cfg)
except Exception:
    pass

fails = [r for r in RESULTS if not r[1]]
print("=" * 48)
print(f"语言切换回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
for n, _, d in fails:
    print(f"  FAIL {n}  [{d}]")
sys.stdout.flush()
os._exit(1 if fails else 0)
