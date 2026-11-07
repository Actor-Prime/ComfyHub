# -*- coding: utf-8 -*-
"""设置页：镜像源 / GitHub 代理 / 关于

2026-10-28：「高级设置」（专家模式 + 全局启动参数）按用户要求整体挪到
ComfyUI 实例设置页（comfyui_page.py 第三分区），本页只保留网络/界面/关于。
合并逻辑仍在 app/process.py build_command()（实例单独设置 > 全局）。
"""
import os

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QGroupBox, QFormLayout, QMessageBox, QScrollArea,
    QFileDialog, QCheckBox, QSlider, QSpinBox, QDialog, QPlainTextEdit,
)

from app.config import save_config, APP_VERSION
from app.bootstrap import PYPI_MIRRORS


class _AiTestWorker(QThread):
    """后台测接口：拉模型清单 + 发一条极短对话，避免同步请求卡住界面"""

    sig_done = Signal(object)

    def __init__(self, base: str, key: str, model: str, parent=None):
        super().__init__(parent)
        self._args = (base, key, model)

    def run(self):
        from app import ai_assist
        try:
            res = ai_assist.test_connection(*self._args)
        except Exception as e:      # 兜底：任何异常都变成可展示的结果
            res = {"ok": False, "models": [], "model": "", "latency_ms": 0,
                   "reply": "", "msg": "%s: %s" % (type(e).__name__, e)}
        self.sig_done.emit(res)


def _mcp_snippets() -> tuple:
    """生成两段 MCP 配置：①ComfyHub 自带（零依赖）②comfyui-mcp（需 node）
    返回 (comfyhub片段, comfyui-mcp片段, 实例端口)"""
    import sys as _sys
    import json as _json
    from app.config import load_config
    from app import ai_tools
    cfg = load_config()
    inst = ai_tools.current_instance(cfg) or {}
    port = int(inst.get("port") or 8188)
    exe = os.path.abspath(_sys.argv[0])
    a = _json.dumps({"comfyhub": {
        "command": exe, "args": ["mcp"]}}, ensure_ascii=False, indent=2)
    b = _json.dumps({"comfyui": {
        "command": "node",
        "args": ["C:/path/to/node_modules/comfyui-mcp/dist/index.js"],
        "env": {"COMFYUI_URL": f"http://127.0.0.1:{port}",
                "COMFYUI_CODE_PATH": inst.get("comfyui_path", "")}}},
        ensure_ascii=False, indent=2)
    return a, b, port


class _SubmitWorker(QThread):
    """后台上传诊断文本：网络请求绝不能跑在 UI 线程（会整个界面卡住）"""

    sig_done = Signal(bool, str)

    def __init__(self, report: str, contact: str, message: str, parent=None):
        super().__init__(parent)
        self._args = (report, contact, message)

    def run(self):
        from app.feedback import submit
        try:
            ok, msg = submit(*self._args)
        except Exception as e:            # submit 内部已兜底，这里再保一层
            ok, msg = False, f"{type(e).__name__}: {e}"
        self.sig_done.emit(ok, msg)


