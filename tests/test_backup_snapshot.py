# -*- coding: utf-8 -*-
"""备份/快照全场景测试：EnvBackup 创建/还原 + Rescue 快照/回滚 + 快照tab UI 集成

重点回归（v0.8.03 修复项）：
  1. Rescue.snapshot/rollback 传错路径（custom_nodes/custom_nodes 恒扫到 0 个插件）
  2. 快照名称带 .disabled 后缀 → 回滚改名成 xxx.disabled.disabled（双重禁用）
  3. PipListWorker(dict) 构造 TypeError（QThread.__init__(dict)）
  4. _RunTask 异常值被 handler 当成功（truthy 字符串）
  5. _log 在工作线程直碰 QWidget（跨线程未定义行为）

约定：APPDATA 重定向到临时目录，绝不污染真机 %APPDATA%\\ComfyHub 数据；
      pip 全部 monkeypatch，不真装包。
"""
import json
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---- APPDATA 防污染：必须先于任何 EnvBackup/Rescue 构造 ----
BASE = r"F:\_bs_test"
APPDATA_DIR = os.path.join(BASE, "appdata")
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(APPDATA_DIR, exist_ok=True)
os.environ["APPDATA"] = APPDATA_DIR

from app.tools import EnvBackup, Rescue          # noqa: E402
from app import tools as tools_mod               # noqa: E402

CP = os.path.join(BASE, "ComfyUI")
NODES = os.path.join(CP, "custom_nodes")

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


logs = []


def log(m):
    logs.append(str(m))
    print("   |", m)


def set_nodes(names, files=()):
    shutil.rmtree(NODES, ignore_errors=True)
    os.makedirs(NODES, exist_ok=True)
    for n in names:
        os.makedirs(os.path.join(NODES, n))
    for f in files:
        open(os.path.join(NODES, f), "w").write("x")


def dirs():
    """与 list_custom_nodes 同口径：只看插件目录（跳过 __ 前缀与非目录）"""
    return sorted(n for n in os.listdir(NODES)
                  if os.path.isdir(os.path.join(NODES, n)) and not n.startswith("__"))


print("=== 1. 救援快照：记录数与名称规范化 ===")
set_nodes(["plugA", "plugB.disabled", "__pycache__"], files=["notes.txt"])
r = Rescue(CP)
ts1 = r.snapshot(log=log)
check("返回时间戳", bool(ts1), str(ts1))
check("日志报 2 个插件（修复前恒 0）", "2 个插件" in logs[-1], logs[-1])
p1 = os.path.join(r.snap_dir, ts1 + ".json")
check("快照文件已写", os.path.isfile(p1), p1)
state = json.load(open(p1, encoding="utf-8")) if os.path.isfile(p1) else []
names = [s["name"] for s in state]
check("名称剥离 .disabled 后缀", names == ["plugA", "plugB"], str(names))
check("启停状态正确", [s["enabled"] for s in state] == [True, False], str(state))
check("跳过 __pycache__ 与非目录文件",
      "__pycache__" not in names and "notes.txt" not in names, str(names))

print("\n=== 2. 回滚：禁用新增 + 恢复启停 ===")
# 模拟：快照之后新装 plugNew、plugA 出问题被禁用
os.rename(os.path.join(NODES, "plugA"), os.path.join(NODES, "plugA.disabled"))
os.makedirs(os.path.join(NODES, "plugNew"))
ok, msg = r.rollback(ts1 + ".json", log=log)
check("回滚返回 True", ok, msg)
check("禁用了快照后新增的 plugNew", "禁用新增: plugNew" in msg, msg)
check("恢复启用 plugA", "恢复启用: plugA" in msg, msg)
d = dirs()
check("目录终态正确", d == ["plugA", "plugB.disabled", "plugNew.disabled"], str(d))
check("无 xxx.disabled.disabled 双重禁用",
      not any(x.endswith(".disabled.disabled") for x in d), str(d))

print("\n=== 3. 回滚：状态一致（幂等）===")
ok, msg = r.rollback(ts1 + ".json", log=log)
check("返回 True", ok, msg)
check("报状态一致", msg == "状态一致，无需回滚", msg)
check("目录未被改动", dirs() == ["plugA", "plugB.disabled", "plugNew.disabled"],
      str(dirs()))

print("\n=== 4. 快照不存在 ===")
ok, msg = r.rollback("no_such.json", log=log)
check("返回 False", not ok, msg)
check("提示快照不存在", msg == "快照不存在", msg)

print("\n=== 5. 旧格式快照（name 带 .disabled 后缀）防御 ===")
json.dump([{"name": "plugB.disabled", "enabled": False}],
          open(os.path.join(r.snap_dir, "old_fmt.json"), "w", encoding="utf-8"))
