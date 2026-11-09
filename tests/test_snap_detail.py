# -*- coding: utf-8 -*-
"""快照/备份「事件标注 + 点击详情」回归测试（2026-11-02 功能）

验证点：
  1. collect_manifest：内核版本 / 插件版本 / 工作流清单 / 模型 sha256 正确
  2. EnvBackup.create 写 meta.json（含 event 字段）
  3. Rescue.snapshot 新 dict 格式（event/plugins/workflows/models）
  4. Rescue.rollback 新旧格式兼容（旧 list 快照仍可回滚）
  5. sha256 缓存命中不重算
  6. UI：3 列表格（含事件列）+ 详情面板 HTML 渲染

约定：APPDATA 重定向到临时目录，不污染真机数据；pip/git 全 monkeypatch。
"""
import hashlib
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---- APPDATA 防污染：必须先于任何 EnvBackup/Rescue 构造 ----
BASE = r"F:\_snap_detail_test"
APPDATA_DIR = os.path.join(BASE, "appdata")
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(APPDATA_DIR, exist_ok=True)
os.environ["APPDATA"] = APPDATA_DIR

from app.tools import (EnvBackup, Rescue, collect_manifest,   # noqa: E402
                       _file_sha256, _hash_cache_file)
from app import tools as tools_mod                            # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


logs = []


def log(m):
    logs.append(str(m))
    print("   |", m)


# ---------------- 假实例 ----------------
CP = os.path.join(BASE, "ComfyUI")
NODES = os.path.join(CP, "custom_nodes")
WF = os.path.join(CP, "user", "default", "workflows")
MODELS = os.path.join(CP, "models", "checkpoints")

os.makedirs(os.path.join(NODES, "plugA"), exist_ok=True)
os.makedirs(os.path.join(NODES, "plugB.disabled"), exist_ok=True)
os.makedirs(os.path.join(NODES, "__pycache__"), exist_ok=True)
os.makedirs(os.path.join(WF, "sub"), exist_ok=True)
os.makedirs(MODELS, exist_ok=True)
# 内核版本
open(os.path.join(CP, "comfyui_version.py"), "w",
     encoding="utf-8").write('__version__ = "0.41.0"\n')
# 工作流
open(os.path.join(WF, "wf1.json"), "w").write("{}")
open(os.path.join(WF, "sub", "wf2.json"), "w").write("{}")
# 模型（两个小文件，验证 sha256）
m1 = os.path.join(MODELS, "a.safetensors")
m2 = os.path.join(MODELS, "b.ckpt")
open(m1, "wb").write(b"model-a" * 100)
open(m2, "wb").write(b"model-b" * 200)
sha1 = hashlib.sha256(open(m1, "rb").read()).hexdigest()
sha2 = hashlib.sha256(open(m2, "rb").read()).hexdigest()
# 非模型文件不计入
open(os.path.join(MODELS, "note.txt"), "w").write("x")

# git describe monkeypatch（假插件目录无 .git，恒走"—"分支；此处只为确定性）
real_run = tools_mod.run


def fake_run(cmd, **kw):
    class R:
        returncode, stdout, stderr = 0, "v1.2.3-4-gabc1234", ""
    return R()


tools_mod.run = fake_run

print("=== 1. collect_manifest 采集 ===")
man = collect_manifest(CP, log=log)
check("内核版本正确", man["kernel"] == "0.41.0", str(man["kernel"]))
plug_names = [p["name"] for p in man["plugins"]]
check("插件名称规范化（剥 .disabled、跳过 __）",
      plug_names == ["plugA", "plugB"], str(plug_names))
check("插件启停正确",
      [p["enabled"] for p in man["plugins"]] == [True, False],
      str([(p["name"], p["enabled"]) for p in man["plugins"]]))
check("非 git 插件版本为 —", man["plugins"][0]["version"] == "—")
wf = man["workflows"]
check("工作流递归采集", sorted(wf) == ["sub/wf2.json", "wf1.json"], str(wf))
mdls = {(m["subdir"], m["name"]): m for m in man["models"]}
check("模型只收白名单扩展名", len(man["models"]) == 2, str(list(mdls)))
check("模型 sha256 正确（a）",
      mdls[("checkpoints", "a.safetensors")]["sha256"] == sha1)
