# -*- coding: utf-8 -*-
"""主窗口：窄图标导航 + 全局顶栏（对标管理大师布局）"""
import os
import uuid

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem, QStackedWidget, QLabel, QMessageBox, QComboBox,
    QPushButton, QFrame,
)

from app.config import load_config, save_config
from app.proc_manager import ProcManager

# (emoji, 文字) —— 窄条导航：图标上、文字下
NAV_ITEMS = [
    ("🏠", "工作台"),
    ("🎛️", "ComfyUI"),
    ("🧩", "插件管理"),
    ("🛡️", "环境管理"),
    ("📦", "模型库"),
    ("🧬", "工作流"),
    ("🖥️", "控制台"),
    ("⚙️", "系统设置"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ComfyHub — ComfyUI 多实例管理器")
        self.resize(1180, 780)
        self.cfg = load_config()
        self.procs = ProcManager()

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 左：窄导航条 ----------
        side = QWidget()
        side.setObjectName("NavRail")
        side.setFixedWidth(88)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 10)
        sv.setSpacing(4)
        logo = QLabel("ComfyHub")
        logo.setObjectName("NavLogo")
        logo.setAlignment(Qt.AlignCenter)
        sv.addWidget(logo)

        self.nav = QListWidget()
        self.nav.setViewMode(QListWidget.IconMode)
        self.nav.setFlow(QListWidget.TopToBottom)
        self.nav.setWrapping(False)
        self.nav.setUniformItemSizes(True)
        self.nav.setSpacing(2)
        for icon, text in NAV_ITEMS:
            item = QListWidgetItem(f"{icon}\n{text}")
            item.setSizeHint(QSize(80, 56))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav)
        sv.addWidget(self.nav, 1)
        from app.config import APP_VERSION
        self.lb_ver = QLabel(f"v{APP_VERSION}")
        self.lb_ver.setStyleSheet("color:#5a636f; font-size:10px;")
        self.lb_ver.setAlignment(Qt.AlignCenter)
        sv.addWidget(self.lb_ver)
        root.addWidget(side)

        # ---------- 右：顶栏 + 内容 ----------
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        top = QFrame()
        top.setObjectName("TopBar")
        top.setFixedHeight(56)
        tb = QHBoxLayout(top)
        tb.setContentsMargins(18, 0, 18, 0)
        tb.setSpacing(10)
        self.lb_page = QLabel("工作台")
        self.lb_page.setObjectName("AppLogo")
        tb.addWidget(self.lb_page)
        tb.addStretch()

        lb_inst = QLabel("当前实例")
        lb_inst.setStyleSheet("color:#7d8797; background:transparent;")
        tb.addWidget(lb_inst)
        self.cb_inst = QComboBox()
        self.cb_inst.setMinimumWidth(190)
        self.cb_inst.setFixedHeight(30)
        self.cb_inst.currentIndexChanged.connect(self._on_inst_changed)
        tb.addWidget(self.cb_inst)
        b_setup = QPushButton("＋ 添加 ComfyUI")
        b_setup.setProperty("accent", True)
        b_setup.setFixedHeight(30)
        b_setup.clicked.connect(self._run_setup_wizard)
        tb.addWidget(b_setup)
        right.addWidget(top)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#262c36;")
        right.addWidget(line)

        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)

        # ---------- 页面 ----------
        from app.ui.pages.home_page import HomePage
        from app.ui.pages.comfyui_page import ComfyuiPage
        from app.ui.pages.plugins_page import PluginsPage
        from app.ui.pages.env_page import EnvPage
        from app.ui.pages.models_page import ModelsPage
        from app.ui.pages.workflow_page import WorkflowPage
        from app.ui.pages.console_page import ConsolePage
        from app.ui.pages.settings_page import SettingsPage

        self.home_page = HomePage(self.cfg, self)
        self.comfyui_page = ComfyuiPage(self.cfg, self)
        self.plugins_page = PluginsPage(self.cfg, self)
        self.env_page = EnvPage(self.cfg, self)
        self.models_page = ModelsPage(self.cfg, self)
        self.workflow_page = WorkflowPage(self.cfg, self)
        self.console_page = ConsolePage(self.cfg, self)
        self.settings_page = SettingsPage(self.cfg, self)

        for w in (self.home_page, self.comfyui_page, self.plugins_page,
                  self.env_page, self.models_page, self.workflow_page,
                  self.console_page, self.settings_page):
            self.stack.addWidget(w)

        self._sync_instances()
        self.nav.setCurrentRow(0)

    # ---------- 实例（全局） ----------
    def current_inst(self):
        i = self.cb_inst.currentIndex()
        return self.cfg["instances"][i] if 0 <= i < len(self.cfg["instances"]) else None

    def _sync_instances(self):
        self.cb_inst.blockSignals(True)
        self.cb_inst.clear()
        for inst in self.cfg["instances"]:
            self.cb_inst.addItem(inst.get("name", "?"), userData=inst.get("id"))
        if not self.cfg["instances"]:
            # 空态占位：删除全部实例后下拉不应残留旧名称
            self.cb_inst.addItem("（无实例，点右上角添加）", userData=None)
        self.cb_inst.blockSignals(False)
        last = self.cfg.get("last_instance_id")
        ids = [i.get("id") for i in self.cfg["instances"]]
        if last in ids:
            self.cb_inst.setCurrentIndex(ids.index(last))
        elif self.cfg["instances"]:
            self.cb_inst.setCurrentIndex(0)
        else:
            self.cb_inst.setCurrentIndex(0)

    def _on_inst_changed(self, i):
        if 0 <= i < len(self.cfg["instances"]):
            self.cfg["last_instance_id"] = self.cfg["instances"][i].get("id")
            save_config(self.cfg)
            # 广播给当前可见页
            w = self.stack.currentWidget()
            if hasattr(w, "on_instance_changed"):
                w.on_instance_changed()

    # ---------- 导航 ----------
    def _on_nav(self, row):
        self.stack.setCurrentIndex(row)
        self.lb_page.setText(NAV_ITEMS[row][1])
        w = self.stack.currentWidget()
        if hasattr(w, "on_show"):
            w.on_show()

    def goto(self, row):
        self.nav.setCurrentRow(row)

    # ---------- 兼容入口 ----------
    def refresh_sidebar(self):
        self._sync_instances()
        self.comfyui_page.load_current()

    def _add_instance(self):
        from app.ui.dialogs import InstanceDialog
        dlg = InstanceDialog(self)
        if dlg.exec() != InstanceDialog.Accepted:
            return
        data = dlg.result_data()
        data["id"] = "inst_" + uuid.uuid4().hex[:8]
        self.cfg["instances"].append(data)
        save_config(self.cfg)
        self._sync_instances()
        self.comfyui_page.load_current()
        # 广播刷新所有页（工作台/模型库/工作流等同步新实例）
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if w and hasattr(w, "on_instance_changed"):
                try:
                    w.on_instance_changed()
                except Exception:
                    pass
        # 工作台全量强刷（实例名/目录卡/启停按钮状态）
        try:
            self.home_page.on_show()
        except Exception:
            pass

    def _run_setup_wizard(self):
        from app.ui.setup_wizard import SetupWizard
        wiz = SetupWizard(self)
        if wiz.exec() != SetupWizard.Accepted or not wiz.setup_result:
            return
        res = wiz.setup_result
        data = {
            "id": "inst_" + uuid.uuid4().hex[:8],
            "name": res.get("name") or os.path.basename(res["comfyui_path"]) or "新装 ComfyUI",
            "comfyui_path": res["comfyui_path"],
            "python_path": res["python_path"],
            "port": self._next_free_port(),
            "vram_mode": "自动",
            "extra_args": "",
            "auto_open_browser": True,
        }
        self.cfg["instances"].append(data)
        save_config(self.cfg)
        self._sync_instances()
        self.comfyui_page.load_current()
        QMessageBox.information(self, "ComfyHub",
                                f"环境安装完成，已添加实例「{data['name']}」。\n"
                                f"到「ComfyUI」页点『▶ 启动』即可使用。")

    def _next_free_port(self):
        from app.process import port_in_use
        used = {int(i.get("port") or 8188) for i in self.cfg["instances"]}
        port = 8188
        while port in used or port_in_use(port):
            port += 1
        return port

    # ---------- 关闭 ----------
    def closeEvent(self, event):
        self.procs.stop_all()
        event.accept()
