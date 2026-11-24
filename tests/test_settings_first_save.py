# -*- coding: utf-8 -*-
"""设置页首次打开点保存，不该弹「本次将保存 N 处修改」（2026-11-22 客户反馈）

现场：首次安装完成后打开设置页，点一次「保存设置」弹出 14 条"改动"：
  PyPI 镜像 (空) → 阿里云 / 主题 (空) → dark / 本地模型 (空) → qwen3-vl-2b …
根因：老 config.json 里根本没有这些键（用户从没设置过 ≠ 空值），而 diff 把
"缺键"当成了"空值 → 默认值"。

修法：设置页记一份「控件刚建好时」的状态当基线，老配置缺键或存空值时拿它比较。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="settings_first_")
os.environ["APPDATA"] = _TMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# D-09：os._exit 不会销毁 QApplication，进程结束时会以 0xC0000005 崩掉（本用例 10/10 必现）
from _qa_exit import clean_exit      # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    print("%s  %s %s" % (tag, name, extra))
    if not cond:
        FAILS.append(name)


class FakeMain:
    """SettingsPage 只用到 cfg 与 current_inst"""
    cfg = {}

    def current_inst(self):
        return None


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from app.ui import changes_dialog
    from app.ui.pages import settings_page as sp

    captured = []           # 每次 confirm 收到的 changes 列表

    def fake_confirm(_parent, _title, changes, _btn):
        captured.append(list(changes))
        return False        # 一律取消：不写盘，只看 diff 内容

    changes_dialog.confirm_changes = fake_confirm

    # ---------- A. 首次安装：cfg 里没有 settings 键 ----------
    print("== A. 首次安装（无 settings）==")
    cfg = {"instances": []}
    page = sp.SettingsPage(cfg, FakeMain())
    page._save()
    n = len(captured[0]) if captured else 0
    check("没有弹出改动确认框", not captured,
          "捕获到 %d 条：%s" % (n, captured[0][:5] if captured else ""))
    check("提示「没有改动」", "没有改动" in page.lb_saved.text(), page.lb_saved.text())

    # ---------- B. 真的改一项 → 只有一条改动 ----------
    print("== B. 只改主题 ==")
    captured.clear()
    cur = page.cb_theme.currentData()
    idx = next((i for i in range(page.cb_theme.count())
                if page.cb_theme.itemData(i) != cur), -1)
    check("找到另一个主题选项", idx >= 0, "count=%d" % page.cb_theme.count())
    if idx >= 0:
        page.cb_theme.setCurrentIndex(idx)
        page._save()
        check("弹出了确认框", bool(captured))
        if captured:
            keys = [c[0] if isinstance(c, (list, tuple)) else c for c in captured[0]]
            check("只有 1 条改动", len(captured[0]) == 1, str(captured[0])[:200])
            check("改的是主题", any("主题" in str(k) for k in keys),
                  str(captured[0])[:200])

    # ---------- C. 老配置有真值时不掩盖真实改动 ----------
    print("== C. 老配置有值 → 仍能识别改动 ==")
    captured.clear()
    cfg2 = {"instances": [], "settings": {"theme": "dark", "ai_max_tokens": 4000}}
    page2 = sp.SettingsPage(cfg2, FakeMain())
    page2.sp_ai_max.setValue(8000)
    page2._save()
    check("识别到最大输出改动", bool(captured))
    if captured:
        txt = str(captured[0])
        check("改动项含最大输出", "最大输出" in txt, txt[:200])

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.stdout.flush()
        clean_exit(1)
    print("ALL PASS")
    sys.stdout.flush()
    clean_exit(0)


if __name__ == "__main__":
    main()
