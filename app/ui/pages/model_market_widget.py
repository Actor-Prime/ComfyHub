# -*- coding: utf-8 -*-
"""模型市场面板（v0.8.06 重构）：卡片式结果流 + 状态条 + 空状态快捷词

由 models_page 的统一搜索栏驱动 run_search(source, keyword)，
下载统一走右上角下载中心（dl_center），无下载中心时退回本地顺序队列。
数据源：hf-mirror（HF 国内镜像）/ civitai.red（C 站国内镜像）/ huggingface.co（需代理）。
"""
import os
import re

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame,
    QScrollArea, QGridLayout, QStackedWidget, QSizePolicy,
    QSizePolicy as _QSP, QDialog, QTreeWidget, QTreeWidgetItem,
    QComboBox, QGroupBox,
)

from app import model_store as ms
from app.workers import swap_worker


def _norm(s: str) -> str:
    """归一化（小写去非字母数字）——用于「已装」启发式匹配"""
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def _fmt_count(n: int) -> str:
    n = int(n or 0)
    if n >= 1000000:
        return f"{round(n / 1000000, 1)}M"
    if n >= 1000:
        return f"{round(n / 1000)}K"
    return str(n)


# ================================================== 后台线程
class SearchWorker(QThread):
    sig_done = Signal(object, str, object)   # rows, err, next_cursor

    def __init__(self, keyword, cursor=None):
        super().__init__()
        self.keyword = keyword
        self.cursor = cursor

    def run(self):
        try:
            # 首页 2 参调用（兼容不带 cursor 形参的旧打桩）；有游标才带第 3 参
            rows = (ms.search_models(self.keyword, 30, self.cursor)
                    if self.cursor else ms.search_models(self.keyword, 30))
            self.sig_done.emit(rows, "", getattr(rows, "next_cursor", None))
        except Exception as e:
            self.sig_done.emit([], str(e), None)


class CivitaiSearchWorker(QThread):
    """CivitAI 镜像搜索（civitai.red）"""
    sig_done = Signal(object, str, object)   # rows, err, next_cursor

    def __init__(self, keyword, cursor=None):
        super().__init__()
        self.keyword = keyword
        self.cursor = cursor

    def run(self):
        try:
            rows = (ms.search_civitai(self.keyword, 20, self.cursor)
                    if self.cursor else ms.search_civitai(self.keyword, 20))
            self.sig_done.emit(rows, "", getattr(rows, "next_cursor", None))
        except Exception as e:
            self.sig_done.emit([], str(e), None)


class CivitaiFilesWorker(QThread):
    """取 CivitAI 模型最新版本文件列表（仅元数据）"""
    sig_done = Signal(str, list, str)      # (model_name, files, err)

    def __init__(self, model_id):
        super().__init__()
        self.model_id = model_id

    def run(self):
        try:
            files, name = ms.civitai_files(self.model_id)
            self.sig_done.emit(name, files, "")
        except Exception as e:
            self.sig_done.emit("", [], str(e))


class MirrorFallbackWorker(QThread):
    """HF 镜像多策略检索（CivitAI 详情弹窗兜底）：derive_mirror_queries 多词组合并去重。

    2026-10-31 实测：CivitAI 文件名直搜 HF 镜像 0 命中，拆实义词组
    （如 "controlnet tile"）命中 8 个社区同步仓库——故按多策略检索词
    逐个搜，合并去重后统一展示。单策略失败不拖垮整组。
    """
    sig_done = Signal(list, str)

    def __init__(self, model_name, file_names=None):
        super().__init__()
        self.model_name = model_name or ""
        self.file_names = [x for x in (file_names or []) if x]

    def run(self):
        try:
            queries = ms.derive_mirror_queries(self.model_name, self.file_names)
            merged, seen = [], set()
            for q in queries:
                try:
                    rows = ms.search_models(q, 10)
                except Exception:
                    continue
                for m in rows:
                    rid = m.get("id") or ""
                    if rid and rid not in seen:
                        seen.add(rid)
                        merged.append(m)
            self.sig_done.emit(merged, "")
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
    """顺序下载队列（无下载中心时的兜底）：items = [(repo_id, file_path, dest_dir), ...]"""
    # 2026-10-31 修：字节数可能超 int32（大模型 7GB+），用 object 承载防溢出
    sig_progress = Signal(object, object, str)   # done, total, fname
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


