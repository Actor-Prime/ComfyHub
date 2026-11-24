# -*- coding: utf-8 -*-
"""QA 独立验证 V6：i18n 完整性（PRD X2，P0：「切英文后新增页签、按钮、提示无残留中文」）

工程师自测走的是**控件树扫描**（`tests/i18n_leftover.py`），它只看「界面上已经被设好
的静态文案」，而且 `app/i18n._translate_tree` 只扫这几种控件：
QLabel / QAbstractButton / QGroupBox / QLineEdit / QTabWidget / QTableWidget 表头 /
QMainWindow·QDialog 标题 / QListWidget item。

于是有两类必然漏掉的地方：
  ① **运行期 setText**（失败后才显示的那句）—— 控件树扫描发生在建窗时，
     之后再 setText 的内容不会被重扫，除非调用点自己写 `T("...")`；
  ② **扫描列表外的控件**：QComboBox 的 item、QPlainTextEdit 的 placeholder、
     非 QLineEdit 的 tooltip（词表里就算有也永远翻不过来）。

本文件的断言方式是**实测**：离屏建窗 → 切英文 → 触发真实代码路径 → 读控件上的字，
看里面还有没有中文。这比 AST 猜「包没包 T()」可靠。

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_i18n.py
"""
import ast
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

# D-09：退出前先销毁 QApplication，否则进程结束阶段偶发 0xC0000005
from _qa_exit import clean_exit        # noqa: E402

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_i18n_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"version": 1, "last_instance_id": "r",
               "instances": [{"id": "r", "name": "R", "kind": "remote",
                              "base_url": "http://1.2.3.4:8188",
                              "auth_type": "none", "port": 8188,
                              "comfyui_path": "", "python_path": "",
                              "vram_mode": "自动", "extra_args": "",
                              "ssh_host": "1.2.3.4", "ssh_user": "root",
                              "ssh_start_cmd": "echo hi",
                              "ssh_stop_cmd": "echo bye"}],
               "settings": {"lang": "en"}}, f, ensure_ascii=False)

from app import i18n                                       # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


def cn(text) -> bool:
    return bool(CJK.search(str(text or "")))


# =====================================================================
print("== V6-1 静态：所有 T()/tr() 的字面量键都在 i18n._EN 里 ==")
TOUCHED = ["app/ui/pages/remote_ops_page.py", "app/ui/remote_banner.py",
           "app/ui/remote_start_dialog.py", "app/ui/pages/comfyui_page.py",
           "app/ui/pages/settings_page.py", "app/ui/main_window.py",
           "app/ui/pages/console_page.py", "app/workers.py"]
_keys = []


def _is_T(n):
    return (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in ("T", "tr"))


def _pieces(node):
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and cn(node.value):
        out.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str) and cn(v.value):
                out.append(v.value)
    return out


for f in TOUCHED:
    tree = ast.parse(open(os.path.join(ROOT, f), encoding="utf-8").read())
    for n in ast.walk(tree):
        if _is_T(n):
            for a in n.args:
                for k in _pieces(a):
                    _keys.append((f, k))
_missing = [(f, k) for f, k in _keys if k not in i18n._EN]
check("V6-1a T() 键共 %d 条（新增文案确实走了 T()）" % len(_keys), len(_keys) > 0)
check("V6-1b 全部 T() 键都能在词表里逐字查到", not _missing, _missing)

# =====================================================================
print("== V6-2 实测：英文模式下运行期 setText 出来的文案 ==")
from PySide6.QtWidgets import QApplication, QMessageBox     # noqa: E402
from app.ui.main_window import MainWindow                    # noqa: E402

app = QApplication.instance() or QApplication([])
w = MainWindow()
page = w.comfyui_page
_ids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_ids.index("r"))
page.load_current()
app.processEvents()
n1 = i18n.apply_language(w, "en")            # 完整的静态扫描（建窗后再扫一遍）
app.processEvents()
ro = page.remote_ops

# 桩掉模态，避免阻塞（不关心弹窗内容，只关心控件上的字）
_orig_info, _orig_warn = QMessageBox.information, QMessageBox.warning
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

# 2a：SSH 失败的 9 种 kind —— 这是 PRD 5.1.3 的逐字文案，用户一定会看到
KINDS = ["auth_password", "auth_passphrase", "auth_key", "key_missing",
         "fingerprint", "sudo", "timeout", "unreachable", "unknown"]
_cn_kinds = {}
for k in KINDS:
    t = ro.ssh_failure_text(k, "detail")
    if cn(t):
        _cn_kinds[k] = str(t).splitlines()[0][:44]
check("V6-2a 英文模式下 ssh_failure_text 的 9 种文案都不含中文",
      not _cn_kinds, _cn_kinds)

