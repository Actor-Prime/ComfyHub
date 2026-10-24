# -*- coding: utf-8 -*-
"""模型市场控件：搜索 hf-mirror（HF 国内镜像）→ 选文件 → 下载到实例 models 目录

作为独立分组控件嵌入 models_page.py（零侵入现有本地库管理）。
"""
import os

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QLineEdit, QProgressBar,
    QDialog, QTreeWidget, QTreeWidgetItem, QComboBox,
)

from app import model_store as ms


class SearchWorker(QThread):
    sig_done = Signal(list, str)

    def __init__(self, keyword):
        super().__init__()
        self.keyword = keyword

    def run(self):
        try:
            rows = ms.search_models(self.keyword, 30)
            self.sig_done.emit(rows, "")
        except Exception as e:
            self.sig_done.emit([], str(e))


class ListFilesWorker(QThread):
    sig_done = Signal(list, str)

    def __init__(self, repo_id):
        super().__init__()
        self.repo_id = repo_id

    def run(self):
        try:
            files = ms.list_weight_files(self.repo_id)
            self.sig_done.emit(files, "")
        except Exception as e:
            self.sig_done.emit([], str(e))


class DownloadWorker(QThread):
    """顺序下载队列：items = [(repo_id, file_path, dest_dir), ...]"""
    sig_progress = Signal(int, int, str)
    sig_file_done = Signal(str)
    sig_done = Signal(int, int)
    sig_error = Signal(str)

    def __init__(self, items):
        super().__init__()
        self.items = items
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok = bad = 0
        for repo_id, file_path, dest_dir in self.items:
            if self._cancel:
                break
            fname = os.path.basename(file_path)
            try:
                ms.download_file(repo_id, file_path, dest_dir,
                                 progress_cb=lambda d, t: self.sig_progress.emit(d, t, fname),
                                 cancel_flag=lambda: self._cancel)
                self.sig_file_done.emit(os.path.join(dest_dir, fname))
                ok += 1
            except KeyboardInterrupt:
                self.sig_done.emit(ok, bad + 1)
                return
            except Exception as e:
                self.sig_error.emit(f"{fname}: {e}")
                bad += 1
        self.sig_done.emit(ok, bad)


class DownloadDialog(QDialog):
    """选文件 + 选目标分类目录"""

    def __init__(self, parent, repo_id, files):
        super().__init__(parent)
        self.setWindowTitle(f"下载模型 —— {repo_id.split('/')[-1]}")
        self.resize(560, 420)
        self.repo_id = repo_id
        self.result_items = []

        root = QVBoxLayout(self)
        root.addWidget(QLabel("勾选要下载的权重文件，选择目标分类目录："))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件", "大小", "预判分类"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for f in files:
            it = QTreeWidgetItem([os.path.basename(f["path"]),
                                  f"{f['size'] / 1048576:,.0f} MB", f["category"]])
            it.setData(0, Qt.UserRole, f)
            it.setCheckState(0, Qt.Checked)
            self.tree.addTopLevelItem(it)
        self.tree.itemChanged.connect(self._auto_category)
        root.addWidget(self.tree, 1)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("目标分类目录:"))
        self.cb_cat = QComboBox()
        self.cb_cat.addItems(ms.CATEGORIES)
        bar.addWidget(self.cb_cat)
        bar.addStretch()
        root.addLayout(bar)

        bb = QHBoxLayout()
        bb.addStretch()
        b_ok = QPushButton("开始下载")
        b_ok.setProperty("accent", True)
        b_ok.clicked.connect(self._ok)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        bb.addWidget(b_ok)
        bb.addWidget(b_cancel)
        root.addLayout(bb)

    def _auto_category(self, item, col):
        try:
            f = item.data(0, Qt.UserRole)
            if f and item.checkState(0) == Qt.Checked:
                self.cb_cat.setCurrentText(f["category"])
        except Exception:
            pass

    def _ok(self):
        cat = self.cb_cat.currentText().strip()
        picked = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                f = it.data(0, Qt.UserRole)
                picked.append((self.repo_id, f["path"], cat))
        if not picked:
            QMessageBox.information(self, "提示", "请先勾选要下载的文件")
            return
        self.result_items = picked
        self.accept()