# ================================================== 对话框
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
            # 2026-10-31 修：默认不勾——此前全选默认会把整个仓库（可达几十 GB）
            # 一次性排进下载队列，用户点「开始下载」就触发意外巨量下载
            it.setCheckState(0, Qt.Unchecked)
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


class CivitaiDialog(QDialog):
    """CivitAI 模型详情：原始文件（⬇ 直链下载 / 🔗 复制直链）+ HF 镜像多策略同步仓库兜底

    背景：civitai.red 只代理元数据，文件直链（civitai.com/api/download/…）
    307→b2.civitai.com 国内直连不稳。2026-10-31 起文件行三路可下：
      ① ⬇ 下载——直链走 URL 直传下载管道（断点续传，网络通则直接下）
      ② 🔗 复制——直链进剪贴板，网络不通时用外部工具下载
      ③ HF 镜像多策略检索社区同步仓库（derive_mirror_queries）兜底
    """

    def __init__(self, parent, model_id, model_name, files, on_download=None):
        super().__init__(parent)
        self.setWindowTitle(f"CivitAI —— {model_name or model_id}")
        self.resize(680, 540)
        self.model_name = model_name
        self._files = files or []
        self._on_download = on_download
        self._hf_rows = []
        self._hf_worker = None

        root = QVBoxLayout(self)

        tip = QLabel("文件行「⬇ 下载」走直链直传（国内网络可能超时，失败可点「🔗 复制」"
                     "拿直链用工具下载）；下方自动检索 HF 镜像社区同步仓库兜底：")
        tip.setStyleSheet("color:#e5c07b;")
        tip.setWordWrap(True)
        root.addWidget(tip)

        g1 = QGroupBox("CivitAI 原始文件（⬇ 直链下载 / 🔗 复制直链）")
        v1 = QVBoxLayout(g1)
        self.tb_files = QTableWidget(0, 4)
        self.tb_files.setHorizontalHeaderLabels(["文件", "大小", "类型", "操作"])
        hh1 = self.tb_files.horizontalHeader()
        hh1.setSectionResizeMode(0, QHeaderView.Stretch)
        hh1.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh1.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh1.setSectionResizeMode(3, QHeaderView.Fixed)
        self.tb_files.setColumnWidth(3, 150)
        self.tb_files.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb_files.verticalHeader().setVisible(False)
        self.tb_files.setRowCount(len(self._files))
        for i, f in enumerate(self._files):
            self.tb_files.setItem(i, 0, QTableWidgetItem(f["name"]))
            sz = QTableWidgetItem(f"{f['size_mb']:,.0f} MB")
            sz.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tb_files.setItem(i, 1, sz)
            self.tb_files.setItem(i, 2, QTableWidgetItem(f["type"]))
            self.tb_files.setCellWidget(i, 3, self._ops_widget(f))
        from app.ui.theme import fit_table
        fit_table(self.tb_files)   # 对话框懒创建，主窗扫描覆盖不到，就地统一行高
        v1.addWidget(self.tb_files)
        root.addWidget(g1, 1)

        g2 = QGroupBox("HF 镜像同名同步仓库（可下载）")
        v2 = QVBoxLayout(g2)
        self.lb_hf = QLabel("正在搜索 HF 镜像…")
        self.lb_hf.setStyleSheet("color:#9aa4b2;")
        v2.addWidget(self.lb_hf)
        self.tb_hf = QTableWidget(0, 3)
        self.tb_hf.setHorizontalHeaderLabels(["仓库", "下载量", "操作"])
        hh = self.tb_hf.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.tb_hf.setColumnWidth(2, 100)
        self.tb_hf.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb_hf.verticalHeader().setVisible(False)
        fit_table(self.tb_hf)
        v2.addWidget(self.tb_hf)
        root.addWidget(g2, 1)

        bb = QHBoxLayout()
        bb.addStretch()
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        bb.addWidget(b_close)
        root.addLayout(bb)

        self._start_hf_search()

    def _ops_widget(self, f):
        """文件行操作列：⬇ 直链下载（URL 直传管道）+ 🔗 复制直链"""
        ops = QWidget()
        h = QHBoxLayout(ops)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(4)
        url = f.get("downloadUrl") or ""
        fname = f.get("name") or "model.safetensors"

        b_dl = QPushButton("⬇ 下载")
        b_dl.setFixedHeight(24)
        if url and self._on_download:
            b_dl.clicked.connect(
                lambda _c=False, u=url, fn=fname, b=b_dl: self._do_download(u, fn, b))
        else:
            b_dl.setEnabled(False)
            b_dl.setToolTip("无直链元数据" if not url else "")
        b_cp = QPushButton("🔗 复制")
        b_cp.setFixedHeight(24)
        if url:
            b_cp.clicked.connect(
                lambda _c=False, u=url, b=b_cp: self._copy_link(u, b))
        else:
            b_cp.setEnabled(False)
        h.addWidget(b_dl)
        h.addWidget(b_cp)
        return ops

    def _do_download(self, url, fname, btn):
        """⬇ 直链下载：交回市场面板走 URL 直传管道（断点续传），对话框不关闭"""
        self._on_download(url, fname)
        old = btn.text()
        btn.setText("已入队 ✓")
        QTimer.singleShot(1500, lambda: btn.setText(old))

    def _copy_link(self, url, btn):
        QApplication.clipboard().setText(url)
        btn.setText("已复制 ✓")
        QTimer.singleShot(1500, lambda: btn.setText("🔗 复制"))

    def _start_hf_search(self):
        name = self.model_name or ""
        fnames = [f.get("name") or "" for f in self._files]
        if not name and not any(fnames):
            self.lb_hf.setText("（无模型名/文件名，无法自动检索，请手动在市场搜同名仓库）")
            return
        self.lb_hf.setText("正在检索 HF 镜像社区同步仓库（模型名 + 文件名词组多策略）…")
        w = MirrorFallbackWorker(name, fnames)
        w.sig_done.connect(self._on_hf)
        swap_worker(self, "_hf_worker", w)
        w.start()

    def _on_hf(self, rows, err):
        if err:
            self.lb_hf.setText(f"HF 镜像检索失败：{err}")
            return
        self._hf_rows = rows
        self.tb_hf.setRowCount(len(rows))
        for i, m in enumerate(rows):
            it = QTableWidgetItem(m["id"])
            it.setData(Qt.UserRole, m["id"])
            self.tb_hf.setItem(i, 0, it)
            d = QTableWidgetItem(f"{m['downloads']:,}")
            d.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.tb_hf.setItem(i, 1, d)
            b = QPushButton("下载 ↓")
            b.clicked.connect(lambda _c=False, rid=m["id"]: self._pick(rid))
            self.tb_hf.setCellWidget(i, 2, b)
        if rows:
            self.lb_hf.setText(
                f"找到 {len(rows)} 个社区同步仓库（多策略合并去重，文件名可能与 CivitAI 不同）")
        else:
            self.lb_hf.setText(
                "HF 镜像暂未找到同步仓库——可点上方「🔗 复制」直链用工具下载")

    def _pick(self, repo_id):
        self.picked_repo = repo_id
        self.accept()


