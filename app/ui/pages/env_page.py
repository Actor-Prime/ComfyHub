# -*- coding: utf-8 -*-
"""环境管理页：环境检测清单（对标 environment.js）+ 快照备份（envSnapshot.js）"""
import os
import re
import shutil

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QPlainTextEdit,
    QTabWidget, QFrame, QLineEdit, QFileDialog, QDialog, QCheckBox,
    QProgressBar, QVBoxLayout,
    QScrollArea, QComboBox, QTextBrowser,
)

from app import plugin_deps
from app.ui.pages.comfyui_page import _FlowBox
from app.i18n import T
from app.tools import EnvBackup, Rescue
from app.bootstrap import find_git
from app.winsub import run as sub_run
from app.workers import swap_worker
from app.ui.theme import card_table


# ---------------- 检测线程 ----------------
class DetectWorker(QThread):
    """逐项检测，每完成一项发一次信号 (索引, 状态文本, 是否OK)"""
    sig_item = Signal(int, str, bool)
    sig_done = Signal()

    def __init__(self, inst):
        super().__init__()
        self.inst = inst

    def run(self):
        py = self.inst.get("python_path", "")

        def probe(code, timeout=90):
            r = sub_run([py, "-c", code], capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "").strip()

        # 0 GIT
        git = find_git()
        if git:
            r = sub_run([git, "--version"], capture_output=True, text=True, timeout=15)
            self.sig_item.emit(0, (r.stdout or "git").strip()[:60], True)
        else:
            self.sig_item.emit(0, "未找到（可在设置中安装便携 MinGit）", False)
        # 1 Python
        if os.path.isfile(py):
            r = sub_run([py, "-c", "import sys;print(sys.version.split()[0])"],
                        capture_output=True, text=True, timeout=30)
            self.sig_item.emit(1, (r.stdout or "?").strip(), True)
        else:
            self.sig_item.emit(1, "路径无效", False)
        # 2/3 PyTorch + CUDA
        torch_ver, cuda = "未安装", "未安装"
        try:
            out = probe(
                "import torch;print(torch.__version__)\n"
                "print('yes' if torch.cuda.is_available() else 'no')\n"
                "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')\n",
                timeout=180)
            lines = [l for l in out.splitlines() if l.strip()]
            if lines:
                torch_ver = lines[0]
                if lines[1] == "yes":
                    cuda = f"CUDA 可用（{lines[2] if len(lines) > 2 else ''}）"
                else:
                    cuda = "仅 CPU"
            self.sig_item.emit(2, torch_ver, torch_ver != "未安装")
            self.sig_item.emit(3, cuda, "可用" in cuda)
        except Exception as e:
            self.sig_item.emit(2, f"检测失败: {type(e).__name__}", False)
            self.sig_item.emit(3, "-", False)
        # 4/5 显卡/驱动
        try:
            from app.bootstrap import detect_gpu
            g = detect_gpu()
            if g.vendor == "nvidia":
                self.sig_item.emit(4, f"{g.name}（{g.vram_mb}MB）", True)
                self.sig_item.emit(5, f"驱动 {g.driver} → 推荐写法 {g.torch_variant}",
                                   g.driver >= 525)
            elif g.vendor:
                self.sig_item.emit(4, g.name, True)
                self.sig_item.emit(5, "非 N 卡，暂用 CPU 模式", False)
            else:
                self.sig_item.emit(4, "未检测到显卡", False)
                self.sig_item.emit(5, "-", False)
        except Exception as e:
            self.sig_item.emit(4, f"检测失败: {e}", False)
            self.sig_item.emit(5, "-", False)
        # 6 CPU
        import psutil
        import platform
        self.sig_item.emit(6, platform.processor() or "未知", True)
        self.sig_done.emit()



class FixDialog(QDialog):
    """修复进度窗口：阶段 + 真实百分比进度条 + 实时日志

    2026-11-08 用户报「这个修复中，能不能看进度」—— 加实时日志；
    2026-11-09 再报「修复那里出个修复进度」—— 从"只会转的不确定条"升级为：
    阶段（准备/清理旧包/下载组件/安装组件/装后验证）+ 真实百分比
    （解析下载器日志「↓ 文件: 已下/总量 MB (p%)」）+ 失败红/成功绿。
    阶段行按 i18n 铁律拆控件：静态前缀 + 动态序号 + 静态阶段名。
    """
    STAGES = ("准备", "清理旧包", "下载组件", "安装组件", "装后验证")

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.NonModal)
        self.resize(760, 430)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)
        # 阶段行：静态「阶段」+ 动态「1/5」+ 阶段名 + 右侧百分比
        row_stage = QHBoxLayout()
        row_stage.setSpacing(8)
        self.lb_stage_prefix = QLabel("阶段")
        self.lb_stage_prefix.setObjectName("RowDesc")
        self.lb_stage_num = QLabel("1/5")
        self.lb_stage_num.setObjectName("RowDesc")
        self.lb_stage_name = QLabel(self.STAGES[0])
        self.lb_stage_name.setObjectName("RowTitle")
        self.lb_pct = QLabel("0%")
        self.lb_pct.setObjectName("RowTitle")
        row_stage.addWidget(self.lb_stage_prefix)
        row_stage.addWidget(self.lb_stage_num)
        row_stage.addWidget(self.lb_stage_name)
        row_stage.addStretch(1)
        row_stage.addWidget(self.lb_pct)
        v.addLayout(row_stage)
        self.lb_head = QLabel("准备中…")
        self.lb_head.setObjectName("RowDesc")
        self.lb_head.setWordWrap(True)
        v.addWidget(self.lb_head)
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogConsole")
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        v.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.pb = QProgressBar()
        self.pb.setObjectName("FixProgress")
        self.pb.setRange(0, 100)        # 真实百分比（下载日志可解析出进度）
        self.pb.setTextVisible(False)
        self.pb.setFixedHeight(4)
        row.addWidget(self.pb, 1)
        self.b_close = QPushButton("后台运行")
        self.b_close.setToolTip("关掉这个窗口不影响安装，装完会刷新检测结果")
        self.b_close.clicked.connect(self.close)
        row.addWidget(self.b_close)
        v.addLayout(row)

    def set_stage(self, n):
        n = max(1, min(5, int(n)))
        self.lb_stage_num.setText(f"{n}/5")
        self.lb_stage_name.setText(self.STAGES[n - 1])

    def set_progress(self, pct, detail=""):
        pct = max(0, min(100, int(pct)))
        self.pb.setValue(pct)
        self.lb_pct.setText(f"{pct}%")
        if detail:
            self.lb_head.setText(str(detail)[:150])

    def add(self, text):
        text = (text or "").rstrip()
        if not text:
            return
        self.log.appendPlainText(text)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def finish(self, ok, msg):
        self.pb.setProperty("status", "ok" if ok else "fail")
        self.pb.style().unpolish(self.pb)
        self.pb.style().polish(self.pb)
        if ok:
            self.pb.setValue(100)
            self.lb_pct.setText("100%")
        self.lb_head.setText(("✅ 完成：" if ok else "❌ 失败：") + str(msg)[:150])
        self.b_close.setText("关闭")
        self.add(("完成：" if ok else "失败：") + str(msg))


