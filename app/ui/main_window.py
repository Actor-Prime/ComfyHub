# -*- coding: utf-8 -*-
"""主窗口：窄图标导航 + 全局顶栏（对标管理大师布局）"""
import os
import time as _time
import uuid

from PySide6.QtCore import QSize, Qt, QRect, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from app.ui.nav import NAV
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem, QStackedWidget, QLabel, QMessageBox, QComboBox,
    QPushButton, QFrame, QDialog,
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
    ("🤖", "AI 助手"),
    ("🖥️", "控制台"),
    ("⚙️", "系统设置"),
]


class _Backdrop(QWidget):
    """主窗背景层（2026-11-01 自定义主题）：负责画用户背景图。

    只有「自定义」主题才画图，其余主题补一层与主题一致的低色兜底，
    避免 rgba 面板叠在未绘制区域上出现花屏。图片按 cover 方式裁切铺满，
    并按面板不透明度压暗 —— 面板越透，图越暗，保证文字始终可读。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = QPixmap()
        self._alpha = 1.0
        self._resolved = "dark"

    def set_background(self, image_path: str, alpha: float, resolved: str):
        self._alpha = alpha
        self._resolved = resolved
        pm = QPixmap(image_path) if (image_path and os.path.isfile(image_path)) else QPixmap()
        self._pix = pm
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#f7f8fa" if self._resolved == "light" else "#1e2229"))
        if self._pix.isNull():
            return
        scaled = self._pix.scaled(r.size(), Qt.KeepAspectRatioByExpanding,
                                  Qt.SmoothTransformation)
        sx = max(0, (scaled.width() - r.width()) // 2)
        sy = max(0, (scaled.height() - r.height()) // 2)
        p.drawPixmap(r, scaled, QRect(sx, sy, r.width(), r.height()))
        # 固定轻度压暗：保证文字在花哨照片上可读，但不要压掉图片本身
        # （早期按 1-alpha 取值，面板越透图越黑，实测等于把壁纸抹掉了）
        p.fillRect(r, QColor(0, 0, 0, 52))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ComfyHub — ComfyUI 多实例管理器")
        self.resize(1180, 780)
        # 最小可压缩尺寸（2026-11-08 用户要求「最好有个最小的压缩像素，避免压缩太多
        # UI 显示错误」）：
        #   · 宽 1280 —— 导航 88 + 内容 1192；实例设置页行「标签 220 + 控件 700」仍余
        #     142px，控件不会被压到 min 300、下拉框也不会顶到卡片边。
        #     取 1280 而不是 1320/1360：1280 是 720p 屏的满宽，任何常见屏都放得下，
        #     再大会在某些笔记本上顶出屏幕右边。
        #   · 高 720 —— 实测导航 9 项共 558px、viewport 600 刚好放得下不出滚动条；
        #     再高会顶出 1366×768 笔记本的可用高度（扣任务栏约 728）。
        #   · 屏幕本身不够大时（小屏/高分屏缩放）自动降下限，绝不让窗口顶出屏幕。
        _mw, _mh = 1280, 720
        try:
            from PySide6.QtWidgets import QApplication
            _scr = QApplication.primaryScreen().availableGeometry()
            if _scr.width() - 40 < _mw:
                _mw = max(1024, _scr.width() - 40)
            if _scr.height() - 80 < _mh:
                _mh = max(640, _scr.height() - 80)
        except Exception:
            pass
        self.setMinimumSize(_mw, _mh)
        self.cfg = load_config()
        # 旧配置里的明文密钥 → 启动时就加密落盘（2026-11-06，一次性迁移）
        if self.cfg.pop("_migrate_key", False):
            try:
                from app.config import save_config
                save_config(self.cfg)
            except Exception:
                pass
        self.procs = ProcManager()
        self._quit_direct = False    # 托盘「退出」直通标志（防被关闭行为「最小化」分支截胡）

        central = _Backdrop()
        central.setObjectName("Backdrop")   # QSS 里 #Backdrop 需透明（自定义主题露图片）
        self._backdrop = central
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
        sv.addSpacing(6)

        self.nav = QListWidget()
        self.nav.setViewMode(QListWidget.IconMode)
        self.nav.setFlow(QListWidget.TopToBottom)
        self.nav.setWrapping(False)
        self.nav.setUniformItemSizes(True)
        self.nav.setSpacing(4)
        # 侧边栏导航绝不能拖（2026-11-08 用户报障：按钮能被拖走/拖乱）——
        # QListWidget 在 IconMode 下默认就是 DragDrop + movement=Free，得显式关掉，
        # 否则按一下拖一下，导航项就被拽出原位
        self.nav.setDragDropMode(QListWidget.NoDragDrop)
        self.nav.setDragEnabled(False)
        self.nav.setAcceptDrops(False)
        self.nav.setDropIndicatorShown(False)
        self.nav.setMovement(QListWidget.Static)
        for icon, text in NAV_ITEMS:
            item = QListWidgetItem(f"{icon}\n{text}")
            # 2026-11-08：56 → 64，图标与文字留出呼吸感；宽度跟着侧栏走
            # 第 2 版：段间距已由 QSS margin 5px + spacing 4 撑开，项高回落到 62，
            # 9 项总高不变但每块更紧凑、间隙更清楚
            item.setSizeHint(QSize(76, 62))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            # 只剩「可选中」：去掉 ItemIsDragEnabled，双保险
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
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

        # 更新提示条（2026-11-04）：非模态，任何页面都可见
        from app.ui.update_banner import UpdateBanner
        self.update_banner = UpdateBanner(self)
        self.update_banner.sig_update.connect(self._on_update_clicked)
        self.update_banner.sig_skip.connect(self._on_skip_version)
        right.addWidget(self.update_banner)
        self._update_info = None
        self._check_worker = None

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
        from app.ui.pages.ai_page import AiPage
        from app.ui.pages.console_page import ConsolePage
        from app.ui.pages.settings_page import SettingsPage

        self.home_page = HomePage(self.cfg, self)
        self.comfyui_page = ComfyuiPage(self.cfg, self)
        self.plugins_page = PluginsPage(self.cfg, self)
        self.env_page = EnvPage(self.cfg, self)
        self.models_page = ModelsPage(self.cfg, self)
        self.workflow_page = WorkflowPage(self.cfg, self)
        self.ai_page = AiPage(self.cfg, self)
        self.console_page = ConsolePage(self.cfg, self)
        self.settings_page = SettingsPage(self.cfg, self)

        for w in (self.home_page, self.comfyui_page, self.plugins_page,
                  self.env_page, self.models_page, self.workflow_page,
                  self.ai_page, self.console_page, self.settings_page):
            self.stack.addWidget(w)

        # 表格行高统一 32px：QSS item 有 4px padding，默认行高会把字上下裁掉
        # （2026-10-27 插件页报障「字体显示不全」）；扫全部页面，新表自动覆盖
        from app.ui.theme import fit_all_tables
        fit_all_tables(self)

        self._init_tray()
        self._init_task_watch()
        self._init_heartbeat()

        self._sync_instances()
        self.nav.setCurrentRow(0)
        self._init_orphan_check()

        # 版本检查：每次启动（用户定 2026-11-04），延迟 6s 避开启动高峰
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(6000, lambda: self.check_updates(manual=False))

        # 延迟提醒（2026-11-12 用户需求）：启动满 1 小时后若仍有新版本，主动提醒一次；
        # 用 60s tick 的轻量判断（只在内存里算时间，联网仍受 update_last_check 节流）
        self._update_started = _time.time()
        self._update_reminded = set()     # 本会话已提醒过的版本
        self._reminder_open = False       # 防模态期间 tick 重入
        self._update_mode = ""
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(60 * 1000)
        self._update_timer.timeout.connect(self._update_tick)
        self._update_timer.start()

        # ---------- 主题与语言（2026-11-01）----------
        self._apply_theme()
        self.settings_page.sig_saved.connect(self._apply_theme)   # 设置页保存后实时重绘
        self._init_theme_watch()
        from app.i18n import apply_language
        apply_language(self, str((self.cfg.get("settings") or {}).get("lang", "zh")))

        # 兜底（2026-11-12 用户报障「圆角缺失/框边缘缺失」）：
        # 卡片类控件若漏设 WA_StyledBackground，QSS 就只画直边不裁圆角。
        # 启动扫一遍 + 每次切页扫当前页（覆盖懒加载/动态创建的卡片）。
        from app.ui.theme import polish_styled_bg
        polish_styled_bg(self)
        self.stack.currentChanged.connect(
            lambda _idx: polish_styled_bg(self.stack.currentWidget()))

    # ---------- 主题 ----------
    def _apply_theme(self):
        """应用主题 QSS + 同步背景层；自定义模式才传图片路径"""
        from PySide6.QtWidgets import QApplication
        from app.ui.theme import apply_theme
        info = apply_theme(QApplication.instance(), self.cfg)
        self._backdrop.set_background(
            info["bg_image"] if info["mode"] == "custom" else "",
            info["alpha"], info["resolved"])
        # 自定义主题下滚动视口会用自己的不透明底把背景图涂掉（palette autoFill），
        # 这里按模式统一关/开；其余主题保持 Qt 原行为不变。
        from PySide6.QtWidgets import QAbstractScrollArea
        custom = info["mode"] == "custom"
        for sa in self.findChildren(QAbstractScrollArea):
            sa.viewport().setAutoFillBackground(not custom)
        # 页面自己决定要不要加"底板"压住背景图（AI 页在自定义背景下会看不清）
        for w in self.findChildren(QWidget):
            fn = getattr(w, "refresh_backdrop", None)
            if callable(fn):
                try:
                    fn(info)
                except Exception:
                    pass
        # 其余页面统一加底板（2026-11-06）：custom 主题把聊天区等容器强制透明，
        # 没有底板的页面元素会浮在背景图上。
        # 2026-11-07 用户要求：底板透明度必须与左侧栏一致（theme.page_scrim_rgba），
        # 不能比左栏实——否则自定义背景图就失去意义了。
        if custom:
            from app.ui.theme import page_scrim_rgba
            scrim = page_scrim_rgba(float(info.get("alpha") or 0.72))
        for page in (self.home_page, self.comfyui_page, self.plugins_page,
                     self.env_page, self.models_page, self.workflow_page,
                     self.console_page, self.settings_page):
            if custom:
                page.setObjectName("PageScrim")
                page.setAttribute(Qt.WA_StyledBackground, True)
                page.setStyleSheet(f"QWidget#PageScrim{{background:{scrim};}}")
            else:
                page.setAttribute(Qt.WA_StyledBackground, False)
                page.setStyleSheet("")
        # 表头/单元格对齐统一（QSS 管不到 QHeaderView 的 text-align，只能代码设）
        try:
            from app.ui.theme import polish_tables
            polish_tables(self)
        except Exception:
            from app.log import log_exc
            log_exc("polish_tables failed")

    def _init_theme_watch(self):
        """「跟随系统」：Windows 深浅色切换后自动重绘（15s 轮询，开销可忽略）"""
        from app.ui.theme import theme_conf, resolve_mode
        self._sys_mode = resolve_mode("system")
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(15000)

        def _tick():
            mode, _alpha, _bg = theme_conf(self.cfg)
            if mode != "system":
                return
            cur = resolve_mode("system")
            if cur != self._sys_mode:
                self._sys_mode = cur
                self._apply_theme()

        self._theme_timer.timeout.connect(_tick)
        self._theme_timer.start()

    # ---------- 运行心跳（卡死检测）----------
    def _init_heartbeat(self):
        """2 秒一次心跳：定时器跑不动 = 主线程被卡住，由 runtime_log 的
        watchdog 线程记一条 hang 事件（进诊断报告，用户报「卡死」时才有据可查）"""
        from app import runtime_log
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(2000)
        self._heartbeat_timer.timeout.connect(runtime_log.beat)
        self._heartbeat_timer.start()
        runtime_log.beat()

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
        act_quit.triggered.connect(self._quit_from_tray)   # 直通退出（不被「最小化」截胡）
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
                w.stop()            # 非阻塞置位，线程自行退出（绝不 wait）
            except Exception:
                pass
            # QThread 运行中被销毁会崩（workers.py 三原则之①）：
            # 入墓碑持引用，线程自然结束后随主窗一起释放
            self._watcher_grave = [x for x in
                                   getattr(self, "_watcher_grave", [])
                                   if x.isRunning()]
            self._watcher_grave.append(w)

    def _stop_watchers(self):
        """退出路径收掉全部任务监听线程：先统一置停止位再逐个短暂 wait
        （已并行退出，实际等待≈一轮 HTTP 超时；运行期间绝不调用）"""
        ws = list(self._watchers.values()) + \
            list(getattr(self, "_watcher_grave", []))
        self._watchers.clear()
        for w in ws:
            try:
                w.stop()
            except Exception:
                pass
        for w in ws:
            try:
                if w.isRunning():
                    w.wait(5000)
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

    def _quit_from_tray(self):
        """托盘「退出 ComfyHub」：明确要退出，不被关闭行为的「最小化」分支截胡。

        2026-11-01 用户实测：窗口收进托盘（已隐藏）后点退出没反应——
        Qt 的 quitOnLastWindowClosed 只统计「可见」窗口，关一个隐藏窗口
        不会触发应用退出，必须显式 quit() 事件循环。
        close() 返回 False 表示 closeEvent 被拦截（用户在确认框点了否），此时不退。
        """
        self._quit_direct = True
        if self.close():
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.quit()

    def _hide_to_tray(self):
        """收进托盘：直接 hide()，不经「先最小化再拦截」的中转。

        2026-11-01 用户实测：走 showMinimized → changeEvent → hide 的链路，
        窗口会先在任务栏出现（有时还残留按钮），期望是「点了最小化就直接变成小图标」。
        直接隐藏的结果是确定的：任务栏无按钮，只剩托盘图标。
        """
        self.hide()
        if getattr(self, "tray", None):
            self.tray.showMessage(
                "ComfyHub", "已最小化到托盘，点击图标恢复窗口",
                self.tray.MessageIcon.Information, 3000)

    # ---------- 实例（全局） ----------
    def current_inst(self):
        i = self.cb_inst.currentIndex()
        return self.cfg["instances"][i] if 0 <= i < len(self.cfg["instances"]) else None

    def _sync_instances(self):
        self.cb_inst.blockSignals(True)
        self.cb_inst.clear()
        for inst in self.cfg["instances"]:
            name = inst.get("name", "?")
            path = inst.get("comfyui_path") or ""
            if not path or not os.path.isdir(path):
                name += "  ⚠ 目录不存在"      # 僵尸实例一眼可见，别对着它点启动
            self.cb_inst.addItem(name, userData=inst.get("id"))
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
        # 英文模式补扫：页面刷新会重设动态文案（按钮态、表头），切页时对齐一次
        from app import i18n
        if i18n.get_lang() != "zh":
            i18n.apply_language(self, i18n.get_lang())

    def goto(self, row):
        self.nav.setCurrentRow(row)

    # ---------- 版本更新（2026-11-04 / 延迟提醒 2026-11-12）----------
    def check_updates(self, manual: bool = False, mode: str = ""):
        """检查更新。

        mode:
          manual  —— 用户点「检查更新」，结果一律弹窗反馈
          auto    —— 启动 6 秒后的自动检查（受「启动时自动检查」开关约束）
          delayed —— 启动满 1 小时后的主动提醒检查（受「延迟提醒」开关约束）
        """
        from app import updater
        from app.ui.update_banner import CheckWorker
        st = self.cfg.get("settings") or {}
        mode = mode or ("manual" if manual else "auto")
        if mode == "auto" and not bool(st.get("update_auto_check", True)):
            return
        if mode == "delayed" and not bool(st.get("update_delayed_check", True)):
            return
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self._update_mode = mode
        self._update_manual = (mode == "manual")
        self._check_worker = CheckWorker(self.cfg, force=manual, parent=self)
        self._check_worker.sig_done.connect(self._on_update_checked)
        self._check_worker.start()

    def _on_update_checked(self, info: dict):
        from PySide6.QtWidgets import QMessageBox
        from app import updater
        self._check_worker = None
        mode = self._update_mode or ("manual" if getattr(self, "_update_manual", False)
                                     else "auto")
        manual = mode == "manual"
        if not info.get("ok"):
            if manual:
                QMessageBox.information(self, "检查更新",
                                        f"暂时无法检查更新：\n{info.get('error')}")
            return
        if not info.get("has_update"):
            self.update_banner.hide()
            self._update_info = None
            if manual:
                QMessageBox.information(self, "检查更新",
                                        f"已是最新版本 v{updater.APP_VERSION}")
            return
        self._update_info = info
        self.update_banner.show_update(info)
        if mode == "delayed":
            # 主动提醒：弹一次；「已提醒过该版本」由 _show_update_reminder 记账
            self._show_update_reminder(info)

    # ---------- 延迟提醒（启动满 1 小时）----------
    def _update_tick(self):
        """每分钟一次的时间判断；真联网受 30 分钟节流（update_last_check）"""
        try:
            from app import update_notify
        except Exception:
            return
        if self._reminder_open:
            return
        st = self.cfg.get("settings") or {}
        now = _time.time()
        if not update_notify.should_remind(st, self._update_started, now):
            return
        info = self._update_info
        if info and info.get("has_update"):
            if info.get("latest") in self._update_reminded:
                return
            self._show_update_reminder(info)
            return
        if not update_notify.can_recheck(st, now):
            return
        self.check_updates(mode="delayed")

    def _show_update_reminder(self, info: dict):
        from app import update_notify
        from app.ui.update_reminder import UpdateReminderDialog
        ver = str(info.get("latest") or "")
        if not ver or ver in self._update_reminded or self._reminder_open:
            return
        self._reminder_open = True
        try:
            dlg = UpdateReminderDialog(info, self)
            dlg.exec()
            act = dlg.action
        finally:
            self._reminder_open = False
        st = self.cfg.setdefault("settings", {})
        if act == "update":
            self._on_update_clicked()
            return
        self._update_reminded.add(ver)      # 同一会话不再对同一版本重复弹
        if act == "later":
            until = update_notify.snooze(st, dlg.delay_hours)
            save_config(self.cfg)
            import datetime as _dt
            try:
                when = _dt.datetime.fromtimestamp(until).strftime("%m-%d %H:%M")
            except Exception:
                when = ""
            self.statusBar().showMessage(f"已延后提醒：{when}" if when else "已延后提醒",
                                         6000)
        elif act == "never":
            # 复用「跳过此版本」语义：之后的自动检查一律不再提示该版本
            st["update_skipped_version"] = ver
            save_config(self.cfg)
            self.update_banner.hide()

    def _on_update_clicked(self):
        from PySide6.QtWidgets import QDialog
        from app.ui.update_banner import UpdateDialog
        if not self._update_info:
            self.check_updates(manual=True)
            return
        if self._update_info.get("missing_asset"):
            import webbrowser
            webbrowser.open(self._update_info.get("page") or "")
            return
        dlg = UpdateDialog(self._update_info, self)
        dlg.exec()
        if dlg.need_restart:
            self._quit_for_update()

    def _on_skip_version(self):
        from app.config import save_config
        ver = (self._update_info or {}).get("latest")
        if not ver:
            return
        self.cfg.setdefault("settings", {})["update_skipped_version"] = ver
        save_config(self.cfg)
        self.update_banner.hide()
        self._update_info = None

    def _quit_for_update(self):
        """更新已就绪：保存配置并退出（安装器/更新脚本已在后台接手）"""
        from PySide6.QtWidgets import QApplication
        from app.config import save_config
        try:
            save_config(self.cfg)
        except Exception:
            pass
        self.hide()
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        QApplication.quit()

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
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            # 无头测试环境绝不能弹模态窗（2026-10-31 实测：测试 show() 后
            # 机器上恰好有实例在跑 → QMessageBox.question 挂死整个测试进程）
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
        # 关闭行为（2026-10-31）：settings.close_behavior = ask / close / minimize
        #   ask      = 弹框询问（退出 / 最小化到托盘，可勾「记住我的选择」）
        #   close    = 直接退出
        #   minimize = 最小化到托盘（直接 hide 收进托盘并提示，任务栏不留按钮）
        # 托盘「退出」置 _quit_direct 直通退出路径
        behavior = str((self.cfg.get("settings") or {}).get("close_behavior") or "ask")
        if not self._quit_direct and behavior == "minimize":
            event.ignore()
            self._hide_to_tray()
            return
        if not self._quit_direct and behavior != "close":
            from app.ui.close_dialog import CloseChoiceDialog
            dlg = CloseChoiceDialog(self)
            if dlg.exec() != QDialog.Accepted or not dlg.choice:
                event.ignore()       # 取消/关掉弹窗：留在窗口
                return
            if dlg.remembered():
                self.cfg.setdefault("settings", {})["close_behavior"] = dlg.choice
                save_config(self.cfg)
                # 设置页下拉立即跟上（不然要切一次页才刷新，用户会以为没记住）
                if hasattr(self, "settings_page"):
                    self.settings_page.on_show()
            if dlg.choice == "minimize":
                event.ignore()
                self._hide_to_tray()
                return
        # 退出路径（直接退出 / 用户选了退出 / 托盘退出）：
        # 有实例运行中 → 明确提示"关闭会一并停止实例"，确认后才退
        running = [i.get("name") or "实例" for i in self.cfg.get("instances", [])
                   if self.procs.is_running(i)]
        if running:
            names = "、".join(running)
            # 主窗口藏在托盘（隐藏）时，挂在它上面的模态框在 Windows 上
            # 可能不被带到前台 → 看起来"没反应"。此时改用无父窗口弹框，
            # 让它自己成为顶层窗口（有任务栏按钮、必到前台）。
            parent = self if self.isVisible() else None
            r = QMessageBox.question(
                parent, "⚠️ 实例正在运行",
                f"当前有 {len(running)} 个实例正在运行：\n{names}\n\n"
                "关闭 ComfyHub 会一并停止这些实例（未保存的生成任务将中断），"
                "确定要关闭吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
        self.procs.stop_all()
        self._stop_watchers()   # 任务监听线程统一退出（2026-11-02 线程化后必须收）
        event.accept()