# 2b：真正走 setText 的那一行（失败后 lb_ssh_test 上留的就是第一行）
ro._on_ssh_tested(False, "auth_password")
_s1 = ro.lb_ssh_test.text()
check("V6-2b 英文模式下 lb_ssh_test 失败提示不含中文", not cn(_s1), _s1[:60])
check("V6-2c 英文模式下「测试 SSH」按钮回到英文", not cn(ro.b_ssh_test.text()),
      ro.b_ssh_test.text())

# 2d：成功分支
ro._on_ssh_tested(True, "")
_s2 = ro.lb_ssh_test.text()
check("V6-2d 英文模式下 SSH 成功提示不含中文", not cn(_s2), _s2[:60])

# 2e：隧道未装配的兜底提示
# 注意：这里**不能**手动 setText 去「造」这条中文——那会把后面 V6-4e 的全页扫
# 描结果污染成假阳性（上一版就犯了这个错，把注入的文案当成了真实残留）。
# 改成源码级取证：这句话必须在词表里，且每个调用点都要经 T() 才翻得过来。
_src_ro = open(os.path.join(ROOT, "app/ui/pages/remote_ops_page.py"),
               encoding="utf-8").read()
_TUN_DOWN = "隧道功能还没就绪：请重启 ComfyHub 后再试"
check("V6-2e 「隧道功能还没就绪…」这句本身在词表里",
      _TUN_DOWN in i18n._EN, i18n._EN.get(_TUN_DOWN))
check("V6-2e' 每个调用点都走了 T()（裸 setText 一处都不许留）",
      _src_ro.count(f'T("{_TUN_DOWN}")') >= 1
      and _src_ro.count(f'setText("{_TUN_DOWN}")') == 0,
      "T() 处数=%d，裸 setText 处数=%d" % (
          _src_ro.count(f'T("{_TUN_DOWN}")'),
          _src_ro.count(f'setText("{_TUN_DOWN}")')))

QMessageBox.information, QMessageBox.warning = _orig_info, _orig_warn

# =====================================================================
print("== V6-3 worker 回来才设置的文案（PRD X2 点名的四类）==")
_src_settings = open(os.path.join(ROOT, "app/ui/pages/settings_page.py"),
                     encoding="utf-8").read()
for key in ("统计中…", "尚无缓存", "总计", "张"):
    check(f"V6-3 {key}：词表里有", key in i18n._EN, i18n._EN.get(key))
    check(f"V6-3 {key}：调用点走了 T()", f'T("{key}")' in _src_settings)
check("V6-3e 「张」的英文不是中文原样", i18n._EN.get("张", "张") != "张",
      i18n._EN.get("张"))
# 实测一遍：英文模式下 T("尚无缓存") 必须变英文
check("V6-3f 英文模式实测 T('尚无缓存') 已翻译", not cn(i18n.T("尚无缓存")),
      i18n.T("尚无缓存"))
check("V6-3g 英文模式实测 T('总计') 已翻译", not cn(i18n.T("总计")), i18n.T("总计"))

# =====================================================================
print("== V6-4 扫描列表外的控件（词表里有也永远翻不过来）==")
_combo_cn = []
for _name, _cb in (("认证方式", ro.cb_auth), ("默认通道", ro.cb_channel),
                   ("平台模板", ro.cb_tpl), ("方法(start)", ro.cb_cs_method)):
    for i in range(_cb.count()):
        if cn(_cb.itemText(i)):
            _combo_cn.append((_name, _cb.itemText(i)))
check("V6-4a QComboBox 的 item 文本在英文模式下无中文（_translate_tree 不扫 combo）",
      not _combo_cn, _combo_cn)

_ph_cn = []
for _name, _wd in (("启动命令", ro.pt_start), ("停止命令", ro.pt_stop),
                   ("请求头", ro.pt_cs_headers), ("请求体", ro.pt_cs_body)):
    if cn(_wd.placeholderText()):
        _ph_cn.append((_name, _wd.placeholderText()[:32]))
check("V6-4b QPlainTextEdit 的 placeholder 在英文模式下无中文（也不在扫描列表）",
      not _ph_cn, _ph_cn)

# QLineEdit 的 placeholder 是扫的 → 应当已翻
check("V6-4c 对照组：QLineEdit 的 placeholder 已被翻译（证明差异来自控件类型）",
      not cn(ro.ed_pass.placeholderText()), ro.ed_pass.placeholderText())

# 模板骨架灰字（QLabel 是扫的 → 说明它压根不在词表里）
_tip = "模板只是骨架，参数（实例 ID、区域、Token）请按平台 API 文档核对后再保存"
check("V6-4d 「模板只是骨架…」在词表里", _tip in i18n._EN)
_labels_cn = [lb.text() for lb in ro.findChildren(type(ro.lb_tunnel))
              if cn(lb.text())]
