# -*- coding: utf-8 -*-
"""QA 独立回归：局域网「需重启」提示（2026-12 用户报障验收）

QA（严过关）独立验证用例 —— 不复用工程师 tests/test_save_restart_gate.py 的断言，
按验收判据逐条证伪：

  V1 保存门控（6 个场景，含逐字相等、双字段、is_running 抛异常落盘）
  V2 字段清单：对 app/process.py build_command/start/env_block_reason 做 AST 级
     inst 键读取扫描，证明 21 个键既无漏判也无误判；核对 output_dir 等 4 个
     「启动时读取但不在 _TRACK」的说法
  V3 改动判定口径：_restart_required_keys 与 diff_changes 随机对拍
  V4 i18n：运行时 tooltip 逐字核对 EN_DICT + en↔zh 双向实测
  V8 找茬：标签兜底、遮罩键交集、_Row 不覆盖 tooltip、_save 代码顺序、
     _loaded=None 回落分支不产生假提示

用法: .venv_build/Scripts/python.exe tests/test_qa_lan_restart.py
"""
import ast
import os
import pathlib
import random
import re
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication(sys.argv[:1])

RESULTS = []
ZH_TIP = ("允许同一局域网其他设备（手机/平板）打开 ComfyUI；"
          "该选项在实例启动时生效，实例运行中修改后需重启实例才能生效。")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  [{detail}]" if detail and not cond else ""))


def pump(sec=0.15):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


# =====================================================================
# V1 —— 保存门控（独立用例）
# =====================================================================
class FakeProcs:
    def __init__(self):
        self.mode = "false"

    def is_own(self, inst):
        return False

    def is_running(self, inst):
        if self.mode == "boom":
            raise RuntimeError("simulated ext-probe failure")
        return self.mode == "true"

    def get(self, inst):
        return None


class FakeMain:
    def __init__(self, cfg, inst):
        self.cfg = cfg
        self.inst = inst
        self.procs = FakeProcs()

    def current_inst(self):
        return self.inst

    def _sync_instances(self):
        pass


