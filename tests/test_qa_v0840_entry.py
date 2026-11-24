# -*- coding: utf-8 -*-
"""QA 独立验证：新建远程实例的**入口**与**全链路字段不丢**

两块，互相独立：

## A. 入口（`＋ 添加 ComfyUI` 必须能加远程实例）

背景：0.8.40 加了远程实例（`InstanceDialog` 支持 `kind="remote"`），但如果顶栏那个
`＋ 添加 ComfyUI` 仍然**单击直连安装向导**，用户就没有任何正常入口建远程实例 ——
功能做了等于没有。期望行为：点击弹 QMenu 且**恰 2 项**（本机安装 / 远程实例），
选第 2 项落到 `InstanceDialog` 且 `cb_kind.currentIndex()==1`，选第 1 项仍走
`_run_setup_wizard`。

做法：把 `QMenu.exec` 与 `InstanceDialog.exec` 打桩，点一下真按钮，看它到底弹了什么、
单选之后落到哪。**不猜源码结构**，只看用户可见的分支。

## B. 全链路：新建 → 保存 → 切页签 → 填参 → 保存 → 重开

这条专门防「新建的实例字段丢失」：
`_add_instance` 用 `uuid` 生成 id，而远程运维页签另有 15 个字段的 `collect()` 约定，
两边一旦脱节，用户填完 SSH 参数点保存就静默丢掉。

做法：完全走真实代码（`save_config` / `load_config` / `page._save()`），只有弹窗打桩。
落盘后**重新 `load_config()`**，逐字段比对。

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_entry.py
"""
import json
import os
import re
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# D-09：退出前先销毁 QApplication，否则进程结束阶段偶发 0xC0000005
from _qa_exit import clean_exit        # noqa: E402

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_entry_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)
# 故意**不放任何实例**：要验的就是「从零新建一个远程实例」
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"version": 1, "instances": [],
               "settings": {"lang": "zh", "close_behavior": "close"}},
              f, ensure_ascii=False)

from PySide6.QtWidgets import QApplication, QMessageBox            # noqa: E402

app = QApplication.instance() or QApplication([])

# 防挂死：这个用例点的是真按钮，一旦打桩失灵就会卡在真模态上（无头环境没人点）。
# 用 faulthandler 设一个硬超时，把「永远不返回」变成「超时 + 打印栈 + 非零退出」。
import faulthandler                                                # noqa: E402

faulthandler.dump_traceback_later(180, exit=True)

CJK = re.compile(r"[\u4e00-\u9fff]")
res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


# =====================================================================
print("== A. 入口：＋ 添加 ComfyUI 必须能加远程实例 ==")

from app.ui.main_window import MainWindow                          # noqa: E402
from app.ui import setup_wizard as _sw                             # noqa: E402
from app.ui.dialogs import InstanceDialog as _IDlg                 # noqa: E402

w = MainWindow()
app.processEvents()

from PySide6.QtWidgets import QPushButton                          # noqa: E402

_b_setup = None
for _b in w.findChildren(QPushButton):
    if "添加 ComfyUI" in (_b.text() or ""):
        _b_setup = _b
        break
check("A0 找得到顶栏「＋ 添加 ComfyUI」按钮", _b_setup is not None,
      _b_setup.text() if _b_setup else "未找到")

_menus = []          # 每次弹菜单记录 [(文案, ...)]
_dlgs = []           # 弹出过的 InstanceDialog
_wizs = []           # 被实例化的 SetupWizard
_pick = [0]          # 本次想选第几项

_orig_dlg_exec = _IDlg.exec


def _fake_pick(self, menu):
    """替掉 MainWindow._pick_add_action：记录菜单项并返回被选中的 action

    ⚠️ 不能去 patch `QMenu.exec`。实测（本机复现）：
        QMenu.__dict__['exec'] is fake → True    # 赋值确实写进了类字典
        type(m).exec           is fake → True    # Python 层查得到
        m.exec                 is fake → False   # 但实例取到的仍是 C++ 槽
        m.exec()               → None            # 走的是真模态
    Shiboken 解析槽函数时先查 C++ 方法表，类级 Python 属性会被忽略，
    于是测试会**卡死在真模态上**（无头环境没人点）而不是报失败。
    只有 Python 子类（InstanceDialog / SetupWizard）才能类级打桩。
    """
    acts = [x for x in menu.actions() if not x.isSeparator()]
    _menus.append([x.text() for x in acts])
    if not acts:
        return None
    return acts[min(_pick[0], len(acts) - 1)]


def _fake_dlg_exec(self, *a, **k):
    _dlgs.append(self)
    return 0        # Rejected：不动真实配置


