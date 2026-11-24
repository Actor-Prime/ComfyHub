# -*- coding: utf-8 -*-
"""QA 独立验证 V2：凭据泄漏面（0.8.40 本版最该查的一面）

工程师的自测覆盖了「密码被存成 enc:v1:」这种正例；本文件换角度，专攻**泄漏口**：

  L1 保存对比框（diff_changes / confirm_changes）会不会把 ssh_password /
     ssh_key_passphrase 的**原文**打在屏幕上（旁边有人看着就被看见）
  L2 `app.log` / 控制台输出 / 异常消息（`SshError.__str__`）里会不会出现密码
  L3 DPAPI：`config.json` 落盘是不是 `enc:v1:`；`load_config` 后内存是明文还是密文
  L4 「留空 = 保持原值」在三条路径都成立（页签 collect / _save 二次兜底 / 首次新增）
  L5 MCP 配置片段（`settings_page._mcp_snippets`）会不会导出隧道端口或凭据
  L6 `cloud_start` / `cloud_stop` 请求头里的 Token **不加密**（不在 `_SECRET_FIELDS`）
     —— 这一条只给判断与证据，不直接判缺陷

关键手法：L1 走**真实 `_save()`**（把 `diff_changes` / `confirm_changes` 换成捕获桩），
因为泄漏如果只在「拼文案」那一步发生，直接测 `diff_changes` 是测不出来的。

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_creds.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# D-09：退出前先销毁 QApplication，否则进程结束阶段偶发 0xC0000005
from _qa_exit import clean_exit        # noqa: E402

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_creds_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)

# ---- 三个哨兵口令：任何一处出现原文就算泄漏 ----
PW_OLD = "OLD-PW-1a2b3c-DO-NOT-LEAK"
PW_NEW = "NEW-PW-9z8y7x-DO-NOT-LEAK"
PP_OLD = "OLD-PP-4d5e6f-DO-NOT-LEAK"

_CFG = {
    "version": 1,
    "last_instance_id": "inst_sec",
    "instances": [
        {"id": "inst_sec", "name": "安全测试实例", "kind": "remote",
         "base_url": "http://10.0.0.5:8188", "auth_type": "bearer",
         "auth_value": "DEMO-TOKEN-OLD-222", "auth_header_name": "X-API-KEY",
         "auth_user": "", "auth_pass": "", "tls_verify": True,
         "port": 8188, "comfyui_path": "", "python_path": "",
         "vram_mode": "自动", "extra_args": "",
         "ssh_host": "10.0.0.5", "ssh_port": 22, "ssh_user": "root",
         "ssh_auth": "password", "ssh_password": PW_OLD,
         "ssh_key_path": "", "ssh_key_passphrase": PP_OLD,
         "ssh_start_cmd": "cd ~/ComfyUI && nohup python main.py &",
         "ssh_stop_cmd": 'pkill -f "main.py --listen" || true',
         "ssh_host_fingerprint": "", "cloud_start": {}, "cloud_stop": {},
         "tunnel_local_port": 18189, "tunnel_remote_host": "127.0.0.1",
         "tunnel_remote_port": 8188, "insecure_warn_muted": True,
         "last_ops_channel": "ssh"},
    ],
    "settings": {},
}
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(_CFG, f, ensure_ascii=False, indent=2)

from app import config, secret_store                  # noqa: E402
from app.remote_ssh import (                          # noqa: E402
    SshSession, SshError, classify_auth_error,
)
from app import api_base as apib                      # noqa: E402
from app import remote_registry as rr                 # noqa: E402
from fake_ssh import FakeFactory, make_inst           # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


def _leaks(text, *secrets):
    hits = [s for s in secrets if s and s in str(text)]
    return hits


# =====================================================================
print("== L1 保存对比框不显示口令原文（走真实 _save，捕获 diff_changes 入参）==")
from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402
from app.ui.main_window import MainWindow                  # noqa: E402
from app.ui import changes_dialog                          # noqa: E402

app = QApplication([])
w = MainWindow()
page = w.comfyui_page
_ids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_ids.index("inst_sec"))
page.load_current()
app.processEvents()

# ---- 桩：拦下模态，只记录「真正会被画到屏幕上」的 changes ----
_orig_diff = changes_dialog.diff_changes
_orig_confirm = changes_dialog.confirm_changes
_orig_info, _orig_warn = QMessageBox.information, QMessageBox.warning
CAP = {}


def _diff_spy(old, new, labels, **kw):
    out = _orig_diff(old, new, labels, **kw)
    CAP["old"], CAP["new"], CAP["kw"] = dict(old), dict(new), dict(kw)
    CAP["changes"] = out
    return out


changes_dialog.diff_changes = _diff_spy
changes_dialog.confirm_changes = lambda *a, **k: False     # 取消保存
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

# ---- L1a：一个字段都不改（旧口令原样在内存里），确认框应当完全不含口令 ----
page.load_current()
app.processEvents()
page._save()
rendered = json.dumps(CAP.get("changes") or [], ensure_ascii=False)
hits = _leaks(rendered, PW_OLD, PP_OLD)
check("L1a 不改任何字段时对比框不含口令原文", not hits, hits)
check("L1a' 不改任何字段时 ssh_password 根本不出现在改动列表",
      not any("SSH 密码" in c[0] for c in (CAP.get("changes") or [])),
      CAP.get("changes"))

# ---- L1b：改密码 → 对比框应显示「已更新」一类的占位，不是原文 ----
page.load_current()
app.processEvents()
page.remote_ops.ed_pass.setText(PW_NEW)
page.remote_ops.ed_pp.setText("NEW-PP-7g8h9i")
app.processEvents()
page._save()
rendered = json.dumps(CAP.get("changes") or [], ensure_ascii=False)
hits = _leaks(rendered, PW_OLD, PW_NEW, PP_OLD, "NEW-PP-7g8h9i")
check("L1b 改密码后对比框不含新旧口令原文", not hits, hits)
pw_rows = [c for c in (CAP.get("changes") or []) if "SSH 密码" in c[0]]
check("L1b' 改密码后仍有一条「SSH 密码」改动项（用户能看到自己动了什么）",
      bool(pw_rows), pw_rows)
check("L1b'' 新值列是脱敏占位", bool(pw_rows) and "不回显" in str(pw_rows[0][2]),
      pw_rows[0][2] if pw_rows else "")
# ★ 关键：_disp 只脱敏了「新值」，旧值列仍是内存里的明文
check("L1b''' 旧值列也不许出现口令原文（_disp 只脱敏 new，old 仍是明文）",
      not _leaks(str(pw_rows[0][1]), PW_OLD) if pw_rows else False,
      pw_rows[0][1] if pw_rows else "")
# 还原 confirm_changes 真正会画到列表里的那一行（见 changes_dialog 第 ~78 行）
if pw_rows:
    _rendered_line = "%s：%s  →  %s" % (
        pw_rows[0][0],
        _orig_diff.__globals__["_fmt"](pw_rows[0][1], 40),
        _orig_diff.__globals__["_fmt"](pw_rows[0][2], 40))
    check("L1b'''' 用户在确认框里实际看到的那一行",
          not _leaks(_rendered_line, PW_OLD, PP_OLD), _rendered_line)
check("L1b''''' 根因：collect() 把已存口令原文带进了 _snapshot（→ _loaded → old）",
      page._loaded is not None
      and page._loaded.get("ssh_password") == PW_OLD,
      str(page._loaded.get("ssh_password"))[:8] if page._loaded else "None")

# 0.8.39 的 auth_value / auth_pass 走同一段代码：旧值列本来是「（空）」，
# 所以不泄漏，但会**每次保存都刷一条假的「凭据已更新」**（下面 L1d）
page.load_current()
app.processEvents()
page.ed_token.setText("AUTH-TOKEN-NEW-111")
page._save()
rendered2 = json.dumps(CAP.get("changes") or [], ensure_ascii=False)
tok_rows = [c for c in (CAP.get("changes") or []) if "凭证" in c[0]]
check("L1d auth_value 的旧值列只是「（空）」——不泄漏（与 ssh_password 不同）",
      bool(tok_rows) and not _leaks(str(tok_rows[0][1]),
                                    "DEMO-TOKEN-OLD-222"),
      tok_rows[0] if tok_rows else "（无凭证行）")
check("L1d' 但非空 token 同样会进 new，脱敏生效",
      bool(tok_rows) and "不回显" in str(tok_rows[0][2]),
      tok_rows[0] if tok_rows else "")

# 顺带记录一个 0.8.39 就存在的「假改动」：不动任何字段也刷一条凭据变更
page.load_current()
app.processEvents()
page._save()
_rows = CAP.get("changes") or []
check("L1e 【0.8.39 既有】不改任何字段也刷出一条「凭证」假改动",
      any("凭证" in c[0] for c in _rows), _rows)

# ---- L1c：diff_changes 自身带 secret_keys 能力，但 _save 没传（加固建议，不算缺陷）----
check("L1c diff_changes 支持 secret_keys 参数（能力在）",
      "secret_keys" in _orig_diff.__code__.co_varnames)
check("L1c' _save 调用 diff_changes 时**未**传 secret_keys（靠 _disp 预脱敏兜住）",
      "secret_keys" not in CAP.get("kw", {}),
      "现状安全，但少一层防御；见报告建议 R2")

changes_dialog.diff_changes = _orig_diff
changes_dialog.confirm_changes = _orig_confirm
QMessageBox.information, QMessageBox.warning = _orig_info, _orig_warn

# =====================================================================
print("== L2 异常消息 / 日志里不出现密码 ==")
inst = make_inst(ssh_password=PW_OLD, ssh_key_passphrase=PP_OLD)
LOGS = []
fac = FakeFactory(auth_error=__import__("paramiko").AuthenticationException(
    "Authentication failed."))
sess = SshSession(inst, transport_factory=fac, log=lambda s: LOGS.append(str(s)))
err = None
try:
    sess.connect(timeout=1)
except SshError as e:
    err = e
check("L2a 密码认证失败抛 SshError(kind=auth_password)",
      err is not None and err.kind == "auth_password",
      err.kind if err else "未抛")
check("L2b SshError.__str__ 不含密码原文",
      err is not None and not _leaks(str(err), PW_OLD, PP_OLD), str(err))
check("L2b' SshError.detail 不含密码原文",
      err is not None and not _leaks(err.detail, PW_OLD, PP_OLD), err.detail)
check("L2c 连接日志行不含密码原文", not _leaks("\n".join(LOGS), PW_OLD, PP_OLD),
      LOGS)

# 密钥认证：文件不存在 → key_missing（**不是** auth_passphrase）。
# 说明：口令错这件事只有真的读到一把加密私钥才可能发生，本机无 sshd 无法真读；
# 分类逻辑本身用 classify_key_error 直接单测（下面 L2d''）。
LOGS2 = []
fac2 = FakeFactory()
inst2 = make_inst(ssh_auth="key", ssh_key_path="/tmp/definitely_missing_id_rsa",
                  ssh_key_passphrase=PP_OLD)
sess2 = SshSession(inst2, transport_factory=fac2, log=lambda s: LOGS2.append(str(s)))
err2 = None
try:
    sess2.connect(timeout=1)
except SshError as e:
    err2 = e
check("L2d 密钥文件不存在 → kind=key_missing（E2b，不误报成认证失败）",
      err2 is not None and err2.kind == "key_missing",
      err2.kind if err2 else "未抛")
check("L2e 该分支消息不含口令原文",
      err2 is not None and not _leaks(str(err2) + err2.detail, PP_OLD),
      str(err2) if err2 else "")
from app.remote_ssh import classify_key_error            # noqa: E402
_k, _d = classify_key_error(
    __import__("paramiko").PasswordRequiredException("Private key file is encrypted"),
    "/home/u/.ssh/id_rsa", False)
check("L2d'' 加密私钥未给口令 → 分类到 auth_passphrase 且消息只含路径",
      _k == "auth_passphrase" and not _leaks(_d, PP_OLD), f"{_k} / {_d}")
_k2, _d2 = classify_key_error(
    __import__("paramiko").PasswordRequiredException("Private key file is encrypted"),
    "/home/u/.ssh/id_rsa", True)
check("L2d''' 给了口令仍解不开 → 分类到 auth_passphrase（口令不对）",
      _k2 == "auth_passphrase" and not _leaks(_d2, PP_OLD), f"{_k2} / {_d2}")
check("L2f classify_auth_error 的 detail 只含异常类名与消息",
      not _leaks(classify_auth_error(
          __import__("paramiko").AuthenticationException("Authentication failed."),
          "password")[1], PW_OLD, PP_OLD))

# =====================================================================
print("== L3 DPAPI：落盘密文 / 内存明文 ==")
cfg = config.load_config()
cfg["instances"][0]["ssh_password"] = PW_OLD
cfg["instances"][0]["ssh_key_passphrase"] = PP_OLD
config.save_config(cfg)
raw = open(config.CONFIG_PATH, encoding="utf-8").read()
disk = json.loads(raw)
d0 = disk["instances"][0]
check("L3a config.json 里 ssh_password 是 enc:v1: 前缀",
      str(d0.get("ssh_password", "")).startswith(secret_store.PREFIX),
      str(d0.get("ssh_password"))[:24])
check("L3b config.json 里 ssh_key_passphrase 是 enc:v1: 前缀",
      str(d0.get("ssh_key_passphrase", "")).startswith(secret_store.PREFIX),
      str(d0.get("ssh_key_passphrase"))[:24])
check("L3c 整个 config.json 文本里搜不到口令原文",
      not _leaks(raw, PW_OLD, PP_OLD))
check("L3d 内存 cfg 未被 save_config 改成密文（后续请求才能用）",
      cfg["instances"][0]["ssh_password"] == PW_OLD)

cfg2 = config.load_config()
check("L3e load_config 后内存里是明文（不是密文）",
      cfg2["instances"][0]["ssh_password"] == PW_OLD,
      cfg2["instances"][0]["ssh_password"])
check("L3e' load_config 后 ssh_key_passphrase 也是明文",
      cfg2["instances"][0]["ssh_key_passphrase"] == PP_OLD)

# =====================================================================
print("== L4 「留空 = 保持原值」三条路径 ==")
ro = page.remote_ops
page.load_current()
app.processEvents()
# 路径①：页签 collect()（输入框空 → 回落到 _orig）
ro.ed_pass.clear()
ro.ed_pp.clear()
c = ro.collect()
check("L4-1 页签 collect()：留空回落到已存口令",
      c["ssh_password"] == PW_OLD and c["ssh_key_passphrase"] == PP_OLD,
      (c["ssh_password"][:6], c["ssh_key_passphrase"][:6]))
# 路径①'：输入框有值 → 用新值
ro.ed_pass.setText(PW_NEW)
check("L4-1' 页签 collect()：填了就用新值", ro.collect()["ssh_password"] == PW_NEW)

# 路径②：_save 的二次兜底 —— 直接把快照里的口令清空，模拟「别的入口动了表单」
page.load_current()
app.processEvents()
real_snap = page._snapshot


def _snap_without_secret():
    d = real_snap()
    d["ssh_password"] = ""
    d["ssh_key_passphrase"] = ""
    return d


CAP2 = {}


def _diff_spy2(old, new, labels, **kw):
    out = _orig_diff(old, new, labels, **kw)
    CAP2["new"] = dict(new)
    CAP2["changes"] = out
    return out


changes_dialog.diff_changes = _diff_spy2
changes_dialog.confirm_changes = lambda *a, **k: False
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
page._snapshot = _snap_without_secret
page._save()
check("L4-2 _save 二次兜底：快照里口令为空 → 回填成已存值",
      CAP2.get("new", {}).get("ssh_password") == PW_OLD
      and CAP2.get("new", {}).get("ssh_key_passphrase") == PP_OLD,
      (CAP2.get("new", {}).get("ssh_password"),
       CAP2.get("new", {}).get("ssh_key_passphrase")))
page._snapshot = real_snap
changes_dialog.diff_changes = _orig_diff
changes_dialog.confirm_changes = _orig_confirm
QMessageBox.information, QMessageBox.warning = _orig_info, _orig_warn

# 路径③：首次新增实例（内存里根本没有这两个键）
fresh = {"id": "inst_new", "name": "新实例", "kind": "remote",
         "base_url": "http://1.2.3.4:8188"}
ro.load(fresh)
app.processEvents()
c3 = ro.collect()
check("L4-3 首次新增：无已存口令时 collect() 是空串而不是 None/异常",
      c3["ssh_password"] == "" and c3["ssh_key_passphrase"] == "",
      (c3["ssh_password"], c3["ssh_key_passphrase"]))
check("L4-3' 首次新增：占位提示为「加密保存在本机（DPAPI）」",
      "DPAPI" in ro.ed_pass.placeholderText(), ro.ed_pass.placeholderText())
ro.load(dict(_CFG["instances"][0]))
check("L4-3'' 已有口令时占位提示为「已保存（留空则不修改）」",
      "已保存" in ro.ed_pass.placeholderText(), ro.ed_pass.placeholderText())

# =====================================================================
print("== L5 MCP 配置片段：不导出隧道端口 / 不导出凭据 ==")
from app.ui.pages import settings_page                       # noqa: E402

# 盘上的实例就是 MCP 片段的数据源（_mcp_snippets 内部 load_config()），
# 所以先把真地址写进配置，再开隧道覆盖 —— 这样两条路径的差异才可观测
cfg4 = config.load_config()
cfg4["last_instance_id"] = "inst_sec"
cfg4["instances"][0]["base_url"] = "https://real.example.com"
config.save_config(cfg4)
_tun_inst = dict(cfg4["instances"][0])
rr.registry.set_override("inst_sec", "http://127.0.0.1:18189", 18189)
check("L5-pre 隧道激活时 api_base 是回环",
      apib.api_base(_tun_inst) == "http://127.0.0.1:18189",
      apib.api_base(_tun_inst))
check("L5-pre' 同时 saved_api_base 仍是真地址",
      apib.saved_api_base(_tun_inst) == "https://real.example.com",
      apib.saved_api_base(_tun_inst))
a_txt, b_txt, port = settings_page._mcp_snippets()
both = a_txt + "\n" + b_txt
check("L5a MCP 片段用的是 saved_api_base（https://real.example.com）",
      "https://real.example.com" in both,
      [ln for ln in both.splitlines() if "COMFYUI_URL" in ln])
check("L5b MCP 片段不含隧道本地端口 18189", "18189" not in both)
check("L5c MCP 片段不含任何口令 / Token 原文",
      not _leaks(both, PW_OLD, PW_NEW, PP_OLD, "DEMO-TOKEN-OLD-222", "T0K"))
rr.registry.clear_override("inst_sec")
check("L5d 清掉覆盖后 api_base 立即回落",
      apib.api_base(_tun_inst) == "https://real.example.com",
      apib.api_base(_tun_inst))

# =====================================================================
print("== L6 云平台请求头里的 Token 是否加密（只给判断，不判缺陷）==")
cfg3 = config.load_config()
cfg3["instances"][0]["cloud_start"] = {
    "url": "https://api.example.com/start", "method": "POST",
    "headers": "Authorization: Bearer CLOUD-TOKEN-abcdef123456",
    "body": '{"id": "i-1"}'}
config.save_config(cfg3)
raw3 = open(config.CONFIG_PATH, encoding="utf-8").read()
is_plain = "CLOUD-TOKEN-abcdef123456" in raw3
check("L6a 事实：云钩子请求头里的 Token 明文落在 config.json（未加密）",
      is_plain, "明文" if is_plain else "未命中")
check("L6b 事实：cloud_start / cloud_stop 不在 _SECRET_FIELDS 里",
      "cloud_start" not in config._SECRET_FIELDS
      and "cloud_stop" not in config._SECRET_FIELDS,
      config._SECRET_FIELDS)
# 只看云平台分区那一段源码（_build_cloud 到 _build_tunnel 之间）有没有加密提示
_page_src = open(os.path.join(ROOT, "app", "ui", "pages", "remote_ops_page.py"),
                 encoding="utf-8").read()
_cloud_part = _page_src.split("def _build_cloud")[-1].split("def _build_tunnel")[0]
_has_hint = any(w in _cloud_part for w in ("加密", "DPAPI", "密文", "明文"))
check("L6c 云平台分区已提示「Token 会明文落盘」（D-05 已修，原先一字不提）",
      _has_hint, "无提示" if not _has_hint else "有提示")

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V2 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
print()
print("L6 判断（不判缺陷）：")
print("  云平台 Token 走 `cloud_start.headers` 纯文本，不进 _SECRET_FIELDS，")
print("  因此和 ssh_password 不同，它是**明文落盘**的。评估：")
print("  · 与 0.8.39 的 auth_value（Bearer Token）不一致 —— 后者是加密的，")
print("    用户会形成「Token 都加密」的预期，这里破了预期；")
print("  · 但它不经过 SSH/SFTP，也不会出现在任何日志里（_hook_detail 只打 method+url）；")
print("  · 缓解成本极低：把 headers 文本里的敏感值整段加密（或整块加密）。")
print("  → 建议记为 P2（下个补丁版修），不作为本版阻断项。")
clean_exit(1 if bad else 0)