class FeedbackDialog(QDialog):
    """反馈与诊断（方案A，2026-11-06）：本地生成诊断文本，用户预览/编辑后自行发出。

    铁律：不主动回传——只有用户点「匿名提交」才发送；文本默认脱敏
    （见 app/feedback.py 顶部注释），保留可编辑，用户看到什么发什么。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("反馈与诊断")
        self.resize(760, 600)
        v = QVBoxLayout(self)

        # 有提交通道时才声明“点按钮会发送”，没配好就老实说不填
        from app.feedback import endpoint
        self._can_submit = bool(endpoint())
        tip = QLabel(
            "诊断文本在本机生成并已脱敏（路径与密钥已隐藏，不含机器名/IP）。\n"
            "可直接编辑；只有点「匿名提交」才会发送，其余操作都不联网。"
            if self._can_submit else
            "诊断文本在本机生成并已脱敏（路径与密钥已隐藏，不含机器名/IP）。\n"
            "可直接编辑，然后复制或保存发给开发者；不会自动上传任何内容。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#9aa4b2;")
        v.addWidget(tip)

        # ---- 用户自己的话（必填；和诊断报告分开，邮件里一眼能看懂）----
        lb_msg = QLabel("问题描述 / 建议")
        lb_msg.setStyleSheet("color:#c8d2de;")
        v.addWidget(lb_msg)
        self.ed_msg = QPlainTextEdit()
        self.ed_msg.setPlaceholderText(
            "请描述遇到的问题、期望的功能，或任何建议（至少 6 个字）…\n"
            "例如：启动实例后界面一直显示“未运行”，要手动刷新才正常。")
        self.ed_msg.setFixedHeight(96)
        v.addWidget(self.ed_msg)

        lb_diag = QLabel("诊断信息（自动生成，已脱敏，可编辑）")
        lb_diag.setStyleSheet("color:#c8d2de;")
        v.addWidget(lb_diag)

        self.txt = QPlainTextEdit()
        self.txt.setPlainText(self._gen())
        f = self.txt.font()
        f.setFamily("Consolas")
        self.txt.setFont(f)
        v.addWidget(self.txt, 1)

        # ---- 选填联系方式 + 匿名提交（地址未配置时整行隐藏，不打扰用户）----
        self.ed_contact = QLineEdit()
        self.ed_contact.setPlaceholderText("邮箱 / QQ（选填，方便回访，可留空完全匿名）")
        self.ed_contact.setMaxLength(120)
        v.addWidget(self.ed_contact)
        self.ed_contact.setVisible(self._can_submit)

        self.lb_state = QLabel("")
        self.lb_state.setStyleSheet("color:#5aa2ff;")
        v.addWidget(self.lb_state)

        row = QHBoxLayout()
        b_re = QPushButton("重新生成")
        b_re.clicked.connect(lambda: self.txt.setPlainText(self._gen()))
        row.addWidget(b_re)
        row.addStretch(1)
        self.b_submit = QPushButton("匿名提交")
        self.b_submit.setProperty("accent", True)
        self.b_submit.setToolTip("直接发送给开发者，无需登录；失败会自动保存到文件")
        self.b_submit.clicked.connect(self._submit)
        self.b_submit.setVisible(self._can_submit)
        row.addWidget(self.b_submit)
        b_copy = QPushButton("复制到剪贴板")
        b_copy.clicked.connect(self._copy)
        row.addWidget(b_copy)
        b_save = QPushButton("保存为文件…")
        b_save.clicked.connect(self._save)
        row.addWidget(b_save)
        b_issue = QPushButton("提交到 Gitee Issues")
        b_issue.clicked.connect(self._open_issue)
        row.addWidget(b_issue)
        v.addLayout(row)

    # ---------------------------------------------------------- 匿名提交
    def _submit(self):
        if not self._can_submit:
            return
        message = self.ed_msg.toPlainText().strip()[:2000]
        if len(message) < 6:
            self.lb_state.setText("请先填写问题描述或建议（至少 6 个字）")
            self.ed_msg.setFocus()
            return
        text = self.txt.toPlainText()
        # 单机防刷：频率/每日/每月/重复内容（按用户描述去重；状态只存哈希）
        from app.feedback import quota_check
        allowed, why = quota_check(text, key=message)
        if not allowed:
            self.lb_state.setText(why)
            return
        self.b_submit.setEnabled(False)
        self.b_submit.setText("提交中…")
        self._pending = (text, message)   # 成功后按这两个内容记账
        self._worker = _SubmitWorker(text, self.ed_contact.text().strip(), message, self)
        self._worker.sig_done.connect(self._on_submitted)
        self._worker.start()

    def _on_submitted(self, ok: bool, msg: str):
        self.b_submit.setEnabled(True)
        self.b_submit.setText("匿名提交")
        if ok:
            try:
                from app.feedback import quota_record
                report, message = getattr(self, "_pending", (self.txt.toPlainText(), ""))
                quota_record(report, key=message)
            except Exception:
                pass
            self.lb_state.setText("已提交，感谢反馈！开发者会尽快查看")
            return
        # 失败兜底：先自动落盘，落盘也失败就塞剪贴板，绝不让用户白写
        path = self._auto_save()
        if path:
            self.lb_state.setText(f"提交失败（{msg}），已自动保存到 {path}")
        else:
            self._copy()
            self.lb_state.setText(f"提交失败（{msg}），内容已复制到剪贴板")

    def _full_text(self) -> str:
        """导出/复制用：把用户描述放前面，诊断报告附在后面"""
        msg = self.ed_msg.toPlainText().strip()
        rep = self.txt.toPlainText()
        return f"{msg}\n\n{rep}" if msg else rep

    def _auto_save(self) -> str:
        import datetime as _dt
        try:
            d = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(d):
                d = os.path.expanduser("~")
            path = os.path.join(d, f"ComfyHub_feedback_{_dt.datetime.now():%Y%m%d_%H%M%S}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._full_text())
            return path
        except Exception:
            return ""

    @staticmethod
    def _gen() -> str:
        from app.feedback import collect
        try:
            return collect()
        except Exception as e:               # 收集失败也要给用户能发的东西
            import traceback
            from app.feedback import sanitize_text
            return (f"（诊断收集失败：{e}）\n\n"
                    + sanitize_text(traceback.format_exc()))

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._full_text())
        self.lb_state.setText("已复制到剪贴板，可直接粘贴到 Issues / 群聊 / 邮件")

    def _save(self):
        import datetime as _dt
        default = os.path.join(
            os.path.expanduser("~"), "Desktop",
            f"ComfyHub_diag_{_dt.datetime.now():%Y%m%d_%H%M%S}.txt")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存诊断文件", default, "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._full_text())
            self.lb_state.setText(f"已保存：{path}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _open_issue(self):
        self._copy()   # 先把文本塞剪贴板，用户到页面 Ctrl+V 即可
        from app.feedback import issues_url
        SettingsPage._open_url(issues_url())
        self.lb_state.setText("已复制诊断文本，请在打开的页面粘贴（Ctrl+V）并提交")


class SettingsPage(QWidget):
    sig_saved = Signal()    # 保存完成（主窗据此实时重绘主题）

    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        st = self.cfg.setdefault("settings", {})
        self._build(st)

    # ------------------------------------------------------------ UI
    def _build(self, st):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 12, 16, 12)

        t = QLabel("设置")
        t.setStyleSheet("font-size:17px; font-weight:bold; color:#fff;")
        root.addWidget(t)

        g1 = QGroupBox("网络")
        f1 = QFormLayout(g1)
        self.cb_mirror = QComboBox()
        self.cb_mirror.addItems(list(PYPI_MIRRORS.keys()))
        self.cb_mirror.setCurrentText(st.get("mirror", "阿里云"))
        f1.addRow("PyPI 镜像:", self.cb_mirror)
        self.ed_ghproxy = QLineEdit(st.get("gh_proxy", ""))
        self.ed_ghproxy.setPlaceholderText("留空直连；如 https://ghproxy.net/ （自动拼在 github URL 前）")
        f1.addRow("GitHub 代理:", self.ed_ghproxy)
        self.ed_gitproxy = QLineEdit(st.get("git_proxy", ""))
        self.ed_gitproxy.setPlaceholderText("git clone 专用代理（留空则同上）")
        f1.addRow("Git Clone 代理:", self.ed_gitproxy)
        root.addWidget(g1)

        # ---- 目录：共享工具（便携 Python/MinGit/torch）安装位置 ----
        g_dir = QGroupBox("目录")
        f_dir = QFormLayout(g_dir)
        row_tools = QHBoxLayout()
        self.ed_tools = QLineEdit(st.get("tools_dir", ""))
        self.ed_tools.setPlaceholderText("留空 = 默认（%APPDATA%\\ComfyHub\\tools）")
        row_tools.addWidget(self.ed_tools)
        b_browse = QPushButton("浏览…")
        b_browse.clicked.connect(self._pick_tools_dir)
        row_tools.addWidget(b_browse)
        b_reset = QPushButton("恢复默认")
        b_reset.clicked.connect(lambda: self.ed_tools.setText(""))
        row_tools.addWidget(b_reset)
        f_dir.addRow("工具目录:", row_tools)
        tip_dir = QLabel("便携 Python / MinGit / PyTorch 的安装位置，留空用默认。"
                         "修改后旧目录原样保留（不会自动搬迁），下次装机/修复生效。"
                         "⚠️ 自定义路径不在卸载器清理范围，删该目录即卸载工具。")
        tip_dir.setWordWrap(True)
        tip_dir.setStyleSheet("color:#888;")
        f_dir.addRow("", tip_dir)
        root.addWidget(g_dir)

        g2 = QGroupBox("界面")
        f2 = QFormLayout(g2)
        # 语言（2026-11-01 首批中英包；页面文案在构建时翻译，切换需重启）
        self.cb_lang = QComboBox()
        self.cb_lang.addItem("简体中文", "zh")
        self.cb_lang.addItem("English (Beta · 部分界面)", "en")
        _il = self.cb_lang.findData(str(st.get("lang", "zh")))
        self.cb_lang.setCurrentIndex(_il if _il >= 0 else 0)
        f2.addRow("语言:", self.cb_lang)
        tip_lang = QLabel("保存后立即生效（表格行/日志等动态文案切页后对齐）")
        tip_lang.setStyleSheet("color:#888;")
        f2.addRow("", tip_lang)

        # 主题（2026-11-01）：深色 / 浅色 / 跟随系统 / 自定义
        self.cb_theme = QComboBox()
        self.cb_theme.addItem("深色", "dark")
        self.cb_theme.addItem("浅色", "light")
        self.cb_theme.addItem("跟随系统", "system")
        self.cb_theme.addItem("自定义（半透明 + 背景图）", "custom")
        _it = self.cb_theme.findData(str(st.get("theme", "dark")))
        self.cb_theme.setCurrentIndex(_it if _it >= 0 else 0)
        f2.addRow("主题:", self.cb_theme)
        tip_theme = QLabel("「跟随系统」读取 Windows 深浅色设置，系统切换后自动跟着变")
        tip_theme.setWordWrap(True)
        tip_theme.setStyleSheet("color:#888;")
        f2.addRow("", tip_theme)

        # 自定义两行（背景图 + 面板不透明度）：紧贴「主题」下方，只有选「自定义」才出现
        # （2026-11-01 用户要求：选了半透明就该在下面直接看到这两个选项条）
        self.box_custom = QWidget()
        fc = QFormLayout(self.box_custom)
        fc.setContentsMargins(0, 4, 0, 0)
        self._bg_value = str(st.get("bg_image", "") or "")
        row_bg = QHBoxLayout()
        self.lb_bg = QLabel()
        self.lb_bg.setWordWrap(True)
        row_bg.addWidget(self.lb_bg, 1)
        btn_pick = QPushButton("选择图片…")
        btn_pick.clicked.connect(self._pick_bg)
        row_bg.addWidget(btn_pick)
        btn_clear = QPushButton("清除")
        btn_clear.clicked.connect(self._clear_bg)
        row_bg.addWidget(btn_clear)
        fc.addRow("背景图片:", row_bg)

        row_alpha = QHBoxLayout()
        self.sl_alpha = QSlider(Qt.Horizontal)
        self.sl_alpha.setRange(35, 100)          # 低于 35% 字看不清，下限夹住
        self.sl_alpha.setValue(int(st.get("bg_opacity", 72) or 72))
        self.lb_alpha = QLabel("%d%%" % self.sl_alpha.value())
        self.lb_alpha.setFixedWidth(44)
        self.sl_alpha.valueChanged.connect(
            lambda v: self.lb_alpha.setText("%d%%" % v))
        row_alpha.addWidget(self.sl_alpha, 1)
        row_alpha.addWidget(self.lb_alpha)
        fc.addRow("面板不透明度:", row_alpha)
        f2.addRow("", self.box_custom)

        # 关闭行为（2026-10-31）：与关闭询问框的「记住我的选择」共用同一设置
        self.cb_close = QComboBox()
        self.cb_close.addItem("每次询问", "ask")
        self.cb_close.addItem("直接退出程序", "close")
        self.cb_close.addItem("最小化到托盘", "minimize")
        idx_close = self.cb_close.findData(str(st.get("close_behavior", "ask")))
        self.cb_close.setCurrentIndex(idx_close if idx_close >= 0 else 0)
        f2.addRow("关闭主窗口时:", self.cb_close)
        tip_close = QLabel("「每次询问」下首次点关闭按钮会弹窗选择，"
                           "勾选『记住我的选择』后不再询问（随时可在此改回）")
        tip_close.setWordWrap(True)
        tip_close.setStyleSheet("color:#888;")
        f2.addRow("", tip_close)
        root.addWidget(g2)

        # ---- 自定义主题的显隐与初值 ----
        self._refresh_bg_label()
        self.cb_theme.currentIndexChanged.connect(self._sync_custom_visible)
        self._sync_custom_visible()

        # ---- AI 接入（可选）：确定性查不到的疑难条目才调用 ----
        g_ai = QGroupBox("AI 接入（可选）")
        f_ai = QFormLayout(g_ai)
        self.ck_ai = QCheckBox("启用 AI 接入（仅确定性查不到时调用）")
        self.ck_ai.setChecked(bool(st.get("ai_enabled", False)))
        f_ai.addRow("", self.ck_ai)
        self.ed_ai_base = QLineEdit(st.get("ai_base_url", ""))
        self.ed_ai_base.setPlaceholderText(
            "OpenAI 兼容端点，如 https://api.deepseek.com/v1")
        # 接口地址 + 「测试接口」（2026-11-01）：一次点击完成「测连通 + 拉模型清单 + 绑定模型」
        row_base = QHBoxLayout()
        row_base.addWidget(self.ed_ai_base, 1)
        self.b_ai_test = QPushButton("测试接口")
        self.b_ai_test.clicked.connect(self._test_ai)
        row_base.addWidget(self.b_ai_test)
        f_ai.addRow("接口地址:", row_base)
        self.ed_ai_key = QLineEdit(st.get("ai_api_key", ""))
        self.ed_ai_key.setEchoMode(QLineEdit.Password)
        self.ed_ai_key.setPlaceholderText("API Key（仅存本机 config.json）")
        f_ai.addRow("API Key:", self.ed_ai_key)
        # 模型名：可编辑下拉 —— 点「测试接口」后自动填入接口返回的模型清单供选择
        self.cb_ai_model = QComboBox()
        self.cb_ai_model.setEditable(True)
        _cur_model = str(st.get("ai_model", "") or "")
        if _cur_model:
            self.cb_ai_model.addItem(_cur_model)
        self.cb_ai_model.setCurrentText(_cur_model)
        if self.cb_ai_model.lineEdit():
            self.cb_ai_model.lineEdit().setPlaceholderText(
                "模型名，如 mimo-v2.6-pro / deepseek-chat")
        f_ai.addRow("模型名:", self.cb_ai_model)

        # ---- 思考强度 / 输出上限 / 上下文（2026-11-03）----
        # 参数名各家不同（thinking.type / enable_thinking / reasoning_effort），
        # 由 app/ai_models.py 按模型画像翻译，这里只给三档语义
        self.cb_ai_think = QComboBox()
        for label, val in (("关闭（最快，推荐）", "off"),
                           ("标准思考", "normal"),
                           ("深度思考", "deep")):
            self.cb_ai_think.addItem(label, val)
        _cur_think = str(st.get("ai_think", "") or "")
        _i = self.cb_ai_think.findData(_cur_think)
        self.cb_ai_think.setCurrentIndex(_i if _i >= 0 else 0)
        f_ai.addRow("思考强度:", self.cb_ai_think)

        self.sp_ai_max = QSpinBox()
        self.sp_ai_max.setRange(256, 32768)
        self.sp_ai_max.setSingleStep(256)
        self.sp_ai_max.setValue(int(st.get("ai_max_tokens") or 4000))
        self.sp_ai_max.setToolTip("单次回复的最大 token。开思考时要给足，否则正文会被思考过程挤空")
        f_ai.addRow("最大输出:", self.sp_ai_max)

        self.cb_ai_ctx = QComboBox()
        for n in (6, 12, 20, 40):
            self.cb_ai_ctx.addItem(f"最近 {n} 条消息", n)
        _cur_ctx = int(st.get("ai_ctx_msgs") or 12)
        _i = self.cb_ai_ctx.findData(_cur_ctx)
        self.cb_ai_ctx.setCurrentIndex(_i if _i >= 0 else 1)
        self.cb_ai_ctx.setToolTip("带多少历史消息进上下文。越多越慢也越贵，但多轮对话更连贯")
        f_ai.addRow("上下文:", self.cb_ai_ctx)

        # ---- 上下文压缩（2026-11-06）----
        self.cb_ai_compress = QComboBox()
        for label, val in (("关闭（只保留最近几条）", "off"),
                           ("轻度（24 条触发，保留 14 条）", "light"),
                           ("中度（16 条触发，保留 9 条，推荐）", "mid"),
                           ("激进（10 条触发，保留 5 条）", "strong")):
            self.cb_ai_compress.addItem(label, val)
        _cur_cp = str(st.get("ai_compress") or "mid")
        _ic = self.cb_ai_compress.findData(_cur_cp)
        self.cb_ai_compress.setCurrentIndex(_ic if _ic >= 0 else 2)
        self.cb_ai_compress.setToolTip(
            "对话变长时自动把较早内容压成摘要，之后只带摘要+最近几条原文；"
            "档位越高保留的原文越少、省得越多")
        f_ai.addRow("上下文压缩:", self.cb_ai_compress)

        # ---- 助手设定 / 直连（2026-11-06）----
        # 默认就填好一份可用的设定（不留空），用户想改再改，改坏了能一键恢复
        from app.ai_persona import DEFAULT_PERSONA, resolve as _resolve_persona
        self.ed_ai_persona = QPlainTextEdit()
        self.ed_ai_persona.setPlainText(_resolve_persona(str(st.get("ai_persona") or "")))
        self.ed_ai_persona.setFixedHeight(118)
        self.ed_ai_persona.setToolTip(
            "这里的内容会作为系统提示词的开头（角色与偏好）。"
            "操作纪律（不许编造路径、报错要如实转述等）始终生效，不会被这里覆盖")
        _wrap_persona = QWidget()
        _pv = QVBoxLayout(_wrap_persona)
        _pv.setContentsMargins(0, 0, 0, 0)
        _pv.setSpacing(4)
        _pv.addWidget(self.ed_ai_persona)
        _pr = QHBoxLayout()
        _pr.setContentsMargins(0, 0, 0, 0)
        _b_reset_persona = QPushButton("恢复默认")
        _b_reset_persona.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#7aa2d0;}"
            "QPushButton:hover{text-decoration:underline;}")
        _b_reset_persona.setCursor(Qt.PointingHandCursor)
        _b_reset_persona.setToolTip("把助手设定改回出厂默认内容")
        _b_reset_persona.clicked.connect(
            lambda: self.ed_ai_persona.setPlainText(DEFAULT_PERSONA))
        _pr.addStretch(1)
        _pr.addWidget(_b_reset_persona)
        _pv.addLayout(_pr)
        f_ai.addRow("助手设定:", _wrap_persona)

        self.ck_ai_direct = QCheckBox("直连（不经过系统代理）")
        self.ck_ai_direct.setChecked(bool(st.get("ai_direct", True)))
        self.ck_ai_direct.setToolTip(
            "国内接口（DeepSeek/智谱/通义/MiMo 等）建议保持勾选。"
            "如果你的电脑开着全局代理且接口在境外，取消勾选改走代理")
        f_ai.addRow("", self.ck_ai_direct)

        self.lb_ai_profile = QLabel("")
        self.lb_ai_profile.setWordWrap(True)
        self.lb_ai_profile.setStyleSheet("color:#7f8894; font-size:11px;")
        f_ai.addRow("", self.lb_ai_profile)
        self.cb_ai_model.currentTextChanged.connect(self._refresh_ai_profile)
        self._refresh_ai_profile(self.cb_ai_model.currentText())

        self.lb_ai_state = QLabel("")
        self.lb_ai_state.setWordWrap(True)
        self.lb_ai_state.setStyleSheet("color:#888;")
        f_ai.addRow("", self.lb_ai_state)
        tip_ai = QLabel(
            "用于「未识别节点 / 无直链模型」的疑难推断，结果标『🤖 待验证』"
            "需人工确认后再用。国内推荐 DeepSeek / 智谱 / 通义（均为 OpenAI 兼容"
            "接口）。不配置则完全走本地确定性识别，不影响其他功能。")
        tip_ai.setWordWrap(True)
        tip_ai.setStyleSheet("color:#888;")
        f_ai.addRow("", tip_ai)

        self.b_ai_mcp = QPushButton("外部 Agent 接入（MCP）…")
        self.b_ai_mcp.setToolTip("生成 MCP 配置片段，让 WorkBuddy / Claude 等外部 AI 客户端"
                                 "通过 MCP 协议操作 ComfyHub 管理的实例")
        self.b_ai_mcp.clicked.connect(self._open_mcp_dialog)
        f_ai.addRow("", self.b_ai_mcp)
        root.addWidget(g_ai)

        # 保存按钮不再放在滚动内容里（2026-11-06 用户要求）：
        # 页面很长，滚到中间就看不到按钮了；改为页面底部常驻栏（见 _build 末尾）
        root.addStretch()
        g3 = QGroupBox("关于")
        v3 = QVBoxLayout(g3)
        # 版本号必须单独成一个控件：翻译是「控件树扫描 + 词表精确匹配」，
        # 版本号并进同一段文本会让键每次都变，英文模式就只能退回中文
        # （v0.8.07 实测踩到：关于框文案升版本后不再翻译）
        lb_ver = QLabel(f"ComfyHub v{APP_VERSION}")
        lb_ver.setStyleSheet("color:#9aa4b2;")
        v3.addWidget(lb_ver)
        lb_desc = QLabel("PySide6 轻量 ComfyUI 多实例管理器（46MB 便携包）\n\n"
                         "Python 3.13 · PySide6 · MIT")
        lb_desc.setStyleSheet("color:#9aa4b2;")
        v3.addWidget(lb_desc)

        # ---- 仓库超链接（2026-11-05 用户要求把 Gitee / GitHub 放上来）----
        # 用扁平按钮而不是 QLabel 富文本 <a>：i18n 是「控件树扫描 + 词表精确
        # 匹配」且会 setText(text())，富文本 HTML 会把整段当 key 永不命中；
        # 按钮文本是纯词条，英文模式正常替换。
        from app import updater as _upd
        row_links = QHBoxLayout()
        row_links.setSpacing(14)
        for _txt, _url in (
                ("Gitee 仓库", f"https://gitee.com/{_upd.OWNER}/{_upd.REPO}"),
                ("GitHub 仓库", f"https://github.com/{_upd.GITHUB_SLUG}")):
            _b = QPushButton(_txt)
            _b.setFlat(True)
            _b.setCursor(Qt.PointingHandCursor)
            _b.setToolTip(_url)
            _b.setStyleSheet(
                "QPushButton{border:none;background:transparent;color:#5aa2ff;"
                "text-align:left;padding:0;background-color:transparent;}"
                "QPushButton:hover{color:#8cc0ff;text-decoration:underline;}")
            _b.clicked.connect(lambda _=False, u=_url: self._open_url(u))
            row_links.addWidget(_b)
        row_links.addStretch(1)
        v3.addLayout(row_links)

        # ---- 反馈与诊断（2026-11-06 方案A：本地收集+脱敏，零网络回传）----
        b_fb = QPushButton("反馈与诊断…")
        b_fb.clicked.connect(self._open_feedback)
        row_fb = QHBoxLayout()
        row_fb.addWidget(b_fb)
        row_fb.addStretch(1)
        v3.addLayout(row_fb)

        # ---- 版本更新（2026-11-04）----
        row_up = QHBoxLayout()
        self.b_check_upd = QPushButton("检查更新")
        self.b_check_upd.clicked.connect(self._check_updates)
        row_up.addWidget(self.b_check_upd)
        self.ck_upd_auto = QCheckBox("启动时自动检查")
        self.ck_upd_auto.setChecked(bool(st.get("update_auto_check", True)))
        self.ck_upd_auto.toggled.connect(self._save_update_prefs)
        row_up.addWidget(self.ck_upd_auto)
        self.b_upd_skip = QPushButton("清除「跳过版本」")
        self.b_upd_skip.setVisible(bool(st.get("update_skipped_version")))
        self.b_upd_skip.clicked.connect(self._clear_skipped_version)
        row_up.addWidget(self.b_upd_skip)
        row_up.addStretch(1)
        v3.addLayout(row_up)

        self.lb_upd_state = QLabel("")
        self.lb_upd_state.setWordWrap(True)
        self.lb_upd_state.setStyleSheet("color:#7f8894; font-size:11px;")
        v3.addWidget(self.lb_upd_state)
        self._refresh_update_state()
        root.addWidget(g3)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- 底部常驻栏（不随滚动走，和 ComfyUI 实例页一致）----
        bar = QHBoxLayout()
        bar.setContentsMargins(18, 4, 18, 12)
        self.lb_saved = QLabel("")
        self.lb_saved.setStyleSheet("color:#6f7885; font-size:11px;")
        bar.addWidget(self.lb_saved)
        bar.addStretch(1)
        self.b_save = QPushButton("保存设置")
        self.b_save.setObjectName("SaveBtn")
        self.b_save.setCursor(Qt.PointingHandCursor)
        self.b_save.setToolTip("保存本页所有设置（快捷键 Ctrl+S 也可）")
        self.b_save.clicked.connect(self._save)
        bar.addWidget(self.b_save)
        outer.addLayout(bar)

        # Ctrl+S 也能保存（tooltip 里承诺了就得有）
        try:
            from PySide6.QtGui import QKeySequence, QShortcut
            self._sc_save = QShortcut(QKeySequence("Ctrl+S"), self)
            self._sc_save.activated.connect(self._save)
        except Exception:
            pass

    # ------------------------------------------------------------ 打开链接
    @staticmethod
    def _open_url(url: str):
        """用系统默认浏览器打开仓库地址"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    # ------------------------------------------------------------ 反馈与诊断
    def _open_feedback(self):
        FeedbackDialog(self).exec()

    # ------------------------------------------------------------ 保存
    def _pick_tools_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择工具目录", self.ed_tools.text().strip() or "")
        if d:
            self.ed_tools.setText(d)

    # ---------------- 自定义主题辅助 ----------------
    def _sync_custom_visible(self):
        """只有选中「自定义」才显示底图/透明度两行，避免误导"""
        is_custom = (self.cb_theme.currentData() or "dark") == "custom"
        self.box_custom.setVisible(is_custom)

    def _refresh_bg_label(self):
        if self._bg_value and os.path.isfile(self._bg_value):
            self.lb_bg.setText(os.path.basename(self._bg_value))
        elif self._bg_value:
            self.lb_bg.setText(self._bg_value + "（文件不存在）")
        else:
            self.lb_bg.setText("（未选择，使用纯深色底）")

    def _pick_bg(self):
        """选图并复制到 %APPDATA%\\ComfyHub\\backgrounds，避免原图被移走/删除后开天窗"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        import shutil
        import time
        from app.paths import data_root
        dst_dir = os.path.join(data_root(), "backgrounds")
        try:
            os.makedirs(dst_dir, exist_ok=True)
            ext = os.path.splitext(path)[1].lower() or ".png"
            dst = os.path.join(dst_dir, "bg_%d%s" % (int(time.time()), ext))
            shutil.copy2(path, dst)
        except Exception as e:
            QMessageBox.warning(self, "ComfyHub", "复制图片失败：%s" % e)
            return
        self._bg_value = dst
        self._refresh_bg_label()

    def _clear_bg(self):
        self._bg_value = ""
        self._refresh_bg_label()

    # ------------------------------------------------------------ 显示同步
    def on_show(self):
        """每次切到本页时，把「外部可能被改过的设置」同步回控件。

        场景（2026-11-01 用户反馈）：在关闭询问框勾了「记住我的选择」→ 会把
        settings.close_behavior 直接写进配置，但本页下拉是启动时读的，不刷新就会显示旧值。
        只同步这一个字段：其他字段没有外部写入方，全量重载反而会丢掉用户未保存的编辑。
        """
        st = self.cfg.setdefault("settings", {})
        idx = self.cb_close.findData(str(st.get("close_behavior", "ask")))
        if idx >= 0 and idx != self.cb_close.currentIndex():
            self.cb_close.blockSignals(True)
            self.cb_close.setCurrentIndex(idx)
            self.cb_close.blockSignals(False)

    # ---------------- AI 接口测试 ----------------
    # ---------- 版本更新（2026-11-04）----------
    def _refresh_update_state(self):
        from app import updater
        st = self.cfg.get("settings") or {}
        parts = ["更新源：Gitee（主）→ GitHub（备用）",
                 "运行形态：" + ("便携版" if updater.is_portable() else "安装版")]
        ts = st.get("update_last_check")
        if ts:
            import time as _t
            parts.append("上次检查：" + _t.strftime("%m-%d %H:%M", _t.localtime(float(ts))))
        skipped = st.get("update_skipped_version")
        if skipped:
            parts.append(f"已跳过版本：{skipped}")
        self.lb_upd_state.setText("　·　".join(parts))

    def _save_update_prefs(self, *_):
        st = self.cfg.setdefault("settings", {})
        st["update_auto_check"] = bool(self.ck_upd_auto.isChecked())
        save_config(self.cfg)

    def _clear_skipped_version(self):
        st = self.cfg.setdefault("settings", {})
        st.pop("update_skipped_version", None)
        save_config(self.cfg)
        self.b_upd_skip.setVisible(False)
        self._refresh_update_state()
        QMessageBox.information(self, "检查更新", "已清除「跳过版本」，下次检查会重新提示。")

    def _check_updates(self):
        """转到主窗的统一检查入口（避免两套逻辑）"""
        win = self.mainwin
        if hasattr(win, "check_updates"):
            win.check_updates(manual=True)
            self._refresh_update_state()

    def _refresh_ai_profile(self, model_text: str = ""):
        """按模型名/域名识别画像，给用户一句人话说明这套参数会怎么生效"""
        try:
            from app import ai_models
            prof = ai_models.detect((model_text or "").strip(),
                                    self.ed_ai_base.text().strip())
            think = str(self.cb_ai_think.currentData() or "off")
            eff_max = ai_models.max_tokens_for(prof, think, self.sp_ai_max.value())
            self.lb_ai_profile.setText(
                "模型画像：" + ai_models.describe(prof) +
                f"　·　当前生效：思考={think}，输出上限={eff_max}")
        except Exception:
            self.lb_ai_profile.setText("")

    def _test_ai(self):
        """测连通 + 拉模型清单 + 把选中模型绑定到下拉（2026-11-01）"""
        base = self.ed_ai_base.text().strip()
        key = self.ed_ai_key.text().strip()
        if not base or not key:
            QMessageBox.warning(self, "ComfyHub", "请先填写接口地址与 API Key。")
            return
        if getattr(self, "_ai_worker", None) and self._ai_worker.isRunning():
            return
        self.b_ai_test.setEnabled(False)
        self.lb_ai_state.setStyleSheet("color:#888;")
        self.lb_ai_state.setText("正在连接…")
        self._ai_worker = _AiTestWorker(base, key,
                                        self.cb_ai_model.currentText().strip(), self)
        self._ai_worker.sig_done.connect(self._on_ai_tested)
        self._ai_worker.start()

    def _on_ai_tested(self, res):
        self.b_ai_test.setEnabled(True)
        models = res.get("models") or []
        if models:
            self.cb_ai_model.blockSignals(True)
            self.cb_ai_model.clear()
            self.cb_ai_model.addItems(models)
            pick = res.get("model") or models[0]
            self.cb_ai_model.setCurrentText(pick)
            self.cb_ai_model.blockSignals(False)
        if res.get("ok"):
            self.lb_ai_state.setStyleSheet("color:#4cd47c;")
            self.lb_ai_state.setText(
                "%s　当前模型：%s　回复：%s" % (res.get("msg", ""),
                                            res.get("model", ""),
                                            res.get("reply", "") or "（空）"))
        else:
            self.lb_ai_state.setStyleSheet("color:#e8635a;")
            self.lb_ai_state.setText(res.get("msg") or "连接失败")

    def _open_mcp_dialog(self):
        """方案 B：外部 Agent 接入配置片段（2026-11-03）"""
        from PySide6.QtWidgets import (QDialog, QPlainTextEdit, QGroupBox,
                                       QApplication)
        try:
            a, b, port = _mcp_snippets()
        except Exception as e:
            QMessageBox.warning(self, "生成配置失败", f"{type(e).__name__}: {e}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("外部 Agent 接入（MCP）")
        v = QVBoxLayout(dlg)
        lb1 = QLabel(
            f"方式一：ComfyHub 自带 MCP 服务（推荐，零依赖）——\n"
            f"把下面片段加进 AI 客户端的 mcp.json（当前实例端口 {port}）：")
        lb1.setWordWrap(True)
        v.addWidget(lb1)
        ed1 = QPlainTextEdit(a)
        ed1.setReadOnly(True)
        ed1.setFixedHeight(96)
        v.addWidget(ed1)
        b1 = QPushButton("复制方式一")
        b1.clicked.connect(lambda: (QApplication.clipboard().setText(a),
                                    b1.setText("已复制")))
        v.addWidget(b1)
        lb2 = QLabel(
            "方式二：comfyui-mcp（需已装 node 与 comfyui-mcp 包）——\n"
            "把路径改成实际安装位置后加入 mcp.json：")
        lb2.setWordWrap(True)
        v.addWidget(lb2)
        ed2 = QPlainTextEdit(b)
        ed2.setReadOnly(True)
        ed2.setFixedHeight(120)
        v.addWidget(ed2)
        b2 = QPushButton("复制方式二")
        b2.clicked.connect(lambda: (QApplication.clipboard().setText(b),
                                    b2.setText("已复制")))
        v.addWidget(b2)
        tip = QLabel("两种方式二选一即可。配置写好后到 AI 客户端里重新加载/信任连接器才生效。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#888;")
        v.addWidget(tip)
        dlg.resize(560, 420)
        dlg.exec()

    # 保存前改动确认用的字段名（2026-11-06）
    _SAVE_LABELS = {
        "mirror": "PyPI 镜像", "gh_proxy": "GitHub 代理", "git_proxy": "Git Clone 代理",
        "tools_dir": "共享工具目录", "close_behavior": "关闭按钮行为",
        "ai_enabled": "AI 接入", "ai_base_url": "接口地址", "ai_api_key": "API Key",
        "ai_model": "模型名", "ai_think": "思考强度", "ai_max_tokens": "最大输出",
        "ai_ctx_msgs": "上下文条数", "ai_compress": "上下文压缩", "ai_persona": "助手设定",
        "ai_direct": "AI 直连", "theme": "主题", "bg_image": "背景图",
        "bg_opacity": "面板不透明度", "lang": "界面语言",
    }

    def _save(self):
        from app.ui.changes_dialog import diff_changes, confirm_changes
        # 工具目录校验：非空必须是绝对路径（防手滑相对路径落到诡异位置）
        new_tools = self.ed_tools.text().strip().rstrip("\\/")
        if new_tools and not os.path.isabs(new_tools):
            QMessageBox.warning(self, "ComfyHub",
                                "工具目录必须是绝对路径（如 F:\\ComfyHub_tools），"
                                "或留空使用默认。")
            return
        old_tools = str(self.cfg["settings"].get("tools_dir", "")).strip()
        st = self.cfg["settings"]
        old = dict(st)
        # 先收集到 pending，确认后才真正写入 st —— 取消则一个字节都不动
        pending = {
            "mirror": self.cb_mirror.currentText(),
            "gh_proxy": self.ed_ghproxy.text().strip(),
            "git_proxy": self.ed_gitproxy.text().strip(),
            "tools_dir": new_tools,
            "close_behavior": self.cb_close.currentData() or "ask",
            "ai_enabled": self.ck_ai.isChecked(),
            "ai_base_url": self.ed_ai_base.text().strip(),
            "ai_api_key": self.ed_ai_key.text().strip(),
            "ai_model": self.cb_ai_model.currentText().strip() or "deepseek-chat",
            "ai_think": str(self.cb_ai_think.currentData() or "off"),
            "ai_max_tokens": int(self.sp_ai_max.value()),
            "ai_ctx_msgs": int(self.cb_ai_ctx.currentData() or 12),
            "ai_compress": str(self.cb_ai_compress.currentData() or "mid"),
            "ai_persona": self.ed_ai_persona.toPlainText().strip()[:4000],
            "ai_direct": bool(self.ck_ai_direct.isChecked()),
            "theme": self.cb_theme.currentData() or "dark",
            "bg_image": self._bg_value,
            "bg_opacity": int(self.sl_alpha.value()),
            "lang": self.cb_lang.currentData() or "zh",
        }
        changes = diff_changes(old, pending, self._SAVE_LABELS,
                               secret_keys=("ai_api_key",),
                               long_keys=("ai_persona",))
        if not changes:
            self.lb_saved.setText("没有改动，无需保存")
            return
        # 用户要求（2026-11-06）：保存前列出改了什么，防手滑改错配置后无感落盘
        if not confirm_changes(self, "系统设置", changes, "保存设置"):
            self.lb_saved.setText("已取消，未保存任何修改")
            return
        st.update(pending)
        old_lang = str(old.get("lang", "zh"))
        save_config(self.cfg)
        self.sig_saved.emit()       # 主窗接到后重设 QSS + 背景层
        try:                        # 底栏给个即时反馈，省掉一次弹窗打扰
            import datetime as _dt
            self.lb_saved.setText(f"已保存 · {_dt.datetime.now():%H:%M:%S}")
        except Exception:
            pass
        # 变更提示：旧目录原样保留（不自动搬迁 GB 级工具链）
        if new_tools != old_tools and old_tools and os.path.isdir(old_tools):
            QMessageBox.information(
                self, "ComfyHub",
                f"设置已保存。\n\n注意：旧工具目录保留在：\n{old_tools}\n\n"
                "新路径于下次装机/修复生效；确认无误后可手动删除旧目录。")
            return
        if st.get("lang", "zh") != old_lang:
            # 2026-11-02：改为**即时生效**。运行时翻译引擎（词表 + 控件树扫描）
            # 本来就支持双向切换（见 app/i18n.py 的 _EN_REV），原来却让人重启一次，
            # 用户会以为"切了没反应"。保存后立刻扫一遍整窗，顺带清掉旧语言的残留。
            from app import i18n as _i18n
            win = self.window()
            n = _i18n.apply_language(win, st["lang"])
            QMessageBox.information(
                self, "ComfyHub",
                f"设置已保存。\n\n界面语言已切换（替换 {n} 处文案）。\n"
                "少数动态文案（表格行、运行日志）切换页面后会对齐。")