class _FakeWiz:
    Accepted = 1

    def __init__(self, *a, **k):
        self.setup_result = None
        _wizs.append(self)

    def exec(self):
        return 0    # Rejected：不装东西


_orig_pick = getattr(MainWindow, "_pick_add_action", None)
MainWindow._pick_add_action = _fake_pick
_IDlg.exec = _fake_dlg_exec
_orig_wiz = _sw.SetupWizard
_sw.SetupWizard = _FakeWiz

# 打桩自检：接缝一旦被改名/删掉，这里立刻红，而不是让下面卡在真模态上。
# 注意不能用 `MainWindow._pick_add_action is _fake_pick` —— 取属性会包成绑定方法，
# 身份比较恒为 False，只有查类的 __dict__ 才可靠。
check("A0' 入口接缝 MainWindow._pick_add_action 打桩生效（否则会卡死在真模态）",
      MainWindow.__dict__.get("_pick_add_action") is _fake_pick,
      "接缝不存在或打桩未生效")

_orig_info = QMessageBox.information
QMessageBox.information = staticmethod(lambda *a, **k: None)

try:
    # ---- A1：点一下，看有没有弹菜单 ----
    _menus.clear()
    _dlgs.clear()
    _wizs.clear()
    _pick[0] = 0
    if _b_setup is not None:
        _b_setup.click()
        app.processEvents()

    check("A1 点击后弹出 QMenu（而不是直接跳向导）", len(_menus) == 1,
          f"弹了 {len(_menus)} 个菜单；向导被实例化={len(_wizs)} 次")
    _items = _menus[0] if _menus else []
    check("A2 菜单恰有 2 项（本机安装 / 远程实例）", len(_items) == 2, _items)
    _has_remote = any("远程" in x for x in _items)
    _has_local = any(("安装" in x) or ("本机" in x) or ("向导" in x) for x in _items)
    check("A3 有一项是「远程实例」", _has_remote, _items)
    check("A4 有一项是「本机安装/向导」", _has_local, _items)

    # ---- A5：选第 2 项（远程）→ 应落 InstanceDialog 且 kind=remote ----
    _dlgs.clear()
    _wizs.clear()
    _menus.clear()
    _pick[0] = 1
    if _b_setup is not None:
        _b_setup.click()
        app.processEvents()
    check("A5 选「远程」后弹出 InstanceDialog", len(_dlgs) == 1,
          f"InstanceDialog 数={len(_dlgs)}，向导数={len(_wizs)}")
    if _dlgs:
        check("A6 该对话框的连接方式预置为「远程」",
              _dlgs[0].cb_kind.currentIndex() == 1,
              f"currentIndex={_dlgs[0].cb_kind.currentIndex()}")

    # ---- A7：选第 1 项（本机）→ 仍走安装向导，不弹 InstanceDialog ----
    _dlgs.clear()
    _wizs.clear()
    _menus.clear()
    _pick[0] = 0
    if _b_setup is not None:
        _b_setup.click()
        app.processEvents()
    check("A7 选「本机安装」仍走安装向导", len(_wizs) == 1,
          f"向导数={len(_wizs)}")
    check("A8 选「本机安装」不弹 InstanceDialog", len(_dlgs) == 0,
          f"InstanceDialog 数={len(_dlgs)}")
finally:
    if _orig_pick is not None:
        MainWindow._pick_add_action = _orig_pick
    else:
        try:
            del MainWindow._pick_add_action
        except Exception:                  # noqa: BLE001
            pass
    _IDlg.exec = _orig_dlg_exec
    _sw.SetupWizard = _orig_wiz
    QMessageBox.information = _orig_info


# =====================================================================
print("== B. 全链路：新建远程实例 → 保存 → 填页签 → 保存 → 重开 ==")

from app.ui import changes_dialog as _cd                          # noqa: E402
from app.config import save_config, load_config                    # noqa: E402

# ---- B1：对话框产出（远程分支）----
dlg = _IDlg()
dlg.cb_kind.setCurrentIndex(1)
dlg.ed_url.setText("10.0.0.9:8188")        # 故意不带 scheme
dlg.ed_name.setText("")
dlg._accept()
data = dlg.result_data()
check("B1 远程分支产出的 kind 是 remote", data.get("kind") == "remote", data.get("kind"))
check("B2 地址自动补全 scheme", str(data.get("base_url")).startswith("http://"),
      data.get("base_url"))
check("B3 名字留空时按地址自动生成", bool(str(data.get("name")).strip()),
      data.get("name"))
check("B4 远程实例不许落假目录（normpath('')=='.' 的老坑）",
      data.get("comfyui_path") == "" and data.get("python_path") == "",
      (data.get("comfyui_path"), data.get("python_path")))

