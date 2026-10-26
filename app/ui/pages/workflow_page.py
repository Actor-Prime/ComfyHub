# -*- coding: utf-8 -*-
"""工作流页：扫描分析 → 生成安装计划 → 一键执行（对标管理大师 workflow 智能安装）"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QFileDialog, QMessageBox, QProgressBar, QGroupBox, QCheckBox, QComboBox,
)

from app import workflow_scan, installer
from app.installer import PlanExecutor
from app.workers import swap_worker


class ScanWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, wf_path):
        super().__init__()
        self.a, self.b = comfyui_path, wf_path

    def run(self):
        try:
            res = workflow_scan.analyze_workflow(
                self.a, self.b, log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(res)
        except Exception as e:
            self.sig_log.emit(f"扫描失败: {e}")
            self.sig_done.emit({})


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
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        t = QLabel("工作流 · 智能安装")
        t.setStyleSheet("font-size:17px; font-weight:bold; color:#fff;")
        root.addWidget(t)

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
        bar.addWidget(self.ck_models)
        self.b_exec = QPushButton("⚡ 执行安装计划")
        self.b_exec.setProperty("accent", True)
        self.b_exec.setEnabled(False)
        self.b_exec.clicked.connect(self._execute)
        b_stop = QPushButton("停止")
        b_stop.setProperty("danger", True)
        b_stop.clicked.connect(self._stop)
        b_imp = QPushButton("导入步骤清单…")
        b_imp.clicked.connect(self._import_plan)
        bar.addWidget(b_imp)
        bar.addWidget(b_stop)
        bar.addWidget(self.b_exec)
        root.addLayout(bar)

        self.bar_prog = QProgressBar()
        self.bar_prog.setVisible(False)
        root.addWidget(self.bar_prog)

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

    def on_instance_changed(self):
        self._refresh_wf_list()

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
        w = ScanWorker(inst["comfyui_path"], self.ed_wf.text().strip())
        w.sig_log.connect(self._log)
        w.sig_done.connect(self._on_scan)
        swap_worker(self, "_worker", w)
        w.start()

    def _on_scan(self, res):
        if not res:
            return
        self.scan_result = res
        self.plan = installer.plan_from_scan(res)
        rows = []
        for s in self.plan["steps"]:
            rows.append(("✅ 可自动安装", s["reason"], s["content"]))
        for n in self.plan["unresolvable"]:
            rows.append(("❓ 未识别", n, "请到 Manager 或 CivitAI 搜索节点名"))
        for m in self.plan["models"]:
            rows.append(("📦 模型", m["file"], m["status"]))
        self.tb.setRowCount(len(rows))
        for i, (a, b, c) in enumerate(rows):
            it = QTableWidgetItem(a)
            if a.startswith("✅"):
                it.setForeground(Qt.green)
            elif a.startswith("❓"):
                it.setForeground(Qt.red)
            self.tb.setItem(i, 0, it)
            self.tb.setItem(i, 1, QTableWidgetItem(b))
            self.tb.setItem(i, 2, QTableWidgetItem(c))
        n_ok = len(self.plan["steps"])
        n_un = len(self.plan["unresolvable"])
        n_m = len(self.plan["models"])
        self.lb_plan.setText(
            f"计划: {n_ok} 步可自动执行 | {n_un} 个节点需手动找插件 | {n_m} 个模型待下载")
        self.b_exec.setEnabled(n_ok > 0)
        self._log(f"✅ 扫描完成: 节点 {len(res['class_types'])}，"
                  f"已装插件 {len(res['required'])}，可自动安装 {n_ok}")

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
        self.plan = {"steps": steps, "unresolvable": [], "models": []}
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
        r = QMessageBox.question(
            self, "执行安装计划",
            f"将执行 {len(self.plan['steps'])} 个安装步骤。\n"
            "执行前会自动创建救援快照，失败可回滚。继续？")
        if r != QMessageBox.Yes:
            return
        self.b_exec.setEnabled(False)
        self.bar_prog.setVisible(True)
        self.bar_prog.setRange(0, len(self.plan["steps"]))
        self.bar_prog.setValue(0)
        self._exec = ExecWorker(inst, self.plan["steps"])
        self._exec.sig_log.connect(self._log)
        self._exec.sig_done.connect(self._on_exec)
        self._exec.start()

    def _on_exec(self, res):
        self.bar_prog.setValue(res.get("done", 0))
        self.b_exec.setEnabled(True)
        snap = res.get("snapshot", "")
        fail = res.get("failed_step")
        if fail is None:
            QMessageBox.information(
                self, "ComfyHub", f"✅ 安装完成（{res.get('done')}/{res.get('total')}）。\n"
                f"重启 ComfyUI 后生效。\n救援快照: {snap}")
        else:
            QMessageBox.warning(
                self, "ComfyHub",
                f"在第 {fail + 1} 步失败，可用救援快照回滚：{snap}\n"
                "（环境管理页 → 救援模式）")

    def _stop(self):
        if self._exec:
            self._exec.stop()
            self._log("…已请求停止")