class FixWorker(QThread):
    """环境修复：装便携 MinGit / 装便携 Python / 重装 CUDA 版 torch

    2026-11-08：环境检测原来只报问题、不给动作 —— 检测出「PyTorch CUDA 仅 CPU」
    用户只能自己去翻文档。这里把 bootstrap 里现成的安装函数接成"一键修复"。
    2026-11-09：加阶段/百分比信号 —— 解析安装链日志换算真实进度
    （下载器输出「↓ 文件: 已下/总量 MB (p%)」，正好映射到下载阶段），
    解析异常绝不影响安装本体。
    """
    sig_log = Signal(str)
    sig_stage = Signal(int)              # 阶段 1..5（准备/清理旧包/下载组件/安装组件/装后验证）
    sig_progress = Signal(int, str)      # 总进度百分比, 当前状态描述（动态）
    sig_done = Signal(int, bool, str)        # 索引, 是否成功, 结果说明

    # 各阶段起始百分比：下载占大头（8→78），安装/验证给固定带
    _STAGE_BASE = {1: 0, 2: 5, 3: 8, 4: 78, 5: 92}
    _DL_RE = re.compile(r"↓\s*([^:：]+?)\s*[:：]\s*([\d.]+)\s*/\s*([\d.]+)\s*MB\s*\((\d+)%\)")

    def __init__(self, idx, kind, inst):
        super().__init__()
        self.idx, self.kind, self.inst = idx, kind, inst
        self._stage = 0
        self._pct = 0
        self._files = {}          # 文件名 -> [总量MB, 已下MB]

    # ---------- 进度换算 ----------
    def _log(self, msg):
        self.sig_log.emit(msg)
        try:
            self._track(msg or "")
        except Exception:
            pass

    def _emit_pct(self, pct, detail=""):
        pct = max(self._pct, min(99, int(pct)))   # 单调不回退
        self._pct = pct
        self.sig_progress.emit(pct, detail)

    def _set_stage(self, n):
        if n <= self._stage:
            return
        self._stage = n
        self.sig_stage.emit(n)
        self._emit_pct(self._STAGE_BASE[n])

    def _track(self, msg):
        m = msg.strip()
        if not m:
            return
        # 阶段识别（按安装链日志关键词）
        if "卸载旧 torch" in m:
            self._set_stage(2)
        elif ("预下载 wheel" in m or "复用本地 wheel" in m or "下载 " in m
              or self._DL_RE.search(m)):
            self._set_stage(3)
        elif ("回退直连 pip" in m or m.startswith("$ ")
              or "Installing collected" in m or "Successfully installed" in m):
            self._set_stage(4)
        elif "自检" in m or m.startswith("✅ torch") or m.startswith("❌ torch"):
            self._set_stage(5)
        # 下载百分比：↓ 文件: 已下/总量 MB (p%)
        md = self._DL_RE.search(m)
        if md:
            fname = md.group(1).strip()
            self._files[fname] = [float(md.group(3)), float(md.group(2))]
            self._emit_dl(fname)
            return
        if "复用本地 wheel" in m:
            fname = m.split("：", 1)[-1].split(":", 1)[-1].strip()
            if fname:
                self._files.setdefault(fname, [1.0, 1.0])   # 秒复用记满格
                self._emit_dl(fname)

    def _emit_dl(self, cur=""):
        total = sum(s for s, _ in self._files.values()) or 1.0
        done = sum(d for _, d in self._files.values())
        frac = max(0.0, min(1.0, done / total))
        band = self._STAGE_BASE[4] - self._STAGE_BASE[3]
        pct = self._STAGE_BASE[3] + band * frac
        detail = ""
        if cur and cur in self._files:
            s, d = self._files[cur]
            detail = f"下载 {cur}：{d:.0f}/{s:.0f} MB"
        self._emit_pct(pct, detail)

    def run(self):
        try:
            self._set_stage(1)
            if self.kind == "git":
                from app.bootstrap import install_min_git
                p = install_min_git(log=self._log)
                self.sig_done.emit(self.idx, bool(p), p or "安装失败")
            elif self.kind == "python":
                from app.bootstrap import install_portable_python, tools_root
                p = install_portable_python(tools_root(), log=self._log)
                self.sig_done.emit(self.idx, bool(p), p or "安装失败")
            elif self.kind == "torch":
                from app.bootstrap import install_torch, detect_gpu, tools_root
                gpu = detect_gpu()
                variant = getattr(gpu, "torch_variant", "") or "cu126"
                ok = install_torch(self.inst.get("python_path", ""), variant,
                                   log=self._log,
                                   wheels_dir=os.path.join(tools_root(), "wheels"))
                self.sig_done.emit(self.idx, bool(ok), f"torch {variant}")
            else:
                self.sig_done.emit(self.idx, False, "该项不支持自动修复")
        except Exception as e:                       # noqa: BLE001
            self.sig_done.emit(self.idx, False, f"{type(e).__name__}: {e}")


