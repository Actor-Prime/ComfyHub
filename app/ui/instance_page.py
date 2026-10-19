# -*- coding: utf-8 -*-
"""单实例页面：控制台 / 启动参数 / 插件管理 / 模型库"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton,
    QLabel, QPlainTextEdit, QFormLayout, QComboBox, QSpinBox,
    QLineEdit, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QInputDialog, QFileDialog, QGroupBox,
)

from app.config import VRAM_MODES, save_config
from app.process import ComfyProcess, port_in_use
from app import instance as inst_lib
from app.ui.tools_page import ToolsPage


# ---------------- 后台线程：批量更新插件 ----------------
class UpdateAllWorker(QThread):
    progress = Signal(str)
    done = Signal(int, int)  # 成功数, 总数

    def __init__(self, repos: list):
        super().__init__()
        self.repos = repos

    def run(self):
        ok = 0
        for name, path in self.repos:
            self.progress.emit(f"更新 {name} …")
            success, msg = inst_lib.git_pull_dir(path)
            self.progress.emit(f"  -> {'OK' if success else '失败'}: {msg[:120]}")
            if success:
                ok += 1
        self.done.emit(ok, len(self.repos))


class InstallWorker(QThread):
    progress = Signal(str)
    done = Signal(bool, str)

    def __init__(self, url: str, dest: str):
        super().__init__()
        self.url, self.dest = url, dest

    def run(self):
        self.progress.emit(f"克隆 {self.url} …")
        ok, msg = inst_lib.git_clone(self.url, self.dest)
        self.done.emit(ok, msg)


# ---------------- 主页面 ----------------
class InstancePage(QWidget):
    """一个 ComfyUI 实例的全部 UI"""

    def __init__(self, cfg: dict, inst: dict, mainwin, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.inst = inst
        self.mainwin = mainwin          # 回调主窗口保存配置
        self.proc = ComfyProcess(self)
        self._worker = None

        self._build_ui()
        self._bind_process()
        self._load_params()
        self._refresh_info()
        self._update_buttons()

    # ================= UI 构建 =================
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # --- 工具栏 ---
        bar = QHBoxLayout()
        self.btn_start = QPushButton("▶ 启动")
        self.btn_stop = QPushButton("■ 停止")
        self.btn_browser = QPushButton("🌐 打开网页")
        self.btn_dir = QPushButton("📁 打开目录")
        for b in (self.btn_start, self.btn_stop, self.btn_browser, self.btn_dir):
            bar.addWidget(b)
        bar.addStretch()
        self.lb_status = QLabel("未运行")
        self.lb_status.setStyleSheet("font-weight: bold; color: #666;")
        bar.addWidget(self.lb_status)
        lay.addLayout(bar)

        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_browser.clicked.connect(self.open_browser)
        self.btn_dir.clicked.connect(
            lambda: inst_lib.open_in_explorer(self.inst["comfyui_path"]))

        # --- 子标签页 ---
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_console_tab(), "控制台")
        self.tabs.addTab(self._build_params_tab(), "启动参数")
        self.tabs.addTab(self._build_plugins_tab(), "插件管理")
        self.tabs.addTab(self._build_models_tab(), "模型库")
        self.tabs.addTab(ToolsPage(self.inst, self._append_log), "高级工具")

    def _build_console_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        f = QFont("Consolas", 9)
        f.setStyleHint(QFont.Monospace)
        self.console.setFont(f)
        self.console.setMaximumBlockCount(5000)  # 防内存无限涨
        v.addWidget(self.console)
        hb = QHBoxLayout()
        b1 = QPushButton("清屏")
        b1.clicked.connect(self.console.clear)
        b2 = QPushButton("复制全部")
        b2.clicked.connect(self._copy_log)
        hb.addWidget(b1)
        hb.addWidget(b2)
        hb.addStretch()
        v.addLayout(hb)
        return w

    def _build_params_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.ed_name = QLineEdit(self.inst.get("name", ""))
        self.ed_path = QLineEdit(self.inst.get("comfyui_path", ""))
        self.ed_path.setReadOnly(True)
        self.ed_python = QLineEdit(self.inst.get("python_path", ""))
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        self.sp_port.setValue(int(self.inst.get("port") or 8188))
        self.cb_vram = QComboBox()
        self.cb_vram.addItems(VRAM_MODES.keys())
        self.cb_vram.setCurrentText(self.inst.get("vram_mode", "自动"))
        self.ed_extra = QLineEdit(self.inst.get("extra_args", ""))
        self.ck_browser = QCheckBox("服务就绪后自动打开浏览器")
        self.ck_browser.setChecked(bool(self.inst.get("auto_open_browser", True)))

        form.addRow("实例名称:", self.ed_name)
        form.addRow("ComfyUI 目录:", self.ed_path)
        form.addRow("Python 路径:", self.ed_python)
        form.addRow("端口:", self.sp_port)
        form.addRow("显存模式:", self.cb_vram)
        form.addRow("附加参数:", self.ed_extra)
        form.addRow("", self.ck_browser)

        save_btn = QPushButton("保存参数")
        save_btn.clicked.connect(self._save_params)
        form.addRow("", save_btn)

        # 内核信息
        info = QGroupBox("内核信息")
        iv = QVBoxLayout(info)
        self.lb_version = QLabel()
        iv.addWidget(self.lb_version)
        form.addRow("", info)
        return w

    def _build_plugins_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        hb = QHBoxLayout()
        b_refresh = QPushButton("刷新列表")
        b_refresh.clicked.connect(self._refresh_plugins)
        b_update = QPushButton("一键更新全部")
        b_update.clicked.connect(self._update_all_plugins)
        b_install = QPushButton("从 Git URL 安装…")
        b_install.clicked.connect(self._install_plugin)
        b_open = QPushButton("打开目录")
        b_open.clicked.connect(lambda: inst_lib.open_in_explorer(
            os.path.join(self.inst["comfyui_path"], "custom_nodes")))
        for b in (b_refresh, b_update, b_install, b_open):
            hb.addWidget(b)
        hb.addStretch()
        v.addLayout(hb)

        self.tb_plugins = QTableWidget(0, 3)
        self.tb_plugins.setHorizontalHeaderLabels(["插件名", "Git 仓库", "状态"])
        self.tb_plugins.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb_plugins.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb_plugins.setSelectionBehavior(QTableWidget.SelectRows)
        v.addWidget(self.tb_plugins, 1)

        tip = QLabel("提示：启用/禁用 = 重命名目录（加/去 .disabled 后缀），双击行操作；改完重启 ComfyUI 生效。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;")
        v.addWidget(tip)
        self.tb_plugins.doubleClicked.connect(self._toggle_plugin)
        return w

    def _build_models_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        hb = QHBoxLayout()
        for label, sub in (("models", "models"), ("input", "input"), ("output", "output")):
            b = QPushButton(f"打开 {label}")
            b.clicked.connect(lambda _=False, s=sub: inst_lib.open_in_explorer(
                os.path.join(self.inst["comfyui_path"], s)))
            hb.addWidget(b)
        b_refresh = QPushButton("刷新统计")
        b_refresh.clicked.connect(self._refresh_models)
        hb.addWidget(b_refresh)
        hb.addStretch()
        v.addLayout(hb)

        self.tb_models = QTableWidget(0, 3)
        self.tb_models.setHorizontalHeaderLabels(["类别", "文件数", "体积 (GB)"])
        self.tb_models.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb_models.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.tb_models, 1)
        return w

    # ================= 进程联动 =================
    def _bind_process(self):
        self.proc.log_line.connect(self._append_log)
        self.proc.state_changed.connect(self._on_state)

    def _append_log(self, line: str):
        low = line.lower()
        if "error" in low or "traceback" in low or "failed" in low:
            color = "#c0392b"
        elif "warning" in low:
            color = "#b9770e"
        elif line.startswith("[ComfyHub]"):
            color = "#1a6fb5"
        else:
            color = None
        if color:
            from PySide6.QtGui import QTextCharFormat, QColor
            cur = self.console.textCursor()
            cur.movePosition(QTextCursor.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cur.insertText(line + "\n", fmt)
        else:
            self.console.appendPlainText(line)
        self.console.moveCursor(QTextCursor.End)

    def _on_state(self, running: bool):
        self._update_buttons()
        self.lb_status.setText("● 运行中" if running else "未运行")
        self.lb_status.setStyleSheet(
            "font-weight: bold; color: %s;" % ("#1e8e3e" if running else "#666"))

    def _update_buttons(self):
        running = self.proc.is_running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    # ================= 动作 =================
    def start(self):
        self.proc.start(self.inst)

    def stop(self):
        self.proc.stop()

    def open_browser(self):
        import webbrowser
        port = int(self.inst.get("port") or 8188)
        webbrowser.open(f"http://127.0.0.1:{port}")

    def _copy_log(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.console.toPlainText())

    # ---------- 参数 ----------
    def _load_params(self):
        pass  # 控件已在 _build_params_tab 里初始化

    def _save_params(self):
        self.inst["name"] = self.ed_name.text().strip() or self.inst["name"]
        self.inst["python_path"] = self.ed_python.text().strip()
        self.inst["port"] = self.sp_port.value()
        self.inst["vram_mode"] = self.cb_vram.currentText()
        self.inst["extra_args"] = self.ed_extra.text().strip()
        self.inst["auto_open_browser"] = self.ck_browser.isChecked()
        save_config(self.cfg)
        self.mainwin.refresh_sidebar()
        QMessageBox.information(self, "ComfyHub", "参数已保存。")

    # ---------- 信息刷新 ----------
    def _refresh_info(self):
        self._refresh_plugins()
        self._refresh_models()
        ver = inst_lib.read_comfyui_version(self.inst["comfyui_path"])
        self.lb_version.setText(f"ComfyUI 版本: {ver}")

    def _refresh_plugins(self):
        rows = inst_lib.list_custom_nodes(self.inst["comfyui_path"])
        self.tb_plugins.setRowCount(len(rows))
        for i, (name, is_git, enabled) in enumerate(rows):
            self.tb_plugins.setItem(i, 0, QTableWidgetItem(name))
            self.tb_plugins.setItem(i, 1, QTableWidgetItem("是" if is_git else "否"))
            it = QTableWidgetItem("启用" if enabled else "已禁用")
            it.setForeground(Qt.green if enabled else Qt.gray)
            self.tb_plugins.setItem(i, 2, it)

    def _refresh_models(self):
        rows = inst_lib.model_stats(self.inst["comfyui_path"])
        self.tb_models.setRowCount(len(rows))
        for i, (cat, cnt, gb) in enumerate(rows):
            self.tb_models.setItem(i, 0, QTableWidgetItem(cat))
            self.tb_models.setItem(i, 1, QTableWidgetItem(str(cnt)))
            self.tb_models.setItem(i, 2, QTableWidgetItem(f"{gb:.2f}"))

    # ---------- 插件操作 ----------
    def _plugin_dir(self) -> str:
        return os.path.join(self.inst["comfyui_path"], "custom_nodes")

    def _toggle_plugin(self, index):
        name = self.tb_plugins.item(index.row(), 0).text()
        src = os.path.join(self._plugin_dir(), name)
        if name.endswith(".disabled"):
            dst = src[:-len(".disabled")]
        else:
            dst = src + ".disabled"
        try:
            os.rename(src, dst)
            self._refresh_plugins()
        except OSError as e:
            QMessageBox.warning(self, "ComfyHub", f"重命名失败（插件可能正被占用）:\n{e}")

    def _update_all_plugins(self):
        if self.proc.is_running:
            QMessageBox.warning(self, "ComfyHub", "更新插件前请先停止 ComfyUI，避免文件占用。")
            return
        repos = []
        for name, is_git, _en in inst_lib.list_custom_nodes(self.inst["comfyui_path"]):
            p = os.path.join(self._plugin_dir(), name)
            if is_git and os.path.isdir(p):
                repos.append((name, p))
        if not repos:
            QMessageBox.information(self, "ComfyHub", "没有可更新的 Git 插件。")
            return
        self._worker = UpdateAllWorker(repos)
        self._worker.progress.connect(self._append_log)
        self._worker.done.connect(
            lambda ok, total: (self._append_log(f"[ComfyHub] 插件更新完成 {ok}/{total}"),
                               self._refresh_plugins()))
        self._worker.start()

    def _install_plugin(self):
        url, ok = QInputDialog.getText(self, "从 Git URL 安装插件",
                                       "插件仓库地址:")
        if not ok or not url.strip():
            return
        url = url.strip().rstrip("/")
        name = url.split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        dest = os.path.join(self._plugin_dir(), name)
        if os.path.exists(dest):
            QMessageBox.warning(self, "ComfyHub", f"目录已存在: {dest}")
            return
        self._worker = InstallWorker(url, dest)
        self._worker.progress.connect(self._append_log)
        self._worker.done.connect(lambda ok2, msg: (
            self._append_log(f"[ComfyHub] 安装{'成功' if ok2 else '失败'}: {msg}"),
            self._refresh_plugins()))
        self._worker.start()

    # ---------- 关闭时 ----------
    def shutdown(self):
        """主窗口关闭时调用：停掉本实例进程"""
        if self.proc.is_running:
            self.proc.stop()
