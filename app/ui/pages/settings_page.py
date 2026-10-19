# -*- coding: utf-8 -*-
"""设置页：镜像源 / GitHub 代理 / 关于（对标 settings.js）"""
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QGroupBox, QFormLayout, QMessageBox,
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

    def _build(self, st):
        root = QVBoxLayout(self)
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
            "PySide6 轻量 ComfyUI 多实例管理器（46MB 便携包）\n"
            "功能对标 ComfyUI管理大师 / TE启动器\n\n"
            "Python 3.13 · PySide6 · MIT")
        about.setStyleSheet("color:#9aa4b2;")
        v3.addWidget(about)
        root.addWidget(g3)

    def _save(self):
        st = self.cfg["settings"]
        st["mirror"] = self.cb_mirror.currentText()
        st["gh_proxy"] = self.ed_ghproxy.text().strip()
        st["git_proxy"] = self.ed_gitproxy.text().strip()
        save_config(self.cfg)
        QMessageBox.information(self, "ComfyHub", "设置已保存")
