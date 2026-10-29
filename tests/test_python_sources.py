# -*- coding: utf-8 -*-
"""Python 来源全部可能性矩阵回归（2026-10-30 用户需求）

矩阵：
  A. 新建实例 Python 来源：
     A1 共享默认 / A2 重装默认 / A3 重装自定义 / A4 自动安装默认 /
     A5 自动安装自定义 / A6 选择新增另一实例 python_embeded /
     A7 使用已有(实例自带/自定义标签) / A8 无效与相对路径拦截 / __add__ 停留态拦截
  B. 分类标签（别跟添加实例自带的搞混）：
     B1 安装/重装（默认或自定义路径）恒挂[共享] · B2 [实例自带] · B3 [自定义]
     B4 解释器路径行 chip 与下拉标签同步
  C. 添加已有实例（用自己安装的共享 Python）：
     C1 共享+自带都有→共享优先 / C2 只有共享 / C3 只有自带 /
     C4 都无→「下一步：修复」且修复装共享
测试结束恢复 config 原状，不污染真实设置；全程不动真实共享 Python。
用法: .venv_build/Scripts/python.exe tests/test_python_sources.py
"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (   # noqa: E402
    QApplication, QMessageBox, QWidget, QWizard)

# 弹窗打桩（不弹窗阻塞）
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

qapp = QApplication(sys.argv[:1])

from app import bootstrap                                    # noqa: E402
from app.config import CONFIG_PATH, load_config, save_config  # noqa: E402
from app.paths import data_root                              # noqa: E402
from app.ui.setup_wizard import SetupWizard                  # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def pump(sec=0.3):
    t = time.time()
    while time.time() - t < sec:
        qapp.processEvents()
        time.sleep(0.01)


def touch(path):
    """造一个假 python.exe（仅满足 os.path.isfile 判定，绝不执行）"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"fake")
    return path


def item_index(wiz, predicate):
    for i in range(wiz.cb_python.count()):
        if predicate(wiz.cb_python.itemText(i), wiz.cb_python.itemData(i)):
            return i
    return -1


# ---------------- config 备份（结束必还原） ----------------
backup = None
if os.path.isfile(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        backup = f.read()

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "ch_py_src_test")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

# 测试素材：假共享 Python / 假实例（含 python_embeded）/ 外部自定义解释器
shared_root = os.path.join(TMP, "tools_shared")
shared_py = touch(os.path.join(shared_root, "python", "python.exe"))
empty_root = os.path.join(TMP, "tools_empty")
os.makedirs(empty_root, exist_ok=True)
fake_inst = os.path.join(TMP, "ComfyUIImage")
os.makedirs(os.path.join(fake_inst, "comfy"), exist_ok=True)
with open(os.path.join(fake_inst, "main.py"), "w", encoding="utf-8") as f:
    f.write("# fake instance\n")
embed_py = touch(os.path.join(fake_inst, "python_embeded", "python.exe"))
other_inst = os.path.join(TMP, "ComfyUI_windows_portable")
embed_py2 = touch(os.path.join(other_inst, "python_embeded", "python.exe"))
custom_py = touch(os.path.join(TMP, "myenv", "python.exe"))