ok, msg = r.rollback("old_fmt.json", log=log)
check("返回 True", ok, msg)
d = dirs()
check("未产生 plugB.disabled.disabled", "plugB.disabled.disabled" not in d, str(d))
check("plugB.disabled 原样保留", "plugB.disabled" in d, str(d))
check("启用中的 plugA 不在旧快照 → 被禁用", "禁用新增: plugA" in msg, msg)
check("终态三个插件全禁用",
      d == ["plugA.disabled", "plugB.disabled", "plugNew.disabled"], str(d))

print("\n=== 6. 空实例（无 custom_nodes 目录）===")
CP2 = os.path.join(BASE, "空实例")
r2 = Rescue(CP2)
ts2 = r2.snapshot(log=log)
check("快照 0 插件不崩", "0 个插件" in logs[-1], logs[-1])
ok, msg = r2.rollback(ts2 + ".json", log=log)
check("回滚空快照返回 True", ok, msg)

print("\n=== 7. 中文实例名 ===")
CP3 = os.path.join(BASE, "测试实例")
os.makedirs(os.path.join(CP3, "custom_nodes", "plugX"), exist_ok=True)
r3 = Rescue(CP3)
ts3 = r3.snapshot(log=log)
check("中文实例独立快照目录",
      os.path.basename(r3.snap_dir).startswith("测试实例"),
      os.path.basename(r3.snap_dir))
check("中文快照文件已写", os.path.isfile(os.path.join(r3.snap_dir, ts3 + ".json")))
ok, msg = r3.rollback(ts3 + ".json", log=log)
check("中文实例回滚状态一致", "状态一致" in msg, msg)

print("\n=== 7b. 同名不同路径实例目录隔离 ===")
CP4 = os.path.join(BASE, "d1", "ComfyUI")
CP5 = os.path.join(BASE, "d2", "ComfyUI")
os.makedirs(CP4, exist_ok=True)
os.makedirs(CP5, exist_ok=True)
check("快照目录隔离", Rescue(CP4).snap_dir != Rescue(CP5).snap_dir,
      os.path.basename(Rescue(CP4).snap_dir) + " vs " + os.path.basename(Rescue(CP5).snap_dir))
check("备份目录隔离", EnvBackup(CP4, "py").backup_dir != EnvBackup(CP5, "py").backup_dir)

print("\n=== 8. 环境备份：创建（monkeypatch pip）===")
set_nodes(["plugA", "plugB.disabled"])
real_run = tools_mod.run


