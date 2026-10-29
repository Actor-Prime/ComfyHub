# -*- coding: utf-8 -*-
"""工具目录自定义（共享 Python/MinGit 安装位置）回归（2026-10-29 用户需求）

验证点：
  1. tools_root() 默认 = <数据根>/tools
  2. settings.tools_dir 为绝对路径 → tools_root() 返回自定义路径（目录自动创建）
  3. settings.tools_dir 为相对路径/非法值 → 回落默认（防手改 config 填错）
  4. 改路径不搬迁旧目录（旧文件原样保留）
  5. 设置页：ed_tools 预填、保存写入 tools_dir、相对路径被拦截不保存
  6. 向导：工具目录入口已移除（用户要求精简），配置值不受向导影响
  7. 解释器路径自定义：可编辑、留空默认、有效路径优先、无效拦截、重装 userData
  8. 解释器行统一路径职责（「Python 安装到」行已删）：重装显示默认路径/改处即装到/
     exe回推安装根/相对拦截；新装留空默认/文件夹即装到/有效exe直用；卸载函数行为
  9. 重装 detect 首检 missing、复验真实检测并带回 python_path（防复验死循环）
测试结束恢复 config 原状，不污染真实设置。
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

# 弹窗打桩（不弹窗阻塞）
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

qapp = QApplication(sys.argv[:1])

from app import bootstrap                                    # noqa: E402
from app.config import CONFIG_PATH, load_config, save_config  # noqa: E402
from app.paths import data_root                              # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def pump(sec=0.3):
    t = time.time()
    while time.time() - t < sec:
        qapp.processEvents()
        time.sleep(0.01)


# ---------------- config 备份（结束必还原） ----------------
backup = None
if os.path.isfile(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        backup = f.read()

try:
    # ---- 1. 默认路径 ----
    print("\n[1] tools_root 默认")
    cfg = load_config()
    cfg.setdefault("settings", {}).pop("tools_dir", None)
    save_config(cfg)
    d = bootstrap.tools_root()
    check("默认 = 数据根/tools", d == os.path.join(data_root(), "tools"), d)

    # ---- 2. 自定义绝对路径 ----
    print("\n[2] 自定义绝对路径")
    custom = os.path.join(os.environ.get("TEMP", "/tmp"), "ch_tools_test")
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = custom
    save_config(cfg)
    d = bootstrap.tools_root()
    check("返回自定义路径", d == custom, d)
    check("目录已自动创建", os.path.isdir(custom))

    # ---- 3. 相对路径回落 ----
    print("\n[3] 非法值回落")
    cfg = load_config()
    cfg["settings"]["tools_dir"] = "relative\\bad"
    save_config(cfg)
    d = bootstrap.tools_root()
    check("相对路径回落默认", d == os.path.join(data_root(), "tools"), d)

    # ---- 4. 旧目录不搬迁 ----
    print("\n[4] 改路径不搬迁")
    marker = os.path.join(custom, "old_marker.txt")
    with open(marker, "w", encoding="utf-8") as f:
        f.write("x")
    cfg = load_config()
    cfg["settings"]["tools_dir"] = os.path.join(custom, "_new")
    save_config(cfg)
    bootstrap.tools_root()
    check("旧目录文件原样保留", os.path.isfile(marker))

    # ---- 5. 设置页 ----
    print("\n[5] 设置页")
    from app.ui.pages.settings_page import SettingsPage
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = custom
    save_config(cfg)
    page = SettingsPage(load_config(), None)
    check("ed_tools 存在", hasattr(page, "ed_tools"))
    check("ed_tools 预填配置值", page.ed_tools.text().strip() == custom,
          page.ed_tools.text())
    # 相对路径被拦截：保存后配置不变
    page.ed_tools.setText("rel\\path")
    page._save()
    check("相对路径拦截不保存",
          load_config()["settings"].get("tools_dir") == custom)
    # 正常保存
    newp = os.path.join(custom, "_saved")
    page.ed_tools.setText(newp)
    page._save()
    check("保存写入 tools_dir",
          load_config()["settings"].get("tools_dir") == newp)
    # 恢复默认 = 清空
    page.ed_tools.setText("")
    page._save()
    check("清空保存即恢复默认",
          not load_config()["settings"].get("tools_dir"))

    # ---- 6. 向导（2026-10-29 用户要求：向导不再放工具目录入口，只在设置页） ----
    print("\n[6] 向导（工具目录入口已移除）")
    from app.ui.setup_wizard import SetupWizard
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = custom
    save_config(cfg)
    wiz = SetupWizard()
    check("向导无 ed_tools（入口已移除）", not hasattr(wiz, "ed_tools"))
    check("无 _persist_tools_dir（挂点已移除）",
          not hasattr(wiz, "_persist_tools_dir"))
    # 已配置的自定义路径不受向导影响（tools_root 仍读配置）
    check("配置值不被向导清掉", bootstrap.tools_root() == custom,
          bootstrap.tools_root())

    # ---- 7. 解释器路径自定义（2026-10-29 用户需求） ----
    print("\n[7] 解释器路径自定义")
    # 「重新安装」选项仅在共享便携 Python 已存在时出现 → 用默认路径重建向导
    # （临时 tools 目录里没有 python，会走「自动安装」分支没有该项）
    cfg = load_config()
    cfg.setdefault("settings", {}).pop("tools_dir", None)
    save_config(cfg)
    wiz.close()
    pump(0.2)
    shared_py = os.path.join(bootstrap.tools_root(), "python", "python.exe")
    if not os.path.isfile(shared_py):
        print("  ⏭️ 共享便携 Python 不存在，跳过重装选项断言")
        sys.exit(0)
    wiz = SetupWizard()
    check("ed_py_path 可编辑（非只读）", not wiz.ed_py_path.isReadOnly())
    # 留空 → 走下拉默认
    wiz.ed_py_path.setText("")
    py, err = wiz._effective_python_exe()
    check("留空走下拉默认", err == "" and py == wiz.cb_python.currentData(),
          repr(py))
    # 填有效路径 → 优先使用（用 venv 自身的 python.exe 当样本）
    real_py = os.path.abspath(sys.executable)
    wiz.ed_py_path.setText(real_py)
    py, err = wiz._effective_python_exe()
    check("有效路径优先", err == "" and py == real_py, py)
    # 填无效路径 → 拦截报错
    wiz.ed_py_path.setText(os.path.join(custom, "no_such_python.exe"))
    py, err = wiz._effective_python_exe()
    check("无效路径拦截", py is None and "不存在" in err, err.splitlines()[0])
    # 重新安装选项 userData 修复（原为 None 导致该选项形同虚设）
    ud_reinstall = None
    for i in range(wiz.cb_python.count()):
        if "重新安装" in wiz.cb_python.itemText(i):
            ud_reinstall = wiz.cb_python.itemData(i)
            break
    check("重新安装 userData=__reinstall__", ud_reinstall == "__reinstall__",
          repr(ud_reinstall))

    # ---- 8. 解释器行统一路径职责（2026-10-30 用户定稿：
    #      「Python 安装到」行已删；重装在解释器行显示默认路径，改了就装到改处） ----
    print("\n[8] 解释器行统一路径职责与卸载")
    wiz.show()
    pump(0.2)
    check("「Python 安装到」行已删除", not hasattr(wiz, "ed_py_install"))
    check("_py_install_dir 已删除", not hasattr(wiz, "_py_install_dir"))
    # 重装模式：解释器行直接显示默认共享 python.exe 路径（不再是空白）
    re_idx = None
    for i in range(wiz.cb_python.count()):
        if wiz.cb_python.itemData(i) == "__reinstall__":
            re_idx = i
            break
    if re_idx is not None:
        wiz.cb_python.setCurrentIndex(re_idx)
        default_exe = os.path.join(
            bootstrap.tools_root(), "python", "python.exe")
        check("重装显示默认路径（可见文本）",
              wiz.ed_py_path.text() == default_exe, wiz.ed_py_path.text())
        py_r, err_r = wiz._effective_python_exe()
        check("默认路径解析=__reinstall__+默认安装根",
              err_r == "" and py_r == "__reinstall__"
              and wiz._install_target_dir("__reinstall__")[0]
              == bootstrap.tools_root(),
              wiz._install_target_dir("__reinstall__")[0])
        # 改成其他文件夹 → 安装根=该文件夹（"改了就装到改处"）
        wiz.ed_py_path.setText(custom)
        d, err = wiz._install_target_dir("__reinstall__")
        check("重装改文件夹=装到该文件夹", err == "" and d == custom, d)
        # …\X\python\python.exe 型 → 安装根=X（原地重装语义）
        wiz.ed_py_path.setText(os.path.join(custom, "python", "python.exe"))
        d, err = wiz._install_target_dir("__reinstall__")
        check("exe路径回推安装根", err == "" and d == custom, d)
        # 相对路径拦截
        wiz.ed_py_path.setText("rel\\dir")
        py_r, err_r = wiz._effective_python_exe()
        check("重装相对路径拦截", err_r != "" and "绝对路径" in err_r,
              err_r.splitlines()[0])
        # 恢复默认值
        wiz.ed_py_path.setText(default_exe)
        py_r, err_r = wiz._effective_python_exe()
        check("重装哨兵解析", err_r == "" and py_r == "__reinstall__", repr(py_r))
    else:
        print("  ⏭️ 无重装选项，跳过重装断言")
    # 新装模式：留空=默认；文件夹=装到那里；exe 存在=直接使用
    wiz.cb_python.setCurrentIndex(0)
    ud0 = wiz.cb_python.currentData()
    if ud0 is None:
        wiz.ed_py_path.setText("")
        py_n, err_n = wiz._effective_python_exe()
        d_n, _ = wiz._install_target_dir(py_n)
        check("新装留空=默认安装根",
              err_n == "" and py_n is None and d_n == bootstrap.tools_root(), d_n)
        wiz.ed_py_path.setText(custom)
        py_n, err_n = wiz._effective_python_exe()
        d_n, _ = wiz._install_target_dir(py_n)
        check("新装填文件夹=装到该文件夹",
              err_n == "" and py_n is None and d_n == custom, d_n)
        wiz.ed_py_path.setText(os.path.abspath(sys.executable))
        py_n, err_n = wiz._effective_python_exe()
        check("新装填有效exe=直接使用",
              err_n == "" and py_n == os.path.abspath(sys.executable), repr(py_n))
        wiz.ed_py_path.setText(os.path.join(custom, "no_such.exe"))
        py_n, err_n = wiz._effective_python_exe()
        check("无效exe拦截", py_n is None and "不存在" in err_n,
              err_n.splitlines()[0])
    else:
        print(f"  ⏭️ 下拉首项非自动安装（ud={ud0!r}），跳过新装分支断言")
    # uninstall_portable_python：无目录→False；有目录→删除并 True
    fake = os.path.join(custom, "python")
    os.makedirs(fake, exist_ok=True)
    with open(os.path.join(fake, "python.exe"), "w") as f:
        f.write("x")
    check("卸载：无目录返回 False",
          not bootstrap.uninstall_portable_python(os.path.join(custom, "_none")))
    check("卸载：有目录删除", bootstrap.uninstall_portable_python(custom)
          and not os.path.isdir(fake))
    wiz.close()
    pump(0.2)

    # ---- 8b. 新装分支（临时把 tools 指到空目录 → 下拉首项=自动安装 ud=None）----
    print("\n[8b] 新装分支解析")
    empty_root = os.path.join(custom, "_empty_tools")
    os.makedirs(empty_root, exist_ok=True)
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = empty_root
    save_config(cfg)
    wiz = SetupWizard()
    wiz.show()
    pump(0.2)
    ud_fresh = wiz.cb_python.currentData()
    if ud_fresh is not None:
        print(f"  ⏭️ 无共享 Python 时首项仍非 None（ud={ud_fresh!r}），跳过")
    else:
        wiz.ed_py_path.setText("")
        py_f, err_f = wiz._effective_python_exe()
        d_f, _ = wiz._install_target_dir(py_f)
        check("新装留空=默认安装根",
              err_f == "" and py_f is None and d_f == bootstrap.tools_root(), d_f)
        wiz.ed_py_path.setText(custom)
        py_f, err_f = wiz._effective_python_exe()
        d_f, _ = wiz._install_target_dir(py_f)
        check("新装填文件夹=装到该文件夹",
              err_f == "" and py_f is None and d_f == custom, d_f)
        wiz.ed_py_path.setText(os.path.abspath(sys.executable))
        py_f, err_f = wiz._effective_python_exe()
        check("新装填有效exe=直接使用",
              err_f == "" and py_f == os.path.abspath(sys.executable), repr(py_f))
        wiz.ed_py_path.setText(os.path.join(custom, "no_such.exe"))
        py_f, err_f = wiz._effective_python_exe()
        check("无效exe拦截", py_f is None and "不存在" in err_f,
              err_f.splitlines()[0])
        wiz.ed_py_path.setText("rel\\dir")
        py_f, err_f = wiz._effective_python_exe()
        check("新装相对路径拦截", err_f != "" and "绝对路径" in err_f,
              err_f.splitlines()[0])
    wiz.close()
    pump(0.2)
    cfg = load_config()          # 还原 tools_dir（下面 [9] 依赖共享 Python）
    cfg.setdefault("settings", {}).pop("tools_dir", None)
    save_config(cfg)

    # ---- 9. 重装复验链路（2026-10-29 修复：detect 永远 missing → 复验死循环）----
    # 首检返回 missing 触发安装；装后复验必须走真实检测并带回 python_path，
    # 否则①复验永远"未通过"②步骤3/4 拿不到解释器路径。
    print("\n[9] 重装 detect 首检/复验")
    fake_comfy = os.path.join(custom, "_fake_comfy")
    os.makedirs(fake_comfy, exist_ok=True)
    with open(os.path.join(fake_comfy, "main.py"), "w", encoding="utf-8") as f:
        f.write("# fake\n")
    wiz = SetupWizard()
    wiz.show()
    pump(0.2)
    shared_exe = os.path.join(bootstrap.tools_root(), "python", "python.exe")
    if not os.path.isfile(shared_exe):
        print("  ⏭️ 共享便携 Python 不存在，跳过复验断言")
    else:
        # target 含 main.py → _auto=False（等待决策，绝不自动执行卸载/安装！）
        wiz.p2.start(fake_comfy, "__reinstall__", "阿里云", "",
                     False, lambda *a: None)
        # 等首检（worker 线程）跑完
        t0 = time.time()
        while time.time() - t0 < 5 and "先卸载旧版" not in wiz.p2.lb_state.text():
            pump(0.1)
        check("重装首检=missing（触发安装）",
              "先卸载旧版" in wiz.p2.lb_state.text(), wiz.p2.lb_state.text())
        # 复验：同一 detect 再次调用 → 必须真实检测 + 带 python_path payload
        status, info, payload = wiz.p2.plan[0]["detect"]()
        check("复验走真实检测（不再永远 missing）",
              status == "ok", f"{status} {info}")
        check("复验带回 python_path（供步骤3/4使用）",
              payload.get("python_path") == shared_exe,
              payload.get("python_path", ""))
        # ★ 全程未执行安装（不许真卸载共享 Python）
        check("共享 Python 未被测试误卸载", os.path.isfile(shared_exe))
    wiz.close()
    pump(0.2)
    shutil.rmtree(fake_comfy, ignore_errors=True)

finally:
    # ---- 还原真实 config ----
    if backup is not None:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(backup)
    shutil.rmtree(os.path.join(
        os.environ.get("TEMP", "/tmp"), "ch_tools_test"), ignore_errors=True)

fails = [n for n, ok, _ in res if not ok]
print(f"\n结果: {len(res) - len(fails)}/{len(res)} 通过")
if fails:
    print("失败项:", fails)
    sys.exit(1)