# ================================================== 卡片
class ModelCard(QFrame):
    """单个模型仓库卡片：色点 + 名称 + 作者/类型 + 统计 + 操作按钮"""
    sig_download = Signal(str)        # repo_id（HF）
    sig_detail = Signal(str, str)     # model_id, name（CivitAI）

    def __init__(self, m: dict, installed: bool, civitai: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(_QSP.Expanding, _QSP.Fixed)
        self.repo_id = m.get("id") or ""
        self.installed = installed

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(3)

        name = m.get("name") or m.get("id") or ""
        kind = (m.get("pipeline") or m.get("type") or "").lower()

        h1 = QHBoxLayout()
        h1.setSpacing(7)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        color = "#4c8dff"
        if "lora" in kind:
            color = "#5dcaa5"
        elif "vae" in kind:
            color = "#ef9f27"
        elif "controlnet" in kind:
            color = "#d85a30"
        elif "upscal" in kind or "esrgan" in kind:
            color = "#b48ead"
        dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        h1.addWidget(dot)
        lb_name = QLabel(name)
        lb_name.setStyleSheet("font-weight:bold; color:#e8edf3; font-size:13px;")
        lb_name.setToolTip(m.get("id") or name)
        h1.addWidget(lb_name, 1)
        if installed:
            badge = QLabel("已装")
            badge.setObjectName("BadgeInstalled")
            h1.addWidget(badge)
        v.addLayout(h1)

        sub = m.get("author") or (m.get("type") if civitai else "") or ""
        if civitai and m.get("type"):
            sub = f"{m.get('type')}"
        lb_sub = QLabel(sub or "—")
        lb_sub.setStyleSheet("color:#7d8797; font-size:12px;")
        v.addWidget(lb_sub)

        h3 = QHBoxLayout()
        h3.setSpacing(12)
        parts = [f"↓ {_fmt_count(m.get('downloads'))}"]
        if not civitai:
            parts.append(f"♥ {_fmt_count(m.get('likes'))}")
            if m.get("pipeline"):
                parts.append(str(m["pipeline"]))
        lb_stat = QLabel(" · ".join(parts))
        lb_stat.setStyleSheet("color:#9aa4b2; font-size:12px;")
        h3.addWidget(lb_stat, 1)
        b = QPushButton("详情" if civitai else "下载")
        b.setFixedHeight(26)
        if not civitai:
            b.setProperty("accent", True)
        if civitai:
            b.clicked.connect(lambda _c=False, cid=m.get("id", ""), nm=name:
                              self.sig_detail.emit(str(cid), nm))
        else:
            b.clicked.connect(lambda _c=False, rid=m.get("id", ""): self.sig_download.emit(rid))
        h3.addWidget(b)
        v.addLayout(h3)


# ================================================== 面板
class ModelMarketWidget(QWidget):
    """在线搜索结果面板（嵌入 models_page，由统一搜索栏驱动）"""

    sig_quick_search = Signal(str)   # 空状态快捷词被点击

    _KIND_COLOR = {"info": "#5a636f", "ok": "#4cd47c",
                   "warn": "#e8a33d", "err": "#e24b4a"}

    def __init__(self, cfg, mainwin, local_names=None):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._local_names = local_names or (lambda: set())
        self._search_worker = None
        self._files_worker = None
        self._dl_worker = None
        self._src = "mirror"
        self._next_cursor = None                    # 下一页游标（None=没有更多）
        self._seen = set()                          # 已渲染卡片 id（加载更多去重）
        self._rendered = 0                          # 已渲染卡片数（网格行列定位）
        self._last_search = ("mirror", "", [])      # (source, kw, notes) 加载更多复用
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    # -------------------------------------------------- UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        # 状态条：圆点 + 文案 + 全局操作
        st = QFrame()
        st.setObjectName("Banner")
        h = QHBoxLayout(st)
        h.setContentsMargins(10, 6, 10, 6)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.dot.setStyleSheet("background:#5a636f; border-radius:4px;")
        h.addWidget(self.dot)
        self.lb_stat = QLabel("输入关键词搜索，或点下方快捷词开始。")
        self.lb_stat.setWordWrap(True)
        self.lb_stat.setStyleSheet("color:#c6cdd8;")
        h.addWidget(self.lb_stat, 1)
        b_cancel = QPushButton("取消全部")
        b_cancel.clicked.connect(self._cancel_dl)
        h.addWidget(b_cancel)
        b_open = QPushButton("📂 models 目录")
        b_open.clicked.connect(self._open_models_dir)
        h.addWidget(b_open)
        root.addWidget(st)

        self.empty_w = self._build_empty()
        self.scroll = self._build_results()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty_w)   # 0
        self.stack.addWidget(self.scroll)    # 1
        root.addWidget(self.stack, 1)

    def _build_empty(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 20, 8, 12)
        v.addStretch()
        t = QLabel("🛒 在线模型商店（免登录 · 直连可下）")
        t.setStyleSheet("font-size:15px; font-weight:bold; color:#e8edf3;")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        s = QLabel("搜索 HuggingFace / CivitAI 模型仓库，勾选文件一键下到实例 models 目录")
        s.setStyleSheet("color:#9aa4b2;")
        s.setAlignment(Qt.AlignCenter)
        v.addWidget(s)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        for kw in ("sdxl", "flux", "vae", "lora", "controlnet",
                   "upscale", "realistic", "anime"):
            b = QPushButton(kw)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=kw: self.sig_quick_search.emit(k))
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)

        tip = QLabel("中文搜索自动换词：写实→realistic · 二次元→anime · 国风→chinese style")
        tip.setObjectName("HintText")
        tip.setAlignment(Qt.AlignCenter)
        v.addWidget(tip)
        v.addStretch()
        return w

    def _build_results(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.cards_host = QWidget()
        host_v = QVBoxLayout(self.cards_host)
        host_v.setContentsMargins(0, 4, 0, 4)
        host_v.setSpacing(8)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        for c in range(3):
            self.grid.setColumnStretch(c, 1)
        host_v.addWidget(self.grid_host, 1)

        # 分页行：「加载更多」按钮 + 已加载计数（滚动到底也会自动加载）
        self.more_row = QWidget()
        mh = QHBoxLayout(self.more_row)
        mh.setContentsMargins(0, 6, 0, 6)
        mh.addStretch()
        self.btn_more = QPushButton("加载更多 ↓")
        self.btn_more.clicked.connect(self._load_more)
        mh.addWidget(self.btn_more)
        self.lb_more = QLabel("")
        self.lb_more.setStyleSheet("color:#7d8797;")
        mh.addWidget(self.lb_more)
        mh.addStretch()
        host_v.addWidget(self.more_row)

        scroll.setWidget(self.cards_host)
        scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        return scroll

    # -------------------------------------------------- 状态
    def set_status(self, text, kind="info"):
        self.lb_stat.setText(text)
        color = self._KIND_COLOR.get(kind, "#5a636f")
        self.dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        self.lb_stat.setStyleSheet(
            "color:#e8a33d;" if kind == "warn"
            else "color:#e24b4a;" if kind == "err"
            else "color:#c6cdd8;")

    def show_hint(self, text):
        """轻提示（如空输入）：黄点状态条，不打断当前页面"""
        self.set_status(text, "warn")

    # -------------------------------------------------- 搜索
    def run_search(self, source: str, keyword: str):
        """source: 'mirror' | 'hf' | 'civitai'"""
        kw = (keyword or "").strip()
        if not kw:
            self.show_hint("请先输入关键词（中文会自动换英文词），或点上方快捷词。")
            return
        if self._search_worker and self._search_worker.isRunning():
            self.set_status("正在搜索中，请稍候…", "info")
            return
        self._src = source
        self._next_cursor = None
        ms.set_source("mirror" if source == "civitai" else source)
        en_kw, notes = ms.translate_keyword(kw)
        note = f"（已换词：{'、'.join(notes)}）" if notes and en_kw != kw else ""
        self.set_status(f"搜索「{en_kw}」中…{note}", "info")
        self._spawn_search(source, en_kw, notes)

    def _spawn_search(self, source, en_kw, notes, cursor=None, append=False):
        self._last_search = (source, en_kw, notes)
        if source == "civitai":
            w = CivitaiSearchWorker(en_kw, cursor)
        else:
            w = SearchWorker(en_kw, cursor)
        w.sig_done.connect(
            lambda rows, err, nxt, src=source, kw=en_kw, nt=notes, ap=append:
            self._on_results(rows, err, nxt, src, kw, nt, ap))
        swap_worker(self, "_search_worker", w)
        w.start()

    def _load_more(self):
        """加载下一页（按钮 / 滚动到底共用入口，worker 在跑时互斥）"""
        if self._search_worker and self._search_worker.isRunning():
            return
        if not self._next_cursor:
            return
        if self.stack.currentWidget() is not self.scroll:
            return
        self.btn_more.setEnabled(False)
        self.btn_more.setText("加载中…")
        self.set_status("正在加载下一页…", "info")
        src, kw, notes = self._last_search
        self._spawn_search(src, kw, notes, cursor=self._next_cursor, append=True)

    def _on_scroll(self, value):
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - 48:
            self._load_more()   # 滚动到底自动加载（_load_more 内部互斥防抖）

    def _on_results(self, rows, err, next_cursor, source, kw, notes, append=False):
        self.btn_more.setEnabled(True)
        self.btn_more.setText("加载更多 ↓")
        note = f"（已换词：{'、'.join(notes)}）" if notes else ""
        if err:
            if source == "civitai":
                # civitai.red 间歇性不可用（SSL 被重置）：自动降级 hf-mirror 继续搜
                self.set_status(
                    f"civitai.red 连接失败（{str(err)[:60]}），已自动改用 hf-mirror…", "warn")
                ms.set_source("mirror")
                self._src = "mirror"
                self._spawn_search("mirror", kw, notes)
                return
            self.set_status(
                (f"加载下一页失败：{str(err)[:120]}" if append
                 else f"搜索失败：{str(err)[:120]}"), "err")
            return
        self._next_cursor = next_cursor if rows else None
        if not rows and append:
            self.more_row.setVisible(False)
            self.set_status(f"已到末页，共 {self._rendered} 个结果{note}", "info")
            return
        if not rows:
            self._fill_cards([], source)
            self.set_status(
                f"「{kw}」没有搜到结果{note}。模型仓库多为英文命名，"
                "试试：写实→realistic · 二次元→anime · 放大→upscale。", "warn")
            return
        self._fill_cards(rows, source, append=append)
        src_name = "CivitAI" if source == "civitai" else "hf-mirror"
        if append:
            more = "，滚动到底或点「加载更多」继续" if self._next_cursor else "（已到末页）"
            self.set_status(
                f"{src_name}「{kw}」：已加载 {self._rendered} 个结果{more}{note}", "ok")
        else:
            more = "，往下滚动可继续加载" if self._next_cursor else ""
            self.set_status(
                f"{src_name} 搜索「{kw}」：{len(rows)} 个结果"
                f"（按下载量排序）{more}{note}", "ok")

    def _fill_cards(self, rows, source, append=False):
        if not append:
            while self.grid.count():
                it = self.grid.takeAt(0)
                w = it.widget()
                if w:
                    w.deleteLater()
            self._seen = set()
            self._rendered = 0
        if not rows and not append:
            self.stack.setCurrentWidget(self.empty_w)
            self.more_row.setVisible(False)
            return
        civitai = source == "civitai"
        names = set()
        try:
            names = {_norm(x) for x in (self._local_names() or []) if x}
        except Exception:
            pass
        ncol = 3
        for m in rows:
            rid = m.get("id") or m.get("name") or ""
            if rid and rid in self._seen:
                continue                    # 加载更多去重（游标翻页可能回捞同条）
            if rid:
                self._seen.add(rid)
            nm = m.get("name") or m.get("id") or ""
            nn = _norm(nm)
            installed = bool(nn) and len(nn) >= 5 and any(nn in ln for ln in names)
            card = ModelCard(m, installed, civitai)
            if civitai:
                card.sig_detail.connect(self._open_civitai)
            else:
                card.sig_download.connect(self._pick_files)
            self.grid.addWidget(card, self._rendered // ncol, self._rendered % ncol)
            self._rendered += 1
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)
        self.grid.setRowStretch((self._rendered + ncol - 1) // ncol, 1)
        self.stack.setCurrentWidget(self.scroll)
        self.more_row.setVisible(bool(self._next_cursor))
        self.lb_more.setText(f"已加载 {self._rendered} 个")

    # -------------------------------------------------- CivitAI 详情
    def _open_civitai(self, model_id, model_name):
        """取 CivitAI 文件元数据 → 弹对话框（原始文件 + HF 同步仓库兜底）"""
        if self._files_worker and self._files_worker.isRunning():
            return
        self.set_status(f"获取 CivitAI 模型 {model_name} 的文件信息…", "info")
        w = CivitaiFilesWorker(model_id)
        w.sig_done.connect(
            lambda name, files, err, mid=model_id: self._on_civitai_files(mid, name, files, err))
        swap_worker(self, "_files_worker", w)
        w.start()

    def _on_civitai_files(self, model_id, name, files, err):
        if err:
            self.set_status(f"CivitAI 模型信息获取失败：{str(err)[:100]}", "err")
            return
        dlg = CivitaiDialog(self, model_id, name, files,
                            on_download=self._civitai_download)
        dlg.exec()
        repo = getattr(dlg, "picked_repo", None)
        if repo:
            self._pick_files(repo)      # 走既有 HF 下载链路

    def _civitai_download(self, url, fname):
        """CivitAI 文件行 ⬇：URL 直传下载（model_store.download_file 支持直链），
        分类按文件名预判，落实例 models/<分类>/。"""
        if not url:
            self.show_hint("该文件无直链元数据，请用 🔗 复制或走 HF 同步仓库")
            return
        self._start_download([(url, fname, ms.guess_category(fname))])

    # -------------------------------------------------- 选文件
    def _pick_files(self, repo_id):
        if self._files_worker and self._files_worker.isRunning():
            return
        self.set_status(f"获取 {repo_id} 的文件列表…", "info")
        w = ListFilesWorker(repo_id)
        w.sig_done.connect(lambda files, err, rid=repo_id: self._on_files(rid, files, err))
        swap_worker(self, "_files_worker", w)
        w.start()

    def _on_files(self, repo_id, files, err):
        if err:
            self.set_status(f"文件列表获取失败：{str(err)[:100]}", "err")
            return
        if not files:
            QMessageBox.information(self, "无权重文件", "该仓库中没有找到模型权重文件")
            return
        self.set_status(f"获取到 {len(files)} 个权重文件，勾选后开始下载", "ok")
        dlg = DownloadDialog(self, repo_id, files)
        if dlg.exec() == QDialog.Accepted and dlg.result_items:
            self._start_download(dlg.result_items)

    # -------------------------------------------------- 下载
    @staticmethod
    def _resolve_dests(items, comfyui_path):
        """(repo_id, path, 分类名|目录) → (repo_id, path, 绝对 dest_dir)

        2026-10-31 修：DownloadDialog 传出第三元是分类名（如 "checkpoints"），
        而下载中心/兜底队列要的是目标目录——此前分类名被当相对路径，
        模型被写进进程 CWD\\<分类>\\，落不到实例 models 目录。统一在此转换。
        """
        mroot = os.path.join(comfyui_path, "models")
        out = []
        for rid, path, third in items:
            third = str(third or "checkpoints")
            if os.path.isabs(third) or ("/" in third or "\\" in third):
                dest = third
            else:
                dest = os.path.join(mroot, third)
            out.append((rid, path, dest))
        return out

    def _start_download(self, items):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "提示", "请先在「实例」页选择 ComfyUI 实例")
            return
        items = self._resolve_dests(items, inst["comfyui_path"])
        # 统一走下载中心（右上角 ⬇ 指示器 + 下拉进度面板）
        if getattr(self, "dl_center", None) is not None:
            self.dl_center.enqueue(items)
            self.set_status(
                f"已加入下载队列（{len(items)} 个文件），右上角 ⬇ 查看进度", "ok")
            return
        if self._dl_worker and self._dl_worker.isRunning():
            QMessageBox.information(self, "提示", "已有下载任务进行中")
            return
        w = DownloadWorker(items)
        w.sig_error.connect(lambda e: self.set_status(f"下载出错：{str(e)[:80]}", "err"))
        w.sig_done.connect(
            lambda ok, bad: self.set_status(f"下载队列结束：成功 {ok}，失败 {bad}",
                                            "ok" if ok and not bad else "warn"))
        swap_worker(self, "_dl_worker", w)
        w.start()   # 兜底下载通道（无下载中心时）
        self.set_status(f"下载到 {items[0][2]} …", "info")

    def _cancel_dl(self):
        done = False
        dc = getattr(self, "dl_center", None)
        if dc is not None:
            try:
                dc.cancel_all()
                done = True
            except Exception:
                pass
        if self._dl_worker and self._dl_worker.isRunning():
            self._dl_worker.cancel()
            done = True
        self.set_status("已请求取消（保留 .part，下次续传）" if done else "当前没有下载任务",
                        "warn" if done else "info")

    def _open_models_dir(self):
        inst = self._inst()
        if inst:
            d = os.path.join(inst["comfyui_path"], "models")
            os.makedirs(d, exist_ok=True)
            from app import instance as inst_lib
            inst_lib.open_in_explorer(d)
