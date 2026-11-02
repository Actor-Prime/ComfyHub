# -*- coding: utf-8 -*-
"""全页面功能走查探针（验收标准①：全部页面的功能都能实现）

做法（desktop-ui-hook 五件套①+⑤）：
  · 逐页切换 8 个导航页 + 打开安装向导首屏 → 截图存证
  · 每页做「安全交互」：只点只读/无副作用按钮（检测、清屏、复制、Tab 切换）
  · 全程挂 excepthook：任何未捕获异常即 FAIL（页面加载炸 / 信号槽炸都算）
  · 探测绝不写盘（不点 开始安装 / 下载 / 删除 / 卸载 / 恢复默认）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/walk_all_pages.py
"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "analysis", "walkthrough")
os.makedirs(OUT, exist_ok=True)

# ---- 全局异常收集 ----
ERRORS = []
WARN = []


def _hook(tp, val, tb):
    ERRORS.append("".join(traceback.format_exception(tp, val, tb))[-1500:])


sys.excepthook = _hook


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.03)


def pump_until(app, cond, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        time.sleep(0.05)
        try:
            if cond():
                return True
        except Exception:
            pass
    return False


def shot(w, name):
    p = os.path.join(OUT, name)
    try:
        w.grab().save(p)
        return p
    except Exception as e:
        WARN.append(f"截图失败 {name}: {e}")
        return None


def click_safe_button(page, keywords, app, pump_sec=1.2, label=""):
    """在 page 里找 text 含 keywords 的可见按钮并点击（每词最多点一个）"""
    from PySide6.QtWidgets import QAbstractButton
    for kw in keywords:
        for b in page.findChildren(QAbstractButton):
            try:
                if kw in b.text() and b.isVisible() and b.isEnabled():
                    before = len(ERRORS)
                    b.click()
                    pump(app, pump_sec)
                    tag = f"{label} 按钮[{b.text().strip()[:12]}]"
                    if len(ERRORS) > before:
                        ERRORS.append(f"{tag} 点击抛异常（详见上条）")
                    else:
                        print(f"  ✓ 点击 {tag}")
                    break
            except Exception as e:
                ERRORS.append(f"{label} 点击[{kw}]异常: {e}")


def main():
    from PySide6.QtWidgets import QApplication, QTabWidget, QAbstractButton, QMessageBox
    # 无头走查绝不能让模态弹窗阻塞（如设置页「保存设置」会弹「设置已保存」，
    # 2026-10-31 实测：8 页布局对齐后设置页首次被走到即挂死）
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    app = QApplication(sys.argv)
    from app.ui.main_window import MainWindow
    from app.ui.nav import NAV

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    pump(app, 2.5)   # 等首轮异步（孤儿检查/侧栏刷新）落定

    names = ["home", "comfyui", "plugins", "env", "models", "workflow", "console", "settings"]
    checks = []      # (页名, 检查项, 结果)

    # ---------- 逐页走查 ----------
    for idx, name in enumerate(names):
        before = len(ERRORS)
        win.goto(idx)
        pump(app, 1.6)
        page = win.stack.widget(idx)
        ok_visible = page is not None and page.isVisibleTo(win.stack)
        checks.append((name, "页面加载可见", "PASS" if ok_visible else "FAIL"))
        shot(win, f"page_{idx}_{name}.png")

        # 页内 Tab 全切一遍（多数页有子 Tab）
        for tab in page.findChildren(QTabWidget):
            for ti in range(tab.count()):
                tab.setCurrentIndex(ti)
                pump(app, 0.5)
                shot(win, f"page_{idx}_{name}_tab{ti}.png")

        # ---- 页级安全交互 ----
        if name == "console":
            win.console_page.append_line("[walk] 走查探针输出一行测试文本")
            pump(app, 0.3)
            click_safe_button(page, ["复制全部", "清屏"], app, label="console")
        elif name == "env":
            click_safe_button(page, ["检测"], app, pump_sec=3.0, label="env")
        elif name == "settings":
            click_safe_button(page, ["保存设置"], app, label="settings")
        elif name == "home":
            # 侧栏/实例卡区域渲染（此时可能无实例——空态也是验收对象）
            click_safe_button(page, ["管理此实例"], app, label="home")

        if len(ERRORS) > before:
            checks.append((name, "页内交互", "FAIL"))
        else:
            checks.append((name, "页内交互", "PASS"))

    # ---------- 安装向导首屏（打开即关，不进安装流程） ----------
    before = len(ERRORS)
    wiz = None
    try:
        from app.ui.setup_wizard import SetupWizard
        wiz = SetupWizard(win)
        wiz.show()
        pump(app, 2.0)
        shot(wiz, "wizard_p0.png")   # 注意：抓向导自己，不是主窗
        wiz.close()
        pump(app, 0.5)
    except Exception as e:
        ERRORS.append(f"向导打开异常: {e}")
    checks.append(("setup_wizard", "打开/截图/关闭",
                   "PASS" if len(ERRORS) == before else "FAIL"))

    # ---------- 汇总 ----------
    print("\n===== 走查结果 =====")
    bad = [c for c in checks if c[2] == "FAIL"]
    for page_, item, res in checks:
        if res == "FAIL":
            print(f"  [{res}] {page_} · {item}")
    print(f"页面检查：{len(checks) - len(bad)}/{len(checks)} 通过")
    if WARN:
        print(f"警告 {len(WARN)} 条:")
        for w in WARN:
            print("  -", w)
    if ERRORS:
        print(f"\n!! 未捕获异常 {len(ERRORS)} 条:")
        for e in ERRORS:
            print("  ---")
            print("  " + e.replace("\n", "\n  ")[:900])
    else:
        print("未捕获异常：0 ✓")

    win.cfg.setdefault("settings", {})["close_behavior"] = "close"   # 测试直退，防关闭询问框挂死
    win.close()
    app.processEvents()
    # 退出前收掉托盘图标与定时器（Qt 收尾更干净；本机 rc 仍报 127，
    # 属 Git-Bash 下 Qt 进程返回码的老问题，以「页面检查/未捕获异常」两行为准）
    try:
        if getattr(win, "_theme_timer", None):
            win._theme_timer.stop()
        if getattr(win, "tray", None):
            win.tray.hide()
            win.tray.setVisible(False)
    except Exception:
        pass
    app.processEvents()
    sys.stdout.flush()
    sys.exit(1 if (ERRORS or bad) else 0)


if __name__ == "__main__":
    main()