def v1_gate():
    from app import config as ACFG
    from app.ui import changes_dialog
    from app.ui.pages import comfyui_page as CP

    inst = {"id": "qa1", "name": "QA门控", "kind": "local",
            "comfyui_path": "C:/fake/ComfyUI", "python_path": "C:/fake/python.exe",
            "port": 8188, "vram_mode": "自动", "extra_args": "",
            "lan_access": False}
    cfg = {"instances": [inst], "settings": {}}

    saved = []
    real_acfg_save, real_cp_save = ACFG.save_config, CP.save_config
    ACFG.save_config = CP.save_config = lambda c: saved.append(c)

    real_info, real_warn = QMessageBox.information, QMessageBox.warning
    msgs = []
    QMessageBox.information = staticmethod(
        lambda *a, **k: (msgs.append(str(a[2]) if len(a) > 2 else ""),
                         QMessageBox.Ok)[1])
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)

    real_confirm = changes_dialog.confirm_changes
    confirms = []
    changes_dialog.confirm_changes = lambda *a, **k: (confirms.append(a), True)[1]

    try:
        mainwin = FakeMain(cfg, inst)
        page = CP.ComfyuiPage(cfg, mainwin)
        page.show()
        pump(0.2)
        page._insecure_modal = lambda i, n: "ok"
        base_msg = "✅ 设置已保存"

        # ---- 1. 运行中 + 改 lan_access → 追加提示 + 列出中文字段名 ----
        mainwin.procs.mode = "true"
        page.ck_lan.setChecked(True)
        msgs.clear()
        page._save()
        pump(0.1)
        m = msgs[-1] if msgs else ""
        check("V1-1 运行中改 lan_access → 追加重启提示并列「· 局域网访问」",
              m.startswith(base_msg) and "需重启实例才生效" in m
              and "· 局域网访问" in m and "停止并重新启动" in m
              and "· 启动端口" not in m, repr(m))

        # ---- 2. 未运行 + 改 lan_access → 弹窗逐字等于原样 ----
        mainwin.procs.mode = "false"
        page.ck_lan.setChecked(False)
        msgs.clear()
        page._save()
        pump(0.1)
        check("V1-2 未运行改 lan_access → 弹窗逐字等于「✅ 设置已保存」",
              msgs and msgs[-1] == base_msg, repr(msgs))

        # ---- 3. 运行中 + 只改 name → 原样 ----
        mainwin.procs.mode = "true"
        page.ed_name.setText("改名了")
        msgs.clear()
        page._save()
        pump(0.1)
        check("V1-3 运行中只改 name → 弹窗原样",
              msgs and msgs[-1] == base_msg, repr(msgs))

        # ---- 4. 运行中 + 只改 base_url → 原样（base_url 不属启动类）----
        page.ed_url.setText("http://10.0.0.9:8188")
        msgs.clear()
        page._save()
        pump(0.1)
        check("V1-4 运行中只改 base_url → 弹窗原样",
              msgs and msgs[-1] == base_msg, repr(msgs))

        # ---- 5. 运行中 + 同时改 lan_access 和 port → 列出两项 ----
        page.ck_lan.setChecked(True)
        page.sp_port.setValue(8189)
        msgs.clear()
        page._save()
        pump(0.1)
        m = msgs[-1] if msgs else ""
        check("V1-5 运行中同时改 lan_access+port → 两项都列出",
              m.startswith(base_msg) and "· 局域网访问" in m
              and "· 启动端口" in m, repr(m))

        # ---- 6. 运行中 + 只改 ssh_host（运行时字段）→ 原样 ----
        page.remote_ops.ed_host.setText("10.1.1.1")
        msgs.clear()
        page._save()
        pump(0.1)
        check("V1-6 运行中只改 ssh_host → 弹窗原样",
              msgs and msgs[-1] == base_msg, repr(msgs))

        # ---- 7. is_running 抛异常 → 照常落盘 + 弹窗原样 ----
        mainwin.procs.mode = "boom"
        page.ck_lan.setChecked(False)             # lan 当前 True → 改回构成改动
        msgs.clear()
        saved.clear()
        confirms.clear()
        page._save()
        pump(0.1)
        check("V1-7a 抛异常 → 弹窗仍是原样",
              msgs and msgs[-1] == base_msg, repr(msgs))
        check("V1-7b 抛异常 → 写回 inst",
              inst.get("lan_access") is False, str(inst.get("lan_access")))
        check("V1-7c 抛异常 → save_config 被调（落盘）",
              bool(saved) and saved[-1]["instances"][0].get("lan_access") is False,
              f"save_config 调用 {len(saved)} 次")
        check("V1-7d 抛异常 → 保存确认框照常弹出（没被吞）", bool(confirms),
              f"confirm 调用 {len(confirms)} 次")
        mainwin.procs.mode = "false"

        page.close()
        return page, inst, cfg, mainwin
    finally:
        ACFG.save_config, CP.save_config = real_acfg_save, real_cp_save
        QMessageBox.information, QMessageBox.warning = real_info, real_warn
        changes_dialog.confirm_changes = real_confirm