class EnvPage(QWidget):
    # 线程安全日志：bk.create/restore 在工作线程里直接调 log，
    # QWidget 不能跨线程碰 → 经信号回主线程（auto connection 自动 queued）
    sig_log = Signal(str)

    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._task = None
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_detect(), "环境检测")
        self.tabs.addTab(self._tab_snapshot(), "快照 / 备份")
        self.tabs.addTab(self._tab_deps(), "依赖管理")

    # ---------- 环境检测 ----------
    def _tab_detect(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        bar = QHBoxLayout()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(self.lb_inst)
        bar.addStretch()
        b_run = QPushButton("🔍 采集检测")
        b_run.setProperty("accent", True)
        b_run.clicked.connect(self._run_detect)
        bar.addWidget(b_run)
        v.addLayout(bar)

        # 检测项列表：和实例设置/系统设置一致，收进 1120 的居中内容列。
        # 1520 宽下 7 张卡片铺满整屏、右侧"未检测"贴着屏幕边，读起来很散（2026-11-08）
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(8)

        self.rows = []   # (row_frame, state_label)
        # 第 3 项是「修复类型」：失败时该行右侧出现「修复」按钮，点了走 bootstrap
        # 的安装函数（None = 硬件/无需修复，不显示按钮）
        CHECKS = [
            ("GIT", "检测 Git，插件安装/更新的基础设施", "git"),
            ("Python", "管理当前实例的 Python 运行环境", "python"),
            ("PyTorch", "深度学习框架的版本检测", None),
            ("PyTorch CUDA", "检测 PyTorch 是否可用 GPU 加速", "torch"),
            ("显卡", "检测显卡型号与显存", None),
            ("显卡驱动", "检测驱动版本，判断支持的 CUDA 上限", None),
            ("处理器 (CPU)", "处理器型号", None),
        ]
        self._fix_kinds = [c[2] for c in CHECKS]
        self.fix_btns = []
        self._fixing = False      # 修复进行中：期间不重跑检测、不显示修复按钮
        self._fix_idx = None
        # 7 张检测卡：宽屏排两列（复用 comfyui_page 的 _FlowBox）。
        # 单列铺满 2280 时每张卡的"名称+说明"后面直接接"未检测"，中间空 1800px
        grid_host = _FlowBox()
        grid_host.TWO_COL_MIN = 1500
        grid_host._grid.setContentsMargins(0, 0, 0, 0)
        for name, desc, fix_kind in CHECKS:
            f = QFrame()
            f.setObjectName("CheckRow")
            f.setAttribute(Qt.WA_StyledBackground, True)   # 否则 QSS 圆角/填充不生效
            h = QHBoxLayout(f)
            h.setContentsMargins(14, 10, 14, 10)
            left = QVBoxLayout()
            t = QLabel(name);  t.setObjectName("RowTitle")
            d = QLabel(desc);  d.setObjectName("RowDesc")
            left.addWidget(t)
            left.addWidget(d)
            h.addLayout(left, 1)
            st = QLabel("未检测")
            st.setObjectName("CheckState")
            st.setProperty("state", "pend")
            h.addWidget(st)
            # 修复按钮：默认隐藏，检测失败且该项支持修复时才出现
            fb = QPushButton("修复")
            fb.setObjectName("FixBtn")
            fb.setCursor(Qt.PointingHandCursor)
            fb.setVisible(False)
            fb.clicked.connect(
                lambda _c=False, i=len(self.rows), k=fix_kind: self._start_fix(i, k))
            h.addWidget(fb)
            self.fix_btns.append(fb)
            grid_host.addWidget(f)
            self.rows.append(st)
        bv.addWidget(grid_host)   # 不给 stretch：给了会把卡片行拉高成 120px
        bv.addStretch()
        # 卡片列表类内容列 1600（第 3 版，同 comfyui_page 的理由）
        body.setMaximumWidth(2280)
        host = QWidget()
        hl = QHBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        # 中间体给大 stretch（不是 1）：两侧 1:1 时 body 只能分到 1/3 宽度，
        # 卡在 ~500px 反而更窄。50:1:1 让 body 先吃饱到 maxWidth，余量再对半补两侧。
        hl.addStretch(1)
        hl.addWidget(body, 50)
        hl.addStretch(1)
        v.addWidget(host, 1)
        return w

    def _set_state(self, i, text, ok):
        st = self.rows[i]
        st.setText(text[:60] or "-")
        st.setProperty("state", "ok" if ok else "fail")
        st.style().unpolish(st)
        st.style().polish(st)
        st.setToolTip(text)
        # 失败 + 该项支持修复 → 显示「修复」按钮（修复中不抢状态）
        if i < len(self.fix_btns):
            bt = self.fix_btns[i]
            bt.setVisible(bool(not ok and self._fix_kinds[i] and not self._fixing))
            bt.setEnabled(True)

    # ---------- 一键修复 ----------
    def _start_fix(self, idx, kind):
        """点「修复」→ 后台跑 bootstrap 的安装函数，进度刷到状态标签上"""
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先添加实例")
            return
        if kind == "torch" and not inst.get("python_path"):
            QMessageBox.information(
                self, "ComfyHub",
                "该实例还没有配置 Python 路径，请先在「ComfyUI · 实例设置」里指定。")
            return
        self._fixing = True
        self._fix_idx = idx
        self.fix_btns[idx].setEnabled(False)
        self.rows[idx].setText("修复中…")
        # 进度窗口：pip 输出逐行灌进去，用户能看见走到哪一步
        _names = {0: "GIT", 1: "Python", 3: "PyTorch CUDA"}
        self._fix_dlg = FixDialog(f"正在修复：{_names.get(idx, '环境')}", self)
        self._fix_dlg.show()
        w = FixWorker(idx, kind, inst)
        w.sig_log.connect(self._fix_dlg.add)
        w.sig_log.connect(self._on_fix_log)
        w.sig_stage.connect(self._fix_dlg.set_stage)
        w.sig_progress.connect(self._fix_dlg.set_progress)
        w.sig_done.connect(self._on_fix_done)
        swap_worker(self, "_fix_worker", w)
        w.start()   # 2026-11-09 修：漏了这行 → 线程永不启动，对话框永停「1/5 准备 0%」

    def _on_fix_log(self, text):
        # torch 要下几个 GB，把最后一行进度顶到状态标签上，用户能看见动静
        if self._fix_idx is None:
            return
        lines = [l for l in (text or "").splitlines() if l.strip()]
        if not lines:
            return
        self.rows[self._fix_idx].setText(lines[-1][:60])

    def _on_fix_done(self, idx, ok, msg):
        self._fixing = False
        self._fix_idx = None
        dlg = getattr(self, "_fix_dlg", None)
        if dlg is not None:
            dlg.finish(ok, msg)
        if idx < len(self.fix_btns):
            self.fix_btns[idx].setEnabled(True)
        if not ok:
            self.rows[idx].setText(f"修复失败：{msg}"[:60])
            QMessageBox.warning(self, "修复失败", str(msg)[:500])
        # 成败都重跑一遍检测，把真实结果刷出来
        self._run_detect()

    def _run_detect(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先添加实例")
            return
        for st in self.rows:
            st.setText("检测中…")
            st.setProperty("state", "pend")
            st.style().unpolish(st)
            st.style().polish(st)
        w = DetectWorker(inst)
        w.sig_item.connect(self._set_state)
        w.sig_done.connect(lambda: self.lb_inst.setText(
            f"{T('实例')}: {inst.get('name')} — {T('检测完成')} ✅"))
        swap_worker(self, "_worker", w)
        w.start()

    def on_show(self):
        self.lb_inst.setText(
            f"{T('实例')}: {self._inst().get('name')}" if self._inst() else "（未选择实例）")
        self._refresh_snapshots()

    def on_instance_changed(self):
        self.on_show()

    # ---------- 快照 / 备份 ----------
    def _tab_snapshot(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.ed_label = QLineEdit()
        self.ed_label.setPlaceholderText("快照/备份备注（可空）")
        self.ed_label.setMaximumWidth(320)
        b_backup = QPushButton("创建环境备份")
        b_backup.setProperty("accent", True)
        b_backup.clicked.connect(self._create_backup)
        b_snap = QPushButton("创建救援快照")
        b_snap.clicked.connect(self._snapshot)
        b_restore = QPushButton("还原选中备份")
        b_restore.setProperty("danger", True)
        b_restore.clicked.connect(self._restore)
        b_rb = QPushButton("回滚选中快照")
        b_rb.setProperty("danger", True)
        b_rb.clicked.connect(self._rollback)
        bar.addWidget(self.ed_label, 0)
        bar.addWidget(b_backup)
        bar.addWidget(b_snap)
        # 破坏性操作（还原/回滚）与创建操作拉开距离，避免 4 个彩色按钮连成一排误点
        bar.addSpacing(14)
        bar.addWidget(b_restore)
        bar.addWidget(b_rb)
        bar.addStretch(1)
        v.addLayout(bar)

        self.tb = QTableWidget(0, 3)
        self.tb.setHorizontalHeaderLabels(["类型", "事件", "名称（时间_备注）"])
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.itemSelectionChanged.connect(self._show_detail)
        v.addWidget(card_table(self.tb), 1)

        # 详情面板：选中行 → 展示事件 / 内核 / 插件版本 / 工作流 / 模型哈希
        self.detail = QTextBrowser()
        self.detail.setObjectName("SnapDetail")
        self.detail.setOpenExternalLinks(False)
        self.detail.setPlaceholderText("选中上方条目查看环境详情（内核/插件/工作流/模型哈希）")
        self.detail.setMaximumHeight(190)
        v.addWidget(self.detail)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.sig_log.connect(self.log.appendPlainText)
        v.addWidget(self.log, 1)
        return w

    def _log(self, m):
        self.sig_log.emit(str(m))

    def _refresh_snapshots(self):
        inst = self._inst()
        if not inst:
            self.tb.setRowCount(0)
            return
        try:
            bk = EnvBackup(inst["comfyui_path"], inst["python_path"])
            rs = Rescue(inst["comfyui_path"])
            # (类型, 名称, 事件) —— 事件从 meta 读，旧条目显示 —
            items = []
            for x in bk.list_backups():
                meta = bk.load_meta(x)
                items.append(("备份", x, (meta or {}).get("event") or "—"))
            for x in rs.list_snapshots():
                meta = rs.load_meta(x)
                items.append(("快照", x, (meta or {}).get("event") or "—"))
        except Exception:
            items = []
        self.tb.setRowCount(len(items))
        for i, (kind, name, event) in enumerate(items):
            self.tb.setItem(i, 0, QTableWidgetItem(kind))
            self.tb.setItem(i, 1, QTableWidgetItem(event))
            self.tb.setItem(i, 2, QTableWidgetItem(name))
        self._show_detail()

    def _selected(self, kind):
        row = self.tb.currentRow()
        if row < 0 or self.tb.item(row, 0).text() != kind:
            return None
        return self.tb.item(row, 2).text()

    def _show_detail(self):
        """选中行 → 下方详情面板（内核版本/插件版本/工作流/模型哈希）"""
        row = self.tb.currentRow()
        if row < 0:
            self.detail.setHtml("<i>选中上方条目查看环境详情</i>")
            return
        kind = self.tb.item(row, 0).text()
        name = self.tb.item(row, 2).text()
        inst = self._inst()
        if not inst:
            return
        try:
            if kind == "备份":
                meta = EnvBackup(inst["comfyui_path"],
                                 inst["python_path"]).load_meta(name)
            else:
                meta = Rescue(inst["comfyui_path"]).load_meta(name)
        except Exception as e:
            self.detail.setHtml(f"<i>读取详情失败: {e}</i>")
            return
        self.detail.setHtml(_render_meta(kind, name, meta))

    def _create_backup(self):
        inst = self._inst()
        if not inst:
            return
        bk = EnvBackup(inst["comfyui_path"], inst["python_path"])
        self._task = _RunTask(lambda: bk.create(self.ed_label.text().strip(),
                                                log=self._log))
        # create 内部已 log"备份完成"，此处只刷新列表
        self._task.sig_done.connect(lambda name: self._refresh_snapshots())
        self._task.sig_err.connect(lambda e: (self._log(f"备份失败: {e}"),
                                              self._refresh_snapshots()))
        self._task.start()

    def _snapshot(self):
        inst = self._inst()
        if not inst:
            return
        rs = Rescue(inst["comfyui_path"])
        # manifest 含模型 sha256（大模型首次采集要十几秒）→ 走工作线程防卡 UI
        self._task = _RunTask(lambda: rs.snapshot(log=self._log,
                                                  event="手动创建"))
        self._task.sig_done.connect(lambda _ts: self._refresh_snapshots())
        self._task.sig_err.connect(lambda e: (self._log(f"快照失败: {e}"),
                                              self._refresh_snapshots()))
        self._task.start()

    def _restore(self):
        name = self._selected("备份")
        if not name:
            QMessageBox.information(self, "ComfyHub", "请选择一个[备份]行")
            return
        r = QMessageBox.question(self, "还原", "将按备份重装 pip 依赖（耗时较长），继续？")
        if r != QMessageBox.Yes:
            return
        inst = self._inst()
        bk = EnvBackup(inst["comfyui_path"], inst["python_path"])
        self._task = _RunTask(lambda: bk.restore(name, log=self._log))
        self._task.sig_done.connect(lambda ok: (
            self._log(f"还原{'成功' if ok else '失败'}: {name}"),
            QMessageBox.information(self, "ComfyHub", "还原完成" if ok else "还原失败")))
        self._task.sig_err.connect(lambda e: (
            self._log(f"还原失败: {e}"),
            QMessageBox.information(self, "ComfyHub", f"还原失败: {e}")))
        self._task.start()

    def _rollback(self):
        name = self._selected("快照")
        if not name:
            QMessageBox.information(self, "ComfyHub", "请选择一个[快照]行")
            return
        r = QMessageBox.question(self, "救援回滚",
                                 "禁用快照后新装的插件并恢复启停状态（不删文件）。继续？")
        if r != QMessageBox.Yes:
            return
        inst = self._inst()
        ok, msg = Rescue(inst["comfyui_path"]).rollback(name, log=self._log)
        self._log(f"回滚{'完成' if ok else '失败'}: {msg}")
        self._refresh_snapshots()




# ================= 依赖管理（对标管理大师 deps 管理） =================


    def _tab_deps(self):
        """依赖管理 tab（对标管理大师依赖管理）"""
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        b_detect = QPushButton("📦 依赖检测")
        b_detect.setProperty("accent", True)
        b_detect.clicked.connect(self._deps_detect)
        b_pdep = QPushButton("🔍 插件依赖自检")
        b_pdep.setToolTip("扫 custom_nodes 各插件的 requirements.txt，"
                          "找出没装上的包（插件报 ModuleNotFoundError / 工作流 400 的根因）")
        b_pdep.clicked.connect(self._plugin_deps_check)
        b_install = QPushButton("➕ 安装新依赖")
        b_install.setProperty("success", True)
        b_install.clicked.connect(self._deps_install)
        self.ed_pkg_kw = QLineEdit()
        self.ed_pkg_kw.setPlaceholderText("🔍 搜索已装依赖…")
        self.ed_pkg_kw.textChanged.connect(lambda _t: self._deps_filter())
        bar.addWidget(b_detect)
        bar.addWidget(b_pdep)
        bar.addWidget(b_install)
        bar.addWidget(self.ed_pkg_kw, 1)
        v.addLayout(bar)

        self.deps_log = QPlainTextEdit()
        self.deps_log.setReadOnly(True)
        self.deps_log.setObjectName("LogConsole")
        self.deps_log.setPlaceholderText("依赖检测/安装日志…")
        self.deps_log.setMaximumHeight(130)
        v.addWidget(self.deps_log)

        self.tb_deps = QTableWidget(0, 3)
        self.tb_deps.setHorizontalHeaderLabels(["依赖名", "版本号", "操作"])
        self.tb_deps.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb_deps.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tb_deps.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(card_table(self.tb_deps), 1)

        hint = QLabel("首次加载需要点上方【依赖检测】；卸载前请先停止 ComfyUI。"
                      "插件节点报 ModuleNotFoundError 时，先跑【插件依赖自检】。")
        hint.setStyleSheet("color:#7d8797;")
        v.addWidget(hint)
        return w

    # ---- 插件依赖自检（2026-11-14）----
    def _plugin_deps_check(self):
        """扫 custom_nodes 的 requirements.txt，和实例解释器已装的包比对"""
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先选择实例")
            return
        if not inst.get("comfyui_path"):
            QMessageBox.information(self, "ComfyHub", "该实例还没有 ComfyUI 目录")
            return
        self._deps_log("正在扫描插件依赖（custom_nodes/*/requirements.txt）…")
        w = PluginScanWorker(inst)
        w.sig_done.connect(self._plugin_deps_done)
        swap_worker(self, "_pdep_scan_worker", w)
        w.start()

    def _plugin_deps_done(self, rows, installed, err):
        if err:
            self._deps_log("❌ 插件依赖自检失败：" + str(err))
            QMessageBox.warning(self, "ComfyHub", "自检失败：" + str(err))
            return
        s = plugin_deps.summarize(rows)
        if not s["plugins"]:
            self._deps_log("没有发现带 requirements.txt 的插件")
            QMessageBox.information(self, "ComfyHub",
                                    "custom_nodes 下没有带 requirements.txt 的插件")
            return
        self._deps_log(f"✅ 自检完成：{s['plugins']} 个插件，{s['bad']} 个缺依赖，"
                       f"共缺 {s['missing']} 个包")
        inst = self._inst()
        dlg = PluginDepsDialog(self, inst, rows, installed)
        dlg.sig_installed.connect(lambda: (self._deps_log("依赖有变化，重新拉取已装列表…"),
                                           self._deps_detect()))
        dlg.exec()

    # ---- 依赖管理逻辑 ----
    def _deps_log(self, m):
        self.deps_log.appendPlainText(str(m))

    def _deps_detect(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先选择实例")
            return
        self._deps_log("正在检测已装依赖（pip list）…")
        w = PipListWorker(inst)
        w.sig_done.connect(self._deps_fill)
        swap_worker(self, "_pip_list_worker", w)
        w.start()

    def _deps_fill(self, pkgs, err):
        if err:
            self._deps_log(f"❌ 检测失败: {err}")
            return
        self._all_pkgs = pkgs
        self._deps_log(f"✅ 检测完成，共 {len(pkgs)} 个依赖")
        self._deps_render()

    def _deps_render(self):
        kw = self.ed_pkg_kw.text().strip().lower()
        self.tb_deps.setRowCount(0)
        for name, ver in getattr(self, "_all_pkgs", []):
            if kw and kw not in name.lower():
                continue
            r = self.tb_deps.rowCount()
            self.tb_deps.insertRow(r)
            self.tb_deps.setItem(r, 0, QTableWidgetItem(name))
            self.tb_deps.setItem(r, 1, QTableWidgetItem(ver))
            b = QPushButton("卸载")
            b.setStyleSheet("color:#e8635a; border:none; background:transparent;")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, n=name: self._deps_uninstall(n))
            self.tb_deps.setCellWidget(r, 2, b)

    def _deps_filter(self):
        self._deps_render()

    def _deps_uninstall(self, name):
        inst = self._inst()
        if not inst:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "卸载依赖前请先停止 ComfyUI")
            return
        r = QMessageBox.question(self, "卸载依赖",
                                 f"确定卸载 {name} 吗？\n（部分插件依赖它可能报错）")
        if r != QMessageBox.Yes:
            return
        self._deps_log(f"卸载 {name} …")
        w = PipOpWorker(inst["python_path"], ["uninstall", "-y", name])
        w.sig_log.connect(self._deps_log)
        w.sig_done.connect(
            lambda ok: (self._deps_log("✅ 卸载完成" if ok else "❌ 卸载失败"),
                        self._deps_detect()))
        swap_worker(self, "_deps_op_worker", w)
        w.start()

    def _deps_install(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先选择实例")
            return
        mirror = "https://mirrors.aliyun.com/pypi/simple/"
        dlg = DepsDialog(self, mirror)
        dlg.sig_install.connect(lambda args: self._deps_run_install(inst, args))
        dlg.exec()

    def _deps_run_install(self, inst, args):
        if args and args[0] == "__RUN_PY__":
            # 通过 Python 文件安装（运行脚本）
            script = args[1]
            self._deps_log(f"运行脚本 {script} …")
            # 直接 run python 脚本
            w = _RunTask(
                lambda: sub_run([inst["python_path"], script],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=3600))
            w.sig_done.connect(
                lambda r: self._deps_log("脚本输出尾: "
                                         + ((r.stdout or r.stderr or "")[-300:] if hasattr(r, "stdout") else str(r))))
            w.sig_err.connect(lambda e: self._deps_log(f"❌ 脚本执行失败: {e}"))
            swap_worker(self, "_deps_op_worker", w)
            w.start()
            return
        w = PipOpWorker(inst["python_path"], args)
        w.sig_log.connect(self._deps_log)
        w.sig_done.connect(
            lambda ok: (self._deps_log("✅ 安装完成" if ok else "❌ 安装失败"),
                        self._deps_detect()))
        swap_worker(self, "_deps_op_worker", w)
        w.start()

class PipListWorker(QThread):
    """pip list 后台检测"""
    sig_done = Signal(list, str)

    def __init__(self, inst, parent=None):
        # ⚠️ 必须显式收 inst：QThread.__init__(dict) 会直接 TypeError
        super().__init__(parent)
        self._inst = inst

    def run(self):
        inst = self._inst_ref()
        try:
            r = sub_run([inst["python_path"], "-m", "pip", "list",
                         "--format=freeze", "--disable-pip-version-check"],
                        capture_output=True, text=True, timeout=300)
            pkgs = []
            for ln in (r.stdout or "").splitlines():
                if "==" in ln:
                    name, ver = ln.split("==", 1)
                    pkgs.append((name.strip(), ver.strip()))
            self.sig_done.emit(pkgs, "")
        except Exception as e:
            self.sig_done.emit([], str(e))

    _inst = None
    def _inst_ref(self):
        return self._inst


class PipOpWorker(QThread):
    """通用 pip 操作（安装/卸载），日志实时回调"""
    sig_log = Signal(str)
    sig_done = Signal(bool)

    def __init__(self, python_exe, args, timeout=7200):
        super().__init__()
        self.python_exe = python_exe
        self.args = args
        self.timeout = timeout

    def run(self):
        self.sig_log.emit("$ " + " ".join([self.python_exe] + self.args[:14]))
        try:
            r = sub_run([self.python_exe, "-m", "pip"] + self.args,
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=self.timeout)
        except Exception as e:
            self.sig_log.emit(f"❌ 异常: {e}")
            self.sig_done.emit(False)
            return
        tail = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines()
                if "[safe-delete]" not in ln and ln.strip()]
        for ln in tail[-15:]:
            self.sig_log.emit("  " + ln[:160])
        self.sig_done.emit(r.returncode == 0)

COMMON_DEPS = [
    ("opencv-python", "图像处理（很多插件需要）"),
    ("rembg", "AI 抠图"),
    ("mediapipe", "人体/人脸关键点检测"),
    ("onnxruntime-gpu", "ONNX 推理（GPU）"),
    ("insightface", "人脸识别（IPAdapter/InstantID 用）"),
    ("huggingface_hub", "HF 模型下载工具"),
    ("comfyui-frontend-package", "ComfyUI 前端包（更新界面）"),
    ("scipy", "科学计算"),
]


# ---------------- 插件依赖自检（2026-11-14） ----------------
class PluginScanWorker(QThread):
    """扫 custom_nodes 各插件 requirements.txt，与实例解释器已装包比对"""
    sig_done = Signal(list, dict, str)      # (rows, installed, err)

    def __init__(self, inst, parent=None):
        super().__init__(parent)
        self._inst = inst

    def run(self):
        try:
            py = self._inst.get("python_path") or ""
            if not os.path.isfile(py):
                self.sig_done.emit([], {}, "解释器不存在：" + str(py))
                return
            installed = plugin_deps.installed_packages(py)
            rows = plugin_deps.scan(self._inst.get("comfyui_path") or "", installed)
            self.sig_done.emit(rows, installed, "")
        except Exception as e:                     # 线程里绝不能抛出去
            self.sig_done.emit([], {}, str(e))


class PluginInstallWorker(QThread):
    """按插件补装缺失依赖（2026-11-22 改成流式输出）

    两条保护（缺一条都可能把环境改坏）：
      · 每行只带「确实没装的包」→ 不会去动已装包；
      · 全局 -c 约束文件把当前所有已装版本钉死 → pip 只能找不冲突的版本，
        找不到就报错，而不是把 transformers / huggingface_hub 悄悄降级。

    2026-11-22 用户报障「补装卡住、日志空白」：旧实现是
    `capture_output=True` 一次读完，pip 的全部输出被吞到进程退出才吐 8 行，
    于是「解析依赖 20 分钟」和「2 秒就失败」在界面上一模一样。
    现在逐行流式吐 + 三档时间判定（见 plugin_deps.run_pip_stream），
    并且随时可以取消（pip 进程树一起杀，不留后台任务）。
    """
    sig_log = Signal(str)
    sig_state = Signal(str)                  # "idle" / "" → 对话框换按钮文案
    sig_done = Signal(list, str)             # (仍导不进来的包, err)

    def __init__(self, python_exe, rows, installed, mirror="", parent=None):
        super().__init__(parent)
        self.python_exe = python_exe
        self.rows = rows
        self.installed = installed
        self.mirror = mirror
        self._cancel = False

    # ---- 外部控制 ----
    def cancel(self):
        """请求终止（pip 进程树会被强杀，不会留后台任务）"""
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="ch_pdep_")
        failed, aborted = [], False
        try:
            cons = plugin_deps.write_constraints(
                self.installed, os.path.join(tmp, "constraints.txt"))
        except Exception as e:
            self.sig_done.emit([], "写约束文件失败：" + str(e))
            return
        n_pkg = sum(len(r["missing"]) for r in self.rows)
        self.sig_log.emit("== 开始：%d 个插件 / %d 个包（镜像 %s）"
                          % (len(self.rows), n_pkg, self.mirror or "官方"))
        for i, r in enumerate(self.rows):
            if self.cancelled():
                aborted = True
                self.sig_log.emit("⏹ 已终止：后续插件未处理")
                break
            text, base, special = plugin_deps.build_requirements([r])
            if base:
                self.sig_log.emit("   跳过底座包（由 ComfyHub 管，装了可能毁 torch）："
                                  + "、".join(base))
            if special:
                self.sig_log.emit("   需手工安装：" + "；".join(special)[:160])
            if not text.strip():
                continue
            self.sig_log.emit("== %s：补装 %d 个包" % (r["name"], len(r["missing"])))
            req = os.path.join(tmp, "req_%d.txt" % i)
            with open(req, "w", encoding="utf-8") as f:
                f.write(text)
            args = plugin_deps.install_args(req, cons, self.mirror)
            res = plugin_deps.run_pip_stream(
                self.python_exe, args,
                on_line=lambda ln: self.sig_log.emit("   " + str(ln)[:200]),
                on_state=lambda st: self.sig_state.emit(str(st)),
                cancelled=self.cancelled)
            self.sig_state.emit("")
            if res["cancelled"]:
                aborted = True
                failed.append(r["name"])
                self.sig_log.emit("⏹ %s 的补装被取消（pip 进程已终止）" % r["name"])
                break
            if res["idle"]:
                failed.append(r["name"])
                self.sig_log.emit("   ⚠️ %s 连续 %d 分钟没有任何输出，已中止"
                                  % (r["name"], plugin_deps.IDLE_KILL // 60))
                self.sig_log.emit("      → 多为依赖回溯打转或没有 Windows 轮子，"
                                  "可在插件目录里手动 pip install 再看报错")
                continue
            if res["timeout"]:
                failed.append(r["name"])
                self.sig_log.emit("   ⚠️ %s 超过 %d 分钟总时长，已中止"
                                  % (r["name"], plugin_deps.PLUGIN_TIMEOUT // 60))
                continue
            if res["code"] != 0:
                failed.append(r["name"])
                self.sig_log.emit("   ⚠️ %s 有包没装上（多为没有 Windows 轮子，"
                                  "其余插件继续）" % r["name"])
            else:
                self.sig_log.emit("   ✅ %s 完成" % r["name"])

        names = sorted({m["name"] for r in self.rows for m in r["missing"]})
        bad = []
        if names and not aborted:
            self.sig_log.emit("正在校验（逐个 import）…")
            try:
                bad = plugin_deps.verify(self.python_exe, names)
            except Exception as e:
                self.sig_done.emit([], "校验失败：" + str(e))
                return
        # 取消/中止/失败之后：跑一次 pip check，把半装的包列出来。
        # 只提示不自动回滚 —— 自动卸包比半装更容易连带弄坏别的插件。
        if failed or aborted:
            self.sig_log.emit("正在检查环境完整性（pip check）…")
            problems = plugin_deps.pip_check(self.python_exe)
            if problems:
                self.sig_log.emit("⚠ 以下包互相冲突（只提示，不自动回滚）：")
                for p in problems[:8]:
                    self.sig_log.emit("   " + p)
                self.sig_log.emit("   → 前往「依赖管理」按需卸载/重装，再复核")
            else:
                self.sig_log.emit("   ✅ 环境依赖关系正常（pip check 通过）")
        err = ""
        if bad:
            err = "以下包仍然导不进来：" + "、".join(bad[:8])
        elif failed:
            err = "有 %d 个插件没装全：%s" % (len(failed), "、".join(failed[:6]))
        self.sig_done.emit(bad, err)



class DepsDialog(QDialog):
    """安装新依赖：六种方式标签页（对标管理大师安装弹窗）"""
    sig_install = Signal(list)   # pip args

    def __init__(self, parent, mirror):
        super().__init__(parent)
        self.setWindowTitle("安装新依赖")
        self.resize(640, 420)
        self.mirror = mirror
        v = QVBoxLayout(self)
        self.tw = QTabWidget()
        v.addWidget(self.tw, 1)
        tip = QLabel("💡 温馨提示：可在【系统设置】切换国内镜像，提高安装速度！")
        tip.setStyleSheet("color:#4cd47c; background:rgba(76,212,124,.08);"
                          "border-radius:4px; padding:8px;")
        v.addWidget(tip)
        hb = QHBoxLayout()
        hb.addStretch()
        b_ok = QPushButton("开始安装")
        b_ok.setProperty("accent", True)
        b_ok.clicked.connect(self._collect)
        b_cancel = QPushButton("关闭")
        b_cancel.clicked.connect(self.reject)
        hb.addWidget(b_cancel)
        hb.addWidget(b_ok)
        v.addLayout(hb)

        # tab1 包名
        w1 = QWidget(); f1 = QVBoxLayout(w1)
        self.ed_names = QLineEdit()
        self.ed_names.setPlaceholderText("输入依赖包名（空格分隔多个），如: opencv-python rembg")
        f1.addWidget(self.ed_names)
        f1.addWidget(QLabel("等价于 pip install <包名>，走国内镜像。"))
        self.tw.addTab(w1, "根据名字安装")

        # tab2 requirements.txt
        w2 = QWidget(); f2 = QVBoxLayout(w2)
        h2 = QHBoxLayout()
        self.ed_req = QLineEdit()
        self.ed_req.setPlaceholderText("requirements.txt 文件路径")
        b2 = QPushButton("浏览…")
        b2.clicked.connect(lambda: self.ed_req.setText(QFileDialog.getOpenFileName(
            self, "选择 requirements.txt", "", "requirements (*.txt)")[0]))
        h2.addWidget(self.ed_req, 1)
        h2.addWidget(b2)
        f2.addLayout(h2)
        f2.addWidget(QLabel("等价于 pip install -r <文件>，适合批量补依赖。"))
        self.tw.addTab(w2, "根据 requirements.txt 安装")

        # tab3 whl 轮子
        w3 = QWidget(); f3 = QVBoxLayout(w3)
        h3 = QHBoxLayout()
        self.ed_whl = QLineEdit()
        self.ed_whl.setPlaceholderText("浏览轮子文件（.whl），如 torch-2.10.0+cu130-....whl")
        b3 = QPushButton("浏览 .whl")
        b3.clicked.connect(lambda: self.ed_whl.setText(QFileDialog.getOpenFileName(
            self, "选择 wheel 文件", "", "wheel (*.whl)")[0]))
        h3.addWidget(self.ed_whl, 1)
        h3.addWidget(b3)
        f3.addLayout(h3)
        f3.addWidget(QLabel("适合离线安装大包（torch 等），不受网络影响。"))
        self.tw.addTab(w3, "安装轮子文件")

        # tab4 python 文件
        w4 = QWidget(); f4 = QVBoxLayout(w4)
        h4 = QHBoxLayout()
        self.ed_py = QLineEdit()
        self.ed_py.setPlaceholderText("Python 脚本路径（如 get-pip.py）")
        b4 = QPushButton("浏览…")
        b4.clicked.connect(lambda: self.ed_py.setText(QFileDialog.getOpenFileName(
            self, "选择 Python 文件", "", "Python (*.py)")[0]))
        h4.addWidget(self.ed_py, 1)
        h4.addWidget(b4)
        f4.addLayout(h4)
        f4.addWidget(QLabel("以当前实例解释器运行该脚本（等价 python <文件>）。"))
        self.tw.addTab(w4, "通过 Python 文件安装")

        # tab5 常用依赖
        w5 = QWidget(); f5 = QVBoxLayout(w5)
        self.ck_list = []
        for name, desc in COMMON_DEPS:
            ck = QCheckBox(f"{name} — {desc}")
            self.ck_list.append((ck, name))
            f5.addWidget(ck)
        scroll5 = QScrollArea()
        scroll5.setWidgetResizable(True)
        scroll5.setFrameShape(QFrame.NoFrame)
        scroll5.setWidget(w5)
        self.tw.addTab(scroll5, "常用依赖")

        # tab6 执行命令
        w6 = QWidget(); f6 = QVBoxLayout(w6)
        self.ed_cmd = QLineEdit()
        self.ed_cmd.setPlaceholderText("pip 子命令与参数，如: install numpy --upgrade")
        f6.addWidget(self.ed_cmd)
        f6.addWidget(QLabel("以 python -m pip <你输入的内容> 执行，熟练玩家用。"))
        self.tw.addTab(w6, "执行命令")

    def _collect(self):
        i = self.tw.currentIndex()
        if i == 0:
            names = self.ed_names.text().split()
            if not names:
                return
            self.sig_install.emit(["install", *names, "-i", self.mirror,
                                   "--no-warn-script-location"])
        elif i == 1:
            f = self.ed_req.text().strip()
            if f:
                self.sig_install.emit(["install", "-r", f, "-i", self.mirror,
                                       "--no-warn-script-location"])
        elif i == 2:
            f = self.ed_whl.text().strip()
            if f:
                self.sig_install.emit(["install", f, "--no-warn-script-location"])
        elif i == 3:
            f = self.ed_py.text().strip()
            if f:
                self.sig_install.emit(["__RUN_PY__", f])
        elif i == 4:
            names = [n for ck, n in self.ck_list if ck.isChecked()]
            if not names:
                return
            self.sig_install.emit(["install", *names, "-i", self.mirror,
                                   "--no-warn-script-location"])
        elif i == 5:
            cmd = self.ed_cmd.text().strip().split()
            if cmd:
                self.sig_install.emit(cmd)
        self.accept()


from PySide6.QtCore import QThread as _QThread, Signal as _Signal


class PluginDepsDialog(QDialog):
    """插件依赖自检结果 + 勾选补装（2026-11-14）

    起因：共享 Python 只装了 ComfyUI 本体的 requirements，插件自带的一律没装。
    表现是插件加载失败、节点不注册，提交工作流直接 HTTP 400，
    用户只看到「未知节点」完全看不出是缺依赖。
    """
    sig_installed = _Signal()          # 装完 → 让依赖管理页刷新已装列表

    def __init__(self, parent, inst, rows, installed):
        super().__init__(parent)
        self.inst = inst
        self.rows = rows
        self.installed = installed
        self._worker = None
        self._running = False
        self._starting = False          # 防重入：正在弹「停机/确认」过程
        self.setWindowTitle("插件依赖自检")
        self.resize(860, 600)
        v = QVBoxLayout(self)

        s = plugin_deps.summarize(rows)
        head_txt = (f"扫描到 {s['plugins']} 个带 requirements.txt 的插件，"
                    f"其中 <b>{s['bad']}</b> 个缺依赖，共缺 <b>{s['missing']}</b> 个包。")
        if s.get("broken"):
            head_txt += (f"　另有 <b style='color:#e05a5a'>{s['broken']}</b> 个插件"
                         f"目录不完整（补依赖修不好，需要重装插件）。")
        head = QLabel(head_txt)
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        heavy = [n for r in rows for n in r["heavy"]]
        hint = plugin_deps.heavy_hint(heavy)
        if hint:
            lb = QLabel("⚠ " + hint)
            lb.setStyleSheet("color:#e8a33d;")
            v.addWidget(lb)

        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["补装", "插件", "缺失", "缺失的包"])
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.verticalHeader().setVisible(False)
        v.addWidget(card_table(self.tb), 1)
        self._fill()

        tip = QLabel(
            "· 只补「确实没装」的包；已装版本用约束文件钉死，不会把 torch / transformers 降级\n"
            "· torch、numpy 等底座包自动跳过（由 ComfyHub 按显卡统一管）\n"
            "· 标「需手工」的是 git 依赖，得在插件目录里手动装；没有 Windows 轮子的包会失败但其余继续\n"
            "· 标「目录不完整」的是插件没装好（缺 __init__.py），补依赖没用 —— 点该行「删除」后重装插件")
        tip.setStyleSheet("color:#7d8797; font-size:11px;")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.log.setPlaceholderText("补装日志…")
        self.log.setMaximumHeight(150)
        v.addWidget(self.log)

        btns = QHBoxLayout()
        self.b_all = QPushButton("全选")
        self.b_none = QPushButton("全不选")
        self.b_all.clicked.connect(lambda: self._check_all(True))
        self.b_none.clicked.connect(lambda: self._check_all(False))
        self.b_go = QPushButton("补装选中")
        self.b_go.setProperty("accent", True)
        self.b_go.clicked.connect(self._on_go)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        btns.addWidget(self.b_all)
        btns.addWidget(self.b_none)
        btns.addStretch(1)
        btns.addWidget(b_close)
        btns.addWidget(self.b_go)
        v.addLayout(btns)

    # ---- 表格 ----
    def _fill(self):
        self.tb.setRowCount(0)
        for r in self.rows:
            n = self.tb.rowCount()
            self.tb.insertRow(n)
            broken = r.get("incomplete") or ""
            if broken:
                # 目录装坏了：勾选框换成「删除」，补依赖对这种情况无效
                b = QPushButton("删除")
                b.setToolTip("删除这个不完整的插件目录：\n%s\n\n%s" % (r["path"], broken))
                b.clicked.connect(lambda _c=False, row=r: self._remove_dir(row))
                b.setStyleSheet("padding:1px 8px; font-size:11px;")
                self.tb.setCellWidget(n, 0, b)
            else:
                it = QTableWidgetItem("")
                if r["missing"]:
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    it.setCheckState(Qt.Checked)
                else:
                    it.setFlags(Qt.NoItemFlags)
                self.tb.setItem(n, 0, it)
            self.tb.setItem(n, 1, QTableWidgetItem(r["name"]))
            if broken:
                cell = QTableWidgetItem("目录不完整")
                cell.setForeground(QColor("#e05a5a"))
                cell.setToolTip(broken)
                self.tb.setItem(n, 2, cell)
                cell2 = QTableWidgetItem(broken)
                cell2.setForeground(QColor("#e05a5a"))
                cell2.setToolTip("插件没装好，补依赖修不好：%s\n\n需要删除后重新安装该插件。"
                                 % broken)
                self.tb.setItem(n, 3, cell2)
                continue
            self.tb.setItem(n, 2, QTableWidgetItem(
                f"{len(r['missing'])}/{r['total']}" if r["missing"]
                else f"齐 ({r['total']})"))
            names = [m["name"] for m in r["missing"]]
            if r["special"]:
                names.append("需手工 " + str(len(r["special"])) + " 项")
            cell = QTableWidgetItem("、".join(names) or "—")
            cell.setToolTip("\n".join(
                [m["raw"] for m in r["missing"]] + (r["special"] or []))[:1500])
            self.tb.setItem(n, 3, cell)

    def _remove_dir(self, row):
        """删除一个不完整的插件目录（用户显式点「删除」才走这里）"""
        name, path = row.get("name") or "", row.get("path") or ""
        if not path or not os.path.isdir(path):
            QMessageBox.information(self, "ComfyHub", "目录已经不在了，刷新一下自检即可")
            self.rows = [x for x in self.rows if x.get("path") != path]
            self._fill()
            return
        mw = self.parent().mainwin if self.parent() is not None else None
        try:
            if mw is not None and mw.procs.is_running(self.inst):
                QMessageBox.warning(self, "ComfyHub", "先停止 ComfyUI 再删插件目录")
                return
        except Exception:
            pass
        if QMessageBox.question(
                self, "删除插件目录",
                "确定删除这个不完整的插件目录？\n\n%s\n%s\n\n%s\n\n"
                "删掉后 ComfyUI 不再加载它；需要用时重新装一遍插件。"
                % (name, path, row.get("incomplete") or "")) != QMessageBox.Yes:
            return
        try:
            shutil.rmtree(path)
        except Exception as e:
            QMessageBox.warning(self, "删除失败",
                                "%s\n\n（多半是被占用：先停止 ComfyUI 再试）" % e)
            return
        self.log.appendPlainText("🗑 已删除不完整目录：" + path)
        self.rows = [x for x in self.rows if x.get("path") != path]
        self._fill()

    def _check_all(self, on: bool):
        for i in range(self.tb.rowCount()):
            it = self.tb.item(i, 0)
            if it is not None and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _selected(self) -> list:
        out = []
        for i in range(self.tb.rowCount()):
            it = self.tb.item(i, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append(self.rows[i])
        return out

    # ---- 补装 ----
    def _note(self, msg: str):
        """往日志写一行（日志是第一现场：到底动没动，第一行就要能回答）"""
        self.log.appendPlainText(msg)

    def _on_go(self):
        """同一个按钮两种身份：空闲时「补装选中」，运行时「取消并终止」"""
        if self._running:
            self._cancel_install()
        else:
            self._install()

    def _cancel_install(self):
        w = self._worker
        if w is None:
            return
        self.b_go.setEnabled(False)
        self.b_go.setText("正在终止…")
        self._note("⏹ 收到取消：正在终止 pip（进程树一起杀）…")
        try:
            w.cancel()
        except Exception as e:
            self._note("   取消失败：%s" % e)

    def _ensure_instance_stopped(self) -> bool:
        """实例在跑就先停下（2026-11-22 加一键处置）

        旧流程只弹「请先停止 ComfyUI」，用户关掉弹窗后界面回到原样、日志空白，
        看着就像"点了没反应"。现在直接给「停止并继续」，点完自动停机再补装。
        """
        mw = self.parent().mainwin if self.parent() is not None else None
        try:
            running = bool(mw is not None and mw.procs.is_running(self.inst))
        except Exception:
            return True
        if not running:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("补装依赖")
        box.setIcon(QMessageBox.Warning)
        box.setText("ComfyUI 正在运行，pip 会去改被占用的文件，装一半就会失败。")
        box.setInformativeText("可以先由 ComfyHub 停止它，再继续补装。")
        b_stop = box.addButton("停止 ComfyUI 并继续", QMessageBox.AcceptRole)
        box.addButton("先不装", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not b_stop:
            self._note("未开始：ComfyUI 正在运行，你选择了先停止它再说")
            return False
        self._note("… 正在停止 ComfyUI")
        try:
            mw.procs.get(self.inst).stop()
        except Exception as e:
            self._note("   停止失败：%s" % e)
        import time as _t
        from PySide6.QtWidgets import QApplication as _QA
        t0 = _t.time()
        while True:
            try:
                if not mw.procs.is_running(self.inst):
                    break
            except Exception:
                break
            if _t.time() - t0 > 30:
                break
            _QA.processEvents()
            _t.sleep(0.2)
        try:
            still = mw.procs.is_running(self.inst)
        except Exception:
            still = False
        if still:
            self._note("未开始：等了 30 秒 ComfyUI 还没停下来，请手动停止后再补装")
            return False
        self._note("   ✅ 已停止，继续补装")
        return True

    def _install(self):
        # 防重入：_ensure_instance_stopped 里跑 processEvents 等停机，这期间用户
        # 还能点到「补装选中」，不加这个闸门会起两个 pip 同时改一个环境。
        if getattr(self, "_starting", False):
            return
        rows = self._selected()
        if not rows:
            self._note("未开始：没有勾选任何插件（缺依赖的行默认已勾选）")
            QMessageBox.information(self, "ComfyHub", "先勾选要补装的插件")
            return
        if self._worker is not None or self._running:
            self._note("未开始：上一次补装还在跑，等它结束（或点「取消并终止」）")
            return
        self._starting = True
        try:
            self._install_inner(rows)
        finally:
            self._starting = False

    def _install_inner(self, rows):
        if not self._ensure_instance_stopped():
            return
        heavy = [n for r in rows for n in r["heavy"]]
        n_pkg = sum(len(r["missing"]) for r in rows)
        msg = f"将补装 {len(rows)} 个插件的 {n_pkg} 个缺失包。"
        h = plugin_deps.heavy_hint(heavy)
        if h:
            msg += "\n\n" + h
        msg += "\n\n补装期间请不要启动 ComfyUI。开始吗？"
        if QMessageBox.question(self, "补装依赖", msg) != QMessageBox.Yes:
            self._note("未开始：你取消了本次补装")
            return
        from app.bootstrap import PYPI_MIRRORS
        mirror = PYPI_MIRRORS.get("阿里云", "")
        # 留痕在先：这几行写下去，日志第一行永远能回答「到底动没动」
        self.log.clear()
        self._note("已开始补装 %d 个插件的 %d 个包（镜像 %s）"
                   % (len(rows), n_pkg, "阿里云" if mirror else "官方"))
        self._running = True
        self.b_go.setText("取消并终止")
        # 先置灰：worker 吐第一行日志（证明真的跑起来了）才解锁成可取消。
        # 否则手快双击「补装选中」会变成「刚启动就被自己取消」。
        self.b_go.setEnabled(False)
        self.b_all.setEnabled(False)
        self.b_none.setEnabled(False)
        w = PluginInstallWorker(self.inst.get("python_path") or "",
                                rows, self.installed, mirror=mirror)
        w.sig_log.connect(self._on_worker_log)
        w.sig_state.connect(self._on_worker_state)
        w.sig_done.connect(self._done)
        swap_worker(self, "_pdep_worker", w)
        self._worker = w
        w.start()

    def _on_worker_log(self, text):
        self.log.appendPlainText(str(text))
        if self._running and not self.b_go.isEnabled():
            self.b_go.setEnabled(True)

    def _on_worker_state(self, st):
        """worker 报「没输出了」→ 把按钮解锁成可取消（不杀进程，可能只是在下大文件）"""
        if st == "idle" and self._running:
            self.b_go.setEnabled(True)
            self.b_go.setText("取消并终止")

    def _done(self, bad, err):
        self._worker = None
        self._running = False
        self.b_go.setText("补装选中")
        self.b_go.setEnabled(True)
        self.b_all.setEnabled(True)
        self.b_none.setEnabled(True)
        if err:
            self.log.appendPlainText("⚠ " + err)
            self.log.appendPlainText("→ 可在插件目录里手动 pip install 缺的包，或换镜像重试")
        else:
            self.log.appendPlainText("✅ 全部补齐，可以启动 ComfyUI 试跑工作流了")
        self.log.appendPlainText("（已让依赖列表刷新；可再点一次「插件依赖自检」复核）")
        self.sig_installed.emit()

    def reject(self):
        """运行中关窗：先请求终止并等线程收尾（否则 QThread 被析构会崩）"""
        if self._running and self._worker is not None:
            if QMessageBox.question(
                    self, "补装进行中",
                    "补装还没结束。关闭窗口会终止 pip 进程，已装的包保持原样。\n\n确定关闭？"
            ) != QMessageBox.Yes:
                return
            try:
                self._worker.cancel()
                if not self._worker.wait(8000):
                    self._note("pip 正在退出，请等几秒再关（已请求终止）")
                    return
            except Exception:
                pass
            self._running = False
        super().reject()


class _RunTask(_QThread):
    sig_done = _Signal(object)
    sig_err = _Signal(str)   # 异常独立通道：不再伪装成返回值（曾导致"失败"被当成功）

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.sig_done.emit(self.fn())
        except Exception as e:
            self.sig_err.emit(str(e))


# ---------- 详情渲染（备份/快照共用） ----------
def _fmt_size(n):
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{max(n, 0) / 1024:.0f} KB"


def _render_meta(kind: str, name: str, meta: "dict | None") -> str:
    """把环境清单 dict 渲染成详情面板 HTML"""
    if not meta:
        return (f"<b>{kind} {name}</b><br>"
                "<i>旧条目：创建于详情功能上线前，无环境清单记录。<br>"
                "还原/回滚功能不受影响。</i>")
    from html import escape as esc
    parts = [f"<b>{esc(kind)} {esc(name)}</b>"]
    ev = meta.get("event") or "—"
    created = meta.get("created") or ""
    parts.append(f"事件：<b>{esc(ev)}</b>" + (f"　创建：{esc(created)}" if created else ""))
    k = meta.get("kernel") or "?"
    parts.append(f"内核版本：<b>{esc(str(k))}</b>")
    # 插件
    plugs = meta.get("plugins") or []
    if plugs:
        rows = "".join(
            f"<tr><td>{esc(str(p.get('name', '?')))}</td>"
            f"<td>{esc(str(p.get('version') or '—'))}</td>"
            f"<td>{'✔ 启用' if p.get('enabled') else '✘ 禁用'}</td></tr>"
            for p in plugs)
        parts.append(f"插件（{len(plugs)}）："
                     f"<table cellspacing='0' width='100%'>{rows}</table>")
    else:
        parts.append("插件：无")
    # 工作流
    wfs = meta.get("workflows") or []
    if wfs:
        parts.append(f"工作流（{len(wfs)}）：" +
                     "、".join(esc(w) for w in wfs))
    else:
        parts.append("工作流：无")
    # 模型 + 哈希
    models = meta.get("models") or []
    if models:
        total = sum(m.get("size", 0) for m in models)
        rows = "".join(
            f"<tr><td>{esc(str(m.get('subdir', '')))}/{esc(str(m.get('name', '')))}</td>"
            f"<td>{_fmt_size(m.get('size', 0))}</td>"
            f"<td style='font-size:11px'>{esc(str(m.get('sha256', ''))[:16])}…</td></tr>"
            for m in models)
        parts.append(f"模型（{len(models)}，共 {_fmt_size(total)}）："
                     f"<table cellspacing='0' width='100%'>{rows}</table>")
    else:
        parts.append("模型：无")
    return "<br>".join(parts)
