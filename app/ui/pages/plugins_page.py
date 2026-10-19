# -*- coding: utf-8 -*-
"""插件管理页：已装列表 / 一键更新 / URL 安装 / 启禁用（对标 customnode.js）"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog,
)

from app import instance as inst_lib


class UpdateAllWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(int, int)

    def __init__(self, repos):
        super().__init__()
        self.repos = repos

    def run(self):
        ok = 0
        for name, path in self.repos:
            self.sig_log.emit(f"更新 {name} …")
            success, msg = inst_lib.git_pull_dir(path)
            self.sig_log.emit(f"  -> {'OK' if success else '失败'}: {msg[:120]}")
            if success:
                ok += 1
        self.sig_done.emit(ok, len(self.repos))


class InstallWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool, str)

    def __init__(self, url, dest):
        super().__init__()
        self.url, self.dest = url, dest

    def run(self):
        self.sig_log.emit(f"克隆 {self.url} …")
        ok, msg = inst_lib.git_clone(self.url, self.dest)
        self.sig_done.emit(ok, msg)


class PluginsPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)

        bar = QHBoxLayout()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(self.lb_inst)
        bar.addStretch()
        b_update = QPushButton("🔄 一键更新所有插件")
        b_update.setProperty("accent", True)
        b_update.clicked.connect(self._update_all)
        b_install = QPushButton("＋ 从 Git URL 安装")
        b_install.setProperty("success", True)
        b_install.clicked.connect(self._install)
        b_open = QPushButton("打开目录")
        b_open.clicked.connect(self._open_dir)
        bar.addWidget(b_update)
        bar.addWidget(b_install)
        bar.addWidget(b_open)
        root.addLayout(bar)

        self.tb = QTableWidget(0, 3)
        self.tb.setHorizontalHeaderLabels(["插件名", "Git 仓库", "状态"])
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        self.tb.doubleClicked.connect(self._toggle)
        root.addWidget(self.tb, 1)

        tip = QLabel("提示：双击行 = 启用/禁用（重命名 .disabled）；改动后重启 ComfyUI 生效。")
        tip.setStyleSheet("color:#7d8797;")
        root.addWidget(tip)
        self.refresh_list()

    def on_show(self):
        self.refresh_list()

    def on_instance_changed(self):
        self.refresh_list()

    def _log(self, m):
        self.mainwin.console_page.append_line(f"[插件] {m}")

    def refresh_list(self):
        inst = self._inst()
        if not inst:
            self.lb_inst.setText("（未选择实例）")
            self.tb.setRowCount(0)
            return
        self.lb_inst.setText(f"实例: {inst.get('name')}")
        rows = inst_lib.list_custom_nodes(inst["comfyui_path"])
        self.tb.setRowCount(len(rows))
        for i, (name, is_git, enabled) in enumerate(rows):
            self.tb.setItem(i, 0, QTableWidgetItem(name))
            self.tb.setItem(i, 1, QTableWidgetItem("是" if is_git else "否"))
            it = QTableWidgetItem("启用" if enabled else "已禁用")
            it.setForeground(Qt.green if enabled else Qt.gray)
            self.tb.setItem(i, 2, it)

    def _nodes_dir(self):
        return os.path.join(self._inst()["comfyui_path"], "custom_nodes")

    def _toggle(self, index):
        name = self.tb.item(index.row(), 0).text()
        src = os.path.join(self._nodes_dir(), name)
        dst = src[:-len(".disabled")] if name.endswith(".disabled") else src + ".disabled"
        try:
            os.rename(src, dst)
            self.refresh_list()
        except OSError as e:
            QMessageBox.warning(self, "ComfyHub", f"重命名失败（可能被占用）:\n{e}")

    def _update_all(self):
        inst = self._inst()
        if not inst:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "更新插件前请先停止 ComfyUI")
            return
        repos = [(n, os.path.join(self._nodes_dir(), n))
                 for n, g, _e in inst_lib.list_custom_nodes(inst["comfyui_path"]) if g]
        if not repos:
            QMessageBox.information(self, "ComfyHub", "没有可更新的 Git 插件")
            return
        self._worker = UpdateAllWorker(repos)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(lambda ok, t: (
            self._log(f"更新完成 {ok}/{t}"), self.refresh_list()))
        self._worker.start()

    def _install(self):
        inst = self._inst()
        if not inst:
            return
        url, ok = QInputDialog.getText(self, "从 Git URL 安装插件", "插件仓库地址:")
        if not ok or not url.strip():
            return
        url = url.strip().rstrip("/")
        name = url.split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        dest = os.path.join(self._nodes_dir(), name)
        if os.path.exists(dest):
            QMessageBox.warning(self, "ComfyHub", f"目录已存在: {dest}")
            return
        self._worker = InstallWorker(url, dest)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(lambda ok2, msg: (
            self._log(f"安装{'成功' if ok2 else '失败'}: {msg}"), self.refresh_list()))
        self._worker.start()

    def _open_dir(self):
        inst = self._inst()
        if inst:
            inst_lib.open_in_explorer(self._nodes_dir())
