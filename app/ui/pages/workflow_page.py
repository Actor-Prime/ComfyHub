# -*- coding: utf-8 -*-
"""工作流页：扫描分析 → 生成安装计划 → 一键执行（对标管理大师 workflow 智能安装）"""
import os
import shutil

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QFileDialog, QMessageBox, QProgressBar, QGroupBox, QCheckBox, QComboBox,
    QTabWidget,
)

from app import workflow_scan, installer
from app.installer import PlanExecutor
from app.workers import swap_worker


class ScanWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, wf_path, ai_settings=None):
        super().__init__()
        self.a, self.b = comfyui_path, wf_path
        self.ai_settings = ai_settings

    def run(self):
        try:
            res = workflow_scan.analyze_workflow(
                self.a, self.b, log=lambda m: self.sig_log.emit(str(m)))
            # 计划也在本线程生成：插件映射/模型树/可选 AI 都是网络或重 IO，
            # 放 UI 线程会复现切页卡顿（2026-10-31 卡顿修复的教训）
            plan = installer.plan_from_scan(
                res, log=lambda m: self.sig_log.emit(str(m)),
                resolve_online=True, ai_settings=self.ai_settings)
            self.sig_done.emit({"scan": res, "plan": plan})
        except Exception as e:
            self.sig_log.emit(f"扫描失败: {e}")
            self.sig_done.emit({})


