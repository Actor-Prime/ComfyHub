# -*- coding: utf-8 -*-
"""启动模式页：内核快照 × 插件/依赖 profile 隔离
（每个工作流/插件集一个启动模式——省空间、不冲突，用户拍板方案 1③/2①/3②/4①/5①/6①）
"""
import os
import shutil

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QPlainTextEdit, QSplitter,
    QFormLayout, QLineEdit, QComboBox, QSpinBox, QListWidget, QListWidgetItem,
    QDialog, QFileDialog, QInputDialog, QCheckBox, QAbstractItemView,
)

from app import kernel_store, profiles, health_check
from app.config import VRAM_MODES
from app.workflow_scan import load_workflow, extract_class_types
from app.ui.theme import fit_table
from app.workers import swap_worker

LAUNCH_MODES = {
    "auto": "自动（新核基座/旧核换头）",
    "base_dir": "官方基座 --base-directory",
    "junction": "junction 换头",
}


# ---------------- 后台线程 ----------------
class _CreateWorker(QThread):
    """创建启动模式（可含 L2 venv / 插件安装 / 工作流拷入，耗时）"""
    sig_log = Signal(str)
    sig_done = Signal(bool, str)

    def __init__(self, name, kernel_version, deps_mode, plugins, wf_srcs):
        super().__init__()
        self.name = name
        self.kernel_version = kernel_version
        self.deps_mode = deps_mode
        self.plugins = plugins          # [(url, desc)]
        self.wf_srcs = wf_srcs          # [本地工作流 json 路径]

    def run(self):
        log = self.sig_log.emit
        try:
            profiles.create_profile(self.name, self.kernel_version,
                                   self.deps_mode, log=log)
            if self.deps_mode == "L2":
                profiles.create_l2_venv(self.name, log=log)
            done = set()
            for url, _desc in self.plugins:
                if url in done:
                    continue
                done.add(url)
                r = profiles.install_plugin(self.name, url, log=log)
                log(f"[创建] {r.get('plugin')}：{r.get('msg')}")
            p = profiles.load_profile(self.name)
            for src in self.wf_srcs:
                base = os.path.basename(src)
                dst = os.path.join(profiles.profile_dir(self.name),
                                   "workflows", base)
                shutil.copy2(src, dst)
                if base not in p["workflows"]:
                    p["workflows"].append(base)
                log(f"[创建] 工作流已拷入：{base}")
            profiles.save_profile(p)
            self.sig_done.emit(True, f"启动模式「{self.name}」创建完成")
        except Exception as e:
            self.sig_done.emit(False, f"创建失败：{e}")


class _HealthWorker(QThread):
    """体检：headless 起内核 → /object_info → 核对模式绑定工作流的节点类"""
    sig_log = Signal(str)
    sig_done = Signal(object)

    def __init__(self, profile):
        super().__init__()
        self.profile = profile

    def run(self):
        log = self.sig_log.emit
        p = self.profile
        try:
            inst = profiles.prepare_launch(p, log=log)
        except Exception as e:
            self.sig_done.emit({"error": f"准备启动失败：{e}"})
            return
        log(f"[体检] 临时端口启动内核（{p.get('kernel_version')}）…")
        classes = health_check.probe_classes(inst, timeout=180, log=log)
        if classes is None:
            self.sig_done.emit({"error": "内核未能启动（详见日志）"})
            return
        log(f"[体检] 内核加载节点类 {len(classes)} 个")
        wf_reports = []
        for wf_name in p.get("workflows", []):
            path = os.path.join(profiles.profile_dir(p["name"]),
                               "workflows", wf_name)
            try:
                ctypes = extract_class_types(load_workflow(path))
                missing = sorted(t for t in ctypes if t not in classes)
                wf_reports.append({"wf": wf_name, "total": len(ctypes),
                                   "missing": missing})
                if missing:
                    log(f"[体检] ❌ {wf_name}：缺 {len(missing)} 类 "
                        f"（{', '.join(missing[:6])}…）")
                else:
                    log(f"[体检] ✅ {wf_name}：{len(ctypes)} 类全部就绪")
            except Exception as e:
                wf_reports.append({"wf": wf_name, "error": str(e)})
        self.sig_done.emit({"classes": len(classes), "workflows": wf_reports})


