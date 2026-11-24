# -*- coding: utf-8 -*-
"""「🛠 远程运维」页签（2026-12-01，0.8.40 T05）

三分区（PRD 5.1.1 / 5.2.1 / 5.3.1）：

  ① **SSH 连接**：主机 / 端口 / 用户名 / 认证方式（密码 · 密钥文件）/ 密码 /
     密钥文件 / 密钥口令 + 「测试 SSH」；启动命令、停止命令各带「恢复默认」；
     底部灰字说明「只用于远程实例的运维命令，不参与 ComfyUI 本身的 HTTP 认证」
  ② **云平台开关机（HTTP）**：模板下拉 + 套用 / 开机请求（URL·方法·请求头·请求体）
     / 关机请求（同上）+ 「发开机请求」「发关机请求」/ 灰字提示模板只是骨架
  ③ **隧道**：本地端口 / 远端主机 / 远端端口 + 建立 / 断开 / 重连 + 四态状态行

**可见性**：整个页签只在 `kind=remote` 时可见，由 `ComfyuiPage._apply_kind_visibility`
调 `setTabVisible(3, remote)` 控制；切回本机时 `setTabVisible(False)`，QTabWidget
会收回按钮占位，**不留空位**（本机实例看不到第 4 个页签）。

**表单 ↔ 实例**：`load(inst)` 灌值、`collect()` 取值。`collect()` 的键必须与
`ComfyuiPage._TRACK` 里的远程字段名**逐字一致**（ARCH §9「新增字段必须五处齐」），
否则会出现「页签里改了、保存不生效」——这是 PRD 最恨的缺陷，也是本页唯一的
硬性接线要求。

密码 / 密钥口令沿用 0.8.39 语义：**输入框留空 = 保持原值**（绝不拿空串覆盖已存的
DPAPI 密文），且界面永不回显原文。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from app import cloud_hooks
from app.config import instance_ops_defaults
from app.i18n import T, TK
from app.workers import SshTestWorker, swap_worker

# `_Row` 定义在 comfyui_page（本模块被它 import），直接顶层 import 会成环 ——
# 延迟到 _build() 里取（那时两个模块都已加载完），保持视觉与实例页完全一致。
_ROW = None


def _row(title, desc, widget, wmax=640):
    global _ROW
    if _ROW is None:
        from app.ui.pages.comfyui_page import _Row as _R
        _ROW = _R
    return _ROW(title, desc, widget, wmax)


def _tip(text: str) -> QLabel:
    """灰字提示（与实例页既有灰字同色，不引入新视觉）"""
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet("color:#888; background:transparent;")
    return lb


def _section(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("SectionLabel")
    return lb


# ---------------------------------------------------------------- SSH 失败文案
# PRD 5.1.3 的逐字文案。**翻译必须发生在填空之前**：一旦把 `root@1.2.3.4` 插进
# 句子里，整串就再也匹配不上词表（这就是 V6-2a 那 9 种文案一直是中文的原因）。
# 所以这里存带占位符的模板：`T(模板)` 查表拿到英文模板，再 `_fill()` 填空。
_SSH_FAIL_TPL = {
    "auth_password": (
        "SSH 认证失败：用户名或密码不对。\n"
        "先确认能在同一台电脑上用 `ssh {user}@{host}` 登进去。"
        "云平台给的密码有时是临时密码或需要首次重置。"),
    "auth_passphrase": (
        "SSH 认证失败：私钥口令不对。\n"
        "这把私钥设了 passphrase，口令要和当初生成时一致。"
        "忘了就重新生成一对，再把新的公钥传到远端。"),
    "auth_key": (
        "SSH 认证失败：远端不接受这把密钥。\n"
        "检查远端的 `~/.ssh/authorized_keys` 里有没有这把私钥对应的公钥"
        "（`.pub` 那份），以及权限：`~/.ssh` 700、`authorized_keys` 600。"),
    "key_missing": (
        "读不到密钥文件：`{path}`。检查路径是否还在、有没有被移动，"
        "或私钥是不是 PuTTY 的 `.ppk`（本版不支持，请用 OpenSSH 私钥）。"),
    "fingerprint": (
        "这台机器的 SSH 主机指纹和上次记录的不一样。\n"
        "可能是对方重装了系统，也可能是有人在中间冒充（中间人）。\n"
        "确认是自己重装过再点「信任新指纹」，否则先停下来核实。"),
    "sudo": "SSH 连接正常，但远端命令需要交互式 sudo 密码，ComfyHub 不做交互式提权。",
    "timeout": (
        "SSH 连不上：`{host}:{port}` 无响应（连接超时）。\n"
        "检查三件事：主机地址有没有写错；端口是不是 22"
        "（部分平台映射到别的端口）；那台机器是否已开机、"
        "安全组/防火墙是否放行。"),
}
# 通用兜底（unreachable / 未知 kind）：原文一直是「长文 + 可选详情」
_SSH_FAIL_GENERIC = (
    "SSH 连不上：`{host}:{port}` 无响应。\n"
    "检查三件事：主机地址有没有写错；端口是不是 22"
    "（部分平台映射到别的端口）；那台机器是否已开机、"
    "安全组/防火墙是否放行。\n"
    "点「测试 SSH」可单独复测，不影响实例配置。")
# unknown 且没有详情时只留一句
_SSH_FAIL_SHORT = "SSH 连不上：`{host}:{port}` 无响应。"
# 详情尾巴（异常原文，通常是英文）
_SSH_DETAIL_TAIL = "（{detail}）"


def _fill(tpl: str, **kw) -> str:
    """把 `{host}` / `{port}` / `{user}` / `{path}` / `{detail}` 填进模板

    不用 str.format：模板里是给用户看的运维文案，未来若出现一个字面 `{`，
    format 会直接抛 KeyError/IndexError；replace 不会。
    """
    out = str(tpl)
    for key, value in kw.items():
        out = out.replace("{" + key + "}", str(value))
    return out


class RemoteOpsPanel(QWidget):
    """远程运维页签内容（无实例时 `clear()`，远程实例时 `load(inst)`）"""

    # 测试 SSH 发完请求后由本页自己跑 worker（不依赖主窗装配）
    sig_ssh_done = Signal(bool, str)
    # 发开机/关机请求 → 走「远程启动」同一条链路（PRD 5.2.1：不另做一套只测接口的行为）
    sig_hook_request = Signal(str, dict)

    def __init__(self, mainwin):
        super().__init__()
        self.mainwin = mainwin
        self._orig = instance_ops_defaults()   # 加载时的值（密码留空时保持原值）
        self._iid = ""
        self._inst = {}
        self._ssh_worker = None
        self._build()
        self.clear()

    # ---------------------------------------------------------------- 构建
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self._build_ssh(v)
        self._build_cloud(v)
        self._build_tunnel(v)
        v.addStretch(1)
        # 通道下拉只在「两个通道都配好」时出现（PRD A10）：跟着相关输入实时变
        for w in (self.ed_host, self.ed_user, self.ed_cs_url):
            w.textChanged.connect(lambda _t: self._apply_channel_visibility())
        self.pt_start.textChanged.connect(
            lambda: self._apply_channel_visibility())
        # 下拉项英译登记（原因见 app/i18n.py 模块 docstring）：这几个下拉的取值
        # 全部走 currentData()（密码/密钥、ssh/cloud、模板 id、HTTP 方法），
        # 显示文本翻译不会污染落盘值。
        for cb in (self.cb_auth, self.cb_channel, self.cb_tpl,
                   self.cb_cs_method, self.cb_ce_method):
            cb.setProperty("i18n_items", True)

    # ---------------- 分区 ①：SSH 连接 ----------------
    def _build_ssh(self, v: QVBoxLayout):
        v.addWidget(_section("SSH 连接"))

        h_ssh = QHBoxLayout()
        h_ssh.setSpacing(8)
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("192.168.1.20")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(22)
        self.sp_port.setFixedWidth(90)
        self.ed_user = QLineEdit()
        self.ed_user.setPlaceholderText("root")
        self.ed_user.setFixedWidth(160)
        h_ssh.addWidget(self.ed_host, 1)
        h_ssh.addWidget(self.sp_port)
        h_ssh.addWidget(self.ed_user)
        wrap_ssh = QWidget(); wrap_ssh.setLayout(h_ssh)
        v.addWidget(_row("主机 / 端口 / 用户名",
                         "云平台给的 SSH 地址（不含用户名部分）；端口不是 22 的填平台映射的端口",
                         wrap_ssh))

        self.cb_auth = QComboBox()
        self.cb_auth.addItem("密码", "password")
        self.cb_auth.addItem("密钥文件", "key")
        self.cb_auth.currentIndexChanged.connect(lambda _i: self._apply_auth_rows())
        v.addWidget(_row("认证方式", "密码登录最省事；密钥更适合长期使用", self.cb_auth))

        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_pass.setPlaceholderText("加密保存在本机（DPAPI）")
        self.row_pass = _row("密码", "加密保存在本机（DPAPI），不会明文写进配置文件",
                             self.ed_pass)
        v.addWidget(self.row_pass)

        h_key = QHBoxLayout()
        h_key.setSpacing(8)
        self.ed_key = QLineEdit()
        self.ed_key.setPlaceholderText(r"如 C:\Users\你\.ssh\id_ed25519")
        b_key = QPushButton("浏览…")
        b_key.clicked.connect(self._pick_key)
        h_key.addWidget(self.ed_key, 1)
        h_key.addWidget(b_key)
        wrap_key = QWidget(); wrap_key.setLayout(h_key)
        self.row_key = _row(
            "密钥文件",
            "OpenSSH 私钥（id_rsa / id_ed25519），不支持 PuTTY 的 .ppk", wrap_key)
        v.addWidget(self.row_key)

        self.ed_pp = QLineEdit()
        self.ed_pp.setEchoMode(QLineEdit.Password)
        self.ed_pp.setPlaceholderText("私钥没设口令就留空")
        self.row_pp = _row("密钥口令", "私钥没设口令就留空；加密保存在本机（DPAPI）",
                           self.ed_pp)
        v.addWidget(self.row_pp)

        h_test = QHBoxLayout()
        self.b_ssh_test = QPushButton("测试 SSH")
        self.b_ssh_test.clicked.connect(self._test_ssh)
        self.lb_ssh_test = QLabel("")
        self.lb_ssh_test.setWordWrap(True)
        self.lb_ssh_test.setStyleSheet("color:#9fb0c8; background:transparent;")
        h_test.addWidget(self.b_ssh_test)
        h_test.addWidget(self.lb_ssh_test, 1)
        wrap_test = QWidget(); wrap_test.setLayout(h_test)
        v.addWidget(_row("连通性", "只复测 SSH，不改实例配置，也不影响 ComfyUI 本身",
                         wrap_test))

        h_start = QHBoxLayout()
        h_start.setSpacing(8)
        self.pt_start = QPlainTextEdit()
        # 60px ≈ 三行：命令普遍很长会折行，52px 时内部会冒竖向滚动条（目视像坏掉）
        self.pt_start.setFixedHeight(60)
        self.pt_start.setPlaceholderText("在远端执行的启动命令")
        b_rs = QPushButton("恢复默认")
        b_rs.setFixedWidth(88)
        b_rs.clicked.connect(self._reset_start_cmd)
        h_start.addWidget(self.pt_start, 1)
        h_start.addWidget(b_rs)
        wrap_start = QWidget(); wrap_start.setLayout(h_start)
        v.addWidget(_row(
            "启动命令",
            "按自己的目录和解释器改（例如 ~/ComfyUI 或 /root/ComfyUI，"
            "python main.py 或 python3 main.py）", wrap_start))

        h_stop = QHBoxLayout()
        h_stop.setSpacing(8)
        self.pt_stop = QPlainTextEdit()
        self.pt_stop.setFixedHeight(60)
        self.pt_stop.setPlaceholderText("在远端执行的停止命令")
        b_rp = QPushButton("恢复默认")
        b_rp.setFixedWidth(88)
        b_rp.clicked.connect(self._reset_stop_cmd)
        h_stop.addWidget(self.pt_stop, 1)
        h_stop.addWidget(b_rp)
        wrap_stop = QWidget(); wrap_stop.setLayout(h_stop)
        v.addWidget(_row(
            "停止命令",
            "pkill -f 按命令行匹配，会杀掉远端所有匹配的进程；"
            "远端还有别的实例时请按实际路径收窄", wrap_stop))

        v.addWidget(_tip("执行后每 2 秒探测 /system_stats，最多等 5 分钟；"
                         "命令退出码为 0 不等于启动成功，以探针就绪为准"))
        v.addWidget(_tip("只用于远程实例的运维命令，不参与 ComfyUI 本身的 HTTP 认证"))

    # ---------------- 分区 ②：云平台开关机 ----------------
    def _build_cloud(self, v: QVBoxLayout):
        v.addWidget(_section("云平台开关机（HTTP）"))
        v.addWidget(_tip("适用：只给 Web API、不给 SSH 的云镜像。"
                         "填完先点「发开机请求」试一次"))

        h_tpl = QHBoxLayout()
        h_tpl.setSpacing(8)
        self.cb_tpl = QComboBox()
        for name, tid in cloud_hooks.template_names():
            self.cb_tpl.addItem(name, tid)
        self.b_apply_tpl = QPushButton("套用")
        self.b_apply_tpl.setFixedWidth(72)
        self.b_apply_tpl.clicked.connect(self._apply_template)
        h_tpl.addWidget(self.cb_tpl, 1)
        h_tpl.addWidget(self.b_apply_tpl)
        wrap_tpl = QWidget(); wrap_tpl.setLayout(h_tpl)
        v.addWidget(_row("平台模板", "套用后请按平台 API 文档核对参数", wrap_tpl))

        self.ed_cs_url, self.cb_cs_method, self.pt_cs_headers, self.pt_cs_body, \
            self.b_cs_send = self._build_hook_group(v, "开机请求", "start")
        self.ed_ce_url, self.cb_ce_method, self.pt_ce_headers, self.pt_ce_body, \
            self.b_ce_send = self._build_hook_group(v, "关机请求", "stop")

        # 默认通道（PRD A10）：两个通道都配好时才显示，记住上次选择
        self.cb_channel = QComboBox()
        self.cb_channel.addItem("用 SSH 启动", "ssh")
        self.cb_channel.addItem("用云平台 API 启动", "cloud")
        self.row_channel = _row(
            "默认通道", "两个通道都配好时，「远程启动」默认走这里选的通道",
            self.cb_channel)
        v.addWidget(self.row_channel)

        v.addWidget(_tip(cloud_hooks.TEMPLATE_TIP))

    def _build_hook_group(self, v: QVBoxLayout, title: str, action: str):
        """建一组「URL / 方法 / 请求头 / 请求体 + 发请求」，返回五个控件"""
        v.addWidget(_section(title))
        ed_url = QLineEdit()
        ed_url.setPlaceholderText("https://<PLATFORM_HOST>/<PATH>")
        v.addWidget(_row("请求 URL", "要填完整地址，包含 http:// 或 https://", ed_url))

        h_m = QHBoxLayout()
        cb_m = QComboBox()
        for m in cloud_hooks.METHODS:
            cb_m.addItem(m, m)
        cb_m.setFixedWidth(110)
        h_m.addWidget(cb_m)
        h_m.addStretch(1)
        wrap_m = QWidget(); wrap_m.setLayout(h_m)
        v.addWidget(_row("方法", "平台文档里写的 HTTP 方法", wrap_m))

        pt_h = QPlainTextEdit()
        pt_h.setFixedHeight(56)
        pt_h.setPlaceholderText(T("一行一个 Key: Value，例如 Authorization: Bearer <TOKEN>"))
        v.addWidget(_row("请求头", "一行一个 Key: Value；空行与 # 开头的行会被忽略", pt_h))
        # D-05：诚实告知 —— 这一块是自由 JSON，Token 会明文落盘（与 auth_value
        # 的加密语义不同）。不做加密：headers 是任意结构，加密会让每次请求都多一条
        # 解密链路，收益不抵风险；提示 + 建议用可吊销的专用 Token 更实在。
        v.addWidget(_tip(
            "请求头里的密钥会明文保存在配置文件中，建议用专用、可随时吊销的 Token。"))

        pt_b = QPlainTextEdit()
        pt_b.setFixedHeight(72)
        pt_b.setPlaceholderText("JSON，可留空")
        v.addWidget(_row("请求体", "填了就必须是合法 JSON（保存/发送前会校验行号）", pt_b))

        h_send = QHBoxLayout()
        b_send = QPushButton("发开机请求" if action == "start" else "发关机请求")
        b_send.clicked.connect(lambda _c=False, a=action: self._send_hook(a))
        h_send.addWidget(b_send)
        h_send.addStretch(1)
        wrap_send = QWidget(); wrap_send.setLayout(h_send)
        v.addWidget(_row("试一次", "和「远程启动」走同一条链路：发请求 → 等就绪 → 读状态",
                         wrap_send))
        return ed_url, cb_m, pt_h, pt_b, b_send

    # ---------------- 分区 ③：隧道 ----------------
    def _build_tunnel(self, v: QVBoxLayout):
        v.addWidget(_section("隧道"))

        h_t = QHBoxLayout()
        h_t.setSpacing(8)
        self.sp_local = QSpinBox()
        self.sp_local.setRange(1024, 65535)
        self.sp_local.setValue(8189)
        self.sp_local.setFixedWidth(96)
        self.ed_rhost = QLineEdit()
        self.ed_rhost.setPlaceholderText("127.0.0.1")
        self.ed_rhost.setFixedWidth(160)
        self.sp_rport = QSpinBox()
        self.sp_rport.setRange(1, 65535)
        self.sp_rport.setValue(8188)
        self.sp_rport.setFixedWidth(96)
        h_t.addWidget(QLabel("本地端口"))
        h_t.addWidget(self.sp_local)
        h_t.addWidget(QLabel("远端"))
        h_t.addWidget(self.ed_rhost)
        h_t.addWidget(QLabel(":"))
        h_t.addWidget(self.sp_rport)
        h_t.addStretch(1)
        wrap_t = QWidget(); wrap_t.setLayout(h_t)
        v.addWidget(_row(
            "转发目标",
            "远端 ComfyUI 监听在哪，一般就是 127.0.0.1:8188；"
            "本地端口被占用会明确报错，不自动换端口", wrap_t))

        h_b = QHBoxLayout()
        h_b.setSpacing(8)
        self.b_tunnel = QPushButton("建立隧道")
        self.b_tunnel.setProperty("accent", True)
        self.b_tunnel.clicked.connect(self._tunnel_start)
        self.b_tunnel_stop = QPushButton("断开")
        self.b_tunnel_stop.clicked.connect(self._tunnel_stop)
        self.b_tunnel_stop.hide()
        self.b_tunnel_retry = QPushButton("重连")
        self.b_tunnel_retry.clicked.connect(self._tunnel_start)
        self.b_tunnel_retry.hide()
        self.lb_tunnel = QLabel("隧道已断开")
        self.lb_tunnel.setWordWrap(True)
        self.lb_tunnel.setStyleSheet("color:#9fb0c8; background:transparent;")
        h_b.addWidget(self.b_tunnel)
        h_b.addWidget(self.b_tunnel_stop)
        h_b.addWidget(self.b_tunnel_retry)
        h_b.addWidget(self.lb_tunnel, 1)
        wrap_b = QWidget(); wrap_b.setLayout(h_b)
        v.addWidget(_row("隧道状态", "SSH 连接复用上面的配置与认证，不用重复填", wrap_b))

        v.addWidget(_tip("隧道激活期间，ComfyHub 访问这个实例会自动走 127.0.0.1:<本地端口>，"
                         "已保存的访问地址不变；断开后自动恢复"))
        v.addWidget(_tip("建议：只走隧道访问时，远端启动命令的 --listen 用 127.0.0.1 更安全"))

    # ---------------------------------------------------------------- 装载
    def clear(self) -> None:
        """没有实例时置空（用默认值，密码框保持空的）"""
        self._orig = instance_ops_defaults()
        self._iid = ""
        self._inst = {}
        self._fill(self._orig, has_saved_secret=False)
        self._apply_auth_rows()
        self._apply_tunnel_ui("idle", "隧道已断开")
        self.lb_ssh_test.setText("")

    def load(self, inst: dict) -> None:
        """把实例的远程运维字段灌进表单（缺字段用默认值，绝不共享默认对象）"""
        vals = instance_ops_defaults()
        for k in vals:
            if k in (inst or {}):
                v = (inst or {}).get(k)
                vals[k] = dict(v) if isinstance(v, dict) else v
        self._orig = vals
        self._inst = dict(inst or {})
        self._iid = str(self._inst.get("id") or "")
        self._fill(vals, has_saved_secret=bool(str(vals.get("ssh_password") or "").strip()
                                               or str(vals.get("ssh_key_passphrase") or "").strip()))
        self._apply_auth_rows()
        self.refresh_tunnel()
        self.lb_ssh_test.setText("")

    def _fill(self, vals: dict, *, has_saved_secret: bool) -> None:
        self.ed_host.setText(str(vals.get("ssh_host") or ""))
        self.sp_port.setValue(int(vals.get("ssh_port") or 22))
        self.ed_user.setText(str(vals.get("ssh_user") or ""))
        auth = str(vals.get("ssh_auth") or "password").lower()
        self.cb_auth.blockSignals(True)
        self.cb_auth.setCurrentIndex(1 if auth == "key" else 0)
        self.cb_auth.blockSignals(False)
        # 密码 / 口令永不回显：留空 = 保持原值
        self.ed_pass.clear()
        self.ed_pass.setPlaceholderText(
            "已保存（留空则不修改）" if str(vals.get("ssh_password") or "").strip()
            else "加密保存在本机（DPAPI）")
        self.ed_key.setText(str(vals.get("ssh_key_path") or ""))
        self.ed_pp.clear()
        self.ed_pp.setPlaceholderText(
            "已保存（留空则不修改）" if str(vals.get("ssh_key_passphrase") or "").strip()
            else "私钥没设口令就留空")
        self.pt_start.setPlainText(str(vals.get("ssh_start_cmd") or ""))
        self.pt_stop.setPlainText(str(vals.get("ssh_stop_cmd") or ""))
        self._fill_block("start", vals.get("cloud_start"))
        self._fill_block("stop", vals.get("cloud_stop"))
        self.sp_local.setValue(int(vals.get("tunnel_local_port") or 8189))
        self.ed_rhost.setText(str(vals.get("tunnel_remote_host") or "127.0.0.1"))
        self.sp_rport.setValue(int(vals.get("tunnel_remote_port") or 8188))
        # 通道记忆（A10）：只有两个通道都配好时下拉才有意义
        ch = str(vals.get("last_ops_channel") or "ssh").lower()
        idx = self.cb_channel.findData(ch)
        self.cb_channel.blockSignals(True)
        self.cb_channel.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_channel.blockSignals(False)
        self._apply_channel_visibility()

    def _fill_block(self, action: str, block) -> None:
        b = cloud_hooks.normalize_block(block)
        ed_url, cb_m, pt_h, pt_b = self._block_widgets(action)
        ed_url.setText(b["url"])
        i = cb_m.findData(b["method"])
        cb_m.setCurrentIndex(i if i >= 0 else cb_m.findData(cloud_hooks.DEFAULT_METHOD))
        pt_h.setPlainText(b["headers"])
        pt_b.setPlainText(b["body"])

    # ---------------------------------------------------------------- 取值
    def collect(self) -> dict:
        """页签当前取值（键与 `ComfyuiPage._TRACK` 的远程字段逐字一致）

        只返回 **15 个可编辑字段**：`ssh_host_fingerprint`（由 remote_fingerprint
        首次连接时写入）与 `insecure_warn_muted`（由明文告警模态/设置页写）不在
        UI 上，不在这里回写 —— 否则会把后台刚记下的指纹用旧值覆盖掉。
        """
        pwd = self.ed_pass.text()
        pp = self.ed_pp.text()
        return {
            "ssh_host": self.ed_host.text().strip(),
            "ssh_port": int(self.sp_port.value()),
            "ssh_user": self.ed_user.text().strip(),
            "ssh_auth": str(self.cb_auth.currentData() or "password"),
            # 留空 = 保持原值（0.8.39 语义；绝不能拿空串把密文冲掉）
            "ssh_password": pwd if pwd else str(self._orig.get("ssh_password") or ""),
            "ssh_key_path": self.ed_key.text().strip(),
            "ssh_key_passphrase": pp if pp else str(
                self._orig.get("ssh_key_passphrase") or ""),
            "ssh_start_cmd": self.pt_start.toPlainText().rstrip("\n"),
            "ssh_stop_cmd": self.pt_stop.toPlainText().rstrip("\n"),
            "cloud_start": self._collect_block("start"),
            "cloud_stop": self._collect_block("stop"),
            "tunnel_local_port": int(self.sp_local.value()),
            "tunnel_remote_host": self.ed_rhost.text().strip() or "127.0.0.1",
            "tunnel_remote_port": int(self.sp_rport.value()),
            "last_ops_channel": str(self.cb_channel.currentData()
                                    or self._orig.get("last_ops_channel") or "ssh"),
        }

    def _block_widgets(self, action: str):
        if str(action) == "stop":
            return (self.ed_ce_url, self.cb_ce_method,
                    self.pt_ce_headers, self.pt_ce_body)
        return self.ed_cs_url, self.cb_cs_method, self.pt_cs_headers, self.pt_cs_body

    def _collect_block(self, action: str) -> dict:
        ed_url, cb_m, pt_h, pt_b = self._block_widgets(action)
        return {"url": ed_url.text().strip(),
                "method": str(cb_m.currentData() or cloud_hooks.DEFAULT_METHOD),
                "headers": pt_h.toPlainText().rstrip("\n"),
                "body": pt_b.toPlainText().rstrip("\n")}

    def inst_snapshot(self) -> dict:
        """当前实例 + 页签上的未保存改动（「发请求」/「建隧道」要用最新值）"""
        inst = dict(self._inst or {})
        inst.update(self.collect())
        return inst

    # ---------------------------------------------------------------- 认证行
    def _apply_auth_rows(self):
        if not hasattr(self, "row_pass"):
            return
        is_key = str(self.cb_auth.currentData() or "password") == "key"
        self.row_pass.setVisible(not is_key)
        self.row_key.setVisible(is_key)
        self.row_pp.setVisible(is_key)

    def _channels(self) -> list:
        """本表单当前可用的启停通道（与 remote_ops.available_channels 同口径）"""
        out = []
        if self.ed_host.text().strip() and self.ed_user.text().strip() \
                and self.pt_start.toPlainText().strip():
            out.append("ssh")
        if self.ed_cs_url.text().strip():
            out.append("cloud")
        return out

    def _apply_channel_visibility(self):
        """两个通道都配好时才显示「默认通道」下拉（PRD A10）"""
        if not hasattr(self, "row_channel"):
            return
        self.row_channel.setVisible(len(self._channels()) == 2)

    def _pick_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 OpenSSH 私钥", self.ed_key.text() or "",
            "所有文件 (*);;私钥 (id_rsa id_ed25519)")
        if path:
            self.ed_key.setText(path)

    def _reset_start_cmd(self):
        self.pt_start.setPlainText(
            str(instance_ops_defaults().get("ssh_start_cmd") or ""))

    def _reset_stop_cmd(self):
        self.pt_stop.setPlainText(
            str(instance_ops_defaults().get("ssh_stop_cmd") or ""))

    # ---------------------------------------------------------------- 测试 SSH
    def _test_ssh(self):
        inst = self.inst_snapshot()
        if not str(inst.get("ssh_host") or "").strip():
            QMessageBox.warning(
                self, "ComfyHub",
                T("请先填主机地址（云平台给的 SSH 地址，不含用户名部分）。"))
            return
        if not str(inst.get("ssh_user") or "").strip():
            QMessageBox.warning(self, "ComfyHub", T("请先填 SSH 用户名（如 root）。"))
            return
        self.b_ssh_test.setEnabled(False)
        self.b_ssh_test.setText(T("测试中…"))
        self.lb_ssh_test.setText(T("正在连接…"))
        w = SshTestWorker(inst)
        w.sig_done.connect(self._on_ssh_tested)
        swap_worker(self, "_ssh_worker", w)
        w.start()

    def _on_ssh_tested(self, ok: bool, kind: str):
        self.b_ssh_test.setEnabled(True)
        self.b_ssh_test.setText(T("测试 SSH"))
        w = self._ssh_worker
        detail = str(getattr(w, "detail", "") or "")
        if ok:
            self.lb_ssh_test.setText(T("✅ SSH 连接与认证都正常"))
            QMessageBox.information(self, "ComfyHub",
                                    T("SSH 连接成功：登录与认证都没问题。"))
            return
        text = self.ssh_failure_text(kind, detail)
        self.lb_ssh_test.setText(text.splitlines()[0] if text else T("连接失败"))
        QMessageBox.warning(self, "ComfyHub", text)
        self.sig_ssh_done.emit(False, kind)

    def ssh_failure_text(self, kind: str, detail: str = "") -> str:
        """`SshError.kind` → PRD 5.1.3 的逐字文案（UI 只按 kind 查，不匹配字符）

        英文模式下先翻模板再填空，见模块顶部的 `_SSH_FAIL_TPL` 注释。
        """
        host = self.ed_host.text().strip() or "<host>"
        port = int(self.sp_port.value())
        user = self.ed_user.text().strip() or "<user>"
        path = self.ed_key.text().strip() or "<path>"
        key = str(kind or "")
        if key == "unknown" and not detail:
            tpl = TK("ssh_fail.short") or _SSH_FAIL_SHORT
        else:
            tpl = _SSH_FAIL_TPL.get(key)
            if tpl is None:
                # unreachable / 未知 kind：通用长文，有详情就补一行
                tpl = TK("ssh_fail.generic") or _SSH_FAIL_GENERIC
                if detail:
                    tpl = tpl + "\n" + (TK("ssh_fail.detail")
                                        or _SSH_DETAIL_TAIL)
            else:
                tpl = TK("ssh_fail." + key) or tpl
        return _fill(tpl, host=host, port=port, user=user, path=path,
                     detail=detail)

    # ---------------------------------------------------------------- 云模板
    def _cloud_filled(self) -> bool:
        for action in ("start", "stop"):
            b = self._collect_block(action)
            if b["url"] or b["headers"].strip() or b["body"].strip():
                return True
        return False

    def _apply_template(self):
        tpl = cloud_hooks.template_of(str(self.cb_tpl.currentData() or ""))
        if not tpl:
            return
        if self._cloud_filled():
            box = QMessageBox(self)
            box.setWindowTitle("ComfyHub")
            box.setText(T("套用模板会覆盖当前已填的开机/关机请求，继续？"))
            b_ok = box.addButton(T("覆盖"), QMessageBox.AcceptRole)
            box.addButton(T("取消"), QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not b_ok:
                return
        self._fill_block("start", tpl.get("start"))
        self._fill_block("stop", tpl.get("stop"))

    def _send_hook(self, action: str):
        """发开机/关机请求：先预检（非法 JSON 不发），再走「远程启动」同一条链路"""
        key = "cloud_stop" if str(action) == "stop" else "cloud_start"
        block = self._collect_block(action)
        ok, err = cloud_hooks.precheck(block, action=action)
        if not ok:
            QMessageBox.warning(self, "ComfyHub", err)
            return
        inst = self.inst_snapshot()
        inst[key] = block
        # 与「远程启动」同一链路（PRD 5.2.1）：发请求 → 等就绪 → 读状态
        self.sig_hook_request.emit(str(action), inst)

    # ---------------------------------------------------------------- 隧道
    def _tunnel_mgr(self):
        return getattr(self.mainwin, "tunnel_manager", None)

    def refresh_tunnel(self) -> None:
        """按主窗的隧道管理器刷新状态行（读内存态，不发网络请求）"""
        from app import tunnel
        mgr = self._tunnel_mgr()
        if mgr is None or not self._iid:
            self._apply_tunnel_ui(tunnel.ST_IDLE, tunnel.TXT_DISCONNECTED)
            return
        st = mgr.state_of(self._iid) or {}
        self._apply_tunnel_ui(st.get("state") or tunnel.ST_IDLE,
                              st.get("text") or tunnel.TXT_DISCONNECTED)

    def set_tunnel_state(self, iid: str, state: str, text: str) -> None:
        """主窗收到隧道状态变化后转发进来（只认当前实例）"""
        if str(iid or "") != self._iid:
            return
        self._apply_tunnel_ui(state, text)

    def _apply_tunnel_ui(self, state: str, text: str) -> None:
        from app import tunnel
        st = str(state or tunnel.ST_IDLE)
        self.lb_tunnel.setText(str(text or tunnel.TXT_DISCONNECTED))
        busy = st in (tunnel.ST_CONNECTING, tunnel.ST_CONNECTED,
                      tunnel.ST_RECONNECTING)
        failed = st == tunnel.ST_FAILED
        self.b_tunnel.setVisible(not busy and not failed)
        self.b_tunnel_stop.setVisible(busy)
        self.b_tunnel_retry.setVisible(failed)
        # SSH 没配时不能建隧道（PRD C1），但已连接/重连中的不受影响
        ssh_ok = bool(self.ed_host.text().strip() and self.ed_user.text().strip())
        for b in (self.b_tunnel, self.b_tunnel_retry):
            b.setEnabled(ssh_ok)
            b.setToolTip("" if ssh_ok else T("先在上面填 SSH 主机与用户名"))

    def _tunnel_start(self):
        fn = getattr(self.mainwin, "start_tunnel", None)
        if fn is None:
            self.lb_tunnel.setText(T("隧道功能还没就绪：请重启 ComfyHub 后再试"))
            return
        fn(self.inst_snapshot())

    def _tunnel_stop(self):
        fn = getattr(self.mainwin, "stop_tunnel", None)
        if fn is not None:
            fn(self._iid)
            return
        self.lb_tunnel.setText(T("隧道功能还没就绪：请重启 ComfyHub 后再试"))