class _FakeR:
    def __init__(self, rc=0, out="numpy==1.26.0\nrequests==2.31.0\n", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


calls = []


def fake_run(cmd, **kw):
    calls.append(list(cmd))
    return _FakeR()


tools_mod.run = fake_run
bk = EnvBackup(CP, os.path.join(BASE, "fake_python.exe"))
name = bk.create("测试", log=log)
check("返回备份名含备注", name.endswith("_测试"), name)
fdir = os.path.join(bk.backup_dir, name)
req = os.path.join(fdir, "requirements_freeze.txt")
check("freeze 文件已写", os.path.isfile(req), req)
check("freeze 内容正确",
      os.path.isfile(req) and open(req, encoding="utf-8").read().startswith("numpy==1.26.0"))
cn_path = os.path.join(fdir, "custom_nodes.json")
cn = json.load(open(cn_path, encoding="utf-8")) if os.path.isfile(cn_path) else []
check("插件清单含启停与禁用态",
      [(x["name"], x["enabled"]) for x in cn] == [("plugA", True), ("plugB", False)],
      str(cn))
check("调了 pip freeze", bool(calls) and calls[0][1:4] == ["-m", "pip", "freeze"],
      str(calls[0] if calls else None))

print("\n=== 9. 环境备份：还原 ===")
n0 = len(calls)
ok = bk.restore(name, log=log)
check("还原成功返回 True", ok is True)
check("走了 pip install -r",
      any("-r" in c and c[1:3] == ["-m", "pip"] for c in calls[n0:]),
      str(calls[-1][:8] if calls else None))
check("日志记还原成功", "还原成功" in logs[-1], logs[-1])
ok2 = bk.restore("not_exist", log=log)
check("不存在的备份返回 False", ok2 is False)
check("提示缺 freeze", "备份不存在或缺 freeze 文件" in logs[-1], logs[-1])
bare = "20200101_000000_bare"
os.makedirs(os.path.join(bk.backup_dir, bare), exist_ok=True)
ok3 = bk.restore(bare, log=log)
check("缺 freeze 文件返回 False", ok3 is False, logs[-1])


def fail_run(cmd, **kw):
    calls.append(list(cmd))
    return _FakeR(rc=1, out="", err="boom")


tools_mod.run = fail_run
ok4 = bk.restore(name, log=log)
check("pip 失败返回 False", ok4 is False)
check("日志记还原失败", "还原失败" in logs[-1], logs[-1])


def raise_run(cmd, **kw):
    raise OSError("pip 挂了")


tools_mod.run = raise_run
name2 = bk.create("异常", log=log)
check("创建时 pip 异常仍返回备份名", bool(name2), name2)
check("日志记 pip freeze 失败", any("pip freeze 失败" in m for m in logs),
      str(logs[-3:]))
check("该备份无 freeze 文件",
      not os.path.isfile(os.path.join(bk.backup_dir, name2, "requirements_freeze.txt")))
ok5 = bk.restore(name2, log=log)
check("无 freeze 的备份还原返回 False", ok5 is False, logs[-1])
tools_mod.run = real_run

print("\n=== 10. 备份列表倒序 ===")
lst = bk.list_backups()
check("至少 3 个备份", len(lst) >= 3, str(lst))
check("倒序排列", lst == sorted(lst, reverse=True), str(lst))

print("\n=== 11. UI 集成（offscreen）===")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication      # noqa: E402

qapp = QApplication(sys.argv[:1])
from app.config import load_config               # noqa: E402
from app.ui.pages.env_page import EnvPage, PipListWorker, _RunTask  # noqa: E402


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        qapp.processEvents()
        time.sleep(0.02)


class StubWin:
    """EnvPage 只用 current_inst() 与 procs.is_running()"""

    def __init__(self):
        self.procs = type("P", (), {"is_running": staticmethod(lambda inst: False)})()

    def current_inst(self):
        return {"id": "inst_bs", "name": "BS", "comfyui_path": CP,
                "python_path": sys.executable, "port": 8398, "vram_mode": "自动",
                "extra_args": "", "auto_open_browser": False}


page = EnvPage(load_config(), StubWin())
check("EnvPage 构建成功", page is not None)

# 11.1 PipListWorker 构造（修复前 TypeError: QThread.__init__(dict)）
try:
    w = PipListWorker({"python_path": sys.executable})
    check("PipListWorker(dict) 可构造", True)
    w.deleteLater()
except TypeError as e:
    check("PipListWorker(dict) 可构造", False, str(e))

# 11.2 _RunTask 异常走 sig_err（修复前伪装成 truthy 返回值）
done, errs = [], []
t = _RunTask(lambda: 1 / 0)
t.sig_done.connect(done.append)
t.sig_err.connect(errs.append)
t.start()
pump(2.0)
check("异常进 sig_err", bool(errs) and "division" in errs[0], str(errs))
check("sig_done 未被误发", not done, str(done))
done2, errs2 = [], []
t2 = _RunTask(lambda: False)
t2.sig_done.connect(done2.append)
t2.sig_err.connect(errs2.append)
t2.start()
pump(2.0)
check("falsy 成功值原样进 sig_done", done2 == [False], str(done2))
check("成功路径无 sig_err", not errs2, str(errs2))

# 11.3 跨线程 _log（修复前直碰 QWidget）
page.log.clear()
threading.Thread(target=lambda: page._log("线程日志OK"), daemon=True).start()
t0 = time.time()
while time.time() - t0 < 3 and "线程日志OK" not in page.log.toPlainText():
    pump(0.05)
check("工作线程 _log 安全送达", "线程日志OK" in page.log.toPlainText())

# 11.4 快照按钮（同步路径）+ 列表刷新 + 选中校验
page._snapshot()
kinds = [page.tb.item(i, 0).text() for i in range(page.tb.rowCount())]
check("快照行已入表", "快照" in kinds, str(kinds))
check("备份行也在表", "备份" in kinds, str(kinds))
snap_row = next((i for i in range(page.tb.rowCount())
                 if page.tb.item(i, 0).text() == "快照"), -1)
page.tb.selectRow(snap_row)
check("选中快照行可取名", page._selected("快照") is not None, str(page._selected("快照")))
bak_row = next((i for i in range(page.tb.rowCount())
                if page.tb.item(i, 0).text() == "备份"), -1)
page.tb.selectRow(bak_row)
check("选中备份行可取名", page._selected("备份") is not None, str(page._selected("备份")))
check("类型不符返回 None", page._selected("快照") is None)

# 11.5 异步创建备份全链路（_RunTask + 工作线程日志 + 完成刷新）
tools_mod.run = fake_run
n_before = page.tb.rowCount()
page.ed_label.setText("UI异步")
page._create_backup()
t0 = time.time()
while time.time() - t0 < 5 and page.tb.rowCount() <= n_before:
    pump(0.05)
check("异步备份完成并刷新列表", page.tb.rowCount() > n_before,
      f"{n_before} -> {page.tb.rowCount()}")
check("工作线程备份日志已送达", "备份完成" in page.log.toPlainText())
tools_mod.run = real_run

# 收尾：等待线程退出，避免 QThread destroyed while running
t = t2 = None
pump(0.3)

print("\n" + "=" * 46)
bad = [n for n, ok, _ in res if not ok]
print(f"结果: {len(res) - len(bad)}/{len(res)} 通过")
for n in bad:
    print("  ❌", n)
sys.exit(1 if bad else 0)
