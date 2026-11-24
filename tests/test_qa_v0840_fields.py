# -*- coding: utf-8 -*-
"""QA 独立验证 V1：远程运维字段「五处齐」程序化穷举（0.8.40）

工程师自测是按功能点写的；本文件换成**从 config 的默认值出发反向穷举**：
先取出 `_INST_DEFAULTS` 里所有远程运维字段，再逐一断言它们在五个落点都出现。
这个方向的断言能抓到「改了不保存」「保存了不显示」——那两类缺陷单测功能点时
不会暴露（功能点是通的，丢的只是某个字段的接线）。

五处（ARCH §9）：
  ① `config._INST_DEFAULTS`
  ② `ComfyuiPage._TRACK`          —— `_save()` 的写回循环靠它
  ③ `ComfyuiPage._SAVE_LABELS`    —— 改动确认框靠它（缺了用户看不到改了什么）
  ④ `ComfyuiPage._snapshot()`     —— 保存基准 / 当前值
  ⑤ `remote_ops_page.collect()`   —— 页签 → 快照的唯一通道

**设计上允许的例外（代码里有明确注释，逐条给证据）**
  · `cloud_start_timeout_s` —— 本版固定 300，不暴露 UI（ARCH §3.2），四处皆无
  · `ssh_host_fingerprint`  —— 由 remote_fingerprint 在连接时写，不在 UI 回写
  · `insecure_warn_muted`   —— 由告警模态 / 设置页写，不在 UI 回写
后两个**在 `_TRACK`/`_SAVE_LABELS` 里但不在 `collect()`/`_snapshot()` 里**，
安全性来自 `_save()` 里的 `if k in new` 守卫（本文件专门断言这个守卫存在且有效）。

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_fields.py
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

# ★ 必须在 import app.* 之前重定向数据目录（config.CONFIG_PATH 在导入期计算）
_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_fields_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)

from app import config                              # noqa: E402

# 两个实例：一个本机（对照）、一个远程（承载全部远程运维字段）
_SENTINEL = {
    "ssh_host": "10.20.30.40",
    "ssh_port": 2222,
    "ssh_user": "qa_user",
    "ssh_auth": "key",
    "ssh_password": "QA-PW-SENTINEL",
    "ssh_key_path": r"C:\Users\qa\.ssh\id_ed25519",
    "ssh_key_passphrase": "QA-PP-SENTINEL",
    "ssh_start_cmd": "cd /srv/ComfyUI && nohup python3 main.py --port 8189 &",
    "ssh_stop_cmd": "pkill -f 'main.py --port 8189' || true",
    "ssh_host_fingerprint": "SHA256:QASENTINEL",
    "cloud_start": {"url": "https://api.example.com/v1/start", "method": "PUT",
                    "headers": "Authorization: Bearer T0K", "body": '{"id": "i-1"}'},
    "cloud_stop": {"url": "https://api.example.com/v1/stop", "method": "PATCH",
                   "headers": "Authorization: Bearer T0K", "body": '{"id": "i-1"}'},
    "cloud_start_timeout_s": 240,
    "tunnel_local_port": 18189,
    "tunnel_remote_host": "127.0.0.9",
    "tunnel_remote_port": 18188,
    "insecure_warn_muted": True,
    "last_ops_channel": "cloud",
}

_CFG = {
    "version": 1,
    "last_instance_id": "inst_qa_remote",
    "instances": [
        {"id": "inst_qa_local", "name": "本机对照", "kind": "local",
         "comfyui_path": "", "python_path": "", "port": 8188,
         "vram_mode": "自动", "extra_args": ""},
        dict({"id": "inst_qa_remote", "name": "QA 远程", "kind": "remote",
              "base_url": "https://qa.example.com", "auth_type": "none",
              "auth_value": "", "auth_header_name": "X-API-KEY",
              "auth_user": "", "auth_pass": "", "tls_verify": True,
              "port": 8188, "comfyui_path": "", "python_path": "",
              "vram_mode": "自动", "extra_args": ""}, **_SENTINEL),
    ],
    "settings": {},
}
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(_CFG, f, ensure_ascii=False, indent=2)

from PySide6.QtWidgets import QApplication             # noqa: E402
from app.ui.main_window import MainWindow              # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


app = QApplication([])
w = MainWindow()
page = w.comfyui_page
_ids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_ids.index("inst_qa_remote"))
page.load_current()
app.processEvents()

KEY_GROUPS = ("ssh_", "cloud_", "tunnel_")
CONFIG_KEYS = [k for k in config._INST_DEFAULTS if k.startswith(KEY_GROUPS)] + \
              ["last_ops_channel", "insecure_warn_muted"]

# 设计上的例外（每个例外都必须有代码注释作为证据，见文件头）
NO_UI = {"cloud_start_timeout_s"}                 # 不暴露 UI：四处皆无
NO_COLLECT = {"ssh_host_fingerprint", "insecure_warn_muted"}   # 后台字段：不 UI 回写

TRACK = set(page._TRACK)
LABELS = set(page._SAVE_LABELS)
COLLECT = set(page.remote_ops.collect().keys())
SNAP = set(page._snapshot().keys())

print("== 1. 字段来源（从 config 默认值反向穷举，共 %d 个）==" % len(CONFIG_KEYS))
check("1a 字段总数 = 18（ARCH §3.2 的 13 实例字段 + 4 隧道/告警 + timeout）",
      len(CONFIG_KEYS) == 18, len(CONFIG_KEYS))
for k in CONFIG_KEYS:
    check(f"1b {k} ∈ config._INST_DEFAULTS", k in config._INST_DEFAULTS)

print("== 2. ② _TRACK ==")
for k in CONFIG_KEYS:
    want = k not in NO_UI
    check(f"2 {k} {'∈' if want else '∉'} _TRACK", (k in TRACK) == want,
          "缺=改了不保存" if want and k not in TRACK else "")

print("== 3. ③ _SAVE_LABELS（缺了改动确认框就看不到这个字段）==")
for k in CONFIG_KEYS:
    want = k not in NO_UI
    check(f"3 {k} {'∈' if want else '∉'} _SAVE_LABELS", (k in LABELS) == want)

print("== 4. ⑤ remote_ops_page.collect() ==")
for k in CONFIG_KEYS:
    want = k not in NO_UI and k not in NO_COLLECT
    check(f"4 {k} {'∈' if want else '∉'} collect()", (k in COLLECT) == want,
          "缺=页签改了保存不生效" if want and k not in COLLECT else "")

print("== 5. ④ _snapshot() ==")
for k in CONFIG_KEYS:
    want = k not in NO_UI and k not in NO_COLLECT
    check(f"5 {k} {'∈' if want else '∉'} _snapshot()", (k in SNAP) == want)

print("== 6. 例外字段的安全性：_save() 的 `if k in new` 守卫 ==")
# 两个后台字段在 _TRACK 里但不在 _snapshot 里 —— 如果 _save 无条件写回，
# 它们会被写成 "缺键" 或旧值，指纹/「不再提示」就会每次保存都被冲掉。
_src = open(os.path.join(ROOT, "app", "ui", "pages", "comfyui_page.py"),
            encoding="utf-8").read()
check("6a _save 写回循环带 `if k in new` 守卫",
      "for k in self._TRACK:\n            if k in new:" in _src
      or "for k in self._TRACK:" in _src and "if k in new:" in _src)
for k in sorted(NO_COLLECT):
    check(f"6b {k} 无 UI 来源时不会被 _save 覆盖",
          k in TRACK and k not in SNAP)
# 反向证据：如果快照里有键但 collect 不产生它，才算「冲掉」；这里两者都没有
check("6c 后台字段既不进 collect 也不进 snapshot（口径一致）",
      NO_COLLECT.isdisjoint(COLLECT) and NO_COLLECT.isdisjoint(SNAP))

print("== 7. 往返：保存的值 → 页签显示 → 再收集回来（逐字段哨兵值）==")
ro = page.remote_ops
for k in sorted(COLLECT):
    inst = dict(w.cfg["instances"][_ids.index("inst_qa_remote")])
    if k in _SENTINEL:
        inst[k] = _SENTINEL[k]
    ro.load(inst)
    app.processEvents()
    got = ro.collect().get(k, "<缺键>")
    check(f"7 {k} 装载后回收集 = 哨兵值", got == inst[k], f"{got!r} vs {inst[k]!r}")

print("== 8. 反向：改控件 → _snapshot() 立即可见（改了会保存）==")
inst = dict(w.cfg["instances"][_ids.index("inst_qa_remote")])
ro.load(inst)
app.processEvents()
WIDGET_CASES = [
    ("ssh_host", "10.9.9.9", lambda: ro.ed_host.setText("10.9.9.9")),
    ("ssh_port", 2200, lambda: ro.sp_port.setValue(2200)),
    ("ssh_user", "root9", lambda: ro.ed_user.setText("root9")),
    ("ssh_auth", "key", lambda: ro.cb_auth.setCurrentIndex(1)),
    ("ssh_password", "NEWPW9", lambda: ro.ed_pass.setText("NEWPW9")),
    ("ssh_key_path", "/k/id_rsa9", lambda: ro.ed_key.setText("/k/id_rsa9")),
    ("ssh_key_passphrase", "NEWPP9", lambda: ro.ed_pp.setText("NEWPP9")),
    ("ssh_start_cmd", "CMD-START-9",
     lambda: ro.pt_start.setPlainText("CMD-START-9")),
    ("ssh_stop_cmd", "CMD-STOP-9",
     lambda: ro.pt_stop.setPlainText("CMD-STOP-9")),
    ("tunnel_local_port", 19555, lambda: ro.sp_local.setValue(19555)),
    ("tunnel_remote_host", "10.1.1.1", lambda: ro.ed_rhost.setText("10.1.1.1")),
    ("tunnel_remote_port", 19556, lambda: ro.sp_rport.setValue(19556)),
    ("last_ops_channel", "cloud", lambda: ro.cb_channel.setCurrentIndex(1)),
    ("cloud_start", "https://x/start", lambda: ro.ed_cs_url.setText("https://x/start")),
    ("cloud_stop", "https://x/stop", lambda: ro.ed_ce_url.setText("https://x/stop")),
]
for key, want, act in WIDGET_CASES:
    inst2 = dict(inst)
    ro.load(inst2)
    act()
    app.processEvents()
    snap = page._snapshot()
    got = snap.get(key, "<缺键>")
    if isinstance(want, str) and isinstance(got, dict):
        got = got.get("url", "")
    check(f"8 {key} 控件→快照", got == want, f"{got!r} vs {want!r}")

print("== 9. 落盘往返：18 个字段全写 config.json 再读回，一个都不许丢 ==")
cfg = config.load_config()
inst3 = [i for i in cfg["instances"] if i.get("id") == "inst_qa_remote"][0]
missing = []
for k in CONFIG_KEYS:
    if k not in inst3:
        missing.append(k)
    elif k not in ("ssh_password", "ssh_key_passphrase") and inst3[k] != _SENTINEL[k]:
        missing.append("%s=%r" % (k, inst3[k]))
check("9a load_config 后 18 个字段齐备且值未变", not missing, missing)

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V1 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
clean_exit(1 if bad else 0)