class _ScanWorker(QThread):
    """解析工作流 → extension-node-map 反查插件建议（缓存未命中会拉网）"""
    sig_log = Signal(str)
    sig_done = Signal(object, object)

    def __init__(self, wf_path):
        super().__init__()
        self.wf_path = wf_path

    def run(self):
        from app import plugin_store
        try:
            classes = extract_class_types(load_workflow(self.wf_path))
            self.sig_log.emit(f"[扫描] 节点类 {len(classes)} 个，反查插件映射…")
            sugg = plugin_store.suggest_for_classes(classes,
                                                   log=self.sig_log.emit)
            self.sig_done.emit(classes, sugg)
        except Exception as e:
            self.sig_log.emit(f"[扫描] 失败：{e}")
            self.sig_done.emit([], [])


class _ListWorker(QThread):
    """模式列表数据后台取：list_profiles 会 walk 整个 profile 目录算体积
    （L2 venv / site_overlay 可能上万文件），同步跑会卡切页（2026-10-31 防回归）"""
    sig_done = Signal(object)

    def run(self):
        try:
            self.sig_done.emit(profiles.list_profiles())
        except Exception:
            self.sig_done.emit([])


# ---------------- 内核管理对话框 ----------------
class KernelManageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("内核快照管理")
        self.resize(760, 420)
        root = QVBoxLayout(self)

        self.tb = QTableWidget(0, 5)
        self.tb.setHorizontalHeaderLabels(
            ["版本", "路径", "Python", "基座标志", "状态"])
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        fit_table(self.tb)
        root.addWidget(self.tb, 1)

        bar = QHBoxLayout()
        b_imp = QPushButton("📥 从实例导入…")
        b_imp.setProperty("accent", True)
        b_imp.clicked.connect(self._import_instance)
        b_zip = QPushButton("从 zip 导入…")
        b_zip.clicked.connect(self._import_zip)
        b_del = QPushButton("删除选中")
        b_del.setProperty("danger", True)
        b_del.clicked.connect(self._remove)
        bar.addWidget(b_imp)
        bar.addWidget(b_zip)
        bar.addWidget(b_del)
        bar.addStretch()
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bar.addWidget(b_close)
        root.addLayout(bar)
        self.refresh()

    def refresh(self):
        ks = kernel_store.list_kernels()
        self.tb.setRowCount(len(ks))
        for r, k in enumerate(ks):
            vals = [
                k.get("version", ""),
                k.get("path", ""),
                k.get("python_path", ""),
                "有" if k.get("base_dir_flag") else "无",
                "缺失" if k.get("missing") else "正常",
            ]
            for c, v in enumerate(vals):
                self.tb.setItem(r, c, QTableWidgetItem(str(v)))

    def _import_instance(self):
        cpath = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 目录")
        if not cpath:
            return
        py, _ = QFileDialog.getOpenFileName(
            self, "选择该实例的 python.exe", "", "Python (python.exe)")
        if not py:
            return
        ver, ok = QInputDialog.getText(
            self, "内核版本标识", "给这份快照起个版本名：",
            text=os.path.basename(cpath.rstrip("\\")) or "kernel")
        if not ok or not ver.strip():
            return
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                kernel_store.import_from_instance(
                    cpath, py, ver.strip())
            finally:
                QApplication.restoreOverrideCursor()
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _import_zip(self):
        zp, _ = QFileDialog.getOpenFileName(
            self, "选择 ComfyUI 源码 zip", "", "Zip (*.zip)")
        if not zp:
            return
        py, _ = QFileDialog.getOpenFileName(
            self, "选择配套 python.exe", "", "Python (python.exe)")
        if not py:
            return
        ver, ok = QInputDialog.getText(
            self, "内核版本标识", "给这份快照起个版本名：",
            text=os.path.splitext(os.path.basename(zp))[0])
        if not ok or not ver.strip():
            return
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                kernel_store.import_from_zip(zp, py, ver.strip())
            finally:
                QApplication.restoreOverrideCursor()
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _remove(self):
        row = self.tb.currentRow()
        if row < 0:
            return
        ver = self.tb.item(row, 0).text()
        ans = QMessageBox.question(
            self, "删除内核", f"删除内核快照「{ver}」？（仅删快照，不影响源实例）")
        if ans != QMessageBox.Yes:
            return
        kernel_store.remove_kernel(ver, log=print)
        self.refresh()


