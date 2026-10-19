# -*- coding: utf-8 -*-
"""工具页：工作流检测 / 内核版本 / 模型下载 / 备份还原 / 救援模式"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QLineEdit, QComboBox, QMessageBox, QFileDialog, QPlainTextEdit,
    QGroupBox,
)

from app import workflow_scan
from app.tools import KernelManager, ModelDownloader, EnvBackup, Rescue


# ---------------- 后台线程 ----------------
class AnalyzeWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, wf_path):
        super().__init__()
        self.comfyui_path, self.wf_path = comfyui_path, wf_path

    def run(self):
        try:
            res = workflow_scan.analyze_workflow(
                self.comfyui_path, self.wf_path,
                log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(res)
        except Exception as e:
            self.sig_log.emit(f"分析失败: {e}")
            self.sig_done.emit({})


class DownloadWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool)

    def __init__(self, downloader, url, subdir, filename):
        super().__init__()
        self.d, self.url, self.subdir, self.fn = downloader, url, subdir, filename

    def run(self):
        ok = self.d.download(self.url, self.subdir, self.fn,
                             log=lambda m: self.sig_log.emit(str(m)))
        self.sig_done.emit(ok)


class BackupWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool, str)

    def __init__(self, backup, name, mode, snap=None, rescue=None):
        super().__init__()
        self.b, self.name, self.mode = backup, name, mode
        self.snap, self.rescue = snap, rescue

    def run(self):
        if self.mode == "create":
            n = self.b.create(self.name, log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(True, n)
        elif self.mode == "restore":
            ok = self.b.restore(self.name, log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(ok, self.name)
        elif self.mode == "rollback":
            ok, msg = self.rescue.rollback(self.snap,
                                           log=lambda m: self.sig_log.emit(str(m)))
            self.sig_done.emit(ok, msg)


class ToolsPage(QWidget):
    """实例的第五个标签页：高级工具集"""

    def __init__(self, inst: dict, get_console_log, parent=None):
        super().__init__(parent)
        self.inst = inst
        self._console_log = get_console_log   # 回调：把日志写进主控制台
        self._worker = None
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        tabs.addTab(self._tab_workflow(), "工作流检测")
        tabs.addTab(self._tab_kernel(), "内核版本")
        tabs.addTab(self._tab_models(), "模型下载")
        tabs.addTab(self._tab_backup(), "备份 / 救援")

    def _logbox(self, key: str) -> QPlainTextEdit:
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setMaximumBlockCount(800)
        if not hasattr(self, "_log_boxes"):
            self._log_boxes = {}
        self._log_boxes[key] = box
        return box

    def _wlog(self, msg, key: str = "wf"):
        box = self._log_boxes.get(key)
        if box:
            box.appendPlainText(str(msg))
        self._console_log(f"[工具] {msg}")

    # ===== 工作流检测 =====
    def _tab_workflow(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        hb = QHBoxLayout()
        self.ed_wf = QLineEdit()
        self.ed_wf.setPlaceholderText("选择 ComfyUI 工作流 JSON（UI 导出或 API 格式均可）")
        b_pick = QPushButton("浏览…")
        b_pick.clicked.connect(self._pick_wf)
        b_go = QPushButton("🔍 分析")
        b_go.clicked.connect(self._analyze)
        hb.addWidget(self.ed_wf, 1)
        hb.addWidget(b_pick)
        hb.addWidget(b_go)
        v.addLayout(hb)

        split_row = QHBoxLayout()
        # 节点/插件表
        self.tb_wf = QTableWidget(0, 3)
        self.tb_wf.setHorizontalHeaderLabels(["类型", "名称", "所需节点"])
        self.tb_wf.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb_wf.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb_wf.setEditTriggers(QTableWidget.NoEditTriggers)
        split_row.addWidget(self.tb_wf, 2)
        # 模型清单
        self.tb_wf_models = QTableWidget(0, 2)
        self.tb_wf_models.setHorizontalHeaderLabels(["模型类别", "文件"])
        self.tb_wf_models.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb_wf_models.setEditTriggers(QTableWidget.NoEditTriggers)
        split_row.addWidget(self.tb_wf_models, 1)
        v.addLayout(split_row, 1)
        v.addWidget(self._logbox("wf"))
        return w

    def _pick_wf(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择工作流 JSON", "",
                                           "工作流 (*.json)")
        if f:
            self.ed_wf.setText(f)

    def _analyze(self):
        wf = self.ed_wf.text().strip()
        if not os.path.isfile(wf):
            QMessageBox.warning(self, "ComfyHub", "请先选择工作流文件")
            return
        self._wlog(f"分析工作流: {os.path.basename(wf)} …")
        self._worker = AnalyzeWorker(self.inst["comfyui_path"], wf)
        self._worker.sig_log.connect(self._wlog)
        self._worker.sig_done.connect(self._show_analysis)
        self._worker.start()

    def _show_analysis(self, res: dict):
        if not res:
            return
        self.tb_wf.setRowCount(0)
        rows = []
        for plug, nodes in sorted(res["required"].items()):
            rows.append(("已装插件", plug, "、".join(nodes[:6]) + ("…" if len(nodes) > 6 else "")))
        for node in res["missing"].get("__unknown__", []):
            rows.append(("❓未装/未知", node, "请在 Manager 中搜索该节点名"))
        for n in res["core"]:
            rows.append(("内核自带", n, ""))
        self.tb_wf.setRowCount(len(rows))
        for i, (a, b, c) in enumerate(rows):
            it = QTableWidgetItem(a)
            if a.startswith("❓"):
                it.setForeground(Qt.red)
            elif a == "已装插件":
                it.setForeground(Qt.darkGreen)
            self.tb_wf.setItem(i, 0, it)
            self.tb_wf.setItem(i, 1, QTableWidgetItem(b))
            self.tb_wf.setItem(i, 2, QTableWidgetItem(c))
        # 模型表
        models = res.get("models", {})
        total = sum(len(v) for v in models.values())
        self.tb_wf_models.setRowCount(total)
        r = 0
        for kind, files in sorted(models.items()):
            for f in files:
                self.tb_wf_models.setItem(r, 0, QTableWidgetItem(kind))
                self.tb_wf_models.setItem(r, 1, QTableWidgetItem(f))
                r += 1
        self._wlog(f"✅ 节点 {len(res['class_types'])} 个 | 已装插件 "
                   f"{len(res['required'])} 个 | 未装 {len(res['missing'].get('__unknown__', []))} 个 | "
                   f"模型 {total} 个")

    # ===== 内核版本 =====
    def _tab_kernel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        km = KernelManager(self.inst["comfyui_path"])
        info = QGroupBox(f"当前内核: {km.current_version()}")
        iv = QVBoxLayout(info)
        hint = QLabel("（git 仓库实例支持在线切换版本；zip 安装实例暂不支持）") \
            if not km.is_git_repo else \
            QLabel("实例为 git 仓库，可在线获取官方 tag 并切换")
        hint.setWordWrap(True)
        iv.addWidget(hint)
        v.addWidget(info)

        hb = QHBoxLayout()
        b_refresh = QPushButton("刷新版本列表")
        b_refresh.clicked.connect(lambda: self._refresh_tags(km))
        b_switch = QPushButton("切换到选中版本")
        b_switch.clicked.connect(lambda: self._switch_tag(km))
        hb.addWidget(b_refresh)
        hb.addWidget(b_switch)
        hb.addStretch()
        v.addLayout(hb)

        self.tb_tags = QTableWidget(0, 1)
        self.tb_tags.setHorizontalHeaderLabels(["版本 tag"])
        self.tb_tags.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        v.addWidget(self.tb_tags, 1)
        if km.is_git_repo:
            self._refresh_tags(km)
        return w

    def _refresh_tags(self, km: KernelManager):
        self._wlog("获取官方版本列表…")
        tags = km.list_tags()
        self.tb_tags.setRowCount(len(tags))
        for i, t in enumerate(tags):
            self.tb_tags.setItem(i, 0, QTableWidgetItem(t))
        self._wlog(f"共 {len(tags)} 个版本")

    def _switch_tag(self, km: KernelManager):
        row = self.tb_tags.currentRow()
        if row < 0:
            QMessageBox.information(self, "ComfyHub", "先在列表中选择一个版本")
            return
        tag = self.tb_tags.item(row, 0).text()
        r = QMessageBox.question(self, "切换内核",
                                 f"切换到 {tag}？\n（会先 stash 本地改动，可恢复）")
        if r != QMessageBox.Yes:
            return
        ok, msg = km.switch_to(tag)
        self._wlog(f"切换 {tag}: {'成功' if ok else '失败'} {msg[:150]}")
        QMessageBox.information(self, "ComfyHub", f"{'✅ 已切换' if ok else '❌ 失败'}\n{msg[:200]}")

    # ===== 模型下载 =====
    def _tab_models(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        form = QHBoxLayout()
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("模型直链 URL（支持断点续传；CivitAI 直链带 &token=）")
        self.cb_sub = QComboBox()
        self.cb_sub.addItems(ModelDownloader(self.inst["comfyui_path"]).MODEL_SUBDIRS)
        b_go = QPushButton("⬇ 下载")
        b_go.clicked.connect(self._download_model)
        form.addWidget(self.ed_url, 1)
        form.addWidget(self.cb_sub)
        form.addWidget(b_go)
        v.addLayout(form)
        v.addWidget(self._logbox("dl"), 1)
        tip = QLabel("提示：下载完成即入对应 models/ 子目录，重启 ComfyUI 刷新节点列表。"
                     "CivitAI / HuggingFace 站内搜索将在后续版本加入。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;")
        v.addWidget(tip)
        return w

    def _download_model(self):
        url = self.ed_url.text().strip()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "ComfyHub", "请填写有效的模型 URL")
            return
        self._wlog(f"准备下载 → models/{self.cb_sub.currentText()}/")
        self._worker = DownloadWorker(
            ModelDownloader(self.inst["comfyui_path"]), url,
            self.cb_sub.currentText(), "")
        self._worker.sig_log.connect(self._wlog)
        self._worker.sig_done.connect(
            lambda ok: self._wlog("✅ 下载完成" if ok else "❌ 下载失败"))
        self._worker.start()

    # ===== 备份 / 救援 =====
    def _tab_backup(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        g1 = QGroupBox("环境备份（pip freeze + 插件清单）")
        h1 = QHBoxLayout(g1)
        self.ed_bk = QLineEdit()
        self.ed_bk.setPlaceholderText("备份备注（可空）")
        b_bk = QPushButton("创建备份")
        b_bk.clicked.connect(self._create_backup)
        b_rs = QPushButton("还原选中备份")
        b_rs.clicked.connect(self._restore_backup)
        h1.addWidget(self.ed_bk, 1)
        h1.addWidget(b_bk)
        h1.addWidget(b_rs)
        v.addWidget(g1)

        g2 = QGroupBox("救援模式（插件装坏一键回滚启停状态）")
        h2 = QHBoxLayout(g2)
        b_snap = QPushButton("创建救援快照")
        b_snap.clicked.connect(self._make_snapshot)
        b_rb = QPushButton("回滚到选中快照")
        b_rb.clicked.connect(self._rollback)
        h2.addWidget(b_snap)
        h2.addWidget(b_rb)
        h2.addStretch()
        v.addWidget(g2)

        self.tb_bk = QTableWidget(0, 1)
        self.tb_bk.setHorizontalHeaderLabels(["备份 / 快照"])
        self.tb_bk.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        v.addWidget(self.tb_bk, 1)
        self._refresh_backup_list()
        return w

    def _refresh_backup_list(self):
        bk = EnvBackup(self.inst["comfyui_path"], self.inst["python_path"])
        rs = Rescue(self.inst["comfyui_path"])
        items = [("备份", x) for x in bk.list_backups()] + \
                [("快照", x) for x in rs.list_snapshots()]
        self.tb_bk.setRowCount(len(items))
        for i, (kind, name) in enumerate(items):
            self.tb_bk.setItem(i, 0, QTableWidgetItem(f"[{kind}] {name}"))

    def _create_backup(self):
        bk = EnvBackup(self.inst["comfyui_path"], self.inst["python_path"])
        self._worker = BackupWorker(bk, self.ed_bk.text().strip(), "create")
        self._worker.sig_log.connect(self._console_log)
        self._worker.sig_done.connect(lambda ok, n: (
            self._console_log(f"[ComfyHub] 备份完成: {n}"),
            self._refresh_backup_list()))
        self._worker.start()

    def _restore_backup(self):
        row = self.tb_bk.currentRow()
        if row < 0 or not self.tb_bk.item(row, 0).text().startswith("[备份]"):
            QMessageBox.information(self, "ComfyHub", "请选择一个[备份]条目")
            return
        name = self.tb_bk.item(row, 0).text().replace("[备份] ", "")
        r = QMessageBox.question(self, "还原环境",
                                 "将按备份的 pip 清单重装依赖（耗时较长），继续？")
        if r != QMessageBox.Yes:
            return
        bk = EnvBackup(self.inst["comfyui_path"], self.inst["python_path"])
        self._worker = BackupWorker(bk, name, "restore")
        self._worker.sig_log.connect(self._console_log)
        self._worker.sig_done.connect(
            lambda ok, n: self._console_log(f"[ComfyHub] 还原{'成功' if ok else '失败'}: {n}"))
        self._worker.start()

    def _make_snapshot(self):
        rs = Rescue(self.inst["comfyui_path"])
        rs.snapshot(log=self._console_log)
        self._refresh_backup_list()

    def _rollback(self):
        row = self.tb_bk.currentRow()
        if row < 0 or "[快照]" not in self.tb_bk.item(row, 0).text():
            QMessageBox.information(self, "ComfyHub", "请选择一个[快照]条目")
            return
        snap = self.tb_bk.item(row, 0).text().replace("[快照] ", "")
        r = QMessageBox.question(self, "救援回滚",
                                 "将禁用快照之后新装的插件，并恢复当时启停状态（不删除文件）。继续？")
        if r != QMessageBox.Yes:
            return
        rs = Rescue(self.inst["comfyui_path"])
        self._worker = BackupWorker(None, "", "rollback", snap=snap, rescue=rs)
        self._worker.sig_log.connect(self._console_log)
        self._worker.sig_done.connect(lambda ok, msg: (
            self._console_log(f"[ComfyHub] 回滚完成: {msg}"),
            self._refresh_backup_list()))
        self._worker.start()
