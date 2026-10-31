# -*- coding: utf-8 -*-
"""模型库页（v0.8.06 重构）：统一搜索栏 + 范围切换 + 分类 chips + 双内容区

交互（一条搜索框管全部，根治双搜索框混淆）：
  · 范围切换：[本地] [HF 镜像] [CivitAI] [HF 官方(代理)]
  · 本地范围：实时过滤——文件名 + 类别名都能搜（「全部」视图也能搜文件）
  · 在线范围：卡片式结果流（ModelMarketWidget），回车/点搜索触发
  · 分类 chips：本地=类别过滤；在线=类型词追加搜索（如 lora）
  · 下载统一走右上角 ⬇ 下载中心；直链下载保留在底部工具行
"""
import os
import shutil
import datetime

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QGroupBox, QFileDialog, QMessageBox,
    QButtonGroup, QScrollArea, QFrame, QStackedWidget,
)

from app.tools import ModelDownloader
from app import instance as inst_lib
from app.workers import swap_worker
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


# 在线范围追加的类型词（chips → 检索词）
_CHIP_EN = {
    "checkpoints": "checkpoint", "loras": "lora", "vae": "vae",
    "controlnet": "controlnet", "unet": "unet", "clip": "clip",
    "clip_vision": "clip vision", "upscale_models": "upscale",
    "embeddings": "embedding", "diffusion_models": "diffusion",
    "text_encoders": "text encoder",
}


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
        root.setSpacing(10)

        # 标题行：标题 | stretch | 实例信息 | 下载中心
        head = QHBoxLayout()
        t = QLabel("模型库")
        t.setStyleSheet("font-size:17px; font-weight:bold; color:#fff;")
        head.addWidget(t)
        head.addStretch()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        head.addWidget(self.lb_inst)
        self.dl_center = DownloadCenter()
        self.dl_center.sig_queue_done.connect(lambda *_: self.refresh_stats())
        head.addWidget(self.dl_center)
        root.addLayout(head)

        # 统一搜索行：范围切换 + 搜索框 + 搜索 + 本地操作
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.scope_group = QButtonGroup(self)
        self.scope_group.setExclusive(True)
        self._scope_defs = [
            ("本地", "local", "搜索本地模型文件名或类别名（实时过滤）"),
            ("HF 镜像", "mirror", "搜索 HuggingFace 国内镜像：sdxl / flux lora / vae…（回车）"),
            ("CivitAI", "civitai", "搜索 CivitAI 国内镜像（热门同步）…（回车）"),
            ("HF 官方", "hf", "搜索 huggingface.co（更新及时，需系统代理）…（回车）"),
        ]
        for i, (label, _key, _tip) in enumerate(self._scope_defs):
            b = QPushButton(label)
            b.setObjectName("ScopeBtn")
            b.setCheckable(True)
            b.setToolTip(_tip)
            self.scope_group.addButton(b, i)
            bar.addWidget(b)

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText(self._scope_defs[0][2])
        self.ed_search.textChanged.connect(self._on_search_text)
        self.ed_search.returnPressed.connect(self._do_search)
        bar.addWidget(self.ed_search, 1)

        b_search = QPushButton("🔍 搜索")
        b_search.setProperty("accent", True)
        b_search.clicked.connect(self._do_search)
        bar.addWidget(b_search)
        b_import = QPushButton("📥 导入")
        b_import.setToolTip("把本地模型文件复制/剪切进当前类别的 models 目录")
        b_import.clicked.connect(self._import_files)
        bar.addWidget(b_import)
        b_open = QPushButton("📂 打开")
        b_open.setToolTip("打开当前类别（或 models 根）目录")
        b_open.clicked.connect(self._open_dir)
        bar.addWidget(b_open)
        b_refresh = QPushButton("🔄 刷新")
        b_refresh.clicked.connect(self.refresh_stats)
        bar.addWidget(b_refresh)
        root.addLayout(bar)

        # 分类 chips（本地=类别过滤；在线=类型词追加）
        chips_host = QWidget()
        chips_h = QHBoxLayout(chips_host)
        chips_h.setContentsMargins(0, 0, 0, 0)
        chips_h.setSpacing(6)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        cats = [""] + list(ModelDownloader("").MODEL_SUBDIRS)
        for i, cat in enumerate(cats):
            b = QPushButton("全部" if not cat else cat)
            b.setObjectName("CatChip")
            b.setCheckable(True)
            b.setFixedHeight(24)
            self.chip_group.addButton(b, i)
            chips_h.addWidget(b)
        chips_h.addStretch()
        chips = QScrollArea()
        chips.setWidgetResizable(True)
        chips.setFrameShape(QFrame.NoFrame)
        chips.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        chips.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        chips.setFixedHeight(36)
        chips.setWidget(chips_host)
        root.addWidget(chips)

        # 内容区：本地表格 / 在线卡片面板
        self.stack = QStackedWidget()
        self._build_local_page()
        self.market = ModelMarketWidget(self.cfg, self.mainwin,
                                        local_names=self._local_names)
        self.market.dl_center = self.dl_center
        self.market.sig_quick_search.connect(self._on_quick_search)
        self.stack.addWidget(self.local_w)     # 0
        self.stack.addWidget(self.market)      # 1
        root.addWidget(self.stack, 1)

        # 直链下载（高级功能，单行工具条）
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
        root.addWidget(g)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.log.setMaximumHeight(110)
        root.addWidget(self.log, 0)

        # 初始范围（记忆上次选择；恢复时同步占位符，别只换选中态）
        saved = QSettings("LeiMengStudio", "ComfyHub").value("model_scope", "local")
        idx = next((i for i, (_l, k, _t) in enumerate(self._scope_defs) if k == saved), 0)
        self.scope_group.button(idx).setChecked(True)
        self.ed_search.setPlaceholderText(self._scope_defs[idx][2])
        self.stack.setCurrentIndex(0 if idx == 0 else 1)
        self.chip_group.button(0).setChecked(True)
        self.scope_group.idToggled.connect(self._on_scope_toggled)
        self.chip_group.idToggled.connect(self._on_chip_toggled)

    def _build_local_page(self):
        self.local_w = QWidget()
        lv = QVBoxLayout(self.local_w)
        lv.setContentsMargins(0, 4, 0, 0)
        self.tb = QTableWidget(0, 3)
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        lv.addWidget(self.tb, 1)

    # ---------- 范围/chips/搜索 ----------
    def _scope_key(self) -> str:
        return self._scope_defs[self.scope_group.checkedId()][1]

    def _on_scope_toggled(self, idx, on):
        if not on:
            return
        key = self._scope_defs[idx][1]
        QSettings("LeiMengStudio", "ComfyHub").setValue("model_scope", key)
        self.ed_search.setPlaceholderText(self._scope_defs[idx][2])
        self.stack.setCurrentIndex(0 if key == "local" else 1)
        if key == "local":
            self.refresh_stats()
        elif self.ed_search.text().strip():
            self._do_search()

    def _on_chip_toggled(self, idx, on):
        if not on:
            return
        if self._scope_key() == "local":
            self.refresh_stats()
        elif self.ed_search.text().strip():
            self._do_search()

    def _on_search_text(self, _t):
        if self._scope_key() == "local":
            self.refresh_stats()

    def _on_quick_search(self, kw):
        self.ed_search.setText(kw)
        self._do_search()

    def _do_search(self):
        if self._scope_key() == "local":
            self.refresh_stats()
            return
        kw = self.ed_search.text().strip()
        if not kw:
            self.market.show_hint("请先输入关键词（中文会自动换英文词），或点空状态里的快捷词。")
            self.ed_search.setFocus()
            return
        cat = self._cur_cat()
        if cat:
            extra = _CHIP_EN.get(cat, cat.replace("_", " "))
            if extra.lower() not in kw.lower():
                kw = f"{kw} {extra}"
        self.market.run_search(self._scope_key(), kw)

    # ---------- 数据 ----------
    def _cur_cat(self) -> str:
        i = self.chip_group.checkedId()
        if i is None or i <= 0:
            return ""
        return ModelDownloader("").MODEL_SUBDIRS[i - 1]

    def _local_names(self):
        """所有本地模型文件名（供在线卡片「已装」启发式匹配）"""
        out = set()
        d_root = self._models_dir()
        if not d_root:
            return out
        for sub in ModelDownloader("").MODEL_SUBDIRS:
            d = os.path.join(d_root, sub)
            if os.path.isdir(d):
                for name in os.listdir(d):
                    if inst_lib.is_model_file(name):
                        out.add(name)
        return out

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
        self.lb_inst.setText(
            f"实例：{inst.get('name', '')}" if inst else "（未选择实例）")
        if not inst:
            self.tb.setColumnCount(3)
            self.tb.setHorizontalHeaderLabels(["提示", "", ""])
            self._add_row(["（未选择实例）", "-", "-"])
            return
        cat = self._cur_cat()
        kw = self.ed_search.text().strip()
        if cat:
            # 单类别：文件明细（支持文件名搜索）
            self.tb.setColumnCount(3)
            self.tb.setHorizontalHeaderLabels(["文件名", "大小", "修改日期"])
            files = self._list_files(cat, kw)
            if not files:
                self._add_row(["（此类别暂无文件）", "-",
                               "点范围切「HF 镜像」在线下载，或「导入」"],
                              {0: "#7d8797"})
            for name, size, mtime in files:
                dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                self._add_row([name, _fmt_size(size), dt])
        elif kw:
            # 「全部」视图 + 关键词：跨类别搜文件名（类别名命中→整类列出）
            self.tb.setColumnCount(4)
            self.tb.setHorizontalHeaderLabels(["文件名", "类别", "大小", "修改日期"])
            hit = False
            for sub in ModelDownloader("").MODEL_SUBDIRS:
                if kw.lower() in sub.lower():
                    files = self._list_files(sub, "")
                else:
                    files = self._list_files(sub, kw)
                for name, size, mtime in files:
                    dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    self._add_row([name, sub, _fmt_size(size), dt])
                    hit = True
            if not hit:
                self._add_row([f"（没有匹配「{kw}」的文件）", "-", "-",
                               "试试更短的关键词，或切到具体类别"],
                              {0: "#7d8797"})
        else:
            # 「全部」视图：分类统计
            self.tb.setColumnCount(3)
            self.tb.setHorizontalHeaderLabels(["类别", "文件数", "总体积"])
            rows = inst_lib.model_stats(inst["comfyui_path"])
            if not rows:
                self._add_row(["（models 目录为空）", "-",
                               "切范围「HF 镜像」在线下载"], {0: "#7d8797"})
            for cat_name, cnt, gb in rows:
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
                                    "请先在分类 chips 选择一个具体类别\n"
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
        w = DlWorker(ModelDownloader(inst["comfyui_path"]), url,
                     self.cb_sub.currentText(), "")
        swap_worker(self, "_worker", w)
        w.sig_log.connect(self._log)
        self._worker.sig_done.connect(
            lambda ok: (self._log("✅ 下载完成" if ok else "❌ 下载失败"),
                        self.refresh_stats()))
        self._worker.start()