# ---- B5：走真实的「加进配置并落盘」----
data["id"] = "inst_" + uuid.uuid4().hex[:8]
w.cfg.setdefault("instances", []).append(data)
save_config(w.cfg)
w._sync_instances()
app.processEvents()

_iids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_iids.index(data["id"]))
w.comfyui_page.load_current()
app.processEvents()

_iid = data["id"]
_inst = w.comfyui_page._current_inst()
check("B5 新实例已在当前页选中", bool(_inst) and _inst.get("id") == _iid,
      _inst.get("id") if _inst else None)

# ---- B6：页签 collect() 的 15 个键必须都落在 _TRACK 里 ----
ro = w.comfyui_page.remote_ops
_vals = ro.collect()
_TRACK = set(w.comfyui_page._TRACK)
_not_in_track = [k for k in _vals if k not in _TRACK]
check("B6 collect() 的每个键都是页签跟踪字段", not _not_in_track, _not_in_track)
check("B7 collect() 恰 15 个可编辑字段", len(_vals) == 15, len(_vals))

# ---- B8：在页签上填 SSH / 隧道参数，然后真保存 ----
ro.ed_host.setText("192.168.1.50")
ro.sp_port.setValue(2222)
ro.ed_user.setText("ubuntu")
ro.cb_auth.setCurrentIndex(0)                 # 0 = 密码
ro.ed_pass.setText("PW-QA-1")
ro.pt_start.setPlainText("bash run.sh")
ro.pt_stop.setPlainText("pkill -f main.py")
ro.sp_local.setValue(18188)
ro.ed_rhost.setText("127.0.0.1")
ro.sp_rport.setValue(8188)
for _i in range(ro.cb_channel.count()):
    if str(ro.cb_channel.itemData(_i) or "") == "ssh":
        ro.cb_channel.setCurrentIndex(_i)
        break

_cd.confirm_changes = lambda *a, **k: True     # 保存对比框：直接确认
w.comfyui_page._insecure_modal = lambda inst, new: "ok"    # http 明文告警：继续
QMessageBox.information = staticmethod(lambda *a, **k: None)
try:
    w.comfyui_page._save()
finally:
    QMessageBox.information = _orig_info
app.processEvents()

# ---- B9：重新从磁盘读回来，逐字段比对 ----
_cfg2 = load_config()
_after = next((i for i in _cfg2.get("instances") or [] if i.get("id") == _iid), None)
check("B9 重开后仍能找到该实例", _after is not None, _iid)

if _after is None:
    _after = {}

_EXPECT = {
    "kind": "remote",
    "ssh_host": "192.168.1.50",
    "ssh_port": 2222,
    "ssh_user": "ubuntu",
    "ssh_password": "PW-QA-1",
    "ssh_start_cmd": "bash run.sh",
    "ssh_stop_cmd": "pkill -f main.py",
    "tunnel_local_port": 18188,
    "tunnel_remote_host": "127.0.0.1",
    "tunnel_remote_port": 8188,
    "last_ops_channel": "ssh",
}
_bad = {}
for _k, _v in _EXPECT.items():
    if _after.get(_k) != _v:
        _bad[_k] = (_after.get(_k), _v)
check("B10 页签填的 11 个字段全部落盘且值正确", not _bad,
      {k: "落盘=%r 期望=%r" % (v[0], v[1]) for k, v in _bad.items()})

# 落盘的是密文，内存里是明文（DPAPI 语义）
_on_disk = open(os.path.join(_cfg_dir, "config.json"), encoding="utf-8").read()
check("B11 SSH 密码在磁盘上是 enc:v1 密文",
      "PW-QA-1" not in _on_disk and "enc:v1:" in _on_disk,
      "明文出现=%s" % ("PW-QA-1" in _on_disk))

# ---- B12：重开页签（模拟用户再进一次），值要能回填 ----
w.cb_inst.setCurrentIndex([i.get("id") for i in w.cfg["instances"]].index(_iid))
w.comfyui_page.load_current()
app.processEvents()
_back = ro.collect()
_bad2 = {k: (_back.get(k), _EXPECT[k]) for k in _EXPECT
         if k in _back and _back.get(k) != _EXPECT[k]}
check("B12 重新进页签后 15 个字段回填一致", not _bad2, _bad2)

# =====================================================================
_bad = [n for n, ok, _ in res if not ok]
print()
print("入口/全链路：%d/%d 通过" % (len(res) - len(_bad), len(res)))
if _bad:
    print("FAILED:")
    for n in _bad:
        print("  - " + n)
sys.stdout.flush()
clean_exit(1 if _bad else 0)
