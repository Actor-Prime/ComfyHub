# -*- coding: utf-8 -*-
"""主窗口：窄图标导航 + 全局顶栏（对标管理大师布局）"""
import os
import uuid

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from app.ui.nav import NAV
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem, QStackedWidget, QLabel, QMessageBox, QComboBox,
    QPushButton, QFrame,
)

from app.config import load_config, save_config
from app.proc_manager import ProcManager

# (emoji, 文字) —— 窄条导航：图标上、文字下
# 上溯三层：开发环境 = 项目根/app/ui -> 项目根；打包后 = _internal/app/ui -> _internal
# （assets 在 _internal/assets，spec datas=('F:/ComfyHub/assets', 'assets')）
# 只上溯两层会在打包后指向 _internal/app/assets → 托盘图标加载失败（2026-10-26 修复）
ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets")

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
        logo_box = QWidget()
        lv = QVBoxLayout(logo_box)
        lv.setContentsMargins(0, 8, 0, 4)
        lv.setSpacing(2)
        icon_label = QLabel()
        # 侧边栏 logo 用 logo.png（256×256）——QPixmap 直接载 icon.ico 只会拿到
        # 16×16 最小帧，放大到 40×40 会发糊（2026-10-30 用户反馈）；ico 留给
        # 窗口/托盘图标（QIcon 会按尺寸挑最合适的帧，不受影响）
        icon_pixmap = QPixmap(os.path.join(ASSETS_DIR, "logo.png"))
        if icon_pixmap.isNull():
            icon_pixmap = QPixmap(os.path.join(ASSETS_DIR, "icon.ico"))
        if not icon_pixmap.isNull():
            # 按 devicePixelRatio 渲染，高 DPI 缩放下保持清晰
            dpr = self.devicePixelRatioF() or 1.0
            pm = icon_pixmap.scaled(int(40 * dpr), int(40 * dpr),
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            icon_label.setPixmap(pm)
        icon_label.setAlignment(Qt.AlignCenter)
        lv.addWidget(icon_label)
        logo = QLabel("ComfyHub")
        logo.setObjectName("NavLogo")
        logo.setAlignment(Qt.AlignCenter)
        lv.addWidget(logo)
        logo_box.setStyleSheet("background: transparent;")
        sv.addWidget(logo_box)

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

        # 表格行高统一 32px：QSS item 有 4px padding，默认行高会把字上下裁掉
        # （2026-10-27 插件页报障「字体显示不全」）；扫全部页面，新表自动覆盖
        from app.ui.theme import fit_all_tables
        fit_all_tables(self)

        self._init_tray()
        self._init_task_watch()

        self._sync_instances()
        self.nav.setCurrentRow(0)
        self._init_orphan_check()

    # ---------- 系统托盘 ----------
    def _init_tray(self):
        """最小化到任务栏托盘：托盘图标 + 显示/退出菜单"""
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        # 双保险：ico 优先（Qt 自带 qico 插件），png 兜底
        ico_path = os.path.join(ASSETS_DIR, "icon.ico")
        png_path = os.path.join(ASSETS_DIR, "logo.png")
        icon = QIcon(ico_path)
        if icon.isNull():
            icon = QIcon(png_path)
        if icon.isNull():
            from app.log import log_exc
            log_exc(f"tray icon load failed: {ico_path} exists={os.path.isfile(ico_path)}, "
                    f"{png_path} exists={os.path.isfile(png_path)}")
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("ComfyHub — ComfyUI 多实例管理器")
        menu = QMenu(self)
        act_show = menu.addAction("显示主窗口")
        act_show.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        act_quit = menu.addAction("退出 ComfyHub")
        act_quit.triggered.connect(self.close)   # 走 closeEvent（含实例运行确认）
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # ==================== 任务完成通知 ====================
    def _init_task_watch(self):
        """统一管理：每 5 秒对齐一次"运行中实例"与"监听器"，其他页面零改动"""
        from PySide6.QtCore import QTimer
        self._watchers = {}
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(5000)
        self._watch_timer.timeout.connect(self._sync_watchers)
        self._watch_timer.start()

    def _sync_watchers(self):
        from app.log import log_exc
        try:
            insts = self.cfg.get("instances") or []
            for inst in insts:
                iid = inst.get("id")
                running = self.procs.is_running(inst)
                has = iid in self._watchers
                want = running and bool(inst.get("notify_on_task_done", True))
                if want and not has:
                    self._ensure_watcher(inst)
                elif not want and has:
                    self._drop_watcher(iid)
        except Exception as e:
            log_exc(f"_sync_watchers: {e}")

    def _ensure_watcher(self, inst):
        from app.task_watch import TaskWatcher
        try:
            w = TaskWatcher(inst.get("name") or "实例", int(inst.get("port") or 8188), self)
            w.sig_task_done.connect(self._on_task_done)
            w.sig_task_error.connect(self._on_task_error)
            w.start()
            self._watchers[inst["id"]] = w
        except Exception as e:
            from app.log import log_exc
            log_exc(f"watcher start failed: {e}")

    def _drop_watcher(self, iid):
        w = self._watchers.pop(iid, None)
        if w:
            try:
                w.stop()
            except Exception:
                pass

    def _notify(self, title: str, msg: str, err=False):
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            icon = (QSystemTrayIcon.Warning if err else QSystemTrayIcon.Information)
            if self.tray and self.tray.isVisible():
                self.tray.showMessage(title, msg, icon, 6000)
            else:
                self.statusBar().showMessage(f"{title} — {msg}", 8000)
        except Exception:
            pass

    def _on_task_done(self, inst_name, summary):
        self._notify(f"✅ {inst_name} 任务完成", summary)
        self.console_page.append_line(f"[任务] {inst_name}: {summary}")

    def _on_task_error(self, inst_name, err):
        self._notify(f"⚠️ {inst_name} 执行出错", err, err=True)
        self.console_page.append_line(f"[任务] {inst_name} 出错: {err}")

    def _on_tray_activated(self, reason):
        from PySide6.QtWidgets import QSystemTrayIcon
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        """点最小化 → 收进任务栏托盘（不再占任务栏按钮）"""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange and self.isMinimized():
            self.hide()
            if getattr(self, "tray", None):
                self.tray.showMessage(
                    "ComfyHub", "已最小化到托盘，点击图标恢复窗口",
                    self.tray.MessageIcon.Information, 3000)
        super().changeEvent(event)

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

    # ---------- 启动孤儿检测 ----------
    def _init_orphan_check(self):
        """启动 0.8 秒后扫一次残留：上次会话崩溃/被强杀时 closeEvent 没机会跑，
        ComfyUI 会留在后台。新启动的实例已绑 kill-on-close Job（防新增），
        这里负责清理历史残留（防已存在的）。"""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, self._check_orphans)

    def _check_orphans(self):
        if not self.isVisible():    # 未显示（自动化测试等）不弹窗
            return
        try:
            from app.process import scan_orphans, kill_tree
            orphans = scan_orphans(
                self.cfg.get("instances") or [],
                exclude_pids=self.procs.running_pids())
            if not orphans:
                return
            lines = [f"· {o['inst'].get('name') or '实例'}"
                     f"（端口 {o['inst'].get('port')}，PID {o['pid']}）"
                     for o in orphans]
            r = QMessageBox.question(
                self, "发现残留的 ComfyUI",
                "检测到以下实例正在运行，但不是本次 ComfyHub 启动的：\n\n"
                + "\n".join(lines) + "\n\n"
                "这通常是上次 ComfyHub 异常退出（崩溃/被强制结束）留下的残留。\n"
                "是否立即结束它们？若它们是你手动启动的，请选择「否」保留。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r != QMessageBox.Yes:
                return
            for o in orphans:
                kill_tree(o["pid"])
                self.console_page.append_line(
                    f"[清理] 已结束残留实例"
                    f"「{o['inst'].get('name') or '实例'}」(PID {o['pid']})")
            self._notify("残留进程已清理",
                         f"已结束 {len(orphans)} 个残留 ComfyUI")
        except Exception as e:
            from app.log import log_exc
            log_exc(f"_check_orphans: {e}")

    # ---------- 关闭 ----------
    def closeEvent(self, event):
        # 有实例运行中 → 明确提示"关闭会一并停止实例"，确认后才退
        running = [i.get("name") or "实例" for i in self.cfg.get("instances", [])
                   if self.procs.is_running(i)]
        if running:
            names = "、".join(running)
            r = QMessageBox.question(
                self, "⚠️ 实例正在运行",
                f"当前有 {len(running)} 个实例正在运行：\n{names}\n\n"
                "关闭 ComfyHub 会一并停止这些实例（未保存的生成任务将中断），"
                "确定要关闭吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
        self.procs.stop_all()
        event.accept()