check("模型 sha256 正确（b）",
      mdls[("checkpoints", "b.ckpt")]["sha256"] == sha2)
check("模型 size 正确",
      mdls[("checkpoints", "b.ckpt")]["size"] == os.path.getsize(m2))

print("\n=== 2. sha256 缓存命中 ===")
check("缓存文件已写", os.path.isfile(_hash_cache_file()))
calls = {"n": 0}
orig_sha = tools_mod._file_sha256


def counting_sha(p, chunk=1024 * 1024):
    calls["n"] += 1
    return orig_sha(p, chunk)


tools_mod._file_sha256 = counting_sha
collect_manifest(CP, log=log)
check("缓存命中后 0 次重算", calls["n"] == 0, f"hash calls={calls['n']}")
# 改动文件 mtime → 重算
os.utime(m1, (time.time() + 10, time.time() + 10))
collect_manifest(CP, log=log)
check("mtime 变化触发重算 1 次", calls["n"] == 1, f"hash calls={calls['n']}")
tools_mod._file_sha256 = orig_sha

print("\n=== 3. EnvBackup.create 写 meta.json ===")
tools_mod.run = fake_run
bk = EnvBackup(CP, os.path.join(BASE, "fake_python.exe"))
name = bk.create("测试", log=log, event="手动创建")
meta_path = os.path.join(bk.backup_dir, name, "meta.json")
check("meta.json 已写", os.path.isfile(meta_path), meta_path)
meta = json.load(open(meta_path, encoding="utf-8")) if os.path.isfile(meta_path) else {}
check("event 字段正确", meta.get("event") == "手动创建", str(meta.get("event")))
check("created 字段存在", bool(meta.get("created")), str(meta.get("created")))
check("meta 含内核版本", meta.get("kernel") == "0.41.0")
check("meta 含插件版本键",
      all("version" in p for p in meta.get("plugins", [])))
check("meta 含模型哈希",
      any(m.get("sha256") == sha1 for m in meta.get("models", [])))
check("load_meta 可读回", (bk.load_meta(name) or {}).get("event") == "手动创建")
check("旧备份 load_meta 返回 None", bk.load_meta("not_exist") is None)
tools_mod.run = real_run

print("\n=== 4. Rescue.snapshot 新 dict 格式 ===")
r = Rescue(CP)
ts = r.snapshot(log=log, event="插件安装前")
p = os.path.join(r.snap_dir, ts + ".json")
data = json.load(open(p, encoding="utf-8"))
check("快照为 dict 格式", isinstance(data, dict), str(type(data)))
check("event=插件安装前", data.get("event") == "插件安装前", str(data.get("event")))
check("plugins 含 name/enabled",
      [(x["name"], x["enabled"]) for x in data["plugins"]]
      == [("plugA", True), ("plugB", False)])
check("快照含工作流", sorted(data.get("workflows", [])) == ["sub/wf2.json", "wf1.json"])
check("快照含模型哈希",
      any(m.get("sha256") == sha1 for m in data.get("models", [])))
check("快照含内核版本", data.get("kernel") == "0.41.0")

print("\n=== 5. rollback：新 dict 格式 ===")
os.rename(os.path.join(NODES, "plugA"), os.path.join(NODES, "plugA.disabled"))
os.makedirs(os.path.join(NODES, "plugNew"))
ok, msg = r.rollback(ts + ".json", log=log)
check("回滚返回 True", ok, msg)
check("禁用新增 plugNew", "禁用新增: plugNew" in msg, msg)
check("恢复启用 plugA", "恢复启用: plugA" in msg, msg)

print("\n=== 6. rollback：旧 list 格式兼容 ===")
old = [{"name": "plugB.disabled", "enabled": False},
       {"name": "plugNew", "enabled": True}]
json.dump(old, open(os.path.join(r.snap_dir, "old_list.json"), "w",
                    encoding="utf-8"))