class ModelMarketWidget(QWidget):
    """在线搜索下载分组（嵌入 models_page 顶部）"""

    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._search_worker = None
        self._files_worker = None
        self._dl_worker = None
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        t = QLabel("🛒 在线下载（hf-mirror · HuggingFace 国内镜像 · 免登录）")
        t.setStyleSheet("font-weight:bold; color:#fff;")
        head.addWidget(t)
        head.addStretch()
        b_open = QPushButton("📂 打开 models 目录")
        b_open.clicked.connect(self._open_models_dir)
        head.addWidget(b_open)
        root.addLayout(head)

        bar = QHBoxLayout()
        self.cb_source = QComboBox()
        self.cb_source.addItem("hf-mirror（国内直连）", "mirror")
        self.cb_source.addItem("huggingface.co（需代理）", "hf")
        settings = QSettings("LeiMengStudio", "ComfyHub")
        saved = settings.value("model_source", "mirror")
        idx = max(0, self.cb_source.findData(saved))
        self.cb_source.setCurrentIndex(idx)
        ms.set_source(self.cb_source.currentData())
        self.cb_source.currentIndexChanged.connect(self._on_source)
        self.cb_source.setToolTip("选择模型数据源：\n国内直连 = hf-mirror.com 全量镜像\n官方 = huggingface.co（更新更及时，需系统代理）")
        bar.addWidget(self.cb_source)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜索模型：sdxl checkpoint / flux lora / vae…（回车）")
        self.ed_search.returnPressed.connect(self._do_search)
        bar.addWidget(self.ed_search, 1)
        b = QPushButton("🔍 搜索")
        b.setProperty("accent", True)
        b.clicked.connect(self._do_search)
        bar.addWidget(b)
        root.addLayout(bar)



        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["模型", "下载量", "赞", "操作"])
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        self.tb.setColumnWidth(3, 90)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        self.tb.setAlternatingRowColors(True)
        self.tb.setVisible(False)
        root.addWidget(self.tb, 1)          # stretch=1：搜索后表格占满剩余空间

        # 底部状态行（表格之下，紧凑）
        prog = QHBoxLayout()
        self.lb_stat = QLabel("搜索后按下载量排序，点「下载 ↓」选择文件与目标分类。")
        self.lb_stat.setStyleSheet("color:#9aa4b2;")
        prog.addWidget(self.lb_stat, 1)
        self.lb_dl = QLabel("")
        self.lb_dl.setStyleSheet("color:#9aa4b2;")
        prog.addWidget(self.lb_dl)
        b_cancel = QPushButton("取消下载")
        b_cancel.clicked.connect(self._cancel_dl)
        prog.addWidget(b_cancel)
        root.addLayout(prog)

    def _on_source(self):
        mode = self.cb_source.currentData()
        ms.set_source(mode)
        QSettings("LeiMengStudio", "ComfyHub").setValue("model_source", mode)
        self.lb_stat.setText(f"数据源已切换：{self.cb_source.currentText()}")

    # -------------------------------------------------- 搜索
    def _do_search(self):
        kw = self.ed_search.text().strip()
        if not kw or (self._search_worker and self._search_worker.isRunning()):
            return
        self.lb_stat.setText(f"搜索「{kw}」中…")
        self.tb.setRowCount(0)
        self.tb.setVisible(True)
        self._search_worker = SearchWorker(kw)
        self._search_worker.sig_done.connect(self._on_search)
        self._search_worker.start()

    def _on_search(self, rows, err):
        if err:
            self.lb_stat.setText(f"搜索失败：{err}")
            return
        self.tb.setRowCount(len(rows))
        for i, m in enumerate(rows):
            it = QTableWidgetItem(m["name"])
            it.setToolTip(m["id"])
            it.setData(Qt.UserRole, m["id"])
            self.tb.setItem(i, 0, it)
            d = QTableWidgetItem(f"{m['downloads']:,}")
            d.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tb.setItem(i, 1, d)
            l = QTableWidgetItem(f"{m['likes']:,}")
            l.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tb.setItem(i, 2, l)
            b = QPushButton("下载 ↓")
            b.clicked.connect(lambda _c=False, rid=m["id"]: self._pick_files(rid))
            self.tb.setCellWidget(i, 3, b)
        self.lb_stat.setText(f"搜索「{self.ed_search.text().strip()}」：{len(rows)} 个结果（按下载量排序）")

    # -------------------------------------------------- 选文件
    def _pick_files(self, repo_id):
        if self._files_worker and self._files_worker.isRunning():
            return
        self.lb_stat.setText(f"获取 {repo_id} 的文件列表…")
        self._files_worker = ListFilesWorker(repo_id)
        self._files_worker.sig_done.connect(lambda files, err, rid=repo_id: self._on_files(rid, files, err))
        self._files_worker.start()

    def _on_files(self, repo_id, files, err):
        if err:
            self.lb_stat.setText(f"文件列表获取失败：{err}")
            return
        if not files:
            QMessageBox.information(self, "无权重文件", "该仓库中没有找到模型权重文件")
            return
        self.lb_stat.setText(f"获取到 {len(files)} 个权重文件")
        dlg = DownloadDialog(self, repo_id, files)
        if dlg.exec() == QDialog.Accepted and dlg.result_items:
            self._start_download(dlg.result_items)

    # -------------------------------------------------- 下载
    def _start_download(self, items):
        # 统一走下载中心（右上角 ⬇ 指示器 + 下拉进度面板）
        if getattr(self, "dl_center", None) is not None:
            self.dl_center.enqueue(items)
            self.lb_dl.setText(f"已加入下载队列（{len(items)} 个文件），右上角 ⬇ 查看进度")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            QMessageBox.information(self, "提示", "已有下载任务进行中")
            return
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "提示", "请先在「实例」页选择 ComfyUI 实例")
            return
        self._dl_worker = DownloadWorker(items)
        self._dl_worker.sig_progress.connect(self._on_progress)
        self._dl_worker.sig_file_done.connect(self._on_file_done)
        self._dl_worker.sig_error.connect(lambda e: self.lb_dl.setText(f"下载出错：{e[:80]}"))
        self._dl_worker.sig_done.connect(self._on_dl_done)
        self.lb_dl.setText(f"下载到 models/{items[0][2]}/ …")

    def _on_progress(self, done, total, fname):
        self.lb_dl.setText(f"下载 {fname}：{done / 1048576:,.0f} / {total / 1048576:,.0f} MB")

    def _on_file_done(self, path):
        self.lb_dl.setText(f"完成：{os.path.basename(path)}")

    def _on_dl_done(self, ok, bad):
        self.lb_dl.setText(f"下载队列结束：成功 {ok}，失败 {bad}")
        if ok:
            QMessageBox.information(self, "下载完成",
                                    f"成功 {ok} 个文件。\n重启 ComfyUI 后即可在对应分类看到新模型。")

    def _cancel_dl(self):
        if self._dl_worker and self._dl_worker.isRunning():
            self._dl_worker.cancel()
            self.lb_dl.setText("取消中…（保留 .part，下次续传）")

    def _open_models_dir(self):
        inst = self._inst()
        if inst:
            d = os.path.join(inst["comfyui_path"], "models")
            os.makedirs(d, exist_ok=True)
            from app import instance as inst_lib
            inst_lib.open_in_explorer(d)