check("V6-4e 整个远程运维页签内仍显示中文的 QLabel 数 = 0",
      not _labels_cn, [x[:50] for x in _labels_cn])

# 非 QLineEdit 的 tooltip
_tip_cn = []
for _name, _wd in (("b_tunnel_stop", ro.b_tunnel_stop),
                   ("b_tunnel_retry", ro.b_tunnel_retry)):
    if cn(_wd.toolTip()):
        _tip_cn.append((_name, _wd.toolTip()[:34]))
check("V6-4f QPushButton 的 tooltip 在英文模式下无中文（只有 QLineEdit 的 tooltip 被扫）",
      not _tip_cn, _tip_cn)

# =====================================================================
print("== V6-5 键必须逐字一致（近似串不算命中）==")
check("V6-5a 半角省略号不能命中全角省略号",
      bool(i18n._EN.get("统计中…")) and i18n._EN.get("统计中...") is None,
      (i18n._EN.get("统计中…"), i18n._EN.get("统计中...")))
check("V6-5b 含 emoji 的键整串一致（✅ 后的空格也要对）",
      "✅ SSH 连接与认证都正常" in i18n._EN)

# =====================================================================
# 2026-11-24 复核 commit 8594bde（「补词表：T05 遗漏的 11 条远程文案」）。
# 该 commit **只改 app/i18n_en.py**，一行 UI 源码都没动。
# 工程师的依据是 tests/test_theme_i18n.py 6a（静态：AST 扫到的中文字面量是否都在词表里），
# 57/57 全绿。但「词表里有」和「运行时翻得过来」是两件事：
#   · 只有 `T("...")` 调用点 和 建窗时被 _translate_tree 扫到的控件类型 才真会翻；
#   · 运行期 setText、QComboBox item、QPlainTextEdit 占位符、按钮 tooltip、QMessageBox
#     —— 词表再怎么补也翻不过来。下面逐条实测这 15 条。
print("== V6-6 复核 8594bde：新增 15 条词条「词表里有」≠「运行时翻得过来」==")
NEW_KEYS = {
    "请填写对方 ComfyUI 的访问地址，例：http://192.168.1.20:8188":
        "dialogs.py:44 QLineEdit 占位符（扫）/ :147 QLabel 校验提示（运行期）",
    "■ 远程停止": "comfyui_page:1235 _update_remote_state 运行期 setText",
    "正在启动，等待实例就绪（最多 5 分钟）": "comfyui_page:1244 按钮 tooltip",
    "中止本轮等待并执行停止命令（不会杀无关进程）": "comfyui_page:1254 按钮 tooltip",
    "在远端执行停止命令，或请求云平台关机": "comfyui_page:1256 按钮 tooltip",
    "实例已在运行，如需重启请先「远程停止」": "comfyui_page:1246 按钮 tooltip",
    "在远端执行启动命令，或请求云平台开机": "comfyui_page:1248 按钮 tooltip",
    "在远端执行的启动命令": "remote_ops_page:179 QPlainTextEdit 占位符",
    "在远端执行的停止命令": "remote_ops_page:195 QPlainTextEdit 占位符",
    r"如 C:\Users\你\.ssh\id_ed25519": "remote_ops_page:144 QLineEdit 占位符（扫）",
    "JSON，可留空": "remote_ops_page:270 QPlainTextEdit 占位符",
    "✅ SSH 连接与认证都正常": "remote_ops_page:521 运行期 setText",
    "隧道功能还没就绪：请重启 ComfyHub 后再试": "remote_ops_page:648/657 运行期 setText",
    "套用模板会覆盖当前已填的开机/关机请求，继续？": "remote_ops_page:586 QMessageBox 运行期",
    "正在清理…": "settings_page:1121 运行期 setText",
}
_miss_new = [k for k in NEW_KEYS if k not in i18n._EN]
check("V6-6a 这 15 条新词条确实都进了合并词表（工程师那一层是对的）",
      not _miss_new, _miss_new)

# --- 6b/6c 实例对话框：QLineEdit 占位符（扫得到） vs QLabel 校验提示（运行期）
from app.ui.dialogs import InstanceDialog                      # noqa: E402
dlg = InstanceDialog()
dlg.cb_kind.setCurrentIndex(1)          # 切到「远程实例」，才校验地址
i18n.apply_language(dlg, "en")
app.processEvents()
check("V6-6b 实例对话框「访问地址」占位符已英译", not cn(dlg.ed_url.placeholderText()),
      dlg.ed_url.placeholderText()[:44])