# 当前状态：plugA 启用、plugB.disabled、plugNew.disabled
ok, msg = r.rollback("old_list.json", log=log)
check("旧格式回滚返回 True", ok, msg)
d = sorted(n for n in os.listdir(NODES)
           if os.path.isdir(os.path.join(NODES, n)) and not n.startswith("__"))
check("旧格式回滚生效（plugA 被禁用、plugNew 恢复）",
      d == ["plugA.disabled", "plugB.disabled", "plugNew"], str(d))
check("无双重禁用", not any(x.endswith(".disabled.disabled") for x in d), str(d))

print("\n=== 7. load_meta：旧 list 快照包装可读 ===")
m = r.load_meta("old_list.json")
check("旧格式 load_meta 返回 dict", isinstance(m, dict))
check("旧格式 event 为空", (m or {}).get("event") == "")
check("旧格式 plugins 可读",
      len((m or {}).get("plugins", [])) == 2, str((m or {}).get("plugins")))

print("\n=== 8. UI（offscreen）：3 列 + 事件列 + 详情面板 ===")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication          # noqa: E402

qapp = QApplication(sys.argv[:1])
from app.config import load_config                  # noqa: E402
from app.ui.pages.env_page import EnvPage           # noqa: E402


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        qapp.processEvents()
        time.sleep(0.02)


class StubWin:
    def __init__(self):
        self.procs = type("P", (), {"is_running": staticmethod(lambda inst: False)})()

    def current_inst(self):
        return {"id": "i1", "name": "T", "comfyui_path": CP,
                "python_path": sys.executable, "port": 8399, "vram_mode": "自动",
                "extra_args": "", "auto_open_browser": False}


page = EnvPage(load_config(), StubWin())
page.show()
pump(0.3)
check("表格 3 列", page.tb.columnCount() == 3, str(page.tb.columnCount()))
check("列头含 事件", page.tb.horizontalHeaderItem(1).text() == "事件",
      page.tb.horizontalHeaderItem(1).text())
page._refresh_snapshots()
pump(0.1)
n = page.tb.rowCount()
check("快照+备份均入表", n >= 2, f"rows={n}")
# 找到 dict 格式快照行（事件列非 —）
snap_row = -1
bak_row = -1
for i in range(n):
    if page.tb.item(i, 0).text() == "快照" and page.tb.item(i, 1).text() != "—":
        snap_row = i
    if page.tb.item(i, 0).text() == "备份":
        bak_row = i
check("新快照行事件列=插件安装前",
      snap_row >= 0 and page.tb.item(snap_row, 1).text() == "插件安装前",
      page.tb.item(snap_row, 1).text() if snap_row >= 0 else "无行")
check("备份行事件列=手动创建",
      bak_row >= 0 and page.tb.item(bak_row, 1).text() == "手动创建",
      page.tb.item(bak_row, 1).text() if bak_row >= 0 else "无行")
# 选中快照行 → 详情面板
page.tb.selectRow(snap_row)
pump(0.2)
html = page.detail.toPlainText()
check("详情含内核版本", "0.41.0" in html, html[:120])
check("详情含插件名", "plugA" in html and "plugB" in html)
check("详情含工作流", "wf1.json" in html and "sub/wf2.json" in html)
check("详情含模型哈希前缀", sha1[:16] in html, sha1[:16])
check("详情含事件", "插件安装前" in html)
# 选中旧 list 快照行 → 详情容错
old_row = next((i for i in range(n)
                if page.tb.item(i, 2).text() == "old_list.json"), -1)
if old_row >= 0:
    page.tb.selectRow(old_row)
    pump(0.2)
    html2 = page.detail.toPlainText()
    check("旧格式详情不崩且有插件", "plugB" in html2, html2[:120])
else:
    check("旧格式行存在", False, "old_list.json 不在表中")

pump(0.3)

print("\n" + "=" * 46)
bad = [n for n, ok, _ in res if not ok]
print(f"结果: {len(res) - len(bad)}/{len(res)} 通过")
for n in bad:
    print("  ❌", n)
sys.exit(1 if bad else 0)