class ModelDLWorker(QThread):
    """单模型断点续传下载（串行，hf-mirror 并发易被掐 TLS）"""
    sig_log = Signal(str)
    sig_progress = Signal(int, int)
    sig_done = Signal(bool)

    def __init__(self, source, dest_dir):
        super().__init__()
        self.source, self.dest_dir = source, dest_dir
        self._cancel = False

    def run(self):
        try:
            from app import model_resolve
            s = self.source
            model_resolve.download_with_retry(
                s["repo"], s["path"], self.dest_dir,
                progress_cb=lambda d, t: self.sig_progress.emit(d, t),
                cancel_flag=lambda: self._cancel,
                log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(True)
        except KeyboardInterrupt:
            self.sig_log.emit("已取消下载（.part 已保留可续传）")
            self.sig_done.emit(False)
        except Exception as e:
            self.sig_log.emit(f"下载失败: {e}")
            self.sig_done.emit(False)

    def stop(self):
        self._cancel = True


class ExecWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, inst, steps):
        super().__init__()
        self.inst, self.steps = inst, steps
        self.executor = PlanExecutor(inst)

    def run(self):
        try:
            res = self.executor.run(self.steps, log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(res)
        except Exception as e:
            self.sig_log.emit(f"执行异常: {e}")
            self.sig_done.emit({"done": 0, "total": len(self.steps),
                                "failed_step": 0, "snapshot": ""})

    def stop(self):
        self.executor.stop()


class WorkflowPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self.scan_result = {}
        self.plan = None
        self._worker = None
        self._exec = None
        self._build()

    def _build(self):
        """双 Tab：🧬 智能安装 + ⚡ 启动模式
        （2026-10-31 用户指定：启动模式并入工作流页，不单列导航）"""
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        tabs = QTabWidget()
        self.tabs = tabs
        tabs.addTab(self._build_smart_tab(), "🧬 工作流 · 智能安装")
        from app.ui.pages.profiles_page import ProfilesPage
        self.profiles = ProfilesPage(self.cfg, self.mainwin)
        tabs.addTab(self.profiles, "⚡ 启动模式")
        root.addWidget(tabs)
        self._setup_drops()

    def _build_smart_tab(self):
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(12, 10, 12, 10)

        # 步骤 1：选择工作流（实例工作流目录优先）
        hb = QHBoxLayout()
        lb_src = QLabel("来源:")
        lb_src.setStyleSheet("color:#9aa4b2;")
        hb.addWidget(lb_src)
        self.cb_wf = QComboBox()
        self.cb_wf.setMinimumWidth(320)
        self.cb_wf.setPlaceholderText("—— 从当前实例的工作流目录选择 ——")
        self.cb_wf.currentIndexChanged.connect(self._on_wf_pick)
        hb.addWidget(self.cb_wf, 1)
        b_reload = QPushButton("🔄")
        b_reload.setFixedWidth(34)
        b_reload.setToolTip("刷新实例工作流列表")
        b_reload.clicked.connect(self._refresh_wf_list)
        hb.addWidget(b_reload)
        b_pick = QPushButton("浏览本地…")
        b_pick.clicked.connect(self._pick)
        hb.addWidget(b_pick)
        b_scan = QPushButton("🔍 扫描依赖")
        b_scan.setProperty("accent", True)
        b_scan.clicked.connect(self._scan)
        hb.addWidget(b_scan)
        root.addLayout(hb)

        # 工作流文件路径：拖入窗口即在此落位（2026-10-31 拖放交互）。
        # 注：此框此前只被引用从未创建，扫描/浏览/下拉三个入口都在抛
        # AttributeError——本次一并修复
        self.ed_wf = QLineEdit()
        self.ed_wf.setPlaceholderText(
            "💡 直接把 .json 工作流文件拖进窗口即可自动扫描；也可用上方下拉或「浏览本地…」选择")
        root.addWidget(self.ed_wf)

        # 步骤 2：结果
        split = QHBoxLayout()
        # 左：插件需求表
        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["状态", "条目", "来源/仓库", "操作"])
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        split.addWidget(self.tb, 2)
        # 右：日志
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        split.addWidget(self.log, 1)
        root.addLayout(split, 1)

        # 步骤 3：执行
        bar = QHBoxLayout()
        self.lb_plan = QLabel("尚未生成计划")
        self.lb_plan.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(self.lb_plan)
        bar.addStretch()
        self.ck_models = QCheckBox("同时下载模型（有直链的才有效）")
        self.ck_models.setChecked(True)
        self.ck_models.toggled.connect(self._sync_exec_btn)
        bar.addWidget(self.ck_models)
        self.b_exec = QPushButton("⚡ 执行安装计划")
        self.b_exec.setProperty("accent", True)
        self.b_exec.setEnabled(False)
        self.b_exec.clicked.connect(self._execute)
        # 2026-11-01：一键把「升级内核 + 装插件 + 下模型」合成一条队列执行
        self.b_ai = QPushButton("🤖 AI 补全并执行")
        self.b_ai.setEnabled(False)
        self.b_ai.setToolTip("按「内核 → 插件 → 模型」顺序补齐扫描出的全部缺口，"
                             "执行前自动快照")
        self.b_ai.clicked.connect(self._ai_solve)
        b_stop = QPushButton("停止")
        b_stop.setProperty("danger", True)
        b_stop.clicked.connect(self._stop)
        b_imp = QPushButton("导入步骤清单…")
        b_imp.clicked.connect(self._import_plan)
        bar.addWidget(b_imp)
        bar.addWidget(b_stop)
        bar.addWidget(self.b_ai)
        bar.addWidget(self.b_exec)
        root.addLayout(bar)

        self.bar_prog = QProgressBar()
        self.bar_prog.setVisible(False)
        root.addWidget(self.bar_prog)
        return w

    # ---------- 拖放（2026-10-31 用户要求：拖 .json 进窗口即用） ----------
    def _setup_drops(self):
        """整页接受拖放；子级文本控件让出拖放，让事件冒泡到本页统一处理
        （QLineEdit/QPlainTextEdit 默认吃文本拖放，会把文件路径插成文本）"""
        self.setAcceptDrops(True)
        for wdg in (self.findChildren(QLineEdit)
                    + self.findChildren(QPlainTextEdit)
                    + self.findChildren(QComboBox)):
            wdg.setAcceptDrops(False)

    @staticmethod
    def _drop_paths(md):
        """拖放载荷里的全部本地 .json 工作流文件（支持多选拖入）
        toLocalFile 返回正斜杠路径，normpath 归一成 Windows 原生分隔符"""
        out = []
        if md is None or not md.hasUrls():
            return out
        for u in md.urls():
            p = u.toLocalFile()
            if not p:
                continue
            p = os.path.normpath(p)
            if os.path.isfile(p) and p.lower().endswith(".json"):
                out.append(p)
        return out

    @staticmethod
    def _drop_path(md):
        """载荷里第一个 .json（兼容旧调用）"""
        ps = WorkflowPage._drop_paths(md)
        return ps[0] if ps else None

    def _import_dropped(self, path):
        """把拖入的工作流「加进」实例工作流库（user/default/workflows，
        即「来源」下拉的数据源）。2026-10-31 用户指定：拖上去 = 加进去。
        同名同内容 → 已在列表中跳过；同名不同内容 → 覆盖更新
        （拖放=副本语义，用户源文件不受影响）；无实例 → 退回仅选中。"""
        wdir = self._workflows_dir()
        if not wdir:
            return path
        os.makedirs(wdir, exist_ok=True)
        src = os.path.normcase(os.path.normpath(path))
        dst = os.path.join(wdir, os.path.basename(path))
        if os.path.normcase(os.path.normpath(dst)) == src:
            return dst              # 拖的本来就是列表内文件
        import filecmp
        if os.path.isfile(dst) and filecmp.cmp(path, dst, shallow=False):
            self._log(f"── 已在列表中：{os.path.basename(dst)} ──")
            return dst
        existed = os.path.isfile(dst)
        shutil.copy2(path, dst)
        verb = "已更新" if existed else "已加入"
        self._log(f"── {verb}工作流列表：{os.path.basename(dst)} ──")
        return dst

    def _select_wf(self, path):
        """在「来源」下拉中选中指定工作流（找不到就直接填路径框）"""
        want = os.path.normcase(os.path.normpath(path))
        for i in range(self.cb_wf.count()):
            d = self.cb_wf.itemData(i)
            if d and os.path.normcase(os.path.normpath(d)) == want:
                self.cb_wf.setCurrentIndex(i)   # 触发 _on_wf_pick 填 ed_wf
                return
        self.ed_wf.setText(path)

    def _set_drop_hint(self, on):
        self.ed_wf.setStyleSheet(
            "QLineEdit{border:2px dashed #4c8dff; background:#1b2430;}"
            if on else "")

    def dragEnterEvent(self, e):
        if self._drop_paths(e.mimeData()):
            e.acceptProposedAction()
            self._set_drop_hint(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._drop_paths(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, _e):
        self._set_drop_hint(False)

    def dropEvent(self, e):
        self._set_drop_hint(False)
        paths = self._drop_paths(e.mimeData())
        if not paths:
            e.ignore()
            return
        e.acceptProposedAction()
        self.tabs.setCurrentIndex(0)      # 确保停在「智能安装」Tab
        first = None
        for p in paths:
            got = self._import_dropped(p)   # 拖入 = 加进「来源」列表
            if first is None:
                first = got
        self._refresh_wf_list()             # 列表立刻可见
        if first:
            self._select_wf(first)
            self._scan()                    # 加完直接扫，一步到位

    # ---------- 交互 ----------
    def _workflows_dir(self):
        inst = self._current_inst()
        if not inst:
            return ""
        return os.path.join(inst["comfyui_path"],
                            "user", "default", "workflows")

    def _refresh_wf_list(self):
        """列出当前实例工作流目录（含子目录）的 JSON 文件"""
        self.cb_wf.blockSignals(True)
        self.cb_wf.clear()
        self.cb_wf.addItem("—— 从当前实例的工作流目录选择 ——", userData="")
        wdir = self._workflows_dir()
        n = 0
        if os.path.isdir(wdir):
            for root_dir, _dirs, files in os.walk(wdir):
                rel = os.path.relpath(root_dir, wdir)
                for f in files:
                    if f.lower().endswith(".json"):
                        full = os.path.join(root_dir, f)
                        display = f if rel == "." else f"{rel}/{f}"
                        self.cb_wf.addItem(display, userData=full)
                        n += 1
        self.cb_wf.blockSignals(False)
        if n == 0:
            self.cb_wf.addItem("（实例工作流目录为空）", userData="")
            self.cb_wf.setCurrentIndex(1)

    def _on_wf_pick(self, _i):
        path = self.cb_wf.currentData()
        if path:
            self.ed_wf.setText(path)

    def on_show(self):
        self._refresh_wf_list()
        self.profiles.on_show()

    def on_instance_changed(self):
        self._refresh_wf_list()
        self.profiles.on_instance_changed()

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择工作流", "", "工作流 (*.json)")
        if f:
            self.ed_wf.setText(f)

    def _log(self, msg):
        self.log.appendPlainText(str(msg))

    def _current_inst(self):
        return self.mainwin.current_inst()

    def _scan(self):
        inst = self._current_inst()
        if not inst:
            QMessageBox.warning(self, "ComfyHub", "请先在「ComfyUI 管理」添加实例")
            return
        if not os.path.isfile(self.ed_wf.text().strip()):
            QMessageBox.warning(self, "ComfyHub", "请选择工作流文件")
            return
        self._log(f"── 扫描 {os.path.basename(self.ed_wf.text())} ──")
        ai = self.cfg.get("settings") or {}
        w = ScanWorker(inst["comfyui_path"], self.ed_wf.text().strip(),
                       ai_settings=ai)
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_scan)
        swap_worker(self, "_worker", w)
        w.start()

    def _on_scan(self, payload):
        if not payload:
            return
        res = payload.get("scan") or {}
        self.scan_result = res
        self.plan = payload.get("plan") or installer.plan_from_scan(res)
        rows, dl_rows = [], {}
        for s in self.plan["steps"]:
            tag = "🤖 AI待验证" if s.get("ai") else "✅ 可自动安装"
            rows.append((tag, s["reason"], s["content"], "自动"))
        for up in self.plan.get("upgrades", []):
            cur = up.get("kernel_ver") or "?"
            rows.append(("⬆️ 需升级内核", up["node"],
                         f"ComfyUI ≥ {up['min_version']} 内置节点"
                         f"（当前内核 {cur}），无需找插件", "更新内核"))
        for n in self.plan["unresolvable"]:
            rows.append(("❓ 未识别", n, "请到 Manager 或 CivitAI 搜索节点名", "手动"))
        for m in self.plan["models"]:
            rows.append(("📦 模型", m["file"], m["status"],
                         "下载" if m.get("downloadable") else "手动"))
            if m.get("downloadable") and m.get("source"):
                dl_rows[len(rows) - 1] = m["source"]
        self.tb.setRowCount(len(rows))
        for i, (a, b, c, d) in enumerate(rows):
            it = QTableWidgetItem(a)
            if a.startswith("✅"):
                it.setForeground(Qt.green)
            elif a.startswith("❓"):
                it.setForeground(Qt.red)
            elif a.startswith("⬆️"):
                it.setForeground(QColor("#ffb347"))
            elif a.startswith("🤖"):
                it.setForeground(QColor("#b78cff"))
            self.tb.setItem(i, 0, it)
            self.tb.setItem(i, 1, QTableWidgetItem(b))
            self.tb.setItem(i, 2, QTableWidgetItem(c))
            if i in dl_rows:
                btn = QPushButton("⬇️ 下载")
                btn.setFixedHeight(26)
                btn.clicked.connect(
                    lambda _checked=False, src=dl_rows[i]: self._dl_model(src))
                self.tb.setCellWidget(i, 3, btn)
            else:
                self.tb.setItem(i, 3, QTableWidgetItem(d))
        n_ok = len(self.plan["steps"])
        n_up = len(self.plan.get("upgrades", []))
        n_un = len(self.plan["unresolvable"])
        n_m = len(self.plan["models"])
        n_dl = len(dl_rows)
        self._counts = (n_ok, n_dl)
        self._has_upgrades = bool(self.plan.get("upgrades"))
        parts = [f"计划: {n_ok} 步可自动执行"]
        if n_up:
            parts.append(f"{n_up} 个需升级内核")
        if n_un:
            parts.append(f"{n_un} 个节点需手动找插件")
        parts.append(f"{n_m} 个模型待下载" + (f"（{n_dl} 个有直链）" if n_dl else ""))
        self.lb_plan.setText(" | ".join(parts))
        self._sync_exec_btn()
        self._log(f"✅ 扫描完成: 节点 {len(res.get('class_types') or [])}，"
                  f"已装插件 {len(res.get('required') or {})}，可自动安装 {n_ok}")
        if n_up:
            self._log(f"⬆️ {n_up} 个节点是新内核内置——升级内核即可，"
                      "去 Manager 找插件是死路")
        if n_dl:
            self._log(f"⬇️ {n_dl} 个模型已定位直链（hf-mirror / ModelScope），"
                      "可逐个点「下载」或随计划批量下载")

    def _sync_exec_btn(self):
        n_ok, n_dl = getattr(self, "_counts", (0, 0))
        has_up = bool(getattr(self, "_has_upgrades", False))
        self.b_exec.setEnabled(bool(n_ok) or bool(n_dl and self.ck_models.isChecked()))
        # AI 补全把「需升级内核」也算进来：那种情况 n_ok=0，但确实有事可做
        self.b_ai.setEnabled(bool(n_ok) or has_up
                             or bool(n_dl and self.ck_models.isChecked()))

    def _dl_model(self, src):
        inst = self._current_inst()
        if not inst:
            return
        if self._worker is not None and self._worker.isRunning():
            pass    # 扫描中不禁下载，两者互不影响
        dest = os.path.join(inst["comfyui_path"], "models",
                            src.get("subdir") or "checkpoints")
        fname = os.path.basename(src["path"])
        r = QMessageBox.question(
            self, "下载模型",
            f"{fname}\n→ {dest}\n\n大文件走断点续传，中断可续。开始下载？")
        if r != QMessageBox.Yes:
            return
        w = ModelDLWorker(src, dest)
        w.sig_log.connect(self._log)
        w.sig_progress.connect(
            lambda d, t: self._log_once_progress(fname, d, t))
        w.sig_done.connect(
            lambda ok, f=fname: self._log(
                f"✅ 下载完成: {f}" if ok else f"⚠️ 未完成: {f}（可点下载续传）"))
        swap_worker(self, "_dl_worker", w)
        w.start()
        self._log(f"⬇️ 开始下载 {fname} …")

    def _log_once_progress(self, fname, d, t):
        # 每 ~10% 报一次，避免刷屏
        if t and int(d * 10 / t) != getattr(self, "_last_pct", -1):
            self._last_pct = int(d * 10 / t)
            self._log(f"  {fname}: {d / 1e6:.0f}/{t / 1e6:.0f} MB"
                      f"（{self._last_pct * 10}%）")

    def _import_plan(self):
        f, _ = QFileDialog.getOpenFileName(self, "导入步骤清单", "",
                                           "清单 JSON (*.json)")
        if not f:
            return
        try:
            steps = installer.import_plan(f)
        except Exception as e:
            QMessageBox.warning(self, "ComfyHub", f"导入失败: {e}")
            return
        self.plan = {"steps": steps, "unresolvable": [], "models": [],
                     "upgrades": []}
        self._counts = (len(steps), 0)
        self.tb.setRowCount(len(steps))
        for i, s in enumerate(steps):
            self.tb.setItem(i, 0, QTableWidgetItem("📥 清单"))
            self.tb.setItem(i, 1, QTableWidgetItem(s.get("reason", "")))
            self.tb.setItem(i, 2, QTableWidgetItem(str(s.get("content", ""))[:80]))
        self.lb_plan.setText(f"已导入 {len(steps)} 步")
        self.b_exec.setEnabled(True)

    def _execute(self):
        inst = self._current_inst()
        if not inst or not self.plan:
            return
        steps = list(self.plan["steps"])
        n_dl = 0
        if self.ck_models.isChecked():
            for m in self.plan.get("models", []):
                if m.get("downloadable") and m.get("source"):
                    s = m["source"]
                    steps.append({
                        "type": "model_url",
                        "content": {"repo": s["repo"], "path": s["path"],
                                    "subdir": s.get("subdir") or "checkpoints"},
                        "reason": f"模型 {m['file']}",
                    })
                    n_dl += 1
        if not steps:
            return
        r = QMessageBox.question(
            self, "执行安装计划",
            f"将执行 {len(steps)} 个步骤"
            + (f"（含 {n_dl} 个模型下载）" if n_dl else "") + "。\n"
            "执行前会自动创建救援快照，失败可回滚。继续？")
        if r != QMessageBox.Yes:
            return
        self._run_steps(inst, steps)

    def _ai_solve(self):
        """AI 补全并执行（2026-11-01 用户需求）

        扫描结果常常长这样：0 步可自动执行 / 2 个节点要升级内核 / 3 个模型待下载。
        逐项点按钮很碎，这里合成一条队列一次跑完：内核 → 插件 → 模型。
        AI 的补全作用在扫描阶段（plan_from_scan 里对未识别节点/模型做推断），
        本方法负责把推断结果和确定性结果合并成可执行队列。
        """
        inst = self._current_inst()
        if not inst or not self.plan:
            QMessageBox.warning(self, "ComfyHub", "请先扫描工作流")
            return
        if self._exec is not None and self._exec.isRunning():
            QMessageBox.information(self, "ComfyHub", "已有任务在执行，等它结束再点")
            return
        steps = installer.build_solve_queue(
            self.plan, include_models=self.ck_models.isChecked(), log=self._log)
        if not steps:
            QMessageBox.information(self, "ComfyHub", "扫描结果里没有需要补的项 ✅")
            return
        # 升级内核要 git checkout，实例运行时文件被锁 → 直接拦住，别等跑到一半失败
        if any(st.get("type") == "upgrade_kernel" for st in steps):
            procs = getattr(self.mainwin, "procs", None)
            try:
                if procs is not None and procs.is_running(inst):
                    QMessageBox.warning(
                        self, "ComfyHub",
                        "该实例正在运行，升级内核会被运行中的文件锁挡住。\n"
                        "请先到「ComfyUI」页停止实例，再回来执行。")
                    return
            except Exception:
                pass
        kinds = {}
        for st in steps:
            kinds[st.get("type", "?")] = kinds.get(st.get("type", "?"), 0) + 1
        label = {"upgrade_kernel": "%d 步升级内核", "git_clone": "%d 个插件",
                 "model_url": "%d 个模型", "python_package": "%d 个依赖",
                 "cmd": "%d 条命令", "python_cmd": "%d 个脚本"}
        desc = "、".join(label.get(k, "%d 个 " + k) % v for k, v in kinds.items())
        r = QMessageBox.question(
            self, "AI 补全并执行",
            f"将按「内核 → 插件 → 模型」顺序执行 {len(steps)} 步：{desc}。\n\n"
            "⚠ 升级内核前请先停止该实例（运行中会锁文件导致失败）。\n"
            "执行前会自动创建救援快照，失败可回滚。继续？")
        if r != QMessageBox.Yes:
            return
        self._run_steps(inst, steps)

    def _run_steps(self, inst, steps):
        """统一执行入口（普通计划与 AI 补全共用）"""
        self._last_had_kernel = any(
            st.get("type") == "upgrade_kernel" for st in steps)
        self.b_exec.setEnabled(False)
        self.b_ai.setEnabled(False)
        self.bar_prog.setVisible(True)
        self.bar_prog.setRange(0, len(steps))
        self.bar_prog.setValue(0)
        self._exec = ExecWorker(inst, steps)
        self._exec.sig_log.connect(self._log)
        self._exec.sig_done.connect(self._on_exec)
        self._exec.start()

    def _on_exec(self, res):
        self.bar_prog.setValue(res.get("done", 0))
        self.b_exec.setEnabled(True)
        self.b_ai.setEnabled(True)
        snap = res.get("snapshot", "")
        fail = res.get("failed_step")
        tail = ("已升级内核：请重启 ComfyUI 实例后再生图。"
                if getattr(self, "_last_had_kernel", False)
                else "重启 ComfyUI 后生效。")
        if fail is None:
            QMessageBox.information(
                self, "ComfyHub", f"✅ 安装完成（{res.get('done')}/{res.get('total')}）。\n"
                f"{tail}\n救援快照: {snap}")
        else:
            QMessageBox.warning(
                self, "ComfyHub",
                f"在第 {fail + 1} 步失败，可用救援快照回滚：{snap}\n"
                "（环境管理页 → 救援模式）")

    def _stop(self):
        if self._exec:
            self._exec.stop()
            self._log("…已请求停止")
