# -*- coding: utf-8 -*-
"""ComfyHub 向导第一页 · 全路径点击与按钮状态矩阵测试

覆盖：
  A 添加已有实例（单实例 / 多实例切换 / 缓存 / 缺依赖 / 无 Python 环境）
  B 全新安装（空目录 / 不存在路径 / 含 comfyui 命名 / 盘根）
  C 按钮互斥（不该出现的按钮是否禁用、是否出现两个"完成"）
  D 零副作用（取消/检测是否创建文件夹）
  E 环境移除（备份 tools → 清空 → 验证按钮全禁用 → 恢复）

用法：
  .venv_build/Scripts/python.exe tests/ui_click_matrix.py
"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWizard

app = QApplication(sys.argv[:1])
from app import bootstrap
from app.ui.main_window import MainWindow
from app.ui.setup_wizard import SetupWizard

TOOLS = bootstrap.tools_root()
TOOLS_BAK = TOOLS.rstrip("\\/") + "_bak_test"
TMP = r"F:\_ui_matrix"

RESULTS = []


def log(msg):
    print(msg, flush=True)


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    log(f"  {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


def pump(seconds):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.05)


def new_wizard():
    """新建一个向导，并等自动探测跑完（避免它覆盖我们设的路径）"""
    win = MainWindow()
    wiz = SetupWizard(win)
    wiz.show()
    pump(1.3)
    return win, wiz


def state(wiz):
    """抓取当前界面关键状态"""
    return {
        "next_text": wiz.button(QWizard.NextButton).text(),
        "next_on": wiz.button(QWizard.NextButton).isEnabled(),
        "next_vis": wiz.button(QWizard.NextButton).isVisible(),
        "finish_on": wiz.button(QWizard.FinishButton).isEnabled(),
        "finish_vis": wiz.button(QWizard.FinishButton).isVisible(),
        "back_on": wiz.button(QWizard.BackButton).isEnabled(),
        "cancel_on": wiz.button(QWizard.CancelButton).isEnabled(),
        "install_shown": not wiz.install_opts.isHidden(),
        "cands": [wiz.cb_found.itemText(i) for i in range(wiz.cb_found.count())],
        "cand_vis": not wiz.cb_found.isHidden(),
        "text": wiz.lb_scan.text().splitlines()[0][:60] if wiz.lb_scan.text() else "",
        "full_text": wiz.lb_scan.text(),
        "path": wiz.ed_dir.text(),
        "name": wiz.ed_name.text(),
    }


def wait_until(cond, timeout=15):
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


def detect(wiz, path, wait=9):
    """发起检测并等待"本轮"结果落定（不依赖固定时长）"""
    wiz.ed_dir.setText(path)
    wiz._detect_existing()
    # 等到出现结论性文案（验证通过/缺失/无 Python/全新安装/无实例）
    wait_until(lambda: any(k in wiz.lb_scan.text() for k in
                          ("验证通过", "缺依赖", "没有可用的 Python", "全新安装",
                           "未发现", "不是可用的 ComfyUI", "空文件夹")), timeout=wait + 8)
    pump(0.3)
    return state(wiz)


# ============================================================
# A. 添加已有实例
# ============================================================
def scenario_multi_instance():
    log("\n【A1】多实例（F:\\ComfyHub_testdata 下两个实例）→ 切换候选")
    win, wiz = new_wizard()
    s = detect(wiz, r"F:\ComfyHub_testdata")
    record("A1.1 列出候选（含第0项「新建新实例」）", len(s["cands"]) >= 3, f"{s['cands']}")
    record("A1.2 添加模式（安装参数隐藏）", not s["install_shown"])
    # 合法终态三种（2026-10-24 修复流程上线后）：
    #   验证通过→「完成」；缺依赖/无 Python→「下一步：修复」（主推修复）
    _a1_ok = s["next_on"] and s["next_text"] in ("完成", "仍要添加", "下一步：修复")
    record("A1.3 按钮进入目标态（完成/仍要添加/下一步：修复）", _a1_ok,
           f"{s['next_text']}/{s['next_on']}")
    record("A1.4 无独立 Finish 按钮", not s["finish_vis"])
    # 切换候选：第 0 项是「新建新实例」（fresh 形态），从第 1 项起才是实例验证
    wiz.cb_found.setCurrentIndex(0)
    pump(0.5)
    st0 = state(wiz)
    record("A1.5 第0项=新建新实例（fresh 形态，下一步可用）",
           "新建新实例" in st0["cands"][0] and st0["next_on"]
           and st0["next_text"].startswith("下一步")
           and st0["install_shown"],   # fresh 应显示安装参数
           f"{st0['next_text']}/{st0['install_shown']}")
    for i in range(1, len(s["cands"])):
        wiz.cb_found.setCurrentIndex(i)
        wait_until(lambda: "验证通过" in wiz.lb_scan.text() or "缺依赖" in wiz.lb_scan.text()
                   or "暂未找到" in wiz.lb_scan.text(), timeout=12)
        st = state(wiz)
        record(f"A1.6 候选{i}（{st['cands'][i] if i < len(st['cands']) else '?'}）进入目标态",
               st["next_on"] and st["next_text"] in ("完成", "仍要添加", "下一步：修复")
               and ("验证通过" in st["full_text"] or "缺依赖" in st["full_text"]
                    or "暂未找到" in st["full_text"]),
               st["next_text"])
    wiz.close(); win.close()


def scenario_no_python():
    log("\n【A2】完全无 Python 环境（临时移走 tools）→ 仍要添加")
    if os.path.isdir(TOOLS):
        shutil.rmtree(TOOLS_BAK, ignore_errors=True)
        shutil.move(TOOLS, TOOLS_BAK)
    try:
        win, wiz = new_wizard()
        s = detect(wiz, r"F:\ComfyHub_testdata\ComfyUIH3")
        # 行为（2026-10-24 修复流程）：无 Python → 主按钮「下一步：修复」（进修复计划）；
        # 「仍要添加」降级为下方低调逃生口按钮 b_skip_repair
        record("A2.1 主按钮=「下一步：修复」", s["next_on"] and s["next_text"] == "下一步：修复",
               f"{s['next_text']}/{s['next_on']}")
        record("A2.2 逃生口按钮可见（仍要添加/跳过修复）", wiz.b_skip_repair.isVisible())
        record("A2.3 无可见的 Finish 按钮", not s["finish_vis"])
        record("A2.4 提示无 Python 环境（全文）",
               "没有可用的 Python" in s["full_text"] or "暂未找到 Python" in s["full_text"],
               s["full_text"][:40].replace("\n", "|"))
        record("A2.5 取消仍可用（能退出）", s["cancel_on"])
        record("A2.6 不显示安装参数", not s["install_shown"])
        record("A2.7 修复模式标志已置位", getattr(wiz, "_repair_mode", False))
        wiz.close(); win.close()
    finally:
        if os.path.isdir(TOOLS_BAK):
            shutil.rmtree(TOOLS, ignore_errors=True)
            shutil.move(TOOLS_BAK, TOOLS)
            log("  （tools 已恢复）")


# ============================================================
# B. 全新安装
# ============================================================
def scenario_fresh_install():
    log("\n【B1】空目录 → 全新安装模式")
    os.makedirs(os.path.join(TMP, "Empty"), exist_ok=True)
    win, wiz = new_wizard()
    s = detect(wiz, os.path.join(TMP, "Empty"), wait=6)
    record("B1.1 显示安装参数", s["install_shown"])
    record("B1.2 下一步可用", s["next_on"] and "下一步" in s["next_text"], s["next_text"])
    record("B1.3 无候选残留", len(s["cands"]) == 0, f"{s['cands']}")
    record("B1.4 完成不可点", not (s["next_text"] == "完成"))
    wiz.close(); win.close()


def scenario_nonexistent():
    log("\n【B2】不存在的路径（父目录无实例）→ 全新安装 + 补 \\ComfyUI")
    nested = os.path.join(TMP, "NoSuch", "Deep")
    win, wiz = new_wizard()
    s = detect(wiz, nested, wait=6)
    ok_path = s["path"].lower().endswith("comfyui")
    record("B2.1 自动补 \\ComfyUI", ok_path, s["path"])
    record("B2.2 显示安装参数", s["install_shown"])
    record("B2.3 目录未被创建（零副作用）", not os.path.isdir(nested), nested)
    wiz.close(); win.close()


def scenario_comfyui_named():
    log("\n【B3】目录名含 comfyui 且为空 → 不补子目录")
    d = os.path.join(TMP, "MyComfyUI")
    os.makedirs(d, exist_ok=True)
    win, wiz = new_wizard()
    s = detect(wiz, d, wait=6)
    record("B3.1 不重复套 \\ComfyUI", os.path.normpath(s["path"]) == os.path.normpath(d), s["path"])
    wiz.close(); win.close()


# ============================================================
# C. 取消零残留
# ============================================================
def scenario_cancel_no_side_effect():
    log("\n【C1】点取消 → 不留下任何文件夹")
    target = os.path.join(TMP, "WillCancel", "ComfyUI")
    win, wiz = new_wizard()
    detect(wiz, target, wait=5)
    existed_before = os.path.isdir(os.path.dirname(target))
    wiz.reject()
    pump(0.5)
    record("C1.1 取消后目录未创建", not os.path.isdir(os.path.dirname(target)),
           f"before={existed_before}")
    wiz.close(); win.close()


# ============================================================
# D. 中文路径警告
# ============================================================
def scenario_chinese_path():
    log("\n【D1】中文路径 → 应显示警告")
    d = os.path.join(TMP, "中文目录")
    os.makedirs(d, exist_ok=True)
    win, wiz = new_wizard()
    detect(wiz, d, wait=5)
    shown = not wiz.lb_warn.isHidden()
    record("D1.1 显示中文路径警告", shown, wiz.lb_warn.text()[:30] if shown else "未显示")
    wiz.close(); win.close()


# ============================================================


# ============================================================
# E. 更多点击可能性（边界/异常/并发）
# ============================================================
def scenario_edge_cases():
    log("\n【E1】空路径 + 回车")
    win, wiz = new_wizard()
    wiz.ed_dir.setText("")
    wiz._detect_existing()
    pump(1.5)
    s = state(wiz)
    record("E1.1 空路径不崩溃", True, f"next={s['next_text']}/{s['next_on']}")
    wiz.close(); win.close()

    log("\n【E2】盘根 F:////")
    win, wiz = new_wizard()
    s = detect(wiz, "F:////", wait=12)
    # 盘根下若扫到现成实例 → 添加模式（正确，验证失败时显示「仍要添加」）；
    # 没扫到 → 补 ComfyUI（也正确）
    _ok = (s["path"].rstrip("\\/").lower().endswith("comfyui")) or \
          ("验证" in s["text"]) or (s["next_text"] == "仍要添加")
    record("E2.1 盘根行为合理（找到实例→添加 / 否则补 ComfyUI）", _ok, f"{s['path']} | {s['text'][:24]}")
    wiz.close(); win.close()

    log("\n【E3】非法字符路径")
    win, wiz = new_wizard()
    s = detect(wiz, r"F://_ui_matrix//a<b>c", wait=4)
    record("E3.1 非法字符不崩溃", True, s["text"][:30])
    record("E3.2 仍是全新安装态（可退出）", s["cancel_on"])
    wiz.close(); win.close()

    log("\n【E4】四层深路径")
    deep = os.path.join(TMP, "L1", "L2", "L3", "L4")
    win, wiz = new_wizard()
    s = detect(wiz, deep, wait=6)
    record("E4.1 深层路径补 ComfyUI", s["path"].endswith("ComfyUI"), s["path"])
    record("E4.2 未创建任何目录", not os.path.isdir(os.path.join(TMP, "L1")))
    wiz.close(); win.close()

    log("\n【E5】实例名留空 → 应自动取目录名")
    win, wiz = new_wizard()
    wiz.ed_name.setText("")
    s = detect(wiz, r"F://ComfyHub_testdata//ComfyUIH3", wait=10)
    record("E5.1 实例名自动填充", bool(s["name"].strip()), s["name"])
    wiz.close(); win.close()

    log("\n【E6】检测进行中立刻改路径（并发扫描）")
    win, wiz = new_wizard()
    wiz.ed_dir.setText(r"F://ComfyHub_testdata")
    wiz._detect_existing()
    pump(0.3)                                  # 扫描还没结束时改路径
    wiz.ed_dir.setText(r"F://ComfyHub_testdata//ComfyUIH3")
    wiz._detect_existing()
    wait_until(lambda: "验证通过" in wiz.lb_scan.text() or "缺依赖" in wiz.lb_scan.text(), timeout=15)
    s = state(wiz)
    record("E6.1 以最后一次路径为准", "ComfyUIH3" in s["path"], s["path"])
    # 2026-10-24 起：验证失败主态=「下一步：修复」；通过=「完成」；无Python=「下一步：修复」
    record("E6.2 最终状态可推进（完成/仍要添加/下一步：修复）",
           s["next_on"] and s["next_text"] in ("完成", "仍要添加", "下一步：修复"),
           f"{s['next_text']}/{s['next_on']}")
    wiz.close(); win.close()

    log("\n【E7】切换 Python 下拉选项")
    win, wiz = new_wizard()
    detect(wiz, r"F://ComfyHub_testdata//ComfyUIH3", wait=10)
    cb = wiz.cb_python
    ok_all = True
    for i in range(cb.count()):
        cb.setCurrentIndex(i)
        pump(0.3)
        if not wiz.ed_py_path.text().strip():
            ok_all = False
    record("E7.1 每个选项都有解释器路径展示", ok_all, f"{cb.count()} 个选项")
    wiz.close(); win.close()

    log("\n【E8】取消后重开，状态不残留")
    win1, wiz1 = new_wizard()
    detect(wiz1, r"F://ComfyHub", wait=10)
    wiz1.reject(); pump(0.4)
    win1.close()
    win2, wiz2 = new_wizard()
    # 新向导会自动探测本机实例——有候选属正常；关键是状态自洽
    wait_until(lambda: "验证通过" in wiz2.lb_scan.text() or "缺依赖" in wiz2.lb_scan.text()
               or "全新安装" in wiz2.lb_scan.text(), timeout=12)
    s = state(wiz2)
    _consistent = (s["next_text"] in ("完成", "仍要添加", "下一步：修复") and s["next_on"]) or \
                  (s["next_text"] == "下一步 >" and s["next_on"])
    record("E8.1 重开后状态自洽（自动探测结果或全新安装）", _consistent,
           f"cands={s['cands']} next={s['next_text']}/{s['next_on']}")
    record("E8.2 无历史错误残留（提示文字不含旧错误）",
           "无法确认" not in s["text"] or s["next_on"])
    wiz2.close(); win2.close()


def scenario_recursive_and_picker():
    """F 组：全深度递归扫描 + 下拉「新建新实例」往返（2026-10-24 新功能）"""
    # ---- F1 递归：4 层深的实例必须被发现（旧版只扫 2 层会漏） ----
    log("\n【F1】全深度递归扫描（L1/L2/L3/L4/ComfyUI）")
    deep = os.path.join(TMP, "L1", "L2", "L3", "L4", "ComfyUI")
    os.makedirs(deep, exist_ok=True)
    open(os.path.join(deep, "main.py"), "w").close()
    os.makedirs(os.path.join(deep, "comfy"), exist_ok=True)
    try:
        win, wiz = new_wizard()
        s = detect(wiz, TMP, wait=6)
        deep_found = any(os.path.normpath(c).endswith(
            os.path.join("L1", "L2", "L3", "L4", "ComfyUI")) for c in s["cands"])
        record("F1.1 4层深实例被发现", deep_found, f"{s['cands']}")
        record("F1.2 结论文案带扫描范围（已扫描…全部子目录）",
               "已扫描" in s["full_text"] and "全部子目录" in s["full_text"],
               s["full_text"][:40].replace("\n", "|"))
        # ---- F2 下拉：新建 ↔ 实例 往返 ----
        log("\n【F2】下拉「新建新实例」与实例往返")
        wiz.cb_found.setCurrentIndex(0)          # 选「新建新实例」
        pump(0.5)
        st_new = state(wiz)
        record("F2.1 选新建→fresh 形态（安装参数展开、下一步可用）",
               st_new["install_shown"] and st_new["next_on"]
               and st_new["next_text"].startswith("下一步")
               and "新建" in (st_new["cands"][0] if st_new["cands"] else ""),
               f"{st_new['next_text']}/{st_new['install_shown']}")
        _target_ok = st_new["path"].endswith("ComfyUI")
        record("F2.2 新建目标按规则推导（…\\ComfyUI）", _target_ok, st_new["path"])
        wiz.cb_found.setCurrentIndex(1)          # 切回第一个实例
        pump(0.8)
        st_back = state(wiz)
        record("F2.3 切回实例→existing 形态（安装参数收起、进入验证）",
               not st_back["install_shown"] and st_back["next_on"]
               and st_back["next_text"] in ("完成", "仍要添加", "下一步：修复"),
               f"{st_back['next_text']}/{st_back['install_shown']}")
        # cands 存的是相对扫描根的 relpath，期望绝对路径需拼回
        _expect = os.path.normpath(os.path.join(TMP, s["cands"][1]))
        record("F2.4 切回后路径同步为选中候选",
               os.path.normpath(st_back["path"]) == _expect, st_back["path"])
        # 2026-10-26：往返后标题标签必须还在（fresh 分支会 hide 它，existing 恢复）
        record("F2.5 往返后「请选择实例」标题仍在",
               wiz.lb_found_title.isVisible() and "请选择实例" in wiz.lb_found_title.text(),
               f"vis={wiz.lb_found_title.isVisible()} text={wiz.lb_found_title.text()!r}")
        wiz.close(); win.close()
    finally:
        shutil.rmtree(os.path.join(TMP, "L1"), ignore_errors=True)


def scenario_fixes_20261024():
    """G 组：2026-10-24 四项修复的回归（复选框样式 / 日志着色 / torch 损坏判定 / 磁盘预检）"""
    from app.ui import theme, setup_wizard
    from app import bootstrap

    log("\n【G1】复选框 indicator 样式（修复「打勾无框」）")
    record("G1.1 DARK_QSS 含 QCheckBox::indicator 边框样式",
           "QCheckBox::indicator" in theme.DARK_QSS
           and "border" in theme.DARK_QSS.split("QCheckBox::indicator")[1][:200])

    log("\n【G2】日志分阶段着色分类")
    cases = [
        ("── 步骤 4/4：检测显卡并安装 PyTorch ──", "#6fc3ff", True),
        ("$ python -s -m pip install torch …", "#8fa3bf", False),
        ("❌ pip 退出码: 1", "#ff6b6b", False),
        ("⚠️ 首次安装失败，自动换镜像重试", "#f0a04b", False),
        ("✅ torch 2.14.1 安装完成并通过导入自检", "#5cc98f", False),
        ("↓ ComfyUI_src.zip: 12.8/12.8 MB (100%)", "#7ec7ff", False),
        ("  Collecting torch (from ...)", "#c8d2e0", False),
    ]
    all_ok = True
    for msg, exp_color, exp_bold in cases:
        c, b = setup_wizard._LogPage._log_css(msg)
        if (c, b) != (exp_color, exp_bold):
            all_ok = False
            log(f"    ✗ 分类不符: {msg[:30]!r} → {(c, b)}，期望 {(exp_color, exp_bold)}")
    record("G2.1 七类日志行颜色分类全部正确", all_ok)

    log("\n【G3】torch 损坏 vs 未安装 判定（真实环境）")
    py = os.path.join(TOOLS, "python", "python.exe")
    if os.path.isfile(py):
        r = bootstrap.run([py, "-c", "import torch"],
                          capture_output=True, text=True, timeout=300)
        st, info, _ = bootstrap.detect_torch_step(py)
        if r.returncode == 0:
            record("G3.1 torch 可导入 → 判 ok/cpu", st in ("ok", "cpu"), f"{st}:{info[:50]}")
        elif st == "missing":
            # 便携 Python 里根本没装 torch（真实环境态）——missing 判定正确，
            # 只是没有崩溃样本可验证 broken 路径 → skip 不算失败
            record("G3.1 便携 Python 无 torch（无崩溃样本，跳过 broken 判定）",
                   True, "skip")
        else:
            record("G3.1 torch 导入崩溃 → 判 broken（非 missing）",
                   st == "broken", f"{st}:{info[:60]}")
        free = bootstrap._free_gb(py)
        record("G3.2 _free_gb 返回正数", free is not None and free >= 0, f"{free}")
    else:
        record("G3.1 无便携 Python，跳过 torch 判定", True, "skip")

    log("\n【G4】broken 决策按钮（强制重装 + 必需步骤禁跳过）")
    win, wiz = new_wizard()
    try:
        page = wiz.page(1)   # _LogPage
        page.state["total"] = 4
        page.cur = 0         # 非末步 → 必需
        page._show_decision("broken", "x")
        record("G4.1 broken 主按钮=强制重装", page.b_act.text().startswith("🔧"),
               page.b_act.text())
        record("G4.2 非末步 broken 禁止跳过", not page.b_skip.isEnabled(),
               f"enabled={page.b_skip.isEnabled()}")
        page.cur = 3         # 末步（PyTorch）→ 可跳过
        page._show_decision("broken", "x")
        record("G4.3 末步 broken 允许跳过", page.b_skip.isEnabled(),
               f"enabled={page.b_skip.isEnabled()}")
    finally:
        wiz.close(); win.close()


def scenario_sibling_scan():
    """H 组：选中某个实例目录时，应扫父目录带出全部兄弟实例（2026-10-25 修复）"""
    log("\n【H1】三实例并列，直接选中其中一个 → 兄弟全带出、默认选中它")
    base = os.path.join(TMP, "sib")
    made = []
    for nm in ("ComfyUI", "ComfyUIH3", "ComfyUIImage"):
        d = os.path.join(base, nm)
        os.makedirs(os.path.join(d, "comfy"), exist_ok=True)
        open(os.path.join(d, "main.py"), "w").close()
        made.append(d)
    picked = made[1]                      # 选中中间的 ComfyUIH3
    try:
        win, wiz = new_wizard()
        s = detect(wiz, picked, wait=6)
        cands = s["cands"]
        has_all = (any("ComfyUIH3" in c for c in cands)
                   and any("ComfyUIImage" in c for c in cands)
                   and any(c == "(此目录)" or c.endswith("ComfyUI") for c in cands))
        record("H1.1 选中实例后兄弟实例全被带出（≥3 项含新建）",
               s["cand_vis"] and len(cands) >= 4 and has_all, f"{cands}")
        record("H1.2 扫描范围=父目录（同级目录）",
               "同级目录" in s["full_text"] or os.path.normpath(base) in s["full_text"],
               s["full_text"][:50].replace("\n", "|"))
        # 默认选中的应是用户点的那个（ComfyUIH3），路径同步到它
        _sel = wiz.cb_found.currentIndex()
        _sel_txt = cands[_sel] if 0 <= _sel < len(cands) else ""
        record("H1.3 默认选中用户点的实例（非固定第一个）",
               "ComfyUIH3" in _sel_txt,
               f"idx={_sel} 选中={_sel_txt}")
        record("H1.4 路径同步为选中的实例",
               os.path.normpath(s["path"]).casefold()
               == os.path.normpath(picked).casefold(), s["path"])
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def scenario_integration_package():
    """I 组：整合包根目录识别（2026-10-25）
    布局复刻两大主流整合包：
      · ComfyUI_windows_portable：根目录 ComfyUI\（实例）+ python_embeded\（Python）
      · ComfyUI-aki-v3（绘世）：根目录 ComfyUI\（实例）+ python\（Python）
    选根目录 → 应扫出子目录里的实例，且 Python 匹配到根目录的配套解释器"""
    log("\n【I1】portable 布局：选根目录 → 实例+python_embeded 匹配")
    base_p = os.path.join(TMP, "pkg_portable")
    try:
        # 复刻官方便携包结构
        os.makedirs(os.path.join(base_p, "ComfyUI", "comfy"), exist_ok=True)
        open(os.path.join(base_p, "ComfyUI", "main.py"), "w").close()
        os.makedirs(os.path.join(base_p, "python_embeded"), exist_ok=True)
        open(os.path.join(base_p, "python_embeded", "python.exe"), "w").close()
        win, wiz = new_wizard()
        s = detect(wiz, base_p, wait=6)
        record("I1.1 根目录扫出子目录实例",
               any(c.endswith("ComfyUI") for c in s["cands"]), f"{s['cands']}")
        _py = wiz._py_map.get(os.path.normpath(
            os.path.join(base_p, "ComfyUI")), [])
        record("I1.2 Python 匹配到 ../python_embeded/python.exe",
               any(p.replace("/", "\\").endswith(
                   "python_embeded\\python.exe") for p in _py), f"{_py}")
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base_p, ignore_errors=True)

    log("\n【I2】绘世(aki) 布局：选根目录 → 实例+python 匹配")
    base_a = os.path.join(TMP, "pkg_aki")
    try:
        os.makedirs(os.path.join(base_a, "ComfyUI", "comfy"), exist_ok=True)
        open(os.path.join(base_a, "ComfyUI", "main.py"), "w").close()
        os.makedirs(os.path.join(base_a, "python"), exist_ok=True)
        open(os.path.join(base_a, "python", "python.exe"), "w").close()
        win, wiz = new_wizard()
        s = detect(wiz, base_a, wait=6)
        record("I2.1 根目录扫出子目录实例",
               any(c.endswith("ComfyUI") for c in s["cands"]), f"{s['cands']}")
        _py = wiz._py_map.get(os.path.normpath(
            os.path.join(base_a, "ComfyUI")), [])
        record("I2.2 Python 匹配到 ../python/python.exe",
               any(p.replace("/", "\\").endswith(
                   "\\python\\python.exe") for p in _py), f"{_py}")
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base_a, ignore_errors=True)


def scenario_existing_intercept():
    """J 组：existing 模式翻页拦截（2026-10-26 aki 整合包「完成」卡死回归）
    真实事故：验证通过点「完成」→ Qt 翻页进 p2 → cb_python 无数据 py=None →
    步骤1 报"未检测到 Python 解释器"卡死。修复后翻页必须被拦截：
      · 不启动安装流程 · setup_result 用验证过的解释器 · 不停留在安装页
    修复模式（_repair_mode=True）不受拦截影响"""
    log("\n【J1】existing 模式翻页进 p2 → 必须拦截并直接完成注册")
    base = os.path.join(TMP, "pkg_intercept")
    try:
        os.makedirs(os.path.join(base, "ComfyUI", "comfy"), exist_ok=True)
        open(os.path.join(base, "ComfyUI", "main.py"), "w").close()
        os.makedirs(os.path.join(base, "python"), exist_ok=True)
        open(os.path.join(base, "python", "python.exe"), "w").close()
        comfy = os.path.normpath(os.path.join(base, "ComfyUI"))
        fake_py = os.path.normpath(os.path.join(base, "python", "python.exe"))
        win, wiz = new_wizard()
        # 手工构造"验证通过"终态（矩阵环境的假 python.exe 跑不了真验证）
        wiz._scan_candidates = [comfy]
        wiz.cb_found.blockSignals(True)
        wiz.cb_found.clear()
        wiz.cb_found.addItem("ComfyUI")
        wiz.cb_found.setCurrentIndex(0)
        wiz.cb_found.blockSignals(False)
        wiz.cb_found.show()
        wiz._py_map = {comfy: [fake_py]}
        wiz._verify_cache = {comfy: fake_py}
        wiz._set_mode("existing")
        wiz.install_opts.hide()
        wiz._repair_mode = False
        wiz._started = False
        # 等后台自动探测线程收尾——accept 的 busy 检查遇活线程会弹框阻塞测试
        wait_until(lambda: all(
            getattr(wiz, a, None) is None or not getattr(wiz, a).isRunning()
            for a in ("_scan_worker", "_verify_worker")), timeout=10)
        p2_idx = next(pid for pid in wiz.pageIds() if wiz.page(pid) is wiz.p2)
        wiz._on_page_change(p2_idx)          # 模拟 Qt 翻页（真实事故的触发点）
        pump(0.6)
        record("J1.1 未启动安装流程（p2 无 target_dir）",
               "target_dir" not in (wiz.p2.state or {}))
        record("J1.2 setup_result 组装且用验证过的解释器",
               wiz.setup_result.get("comfyui_path") == comfy
               and wiz.setup_result.get("python_path") == fake_py,
               f"path={wiz.setup_result.get('comfyui_path')} "
               f"py={wiz.setup_result.get('python_path')}")
        record("J1.3 向导未停留在安装页", wiz.currentId() != p2_idx)
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)

    log("\n【J2】_repair_mode=True 翻页 → 不受拦截，正常进修复")
    base2 = os.path.join(TMP, "pkg_intercept_r")
    try:
        os.makedirs(os.path.join(base2, "ComfyUI", "comfy"), exist_ok=True)
        open(os.path.join(base2, "ComfyUI", "main.py"), "w").close()
        comfy2 = os.path.normpath(os.path.join(base2, "ComfyUI"))
        win, wiz = new_wizard()
        wiz._scan_candidates = [comfy2]
        wiz.cb_found.blockSignals(True)
        wiz.cb_found.clear()
        wiz.cb_found.addItem("ComfyUI")
        wiz.cb_found.setCurrentIndex(0)
        wiz.cb_found.blockSignals(False)
        wiz._py_map = {comfy2: []}
        wiz._set_mode("existing")
        wiz._repair_mode = True              # 失败态主推修复的场景
        wiz._started = False
        called = {}
        wiz.p2.start_repair = lambda *a, **k: called.setdefault("args", a)
        wiz._on_page_change(next(pid for pid in wiz.pageIds() if wiz.page(pid) is wiz.p2))
        pump(0.3)
        record("J2.1 修复模式正常调用 start_repair（不被拦截）", "args" in called)
        wiz._started = True                  # 防 close 前再触发
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base2, ignore_errors=True)


def scenario_pick_no_append():
    """K 组：浏览选目录不再无条件拼 \\ComfyUI（2026-10-26 用户实测：选 F:\\
    被拼成不存在的 F:\\ComfyUI 且误判全新安装）。
    _pick 的决策已下沉到 _detect_existing + _derive_target，这里直接驱动同路径：
    · 选含实例的目录 → 添加模式列候选（路径不被拼改）
    · 选空目录       → fresh 模式，目标自动补 \\ComfyUI（与手动输入同口径）"""
    log("\n【K1】选含实例目录 → 添加模式、不拼名")
    base = os.path.join(TMP, "pickbase")
    inst = os.path.join(base, "ComfyUI")
    os.makedirs(os.path.join(inst, "comfy"), exist_ok=True)
    open(os.path.join(inst, "main.py"), "w").close()
    try:
        win, wiz = new_wizard()
        wiz.ed_dir.setText(base)          # 等价 _pick 拿到目录后的 setText
        wiz._detect_existing()
        wait_until(lambda: "验证" in wiz.lb_scan.text()
                   or "全新安装" in wiz.lb_scan.text(), timeout=15)
        pump(0.3)
        record("K1.1 路径未被拼改（保持用户所选目录）",
               os.path.normpath(wiz.ed_dir.text()) in
               (os.path.normpath(base), os.path.normpath(inst)),
               wiz.ed_dir.text())
        record("K1.2 添加实例模式（安装参数隐藏）", wiz.install_opts.isHidden())
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)

    log("\n【K2】选空目录 → fresh 模式目标补 \\ComfyUI")
    base2 = os.path.join(TMP, "pickempty")
    os.makedirs(base2, exist_ok=True)
    try:
        win, wiz = new_wizard()
        wiz.ed_dir.setText(base2)
        wiz._detect_existing()
        wait_until(lambda: "全新安装" in wiz.lb_scan.text(), timeout=15)
        pump(0.3)
        record("K2.1 安装目标自动补 \\ComfyUI",
               os.path.normpath(wiz.ed_dir.text()) ==
               os.path.normpath(os.path.join(base2, "ComfyUI")),
               wiz.ed_dir.text())
        record("K2.2 fresh 模式（安装参数展开）", not wiz.install_opts.isHidden())
        wiz.close(); win.close()
    finally:
        shutil.rmtree(base2, ignore_errors=True)


def scenario_plugin_toggle():
    """L 组：插件页启用/禁用的路径决策（2026-10-26 用户实测 aki 实例
    GGUF 插件报 WinError 2：旧逻辑拿去掉了 .disabled 的显示名砍 9 个字符，
    把 'ComfyUI-GGUF' 砍成 'Com'）。
    直接驱动 PluginsPage._toggle，验证三个分支的 src/dst 决策。"""
    log("\n【L1】已禁用插件 → 启用（name.disabled → name）")
    base = os.path.join(TMP, "plugtoggle")
    nodes = os.path.join(base, "custom_nodes")
    os.makedirs(os.path.join(nodes, "ComfyUI-GGUF.disabled"), exist_ok=True)
    os.makedirs(os.path.join(nodes, "other-plugin"), exist_ok=True)
    try:
        win = MainWindow()
        pg = win.plugins_page
        # 隔离：实例指向临时目录，refresh_list 用真实 list_plugins 跑通
        pg._inst = lambda: {"name": "t", "comfyui_path": base}
        pg.refresh_list()
        pump(0.3)
        # 表里应有两行；找到显示名为 ComfyUI-GGUF 的行
        rows = {pg.tb.item(r, 0).text(): r for r in range(pg.tb.rowCount())}
        record("L1.1 列表含规范化显示名（无 .disabled 后缀）",
               "ComfyUI-GGUF" in rows, str(list(rows)))

        class _Idx:
            def __init__(self, r):
                self._r = r

            def row(self):
                return self._r

        pg._toggle(_Idx(rows["ComfyUI-GGUF"]))
        pump(0.3)
        ok_on = os.path.isdir(os.path.join(nodes, "ComfyUI-GGUF"))
        ok_off = not os.path.isdir(os.path.join(nodes, "ComfyUI-GGUF.disabled"))
        record("L1.2 目录被正确启用（.disabled 后缀剥掉）", ok_on and ok_off)

        log("\n【L2】启用中插件 → 禁用（name → name.disabled）")
        rows = {pg.tb.item(r, 0).text(): r for r in range(pg.tb.rowCount())}
        pg._toggle(_Idx(rows["ComfyUI-GGUF"]))
        pump(0.3)
        ok_off2 = os.path.isdir(os.path.join(nodes, "ComfyUI-GGUF.disabled"))
        ok_on2 = not os.path.isdir(os.path.join(nodes, "ComfyUI-GGUF"))
        record("L2.1 目录被正确禁用（追加 .disabled 后缀）", ok_off2 and ok_on2)

        log("\n【L3】两个分支后无 'Com' 截断残留（旧 bug 特征）")
        names = os.listdir(nodes)
        record("L3.1 custom_nodes 无异常短名目录",
               all(n in ("ComfyUI-GGUF.disabled", "other-plugin") for n in names),
               str(names))
        win.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    log("=" * 70)
    log("ComfyHub 向导第一页 · 点击与按钮状态矩阵测试")
    log("=" * 70)
    os.makedirs(TMP, exist_ok=True)
    try:
        scenario_multi_instance()
        scenario_no_python()          # 含 tools 备份/恢复
        scenario_fresh_install()
        scenario_nonexistent()
        scenario_comfyui_named()
        scenario_cancel_no_side_effect()
        scenario_chinese_path()
        scenario_recursive_and_picker()
        scenario_fixes_20261024()
        scenario_sibling_scan()
        scenario_integration_package()
        scenario_existing_intercept()   # 2026-10-26 aki「完成」卡死回归
        scenario_pick_no_append()       # 2026-10-26 浏览选目录不拼名
        scenario_plugin_toggle()        # 2026-10-26 插件启用/禁用路径决策
        scenario_edge_cases()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        if os.path.isdir(TOOLS_BAK) and not os.path.isdir(TOOLS):
            shutil.move(TOOLS_BAK, TOOLS)

    log("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    log(f"结果：{passed}/{total} 通过")
    for name, ok, detail in RESULTS:
        if not ok:
            log(f"  ❌ {name}  [{detail}]")
    log("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
