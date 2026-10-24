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
        wiz.close(); win.close()
    finally:
        shutil.rmtree(os.path.join(TMP, "L1"), ignore_errors=True)


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
