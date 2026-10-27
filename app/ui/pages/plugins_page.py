# -*- coding: utf-8 -*-
"""插件管理页

两个分区（Tab）：
  · 已安装 —— 启停 / 启动模式（插件方案）/ 节点冲突检查 / 批量更新
  · 插件市场 —— 从 ComfyUI-Manager 官方清单（6000+）搜索并一键安装
"""
import os

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QIcon, QColor, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog, QComboBox,
    QTabWidget, QLineEdit, QAbstractItemView,
)

from app import instance as inst_lib
from app import plugin_profiles as pp
from app import plugin_store as store
from app.config import save_config
from app.workers import swap_worker


# 状态列警告图标（PIL 自绘 assets/warn_triangle.png，见 assets/SOURCES.md）。
# 本文件在 app/ui/pages/ 下（比 theme.py 多一层），上溯四层：
# 开发环境 = 项目根；打包后 = _internal（assets 在 _internal/assets）。
_WARN_ICON = None


def _warn_icon():
    global _WARN_ICON
    if _WARN_ICON is None:
        p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))), "assets", "warn_triangle.png")
        _WARN_ICON = QIcon(p) if os.path.isfile(p) else QIcon()
    return _WARN_ICON


# ============================================================ Workers
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


class ApplyProfileWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, enabled_names):
        super().__init__()
        self.comfyui_path = comfyui_path
        self.enabled_names = enabled_names

    def run(self):
        try:
            out = pp.apply_profile(self.comfyui_path, self.enabled_names,
                                   log=self.sig_log.emit)
        except Exception as e:
            out = {"ok": False, "changed": [], "errors": [str(e)], "rolled_back": False}
        self.sig_done.emit(out)


class ScanDupWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, only_enabled=True):
        super().__init__()
        self.comfyui_path = comfyui_path
        self.only_enabled = only_enabled

    def run(self):
        try:
            r = pp.scan_duplicate_nodes(self.comfyui_path, self.only_enabled,
                                        log=self.sig_log.emit)
        except Exception as e:
            r = {"conflicts": {}, "scanned": 0, "keys_total": 0, "error": str(e)}
        self.sig_done.emit(r)


class GitInfoWorker(QThread):
    """后台批量读 git 分支/commit/时间（每插件 2 条本地 git 命令，避免卡 UI）"""
    sig_done = Signal(dict)     # {repo_dir: {branch, commit, date}}

    def __init__(self, dirs):
        super().__init__()
        self.dirs = dirs

    def run(self):
        out = {}
        for d in self.dirs:
            out[d] = inst_lib.git_repo_info(d)
        self.sig_done.emit(out)


class RefsWorker(QThread):
    """联网列远端分支/tag（切换版本弹窗用）"""
    sig_done = Signal(list, str)    # (refs, error)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        refs, err = inst_lib.git_remote_refs(self.url)
        self.sig_done.emit(refs, err)


class CheckoutWorker(QThread):
    """fetch + checkout 指定版本"""
    sig_log = Signal(str)
    sig_done = Signal(bool, str)

    def __init__(self, repo_dir, ref):
        super().__init__()
        self.repo_dir, self.ref = repo_dir, ref

    def run(self):
        self.sig_log.emit(f"切换 {os.path.basename(self.repo_dir)} → {self.ref} …")
        ok, msg = inst_lib.git_checkout_ref(self.repo_dir, self.ref)
        self.sig_done.emit(ok, msg)


class CatalogWorker(QThread):
    """拉取插件清单（首次约 10s，之后走 12h 缓存）"""
    sig_log = Signal(str)
    sig_done = Signal(list, dict)
    sig_error = Signal(str)

    def __init__(self, force=False):
        super().__init__()
        self.force = force

    def run(self):
        try:
            nodes, meta = store.load_catalog(force=self.force, log=self.sig_log.emit)
            self.sig_done.emit(nodes, meta)
        except Exception as e:
            self.sig_error.emit(str(e))


class StatsWorker(QThread):
    """拉官方 Registry 热度索引（6131 个仓库，首次约 90 秒，之后走 12h 缓存）"""
    sig_log = Signal(str)
    sig_done = Signal(dict)
    sig_progress = Signal(int, int)

    def __init__(self, force=False):
        super().__init__()
        self.force = force

    def run(self):
        try:
            stats = store.load_registry_stats(
                force=self.force,
                progress=lambda d, t: self.sig_progress.emit(d, t),
                log=self.sig_log.emit)
            self.sig_done.emit(stats or {})
        except Exception as e:
            self.sig_log.emit(f"热度数据获取失败：{e}")
            self.sig_done.emit({})


