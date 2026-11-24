# -*- coding: utf-8 -*-
"""远程实例 UI 冒烟（0.8.39）：离屏渲染实例设置页，验证连接方式切换不炸

做法：把 APPDATA 指到临时目录（config.CONFIG_PATH 在导入期计算，必须先设环境），
写一份含「本地 + 远程」两个实例的配置，然后离屏建主窗、截两张图。
产物：analysis/remote_ui_local.png / remote_ui_remote.png
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# D-09：退出前先销毁 QApplication，否则进程结束阶段偶发 0xC0000005
from _qa_exit import clean_exit      # noqa: E402

# ★ 必须在 import app.* 之前重定向数据目录
_TMP = tempfile.mkdtemp(prefix="comfyhub_ui_")
os.environ["APPDATA"] = _TMP
# 远程产物落在 %LOCALAPPDATA%\ComfyHub\remote（api_base.remote_output_dir 运行期读环境），
# 不改这里 T07 段就会往真实用户目录写文件 / 扫目录
os.environ["LOCALAPPDATA"] = _TMP
_cfg_dir = os.path.join(_TMP, "ComfyHub")
os.makedirs(_cfg_dir, exist_ok=True)
cfg = {
    "version": 1,
    "last_instance_id": "inst_r",
    "instances": [
        {"id": "inst_l", "name": "本机实例", "kind": "local",
         "comfyui_path": r"F:\ComfyUI-aki-v3\ComfyUI",
         "python_path": r"F:\ComfyUI-aki-v3\python\python.exe",
         "port": 8188, "vram_mode": "自动", "extra_args": ""},
        {"id": "inst_r", "name": "云镜像", "kind": "remote",
         "base_url": "https://xxx.platform.com", "auth_type": "bearer",
         "auth_value": "demo-token", "auth_header_name": "X-API-KEY",
         "tls_verify": True, "port": 8188, "comfyui_path": "",
         "python_path": "", "vram_mode": "自动", "extra_args": ""},
    ],
    "settings": {},
}
with open(os.path.join(_cfg_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication          # noqa: E402
from app.ui.main_window import MainWindow           # noqa: E402

OUT = os.path.join(ROOT, "analysis")
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
w = MainWindow()
w.resize(1680, 1020)
w.show()
app.processEvents()
w.stack.setCurrentWidget(w.comfyui_page)
page = w.comfyui_page

# 选远程实例（current_inst() 认的是顶部下拉的当前项）
_ids = [i.get("id") for i in w.cfg["instances"]]
w.cb_inst.setCurrentIndex(_ids.index("inst_r"))
page.load_current()
app.processEvents()

assert page.cb_kind.currentIndex() == 1, "远程实例应选中「远程实例」"
assert page.ed_url.text() == "https://xxx.platform.com", page.ed_url.text()
assert page.cb_auth.currentIndex() == 1, "auth_type=bearer 应选中第 2 项"
assert page.row_url.isVisible(), "远程模式应显示访问地址行"
assert not page.row_basic.isVisible(), "bearer 模式不该显示账号密码行"
items = page._t1v._items
hidden_ok = all(getattr(items[i][1], "isVisible", lambda: True)() is False
                for i in range(page._local_start, page._local_end)
                if hasattr(items[i][1], "isVisible"))
print("远程模式：本机专属行已隐藏 =", hidden_ok)
p1 = os.path.join(OUT, "remote_ui_remote.png")
page.grab().save(p1)
print("截图:", p1)

# 切回本地实例
w.cb_inst.setCurrentIndex(_ids.index("inst_l"))
page.load_current()
app.processEvents()
assert page.cb_kind.currentIndex() == 0
assert not page.row_url.isVisible(), "本地模式不该显示访问地址行"
vis_local = sum(1 for i in range(page._local_start, page._local_end)
                if hasattr(items[i][1], "isVisible") and items[i][1].isVisible())
print("本地模式：可见的本机专属行 =", vis_local)
p2 = os.path.join(OUT, "remote_ui_local.png")
page.grab().save(p2)
print("截图:", p2)

# ---------------------------------------------------------------- 0.8.40 T05
# 远程运维页签：可见性 / 不留空位 / collect() ↔ _snapshot() 必须对得上
print("=== T05：远程运维页签 ===")
_ids = [i.get("id") for i in w.cfg["instances"]]
# 让实例带一份「已保存的 SSH 密码」，验证「留空 = 保持原值」
w.cfg["instances"][1]["ssh_password"] = "旧密码-old"
w.cb_inst.setCurrentIndex(_ids.index("inst_r"))
page.load_current()
app.processEvents()

assert page.tabs.count() == 4, page.tabs.count()
assert page.tabs.isTabVisible(3) is True, "远程实例必须看到第 4 个页签"
assert page.tabs.tabText(3).startswith("🛠"), page.tabs.tabText(3)
panel = page.remote_ops
print("远程：页签数 =", page.tabs.count(), "第 4 页可见 =", page.tabs.isTabVisible(3))

# 认证方式切换 → 行显隐正确（密码 ↔ 密钥文件）
panel.cb_auth.setCurrentIndex(1)
app.processEvents()
assert not panel.row_pass.isVisibleTo(panel) and panel.row_key.isVisibleTo(panel), \
    "密钥模式应显示密钥行"
panel.cb_auth.setCurrentIndex(0)
app.processEvents()
assert panel.row_pass.isVisibleTo(panel) and not panel.row_key.isVisibleTo(panel), \
    "密码模式应显示密码行"
print("认证方式切换：行显隐正确")

# 在页签上改一堆值 → _snapshot() 必须原样带回（防「改了不保存」）
panel.ed_host.setText("192.168.1.50")
panel.sp_port.setValue(2222)
panel.ed_user.setText("ubuntu")
panel.cb_auth.setCurrentIndex(1)
panel.ed_key.setText(r"C:\Users\me\.ssh\id_ed25519")
panel.ed_pp.setText("")                      # 留空 = 保持原值
panel.pt_start.setPlainText(
    "cd /opt/ComfyUI && nohup python3 main.py --listen 127.0.0.1 --port 8188 "
    "> /tmp/c.log 2>&1 &")
panel.pt_stop.setPlainText('pkill -f "opt/ComfyUI/main.py" || true')
panel.ed_cs_url.setText("https://api.example.com/start")
panel.cb_cs_method.setCurrentIndex(panel.cb_cs_method.findData("PUT"))
panel.pt_cs_headers.setPlainText("Authorization: Bearer tok\nX-API-KEY: k")
panel.pt_cs_body.setPlainText('{"instance_id": "i-1"}')
panel.ed_ce_url.setText("https://api.example.com/stop")
panel.sp_local.setValue(8288)
panel.ed_rhost.setText("10.0.0.5")
panel.sp_rport.setValue(8199)
app.processEvents()

_collected = panel.collect()
_expect_keys = {
    "ssh_host", "ssh_port", "ssh_user", "ssh_auth", "ssh_password",
    "ssh_key_path", "ssh_key_passphrase", "ssh_start_cmd", "ssh_stop_cmd",
    "cloud_start", "cloud_stop", "tunnel_local_port", "tunnel_remote_host",
    "tunnel_remote_port", "last_ops_channel",
}
assert set(_collected) == _expect_keys, sorted(set(_collected) ^ _expect_keys)
print("collect() 键数与 _TRACK 对齐：", len(_collected), "个")

snap = page._snapshot()
assert snap["ssh_host"] == "192.168.1.50", snap.get("ssh_host")
assert snap["ssh_port"] == 2222, snap.get("ssh_port")
assert snap["ssh_user"] == "ubuntu"
assert snap["ssh_auth"] == "key"
assert snap["ssh_key_path"] == r"C:\Users\me\.ssh\id_ed25519"
assert snap["ssh_password"] == "旧密码-old", "密码留空必须保持原值"
assert snap["ssh_key_passphrase"] == "", snap.get("ssh_key_passphrase")
assert snap["ssh_start_cmd"].startswith("cd /opt/ComfyUI")
assert snap["ssh_stop_cmd"].startswith("pkill -f")
assert snap["cloud_start"]["url"] == "https://api.example.com/start"
assert snap["cloud_start"]["method"] == "PUT", snap["cloud_start"]["method"]
assert snap["cloud_start"]["body"] == '{"instance_id": "i-1"}'
assert snap["cloud_stop"]["url"] == "https://api.example.com/stop"
assert snap["tunnel_local_port"] == 8288
assert snap["tunnel_remote_host"] == "10.0.0.5"
assert snap["tunnel_remote_port"] == 8199
print("_snapshot() 带回页签上改的全部值（含密码留空不覆盖）")

# 页签保存后真的落到 inst（走 _TRACK 写回循环的那一段）
for k in page._TRACK:
    if k in snap:
        w.cfg["instances"][1][k] = snap[k]
assert w.cfg["instances"][1]["ssh_host"] == "192.168.1.50"
assert w.cfg["instances"][1]["tunnel_local_port"] == 8288

# 页签截图（远程运维三分区）
page.tabs.setCurrentIndex(3)
app.processEvents()
p3 = os.path.join(OUT, "remote_ui_ops.png")
page.grab().save(p3)
print("截图:", p3)

# 滚到底再截一张：第三分区「隧道」只有滚到底才看得见（布局问题要靠它目视）
from PySide6.QtWidgets import QScrollArea        # noqa: E402
_scroll = panel.findChildren(QScrollArea)[0]
_scroll.verticalScrollBar().setValue(_scroll.verticalScrollBar().maximum())
app.processEvents()
p4 = os.path.join(OUT, "remote_ui_ops_tunnel.png")
page.grab().save(p4)
print("截图:", p4)

# 切回本地：页签消失且不留空位（QTabWidget 隐藏后 tabBar 不占位）
w.cb_inst.setCurrentIndex(_ids.index("inst_l"))
page.load_current()
app.processEvents()
assert page.tabs.isTabVisible(3) is False, "本机实例不该看到第 4 个页签"
_vis = [i for i in range(page.tabs.count()) if page.tabs.isTabVisible(i)]
assert _vis == [0, 1, 2], _vis      # 本机：实例设置 + 内核版本 + 共享外挂库
print("本机：可见页签 =", _vis, "（第 4 页已收起，不留空位）")
assert not panel.row_channel.isVisibleTo(panel), "只有一个通道时不该显示「默认通道」下拉"

# ---------------------------------------------------------------- 0.8.40 T06
# http 明文告警三种判定（D1）+ 黄条去重（D3）+ 删实例清隧道（C5）
print("=== T06：明文告警 / 隧道清理 ===")
from app import remote_ops as _ro            # noqa: E402
from app import remote_registry as _rr      # noqa: E402
from app import api_base as _ab             # noqa: E402

_inst_r = w.cfg["instances"][1]
_inst_r["base_url"] = "http://192.168.1.20:8188"
assert _ro.ops.insecure_reason(_inst_r) == "http://192.168.1.20:8188", \
    _ro.ops.insecure_reason(_inst_r)
print("① http 明文远程 → 告警：", _ro.ops.insecure_reason(_inst_r))
_inst_r["base_url"] = "https://xxx.platform.com"
assert _ro.ops.insecure_reason(_inst_r) == "", "https 不该告警"
print("② https → 不告警")

_inst_r["base_url"] = "http://192.168.1.20:8188"
_rr.registry.set_override("inst_r", "http://127.0.0.1:8189", 8189)
assert _ab.effective_base_info(_inst_r)["via_tunnel"] is True
assert _ro.ops.insecure_reason(_inst_r) == "", "隧道激活时生效基址是回环，不该告警"
print("③ 隧道激活（生效基址", _ab.api_base(_inst_r), "）→ 不告警")

# 黄条：同实例同会话只出现一次；「不再提示」永不出现；✕ 可关
w._insecure_shown.clear()
w.remote_banner.hide()
w.maybe_insecure_banner(_inst_r)          # 隧道还开着 → 不该显示
assert w.remote_banner.isHidden()
_rr.registry.clear_override("inst_r")
w.maybe_insecure_banner(_inst_r)
assert not w.remote_banner.isHidden(), "明文地址在连接成功时该弹黄条"
print("黄条文案：", w.remote_banner.lb.text())
_banner_shot = os.path.join(OUT, "remote_ui_banner.png")
w.grab().save(_banner_shot)
print("截图:", _banner_shot)
w.remote_banner._on_close()               # 点「知道了」/✕
assert w.remote_banner.isHidden()
w.maybe_insecure_banner(_inst_r)
assert w.remote_banner.isHidden(), "同实例同会话只提示一次"
print("黄条：同实例同会话只出现一次")

w._insecure_shown.clear()
_muted = dict(_inst_r)
_muted["insecure_warn_muted"] = True
w.maybe_insecure_banner(_muted)
assert w.remote_banner.isHidden(), "勾过「不再提示」的实例永不出现"
_inst_r["insecure_warn_muted"] = True
_n = w.restore_insecure_warn()
assert _n >= 1 and _inst_r["insecure_warn_muted"] is False, (_n, _inst_r)
print("「恢复明文告警」清掉", _n, "个实例的忽略标记")

# 删实例 → 隧道与运行期覆盖一起清掉（C5）
_rr.registry.set_override("inst_r", "http://127.0.0.1:8189", 8189)
assert _rr.registry.is_active("inst_r")
page._drop_remote_resources(_inst_r)
assert not _rr.registry.is_active("inst_r"), "删实例必须清注册表覆盖"
print("删实例：隧道覆盖已清空，api_base 回落 =", _ab.api_base(_inst_r))

# 复原，不影响后面的对话框断言
_inst_r["base_url"] = "https://xxx.platform.com"

# 新增实例对话框：远程模式
from app.ui.dialogs import InstanceDialog   # noqa: E402
dlg = InstanceDialog(w)
dlg.cb_kind.setCurrentIndex(1)
dlg.ed_url.setText("192.168.1.20:8188")
dlg.ed_name.setText("另一台")
dlg._accept()
data = dlg.result_data()
print("对话框返回:", {k: data[k] for k in ("kind", "name", "base_url", "comfyui_path")})
assert data["kind"] == "remote" and data["comfyui_path"] == ""
assert data["base_url"].startswith("http://"), data["base_url"]

print("=== T07：设置页「远程产物」分组 ===")
import time as _t                                    # noqa: E402
from PySide6.QtWidgets import QScrollArea           # noqa: E402
from app import api_base as _ab2                    # noqa: E402
from app import remote_cleanup as _rc               # noqa: E402

_inst_r["kind"] = "remote"
_dir_r = _ab2.remote_output_dir(_inst_r, w.cfg)
_root_r = os.path.dirname(_dir_r)
# 前面几段可能已经在这个临时 APPDATA 里留下空目录，先扫干净，保证孤儿行只有我们造的
if os.path.isdir(_root_r):
    for _n in os.listdir(_root_r):
        shutil.rmtree(os.path.join(_root_r, _n), ignore_errors=True)
os.makedirs(_dir_r, exist_ok=True)
for _i in range(6):
    with open(os.path.join(_dir_r, f"shot_{_i}.png"), "wb") as _f:
        _f.write(b"x" * 65536)
# 孤儿目录：实例已删但目录还在（PRD 边界 #19）
_orphan = os.path.join(os.path.dirname(_dir_r), "dead-box")
os.makedirs(_orphan, exist_ok=True)
with open(os.path.join(_orphan, "old.png"), "wb") as _f:
    _f.write(b"y" * 4096)
_rc.invalidate_cache()

w.stack.setCurrentWidget(w.settings_page)
sp = w.settings_page
sp.ck_rc_auto.setChecked(True)        # 让策略控件可编辑，截图信息更全
sp._refresh_remote_stats(force=True)
_t0 = _t.monotonic()
while sp._rc_busy and _t.monotonic() - _t0 < 15:
    app.processEvents()
    _t.sleep(0.02)
assert not sp._rc_busy, "统计 worker 15 秒内没回来（UI 不能等扫描）"

_names = [sp.tb_remote.item(r, 0).text() for r in range(sp.tb_remote.rowCount())]
print("表格行:", _names)
assert _names[0] == "云镜像", _names
assert sp.tb_remote.item(0, 1).text() == "6 张", sp.tb_remote.item(0, 1).text()
assert sp.tb_remote.item(0, 2).text().startswith("384"), sp.tb_remote.item(0, 2).text()
assert "（已删除的实例）dead-box" in _names, _names
assert _names[-1] == "总计", _names
_last = sp.tb_remote.rowCount() - 1
_cells = [sp.tb_remote.item(r, 1).text() for r in range(_last)]
print("文件数列:", _cells)
assert _cells == ["6 张", "1 张"], _cells
_per = sum(int(t.split()[0]) for t in _cells if "张" in t)
assert sp.tb_remote.item(_last, 1).text() == f"{_per} 张", (
    sp.tb_remote.item(_last, 1).text(), _per)
print("统计：云镜像 6 张 + 孤儿 1 张，总计行 =", sp.tb_remote.item(_last, 2).text())
assert sp.lb_rc_state.text() == "已统计", sp.lb_rc_state.text()
assert _root_r in sp.lb_rc_dir.text(), sp.lb_rc_dir.text()
# 行高必须容得下动作列里 26px 的按钮，最后一行也不能被裁掉
assert sp.tb_remote.rowHeight(0) >= 26, sp.tb_remote.rowHeight(0)
assert sp.tb_remote.viewport().height() >= sum(
    sp.tb_remote.rowHeight(r) for r in range(sp.tb_remote.rowCount())), (
    sp.tb_remote.viewport().height(), sp.tb_remote.rowCount())
print("状态行:", sp.lb_rc_state.text(), "| 灰字:", sp.lb_rc_dir.text())
print("自动清理控件：勾选=%s 策略=%s %s 天" % (
    sp.ck_rc_auto.isChecked(),
    "保留最近" if sp.rb_rc_days.isChecked() else "总量不超过",
    sp.sp_rc_days.value()))

_sa = sp.findChild(QScrollArea)
if _sa is not None:
    _sa.ensureWidgetVisible(sp.tb_remote)
    _sa.verticalScrollBar().setValue(
        max(0, _sa.verticalScrollBar().value() - 40))
app.processEvents()
_t07_shot = os.path.join(OUT, "remote_ui_settings_cleanup.png")
w.grab().save(_t07_shot)
print("截图:", _t07_shot)
_tb_shot = os.path.join(OUT, "remote_ui_settings_table.png")
sp.tb_remote.grab().save(_tb_shot)
print("截图(表格本体):", _tb_shot, "尺寸", sp.tb_remote.width(), "x", sp.tb_remote.height())

# 目录被清空后目录本身也收掉 → 下次刷新显示「尚无缓存」而不是 0 B
_rc.clean_dir(_dir_r)
sp._refresh_remote_stats(force=True)
_t0 = _t.monotonic()
while sp._rc_busy and _t.monotonic() - _t0 < 15:
    app.processEvents()
    _t.sleep(0.02)
assert sp.tb_remote.item(0, 1).text() == "尚无缓存", sp.tb_remote.item(0, 1).text()
print("清空后该行显示:", sp.tb_remote.item(0, 1).text())

print("OK 全部通过")
# 直接退出：主窗 closeEvent 可能弹「最小化到托盘」确认框（离屏无人点，会挂住），
# 内核信息线程也还在跑 git —— 测试脚本不欠它一个优雅收尾。
# 但**必须**先把 QApplication 销毁掉再退（D-09），否则退出阶段偶发 0xC0000005。
shutil.rmtree(_TMP, ignore_errors=True)
sys.stdout.flush()
clean_exit(0)