# =====================================================================
# V2 —— 字段清单：AST 扫描 process.py 启动路径读取的 inst 键
# =====================================================================
def _fn_reads(src_path, fn_names):
    """返回 {函数名: 该函数体内从 inst 读取的字符串键集合}（AST 级，非正则）"""
    tree = ast.parse(pathlib.Path(src_path).read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in fn_names and node.name not in out:
            keys = set()
            for n in ast.walk(node):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                        and n.func.attr == "get" and isinstance(n.func.value, ast.Name) \
                        and n.func.value.id == "inst" and n.args \
                        and isinstance(n.args[0], ast.Constant) \
                        and isinstance(n.args[0].value, str):
                    keys.add(n.args[0].value)
                if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                        and n.value.id == "inst" and isinstance(n.slice, ast.Constant) \
                        and isinstance(n.slice.value, str):
                    keys.add(n.slice.value)
            out[node.name] = keys
    return out


def v2_keyset():
    from app.ui.changes_dialog import _RESTART_AT_START_KEYS
    from app.ui.pages.comfyui_page import ComfyuiPage

    # 验收基准（QA 自己读 build_command/start 逐字段列出，独立于工程师的清单）
    expected = {
        "lan_access", "port", "python_path", "vram_mode", "engine",
        "cross_attn", "preview_method", "precision", "fast_mode",
        "no_smart_memory", "cpu_vae", "deterministic", "channels_last",
        "cuda_malloc", "multi_user", "auto_open_browser", "models_dir",
        "mount_enabled", "mount_dir", "extra_args", "hf_offline",
    }
    check("V2-1 集合恰为 21 个且与 QA 独立核对结果一致",
          set(_RESTART_AT_START_KEYS) == expected
          and len(_RESTART_AT_START_KEYS) == 21,
          f"diff={set(_RESTART_AT_START_KEYS) ^ expected}")

    reads = _fn_reads(os.path.join(ROOT, "app", "process.py"),
                      {"build_command", "start", "env_block_reason"})
    launch = set().union(*reads.values())
    # engine 落盘是 engine_backend/engine_index 两个 inst 键 → 映射回快照键 "engine"
    launch_snap = {("engine" if k in ("engine_backend", "engine_index") else k)
                   for k in launch}
    track = set(ComfyuiPage._TRACK)

    # 2a 漏判：启动路径读取的 _TRACK 字段必须全部在集合里
    miss = (launch_snap & track) - set(_RESTART_AT_START_KEYS)
    check("V2-2 启动路径读取的 _TRACK 字段无漏判", not miss, str(sorted(miss)))

    # 2b 挂载两键经 model_paths.sync_instance_config 读取（build_command 调用链）
    mp = pathlib.Path(ROOT, "app", "model_paths.py").read_text(encoding="utf-8")
    bc_src = pathlib.Path(ROOT, "app", "process.py").read_text(encoding="utf-8")
    check("V2-3 mount_enabled/mount_dir 走启动链（_instance_extra_config → "
          "model_paths 读取）",
          "_instance_extra_config(inst)" in bc_src
          and "mount_enabled" in mp and "mount_dir" in mp
          and {"mount_enabled", "mount_dir"} <= set(_RESTART_AT_START_KEYS))

    # 2c 误判：集合里每个键都必须真的在启动路径被读取
    extra = set(_RESTART_AT_START_KEYS) - launch_snap - {"mount_enabled", "mount_dir"}
    check("V2-4 集合内无「运行时可变」的误判键（每个键都在启动路径被读）",
          not extra, str(sorted(extra)))

    # 2d 工程师说法核实：output_dir/input_dir/base_directory/proxy 启动时读、
    #     但确实不在本页 _TRACK → 未纳入是正确的
    claimed = {"output_dir", "input_dir", "base_directory", "proxy"}
    check("V2-5 output_dir/input_dir/base_directory/proxy 确在启动路径读取",
          claimed <= launch, str(sorted(claimed - launch)))
    check("V2-6 这 4 个键确实不在本页 _TRACK（未纳入集合是正确的）",
          not (claimed & track), str(sorted(claimed & track)))

    # 2e 误判反向：_TRACK 里、不属集合的 25 个键必须都是 ComfyHub 侧运行时配置
    non_restart = track - set(_RESTART_AT_START_KEYS)
    allowed = {"name", "kind", "base_url", "auth_type", "auth_value",
               "auth_header_name", "auth_user", "auth_pass", "tls_verify",
               "ssh_host", "ssh_port", "ssh_user", "ssh_auth", "ssh_password",
               "ssh_key_path", "ssh_key_passphrase", "ssh_start_cmd",
               "ssh_stop_cmd", "ssh_host_fingerprint", "cloud_start",
               "cloud_stop", "tunnel_local_port", "tunnel_remote_host",
               "tunnel_remote_port", "insecure_warn_muted", "last_ops_channel"}
    check("V2-7 _TRACK 中不在集合的键恰为预期的运行时配置",
          non_restart <= allowed and not (non_restart & expected),
          f"越界={sorted(non_restart - allowed)}")


# =====================================================================
# V3 —— 改动判定口径：与 diff_changes 随机对拍
# =====================================================================
def v3_parity():
    from app.ui.changes_dialog import (_RESTART_AT_START_KEYS,
                                       _restart_required_keys, diff_changes)

    rng = random.Random(20261201)
    pool = [None, "", 0, False, True, "x", 8188, 9000, "自动", "强制 FP16",
            ("cuda", 0), ("auto", None), ("cpu", None), ("dml", 1),
            {"url": ""}, "C:/path"]
    other = ["name", "base_url", "kind", "ssh_host"]
    keys = sorted(_RESTART_AT_START_KEYS) + other
    bad = None
    for _ in range(3000):
        old, new = {}, {}
        for k in keys:
            if rng.random() < 0.45:
                continue                        # 一侧缺失
            old[k] = rng.choice(pool)
            if rng.random() < 0.45:
                continue                        # 无改动
            new[k] = rng.choice(pool)
        # 只在 new 里出现的键（diff_changes 的「新增」分支）
        for k in keys:
            if rng.random() < 0.15:
                new[k] = rng.choice(pool)
        diff_keys = [lab for lab, _o, _n in diff_changes(old, new, {})]
        rk = _restart_required_keys(old, new)
        want = [k for k in new if k in _RESTART_AT_START_KEYS and k in diff_keys]
        if rk != want or set(rk) != set(diff_keys) & set(_RESTART_AT_START_KEYS):
            bad = (old, new, rk, want, diff_keys)
            break
    check("V3-1 3000 组随机对拍：_restart_required_keys ≡ "
          "diff_changes∩启动键（顺序也一致）", bad is None,
          str(bad)[:400])

    # 显式反例搜寻：两侧口径必须逐条一致
    cases = [
        ({}, {"lan_access": True}),          # 新增真值
        ({}, {"lan_access": False}),         # 新增空值
        ({"port": None}, {"port": ""}),      # None↔""
        ({"port": 8188}, {"port": ""}),      # 清空
        ({"extra_args": None}, {"extra_args": ""}),
        ({"port": 8188}, {"port": 8188}),    # 无改动
        ({"engine": ("cuda", 0)}, {"engine": ("auto", None)}),
        ({"engine": ("cuda", 0)}, {"engine": ("cuda", 0)}),
        ({"hf_offline": False}, {"hf_offline": True}),
    ]
    diffs = []
    for o, n in cases:
        dk = {lab for lab, _a, _b in diff_changes(o, n, {})}
        rk = set(_restart_required_keys(o, n))
        if rk != (dk & set(_RESTART_AT_START_KEYS)):
            diffs.append((o, n, sorted(rk), sorted(dk)))
    check("V3-2 显式反例（9 组）口径无差异", not diffs, str(diffs))

    # 真实调用链的输入差异：_save 里 diff 用遮罩后的 _old_disp/_disp，
    # 重启判定用原始 old/new —— 遮罩只碰 4 个密钥键，且它们都不是启动键
    masked = {"auth_value", "auth_pass", "ssh_password", "ssh_key_passphrase"}
    check("V3-3 遮罩键与启动键集合无交集（两份输入对启动键等价）",
          not (masked & set(_RESTART_AT_START_KEYS)))


# =====================================================================
# V4 —— i18n：tooltip 词条逐字一致 + 中英双向切换实测
# =====================================================================
def v4_i18n(page):
    from app import i18n
    from app.i18n_en import EN_DICT

    tip = page.ck_lan.toolTip()
    check("V4-1 运行时 tooltip 与验收文案逐字一致", tip == ZH_TIP, repr(tip))
    check("V4-2 两态都不为假（启动时生效 + 运行中改需重启都在）",
          "启动时生效" in tip and "运行中修改后需重启实例才能生效" in tip, repr(tip))
    check("V4-3 EN_DICT 有逐字词条", tip in EN_DICT, "缺词条")
    en = EN_DICT.get(tip, "")
    check("V4-4 英文词条非空", bool(en) and en != tip, repr(en))

    # AST 静态扫描视角（test_theme_i18n 6a 同口径）：相邻字面量拼接后能被扫到
    src = pathlib.Path(ROOT, "app", "ui", "pages", "comfyui_page.py") \
        .read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = None
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "setToolTip" \
                and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str) and "局域网" in n.args[0].value:
            found = n.args[0].value
    check("V4-5 AST 拼接常量 == 生效字符串且长度<90（6a 扫描覆盖）",
          found == tip and len(tip) < 90, f"found={found!r} len={len(tip)}")

    # en → zh 实测（正向 T + 控件树双向翻译往返）
    i18n.set_lang("en")
    check("V4-6 en 模式 T(zh) == 英文词条", i18n.T(tip) == en, repr(i18n.T(tip)))
    n_en = i18n._translate_tree(page, "en")
    got_en = page.ck_lan.toolTip()
    i18n.set_lang("zh")
    i18n._translate_tree(page, "zh")
    got_zh = page.ck_lan.toolTip()
    check("V4-7 控件树 en→zh 往返后 tooltip 复原且中间态是英文",
          got_en == en and got_zh == tip and n_en > 0,
          f"en={got_en!r} zh={got_zh!r} n={n_en}")
    check("V4-8 反向表可恢复（en→zh 查得到）",
          i18n._EN_REV.get(en) == tip, str(i18n._EN_REV.get(en)))