try:
    dlg.ed_url.setText("")
    dlg._accept()                        # 地址为空 → 只设 lb_hint 就 return
except Exception as _e:                  # noqa: BLE001
    print("      （_accept 抛异常，忽略：%r）" % (_e,))
check("V6-6c 实例对话框「地址不能为空」校验提示已英译", not cn(dlg.lb_hint.text()),
      dlg.lb_hint.text()[:50])

# --- 6d~6g 主窗远程按钮：用户切完语言就去用 → _update_remote_state 用中文重刷
_inst = w.cfg["instances"][0]
i18n.apply_language(w, "en")
app.processEvents()
try:
    page._update_remote_state(_inst)
except Exception as _e:                  # noqa: BLE001
    print("      （_update_remote_state 抛异常，忽略：%r）" % (_e,))
app.processEvents()
check("V6-6d 「■ 远程停止」切语言后不再变回中文", not cn(page.b_stop.text()),
      page.b_stop.text())
check("V6-6e 「远程启动」切语言后不再变回中文", not cn(page.b_start.text()),
      page.b_start.text())
check("V6-6f b_stop 的 tooltip 已英译", not cn(page.b_stop.toolTip()),
      page.b_stop.toolTip()[:50])
check("V6-6g b_start 的 tooltip 已英译", not cn(page.b_start.toolTip()),
      page.b_start.toolTip()[:50])

# --- 6h~6k 远程运维页签：QPlainTextEdit 占位符（扫不到） vs QLineEdit（扫得到）
check("V6-6h 「在远端执行的启动命令」占位符已英译",
      not cn(ro.pt_start.placeholderText()), ro.pt_start.placeholderText()[:44])
check("V6-6i 「在远端执行的停止命令」占位符已英译",
      not cn(ro.pt_stop.placeholderText()), ro.pt_stop.placeholderText()[:44])
check("V6-6j 「JSON，可留空」占位符已英译",
      not cn(ro.pt_cs_body.placeholderText()), ro.pt_cs_body.placeholderText()[:44])
check("V6-6k 对照组：QLineEdit 的私钥路径占位符已英译",
      not cn(ro.ed_key.placeholderText()), ro.ed_key.placeholderText()[:44])

# --- 6l 套用模板覆盖确认：QMessageBox 是运行期 new 出来的，扫不到
import app.ui.pages.remote_ops_page as _rop                     # noqa: E402


class _FakeBox:
    # 调用点写的是 QMessageBox.AcceptRole / RejectRole，替换掉类之后这两个常量也
    # 得跟着存在，否则 _apply_template 会在 addButton 处抛 AttributeError
    AcceptRole = 0
    RejectRole = 1

    def __init__(self, *a, **k):
        self.text = ""
        self.title = ""
        _boxes.append(self)

    def setWindowTitle(self, t):
        self.title = t

    def setText(self, t):
        self.text = t

    def addButton(self, *a, **k):
        return object()

    def exec(self):
        return 0

    def clickedButton(self):
        return None


_boxes = []
_orig_box = _rop.QMessageBox
_rop.QMessageBox = _FakeBox
try:
    ro.pt_cs_body.setPlainText("{}")        # _cloud_filled() 要有一个非空块
    if ro.cb_tpl.count() > 1:
        ro.cb_tpl.setCurrentIndex(1)
    ro._apply_template()
except Exception as _e:                     # noqa: BLE001
    print("      （_apply_template 抛异常，忽略：%r）" % (_e,))
finally:
    _rop.QMessageBox = _orig_box
_conf = [b.text for b in _boxes if b.text]
check("V6-6l 「套用模板会覆盖…」确认框文案已英译",
      len(_conf) >= 1 and not any(cn(x) for x in _conf),
      _conf if _conf else "未捕获到确认框（cb_tpl 无有效模板？）")

# --- 6m 设置页「正在清理…」：同样是运行期 setText
import app.ui.pages.settings_page as _sp                        # noqa: E402


class _FakeSig:
    def connect(self, *a, **k):
        pass


class _FakeWorker:
    def __init__(self, *a, **k):
        self.sig_done = _FakeSig()

    def start(self):
        pass


_orig_cw = _sp.CleanupWorker
_sp.CleanupWorker = _FakeWorker
try:
    w.settings_page._start_clean("")
except Exception:                           # noqa: BLE001
    pass                                    # setText 是第一行，异常前已生效
finally:
    _sp.CleanupWorker = _orig_cw
_rc_state = w.settings_page.lb_rc_state.text()
check("V6-6m 「正在清理…」状态行已英译", not cn(_rc_state), _rc_state[:40])

i18n.set_lang("zh")

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V6 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
clean_exit(1 if bad else 0)
