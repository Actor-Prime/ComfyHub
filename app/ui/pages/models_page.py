# -*- coding: utf-8 -*-
"""模型库页：本地模型列表（主角）+ 添加模型（在线下载 Tab）+ 下载中心指示器（右上角下拉）

交互（对齐插件页模式）：
  · 默认显示本地模型列表（类别筛选 + 明细）
  · 「➕ 添加模型」→ 切到「在线下载」Tab：选源 → 搜索 → 勾文件 → 入队下载
  · 右上角 ⬇ 指示器：点击下拉显示每个任务的实时进度
"""
import os
import shutil
import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QGroupBox, QFileDialog, QMessageBox, QTabBar,
    QTabWidget,
)

from app.tools import ModelDownloader
from app import instance as inst_lib
from app.ui.pages.model_market_widget import ModelMarketWidget
from app.ui.download_center import DownloadCenter


class DlWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool)

    def __init__(self, downloader, url, subdir, filename):
        super().__init__()
        self.d, self.u, self.s, self.f = downloader, url, subdir, filename

    def run(self):
        ok = self.d.download(self.u, self.s, self.f,
                             log=lambda m: self.sig_log.emit(str(m)))
        self.sig_done.emit(ok)


def _fmt_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.0f} KB"


class ModelsPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    def _models_dir(self) -> str:
        inst = self._inst()
        return os.path.join(inst["comfyui_path"], "models") if inst else ""

    # ---------- UI ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        t = QLabel("模型库")
        t.setStyleSheet("font-size:17px; font-weight:bold; color:#fff;")
        root.addWidget(t)

        # 标题行：实例信息 | stretch | 下载中心指示器（右上角下拉）
        head = QHBoxLayout()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        head.addWidget(self.lb_inst)
        head.addStretch()
        self.dl_center = DownloadCenter()
        self.dl_center.sig_queue_done.connect(lambda *_: self.refresh_stats())
        head.addWidget(self.dl_center)
        root.addLayout(head)

        # 操作行：添加模型（主入口）+ 本地库操作
        h2 = QHBoxLayout()
        b_add = QPushButton("➕ 添加模型")
        b_add.setProperty("accent", True)
        b_add.clicked.connect(self._go_market)
        h2.addWidget(b_add)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("🔍 搜索本地模型文件名…")
        self.ed_search.textChanged.connect(lambda _t: self.refresh_stats())
        h2.addWidget(self.ed_search, 1)
        b_refresh = QPushButton("🔄 刷新列表")
        b_refresh.clicked.connect(self.refresh_stats)
        b_import = QPushButton("📥 导入模型文件")
        b_import.clicked.connect(self._import_files)
        b_open = QPushButton("📂 打开文件夹")
        b_open.clicked.connect(self._open_dir)
        h2.addWidget(b_refresh)
        h2.addWidget(b_import)
        h2.addWidget(b_open)
        root.addLayout(h2)

        # 双 Tab：本地模型（默认）/ 在线下载
        self.tabs2 = QTabWidget()
        self.tabs2.addTab(self._build_local_tab(), "💾 本地模型")
        self.market = ModelMarketWidget(self.cfg, self.mainwin)
        self.market.dl_center = self.dl_center            # 下载统一走下载中心
        self.tabs2.addTab(self.market, "🛒 在线下载")
        self.tabs2.currentChanged.connect(self._on_main_tab)
        root.addWidget(self.tabs2, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.log.setMaximumHeight(110)
        root.addWidget(self.log, 0)

    def _build_local_tab(self) -> QWidget:
        w = QWidget()
        lv = QVBoxLayout(w)
        lv.setContentsMargins(0, 6, 0, 0)

        # 直链下载（高级功能）
        g = QGroupBox("直链下载（断点续传 · CivitAI / HuggingFace 链接）")
        h1 = QHBoxLayout(g)
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText(
            "模型直链 URL（CivitAI 直链带 &token=；HuggingFace 用 resolve/main 链接）")
        h1.addWidget(self.ed_url, 1)
        self.cb_sub = QComboBox()
        self.cb_sub.addItems(ModelDownloader("").MODEL_SUBDIRS)
        h1.addWidget(self.cb_sub)
        b_dl = QPushButton("⬇ 下载")
        b_dl.clicked.connect(self._download)
        h1.addWidget(b_dl)
        lv.addWidget(g)

        # 类别标签（顶部横排）
        self.tabs = QTabBar()
        self.tabs.addTab("全部")
        for sub in ModelDownloader("").MODEL_SUBDIRS:
            self.tabs.addTab(sub)
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(lambda _i: self.refresh_stats())
        lv.addWidget(self.tabs)

        # 文件明细表
        self.tb = QTableWidget(0, 3)
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        lv.addWidget(self.tb, 1)
        return w

    def _go_market(self):
        self.tabs2.setCurrentWidget(self.market)
        self.market.ed_search.setFocus()

    def _on_main_tab(self, i):
        if self.tabs2.tabText(i).startswith("💾"):
            self.refresh_stats()

    # ---------- 数据 ----------
    def _cur_cat(self) -> str:
        i = self.tabs.currentIndex()
        return "" if i <= 0 else self.tabs.tabText(i)

    def _list_files(self, subdir: str, kw: str = ""):
        d = os.path.join(self._models_dir(), subdir)
        out = []
        if os.path.isdir(d):
            for name in os.listdir(d):
                p = os.path.join(d, name)
                # 只显示模型格式文件（txt 占位符等排除）
                if (os.path.isfile(p) and kw.lower() in name.lower()
                        and inst_lib.is_model_file(name)):
                    st = os.stat(p)
                    out.append((name, st.st_size, st.st_mtime))
        out.sort(key=lambda x: -x[1])
        return out

    def _add_row(self, vals, colors=None):
        r = self.tb.rowCount()
        self.tb.insertRow(r)
        for c, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            if colors and colors.get(c):
                it.setForeground(QColor(colors[c]))
            self.tb.setItem(r, c, it)

    def refresh_stats(self):
        self.tb.setRowCount(0)
        inst = self._inst()
        if not inst:
            self.tb.setColumnCount(3)
            self.tb.setHorizontalHeaderLabels(["提示", "", ""])
            self._add_row(["（未选择实例）", "-", "-"])
            return
        cat = self._cur_cat()
        kw = self.ed_search.text().strip()
        if cat:
            # 单类别：文件明细（对标绘世列表）
            self.tb.setColumnCount(3)
            self.tb.setHorizontalHeaderLabels(["文件名", "大小", "修改日期"])
            files = self._list_files(cat, kw)
            if not files:
                self._add_row(["（此类别暂无文件）", "-",
                               "点「➕ 添加模型」在线下载，或「导入模型文件」"],
                              {0: "#7d8797"})
            for name, size, mtime in files:
                dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                self._add_row([name, _fmt_size(size), dt])
        else:
            # 全部：分类统计（支持搜索类别名）
            self.tb.setColumnCount(3)
            self.tb.setHorizontalHeaderLabels(["类别", "文件数", "总体积"])
            rows = inst_lib.model_stats(inst["comfyui_path"])
            if not rows:
                self._add_row(["（models 目录为空）", "-", "点「➕ 添加模型」在线下载"],
                              {0: "#7d8797"})
            for cat_name, cnt, gb in rows:
                if kw and kw.lower() not in cat_name.lower():
                    continue
                self._add_row([cat_name, str(cnt), f"{gb:.2f} GB"])

    def on_show(self):
        self.refresh_stats()

    def on_instance_changed(self):
        self.refresh_stats()

    # ---------- 导入（复制/剪切） ----------
    def _import_files(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先选择实例")
            return
        cat = self._cur_cat()
        if not cat:
            QMessageBox.information(self, "ComfyHub",
                                    "请先在顶部标签页选择一个具体类别\n"
                                    "（导入的模型会放进该类别目录）")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的模型文件（可多选）", "",
            "模型文件 (*.safetensors *.ckpt *.pt *.pth *.gguf *.sft *.bin);;所有文件 (*)")
        if not files:
            return
        box = QMessageBox(self)
        box.setWindowTitle("导入方式")
        box.setText(f"将 {len(files)} 个文件导入到 models/{cat}/")
        b_copy = box.addButton("📋 复制", QMessageBox.AcceptRole)
        b_move = box.addButton("✂️ 剪切", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_copy, b_move):
            return
        mode = "copy" if clicked is b_copy else "move"
        dest_dir = os.path.join(self._models_dir(), cat)
        os.makedirs(dest_dir, exist_ok=True)
        ok_n, skip_n = 0, 0
        for f in files:
            base = os.path.basename(f)
            dest = os.path.join(dest_dir, base)
            stem, ext = os.path.splitext(base)
            i = 1
            while os.path.exists(dest):
                dest = os.path.join(dest_dir, f"{stem}({i}){ext}")
                i += 1
            try:
                if mode == "copy":
                    shutil.copy2(f, dest)
                else:
                    shutil.move(f, dest)
                ok_n += 1
                self._log(f"{'复制' if mode == 'copy' else '剪切'} → {cat}/{os.path.basename(dest)}")
            except (OSError, shutil.Error) as e:
                skip_n += 1
                self._log(f"❌ 导入失败 {base}: {e}")
        self._log(f"导入完成：成功 {ok_n}，失败 {skip_n}")
        self.refresh_stats()

    def _open_dir(self):
        cat = self._cur_cat()
        d = os.path.join(self._models_dir(), cat) if cat else self._models_dir()
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        inst_lib.open_in_explorer(d)

    def _log(self, m):
        self.log.appendPlainText(str(m))

    # ---------- 直链下载 ----------
    def _download(self):
        inst = self._inst()
        if not inst:
            self._log("❌ 无实例")
            return
        url = self.ed_url.text().strip()
        if not url.startswith(("http://", "https://")):
            self._log("❌ 请填写有效 URL")
            return
        self._worker = DlWorker(ModelDownloader(inst["comfyui_path"]), url,
                                self.cb_sub.currentText(), "")
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(
            lambda ok: (self._log("✅ 下载完成" if ok else "❌ 下载失败"),
                        self.refresh_stats()))
        self._worker.start()