# =====================================================================
# V8 —— 找茬
# =====================================================================
def v8_scraps(page, inst, cfg, mainwin):
    from app.ui.changes_dialog import _RESTART_AT_START_KEYS
    from app.ui.pages import comfyui_page as CP

    # 8a 提示文案标签兜底：21 个键全有中文标签（否则弹窗里直接露英文 key）
    missing = [k for k in _RESTART_AT_START_KEYS
               if k not in CP.ComfyuiPage._SAVE_LABELS]
    check("V8-1 启动键全部有中文标签（不会露英文 key）", not missing,
          str(missing))

    # 8b _Row 不覆盖 ck_lan 的 tooltip
    row_src = pathlib.Path(ROOT, "app", "ui", "pages", "comfyui_page.py") \
        .read_text(encoding="utf-8")
    m = re.search(r"class _Row\(.*?(?=\nclass )", row_src, re.S)
    check("V8-2 _Row 构造不调 setToolTip（不会盖掉新文案）",
          m is not None and "setToolTip" not in m.group(0))

    # 8c _save 代码顺序：确认框 → 明文告警 → 取 _was_running → 写回 → 保存
    sm = re.search(r"    def _save\(self\):.*?(?=\n    def _insecure_modal)",
                   row_src, re.S)
    body = sm.group(0) if sm else ""
    i_modal = body.find('if self._insecure_modal(inst, new) == "abort"')
    i_run = body.find("_was_running = bool(self.mainwin.procs.is_running(inst))")
    i_write = body.find("for k in self._TRACK:")
    i_save = body.find("save_config(self.cfg)")
    # 注释里也有 `except Exception: base = ""` 字样 → 必须按行首匹配真语句
    m_exc = re.search(r"^[ \t]*except Exception:", body[i_run:], re.M)
    i_exc = i_run + m_exc.start() if m_exc else -1
    i_false = body.find("_was_running = False", i_run)
    check("V8-3 取 _was_running 的位置：明文告警之后、写回之前",
          0 < i_modal < i_run < i_write < i_save,
          f"modal={i_modal} run={i_run} write={i_write} save={i_save}")
    check("V8-4 is_running 包在 try/except 里且回落 False",
          0 < i_run < i_exc < i_false < i_write,
          f"run={i_run} exc={i_exc} false={i_false}")
    check("V8-5 门控条件为 `if _rk and _was_running`（未运行绝不追加）",
          "if _rk and _was_running:" in body)

    # 8c' 明文告警 abort 提前返回 → 不会碰 is_running / 不会写盘
    check("V8-6 明文告警 abort 的 return 在取 _was_running 之前",
          0 < i_modal < i_run and body.find(
              'if self._insecure_modal(inst, new) == "abort":\n            return')
          < i_run)

    # 8d _loaded=None 回落分支：格式一致 → 不产生假改动/假提示
    #    （回落 old 的 engine 是 (backend,index) 元组，快照 engine 也是元组）
    snap = page._snapshot()
    check("V8-7 快照 engine 是元组", isinstance(snap["engine"], tuple),
          repr(snap["engine"]))
    inst2 = {k: snap[k] for k in CP.ComfyuiPage._TRACK if k in snap}
    inst2["id"] = "qa2"
    inst2["engine_backend"], inst2["engine_index"] = snap["engine"]
    inst2["comfyui_path"] = "C:/fake/ComfyUI"
    mainwin.inst = inst2
    cfg["instances"] = [inst2]
    page._loaded = None                       # 强制走回落分支
    from app.ui import changes_dialog
    real_confirm = changes_dialog.confirm_changes
    confirms = []
    changes_dialog.confirm_changes = lambda *a, **k: (confirms.append(a), True)[1]
    real_info = QMessageBox.information
    msgs = []
    QMessageBox.information = staticmethod(
        lambda *a, **k: (msgs.append(str(a[2]) if len(a) > 2 else ""),
                         QMessageBox.Ok)[1])
    try:
        page._save()
        pump(0.1)
    finally:
        changes_dialog.confirm_changes = real_confirm
        QMessageBox.information = real_info
    check("V8-8 _loaded=None 回落：无改动 → 不弹确认、无假提示",
          msgs == ["没有改动，无需保存"] and not confirms,
          f"msgs={msgs} confirms={len(confirms)}")
    page._loaded = page._snapshot()


def main():
    page, inst, cfg, mainwin = v1_gate()
    v2_keyset()
    v3_parity()
    v4_i18n(page)
    v8_scraps(page, inst, cfg, mainwin)

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nqa_lan_restart：{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    if bad:
        print("FAILED:")
        for n in bad:
            print("  - " + n)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
