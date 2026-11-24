# -*- coding: utf-8 -*-
"""QA 独立验证：修 D-03 时新引入的「下拉翻译」不得写坏配置（P0 风险面）

## 为什么要单独验这个

工程师修 D-03 时给 `_translate_tree` 加了 QComboBox 分支。但项目里有**大量下拉是
`currentText()` 直接落盘**的（`vram_mode`、`cross_attn`、`preview_method`、`precision`、
`cuda_malloc`、`mirror`、`ai_model`、`deps_mode`…）。如果全局翻译，用户切一次英文再点保存，
`vram_mode` 就会从「自动」变成「Auto」写进配置文件，之后所有按中文匹配的地方全部失配。

工程师的缓解是**逐控件显式登记**：只有「item 用 itemData 存值、取值一律走 currentData()」
的下拉才 `setProperty("i18n_items", True)`，`_translate_tree` 只翻登记过的。

这个缓解**必须验**，而且只能验两件事：
  ① 登记过的下拉，英文模式下确实翻了（否则 D-03 假绿）；
  ② **没登记的下拉，英文模式下必须原样是中文**（否则就是 P0 配置损坏）。

②比①重要：①坏了是显示问题，②坏了是用户数据被改坏。

## 做法

真建窗 → 切英文 → 逐个下拉读 `itemText()`，看有没有被误翻；
再走一次真实保存，把 config 落盘后重新 `load_config()`，比对枚举值是否原样。

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_i18n_save.py
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# D-09：退出前先销毁 QApplication，否则进程结束阶段偶发 0xC0000005
from _qa_exit import clean_exit        # noqa: E402

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_i18nsave_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)

# 本机实例：vram_mode / cross_attn / preview_method / precision 都是中文枚举值，
# 且都是 currentText() 落盘 —— 正是最容易被误翻写坏的那批。
_SEED = {
    "version": 1,
    "last_instance_id": "loc",
    "instances": [{
        "id": "loc", "name": "L", "kind": "local",
        "comfyui_path": "C:/fake/ComfyUI", "python_path": "C:/fake/python.exe",
        "port": 8188, "vram_mode": "自动", "cross_attn": "自动",
        "preview_method": "自动", "precision": "自动", "cuda_malloc": "自动",
        "extra_args": "", "models_dir": "", "engine_backend": "comfyui",
        "engine_index": 0, "auth_type": "none",
    }],
    "settings": {"lang": "en", "close_behavior": "close", "mirror": "阿里云",
                 "ai_model": "", "theme": "dark"},
}
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(_SEED, f, ensure_ascii=False)

from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox   # noqa: E402

app = QApplication.instance() or QApplication([])

CJK = re.compile(r"[\u4e00-\u9fff]")
res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


def cn(text):
    return bool(CJK.search(str(text or "")))


def combo_items(cb):
    return [cb.itemText(i) for i in range(cb.count())]


# =====================================================================
print("== A. 登记与未登记的边界：哪些下拉被翻了 ==")

from app import i18n                                              # noqa: E402
from app.ui.main_window import MainWindow                          # noqa: E402

w = MainWindow()
_ids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_ids.index("loc"))
w.comfyui_page.load_current()
app.processEvents()

# 切英文**之前**先把每个下拉的选项记账：A3 的判据是「没登记的下拉，切前切后一字不差」。
# （不能只看「现在是不是中文」——有些下拉本来就是 ASCII，如 L1/L2、checkpoints/loras，
#   那样会把正常值当成「被误翻」误报。）
_before_items = {}
for _cb in w.findChildren(QComboBox):
    if _cb.count():
        _before_items[id(_cb)] = combo_items(_cb)

i18n.apply_language(w, "en")
app.processEvents()

_flagged, _unflagged = [], []
for cb in w.findChildren(QComboBox):
    if not cb.count():
        continue
    _row = (cb, _before_items.get(id(cb), []))
    if bool(cb.property("i18n_items")):
        _flagged.append((cb, combo_items(cb)))
    else:
        _unflagged.append(_row)

check("A1 确实有登记过的下拉（否则 D-03 就是假绿）", len(_flagged) > 0,
      f"登记={len(_flagged)} 未登记={len(_unflagged)}")

# ---- A2：登记过的，英文模式下应当已经翻了 ----
_still_cn = []
for cb, items in _flagged:
    for it in items:
        if cn(it):
            _still_cn.append(it)
check("A2 登记过的下拉项在英文模式下已英译", not _still_cn, _still_cn)

# ---- A3 ★：没登记的，切前切后必须一字不差（否则 currentText() 落盘会写坏配置）----
_changed = []
for cb, before in _unflagged:
    now = combo_items(cb)
    if now != before:
        _changed.append((before[:4], now[:4]))
check("A3★ 未登记的下拉项切英文前后完全一致（currentText() 落盘的枚举值不被改坏）",
      not _changed, _changed)

# 抽查几个「必须有中文项」的落盘型下拉
_crit = {
    "CPU/显存优化(vram_mode)": getattr(w.comfyui_page, "cb_vram", None),
}
for _label, _cb in _crit.items():
    if _cb is not None:
        check(f"A4 {_label} 选项仍是中文原样", any(cn(x) for x in combo_items(_cb)),
              combo_items(_cb)[:4])

# =====================================================================
print("== B. 真实保存后，落盘枚举值不许被改坏 ==")

from app.ui import changes_dialog as _cd                           # noqa: E402
from app.config import load_config                                 # noqa: E402

_cd.confirm_changes = lambda *a, **k: True
w.comfyui_page._insecure_modal = lambda inst, new: "ok"
_orig_info = QMessageBox.information
QMessageBox.information = staticmethod(lambda *a, **k: None)
try:
    # 英文模式下，用户什么都不改，直接点保存
    _before = {i.get("id"): dict(i) for i in w.cfg["instances"]}
    w.comfyui_page._save()
    app.processEvents()
finally:
    QMessageBox.information = _orig_info

_cfg2 = load_config()
_after = next((i for i in _cfg2.get("instances") or [] if i.get("id") == "loc"), {})
_before_inst = _before.get("loc", {})

_ENUM_KEYS = ("vram_mode", "cross_attn", "preview_method", "precision",
              "cuda_malloc")
_bad = {}
for _k in _ENUM_KEYS:
    if _k in _before_inst and _after.get(_k) != _before_inst.get(_k):
        _bad[_k] = (_before_inst.get(_k), _after.get(_k))
check("B1★ 英文模式下保存，5 个 currentText() 落盘的枚举值一个都没变",
      not _bad, _bad)

check("B2 vram_mode 落盘仍是「自动」", _after.get("vram_mode") == "自动",
      _after.get("vram_mode"))

# ---- B3：设置页同理（mirror 是 currentText() 落盘）----
_sp = w.settings_page
try:
    _cd.confirm_changes = lambda *a, **k: True
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    _sp._save()
    app.processEvents()
finally:
    QMessageBox.information = _orig_info

_cfg3 = load_config()
_mirror = (_cfg3.get("settings") or {}).get("mirror")
check("B3★ mirror 落盘值与初始一致（未被翻成英文写进配置）",
      _mirror == _SEED["settings"]["mirror"], _mirror)

# =====================================================================
print("== C. 反向：EN → ZH → EN 必须可逆（否则每切一次语言就漂一点）==")

# 注意：seed 里 lang="en"，所以建窗时下拉**本来就是英文**，`_before_items` 记的是英文态。
# 因此这里要验的是「来回切换可逆」，而不是「切回中文后含中文」。
_A_en = {id(cb): combo_items(cb) for cb, _ in _flagged}

i18n.apply_language(w, "zh")
app.processEvents()
_B_zh = {id(cb): combo_items(cb) for cb, _ in _flagged}

i18n.apply_language(w, "en")
app.processEvents()
_C_en = {id(cb): combo_items(cb) for cb, _ in _flagged}

check("C1 切到中文后登记过的下拉项确实变了（不是空操作）", _B_zh != _A_en,
      [(v[:3]) for v in _B_zh.values()])
check("C2★ 再切回英文后与第一次英文态完全一致（来回切换不漂移）",
      _C_en == _A_en,
      {k: (_A_en[k][:3], _C_en[k][:3]) for k in _A_en if _A_en[k] != _C_en[k]})

# 中文态里应当出现中文；例外是本来就是 ASCII 的项（GET/POST/PUT 这类 HTTP 方法，
# 中英本来就一样）。真正要抓的是「有中文却中英一样」= 该翻没翻。
_lost = []
for _cid, _en in _A_en.items():
    _zh = _B_zh.get(_cid, [])
    if _en == _zh and any(cn(x) for x in _zh):
        _lost.append(_zh[:3])
check("C3 没有「该翻却没翻」的登记下拉（中英相同的只允许是纯 ASCII 项）", not _lost, _lost)

# =====================================================================
_bad = [n for n, ok, _ in res if not ok]
print()
print("下拉翻译×配置安全：%d/%d 通过" % (len(res) - len(_bad), len(res)))
if _bad:
    print("FAILED:")
    for n in _bad:
        print("  - " + n)
sys.stdout.flush()
clean_exit(1 if _bad else 0)