# ---------------- 从工作流创建对话框（#76 主流程） ----------------
class FromWorkflowDialog(QDialog):
    """解析工作流 → 节点类反查插件 → 勾选安装 → 存为启动模式"""
    sig_log = Signal(str)

    def __init__(self, cfg, mainwin, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.mainwin = mainwin
        self.classes = []
        self.sugg = []
        self._worker = None
        self.setWindowTitle("从工作流创建启动模式")
        self.resize(820, 560)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        hb = QHBoxLayout()
        hb.addWidget(QLabel("工作流:"))
        self.cb_wf = QComboBox()
        self.cb_wf.setMinimumWidth(340)
        self.cb_wf.setPlaceholderText("—— 当前实例工作流目录 ——")
        hb.addWidget(self.cb_wf, 1)
        b_browse = QPushButton("浏览…")
        b_browse.clicked.connect(self._browse)
        hb.addWidget(b_browse)
        self.b_scan = QPushButton("🔍 扫描节点")
        self.b_scan.setProperty("accent", True)
        self.b_scan.clicked.connect(self._scan)
        hb.addWidget(self.b_scan)
        root.addLayout(hb)

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("启动模式名称（如：Qwen三视图）")
        form.addRow("模式名:", self.ed_name)
        self.cb_kernel = QComboBox()
        for k in kernel_store.list_kernels():
            if not k.get("missing"):
                self.cb_kernel.addItem(k["version"], userData=k["version"])
        form.addRow("内核快照:", self.cb_kernel)
        self.cb_deps = QComboBox()
        self.cb_deps.addItems(["L1", "L2"])
        self.cb_deps.setToolTip(
            "L1=PYTHONPATH overlay（推荐）；L2=轻 venv 共享大件（冲突时兜底）")
        form.addRow("依赖模式:", self.cb_deps)
        root.addLayout(form)

        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["安装", "节点类", "建议插件", "说明"])
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        fit_table(self.tb)
        root.addWidget(self.tb, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.log.setMaximumHeight(110)
        root.addWidget(self.log)

        bar = QHBoxLayout()
        lb = QLabel("勾选要装进该模式的插件（核心节点无需安装）")
        lb.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(lb)
        bar.addStretch()
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        bar.addWidget(b_cancel)
        self.b_create = QPushButton("⚡ 创建启动模式")
        self.b_create.setProperty("accent", True)
        self.b_create.setEnabled(False)
        self.b_create.clicked.connect(self._create)
        bar.addWidget(self.b_create)
        root.addLayout(bar)
        self._refresh_wf_list()

    # ---- 工作流选择 ----
    def _wf_dir(self):
        inst = self.mainwin.current_inst()
        if not inst:
            return ""
        return os.path.join(inst["comfyui_path"],
                            "user", "default", "workflows")

    def _refresh_wf_list(self):
        self.cb_wf.clear()
        self.cb_wf.addItem("—— 当前实例工作流目录 ——", userData="")
        wdir = self._wf_dir()
        if os.path.isdir(wdir):
            for root_dir, _d, files in os.walk(wdir):
                rel = os.path.relpath(root_dir, wdir)
                for f in files:
                    if f.lower().endswith(".json"):
                        full = os.path.join(root_dir, f)
                        display = f if rel == "." else f"{rel}/{f}"
                        self.cb_wf.addItem(display, userData=full)

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择工作流", "", "工作流 (*.json)")
        if f:
            self.cb_wf.addItem(os.path.basename(f), userData=f)
            self.cb_wf.setCurrentIndex(self.cb_wf.count() - 1)

    # ---- 扫描 ----
    def _scan(self):
        path = self.cb_wf.currentData()
        if not path:
            QMessageBox.information(self, "扫描", "请先选择工作流文件")
            return
        if self._worker and self._worker.isRunning():
            return
        self.b_scan.setEnabled(False)
        w = _ScanWorker(path)
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_scanned)
        swap_worker(self, "_worker", w)
        w.start()

    def _on_scanned(self, classes, sugg):
        self.classes, self.sugg = classes, sugg
        self.b_scan.setEnabled(True)
        # 按仓库聚合：url -> [节点类]
        by_repo = {}
        order = []
        for s in sugg:
            urls = s.get("urls") or []
            url = urls[0] if urls else ""
            key = url or f"__none__{s.get('class')}"
            if key not in by_repo:
                by_repo[key] = {"url": url, "classes": [],
                                "desc": s.get("desc", "")}
                order.append(key)
            by_repo[key]["classes"].append(s.get("class", ""))
        self.tb.setRowCount(len(order))
        for r, key in enumerate(order):
            info = by_repo[key]
            ck = QCheckBox()
            ck.setChecked(bool(info["url"]))
            self.tb.setCellWidget(r, 0, ck)
            self.tb.setItem(r, 1, QTableWidgetItem(", ".join(info["classes"])))
            self.tb.setItem(r, 2, QTableWidgetItem(
                info["url"] or "（未在目录找到，需手动装）"))
            self.tb.setItem(r, 3, QTableWidgetItem(info["desc"][:80]))
        n_core = len(classes) - len(sugg)
        self._log(f"[扫描] 共 {len(classes)} 个节点类：核心/已具备 {n_core}，"
                  f"需补插件 {len(sugg)}（聚合 {len(order)} 个仓库）")
        self.b_create.setEnabled(True)

    # ---- 创建 ----
    def _create(self):
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.information(self, "创建", "请填写启动模式名称")
            return
        if profiles.load_profile(name) is not None:
            QMessageBox.warning(self, "创建", f"「{name}」已存在")
            return
        if not self.cb_kernel.currentData():
            QMessageBox.information(
                self, "创建", "没有可用内核快照，请先在内核管理导入")
            return
        plugins = []
        for r in range(self.tb.rowCount()):
            ck = self.tb.cellWidget(r, 0)
            if ck and ck.isChecked():
                url = self.tb.item(r, 2).text()
                if url.startswith("http"):
                    plugins.append((url, self.tb.item(r, 3).text()))
        src = self.cb_wf.currentData()
        wf_srcs = [src] if src else []
        self.b_create.setEnabled(False)
        self.b_scan.setEnabled(False)
        w = _CreateWorker(name, self.cb_kernel.currentData(),
                          self.cb_deps.currentText(), plugins, wf_srcs)
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_created)
        swap_worker(self, "_worker", w)
        w.start()
        self._log(f"[创建] 开始：模式「{name}」，插件 {len(plugins)} 个…")

    def _on_created(self, ok, msg):
        self.b_create.setEnabled(True)
        self.b_scan.setEnabled(True)
        self._log(f"[创建] {msg}")
        if ok:
            QMessageBox.information(self, "创建完成", msg)
            self.accept()
        else:
            QMessageBox.warning(self, "创建失败", msg)

    def _log(self, msg):
        self.log.appendPlainText(str(msg))


