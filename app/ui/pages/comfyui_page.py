# -*- coding: utf-8 -*-
"""ComfyUI 编辑页：实例设置（基础+高级+全局参数合一页，三分区标题）+ 内核版本 + 启停 + 保存/删除"""
import os
import time

from PySide6.QtCore import Qt, QThread, Signal

_KVER_PAGE = 20   # 内核版本列表一次加载行数（2026-11-02 用户定：分页 + 「加载更多」）
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSpinBox, QCheckBox, QFileDialog, QMessageBox, QFrame,
    QScrollArea, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QGridLayout,
)

from app.config import VRAM_MODES, save_config
from app.log import log_exc
from app.ui.nav import NAV
from app.workers import BgWorker, RmWorker, swap_worker, wait_all
from app.process import build_command, port_in_use


class _Row(QFrame):
    """行式设置项：左标题+说明（2），右控件（3，宽度优先给控件）"""
    def __init__(self, title, desc, widget):
        super().__init__()
        self.setObjectName("RowCard")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 7, 14, 7)
        h.setSpacing(12)
        left = QVBoxLayout()
        t = QLabel(title);  t.setObjectName("RowTitle")
        d = QLabel(desc);   d.setObjectName("RowDesc")
        d.setWordWrap(True)
        left.addWidget(t)
        left.addWidget(d)
        h.addLayout(left, 2)
        if widget:
            widget.setMinimumWidth(380)
            if hasattr(widget, "setToolTip"):
                # 路径类控件：悬停看全量内容
                pass
            h.addWidget(widget, 3, Qt.AlignVCenter)




class _KernelInfoWorker(QThread):
    """内核信息（版本/远端/分支）后台读取。
    2026-10-31 卡顿修复：remote_url/current_branch 各起一个 git 子进程，
    Windows 上进程创建+杀软扫描 ≈ 100ms/个，同步跑会在切页时冻结 UI
    250~340ms（实测）——挪到后台线程，UI 只画缓存。"""
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, proxy=""):
        super().__init__()
        self.path = comfyui_path
        self.proxy = proxy

    def run(self):
        try:
            from app.tools import KernelManager
            km = KernelManager(self.path, proxy=self.proxy)
            info = {
                "ver": km.current_version() or "未知",
                "is_git": bool(km.is_git_repo),
                "remote": "", "branch": "",
            }
            if info["is_git"]:
                info["remote"] = km.remote_url()
                info["branch"] = km.current_branch()
            self.sig_done.emit(info)
        except Exception as e:
            self.sig_done.emit({"ver": f"读取版本失败：{e}",
                               "is_git": False, "remote": "", "branch": "",
                               "error": str(e)})


class _KernelWorker(QThread):
    """内核版本：拉标签 / 切换+装依赖（git 与 pip 都可能慢，必须后台）"""
    sig_log = Signal(str)
    sig_tags = Signal(list)
    sig_switch = Signal(bool, str)
    sig_versions = Signal(list)

    def __init__(self, km, action, tag="", python=""):
        super().__init__()
        self.km = km
        self.action = action
        self.tag = tag
        self.python = python

    def run(self):
        try:
            if self.action == "versions":
                self.sig_log.emit("[内核] 正在拉取远端更新（git fetch）…")
                versions = self.km.list_versions(200)   # 内部已前置 fetch；UI 分页每次画 20
                if getattr(self.km, "last_fetch_ok", True):
                    self.sig_log.emit(f"[内核] 远端已是最新，取到 {len(versions)} 个版本")
                else:
                    self.sig_log.emit(
                        f"[内核] ⚠️ 拉取远端失败（网络慢/被墙？可在实例设置配代理后重试），"
                        f"以下为本地缓存的 {len(versions)} 个版本")
                self.sig_versions.emit(versions)
                return
            if self.action == "update":
                self.sig_log.emit("[内核] 更新至最新稳定版…")
                ok, msg = self.km.update_stable()
                self.sig_log.emit(f"[内核] 更新: {'OK' if ok else '失败'} {msg[:200]}")
                if ok and self.python:
                    from app import bootstrap
                    self.sig_log.emit("[内核] 安装该版本要求的依赖…")
                    try:
                        bootstrap.install_comfyui_requirements(
                            self.python, self.km.path, log=self.sig_log.emit)
                        self.sig_log.emit("[内核] 依赖已同步")
                    except Exception as e:
                        self.sig_log.emit(f"[内核] 依赖安装异常（可稍后手动装）: {e}")
                self.sig_switch.emit(ok, msg)
                return
            # switch
            self.sig_log.emit(f"[内核] 切换到 {self.tag} …")
            ok, msg = self.km.switch_to(self.tag)
            self.sig_log.emit(f"[内核] checkout: {'OK' if ok else '失败'} {msg[:200]}")
            if not ok:
                self.sig_switch.emit(False, msg)
                return
            if self.python:
                from app import bootstrap
                self.sig_log.emit("[内核] 安装该版本要求的依赖…")
                try:
                    bootstrap.install_comfyui_requirements(
                        self.python, self.km.path, log=self.sig_log.emit)
                    self.sig_log.emit("[内核] 依赖已同步")
                except Exception as e:
                    self.sig_log.emit(f"[内核] 依赖安装异常（可稍后手动装）: {e}")
            self.sig_switch.emit(True, msg)
        except Exception as e:
            self.sig_switch.emit(False, str(e))


class ComfyuiPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        # 加载时界面取值快照：保存前对比用（2026-11-08 用户要求「空 = 默认值」，
        # 老配置缺字段时界面会显示默认值，不该被算成一次改动）
        self._loaded = None
        self._build()
        self.load_current()
        self.load_shared()          # 共享外挂库是全局设置，只加载一次

    # ---------- 构建 ----------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # ---- 快捷操作行 ----
        bar = QHBoxLayout()
        self.b_start = QPushButton("▶ 启动")
        self.b_start.setProperty("success", True)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("■ 停止")
        self.b_stop.clicked.connect(self.stop)
        self.b_web = QPushButton("🌐 打开网页")
        self.b_web.clicked.connect(self.open_browser)
        # 批量任务：用户手动开启，可设次数与重启间隔（防 OOM）
        self.b_batch = QPushButton("🔁 批量运行")
        self.b_batch.setToolTip("同一工作流跑多次，可设置每 N 次重启实例释放显存")
        self.b_batch.clicked.connect(self._open_batch)
        self.lb_state = QLabel("未运行")
        self.lb_state.setObjectName("StatusStop")
        bar.addWidget(self.b_start)
        bar.addWidget(self.b_stop)
        bar.addWidget(self.b_web)
        bar.addWidget(self.b_batch)
        bar.addStretch()
        bar.addWidget(self.lb_state)
        outer.addLayout(bar)


        # ---- 两 Tab：实例设置（基础+高级合一页）/ 内核版本 ----
        # 2026-10-25 用户要求：基础设置和高级设置不要分开页面，合并成一页
        self.tabs = QTabWidget()
        self.tabs.setContentsMargins(8, 8, 8, 0)
        outer.addWidget(self.tabs, 1)
        # 切到「内核版本」Tab 自动刷新一次（2026-10-29 用户需求，带 60s 节流）
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ---- Tab1 实例设置：基础区块 + 高级区块（同页两个分区标题）----
        t1 = QWidget(); t1v = QVBoxLayout(t1)
        t1v.setContentsMargins(18, 14, 18, 14); t1v.setSpacing(8)
        lb1 = QLabel("基础设置")
        lb1.setObjectName("SectionLabel")
        t1v.addWidget(lb1)

        self.ed_name = QLineEdit()
        t1v.addWidget(_Row("实例名称", "实例显示名称", self.ed_name))

        h_dir = QHBoxLayout()
        self.ed_path = QLineEdit();  self.ed_path.setReadOnly(True)
        b_dir = QPushButton("修改目录")
        b_dir.clicked.connect(self._pick_dir)
        h_dir.addWidget(self.ed_path, 1)
        h_dir.addWidget(b_dir)
        wrap1 = QWidget(); wrap1.setLayout(h_dir)
        t1v.addWidget(_Row("根目录", "包含 main.py 的 ComfyUI 目录", wrap1))

        # 模型目录卡片（2026-11-08 用户要求：卡片拉高，下面加「启用挂载模型目录」
        # 开关 + 目录选择，两个目录一眼分得清：上面 = 实例自己的 models 根，
        # 下面 = 别的盘的外置库，用 extra_model_paths.yaml 挂进来）
        h_models = QHBoxLayout()
        h_models.setSpacing(8)
        self.ed_models = QLineEdit(); self.ed_models.setReadOnly(True)
        self.b_models = QPushButton("修改目录")
        self.b_models.clicked.connect(self._pick_models)
        h_models.addWidget(self.ed_models, 1)
        h_models.addWidget(self.b_models)

        h_mount = QHBoxLayout()
        h_mount.setSpacing(8)
        self.ck_mount = QCheckBox("启用挂载模型目录")
        self.ck_mount.setToolTip(
            "开启后启动 ComfyUI 会带上本实例的挂载表（--extra-model-paths-config），\n"
            "下面选的目录里的 checkpoints / loras / vae 等会被自动映射挂进来。\n"
            "只影响当前实例，重启实例后生效。")
        self.ck_mount.toggled.connect(self._on_mount_toggled)
        self.ed_mount = QLineEdit(); self.ed_mount.setReadOnly(True)
        self.ed_mount.setPlaceholderText("未选择：开启开关后可选一个外部模型根目录")
        self.b_mount = QPushButton("选择目录")
        self.b_mount.clicked.connect(self._pick_mount)
        h_mount.addWidget(self.ck_mount)
        h_mount.addWidget(self.ed_mount, 1)
        h_mount.addWidget(self.b_mount)

        wrap2 = QWidget()
        v2 = QVBoxLayout(wrap2)
        v2.setContentsMargins(0, 2, 0, 2)
        v2.setSpacing(8)
        v2.addLayout(h_models)
        v2.addLayout(h_mount)
        wrap2.setMinimumHeight(74)
        t1v.addWidget(_Row(
            "模型目录",
            "ComfyUI 的模型根目录（留空跟随 <根目录>/models）；"
            "其他盘的模型库用下面的开关单独挂载，按标准类别子目录自动映射",
            wrap2))

        h_py = QHBoxLayout()
        self.ed_python = QLineEdit()
        b_py = QPushButton("修改目录")
        b_py.clicked.connect(self._pick_python)
        h_py.addWidget(self.ed_python, 1)
        h_py.addWidget(b_py)
        wrap3 = QWidget(); wrap3.setLayout(h_py)
        t1v.addWidget(_Row("Python 路径", "实例配套的解释器（建议用独立 venv）", wrap3))

        # ---- 同页第二分区：高级设置 ----
        t1v.addSpacing(10)
        lb2 = QLabel("高级设置")
        lb2.setObjectName("SectionLabel")
        t1v.addWidget(lb2)

        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        t1v.addWidget(_Row("启动端口", "用于访问 ComfyUI 的 HTTP 端口（默认 8188）", self.sp_port))

        # 生成引擎：统一后端+设备一个下拉（2026-10-29 对齐绘世：
        #   自动 / CUDA GPU i / DML GPU i / CPU；DML 依赖 torch-directml 需后台探测）
        self.cb_eng = QComboBox()
        self.cb_eng.setMinimumWidth(320)
        self.cb_eng.setToolTip(
            "CUDA：NVIDIA 显卡加速（推荐）\n"
            "DML：DirectML 后端（AMD/Intel 显卡可用；依赖 torch-directml）\n"
            "CPU：仅调试用，速度极慢")
        t1v.addWidget(_Row(
            "生成引擎",
            "选择参与计算的硬件引擎：CUDA（NVIDIA 加速）/ DML（DirectML，AMD/Intel 可用）/ CPU（仅调试）",
            self.cb_eng))
        # 切到 CPU → 置灰显存优化（语义打架防护）
        self.cb_eng.currentIndexChanged.connect(self._on_engine_changed)

        self.cb_vram = QComboBox()
        self.cb_vram.addItems(VRAM_MODES.keys())
        t1v.addWidget(_Row("CPU/显存优化", "按显存大小选择优化级别（低配选低显存）", self.cb_vram))

        self.cb_ca = QComboBox()
        # 对齐绘世五档（2026-10-29）：Sage 为新增；旧值"自动"仍兼容
        self.cb_ca.addItems([
            "自动（xFormers 优先）", "Sage Attention",
            "PyTorch SDP", "Quad（省显存）", "Split（更快）"])
        t1v.addWidget(_Row("Cross Attention 优化",
                           "注意力算法：优先 xFormers（需环境已装），未装回落 SDP；Sage 需装 sageattention；省显存选 Quad",
                           self.cb_ca))

        self.cb_preview = QComboBox()
        self.cb_preview.addItems(["无", "自动", "latent2rgb（快）", "TAESD（精细）"])
        t1v.addWidget(_Row("采样预览", "出图过程中的实时预览画质（自动=ComfyUI 自己选；TAESD 更清晰但略耗性能）", self.cb_preview))

        # 计算精度：三档互斥（自动 / 强制 FP16 / 强制 FP32）
        self.cb_prec = QComboBox()
        self.cb_prec.addItems(["自动", "强制 FP16", "强制 FP32"])
        t1v.addWidget(_Row("计算精度", "平衡性能与显存占用，缓解精度带来的黑图问题", self.cb_prec))

        self.ck_fast = QCheckBox("开启")
        t1v.addWidget(_Row("快速模式（激进提速）", "启用 PyTorch 加速技巧，以一定质量代价换速度（新显卡提速明显，老卡慎用）", self.ck_fast))

        self.ck_nosm = QCheckBox("开启")
        t1v.addWidget(_Row("禁用智能显存（共享显存）", "显存紧张/爆显存时开启，稍降速度换稳定（关闭可避免内存弥补导致的性能降级）", self.ck_nosm))

        self.ck_cpu_vae = QCheckBox("开启")
        t1v.addWidget(_Row("CPU 运行 VAE", "显著降低峰值显存消耗，但会降低整体速度", self.ck_cpu_vae))

        self.ck_deterministic = QCheckBox("开启")
        t1v.addWidget(_Row("稳定计算", "使用速度较慢的确定性算法，保证结果可复现（可能无法让图像在所有情况下一致）", self.ck_deterministic))

        self.ck_channels = QCheckBox("开启")
        t1v.addWidget(_Row("Channels-Last 优化", "改用 channels_last 内存格式，部分场景有推理提速（对性能影响未知）", self.ck_channels))

        self.cb_cuda_malloc = QComboBox()
        self.cb_cuda_malloc.addItems(["自动", "强制启用", "禁用"])
        t1v.addWidget(_Row("CUDA 内存分配方案", "更换底层 CUDA 内存分配器（内置异步分配器需 CUDA 11.4+）", self.cb_cuda_malloc))

        self.ck_multi_user = QCheckBox("开启")
        t1v.addWidget(_Row("多用户模式", "允许多个用户同时使用一个实例（每人独立存储空间）", self.ck_multi_user))

        self.ck_lan = QCheckBox("开启")
        t1v.addWidget(_Row("局域网访问", "允许同一局域网其他设备（手机/平板）打开 ComfyUI", self.ck_lan))

        self.ck_hf_offline = QCheckBox("开启")
        t1v.addWidget(_Row("Huggingface 离线模式", "关闭从 Huggingface 联网加载模型，启用后无法在线下载本地缺失的模型", self.ck_hf_offline))

        self.ck_browser = QCheckBox("开启")
        t1v.addWidget(_Row("自动打开", "启动成功后自动用浏览器打开 ComfyUI 界面", self.ck_browser))

        self.ed_extra = QLineEdit()
        self.ed_extra.setPlaceholderText("例如: --reserve-vram 2")
        t1v.addWidget(_Row("启动参数", "附加的命令行参数（空格分隔的高阶用法）", self.ed_extra))

        # 2026-10-28 用户要求：专家模式整块移除（全局启动参数不再暴露 UI）

        t1v.addStretch()
        s1 = QScrollArea(); s1.setWidgetResizable(True); s1.setFrameShape(QFrame.NoFrame)
        s1.setWidget(t1)
        self.tabs.addTab(s1, "⚙️ 实例设置")

        # ---- Tab2 内核版本 ----
        t2 = QWidget(); t2v = QVBoxLayout(t2)
        t2v.setContentsMargins(18, 14, 18, 14); t2v.setSpacing(8)
        # ---- 内核版本 ----
        lb_k = QLabel("内核版本")
        lb_k.setObjectName("SectionLabel")
        t2v.addWidget(lb_k)

        h_k = QHBoxLayout()
        self.lb_kver = QLabel("…")
        self.lb_kver.setStyleSheet("color:#e8eef7;")
        h_k.addWidget(self.lb_kver, 1)
        self.b_kupdate = QPushButton("⬆ 更新至最新稳定版")
        self.b_kupdate.setProperty("accent", True)
        self.b_kupdate.clicked.connect(self._update_stable)
        b_krefresh = QPushButton("刷新版本")
        b_krefresh.clicked.connect(self._refresh_versions)
        self.cb_kkind = QComboBox()
        self.cb_kkind.addItems(["稳定版", "开发版", "全部"])   # 默认停在稳定版，全部放最后
        self.cb_kkind.currentIndexChanged.connect(lambda _i: self._filter_versions())
        # 筛选计数反馈：切筛选时行数变化直接可见（稳定版排最前，切「全部」顶部行不变，
        # 没有计数用户会以为点了没反应——2026-10-27 报障）
        self.lb_kcount = QLabel("")
        self.lb_kcount.setStyleSheet("color:#9fb0c8;")
        h_k.addWidget(self.b_kupdate)
        h_k.addWidget(b_krefresh)
        h_k.addWidget(self.lb_kcount)
        h_k.addWidget(self.cb_kkind)
        wrap_k = QWidget(); wrap_k.setLayout(h_k)
        t2v.addWidget(_Row("内核版本管理", "一键更新到最新稳定版，或按版本回滚（切换后自动安装配套依赖）", wrap_k))

        # 远端数据源提示（2026-10-29 加）：列表来自哪个仓库一目了然
        self.lb_remote = QLabel("")
        self.lb_remote.setStyleSheet("color:#9fb0c8; font-size:12px;")
        self.lb_remote.setVisible(False)
        t2v.addWidget(self.lb_remote)

        # 版本表格（版本 ID | 更新时间 | 类型 | 操作）
        self.tb_kver = QTableWidget(0, 4)
        self.tb_kver.setHorizontalHeaderLabels(["版本 ID", "更新时间", "类型", "操作"])
        hh_k = self.tb_kver.horizontalHeader()
        hh_k.setSectionResizeMode(0, QHeaderView.Stretch)
        hh_k.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh_k.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh_k.setSectionResizeMode(3, QHeaderView.Fixed)
        self.tb_kver.setColumnWidth(3, 150)
        self.tb_kver.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb_kver.setSelectionBehavior(QTableWidget.SelectRows)
        # 不再设最大高度：列表向下撑满到窗口底部操作栏（2026-10-27 报障改）
        t2v.addWidget(self.tb_kver, 1)
        # 「加载更多」（2026-11-02 用户定）：一次只画 20 行，拉到底点按钮再追加；
        # 滚动条触底也会自动追加。无剩余时按钮隐藏。
        self.b_kmore = QPushButton("加载更多")
        self.b_kmore.clicked.connect(self._load_more)
        self.b_kmore.setVisible(False)
        t2v.addWidget(self.b_kmore)
        self.tb_kver.verticalScrollBar().valueChanged.connect(self._on_kver_scroll)
        self.tabs.addTab(t2, "🧬 内核版本")

        # ---- Tab3 共享外挂库（2026-11-08 用户要求：从「系统设置」搬到 ComfyUI 页）----
        # 一份挂载表所有实例通用（写进系统设置），和 Tab1 里「启用挂载模型目录」
        # （单实例）互相独立，启动时两个 yaml 会一起传给 ComfyUI，叠加生效
        t3 = QWidget(); t3v = QVBoxLayout(t3)
        t3v.setContentsMargins(18, 14, 18, 14); t3v.setSpacing(8)
        lb_s = QLabel("共享外挂模型库")
        lb_s.setObjectName("SectionLabel")
        t3v.addWidget(lb_s)

        self.ck_share = QCheckBox("启用：所有实例共用同一个外部模型库")
        self.ck_share.toggled.connect(self._on_share_toggled)
        self.ck_share.toggled.connect(self._refresh_share_state)
        t3v.addWidget(_Row("启用共享外挂库",
                           "开启后启动任意实例都会带上这张挂载表", self.ck_share))

        h_share = QHBoxLayout()
        h_share.setSpacing(8)
        self.ed_share_root = QLineEdit()
        self.ed_share_root.setPlaceholderText(
            r"如 D:\ComfyUI-Models（标准子目录 checkpoints / loras / vae / unet …）")
        self.ed_share_root.textChanged.connect(self._refresh_share_state)
        self.b_share = QPushButton("选择目录")
        self.b_share.clicked.connect(self._pick_share_root)
        h_share.addWidget(self.ed_share_root, 1)
        h_share.addWidget(self.b_share)
        wrap_share = QWidget(); wrap_share.setLayout(h_share)
        t3v.addWidget(_Row("挂载根目录",
                           "目录下的标准子目录会自动映射进来，不用手写映射表",
                           wrap_share))

        self.lb_share_state = QLabel()
        self.lb_share_state.setWordWrap(True)
        self.lb_share_state.setStyleSheet("color:#7fa8d9; background:transparent;")
        t3v.addWidget(self.lb_share_state)
        tip_share = QLabel(
            "开启后，启动任意实例都会把该目录传给 ComfyUI（--extra-model-paths-config），"
            "不用给每个实例单独写 yaml。目录下的标准子目录自动映射；实例自己的 "
            "extra_model_paths.yaml 仍照旧生效，两者叠加。")
        tip_share.setWordWrap(True)
        tip_share.setStyleSheet("color:#888; background:transparent;")
        t3v.addWidget(tip_share)
        t3v.addStretch()
        h_save = QHBoxLayout()
        h_save.addStretch()
        self.b_share_save = QPushButton("保存共享库设置")
        self.b_share_save.setProperty("accent", True)
        self.b_share_save.clicked.connect(self._save_shared)
        h_save.addWidget(self.b_share_save)
        t3v.addLayout(h_save)
        self.tabs.addTab(t3, "📚 共享外挂库")

        # ---- 底部：保存 / 删除（全局）----
        bottom = QHBoxLayout()
        bottom.setContentsMargins(18, 0, 18, 12)
        bottom.addStretch()
        b_save = QPushButton("保存设置")
        b_save.setObjectName("SaveBtn")
        b_save.clicked.connect(self._save)
        bottom.addWidget(b_save)
        self.b_del = QPushButton("🗑 删除此实例（可选仅移出管理 / 彻底删除文件）")
        self.b_del.setObjectName("DeleteBtn")
        self.b_del.clicked.connect(self._delete)
        bottom.addWidget(self.b_del)
        outer.addLayout(bottom)
    # ---------- 数据 ----------
    def load_current(self):
        inst = self.mainwin.current_inst()
        self.b_del.setEnabled(inst is not None)
        if not inst:
            for w in (self.ed_name, self.ed_path, self.ed_models, self.ed_python,
                      self.ed_mount):
                w.clear()
            self.ck_mount.setChecked(False)
            self._on_mount_toggled(False)
            self._loaded = None
            return
        self.ed_name.setText(inst.get("name", ""))
        self.ed_path.setText(inst.get("comfyui_path", ""))
        # 模型目录：配置为空 = 跟随默认（<comfyui_path>/models）。输入框留空、
        # 灰字显示默认路径；别把默认路径写死进配置，否则以后挪 ComfyUI 目录时
        # 这一栏还钉在旧位置（2026-11-07）。已经写死成默认路径的老配置也顺手净化回空
        _dflt_models = os.path.join(inst["comfyui_path"], "models")
        _md = (inst.get("models_dir") or "").strip()
        if _md and os.path.normpath(_md) == os.path.normpath(_dflt_models):
            _md = ""
        self.ed_models.setText(_md)
        self.ed_models.setPlaceholderText(_dflt_models)
        # 挂载模型目录（2026-11-08）：按实例存，切实例各自独立
        self.ck_mount.setChecked(bool(inst.get("mount_enabled")))
        _mdir = (inst.get("mount_dir") or "").strip()
        self.ed_mount.setText(_mdir)
        self.ed_mount.setToolTip(_mdir)
        self._on_mount_toggled(self.ck_mount.isChecked())
        self.ed_python.setText(inst.get("python_path", ""))
        # 路径框：光标移到末尾（显示尾部目录名），tooltip 存完整路径
        for ed in (self.ed_path, self.ed_models, self.ed_python):
            ed.setToolTip(ed.text() or ed.placeholderText())
            ed.setCursorPosition(len(ed.text()))
        self.sp_port.setValue(int(inst.get("port") or 8188))
        self.cb_vram.setCurrentText(inst.get("vram_mode", "自动"))
        # cross_attn 旧值迁移：「自动」→「自动（xFormers 优先）」（2026-10-29 五档化）
        _ca = inst.get("cross_attn", "自动")
        if _ca == "自动":
            _ca = "自动（xFormers 优先）"
        self.cb_ca.setCurrentText(_ca)
        self.cb_preview.setCurrentText(inst.get("preview_method", "无"))
        self.cb_prec.setCurrentText(inst.get("precision", "自动"))
        self.ck_fast.setChecked(bool(inst.get("fast_mode")))
        self.ck_nosm.setChecked(bool(inst.get("no_smart_memory")))
        self.ck_cpu_vae.setChecked(bool(inst.get("cpu_vae")))
        self.ck_deterministic.setChecked(bool(inst.get("deterministic")))
        self.ck_channels.setChecked(bool(inst.get("channels_last")))
        self.cb_cuda_malloc.setCurrentText(inst.get("cuda_malloc", "自动"))
        self.ck_multi_user.setChecked(bool(inst.get("multi_user")))
        self.ck_lan.setChecked(bool(inst.get("lan_access")))
        self.ck_hf_offline.setChecked(bool(inst.get("hf_offline")))
        self.ck_browser.setChecked(bool(inst.get("auto_open_browser", True)))
        self.ed_extra.setText(inst.get("extra_args", ""))
        self._restore_engine_selection(inst)
        self._refresh_kernel_info()
        self._update_state()
        # 记下「界面上看到的值」作为保存对比基准（空字段已在此前填成默认值）
        self._loaded = self._snapshot()

    # ---------- 内核版本 ----------
    def _km(self):
        from app.tools import KernelManager
        inst = self.mainwin.current_inst()
        # git fetch/pull 走实例代理（2026-10-29：GitHub 直连慢时靠它）
        return KernelManager(inst["comfyui_path"],
                             proxy=inst.get("proxy") or "") if inst else None

    def _refresh_kernel_info(self):
        """内核信息展示：同步 git 子进程改为「缓存秒画 + 后台补读」
        （2026-10-31 卡顿修复，见 _KernelInfoWorker 注释）"""
        inst = self.mainwin.current_inst()
        if not inst or not hasattr(self, "lb_kver"):
            return
        iid = inst.get("id")
        cache = getattr(self, "_kinfo_cache", None)
        if cache is None:
            cache = self._kinfo_cache = {}
        hit = cache.get(iid)
        if hit and time.time() - hit["ts"] < 300:
            self._paint_kinfo(hit["info"])
            return
        if not hit:
            self.lb_kver.setText("正在读取内核信息…")
            self.lb_kver.setStyleSheet("color:#9aa4b2;")
        w = _KernelInfoWorker(inst["comfyui_path"],
                              proxy=inst.get("proxy") or "")
        w.sig_done.connect(lambda info, iid=iid: self._on_kinfo(iid, info))
        swap_worker(self, "_kinfo_worker", w)
        w.start()

    def _on_kinfo(self, iid, info):
        self._kinfo_cache[iid] = {"info": info, "ts": time.time()}
        inst = self.mainwin.current_inst()
        if inst and inst.get("id") == iid:
            self._paint_kinfo(info)

    def _paint_kinfo(self, info):
        try:
            ver = info.get("ver") or "未知"
            if info.get("is_git"):
                self.lb_kver.setText(f"当前：{ver}　·　git 仓库 ✓")
                self.lb_kver.setStyleSheet("color:#4cd47c;")
                # 远端 + 分支（2026-10-29 加：让用户看清列表数据源，别再和
                # 其他启动器「对不上」——大家拉的可能根本不是一个仓库）
                remote = info.get("remote") or ""
                branch = info.get("branch") or ""
                if remote:
                    import re as _re
                    short = _re.sub(r"\.git$", "", remote.split("://")[-1])
                    self.lb_remote.setText(
                        f"远端：{short}　·　分支：{branch or '?'}")
                    self.lb_remote.setToolTip(f"远端地址：{remote}\n版本列表与"
                                              "「更新至最新稳定版」都来自这个仓库")
                    self.lb_remote.setVisible(True)
                else:
                    self.lb_remote.setVisible(False)
            else:
                self.lb_kver.setText(f"当前：{ver}　·　非 git 仓库（无法切换版本）")
                self.lb_kver.setStyleSheet("color:#e8a33d;")
                self.lb_remote.setVisible(False)
        except Exception as e:
            self.lb_kver.setText(f"读取版本失败：{e}")
            self.lb_kver.setStyleSheet("color:#e8635a;")

    def _refresh_versions(self, silent=False):
        """刷新版本列表。silent=True 用于切 Tab 自动刷新（非 git 仓库静默跳过不弹窗）"""
        inst = self.mainwin.current_inst()
        if not inst:
            return
        km = self._km()
        if not km or not km.is_git_repo:
            if not silent:
                QMessageBox.information(self, "内核版本",
                                        "该实例不是 git 仓库（可能是 zip 安装），无法按版本切换。")
            return
        if getattr(self, "_kw", None) and self._kw.isRunning():
            return
        self.lb_kver.setText("正在拉取远端更新并加载版本列表…")
        self._kw = _KernelWorker(km, "versions")
        self._kw.sig_log.connect(self.mainwin.console_page.append_line)
        self._kw.sig_versions.connect(self._on_versions)
        self._kw.start()

    def _on_tab_changed(self, idx):
        """切到「内核版本」Tab 自动刷新一次（2026-10-29 用户需求）。

        节流：60 秒内已刷过且有数据 → 不重复 fetch（fetch 要走网络）。
        """
        if idx != 1:   # 0=实例设置, 1=内核版本
            return
        last = getattr(self, "_kver_last_refresh", 0.0)
        if time.time() - last < 60 and getattr(self, "_all_versions", None):
            return
        self._refresh_versions(silent=True)

    def _on_versions(self, versions):
        self._all_versions = versions
        self._kver_last_refresh = time.time()   # 自动刷新节流基准
        # 记录取数来源（2026-11-02）：列表读哪个 ref、fetch 是否成功——
        # 显示在计数行，让用户一眼看出列表和「远端」是否真的对得上
        km = getattr(getattr(self, "_kw", None), "km", None)
        self._kver_ref = getattr(km, "last_dev_ref", "") or ""
        self._kver_fetch_ok = getattr(km, "last_fetch_ok", True)
        self._filter_versions()
        self._refresh_kernel_info()
        self.lb_kver.setText(self.lb_kver.text() + f"　·　{len(versions)} 个版本")

    def _filter_versions(self, reset=True):
        """按类型筛选渲染版本表。reset=True 重新分页（回 20 行），
        reset=False 由「加载更多」追加（2026-11-02 分页改造）。
        渲染期置 _kver_rendering 防重入：setRowCount 会触发滚动条信号，
        钻进 _load_more 做嵌套渲染会让行数/文案互相覆盖（同日实测踩坑）。"""
        if getattr(self, "_kver_rendering", False):
            return
        self._kver_rendering = True
        try:
            self._filter_versions_impl(reset)
        finally:
            self._kver_rendering = False

    def _filter_versions_impl(self, reset):
        kind = self.cb_kkind.currentText()
        allv = getattr(self, "_all_versions", [])
        rows = [v for v in allv
                if kind == "全部" or v["kind"] == kind]
        self._kver_filtered = rows
        if reset:
            self._kver_shown = _KVER_PAGE
        shown = getattr(self, "_kver_shown", _KVER_PAGE)
        page = rows[:shown]
        self.tb_kver.setRowCount(len(page))
        for i, v in enumerate(page):
            self.tb_kver.setItem(i, 0, QTableWidgetItem(v["version"]))
            self.tb_kver.setItem(i, 1, QTableWidgetItem(v["date"]))
            k = QTableWidgetItem(v["kind"])
            k.setForeground(Qt.green if v["kind"] == "稳定版" else Qt.gray)
            self.tb_kver.setItem(i, 2, k)
            b = QPushButton("切换并装依赖")
            # 2026-11-02 修复：开发版行 version 是提交标题（不是 ref！），
            # git checkout 会直接失败——检出用 hash，弹窗文案仍显示标题
            ref = v["version"] if v["kind"] == "稳定版" else (v.get("hash") or v["version"])
            b.clicked.connect(
                lambda _c=False, v=v, ref=ref: self._switch_kernel(v["version"], ref))
            self.tb_kver.setCellWidget(i, 3, b)
        # 「加载更多」按钮：有剩余才显示，文案带剩余条数
        remain = len(rows) - len(page)
        self.b_kmore.setVisible(remain > 0)
        self.b_kmore.setText(f"加载更多（还有 {remain} 条）")
        # 计数反馈：让用户看见筛选生效（显示 X / 共 Y）+ 列表来源（对得上远端吗）
        if allv:
            ref = getattr(self, "_kver_ref", "") or ""
            ok = getattr(self, "_kver_fetch_ok", True)
            if ref and ref != "HEAD":
                note = (f"　·　列表：{ref}" if ok else
                        f"　·　⚠️ 远端拉取失败，以下为 {ref} 本地缓存")
            else:
                note = ("　·　列表：本地 HEAD" if ok else
                        "　·　⚠️ 远端拉取失败，以下为本地历史")
            self.lb_kcount.setText(f"显示 {len(page)} / 共 {len(allv)}{note}")
        else:
            self.lb_kcount.setText("")
        if reset:
            self.tb_kver.verticalScrollBar().setValue(0)

    def _load_more(self):
        """再加载一页（20 行）。渲染中被信号闯入时直接让路（防重入）"""
        if getattr(self, "_kver_rendering", False):
            return
        self._kver_shown = getattr(self, "_kver_shown", _KVER_PAGE) + _KVER_PAGE
        self._filter_versions(reset=False)

    def _on_kver_scroll(self, v):
        """滚动条触底自动追加一页（2026-11-02 用户定「滚到底部就加载」）。
        maximum<=0（内容没溢出）不触发，防止初次渲染时连环自动加载。
        守卫用 isHidden()（自身显隐标志）而非 isVisible()——后者受父链
        可见性影响，页面不在导航栈顶时恒 False 会把自动加载整个吞掉。"""
        if getattr(self, "_kver_rendering", False):
            return
        sb = self.tb_kver.verticalScrollBar()
        if (sb.maximum() > 0 and v >= sb.maximum()
                and not self.b_kmore.isHidden()):
            self._load_more()

    def _update_stable(self):
        inst = self.mainwin.current_inst()
        if not inst:
            return
        km = self._km()
        if not km or not km.is_git_repo:
            QMessageBox.information(self, "内核版本",
                                    "该实例不是 git 仓库（可能是 zip 安装），无法在线更新。")
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "内核版本", "请先停止该实例再更新内核")
            return
        if getattr(self, "_kw", None) and self._kw.isRunning():
            return
        self._kw = _KernelWorker(km, "update", python=inst.get("python_path") or "")
        self._kw.sig_log.connect(self.mainwin.console_page.append_line)
        self._kw.sig_switch.connect(self._on_switched)
        self._kw.start()
        self.lb_kver.setText("正在更新至最新稳定版…（含依赖安装，可能需要几分钟）")

    def _switch_kernel(self, label, ref=None):
        """切换内核。label=弹窗显示名（tag 名或提交标题），ref=git 检出用 ref
        （开发版传短 hash——提交标题不是合法 ref，2026-11-02 修复）"""
        ref = ref or label
        inst = self.mainwin.current_inst()
        if not inst or not label:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "内核版本", "请先停止该实例再切换内核版本")
            return
        hint = f"（提交 {ref}）" if ref != label else ""
        ans = QMessageBox.question(
            self, "切换内核",
            f"将把内核切换到 {label}{hint}，并自动安装该版本要求的依赖。\n"
            "当前未提交的改动会被 git stash 暂存。继续？")
        if ans != QMessageBox.Yes:
            return
        if getattr(self, "_kw", None) and self._kw.isRunning():
            return
        self._kw = _KernelWorker(self._km(), "switch", ref, inst.get("python_path") or "")
        self._kw.sig_log.connect(self.mainwin.console_page.append_line)
        self._kw.sig_switch.connect(self._on_switched)
        self._kw.start()
        self.lb_kver.setText(f"正在切换到 {label} …（含依赖安装，可能需要几分钟）")

    def _on_switched(self, ok, msg):
        self._refresh_kernel_info()
        if ok:
            QMessageBox.information(self, "切换完成", "内核版本已切换，依赖已同步。\n请重新启动该实例。")
        else:
            QMessageBox.warning(self, "切换失败", msg[:400])

    def on_show(self):
        self.load_current()

    def on_instance_changed(self):
        self.load_current()

    # ---------- 生成引擎（统一后端下拉：自动 / CUDA / DML / CPU） ----------
    def _ensure_engine_list(self):
        """懒填充生成引擎下拉（只填一次）：
        - CUDA 卡清单来自 nvidia-smi（毫秒级，同步安全）
        - DML 设备数要 import torch_directml（秒级），必须后台探测，
          探测完成后把「DML」占位项替换为具体设备；未安装则置灰提示
        itemData: (backend, index)；backend ∈ auto/cuda/dml/cpu
        """
        if getattr(self, "_eng_ready", False):
            return
        self._eng_ready = True
        from app.process import detect_gpus
        self.cb_eng.clear()
        self.cb_eng.addItem("自动（推荐）", ("auto", None))
        self._gpus = detect_gpus()
        for g in self._gpus:
            vram_gb = g["vram_mib"] / 1024
            self.cb_eng.addItem(
                f"CUDA GPU {g['id']}: {g['name']} ({vram_gb:.0f} GB)",
                ("cuda", g["id"]))
        # DML 占位项：后台探测 torch_directml 后替换为 DML GPU 0..N
        self.cb_eng.addItem("DML 设备检测中…", None)
        self._dml_row = self.cb_eng.count() - 1
        self.cb_eng.addItem("CPU", ("cpu", None))
        self._start_dml_probe()

    def _start_dml_probe(self):
        """用实例配套解释器探测 torch_directml.device_count()（秒级，后台）"""
        inst = self.mainwin.current_inst()
        py = (inst or {}).get("python_path") or ""
        if not py or not os.path.isfile(py):
            self._apply_dml_result(None)
            return

        def _probe():
            import subprocess
            try:
                out = subprocess.check_output(
                    [py, "-c",
                     "import torch_directml;"
                     "print(torch_directml.device_count())"],
                    stderr=subprocess.STDOUT, timeout=20,
                    creationflags=0x08000000 if os.name == "nt" else 0)
                n = int(out.decode("utf-8", "replace").strip().splitlines()[-1])
                return n if n > 0 else None
            except Exception:
                return None   # 未装 / 超时 / 非整数输出 → 视为不可用

        self._dml_worker = BgWorker(_probe)
        self._dml_worker.sig_done.connect(self._apply_dml_result)
        self._dml_worker.start()

    def _apply_dml_result(self, n):
        """DML 探测回调：替换占位项。n=None 表示 torch_directml 不可用"""
        row = getattr(self, "_dml_row", -1)
        if row < 0 or row >= self.cb_eng.count():
            return
        keep = self.cb_eng.currentData()   # 记住当前选中（别把用户选择清掉）
        self.cb_eng.removeItem(row)
        if n:
            for i in range(n):
                self.cb_eng.insertItem(row, f"DML GPU {i}", ("dml", i))
                row += 1
        else:
            self.cb_eng.insertItem(
                row, "DML（需安装 torch-directml）", ("dml", None))
            item = self.cb_eng.model().item(row)
            if item:
                item.setEnabled(False)   # 置灰：不可选但可见（用户知道有这功能）
                item.setToolTip(
                    "该实例的 Python 环境未安装 torch-directml。\n"
                    "AMD/Intel 显卡用户可安装后使用：pip install torch-directml")
        # 恢复选中：原选中项还在就选回它；没了（如原选中是占位项）回自动
        new_idx = 0
        if keep is not None:
            for i in range(self.cb_eng.count()):
                if self.cb_eng.itemData(i) == keep:
                    new_idx = i
                    break
        self.cb_eng.setCurrentIndex(new_idx)
        # 探测是异步的：若用户配置选了 DML 且现在可用，按配置恢复
        inst = self.mainwin.current_inst()
        if inst and inst.get("engine_backend") == "dml" and n:
            self._restore_engine_selection(inst)

    def _restore_engine_selection(self, inst):
        """按实例配置恢复引擎下拉选中（含旧字段迁移）"""
        self._ensure_engine_list()
        backend = inst.get("engine_backend")
        idx = inst.get("engine_index")
        if backend is None:
            # 旧配置迁移：device=cpu → cpu；cuda_device>=0 → cuda i；否则 auto
            if inst.get("device") == "cpu":
                backend, idx = "cpu", None
            else:
                dev = inst.get("cuda_device")
                if dev is not None and str(dev).strip() != "" and int(dev) >= 0:
                    backend, idx = "cuda", int(dev)
                else:
                    backend, idx = "auto", None
        for i in range(self.cb_eng.count()):
            d = self.cb_eng.itemData(i)
            if d and d[0] == backend and \
                    (backend != "cuda" or (d[1] or 0) == (idx or 0)):
                self.cb_eng.setCurrentIndex(i)
                break
        # CPU 时显存优化无意义，置灰防语义打架（同旧 _set_device 逻辑）
        cur = self.cb_eng.currentData()
        self.cb_vram.setEnabled(not (cur and cur[0] == "cpu"))

    def _on_engine_changed(self, *_a):
        cur = self.cb_eng.currentData()
        self.cb_vram.setEnabled(not (cur and cur[0] == "cpu"))

    def _update_state(self):
        inst = self.mainwin.current_inst()
        running = bool(inst and self.mainwin.procs.is_running(inst))
        self.b_start.setEnabled(bool(inst) and not running)
        self.b_stop.setEnabled(running)
        # 未启动不能打开网页（用户定）；运行中按实际端口打开
        self.b_web.setEnabled(running)
        self.b_web.setToolTip("打开当前实例网页（自动识别实际端口）"
                              if running else "实例启动后可用")
        self.lb_state.setText("● 运行中" if running else "未运行")
        self.lb_state.setObjectName("StatusRun" if running else "StatusStop")
        # QSS objectName 变更后需重刷样式
        self.lb_state.style().unpolish(self.lb_state)
        self.lb_state.style().polish(self.lb_state)

    # ---------- 动作 ----------
    def _current_inst(self):
        return self.mainwin.current_inst()

    # 保存前改动确认用的字段名（2026-11-06）
    _SAVE_LABELS = {
        "name": "实例名称", "models_dir": "模型目录", "python_path": "Python 路径",
        "mount_enabled": "启用挂载模型目录", "mount_dir": "挂载模型目录",
        "port": "启动端口", "vram_mode": "CPU/显存优化", "cross_attn": "Cross Attention",
        "preview_method": "预览方式", "precision": "精度", "fast_mode": "Fast 模式",
        "no_smart_memory": "禁用智能显存", "cpu_vae": "CPU VAE",
        "deterministic": "确定性计算", "channels_last": "channels_last",
        "cuda_malloc": "CUDA Malloc", "multi_user": "多用户模式",
        "lan_access": "局域网访问", "hf_offline": "HF 离线模式",
        "auto_open_browser": "自动打开浏览器", "extra_args": "额外启动参数",
        "engine": "生成引擎",
    }
    # 这些字段留空 = 用默认值，对比框里别显示成「（空）」让人以为没配
    _SAVE_EMPTY_TEXT = {
        "models_dir": "默认（<根目录>/models）",
        "mount_dir": "未选择",
        "extra_args": "无",
    }

    # 参与保存/对比的字段
    _TRACK = ("name", "models_dir", "python_path", "port", "vram_mode",
              "cross_attn", "preview_method", "precision", "fast_mode",
              "no_smart_memory", "cpu_vae", "deterministic", "channels_last",
              "cuda_malloc", "multi_user", "lan_access", "hf_offline",
              "auto_open_browser", "extra_args",
              "mount_enabled", "mount_dir")

    def _snapshot(self) -> dict:
        """界面当前取值（键与 _TRACK 一致，外加 engine）

        保存对比一律用它做基准：老配置里缺的字段（None / ""）在界面上已经被
        load_current 填成默认值，快照里就是那个默认值，于是「没动过 = 没改动」，
        不会再刷出一串「（空）→ 自动」的假改动（2026-11-08 用户报障）。
        """
        return {
            "name": self.ed_name.text().strip(),
            "models_dir": self.ed_models.text().strip(),
            "python_path": self.ed_python.text().strip(),
            "port": self.sp_port.value(),
            "vram_mode": self.cb_vram.currentText(),
            "cross_attn": self.cb_ca.currentText(),
            "preview_method": self.cb_preview.currentText(),
            "precision": self.cb_prec.currentText(),
            "fast_mode": self.ck_fast.isChecked(),
            "no_smart_memory": self.ck_nosm.isChecked(),
            "cpu_vae": self.ck_cpu_vae.isChecked(),
            "deterministic": self.ck_deterministic.isChecked(),
            "channels_last": self.ck_channels.isChecked(),
            "cuda_malloc": self.cb_cuda_malloc.currentText(),
            "multi_user": self.ck_multi_user.isChecked(),
            "lan_access": self.ck_lan.isChecked(),
            "hf_offline": self.ck_hf_offline.isChecked(),
            "auto_open_browser": self.ck_browser.isChecked(),
            "extra_args": self.ed_extra.text().strip(),
            "mount_enabled": self.ck_mount.isChecked(),
            "mount_dir": self.ed_mount.text().strip(),
            "engine": (self.cb_eng.currentData() or ("auto", None)),
        }

    def _save(self):
        from app.ui.changes_dialog import diff_changes, confirm_changes
        inst = self._current_inst()
        if not inst:
            return
        # 基准 = 加载时界面取值（2026-11-08）；没加载过才回落到配置原值
        if self._loaded is not None:
            old = dict(self._loaded)
        else:
            old = {k: inst.get(k) for k in self._TRACK}
            old["engine"] = (inst.get("engine_backend"), inst.get("engine_index"))
        new = self._snapshot()
        if not new["name"]:
            new["name"] = inst.get("name") or ""
        changes = diff_changes(old, new, self._SAVE_LABELS,
                               defaults=self._SAVE_EMPTY_TEXT)
        # 挂载开关开了却没选目录 / 目录已失效 → 直接拦下，别写一份空挂载
        if new["mount_enabled"]:
            _mr = new["mount_dir"]
            if not _mr:
                QMessageBox.warning(self, "ComfyHub",
                                    "已勾选「启用挂载模型目录」，但还没选目录。\n"
                                    "请点右侧「选择目录」，或取消勾选后再保存。")
                return
            if not os.path.isdir(_mr):
                QMessageBox.warning(self, "ComfyHub",
                                    f"挂载目录不存在：\n{_mr}\n\n请重新选择。")
                return
        if not changes:
            QMessageBox.information(self, "ComfyHub", "没有改动，无需保存")
            return
        # 用户要求（2026-11-06）：保存前列出改了什么，防手滑改错配置后无感落盘
        if not confirm_changes(self, f"实例「{inst.get('name')}」", changes, "保存设置"):
            return
        for k in self._TRACK:
            if k in new:
                inst[k] = new[k]
        _eng = new["engine"]
        inst["engine_backend"] = _eng[0]
        inst["engine_index"] = _eng[1]
        # 旧字段清理：device/cuda_device 已被引擎下拉取代，防止残留配置打架
        inst.pop("device", None)
        inst.pop("cuda_device", None)
        # 2026-10-28 专家模式移除：顺手清掉旧配置残留，防止无 UI 的参数静默生效
        st = self.cfg.setdefault("settings", {})
        st.pop("expert_mode", None)
        st.pop("launch", None)
        save_config(self.cfg)
        self.mainwin._sync_instances()
        self._loaded = dict(new)      # 基准跟着更新，连点保存不会再刷一堆假改动
        QMessageBox.information(self, "ComfyHub", "✅ 设置已保存")

    def _delete(self):
        inst = self._current_inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub",
                                    "当前没有可删除的实例。\n"
                                    "点右上角「＋ 添加 ComfyUI」先添加一个。")
            return
        path = inst.get("comfyui_path", "")
        box = QMessageBox(self)
        box.setWindowTitle("删除实例")
        box.setText(f"实例「{inst.get('name')}」\n根目录：{path}\n\n选择删除方式：")
        b_keep = box.addButton("仅移出管理", QMessageBox.AcceptRole)
        b_del = box.addButton("彻底删除（含磁盘文件）", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_keep:
            self.mainwin.procs.get(inst).stop()
            self.cfg["instances"].remove(inst)
            save_config(self.cfg)
            self.mainwin._sync_instances()
            self.load_current()
        elif clicked is b_del:
            self._delete_files(inst)

    def _swap_worker(self, attr, worker, timeout=60000):
        """替换 worker：旧线程断开信号后放入墓碑（持引用防 GC，后台自然结束）。
        绝不 wait 阻塞 UI——上一版用 wait 导致界面卡死在检测中"""
        old_w = getattr(self, attr, None)
        if old_w is not None and old_w.isRunning():
            for sig_name in ("sig_done", "sig_log", "sig_progress", "sig_prog"):
                sig = getattr(old_w, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except Exception:
                        pass
            self._graveyard = [w for w in getattr(self, "_graveyard", [])
                               if w.isRunning()]
            self._graveyard.append(old_w)
        setattr(self, attr, worker)


    def closeEvent(self, event):
        wait_all(self, ("_del_worker", "_rm_worker"), 30000)
        event.accept()

    def _delete_files(self, inst):
        """彻底删除（全异步，不卡 UI）：
        停进程 → 后台算体积 → 确认框 → 后台删除 → 移出管理"""
        import shutil
        path = inst.get("comfyui_path", "")
        if not os.path.isdir(path):
            QMessageBox.warning(self, "ComfyHub", f"目录不存在：\n{path}")
            return
        self.mainwin.procs.get(inst).stop()
        self.b_del.setEnabled(False)

        def _size():
            total = 0
            for root, _d, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total

        self._del_inst = inst
        self._del_path = path
        self.b_del.setText("⏳ 正在计算目录体积…")
        swap_worker(self, "_del_worker", BgWorker(_size))
        self._del_worker.sig_done.connect(self._on_size_done)
        self._del_worker.start()

    def _on_size_done(self, size):
        inst, path = self._del_inst, self._del_path
        self.b_del.setEnabled(True)
        self.b_del.setText("🗑 删除此实例（可选仅移出管理 / 彻底删除文件）")
        size_gb = size / 1024 ** 3
        r = QMessageBox.warning(
            self, "⚠️ 彻底删除确认",
            f"将永久删除以下内容，不可恢复：\n\n"
            f"目录：{path}\n"
            f"体积：约 {size_gb:.1f} GB\n\n"
            f"注意：\n"
            f"· 便携 Python / MinGit 在共享 tools 目录，不受影响\n"
            f"· 自定义模型目录（models_dir）若指向本目录之外，不会被删除\n\n"
            f"确认要删除吗？（删除在后台进行，大目录需几分钟）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.b_del.setEnabled(False)
        self.b_del.setText("🗑 正在后台删除，请勿关闭软件…")

        def _collect():
            out = []
            for root, _d, files in os.walk(path):
                for f in files:
                    out.append(os.path.join(root, f))
            return out

        swap_worker(self, "_del_worker", BgWorker(_collect))
        try:
            self._del_worker.sig_done.disconnect()
        except Exception:
            pass
        self._del_worker.sig_done.connect(self._on_collect_done)
        self._del_worker.start()

    def _on_collect_done(self, files):
        from PySide6.QtWidgets import QProgressDialog
        files = files or []
        self._rm_prog = QProgressDialog(
            f"正在删除 0/{len(files)} 个文件…", None, 0, max(len(files), 1), self)
        self._rm_prog.setWindowTitle("彻底删除实例")
        self._rm_prog.setCancelButton(None)
        self._rm_prog.setWindowModality(Qt.WindowModal)
        self._rm_prog.setMinimumDuration(0)
        self._rm_prog.setValue(0)
        swap_worker(self, "_rm_worker", RmWorker(self._del_path, files))
        self._rm_worker.sig_prog.connect(self._on_rm_prog)
        self._rm_worker.sig_done.connect(self._on_rm_done)
        self._rm_worker.start()

    def _on_rm_prog(self, cur_file, done, total):
        self._rm_prog.setMaximum(max(total, 1))
        self._rm_prog.setValue(done)
        self._rm_prog.setLabelText(
            f"正在删除 {done}/{total} 个文件（{done * 100 // max(total, 1)}%）\n"
            f"当前：{cur_file}")

    def _on_rm_done(self, ok, err=""):
        try:
            self._rm_prog.close()
        except Exception:
            pass
        inst, path = self._del_inst, self._del_path
        self.b_del.setEnabled(True)
        self.b_del.setText("🗑 删除此实例（可选仅移出管理 / 彻底删除文件）")
        if not ok:
            QMessageBox.critical(self, "ComfyHub",
                                 f"删除失败（可能有文件被占用）：\n{path}\n\n"
                                 "请确认 ComfyUI 已停止后重试。")
            return
        if inst in self.cfg["instances"]:
            self.cfg["instances"].remove(inst)
            save_config(self.cfg)
        try:
            self.mainwin._sync_instances()
            self.load_current()
        except Exception:
            pass   # 刷新失败不阻断删除完成提示
        QMessageBox.information(self, "ComfyHub",
                                f"实例「{inst.get('name')}」已彻底删除。")

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 根目录",
                                             self.ed_path.text() or "F:\\")
        if d and os.path.isfile(os.path.join(d, "main.py")):
            self.ed_path.setText(os.path.normpath(d))
        elif d:
            QMessageBox.warning(self, "ComfyHub", "该目录下没有 main.py")

    def _pick_models(self):
        d = QFileDialog.getExistingDirectory(self, "选择模型目录",
                                             self.ed_models.text() or "F:\\")
        if d:
            d = os.path.normpath(d)
            self.ed_models.setText(d)
            self.ed_models.setToolTip(d)
            # 只存实例字段：启动时按 --models-directory 传给 ComfyUI。
            # 不再整份重写 extra_model_paths.yaml —— 那是用户手动配的外部挂载表
            # （比如把 D:\ComfyUI-Models 挂进来），覆盖写会把人家的配置冲掉（2026-11-07）
            inst = self._current_inst()
            if inst:
                inst["models_dir"] = d
                from app.config import save_config
                save_config(self.cfg)

    def _pick_mount(self):
        """选择「挂载模型目录」的根（别的盘放模型库的那一层，下面就是 checkpoints/loras）"""
        cur = (self.ed_mount.text() or "").strip() or self.ed_models.text() or "F:\\"
        d = QFileDialog.getExistingDirectory(self, "选择要挂载的模型目录", cur)
        if not d:
            return
        d = os.path.normpath(d)
        self.ed_mount.setText(d)
        self.ed_mount.setToolTip(d)
        self.ed_mount.setCursorPosition(len(d))
        # 选完目录顺手把开关打开（否则用户容易以为已经挂上了）
        if not self.ck_mount.isChecked():
            self.ck_mount.setChecked(True)
        inst = self._current_inst()
        if inst:
            inst["mount_dir"] = d
            inst["mount_enabled"] = self.ck_mount.isChecked()
            from app.config import save_config
            save_config(self.cfg)

    def _on_mount_toggled(self, on):
        """开关联动：关掉时目录框置灰（值保留，方便下次再开）"""
        self.ed_mount.setEnabled(bool(on))
        self.b_mount.setEnabled(bool(on))
        if on and not self.ed_mount.text().strip():
            self.ed_mount.setPlaceholderText("请点右侧「选择目录」挑一个外部模型根目录")
        else:
            self.ed_mount.setPlaceholderText(
                "未选择：开启开关后可选一个外部模型根目录")

    def _pick_python(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 Python 解释器",
                                           self.ed_python.text(), "Python (python.exe)")
        if f:
            self.ed_python.setText(f)

    # ---------- 共享外挂库（全局，Tab3；2026-11-08 从系统设置搬来） ----------
    def load_shared(self):
        """把系统设置里的共享外挂库读进界面（全局，与实例无关）"""
        st = (self.cfg.get("settings") or {})
        self.ck_share.blockSignals(True)
        self.ed_share_root.blockSignals(True)
        self.ck_share.setChecked(bool(st.get("shared_models_enabled")))
        self.ed_share_root.setText(str(st.get("shared_models_root") or ""))
        self.ck_share.blockSignals(False)
        self.ed_share_root.blockSignals(False)
        self._on_share_toggled(self.ck_share.isChecked())   # 关掉时目录行置灰
        self._refresh_share_state()

    def _on_share_toggled(self, on):
        self.ed_share_root.setEnabled(bool(on))
        self.b_share.setEnabled(bool(on))

    def _pick_share_root(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择共享外挂模型库根目录", self.ed_share_root.text().strip() or "")
        if not d:
            return
        self.ed_share_root.setText(os.path.normpath(d))
        if not self.ck_share.isChecked():     # 选完顺手启用，免得白选
            self.ck_share.setChecked(True)

    def _refresh_share_state(self, *_a):
        """状态行：共享外挂当前是否生效、映射了几个类别

        （含动态值的文案用 T(静态前缀) + 变量拼，别整串进词表——词表是精确匹配）
        """
        from app import model_paths
        from app.i18n import T
        enabled = self.ck_share.isChecked()
        root = self.ed_share_root.text().strip()
        if not enabled:
            self.lb_share_state.setText(
                T("未启用（各实例仍按自己的 extra_model_paths.yaml 走）"))
            return
        if not root:
            self.lb_share_state.setText(T("已启用，但还没选挂载根目录 —— 保存会被拦下"))
            return
        if not os.path.isdir(root):
            self.lb_share_state.setText(T("目录不存在：") + root)
            return
        cats = [c for c in model_paths.STANDARD_CATS
                if os.path.isdir(os.path.join(root, c))]
        self.lb_share_state.setText(
            T("已启用 · 将映射") + f" {len(cats)} " + T("个类别子目录：")
            + ("、".join(cats) if cats else T("（该目录下没有标准子目录，映射为空）")))

    def _save_shared(self):
        """保存共享外挂库设置（写系统设置 + 立刻生成/删除托管 yaml）"""
        from app import model_paths
        on = bool(self.ck_share.isChecked())
        root = self.ed_share_root.text().strip().rstrip("\\/")
        if on:
            if not root:
                QMessageBox.warning(self, "ComfyHub",
                                    "已勾选启用共享外挂模型库，请先选择挂载根目录。")
                return
            if not os.path.isdir(root):
                QMessageBox.warning(self, "ComfyHub",
                                    f"共享外挂库目录不存在：\n{root}")
                return
        st = self.cfg.setdefault("settings", {})
        same = (bool(st.get("shared_models_enabled")) == on
                and str(st.get("shared_models_root") or "").rstrip("\\/")
                == (root if on else str(st.get("shared_models_root") or "").rstrip("\\/")))
        st["shared_models_enabled"] = on
        st["shared_models_root"] = root
        save_config(self.cfg)
        try:                      # 关掉就删 yaml，别留僵尸挂载
            model_paths.sync_shared_config(root, on)
        except Exception:
            log_exc("sync_shared_config")
        self._on_share_toggled(on)
        self._refresh_share_state()
        if same:
            QMessageBox.information(self, "ComfyHub", "没有改动，无需保存")
        else:
            QMessageBox.information(
                self, "ComfyHub",
                "✅ 共享外挂库设置已保存\n\n"
                + ("已生成挂载表，下次启动实例即生效（正在运行的实例需重启）。"
                   if on else "已关闭，托管挂载表已删除。"))


    # ---------- 启停（供首页/控制台调用） ----------
    def _open_batch(self):
        """打开批量运行对话框（用户手动开启，可设次数/重启间隔）"""
        inst = self.mainwin.current_inst()
        if not inst:
            QMessageBox.information(self, "批量运行", "请先选择实例")
            return
        from app.ui.batch_dialog import BatchRunDialog
        dlg = BatchRunDialog(self.mainwin, inst, self)
        dlg.exec()

    def start(self):
        inst = self._current_inst()
        if not inst:
            return
        # device 字段由 build_command 统一处理（--cpu 等）
        self.mainwin.procs.get(inst).start(inst)
        self._update_state()
        self.mainwin.goto(NAV.CONSOLE)   # 启动后自动切到「控制台」看实时日志

    def stop(self):
        inst = self._current_inst()
        if inst:
            self.mainwin.procs.get(inst).stop()
            self._update_state()

    def open_browser(self):
        """打开当前实例网页：未启动不打开；启动时按实际监听端口
        （配置端口被占、自动顺延后实际端口可能 ≠ 配置端口）"""
        import webbrowser
        inst = self._current_inst()
        if not inst:
            return
        if not self.mainwin.procs.is_running(inst):
            self.mainwin.console_page.append_line(
                "[ComfyHub] 实例未启动，无法打开网页。")
            return
        cfg = int(inst.get("port") or 8188)
        port = self.mainwin.procs.actual_port(inst) or cfg
        if port != cfg:
            self.mainwin.console_page.append_line(
                f"[ComfyHub] 实际端口 {port}（配置 {cfg}），已按实际端口打开")
        webbrowser.open(f"http://127.0.0.1:{port}")