wiz = None
wiz2 = None
try:
    # ================= [1] 分类函数 _classify_py =================
    print("\n[1] 解释器来源分类 _classify_py")
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = shared_root
    save_config(cfg)
    wiz = SetupWizard()
    pump(0.15)
    # 给向导注入已注册实例/扫描候选（分类函数按路径亲缘判定）
    wiz._parent_cfg = {"instances": [{"comfyui_path": fake_inst,
                                      "python_path": embed_py}]}
    wiz._scan_candidates = [other_inst]
    check("共享 → 共享", wiz._classify_py(shared_py) == "共享",
          wiz._classify_py(shared_py))
    check("实例内 python_embeded → 实例自带",
          wiz._classify_py(embed_py) == "实例自带", wiz._classify_py(embed_py))
    check("未注册实例 python_embeded（扫描候选）→ 实例自带",
          wiz._classify_py(embed_py2) == "实例自带", wiz._classify_py(embed_py2))
    check("外部任意解释器 → 自定义", wiz._classify_py(custom_py) == "自定义",
          wiz._classify_py(custom_py))

    # ================= [2] 下拉构建与标签 =================
    print("\n[2] 下拉分类标签（A1/A2/A7）")
    i_shared = item_index(wiz, lambda t, d: d == shared_py)
    i_re = item_index(wiz, lambda t, d: d == "__reinstall__")
    i_add = item_index(wiz, lambda t, d: d == "__add__")
    check("A1 [共享]使用共享项存在且标签正确",
          i_shared >= 0 and wiz.cb_python.itemText(i_shared).startswith("[共享]"),
          wiz.cb_python.itemText(i_shared) if i_shared >= 0 else "缺")
    check("A2 [共享]重新安装项存在且标签正确",
          i_re >= 0 and wiz.cb_python.itemText(i_re).startswith("[共享]"),
          wiz.cb_python.itemText(i_re) if i_re >= 0 else "缺")
    check("A6 「➕ 选择新增」入口存在",
          i_add >= 0 and "选择新增" in wiz.cb_python.itemText(i_add),
          wiz.cb_python.itemText(i_add) if i_add >= 0 else "缺")
    check("选择新增固定在末尾", i_add == wiz.cb_python.count() - 1)
    # A7「使用已有」来自已注册实例 → 向导须带 cfg 的 parent 构造（下拉在 __init__ 构建）
    wiz.close()
    pump(0.15)
    parent_w = QWidget()
    parent_w.cfg = {"instances": [{"comfyui_path": fake_inst,
                                   "python_path": embed_py},
                                  {"comfyui_path": os.path.join(TMP, "Z"),
                                   "python_path": custom_py}]}
    wiz = SetupWizard(parent_w)
    pump(0.15)
    i_used = item_index(wiz, lambda t, d: d == embed_py)
    check("A7 使用已有(实例自带)标签正确",
          i_used >= 0 and wiz.cb_python.itemText(i_used).startswith("[实例自带]"),
          wiz.cb_python.itemText(i_used) if i_used >= 0 else "缺")
    i_cus = item_index(wiz, lambda t, d: d == custom_py)
    check("A7 使用已有(外部解释器)标签=[自定义]",
          i_cus >= 0 and wiz.cb_python.itemText(i_cus).startswith("[自定义]"),
          wiz.cb_python.itemText(i_cus) if i_cus >= 0 else "缺")
    wiz.close()
    pump(0.15)

    # ================= [3] 选择新增流程（A6） =================
    print("\n[3] 「➕ 选择新增」流程（A6）")
    wiz = SetupWizard()
    pump(0.15)
    wiz._parent_cfg = {"instances": [{"comfyui_path": fake_inst,
                                      "python_path": embed_py}]}
    wiz._scan_candidates = [other_inst]
    n0 = wiz.cb_python.count()
    i_add = item_index(wiz, lambda t, d: d == "__add__")
    # -- A6a 选另一个实例的 python_embeded → 插入[实例自带]并选中
    wiz._choose_python_exe = lambda: embed_py2      # 打桩文件选择器
    wiz.cb_python.setCurrentIndex(i_add)
    pump(0.3)
    i_new = item_index(wiz, lambda t, d: d == os.path.normpath(embed_py2))
    check("A6a 新项插入（在➕之前）", i_new >= 0 and i_new < wiz.cb_python.count() - 1,
          f"idx={i_new}")
    check("A6a 标签=[实例自带]",
          i_new >= 0 and wiz.cb_python.itemText(i_new).startswith("[实例自带]"),
          wiz.cb_python.itemText(i_new) if i_new >= 0 else "缺")
    check("A6a 自动选中新项",
          wiz.cb_python.currentData() == os.path.normpath(embed_py2),
          repr(wiz.cb_python.currentData()))
    check("A6a 解释器行同步", wiz.ed_py_path.text() == os.path.normpath(embed_py2),
          wiz.ed_py_path.text())
    check("B2 chip=实例自带", "实例自带" in wiz.lb_py_chip.text(),
          wiz.lb_py_chip.text())
    check("A6a 数量+1", wiz.cb_python.count() == n0 + 1, f"{wiz.cb_python.count()}")
    # -- A6b 重复选同一路径 → 查重不加项
    i_add = item_index(wiz, lambda t, d: d == "__add__")
    wiz.cb_python.setCurrentIndex(i_add)
    pump(0.3)
    check("A6b 查重不重复插入", wiz.cb_python.count() == n0 + 1,
          f"{wiz.cb_python.count()}")
    # -- A6c 选外部自定义解释器 → [自定义]
    wiz._choose_python_exe = lambda: custom_py
    i_add = item_index(wiz, lambda t, d: d == "__add__")
    wiz.cb_python.setCurrentIndex(i_add)
    pump(0.3)
    i_cus = item_index(wiz, lambda t, d: d == os.path.normpath(custom_py))
    check("A6c 外部解释器标签=[自定义]",
          i_cus >= 0 and wiz.cb_python.itemText(i_cus).startswith("[自定义]"),
          wiz.cb_python.itemText(i_cus) if i_cus >= 0 else "缺")
    check("B3 chip=自定义", "自定义" in wiz.lb_py_chip.text(), wiz.lb_py_chip.text())
    # -- A6d 取消选择 → 回退到进入前选项，不加项
    n1 = wiz.cb_python.count()
    prev_idx = wiz.cb_python.currentIndex()
    wiz._choose_python_exe = lambda: ""
    i_add = item_index(wiz, lambda t, d: d == "__add__")
    wiz.cb_python.setCurrentIndex(i_add)
    pump(0.3)
    check("A6d 取消回退原选项", wiz.cb_python.currentIndex() == prev_idx,
          f"{wiz.cb_python.currentIndex()} vs {prev_idx}")
    check("A6d 取消不加项", wiz.cb_python.count() == n1, f"{wiz.cb_python.count()}")
    # -- A8 __add__ 停留态拦截（没选成文件就点下一步）
    wiz._flow_add_python = lambda: None             # 冻结流程，制造停留态
    i_add = item_index(wiz, lambda t, d: d == "__add__")
    wiz.cb_python.setCurrentIndex(i_add)
    py, err = wiz._effective_python_exe()
    check("A8 __add__停留态拦截", py is None and "选择新增" in err,
          err.splitlines()[0] if err else "无错误")
    wiz.cb_python.setCurrentIndex(0)
    wiz.close()
    pump(0.15)

    # ================= [4] 安装/重装路径与[共享]标签（B1 核心） =================
    print("\n[4] 安装/重装路径解析与标签（A2/A3/A4/A5/B1）")
    wiz = SetupWizard()          # tools_dir=shared_root → 下拉含重新安装项
    pump(0.15)
    i_re = item_index(wiz, lambda t, d: d == "__reinstall__")
    check("重新安装项存在（共享已装）", i_re >= 0)
    if i_re >= 0:
        wiz.cb_python.setCurrentIndex(i_re)
        default_exe = os.path.join(shared_root, "python", "python.exe")
        check("A2 重装显示默认路径", wiz.ed_py_path.text() == default_exe,
              wiz.ed_py_path.text())
        check("B1 重装默认路径 chip=[共享]", "共享" in wiz.lb_py_chip.text(),
              wiz.lb_py_chip.text())
        # A3 重装到自定义文件夹——仍然标 [共享]（ComfyHub 管理，别跟实例自带混淆）
        custom_tools = os.path.join(TMP, "my_tools")
        wiz.ed_py_path.setText(custom_tools)
        wiz._update_py_chip(wiz.cb_python.currentData())
        d, err = wiz._install_target_dir("__reinstall__")
        check("A3 重装自定义=装到该文件夹", err == "" and d == custom_tools, d)
        check("B1 重装自定义路径 chip 仍=[共享]", "共享" in wiz.lb_py_chip.text(),
              wiz.lb_py_chip.text())
        # exe 路径回推安装根
        wiz.ed_py_path.setText(os.path.join(custom_tools, "python", "python.exe"))
        d, err = wiz._install_target_dir("__reinstall__")
        check("A3 重装exe路径回推安装根", err == "" and d == custom_tools, d)
        check("B1 回推后 chip 仍=[共享]", "共享" in wiz.lb_py_chip.text(),
              wiz.lb_py_chip.text())
        # A8 相对路径拦截
        wiz.ed_py_path.setText("rel\\dir")
        py_r, err_r = wiz._effective_python_exe()
        check("A8 重装相对路径拦截", err_r != "" and "绝对路径" in err_r,
              err_r.splitlines()[0] if err_r else "无错误")
        # 恢复
        wiz.ed_py_path.setText(default_exe)
        py_r, err_r = wiz._effective_python_exe()
        check("重装哨兵解析", err_r == "" and py_r == "__reinstall__", repr(py_r))
    wiz.close()
    pump(0.15)

    # A4/A5 新装（共享未装 → 下拉首项=自动安装 ud=None）
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = empty_root
    save_config(cfg)
    wiz = SetupWizard()
    pump(0.15)
    ud0 = wiz.cb_python.currentData()
    check("A4 共享未装首项=自动安装(None)", ud0 is None, repr(ud0))
    check("自动安装项标签=[共享]",
          wiz.cb_python.itemText(0).startswith("[共享]"), wiz.cb_python.itemText(0))
    check("B1 自动安装 chip=[共享]", "共享" in wiz.lb_py_chip.text(),
          wiz.lb_py_chip.text())
    wiz.ed_py_path.setText("")
    py_n, err_n = wiz._effective_python_exe()
    d_n, _ = wiz._install_target_dir(py_n)
    check("A4 新装留空=默认安装根", err_n == "" and py_n is None
          and d_n == bootstrap.tools_root(), d_n)
    check("B1 留空=默认 chip 仍=[共享]", "共享" in wiz.lb_py_chip.text(),
          wiz.lb_py_chip.text())
    wiz.ed_py_path.setText(custom_tools)
    wiz._update_py_chip(wiz.cb_python.currentData())
    py_n, err_n = wiz._effective_python_exe()
    d_n, _ = wiz._install_target_dir(py_n)
    check("A5 新装自定义文件夹=装到那里", err_n == "" and py_n is None
          and d_n == custom_tools, d_n)
    check("B1 自定义安装路径 chip 仍=[共享]", "共享" in wiz.lb_py_chip.text(),
          wiz.lb_py_chip.text())
    # 新装填有效 exe=直接使用，chip 按真实来源分类（非笼统共享）
    wiz.ed_py_path.setText(embed_py2)
    wiz._update_py_chip(wiz.cb_python.currentData())
    py_n, err_n = wiz._effective_python_exe()
    check("A6变体 新装填实例exe=直接使用", err_n == "" and py_n == embed_py2,
          repr(py_n))
    check("B4 填实例exe chip=[实例自带]", "实例自带" in wiz.lb_py_chip.text(),
          wiz.lb_py_chip.text())
    wiz.ed_py_path.setText(os.path.join(TMP, "nope", "python.exe"))
    py_n, err_n = wiz._effective_python_exe()
    check("A8 无效exe拦截", py_n is None and "不存在" in err_n,
          err_n.splitlines()[0] if err_n else "无错误")
    wiz.ed_py_path.setText("rel\\x")
    py_n, err_n = wiz._effective_python_exe()
    check("A8 新装相对路径拦截", err_n != "" and "绝对路径" in err_n,
          err_n.splitlines()[0] if err_n else "无错误")
    wiz.close()
    pump(0.15)

    # ================= [5] 添加已有实例（C1-C4） =================
    print("\n[5] 添加已有实例用共享 Python（C1-C4）")
    # C1 共享+实例自带都有 → 共享排第一（验证优先用共享）
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = shared_root
    save_config(cfg)
    wiz = SetupWizard()
    pump(0.15)
    wiz._match_pythons([fake_inst])
    cands = wiz._py_map.get(os.path.normpath(fake_inst)) or []
    check("C1 共享+自带都有 → 共享优先",
          len(cands) >= 2 and cands[0] == os.path.normpath(shared_py),
          " → ".join(cands))
    check("C1 实例自带排后", os.path.normpath(embed_py) in cands[1:],
          " → ".join(cands))
    # _prepare_result：验证通过的共享解释器被写入注册结果
    wiz._scan_candidates = [fake_inst]
    wiz._verify_cache = {fake_inst: os.path.normpath(shared_py)}
    wiz._prepare_result()
    check("C1 注册用验证通过的共享解释器",
          wiz.setup_result.get("python_path") == os.path.normpath(shared_py),
          wiz.setup_result.get("python_path", ""))
    # C2 只有共享（实例无自带）——孤立目录：同级无 python，杜绝同级扫描兜底干扰
    iso_bare = os.path.join(TMP, "iso", "ComfyUIBare")
    os.makedirs(os.path.join(iso_bare, "comfy"), exist_ok=True)
    with open(os.path.join(iso_bare, "main.py"), "w", encoding="utf-8") as f:
        f.write("# fake\n")
    wiz._match_pythons([iso_bare])
    cands2 = wiz._py_map.get(os.path.normpath(iso_bare)) or []
    check("C2 只有共享 → 用共享", cands2 == [os.path.normpath(shared_py)],
          " → ".join(cands2))
    wiz.close()
    pump(0.15)
    # C3 只有实例自带（共享未装）
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = empty_root
    save_config(cfg)
    wiz = SetupWizard()
    pump(0.15)
    wiz._match_pythons([fake_inst])
    cands3 = wiz._py_map.get(os.path.normpath(fake_inst)) or []
    check("C3 只有自带 → 用自带", os.path.normpath(embed_py) in cands3
          and os.path.normpath(shared_py) not in cands3, " → ".join(cands3))
    wiz.close()
    pump(0.15)
    # C4 都没有 → 「下一步：修复」+ 修复装共享
    wiz = SetupWizard()
    pump(0.4)          # 等 _auto_probe 定时器建好 p2（150ms）
    wiz._match_pythons([iso_bare])
    cands4 = wiz._py_map.get(os.path.normpath(iso_bare)) or []
    check("C4 都没有 → 候选为空", cands4 == [], " → ".join(cands4))
    wiz._verify_fail("没有可用的 Python 解释器（共享便携 Python 也未安装）——")
    check("C4 引导「下一步：修复」", wiz._repair_mode is True
          and wiz.button(QWizard.NextButton).text().strip() == "下一步：修复",
          wiz.button(QWizard.NextButton).text())
    check("C4 修复目标=装共享 Python（不碰实例目录）",
          "安装便携 Python" in wiz.lb_scan.text(), wiz.lb_scan.text()[:60])
    wiz._repair_mode = False
    # C4b 修复计划的安装目标 = 共享 tools_root（打桩验证，绝不真装）
    rec = {}
    orig_inst_fn = bootstrap.install_portable_python

    def _fake_install(tools_dir, log=print, proxy="", force=False):
        rec["tools_dir"] = tools_dir
        return os.path.join(tools_dir, "python", "python.exe")

    bootstrap.install_portable_python = _fake_install
    try:
        wiz.p2._begin_detect = lambda: None        # 不真跑后台线程
        wiz.p2.start_repair(iso_bare, "", "阿里云", "", lambda *a: None)
        wiz.p2.plan[0]["install"]()
    finally:
        bootstrap.install_portable_python = orig_inst_fn
    check("C4b 修复安装目标=共享 tools_root",
          rec.get("tools_dir") == bootstrap.tools_root(), rec.get("tools_dir", ""))
    check("C4b 修复不改实例目录", wiz.p2.state.get("comfyui_path") == iso_bare,
          wiz.p2.state.get("comfyui_path", ""))
    wiz.close()
    pump(0.15)

    # ================= [6] 防混淆总检（B 类汇总） =================
    print("\n[6] 分类标签防混淆总检")
    cfg = load_config()
    cfg.setdefault("settings", {})["tools_dir"] = shared_root
    save_config(cfg)
    parent_w2 = QWidget()
    parent_w2.cfg = {"instances": [{"comfyui_path": fake_inst,
                                    "python_path": embed_py}]}
    wiz2 = SetupWizard(parent_w2)
    pump(0.15)
    labels = [wiz2.cb_python.itemText(i) for i in range(wiz2.cb_python.count())]
    check("每个下拉项都带分类标签",
          all(t.startswith("[") or t.startswith("➕") for t in labels),
          " | ".join(labels))
    check("共享项与实例自带项标签可区分",
          any(t.startswith("[共享]") for t in labels)
          and any(t.startswith("[实例自带]") for t in labels))
    wiz2.close()
    pump(0.15)

finally:
    # ---- 还原真实 config / 清理临时目录 ----
    if backup is not None:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(backup)
    for w in (wiz, wiz2):
        try:
            if w is not None:
                w.close()
        except Exception:
            pass
    shutil.rmtree(TMP, ignore_errors=True)

fails = [n for n, ok, _ in res if not ok]
print(f"\n结果: {len(res) - len(fails)}/{len(res)} 通过")
if fails:
    print("失败项:", fails)
    sys.exit(1)
