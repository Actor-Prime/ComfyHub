# -*- coding: utf-8 -*-
"""主题系统 + 中英语言包回归（2026-11-01）

覆盖：
1. 主题：dark 原样 / light 换调色板（不再出现深色底）/ custom 面板 rgba + 背景层透明
2. 模式解析：system 折 light|dark、未知值兜底 dark、透明度越界夹取
3. apply_theme 不崩、返回值正确
4. i18n：中文恒等、英文查表、未收录原样、控件树扫描（按钮/标签）、下拉不误伤
5. 设置页：主题四项、自定义组显隐、保存持久化、语言改动提示重启
6. 主窗：自定义主题（背景图不存在）构建不崩、背景层就位

用法：.venv_build/Scripts/python.exe tests/test_theme_i18n.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QComboBox,
    QMessageBox,
)

app = QApplication([])
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.2):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


_shown = []
QMessageBox.information = staticmethod(lambda *a, **k: _shown.append(("info", a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: _shown.append(("warn", "")))

from app.ui import theme as th
from app import i18n

# -------------------------------------------------- 1. 主题 QSS
check("1a dark 原样返回模板", th.build_qss("dark") == th.DARK_QSS)

light = th.build_qss("light")
check("1b light 换成浅底", "#f7f8fa" in light and "#1e2229" not in light)
check("1c light 文本转深色", "#1f2430" in light and "#d8dee6" not in light)

custom = th.build_qss("custom", 0.5)
check("1d custom 面板转 rgba", "rgba(" in custom)
check("1e custom 背景层透明规则", "#Backdrop { background: transparent; }" in custom)
import re as _re
check("1f custom 用百分比透明度（Qt 只认 % 形式）",
      bool(_re.search(r"rgba\(\d+,\d+,\d+,\d+%\)", custom)), custom[:120])
check("1f2 不透明度 100% → 面板不透明",
      "rgba(30,34,41,100%)" in th.build_qss("custom", 1.0))
check("1f3 低不透明度确实更通透",
      "rgba(30,34,41,15%)" in th.build_qss("custom", 0.35))

# -------------------------------------------------- 2. 模式解析与配置兜底
check("2a system 折成 light|dark", th.resolve_mode("system") in ("light", "dark"))
check("2b 未知模式兜底 dark", th.resolve_mode("nonsense") == "dark")
check("2c 默认配置 = dark/72%", th.theme_conf({})[:2] == ("dark", 0.72))
check("2d 透明度下限夹 35%",
      th.theme_conf({"settings": {"bg_opacity": 5}})[1] == 0.35)
check("2e 透明度上限夹 100%",
      th.theme_conf({"settings": {"bg_opacity": 500}})[1] == 1.0)
check("2f 非法模式回落 dark",
      th.theme_conf({"settings": {"theme": "rainbow"}})[0] == "dark")

info = th.apply_theme(app, {"settings": {"theme": "light"}})
check("2g apply_theme 返回 resolved", info.get("resolved") == "light", str(info))
check("2h apply_theme 真的换掉了样式", "#f7f8fa" in app.styleSheet())
info2 = th.apply_theme(app, {"settings": {"theme": "dark"}})
check("2i 可以切回深色", info2.get("resolved") == "dark" and app.styleSheet() == th.DARK_QSS)

# -------------------------------------------------- 3. i18n
check("3a 中文模式恒等", i18n.T("工作台") == "工作台")
i18n.set_lang("zh")
check("3b set_lang(zh) 恒等", i18n.T("设置") == "设置")
i18n.set_lang("en")
check("3c 英文查表", i18n.T("工作台") == "Workbench", i18n.T("工作台"))
check("3d 未收录原样返回", i18n.T("这条没翻译") == "这条没翻译")
check("3e get_lang 反映设置", i18n.get_lang() == "en")

host = QWidget()
lay = QVBoxLayout(host)
lb = QLabel("主题:")
btn = QPushButton("保存设置")
cb = QComboBox()
cb.addItem("深色")
lay.addWidget(lb)
lay.addWidget(btn)
lay.addWidget(cb)
n = i18n.apply_language(host, "en")
check("3f 控件树扫描替换标签", lb.text() == "Theme:", lb.text())
check("3g 控件树扫描替换按钮", btn.text() == "Save settings", btn.text())
check("3h 返回替换条数", n >= 2, str(n))
check("3i 下拉不被误译（保留取值语义）", cb.itemText(0) == "深色", cb.itemText(0))

n2 = i18n.apply_language(host, "zh")
check("3j 中文模式不替换", n2 == 0 and lb.text() == "Theme:")

# -------------------------------------------------- 4. 设置页
from app.ui.pages import settings_page as sp_mod


class FakeMain:
    def current_inst(self):
        return None


sp_mod.save_config = lambda cfg: None
cfg = {"settings": {}}
page = sp_mod.SettingsPage(cfg, FakeMain())
datas = [page.cb_theme.itemData(i) for i in range(page.cb_theme.count())]
check("4a 主题四项齐全", datas == ["dark", "light", "system", "custom"], str(datas))
check("4b 默认深色", page.cb_theme.currentData() == "dark")
check("4c 非自定义时隐藏背景行", page.box_custom.isHidden())
page.cb_theme.setCurrentIndex(page.cb_theme.findData("custom"))
pump(0.05)
# 注意：用 isHidden()（显式隐藏标志）而不是 isVisible()——页面本身没 show()，
# isVisible 对整条祖先链恒 False，会误判（2026-10-31 踩过同类假绿坑）
check("4d 选自定义后展开背景行", not page.box_custom.isHidden())
check("4e 语言下拉两项", [page.cb_lang.itemData(i) for i in range(page.cb_lang.count())] == ["zh", "en"])
check("4f 背景图默认空", page._bg_value == "")
check("4g 透明度默认 72", page.sl_alpha.value() == 72, str(page.sl_alpha.value()))

page.sl_alpha.setValue(50)
page.cb_lang.setCurrentIndex(page.cb_lang.findData("en"))
page._save()
st = cfg["settings"]
check("4h 保存主题=custom", st.get("theme") == "custom", str(st.get("theme")))
check("4i 保存透明度=50", st.get("bg_opacity") == 50, str(st.get("bg_opacity")))
check("4j 保存语言=en", st.get("lang") == "en", str(st.get("lang")))
check("4k 语言改动提示重启",
      any("重启" in str(x[1]) for x in _shown), str(_shown[-1:]))

page._clear_bg()
check("4l 清除背景图置空", page._bg_value == "")

# -------------------------------------------------- 5. 主窗（自定义主题 + 缺失底图）
from app.config import load_config, save_config as real_save  # noqa: E402
import app.ui.pages.settings_page as sp2  # noqa: E402
sp2.save_config = real_save

cfg2 = load_config()
cfg2.setdefault("settings", {})
cfg2["settings"].update({"close_behavior": "close", "theme": "custom",
                         "bg_image": "F:/definitely_not_here/bg.png",
                         "bg_opacity": 60})
from app.ui import main_window as mw  # noqa: E402
mw.load_config = lambda: cfg2
mw_win = mw.MainWindow()
pump(0.2)
check("5a 自定义主题构建不崩", mw_win is not None)
check("5b 背景层就位", mw_win._backdrop.objectName() == "Backdrop")
check("5c 底图缺失不抛异常（空图兜底）", mw_win._backdrop._pix.isNull())
mw_win.cfg.setdefault("settings", {})["close_behavior"] = "close"
mw_win.close()

# -------------------------------------------------- 6. 英文包覆盖率
import ast as _ast
import pathlib as _pathlib
import re as _re2

from app.i18n_en import EN_DICT

_CTOR = {"QLabel", "QPushButton", "QCheckBox", "QGroupBox", "QRadioButton", "QToolButton"}
_CALLS = {"setText", "setTitle", "setPlaceholderText", "setToolTip",
          "setWindowTitle", "addTab", "addRow", "addItem"}
_CJK = _re2.compile(r"[\u4e00-\u9fff]")
_found = {}
for _p in _pathlib.Path("app/ui").rglob("*.py"):
    try:
        _t = _ast.parse(_p.read_text(encoding="utf-8"))
    except Exception:
        continue
    for _n in _ast.walk(_t):
        if not isinstance(_n, _ast.Call):
            continue
        _fn = _n.func
        _nm = getattr(_fn, "attr", None) or getattr(_fn, "id", None)
        if _nm not in _CTOR and _nm not in _CALLS:
            continue
        for _a in list(_n.args) + [k.value for k in _n.keywords]:
            if (isinstance(_a, _ast.Constant) and isinstance(_a.value, str)
                    and _CJK.search(_a.value) and len(_a.value) < 90):
                _found.setdefault(_a.value, _p.name)

_missing = [k for k in _found if k not in EN_DICT and k not in {"简体中文"}]
check("6a 可扫描文案全部有英文", not _missing, str(_missing[:3]))
check("6b 词表规模 ≥ 300", len(EN_DICT) >= 300, str(len(EN_DICT)))
i18n.set_lang("en")
check("6c 抽查译文", i18n.T("插件市场") == "Marketplace"
      and i18n.T("主题:") == "Theme:" and i18n.T("加载更多 ↓") == "Load more ↓")
i18n.set_lang("zh")

# -------------------------------------------------- 汇总
ok = sum(1 for _, c, _ in RESULTS if c)
print(f"\n{RESULTS and ok}/{len(RESULTS)} passed")
if ok != len(RESULTS):
    print("FAILED:")
    for n, c, d in RESULTS:
        if not c:
            print("  -", n, d)
sys.exit(0 if ok == len(RESULTS) else 1)
