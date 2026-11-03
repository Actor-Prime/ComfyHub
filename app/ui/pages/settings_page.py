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
    QFileDialog, QCheckBox, QSlider, QSpinBox,
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

        b_save = QPushButton("保存设置")
        b_save.setProperty("accent", True)
        b_save.clicked.connect(self._save)
        root.addWidget(b_save)
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
        outer.addWidget(scroll)

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

    def _save(self):
        # 工具目录校验：非空必须是绝对路径（防手滑相对路径落到诡异位置）
        new_tools = self.ed_tools.text().strip().rstrip("\\/")
        if new_tools and not os.path.isabs(new_tools):
            QMessageBox.warning(self, "ComfyHub",
                                "工具目录必须是绝对路径（如 F:\\ComfyHub_tools），"
                                "或留空使用默认。")
            return
        old_tools = str(self.cfg["settings"].get("tools_dir", "")).strip()
        st = self.cfg["settings"]
        st["mirror"] = self.cb_mirror.currentText()
        st["gh_proxy"] = self.ed_ghproxy.text().strip()
        st["git_proxy"] = self.ed_gitproxy.text().strip()
        st["tools_dir"] = new_tools
        st["close_behavior"] = self.cb_close.currentData() or "ask"
        st["ai_enabled"] = self.ck_ai.isChecked()
        st["ai_base_url"] = self.ed_ai_base.text().strip()
        st["ai_api_key"] = self.ed_ai_key.text().strip()
        st["ai_model"] = self.cb_ai_model.currentText().strip() or "deepseek-chat"
        st["ai_think"] = str(self.cb_ai_think.currentData() or "off")
        st["ai_max_tokens"] = int(self.sp_ai_max.value())
        st["ai_ctx_msgs"] = int(self.cb_ai_ctx.currentData() or 12)
        # 主题与语言（2026-11-01）：主题即时重绘，语言需重启
        old_lang = str(st.get("lang", "zh"))
        st["theme"] = self.cb_theme.currentData() or "dark"
        st["bg_image"] = self._bg_value
        st["bg_opacity"] = int(self.sl_alpha.value())
        st["lang"] = self.cb_lang.currentData() or "zh"
        save_config(self.cfg)
        self.sig_saved.emit()       # 主窗接到后重设 QSS + 背景层
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
        else:
            QMessageBox.information(self, "ComfyHub", "设置已保存")