# ---------------- 主页面 ----------------
class ProfilesPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._launched = {}      # name -> prepare_launch 产物（停靠用）
        self._loading = False
        self._build()
        self.refresh()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        t = QLabel("⚡ 启动模式 · 快照隔离")
        t.setStyleSheet("font-size:17px; font-weight:bold; color:#fff;")
        root.addWidget(t)
        sub = QLabel("基础内核只存一份快照；每个工作流/插件集一个启动模式，"
                     "插件与依赖互相隔离、模型全局共享")
        sub.setStyleSheet("color:#9aa4b2;")
        root.addWidget(sub)

        # 工具栏
        bar = QHBoxLayout()
        b_wf = QPushButton("🧬 从工作流创建…")
        b_wf.setProperty("accent", True)
        b_wf.clicked.connect(self._from_workflow)
        b_new = QPushButton("新建空白…")
        b_new.clicked.connect(self._new_profile)
        b_dup = QPushButton("复制…")
        b_dup.clicked.connect(self._duplicate)
        b_del = QPushButton("删除")
        b_del.setProperty("danger", True)
        b_del.clicked.connect(self._delete)
        b_kern = QPushButton("🔧 内核管理…")
        b_kern.clicked.connect(self._kernels)
        b_ref = QPushButton("🔄")
        b_ref.setFixedWidth(34)
        b_ref.setToolTip("刷新模式列表")
        b_ref.clicked.connect(self.refresh)
        for wdg in (b_wf, b_new, b_dup, b_del, b_kern):
            bar.addWidget(wdg)
        bar.addStretch()
        bar.addWidget(b_ref)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        # 左：模式列表
        self.tb = QTableWidget(0, 6)
        self.tb.setHorizontalHeaderLabels(
            ["模式名", "内核", "依赖", "端口", "插件", "占用MB"])
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.tb)

        # 右：详情表单
        right = QWidget()
        form = QFormLayout(right)
        form.setContentsMargins(8, 4, 0, 0)
        self.cb_kernel = QComboBox()
        self.cb_kernel.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("内核快照:", self.cb_kernel)
        self.cb_deps = QComboBox()
        self.cb_deps.addItems(["L1", "L2"])
        self.cb_deps.setToolTip(
            "L1=PYTHONPATH overlay 压过共享包（推荐）；"
            "L2=轻 venv 共享 torch 大件（依赖冲突兜底）")
        self.cb_deps.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("依赖模式:", self.cb_deps)
        self.cb_launch = QComboBox()
        for k, v in LAUNCH_MODES.items():
            self.cb_launch.addItem(v, userData=k)
        self.cb_launch.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("隔离方式:", self.cb_launch)
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        self.sp_port.valueChanged.connect(self._mark_dirty)
        form.addRow("端口:", self.sp_port)
        self.cb_vram = QComboBox()
        self.cb_vram.addItems(VRAM_MODES.keys())
        self.cb_vram.currentIndexChanged.connect(self._mark_dirty)
        form.addRow("显存优化:", self.cb_vram)
        self.ed_args = QLineEdit()
        self.ed_args.setPlaceholderText("例如: --reserve-vram 2")
        self.ed_args.textChanged.connect(self._mark_dirty)
        form.addRow("额外参数:", self.ed_args)
        hb_models = QHBoxLayout()
        self.ed_models = QLineEdit()
        self.ed_models.setPlaceholderText("空 = 全局共享模型目录")
        self.ed_models.textChanged.connect(self._mark_dirty)
        hb_models.addWidget(self.ed_models, 1)
        b_m = QPushButton("…")
        b_m.setFixedWidth(30)
        b_m.clicked.connect(self._pick_models)
        hb_models.addWidget(b_m)
        form.addRow("模型目录:", hb_models)
        self.lb_plugins = QLabel("—")
        self.lb_plugins.setWordWrap(True)
        self.lb_plugins.setStyleSheet("color:#9aa4b2;")
        form.addRow("已装插件:", self.lb_plugins)
        hb_wf = QHBoxLayout()
        self.lw_wf = QListWidget()
        self.lw_wf.setMaximumHeight(88)
        hb_wf.addWidget(self.lw_wf, 1)
        vb_wf = QVBoxLayout()
        b_wadd = QPushButton("+")
        b_wadd.setFixedWidth(30)
        b_wadd.setToolTip("拷入工作流 JSON")
        b_wadd.clicked.connect(self._add_workflow)
        b_wdel = QPushButton("-")
        b_wdel.setFixedWidth(30)
        b_wdel.setToolTip("移除绑定（不删文件）")
        b_wdel.clicked.connect(self._del_workflow)
        vb_wf.addWidget(b_wadd)
        vb_wf.addWidget(b_wdel)
        vb_wf.addStretch()
        hb_wf.addLayout(vb_wf)
        form.addRow("绑定工作流:", hb_wf)
        self.ed_notes = QPlainTextEdit()
        self.ed_notes.setMaximumHeight(60)
        self.ed_notes.textChanged.connect(self._mark_dirty)
        form.addRow("备注:", self.ed_notes)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        # 操作条
        ops = QHBoxLayout()
        self.lb_state = QLabel("—")
        self.lb_state.setStyleSheet("color:#9aa4b2;")
        ops.addWidget(self.lb_state)
        ops.addStretch()
        self.b_save = QPushButton("💾 保存修改")
        self.b_save.clicked.connect(self._save)
        self.b_health = QPushButton("🩺 体检")
        self.b_health.setToolTip("临时端口起内核，核对绑定工作流节点是否全可加载")
        self.b_health.clicked.connect(self._health)
        self.b_start = QPushButton("▶ 启动")
        self.b_start.setProperty("accent", True)
        self.b_start.clicked.connect(self._start)
        self.b_stop = QPushButton("⏹ 停止")
        self.b_stop.setProperty("danger", True)
        self.b_stop.clicked.connect(self._stop)
        for wdg in (self.b_save, self.b_health, self.b_start, self.b_stop):
            ops.addWidget(wdg)
        root.addLayout(ops)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.log.setMaximumHeight(140)
        root.addWidget(self.log)

    # ---------- 数据 ----------
    def refresh(self):
        w = _ListWorker()
        w.sig_done.connect(self._on_rows)
        swap_worker(self, "_list_worker", w)
        w.start()
        # 内核下拉（轻，同步刷）
        self._loading = True
        self.cb_kernel.blockSignals(True)
        self.cb_kernel.clear()
        for k in kernel_store.list_kernels():
            mark = "（缺失）" if k.get("missing") else ""
            self.cb_kernel.addItem(k["version"] + mark,
                                   userData=k["version"])
        self.cb_kernel.blockSignals(False)
        self._loading = False
        self._sync_buttons()

    def _on_rows(self, rows):
        cur = self._current_name()
        self._loading = True
        self.tb.setRowCount(len(rows))
        sel_row = -1
        for r, p in enumerate(rows):
            name = p.get("name", "")
            if p.get("broken"):
                vals = [name, "—(损坏)—", "", "", "", ""]
            else:
                vals = [name, p.get("kernel_version", ""),
                        p.get("deps_mode", ""), str(p.get("port", "")),
                        str(len(p.get("plugins") or [])),
                        str(p.get("size_mb", ""))]
            for c, v in enumerate(vals):
                self.tb.setItem(r, c, QTableWidgetItem(v))
            if name == cur:
                sel_row = r
        self._loading = False
        if sel_row >= 0:
            self.tb.selectRow(sel_row)
        elif rows:
            self.tb.selectRow(0)
        self._sync_buttons()

    def _current_name(self):
        row = self.tb.currentRow()
        if row < 0:
            return ""
        it = self.tb.item(row, 0)
        return it.text() if it else ""

    def _current_profile(self):
        name = self._current_name()
        return profiles.load_profile(name) if name else None

    def _on_select(self):
        if self._loading:
            return
        p = self._current_profile()
        self._loading = True
        if p is None:
            self.lb_state.setText("—")
        else:
            self.cb_kernel.setCurrentIndex(max(
                0, self.cb_kernel.findData(p.get("kernel_version"))))
            self.cb_deps.setCurrentText(p.get("deps_mode", "L1"))
            idx = self.cb_launch.findData(p.get("launch_mode", "auto"))
            self.cb_launch.setCurrentIndex(max(0, idx))
            self.sp_port.setValue(int(p.get("port") or 8310))
            self.cb_vram.setCurrentText(p.get("vram_mode", "自动"))
            self.ed_args.setText(p.get("args", ""))
            self.ed_models.setText(p.get("models_dir", ""))
            plugs = p.get("plugins") or []
            self.lb_plugins.setText(
                "、".join(x.get("name", "?") for x in plugs) if plugs else "无")
            self.lw_wf.clear()
            for w in p.get("workflows") or []:
                self.lw_wf.addItem(QListWidgetItem(w))
            self.ed_notes.setPlainText(p.get("notes", ""))
            self.lb_state.setText(
                f"模式「{p['name']}」· 创建于 {p.get('created', '?')}")
        self._loading = False
        self._sync_buttons()

    def _mark_dirty(self, *_):
        if not self._loading:
            self.b_save.setText("💾 保存修改 *")

    def _sync_buttons(self):
        name = self._current_name()
        has = bool(name)
        self.b_save.setEnabled(has)
        self.b_health.setEnabled(has)
        self.b_start.setEnabled(has)
        self.b_stop.setEnabled(has)
        self.b_save.setText("💾 保存修改")

    def _log(self, msg):
        self.log.appendPlainText(str(msg))

    # ---------- 增删改 ----------
    def _new_profile(self):
        ks = [k for k in kernel_store.list_kernels() if not k.get("missing")]
        if not ks:
            QMessageBox.information(
                self, "新建启动模式",
                "还没有内核快照。\n请先点「🔧 内核管理」从现有实例导入一份基础内核。")
            return
        name, ok = QInputDialog.getText(self, "新建启动模式", "模式名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        if profiles.load_profile(name) is not None:
            QMessageBox.warning(self, "新建", f"「{name}」已存在")
            return
        kver, ok2 = QInputDialog.getItem(
            self, "选择内核快照", "内核版本：",
            [k["version"] for k in ks], 0, False)
        if not ok2:
            return
        try:
            p = profiles.create_profile(name, kver, "L1", log=self._log)
            self.refresh()
            self._log(f"[启动模式] 已创建空白模式「{name}」（内核 {kver}）")
        except Exception as e:
            QMessageBox.warning(self, "新建失败", str(e))

    def _duplicate(self):
        name = self._current_name()
        if not name:
            return
        dst, ok = QInputDialog.getText(
            self, "复制启动模式", "新模式名称：", text=name + "_副本")
        if not ok or not dst.strip() or dst.strip() == name:
            return
        try:
            profiles.duplicate_profile(name, dst.strip(), log=self._log)
            self.refresh()
        except Exception as e:
            QMessageBox.warning(self, "复制失败", str(e))

    def _delete(self):
        name = self._current_name()
        if not name:
            return
        ans = QMessageBox.question(
            self, "删除启动模式",
            f"删除「{name}」？\n其 custom_nodes/依赖/绑定工作流一并删除（模型共享不受影响）")
        if ans != QMessageBox.Yes:
            return
        profiles.delete_profile(name, log=self._log)
        self.refresh()

    def _kernels(self):
        dlg = KernelManageDialog(self)
        dlg.exec()
        self.refresh()

    def _from_workflow(self):
        dlg = FromWorkflowDialog(self.cfg, self.mainwin, self)
        dlg.sig_log.connect(self._log)
        dlg.exec()
        self.refresh()

    def _pick_models(self):
        d = QFileDialog.getExistingDirectory(self, "选择模型目录（留空=共享）")
        if d:
            self.ed_models.setText(d)

    def _add_workflow(self):
        p = self._current_profile()
        if p is None:
            return
        f, _ = QFileDialog.getOpenFileName(
            self, "选择工作流 JSON", "", "工作流 (*.json)")
        if not f:
            return
        base = os.path.basename(f)
        dst = os.path.join(profiles.profile_dir(p["name"]), "workflows", base)
        shutil.copy2(f, dst)
        if base not in p["workflows"]:
            p["workflows"].append(base)
        profiles.save_profile(p)
        self._on_select()
        self._log(f"[启动模式] 工作流已拷入「{p['name']}」：{base}")

    def _del_workflow(self):
        p = self._current_profile()
        row = self.lw_wf.currentRow()
        if p is None or row < 0:
            return
        name = self.lw_wf.item(row).text()
        p["workflows"] = [w for w in p["workflows"] if w != name]
        profiles.save_profile(p)
        self._on_select()

    def _save(self):
        p = self._current_profile()
        if p is None:
            return
        p["kernel_version"] = self.cb_kernel.currentData() or ""
        p["deps_mode"] = self.cb_deps.currentText()
        p["launch_mode"] = self.cb_launch.currentData() or "auto"
        p["port"] = self.sp_port.value()
        p["vram_mode"] = self.cb_vram.currentText()
        p["args"] = self.ed_args.text().strip()
        p["models_dir"] = self.ed_models.text().strip()
        p["notes"] = self.ed_notes.toPlainText()
        profiles.save_profile(p)
        self._log(f"[启动模式] 已保存「{p['name']}」")
        self.refresh()

    # ---------- 体检 / 启停 ----------
    def _health(self):
        p = self._current_profile()
        if p is None:
            return
        if self.mainwin.procs.is_running(self._inst_id(p)):
            QMessageBox.information(self, "体检", "该模式正在运行，体检请先停止")
            return
        self.b_health.setEnabled(False)
        w = _HealthWorker(p)
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_health)
        swap_worker(self, "_worker", w)
        w.start()
        self._log(f"[体检] 开始体检「{p['name']}」…")

    def _on_health(self, report):
        self.b_health.setEnabled(True)
        if "error" in report:
            self._log(f"[体检] ❌ {report['error']}")
            QMessageBox.warning(self, "体检", report["error"])
            return
        lines = [f"内核加载节点类 {report['classes']} 个"]
        bad = 0
        for wr in report.get("workflows", []):
            if "error" in wr:
                lines.append(f"· {wr['wf']}：解析失败 {wr['error']}")
                bad += 1
            elif wr["missing"]:
                lines.append(f"· {wr['wf']}：缺 {len(wr['missing'])} 类 — "
                             f"{', '.join(wr['missing'][:8])}")
                bad += 1
            else:
                lines.append(f"· {wr['wf']}：✅ {wr['total']} 类全就绪")
        if not report.get("workflows"):
            lines.append("（未绑定工作流，仅体检内核加载）")
        msg = "\n".join(lines)
        self._log("[体检] 完成")
        if bad:
            QMessageBox.warning(self, "体检结果", msg)
        else:
            QMessageBox.information(self, "体检结果", msg)

    def _inst_id(self, p):
        return {"id": "profile_" + profiles._slug(p["name"])}

    def _start(self):
        p = self._current_profile()
        if p is None:
            return
        try:
            inst = profiles.prepare_launch(p, log=self._log)
        except Exception as e:
            QMessageBox.warning(self, "启动失败", str(e))
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.information(self, "启动", "该模式已在运行")
            return
        self._launched[p["name"]] = inst
        self.mainwin.procs.get(inst).start(inst)
        self._log(f"[启动模式] ▶ 已启动「{p['name']}」"
                  f"（端口 {inst.get('port')}）")

    def _stop(self):
        name = self._current_name()
        inst = self._launched.get(name) or self._inst_id(
            self._current_profile() or {"name": name})
        if self.mainwin.procs.is_running(inst):
            self.mainwin.procs.get(inst).stop()
            self._log(f"[启动模式] ⏹ 已停止「{name}」")
        else:
            self._log(f"[启动模式] 「{name}」未在运行")

    # ---------- 页面钩子 ----------
    def on_show(self):
        self.refresh()

    def on_instance_changed(self):
        pass
