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
    QFileDialog, QCheckBox, QSlider,
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
        tip_lang = QLabel("切换语言后需重启 ComfyHub 生效")
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
        root.addWidget(g_ai)

        b_save = QPushButton("保存设置")
        b_save.setProperty("accent", True)
        b_save.clicked.connect(self._save)
        root.addWidget(b_save)
        root.addStretch()

        g3 = QGroupBox("关于")
        v3 = QVBoxLayout(g3)
        about = QLabel(
            f"ComfyHub v{APP_VERSION}\n"
            "PySide6 轻量 ComfyUI 多实例管理器（46MB 便携包）\n\n"
            "Python 3.13 · PySide6 · MIT")
        about.setStyleSheet("color:#9aa4b2;")
        v3.addWidget(about)
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
            QMessageBox.information(
                self, "ComfyHub",
                "设置已保存。\n\n语言切换将在重启 ComfyHub 后生效。")
        else:
            QMessageBox.information(self, "ComfyHub", "设置已保存")
