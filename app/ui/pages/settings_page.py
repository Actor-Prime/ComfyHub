# -*- coding: utf-8 -*-
"""设置页：镜像源 / GitHub 代理 / 关于

2026-10-28：「高级设置」（专家模式 + 全局启动参数）按用户要求整体挪到
ComfyUI 实例设置页（comfyui_page.py 第三分区），本页只保留网络/界面/关于。
合并逻辑仍在 app/process.py build_command()（实例单独设置 > 全局）。
"""
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QGroupBox, QFormLayout, QMessageBox, QScrollArea,
    QFileDialog,
)

from app.config import save_config, APP_VERSION
from app.bootstrap import PYPI_MIRRORS


class SettingsPage(QWidget):
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
        self.cb_lang = QComboBox()
        self.cb_lang.addItems(["简体中文"])
        self.cb_lang.setEnabled(False)   # v0.5 接入 .mo 翻译后开放
        f2.addRow("语言:", self.cb_lang)
        self.cb_theme = QComboBox()
        self.cb_theme.addItems(["深色"])
        self.cb_theme.setEnabled(False)  # v0.5 加浅色主题后开放
        f2.addRow("主题:", self.cb_theme)
        root.addWidget(g2)

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
        save_config(self.cfg)
        # 变更提示：旧目录原样保留（不自动搬迁 GB 级工具链）
        if new_tools != old_tools and old_tools and os.path.isdir(old_tools):
            QMessageBox.information(
                self, "ComfyHub",
                f"设置已保存。\n\n注意：旧工具目录保留在：\n{old_tools}\n\n"
                "新路径于下次装机/修复生效；确认无误后可手动删除旧目录。")
            return
        QMessageBox.information(self, "ComfyHub", "设置已保存")
