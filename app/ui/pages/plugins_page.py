# -*- coding: utf-8 -*-
"""插件管理页

两个分区（Tab）：
  · 已安装 —— 启停 / 启动模式（插件方案）/ 节点冲突检查 / 批量更新
  · 插件市场 —— 从 ComfyUI-Manager 官方清单（6000+）搜索并一键安装
"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog, QComboBox,
    QTabWidget, QLineEdit, QAbstractItemView,
)

from app import instance as inst_lib
from app import plugin_profiles as pp
from app import plugin_store as store
from app.config import save_config


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


# ============================================================ 页面
class PluginsPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._conflict_map = {}
        self._catalog = []
        self._catalog_meta = {}
        self._catalog_worker = None
        self._last_market_rows = []
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

        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["插件名", "Git 仓库", "状态", "节点冲突"])
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb.setAlternatingRowColors(True)
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

        self.lb_market_stat = QLabel("尚未加载插件清单（切到此页会自动加载，首次约 10 秒）")
        self.lb_market_stat.setStyleSheet("color:#9aa4b2;")
        root.addWidget(self.lb_market_stat)

        self.tb_market = QTableWidget(0, 4)
        self.tb_market.setHorizontalHeaderLabels(["插件", "作者", "说明", "操作"])
        hh = self.tb_market.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tb_market.setColumnWidth(0, 260)
        self.tb_market.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb_market.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb_market.setAlternatingRowColors(True)
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
        self.tb.setRowCount(len(rows))
        pr = self._current_profile()
        want = set((pr or {}).get("enabled") or [])
        for i, p in enumerate(rows):
            name = p["name"]
            item = QTableWidgetItem(name)
            if pr is not None and (name in want) != p["enabled"]:
                item.setForeground(Qt.gray)
                item.setToolTip("与当前模式不一致，点「▶ 应用」同步")
            self.tb.setItem(i, 0, item)
            self.tb.setItem(i, 1, QTableWidgetItem("是" if p["git"] else "否"))
            st = QTableWidgetItem("启用" if p["enabled"] else "已禁用")
            st.setForeground(Qt.green if p["enabled"] else Qt.gray)
            self.tb.setItem(i, 2, st)
            cf = self._conflict_map.get(name)
            if cf:
                c = QTableWidgetItem("⚠ " + "、".join(cf[:3]))
                c.setForeground(Qt.red)
                c.setToolTip("与其他插件重复的节点名：" + "、".join(cf))
                self.tb.setItem(i, 3, c)
            else:
                self.tb.setItem(i, 3, QTableWidgetItem(""))
        if not rows:
            self.lb_tip.setText("这个实例还没有插件 —— 切到「插件市场」搜一个装上吧。")
        if not rows:
            self.lb_tip.setText("这个实例还没有插件 —— 切到「插件市场」搜一个装上吧。")
        # 市场列表的「已安装」标记跟着刷新
        if self._catalog and self._last_market_rows:
            self._render_market(self._last_market_rows)

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
        self._worker = ApplyProfileWorker(inst["comfyui_path"], enabled)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(self._on_applied)
        self._worker.start()

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
        self._worker = ScanDupWorker(inst["comfyui_path"], only_enabled=True)
        self._worker.sig_log.connect(lambda m: None)
        self._worker.sig_done.connect(self._on_dup_done)
        self._worker.start()

    def _on_dup_done(self, r):
        conflicts = r.get("conflicts") or {}
        self._conflict_map = {}
        for node, owners in conflicts.items():
            for o in owners:
                self._conflict_map.setdefault(o, []).append(node)
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
        self.lb_tip.setText(f"⚠️ 发现 {len(conflicts)} 组节点冲突，见表格「节点冲突」列")
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
        self._catalog_worker = CatalogWorker(force=force)
        self._catalog_worker.sig_log.connect(self._log)
        self._catalog_worker.sig_done.connect(self._on_catalog)
        self._catalog_worker.sig_error.connect(self._on_catalog_err)
        self._catalog_worker.start()

    def _on_catalog(self, nodes, meta):
        self._catalog = nodes
        self._catalog_meta = meta
        self.b_refresh.setEnabled(True)
        src = meta.get("source", "?")
        cached = "缓存" if meta.get("cached") else "在线"
        self.lb_market_stat.setText(
            f"共 {len(nodes)} 个插件（来源 {src}·{cached}）　输入关键词后回车搜索")
        self._do_search()

    def _on_catalog_err(self, msg):
        self.b_refresh.setEnabled(True)
        self.lb_market_stat.setText(f"清单加载失败：{msg}")
        QMessageBox.warning(self, "插件市场",
                            f"{msg}\n\n可在「系统设置」里配置代理后点「刷新清单」重试。")

    def _do_search(self):
        if not self._catalog:
            return
        kw = self.ed_search.text().strip()
        rows = store.search(self._catalog, kw, limit=400)
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
            t.setToolTip(store.install_url(n))
            self.tb_market.setItem(i, 0, t)
            self.tb_market.setItem(i, 1, QTableWidgetItem(n.get("author") or ""))
            desc = (n.get("description") or "").replace("\n", " ")
            d = QTableWidgetItem(desc)
            d.setToolTip(desc)
            self.tb_market.setItem(i, 2, d)
            guess = store.install_url(n).rstrip("/").split("/")[-1]
            if guess.endswith(".git"):
                guess = guess[:-4]
            if guess in installed:
                it = QTableWidgetItem("已安装 ✓")
                it.setForeground(Qt.green)
                self.tb_market.setItem(i, 3, it)
            else:
                btn = QPushButton("安装")
                btn.setProperty("success", True)
                btn.clicked.connect(lambda _c=False, node=n: self._install_from_store(node))
                self.tb_market.setCellWidget(i, 3, btn)

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
        self._worker = InstallWorker(url, dest)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(lambda ok, msg: self._on_installed(ok, msg, name))
        self._worker.start()

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

    def _toggle(self, index):
        inst = self._inst()
        if not inst:
            return
        name = self.tb.item(index.row(), 0).text()
        src = os.path.join(self._nodes_dir(), name)
        dst = src + ".disabled" if os.path.isdir(src) else src[:-len(".disabled")]
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