# ============================================================ 页面
class PluginsPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._conflict_map = {}
        self._conflict_with = {}    # 插件名 -> 冲突对方插件名集合（状态列悬停提示用）
        self._catalog = []
        self._catalog_meta = {}
        self._catalog_worker = None
        self._last_market_rows = []
        self._stats = {}
        self._stats_worker = None
        self._rows_data = []           # 已安装表当前行对应的数据（操作列按钮用）
        self._gitinfo_worker = None    # 后台 git 信息线程
        self._refs_worker = None       # 远端 refs 线程
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    # ---------------------------------------------------------- 构建
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        head.addWidget(self.lb_inst)
        head.addStretch()
        root.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_installed_tab(), "已安装")
        self.tabs.addTab(self._build_market_tab(), "插件市场")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

    # ---------------- Tab 1：已安装 ----------------
    def _build_installed_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(6, 10, 6, 6)
        root.setSpacing(8)

        pbar = QHBoxLayout()
        lb = QLabel("启动模式:")
        lb.setStyleSheet("color:#9aa4b2;")
        self.cb_profile = QComboBox()
        self.cb_profile.setMinimumWidth(170)
        self.cb_profile.currentIndexChanged.connect(self._on_profile_changed)
        b_apply = QPushButton("▶ 应用")
        b_apply.setProperty("accent", True)
        b_apply.setToolTip("按此模式批量启用/停用插件（重启 ComfyUI 生效）")
        b_apply.clicked.connect(self._apply_profile)
        b_save = QPushButton("💾 存为方案")
        b_save.setToolTip("把当前插件的启停状态保存为一个模式")
        b_save.clicked.connect(self._save_profile)
        b_del = QPushButton("删除")
        b_del.clicked.connect(self._delete_profile)
        pbar.addWidget(lb)
        pbar.addWidget(self.cb_profile)
        pbar.addWidget(b_apply)
        pbar.addWidget(b_save)
        pbar.addWidget(b_del)
        pbar.addStretch()
        root.addLayout(pbar)

        self.tb = QTableWidget(0, 7)
        self.tb.setHorizontalHeaderLabels(
            ["插件名", "Git 地址", "分支", "版本", "更新时间", "状态", "操作"])
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)   # 地址列固定宽，可手动拉
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)         # 状态列：文字+⚠ 图标
        hh.setSectionResizeMode(6, QHeaderView.Fixed)
        # 列宽预算（2026-10-28 用户要求「插件名尽量显示全，链接可截断」）：
        # 地址 300→160（截断靠 tooltip 看全文）、状态 110→96、操作 232→210
        # （4 按钮实需 184+item padding 12=196，210 留 14px 余量），
        # 省出 ~76px 全给 Stretch 的插件名列
        self.tb.setColumnWidth(1, 160)
        self.tb.setColumnWidth(5, 96)
        self.tb.setColumnWidth(6, 210)
        self.tb.horizontalHeaderItem(3).setToolTip(
            "当前 commit 短哈希；点「版本」可切换分支/tag")
        self.tb.horizontalHeaderItem(5).setToolTip(
            "有节点冲突等故障时，「启用」后面会挂 ⚠ 图标，悬停可看详情")
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb.setAlternatingRowColors(True)
        # 双击 Git 地址列 → 浏览器打开仓库页（单击只选中；2026-10-27 用户定）
        self.tb.cellDoubleClicked.connect(self._on_installed_click)
        self.tb.doubleClicked.connect(self._toggle)
        root.addWidget(self.tb, 1)

        bar = QHBoxLayout()
        self.lb_tip = QLabel("双击行 = 启用/禁用；调好后点「存为方案」保存成启动模式。")
        self.lb_tip.setStyleSheet("color:#7d8797;")
        bar.addWidget(self.lb_tip, 1)
        b_dup = QPushButton("🔍 检查节点冲突")
        b_dup.clicked.connect(self._check_duplicates)
        b_upd = QPushButton("🔄 更新全部")
        b_upd.clicked.connect(self._update_all)
        b_open = QPushButton("打开目录")
        b_open.clicked.connect(self._open_dir)
        b_git = QPushButton("＋ Git URL 安装")
        b_git.clicked.connect(self._install)
        bar.addWidget(b_dup)
        bar.addWidget(b_upd)
        bar.addWidget(b_open)
        bar.addWidget(b_git)
        root.addLayout(bar)
        return w

    # ---------------- Tab 2：插件市场 ----------------
    def _build_market_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(6, 10, 6, 6)
        root.setSpacing(8)

        bar = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜索插件名 / 作者 / 描述…（回车搜索）")
        self.ed_search.returnPressed.connect(self._do_search)
        self.ed_search.textChanged.connect(self._on_search_typing)
        b_search = QPushButton("🔍 搜索")
        b_search.setProperty("accent", True)
        b_search.clicked.connect(self._do_search)
        self.b_refresh = QPushButton("刷新清单")
        self.b_refresh.clicked.connect(lambda: self._load_catalog(force=True))
        bar.addWidget(self.ed_search, 1)
        bar.addWidget(b_search)
        bar.addWidget(self.b_refresh)
        root.addLayout(bar)

        sortbar = QHBoxLayout()
        self.lb_market_stat = QLabel("尚未加载插件清单（切到此页会自动加载，首次约 10 秒）")
        self.lb_market_stat.setStyleSheet("color:#9aa4b2;")
        sortbar.addWidget(self.lb_market_stat, 1)
        sortbar.addWidget(QLabel("排序:"))
        self.cb_sort = QComboBox()
        self.cb_sort.addItem("热门优先", "hot")
        self.cb_sort.addItem("名称 A→Z", "name")
        self.cb_sort.currentIndexChanged.connect(lambda _i: self._do_search())
        sortbar.addWidget(self.cb_sort)
        root.addLayout(sortbar)

        self.tb_market = QTableWidget(0, 5)
        self.tb_market.setHorizontalHeaderLabels(["插件", "下载量", "作者", "说明", "操作"])
        hh = self.tb_market.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self.tb_market.setColumnWidth(0, 250)
        self.tb_market.setColumnWidth(1, 92)
        self.tb_market.setColumnWidth(4, 96)
        self.tb_market.horizontalHeaderItem(1).setToolTip(
            "ComfyUI 官方 Registry 统计的下载量（万 = 10,000 次）\n"
            "无下载量数据的插件显示 GitHub 星数（⭐）或 —")
        self.tb_market.horizontalHeaderItem(0).setToolTip("双击插件名可在浏览器打开仓库页面（单击仅选中）")
        self.tb_market.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb_market.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb_market.setAlternatingRowColors(True)
        self.tb_market.setWordWrap(False)          # 说明列单行，超长用省略号
        self.tb_market.verticalHeader().setDefaultSectionSize(30)
        # 双击插件名 → 系统默认浏览器打开仓库页面（2026-10-27 用户定：单击只选中，
        # 双击才跳转，避免误开）
        self.tb_market.cellDoubleClicked.connect(self._on_market_click)
        root.addWidget(self.tb_market, 1)

        tip = QLabel("数据来源：ComfyUI-Manager 官方插件清单（6000+）。安装走 git clone，"
                     "网络不佳时可在「系统设置」配置代理后点「刷新清单」。")
        tip.setStyleSheet("color:#7d8797;")
        tip.setWordWrap(True)
        root.addWidget(tip)
        return w

    # ---------------------------------------------------------- 生命周期
    def on_show(self):
        self.refresh_list()
        if self.tabs.currentIndex() == 1 and not self._catalog:
            self._load_catalog()

    def on_instance_changed(self):
        self.refresh_list()

    def _on_tab_changed(self, i):
        if i == 1:
            self.refresh_list()
            if not self._catalog:
                self._load_catalog()

    def _log(self, m):
        try:
            self.mainwin.console_page.append_line(f"[插件] {m}")
        except Exception:
            pass

    # ---------------------------------------------------------- 已安装列表
    def refresh_list(self):
        inst = self._inst()
        self._refill_profiles()
        if not inst:
            self.lb_inst.setText("（未选择实例）")
            self.tb.setRowCount(0)
            return
        self.lb_inst.setText(f"实例：{inst.get('name')}")
        rows = pp.list_plugins(inst["comfyui_path"])     # 已剥离 .disabled 后缀
        self._rows_data = rows                            # 操作列按钮回调用
        self.tb.setRowCount(len(rows))
        pr = self._current_profile()
        want = set((pr or {}).get("enabled") or [])
        git_dirs = []
        for i, p in enumerate(rows):
            name = p["name"]
            item = QTableWidgetItem(name)
            # 悬停显示完整插件名：列被压窄时文字会省略号截断，tooltip 补全
            item.setToolTip(name)
            if pr is not None and (name in want) != p["enabled"]:
                item.setForeground(Qt.gray)
                item.setToolTip(name + "\n与当前模式不一致，点「▶ 应用」同步")
            self.tb.setItem(i, 0, item)
            url = (p.get("url") or "").rstrip("/").removesuffix(".git")
            it_url = QTableWidgetItem(url)
            if url:
                it_url.setForeground(Qt.cyan)
                f = it_url.font()
                f.setUnderline(True)            # 链接语义：点地址开网页
                it_url.setFont(f)
                # tooltip 给原始完整 URL（列截断时悬停可读全文）+ 打开提示
                it_url.setToolTip(url + "\n双击在浏览器打开：" + store.web_url({"reference": url}))
            else:
                it_url.setToolTip("该插件无 Git 地址（本地/手工安装）")
            self.tb.setItem(i, 1, it_url)
            # 分支/版本/更新时间先占位，后台 git 信息线程回来再填
            for col in (2, 3, 4):
                ph = QTableWidgetItem("…" if p["git"] else "—")
                ph.setForeground(Qt.gray)
                self.tb.setItem(i, col, ph)
            if p["git"]:
                git_dirs.append(p["dir"])
            cf = self._conflict_map.get(name)
            # 状态列（2026-10-26 与「节点冲突」列合并）：文字在前，
            # 有冲突时后面跟一个 ⚠ 三角图标，悬停显示对方插件+冲突节点，移开消失
            self.tb.setCellWidget(i, 5, self._make_status(
                p, cf, self._conflict_with.get(name),
                pr is not None and (name in want) != p["enabled"]))
            self.tb.setCellWidget(i, 6, self._make_op_buttons(i, p))
        if not rows:
            self.lb_tip.setText("这个实例还没有插件 —— 切到「插件市场」搜一个装上吧。")
        # 后台批量读 git 分支/commit/时间（不卡界面）
        # ★ 必须走 swap_worker：refresh_list 会被反复调用（双击启停/操作后都要刷），
        #   直接覆盖旧引用会让运行中的 QThread 被 GC → qFatal "QThread: Destroyed
        #   while thread is still running" → 进程闪退（2026-10-26 用户实测 18:55 崩溃）
        if git_dirs:
            w = GitInfoWorker(git_dirs)
            w.sig_done.connect(self._on_gitinfo)
            swap_worker(self, "_gitinfo_worker", w)
            w.start()   # ← swap_worker 只负责挂载+旧线程退役，不代替 start()
        # 市场列表的「已安装」标记跟着刷新
        if self._catalog and self._last_market_rows:
            self._render_market(self._last_market_rows)

    def _make_status(self, p, cf, other_names, mismatch):
        """状态格：「启用/已禁用」文字 + 有冲突时后跟 ⚠ 图标

        用行内 widget 而非 QTableWidgetItem：item 的图标只能在文字前面，
        这里要求图标跟在文字后面（2026-10-26 用户指定）。
        悬停出提示、移开消失是 Qt tooltip 原生行为。
        """
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(6, 0, 4, 0)
        hl.setSpacing(4)
        lb = QLabel("启用" if p["enabled"] else "已禁用")
        lb.setStyleSheet(
            "color:%s; background:transparent;" % ("#3fb950" if p["enabled"] else "#7d8797"))
        hl.addWidget(lb)
        if cf:
            ic = QLabel()
            ic.setPixmap(_warn_icon().pixmap(14, 14))
            hl.addWidget(ic)
            tip = ["该插件与其他插件存在节点冲突："]
            if other_names:
                tip.append("冲突对方：" + "、".join(sorted(other_names)))
            tip.append("重复节点：" + "、".join(cf))
            tip.append("")
            tip.append("同名节点后加载的会覆盖先加载的，可能出图异常。\n"
                       "禁用其中一个插件，或用「启动模式」互斥启用即可解决。")
            holder.setToolTip("\n".join(tip))
        elif mismatch:
            holder.setToolTip("该插件当前状态与所选启动模式不一致，点「▶ 应用」同步")
        hl.addStretch()
        return holder

    def _make_op_buttons(self, row, p):
        """每行操作：更新 / 版本 / 打开 / 卸载"""
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(4)

        def mk(text, cb, danger=False):
            b = QPushButton(text)
            # 22px：cellWidget 可用高 23px（行32-item padding 8），24 会溢出 1px 被裁
            b.setFixedHeight(22)
            if danger:
                b.setStyleSheet("color:#e06c75;")
            b.clicked.connect(lambda _c=False, r=row: cb(r))
            hl.addWidget(b)
            return b

        mk("更新", self._op_update, )
        mk("版本", self._op_switch_version)
        mk("打开", self._op_open, )
        mk("卸载", self._op_uninstall, danger=True)
        hl.addStretch()
        return holder

    def _op_row_data(self, row):
        inst = self._inst()
        if not inst or row >= len(getattr(self, "_rows_data", []) or []):
            return None
        return self._rows_data[row]

    def _on_gitinfo(self, info: dict):
        """GitInfoWorker 回来：按目录回填分支/版本/更新时间"""
        for i in range(self.tb.rowCount()):
            it = self.tb.item(i, 0)
            p = self._op_row_data(i)
            if not it or not p or p["dir"] not in info:
                continue
            gi = info[p["dir"]]
            if gi.get("branch"):
                self.tb.setItem(i, 2, QTableWidgetItem(gi["branch"]))
            if gi.get("commit"):
                c = QTableWidgetItem(gi["commit"])
                c.setToolTip("commit " + gi["commit"])
                self.tb.setItem(i, 3, c)
            if gi.get("date"):
                self.tb.setItem(i, 4, QTableWidgetItem(gi["date"]))

    # ---------------------------------------------------------- 启动模式
    def _profiles(self):
        inst = self._inst()
        if not inst:
            return []
        if not isinstance(inst.get("plugin_profiles"), list) or not inst["plugin_profiles"]:
            inst["plugin_profiles"] = [{"name": "默认", "enabled": [], "args": ""}]
        return inst["plugin_profiles"]

    def _refill_profiles(self):
        inst = self._inst()
        self.cb_profile.blockSignals(True)
        self.cb_profile.clear()
        if inst:
            for pr in self._profiles():
                self.cb_profile.addItem(pr.get("name", "未命名"))
            idx = self.cb_profile.findText(inst.get("last_profile") or "")
            if idx >= 0:
                self.cb_profile.setCurrentIndex(idx)
        self.cb_profile.blockSignals(False)

    def _current_profile(self):
        name = self.cb_profile.currentText()
        for pr in self._profiles():
            if pr.get("name") == name:
                return pr
        return None

    def _on_profile_changed(self, _i):
        inst = self._inst()
        pr = self._current_profile()
        if not inst or pr is None:
            return
        inst["last_profile"] = pr.get("name")
        save_config(self.cfg)
        self.lb_tip.setText(
            f"已选模式「{pr.get('name')}」（{len(pr.get('enabled') or [])} 个插件）。"
            "点「▶ 应用」才会真正启停插件。")

    def _apply_profile(self):
        inst = self._inst()
        pr = self._current_profile()
        if not inst or pr is None:
            QMessageBox.information(self, "提示", "请先选择实例与模式")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行上一个操作，请稍候")
            return
        enabled = pr.get("enabled") or []
        if not enabled:
            ans = QMessageBox.question(
                self, "确认", "该模式没有记录任何插件，应用后会停用全部插件。继续？")
            if ans != QMessageBox.Yes:
                return
        if self.mainwin.procs.is_running(inst):
            ans = QMessageBox.question(
                self, "ComfyUI 正在运行",
                "改动插件需要重启 ComfyUI 才生效。现在就应用吗？")
            if ans != QMessageBox.Yes:
                return
        w = ApplyProfileWorker(inst["comfyui_path"], enabled)
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_applied)
        swap_worker(self, "_worker", w)   # 墓碑替换，防运行中线程被 GC 闪退
        w.start()

    def _on_applied(self, out):
        self.refresh_list()
        if out.get("ok"):
            ch = out.get("changed") or []
            on = len([c for c in ch if c[1] == "on"])
            off = len([c for c in ch if c[1] == "off"])
            self.lb_tip.setText(f"已应用：{on} 个启用、{off} 个停用　（重启 ComfyUI 生效）")
            self._log(f"应用模式完成：启用 {on}、停用 {off}")
        else:
            errs = "；".join(out.get("errors") or [])[:300]
            tail = "（已回滚到操作前状态）" if out.get("rolled_back") else ""
            QMessageBox.warning(self, "应用失败", errs + tail)

    def _save_profile(self):
        inst = self._inst()
        if not inst:
            return
        cur = pp.capture_current(inst["comfyui_path"])
        name, ok = QInputDialog.getText(
            self, "存为方案",
            f"把当前 {len(cur)} 个已启用插件存为一个模式，名称：",
            text=self.cb_profile.currentText() or "新模式")
        name = (name or "").strip()
        if not ok or not name:
            return
        profiles = self._profiles()
        for pr in profiles:
            if pr.get("name") == name:
                ans = QMessageBox.question(self, "已存在", f"模式「{name}」已存在，覆盖？")
                if ans != QMessageBox.Yes:
                    return
                pr["enabled"] = cur
                break
        else:
            profiles.append({"name": name, "enabled": cur, "args": ""})
        inst["last_profile"] = name
        save_config(self.cfg)
        self._refill_profiles()
        self.cb_profile.setCurrentText(name)
        self.lb_tip.setText(f"已保存模式「{name}」（{len(cur)} 个插件）")

    def _delete_profile(self):
        inst = self._inst()
        pr = self._current_profile()
        if not inst or pr is None:
            return
        if len(self._profiles()) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个模式")
            return
        ans = QMessageBox.question(self, "删除模式",
                                   f"删除模式「{pr.get('name')}」？（不影响插件本身）")
        if ans != QMessageBox.Yes:
            return
        inst["plugin_profiles"] = [x for x in self._profiles() if x is not pr]
        if inst.get("last_profile") == pr.get("name"):
            inst["last_profile"] = inst["plugin_profiles"][0].get("name", "默认")
        save_config(self.cfg)
        self._refill_profiles()

    # ---------------------------------------------------------- 节点冲突
    def _check_duplicates(self):
        inst = self._inst()
        if not inst:
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行上一个操作，请稍候")
            return
        self.lb_tip.setText("正在扫描节点冲突（只查已启用插件，静态解析不执行插件代码）…")
        w = ScanDupWorker(inst["comfyui_path"], only_enabled=True)
        w.sig_log.connect(lambda m: None)
        w.sig_done.connect(self._on_dup_done)
        swap_worker(self, "_worker", w)
        w.start()

    def _on_dup_done(self, r):
        conflicts = r.get("conflicts") or {}
        self._conflict_map = {}
        self._conflict_with = {}    # 插件名 -> 与之冲突的对方插件名集合（状态列悬停提示用）
        for node, owners in conflicts.items():
            for o in owners:
                self._conflict_map.setdefault(o, []).append(node)
                self._conflict_with.setdefault(o, set()).update(
                    x for x in owners if x != o)
        self.refresh_list()
        if r.get("error"):
            QMessageBox.warning(self, "扫描失败", r["error"])
            return
        if not conflicts:
            self.lb_tip.setText(
                f"✅ 未发现节点冲突（扫描 {r.get('scanned', 0)} 个插件、"
                f"{r.get('keys_total', 0)} 个节点）")
            return
        lines = [f"共 {len(conflicts)} 组同名节点：", ""]
        for node, owners in list(conflicts.items())[:15]:
            lines.append(f"· {node}")
            lines.append("    ← " + "、".join(owners))
        if len(conflicts) > 15:
            lines.append(f"…还有 {len(conflicts) - 15} 组")
        lines.append("")
        lines.append("同名节点后加载的会覆盖先加载的，可能导致出图异常。")
        lines.append("建议用「启动模式」只启用其中一方。")
        self.lb_tip.setText(
            f"⚠️ 发现 {len(conflicts)} 组节点冲突，见表格「状态」列的 ⚠ 图标（悬停看详情）")
        QMessageBox.warning(self, "节点冲突", "\n".join(lines))

    # ---------------------------------------------------------- 插件市场
    def _on_search_typing(self, _t):
        if self._catalog:
            self._do_search()

    def _load_catalog(self, force=False):
        if self._catalog_worker and self._catalog_worker.isRunning():
            return
        self.lb_market_stat.setText("正在拉取插件清单…（首次约 10 秒，之后走缓存）")
        self.b_refresh.setEnabled(False)
        w = CatalogWorker(force=force)
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_catalog)
        w.sig_error.connect(self._on_catalog_err)
        swap_worker(self, "_catalog_worker", w)
        w.start()

    def _on_catalog(self, nodes, meta):
        self._catalog = nodes
        self._catalog_meta = meta
        self.b_refresh.setEnabled(True)
        src = meta.get("source", "?")
        cached = "缓存" if meta.get("cached") else "在线"
        self.lb_market_stat.setText(
            f"共 {len(nodes)} 个插件（来源 {src}·{cached}）　正在获取热度数据…")
        self._do_search()
        self._ensure_stats()

    def _on_catalog_err(self, msg):
        self.b_refresh.setEnabled(True)
        self.lb_market_stat.setText(f"清单加载失败：{msg}")
        QMessageBox.warning(self, "插件市场",
                            f"{msg}\n\n可在「系统设置」里配置代理后点「刷新清单」重试。")

    def _ensure_stats(self, force=False):
        """拉官方 Registry 热度（后台，约 90 秒首次 / 之后走 12h 缓存）"""
        if not force and self._stats:
            store.attach_stats(self._catalog, self._stats)
            self._do_search()
            return
        if self._stats_worker and self._stats_worker.isRunning():
            return
        w = StatsWorker(force=force)
        w.sig_log.connect(self._log)
        w.sig_progress.connect(self._on_stats_progress)
        w.sig_done.connect(self._on_stats)
        swap_worker(self, "_stats_worker", w)
        w.start()

    def _on_stats_progress(self, done, total):
        self.lb_market_stat.setText(f"正在获取热度数据…（{done}/{total} 页）")

    def _on_stats(self, stats):
        self._stats = stats or {}
        if self._stats:
            store.attach_stats(self._catalog, self._stats)
            self._do_search()
            hit = sum(1 for n in self._catalog if n.get("_downloads") or n.get("_stars"))
            self.lb_market_stat.setText(
                f"共 {len(self._catalog)} 个插件　热度已更新（{hit} 个有下载/星标数据）")
        else:
            self.lb_market_stat.setText(
                f"共 {len(self._catalog)} 个插件　热度数据暂不可用（不影响安装）")

    def _do_search(self):
        if not self._catalog:
            return
        kw = self.ed_search.text().strip()
        sort = (self.cb_sort.currentData() or "hot") if hasattr(self, "cb_sort") else "hot"
        rows = store.search(self._catalog, kw, limit=400, sort=sort)
        self._last_market_rows = rows
        self._render_market(rows)
        if kw:
            self.lb_market_stat.setText(
                f"搜索「{kw}」：{len(rows)} 个结果"
                + ("（已截断，请细化关键词）" if len(rows) >= 400 else ""))

    def _render_market(self, rows):
        inst = self._inst()
        installed = set()
        if inst:
            installed = {p["name"] for p in pp.list_plugins(inst["comfyui_path"])}
        self.tb_market.setRowCount(len(rows))
        for i, n in enumerate(rows):
            name = store.display_name(n)
            t = QTableWidgetItem(name)
            # 链接样式：蓝色+下划线，提示可点开仓库页面
            web = store.web_url(n)
            if web:
                t.setForeground(QColor("#6fb3ff"))
                f = QFont(t.font())
                f.setUnderline(True)
                t.setFont(f)
                t.setToolTip(f"双击在浏览器打开：{web}")
            else:
                t.setToolTip(store.install_url(n) or "（无仓库地址）")
            self.tb_market.setItem(i, 0, t)
            # 热度：下载量优先，其次 GitHub 星数
            hot = QTableWidgetItem(store.hot_text(n))
            dl = int(n.get("_downloads") or 0)
            st_ = int(n.get("_stars") or 0)
            if dl >= 100000 or st_ >= 1000:
                hot.setForeground(Qt.cyan)
            elif dl > 0 or st_ > 0:
                hot.setForeground(Qt.green)
            else:
                hot.setForeground(Qt.gray)
            hot.setTextAlignment(Qt.AlignCenter)
            hot.setToolTip(f"官方 Registry 下载量 {dl:,}　·　GitHub 星数 {st_:,}"
                           + ("" if (dl or st_) else "\n（该插件未注册到 ComfyUI 官方 Registry）"))
            self.tb_market.setItem(i, 1, hot)
            self.tb_market.setItem(i, 2, QTableWidgetItem(n.get("author") or ""))
            desc = (n.get("description") or "").replace("\n", " ")
            d = QTableWidgetItem(desc)
            d.setToolTip(desc)
            self.tb_market.setItem(i, 3, d)
            guess = store.install_url(n).rstrip("/").split("/")[-1]
            if guess.endswith(".git"):
                guess = guess[:-4]
            if guess in installed:
                it = QTableWidgetItem("已安装 ✓")
                it.setForeground(Qt.green)
                it.setTextAlignment(Qt.AlignCenter)      # 与按钮列视觉对齐
                self.tb_market.setItem(i, 4, it)
            else:
                btn = QPushButton("安装")
                btn.setProperty("success", True)
                btn.setFixedSize(80, 22)          # 固定尺寸防列宽变化换行；22 ≤ cell 可用高 23
                btn.clicked.connect(lambda _c=False, node=n: self._install_from_store(node))
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.addWidget(btn, 0, Qt.AlignCenter)
                self.tb_market.setCellWidget(i, 4, holder)

    def _on_market_click(self, row, col):
        """双击插件市场第 0 列（名称）→ 系统默认浏览器打开仓库页面；
        单击仅选中（cellClicked 未接槽），双击其他列不动作"""
        if col != 0:
            return
        rows = self._last_market_rows
        if not (0 <= row < len(rows)):
            return
        u = store.web_url(rows[row])
        if u:
            QDesktopServices.openUrl(QUrl(u))

    def _install_from_store(self, node):
        inst = self._inst()
        if not inst:
            return
        url = store.install_url(node)
        if not url:
            QMessageBox.warning(self, "插件市场", "该插件没有可用的安装地址")
            return
        name = store.display_name(node)
        repo = url.rstrip("/").split("/")[-1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        dest = os.path.join(inst["comfyui_path"], "custom_nodes", repo)
        if os.path.exists(dest) or os.path.exists(dest + ".disabled"):
            QMessageBox.information(self, "插件市场", f"已存在同名插件目录：{repo}")
            return
        ans = QMessageBox.question(
            self, "安装插件",
            f"安装「{name}」？\n\n仓库：{url}\n目录：custom_nodes/{repo}\n\n"
            "安装后建议重启 ComfyUI。")
        if ans != QMessageBox.Yes:
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "已有安装任务在进行")
            return
        w = InstallWorker(url, dest)
        w.sig_log.connect(self._log)
        w.sig_done.connect(lambda ok, msg: self._on_installed(ok, msg, name))
        swap_worker(self, "_worker", w)
        w.start()

    def _on_installed(self, ok, msg, name):
        self.refresh_list()
        if ok:
            QMessageBox.information(self, "安装完成",
                                    f"「{name}」已安装。\n重启 ComfyUI 后生效。")
        else:
            QMessageBox.warning(self, "安装失败", msg[:400])

    # ---------------------------------------------------------- 原有操作
    def _nodes_dir(self):
        inst = self._inst()
        return os.path.join(inst["comfyui_path"], "custom_nodes") if inst else ""

    def _on_installed_click(self, row, col):
        """已安装页：双击 Git 地址列（col 1）→ 系统默认浏览器打开仓库页面；
        单击仅选中；双击地址列由 _toggle 的列守卫跳过启用/禁用"""
        if col != 1:
            return
        rows = getattr(self, "_rows_data", [])
        if not (0 <= row < len(rows)):
            return
        u = store.web_url({"reference": rows[row].get("url") or ""})
        if u:
            QDesktopServices.openUrl(QUrl(u))

    def _toggle(self, index):
        # 双击 Git 地址列 = 开网页（单击已触发），不切启用/禁用
        if index.column() == 1:
            return
        inst = self._inst()
        if not inst:
            return
        # ★ 表格显示名是去掉 .disabled 后缀的（list_plugins 规范化），
        #   不能拿它当真实路径直接砍后缀——禁用态的真实目录是 name.disabled，
        #   旧逻辑 isdir(显示名) 判否后走启用分支 src[:-9] 把名字砍成 "Com"
        #   → WinError 2（2026-10-26 用户实测 aki 实例 GGUF 插件踩到）
        name = self.tb.item(index.row(), 0).text()
        base = os.path.join(self._nodes_dir(), name)
        disabled = base + ".disabled"
        if os.path.isdir(base):
            src, dst = base, disabled          # 启用中 → 禁用
        elif os.path.isdir(disabled):
            src, dst = disabled, base          # 已禁用 → 启用
        else:
            QMessageBox.warning(
                self, "ComfyHub",
                f"未找到插件目录：\n{base}\n（可能已被手动删除，先刷新列表）")
            return
        try:
            os.rename(src, dst)
            self.refresh_list()
        except OSError as e:
            QMessageBox.warning(self, "ComfyHub", "重命名失败（可能被占用）:\n" + str(e))

    def _update_all(self):
        inst = self._inst()
        if not inst:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "更新插件前请先停止 ComfyUI")
            return
        repos = [(p["name"], p["dir"]) for p in pp.list_plugins(inst["comfyui_path"]) if p["git"]]
        if not repos:
            QMessageBox.information(self, "ComfyHub", "没有可更新的 Git 插件")
            return
        w = UpdateAllWorker(repos)
        w.sig_log.connect(self._log)
        w.sig_done.connect(lambda ok, t: (
            self._log(f"更新完成 {ok}/{t}"), self.refresh_list()))
        swap_worker(self, "_worker", w)
        w.start()

    # ------------------------------------------------------ 行内操作（更新/版本/打开/卸载）
    def _op_update(self, row):
        """单个插件更新（git pull）"""
        p = self._op_row_data(row)
        inst = self._inst()
        if not p or not inst:
            return
        if not p["git"]:
            QMessageBox.information(self, "ComfyHub", "该插件不是 Git 仓库，无法自动更新")
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "更新插件前请先停止 ComfyUI")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行上一个操作，请稍候")
            return
        self.lb_tip.setText(f"正在更新「{p['name']}」…")
        w = UpdateAllWorker([(p["name"], p["dir"])])
        w.sig_log.connect(self._log)
        w.sig_done.connect(lambda ok, t, n=p["name"]: (
            self.lb_tip.setText(f"「{n}」{'已是最新' if ok else '更新失败'}，详见控制台"),
            self.refresh_list()))
        swap_worker(self, "_worker", w)
        w.start()

    def _op_switch_version(self, row):
        """切换分支/tag：先 ls-remote 列远端 refs（联网），选择后 fetch+checkout"""
        p = self._op_row_data(row)
        if not p:
            return
        if not p["git"] or not p.get("url"):
            QMessageBox.information(self, "ComfyHub", "该插件不是 Git 仓库，无法切换版本")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行上一个操作，请稍候")
            return
        self.lb_tip.setText(f"正在获取「{p['name']}」的远端分支/标签…")
        w = RefsWorker(p["url"])
        w.sig_done.connect(lambda refs, err, pp_=p: self._on_refs(refs, err, pp_))
        swap_worker(self, "_refs_worker", w)
        w.start()

    def _on_refs(self, refs, err, p):
        if err:
            self.lb_tip.setText("获取远端版本失败")
            QMessageBox.warning(self, "ComfyHub",
                                f"获取远端分支/标签失败：\n{err}\n\n"
                                "可在「系统设置」配置代理后重试。")
            return
        if not refs:
            QMessageBox.information(self, "ComfyHub", "远端没有任何分支/标签")
            return
        labels = [f"{disp}  ({sha})" for disp, sha in refs]
        label, ok = QInputDialog.getItem(
            self, f"切换版本 — {p['name']}",
            f"选择要切换到的版本（当前：{self.tb.item(self._current_row(p), 3).text() if self._current_row(p) is not None else '?'}）:",
            labels, 0, False)
        if not ok or not label:
            return
        ref = label.split("  (")[0]
        ref_name = ref.split(":", 1)[1] if ":" in ref else ref
        ans = QMessageBox.question(
            self, "切换版本",
            f"把「{p['name']}」切换到 {ref}？\n\n"
            "注意：切换后本地改动会被覆盖，且「更新」会拉取该分支的最新提交。")
        if ans != QMessageBox.Yes:
            return
        if self._worker and self._worker.isRunning():
            return
        w = CheckoutWorker(p["dir"], ref_name)
        w.sig_log.connect(self._log)
        w.sig_done.connect(lambda ok2, msg, n=p["name"]: self._on_checked_out(ok2, msg, n))
        swap_worker(self, "_worker", w)
        w.start()

    def _current_row(self, p):
        for i in range(self.tb.rowCount()):
            d = self._op_row_data(i)
            if d is p:
                return i
        return None

    def _on_checked_out(self, ok, msg, name):
        self.refresh_list()
        if ok:
            self.lb_tip.setText(f"「{name}」已切换版本（重启 ComfyUI 生效）")
        else:
            QMessageBox.warning(self, "切换失败", msg[:400])

    def _op_open(self, row):
        p = self._op_row_data(row)
        if p and os.path.isdir(p["dir"]):
            inst_lib.open_in_explorer(p["dir"])

    def _op_uninstall(self, row):
        """卸载 = 把插件目录移到回收站（可恢复），不是永久删除"""
        p = self._op_row_data(row)
        inst = self._inst()
        if not p or not inst:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "卸载插件前请先停止 ComfyUI")
            return
        ans = QMessageBox.question(
            self, "卸载插件",
            f"卸载「{p['name']}」？\n\n"
            f"目录：{p['dir']}\n\n"
            "插件目录会被移入回收站（可恢复）。")
        if ans != QMessageBox.Yes:
            return
        try:
            self._trash_dir(p["dir"])
            self.lb_tip.setText(f"「{p['name']}」已移入回收站（重启 ComfyUI 生效）")
            self.refresh_list()
        except Exception as e:
            QMessageBox.warning(self, "卸载失败", str(e)[:300])

    @staticmethod
    def _trash_dir(path):
        """用 PowerShell SendTo recycling bin 移入回收站（可恢复，不永久删）"""
        import subprocess
        ps = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
            f"'{path}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=60, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "回收站操作失败").strip()[:300])

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
        w = InstallWorker(url, dest)
        w.sig_log.connect(self._log)
        w.sig_done.connect(lambda ok2, msg: (
            self._log(f"安装{'成功' if ok2 else '失败'}: {msg}"), self.refresh_list()))
        swap_worker(self, "_worker", w)
        w.start()

    def _open_dir(self):
        inst = self._inst()
        if inst:
            inst_lib.open_in_explorer(self._nodes_dir())
