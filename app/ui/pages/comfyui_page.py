# -*- coding: utf-8 -*-
"""ComfyUI 编辑页：实例设置（基础+高级+全局参数合一页，三分区标题）+ 内核版本 + 启停 + 保存/删除"""
import os
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor

_KVER_PAGE = 20   # 内核版本列表一次加载行数（2026-11-02 用户定：分页 + 「加载更多」）
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSpinBox, QCheckBox, QFileDialog, QMessageBox, QFrame,
    QScrollArea, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QGridLayout, QApplication,
)

from app.config import VRAM_MODES, save_config
from app.log import log_exc
from app.ui.nav import NAV
from app.workers import BgWorker, RemoteOpsWorker, RmWorker, swap_worker, wait_all
from app.process import build_command, port_in_use
from app.i18n import T
from app.ui import theme
from app.ui.theme import card_table
from app.ui.pages.remote_ops_page import RemoteOpsPanel


class _Row(QFrame):
    """行式设置项：左标题+说明（固定列），右控件（紧跟其后、限宽），尾部留白

    2026-11-08 修显示问题：2560 宽屏下控件原来被拉到 2000px 宽（一条细长横条，
    下拉箭头飘在中间），看着不像正经设置面板。
    改法（第 2 版）：标签列固定 230、控件紧跟标签左对齐、限宽 560，剩余宽度
    全部由尾部留白吸收 —— 整页阅读起点统一在左，不会出现"控件贴最右、中间
    空出一大截"的断层感（第 1 版把 stretch 放中间就是这个毛病）。
    """
    def __init__(self, title, desc, widget, wmax=640):
        """两行式：第 1 行「标题 …… 控件」两端对齐，第 2 行说明占满宽度

        2026-11-08 第 4 版。前三版都是「左标签列(220) + 右控件」的横向两栏，问题是
        说明文字被塞进 220 宽里爆成 3~4 行，卡片长高、左侧形成一整块竖向空白带，
        用户连着反馈「留白太多 / 没对齐」。
        改成两行式后：
          · 说明独占一行 → 一行就能写完，卡片高度砍掉近一半
          · 标题左、控件右（参考 ComfyUI 管理大师的两端对齐）→ 视觉重心落在两侧，
            中间留白不会形成"断层"
          · 控件宽度按类型定：下拉/数字 480、输入框 640（路径够长又不会被拉成细条）
        """
        super().__init__()
        self.setObjectName("RowCard")
        # 不设这个 Qt 只画直边不裁圆角（QSS 背景需要 WA_StyledBackground）
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 11, 18, 11)
        v.setSpacing(5)

        h = QHBoxLayout()
        h.setSpacing(16)
        t = QLabel(title)
        t.setObjectName("RowTitle")
        h.addWidget(t, 0, Qt.AlignVCenter)
        h.addStretch(1)                      # 把控件顶到右侧
        if widget:
            from PySide6.QtWidgets import QComboBox, QSpinBox, QCheckBox
            # ⚠️ 两个坑（2026-11-08 连踩）：
            #   1. `addWidget(w, n, Qt.AlignXXX)` 带 alignment 时 Qt 只按 sizeHint 摆放，
            #      输入框会缩成 120px 小细条；
            #   2. 但去掉 alignment 后如果控件是 setFixedWidth，一旦行宽算不准就会
            #      顶到卡片外 —— 所以这里既不指定 alignment，也用 maximumWidth 封顶，
            #      让布局按"sizeHint~maxWidth"自由分配。
            if not isinstance(widget, QCheckBox):
                _narrow = isinstance(widget, (QComboBox, QSpinBox))
                widget.setMinimumWidth(480 if _narrow else wmax)
                widget.setMaximumWidth(480 if _narrow else wmax)
            h.addWidget(widget, 0)
        v.addLayout(h)

        d = QLabel(desc)
        d.setObjectName("RowDesc")
        d.setWordWrap(True)
        v.addWidget(d)




class _FlowBox(QWidget):
    """设置行容器：**宽度 ≥1700 自动排成两列**，否则单列

    2026-11-08 第 5 版。23 个设置行铺成单列时，每行只有「标签 + 640 控件」，
    卡片剩余 1000+ px 全是空白 —— 用户连着几轮反馈「左右留白太多」。
    排成两列后宽度被填满，控件也不会被拉成细长条（这是宽屏设置页的通行做法：
    Windows 11 设置、macOS 系统设置的分组网格都是这个思路）。

    对调用方透明：提供 addWidget / addSpacing / addStretch 三个与 QVBoxLayout
    同名的方法，原代码一行都不用改。
      · objectName == "SectionLabel" 的标题 → 占整行
      · 其余（_Row）→ 占半行，按列数自动换行
    """
    TWO_COL_MIN = 1700          # 页面宽到这个值才排两列

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []        # [(kind, widget)] kind: full / half / gap / stretch
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(22, 18, 22, 18)
        self._grid.setSpacing(10)
        self._cols = 0
        # ⚠️ 必须用 dirty 标记：只靠"cols 有没有变"做去重会漏掉新增项 ——
        # 第一个控件加进来时 cols 0→1 排一次，之后 _cols 已是 1，后面 24 个
        # addWidget 全部命中 `cols == _cols` 直接 return，grid 里永远只有 1 个
        # 控件、整页空白（2026-11-08 实测：items=25 但 grid_count=1）
        self._dirty = True

    # ---- 与 QVBoxLayout 同名接口 ----
    def addWidget(self, w, *a, **k):          # noqa: N802
        full = w.objectName() == "SectionLabel"
        self._items.append(("full" if full else "half", w))
        self._dirty = True
        self._relayout()

    def addSpacing(self, px):                 # noqa: N802
        self._items.append(("gap", int(px)))
        self._dirty = True
        self._relayout()

    def addStretch(self, *a):                 # noqa: N802
        self._items.append(("stretch", None))
        self._dirty = True
        self._relayout()

    # ---- 重排 ----
    def _relayout(self):
        cols = 2 if self.width() >= self.TWO_COL_MIN else 1
        if cols == self._cols and not self._dirty:
            return
        self._cols = cols
        self._dirty = False
        r = c = 0
        for kind, obj in self._items:
            if kind == "stretch":
                self._grid.setRowStretch(r, 1)
                continue
            if kind == "gap":
                r += 1
                continue
            if kind == "full":
                if c:
                    r += 1
                    c = 0
                self._grid.addWidget(obj, r, 0, 1, cols)
                r += 1
            else:
                self._grid.addWidget(obj, r, c)
                c += 1
                if c >= cols:
                    c = 0
                    r += 1
        for cc in range(2):
            self._grid.setColumnStretch(cc, 1 if cc < cols else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()


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
        # 远程运维字段（2026-12-01，0.8.40）：取值来源 = 「🛠 远程运维」页签的
        # `self.remote_ops.collect()`（0.8.40 T05 接线）。第一批曾用 `_ops_values`
        # 兜底（那时还没有页签），现在必须走页签，否则「页签改了保存不生效」。
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
        t1v = _FlowBox()      # 宽屏自动两列（见 _FlowBox）
        lb1 = QLabel("基础设置")
        lb1.setObjectName("SectionLabel")
        t1v.addWidget(lb1)

        self.ed_name = QLineEdit()
        t1v.addWidget(_Row("实例名称", "实例显示名称", self.ed_name))

        # ---- 连接方式 + 远程连接参数（2026-11-23，0.8.39）----
        # 一个实例列表、一套状态机，只有「连哪」不同。远程模式下本机专属的
        # 目录/引擎/显存/参数行全部隐藏（见 _apply_kind_visibility）——这些设置
        # 对远端不起作用，留着只会让人以为改了会生效。
        self.cb_kind = QComboBox()
        self.cb_kind.addItems(["本机实例", "远程实例"])
        self.cb_kind.setToolTip(
            "本机实例：在这台电脑上启动并管理 ComfyUI（原有行为）\n"
            "远程实例：连接另一台机器或云镜像上已经跑着的 ComfyUI\n"
            "　　　　　例：http://192.168.1.20:8188、https://xxx.platform.com")
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        t1v.addWidget(_Row(
            "连接方式",
            "本机实例=在这台电脑启动；远程实例=连另一台机器/云镜像上已运行的 ComfyUI"
            "（本机只负责提交任务、看队列、取结果，不能启停远端进程）",
            self.cb_kind))

        h_url = QHBoxLayout()
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("http://192.168.1.20:8188 或 https://xxx.platform.com")
        self.b_test = QPushButton("测试连接")
        self.b_test.clicked.connect(self._test_remote)
        h_url.addWidget(self.ed_url, 1)
        h_url.addWidget(self.b_test)
        wrap_url = QWidget(); wrap_url.setLayout(h_url)
        self.row_url = _Row(
            "访问地址",
            "对方 ComfyUI 的 HTTP 地址（只写 IP:端口 也行，会自动补 http://）",
            wrap_url)
        t1v.addWidget(self.row_url)

        self.cb_auth = QComboBox()
        self.cb_auth.addItems(["不需要认证", "Bearer Token", "账号密码（Basic）", "自定义请求头"])
        self.cb_auth.currentIndexChanged.connect(lambda _i: self._apply_auth_rows())
        self.row_auth = _Row(
            "认证方式",
            "云平台多给 Token 或自定义请求头；自己内网裸 IP 直连一般选「不需要认证」",
            self.cb_auth)
        t1v.addWidget(self.row_auth)

        self.ed_token = QLineEdit()
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("粘贴平台给的 Token / 请求头的值")
        self.row_token = _Row(
            "凭证", "加密保存在本机（DPAPI），不会明文写进配置文件", self.ed_token)
        t1v.addWidget(self.row_token)

        self.ed_hname = QLineEdit()
        self.ed_hname.setPlaceholderText("X-API-KEY")
        self.row_hname = _Row(
            "请求头名称", "选「自定义请求头」时的头名，如 X-API-KEY / X-Token",
            self.ed_hname)
        t1v.addWidget(self.row_hname)

        h_basic = QHBoxLayout()
        self.ed_user = QLineEdit(); self.ed_user.setPlaceholderText("用户名")
        self.ed_pass = QLineEdit(); self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_pass.setPlaceholderText("密码")
        h_basic.addWidget(self.ed_user, 1)
        h_basic.addWidget(self.ed_pass, 1)
        wrap_basic = QWidget(); wrap_basic.setLayout(h_basic)
        self.row_basic = _Row("账号密码", "HTTP Basic 认证（少数反代/平台使用）", wrap_basic)
        t1v.addWidget(self.row_basic)

        self.ck_tls = QCheckBox("校验 HTTPS 证书")
        self.ck_tls.setChecked(True)
        self.row_tls = _Row(
            "TLS 证书", "云平台用自签证书时取消勾选，否则会报证书校验失败", self.ck_tls)
        t1v.addWidget(self.row_tls)

        self._remote_rows = [self.row_url, self.row_auth, self.row_token,
                             self.row_hname, self.row_basic, self.row_tls]
        self._t1v = t1v
        # 本机专属行的起点（含「高级设置」标题）；终点在参数行之后记
        self._local_start = len(t1v._items)

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
        # 「已继承全局共享库」提示（方案 A，2026-11-08）：
        # 共享外挂库和这里的「启用挂载模型目录」功能极像，用户分不清哪个管全局、
        # 更会误以为"要先把原 models 删掉共享库才生效"。这条提示直接在当前实例
        # 上下文里说明：全局那份已经挂上了，原 models 仍扫描，这里只挂本实例专用库。
        # ⚠️ 文案拆成三段控件：i18n 是"控件树扫描 + 词表精确匹配"，含动态路径的
        # 整句永远匹配不上（见项目铁律「翻译铁律」）。
        self.lb_share_hint = QWidget()
        _hv = QHBoxLayout(self.lb_share_hint)
        _hv.setContentsMargins(0, 0, 0, 0)
        _hv.setSpacing(0)
        self.lb_sh_a = QLabel("已继承全局共享库：")
        self.lb_sh_path = QLabel("")
        self.lb_sh_path.setMaximumWidth(420)
        self.lb_sh_b = QLabel("（原 models 目录仍会扫描，这里只挂本实例专用的）")
        for _l in (self.lb_sh_a, self.lb_sh_path, self.lb_sh_b):
            _l.setStyleSheet("color:#7fa8d9; background:transparent;")
            _hv.addWidget(_l)
        _hv.addStretch(1)
        self.lb_share_hint.setVisible(False)
        v2.addWidget(self.lb_share_hint)
        wrap2.setMinimumHeight(74)
        t1v.addWidget(_Row(
            "模型目录",
            "本实例的模型根目录（留空跟随 <根目录>/models）；下面的开关只挂本实例专用库，"
            "与「共享外挂库」叠加生效",
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
        # 本机专属行的终点（不含后面的 stretch 与两列重排）
        self._local_end = len(t1v._items)

        # 2026-10-28 用户要求：专家模式整块移除（全局启动参数不再暴露 UI）

        t1v.addStretch()
        # 宽屏下内容不铺满：居中一个 1120 的内容列，两侧留白（2026-11-08，第 2 版
        # 从 1460 收到 1120 —— 1460 在 1600 窗口下几乎铺满、留白不对称，看着仍是"平铺"）
        # 内容列 1600（第 3 版）：2560 全屏下 1120 只占 44%，整页缩成中间一条、
        # 左右各空 720px —— 用户报「全屏出现很多变形」。1600 在 1600 窗口下正好
        # 铺满、在 2560 下占 65%，行内控件另有 700 上限所以不会被拉成细长条。
        t1v.setMaximumWidth(2280)
        s1 = QScrollArea(); s1.setWidgetResizable(True); s1.setFrameShape(QFrame.NoFrame)
        s1.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        s1.setWidget(t1v)
        self.tabs.addTab(s1, "⚙️ 实例设置")

        # ---- Tab2 内核版本 ----
        t2 = QWidget(); t2v = QVBoxLayout(t2)
        t2v.setContentsMargins(22, 18, 22, 18); t2v.setSpacing(10)
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
        t2v.addWidget(_Row("内核版本管理", "一键更新到最新稳定版，或按版本回滚（切换后自动安装配套依赖）", wrap_k, wmax=820))

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
        t2v.addWidget(card_table(self.tb_kver), 1)
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
        t3v.setContentsMargins(22, 18, 22, 18); t3v.setSpacing(10)
        lb_s = QLabel("共享外挂模型库")
        lb_s.setObjectName("SectionLabel")
        # 作用域徽章：和 Tab1 的「启用挂载模型目录」区分开（方案 A）
        _hs = QHBoxLayout()
        _hs.setSpacing(8)
        _hs.addWidget(lb_s)
        lb_badge = QLabel("全局 · 所有实例共用")
        lb_badge.setObjectName("ScopeBadge")
        _hs.addWidget(lb_badge)
        _hs.addStretch(1)
        t3v.addLayout(_hs)

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
        # 保存按钮紧跟内容（2026-11-08 修：原来 addStretch 把它顶到 Tab 底部，
        # 中间 600px 空白，像丢在外面）
        h_save = QHBoxLayout()
        h_save.addStretch()
        self.b_share_save = QPushButton("保存共享库设置")
        self.b_share_save.setProperty("accent", True)
        self.b_share_save.clicked.connect(self._save_shared)
        h_save.addWidget(self.b_share_save)
        t3v.addLayout(h_save)
        t3v.addStretch(1)
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

        # ---- Tab4 🛠 远程运维（2026-12-01，0.8.40 T05）----
        # 只在 kind=remote 时可见（_apply_kind_visibility 调 setTabVisible）；
        # 初始隐藏：本机实例绝不该看到这一页（PRD A1）。
        self.remote_ops = RemoteOpsPanel(self.mainwin)
        self.remote_ops.sig_hook_request.connect(self._on_hook_request)
        self.tabs.addTab(self.remote_ops, "🛠 远程运维")
        self.tabs.setTabVisible(3, False)

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
            self.cb_kind.setCurrentIndex(0)
            self.ed_url.clear()
            self.ed_token.clear()
            self.ed_pass.clear()
            self._apply_kind_visibility()
            self.remote_ops.clear()
            self._loaded = None
            return
        self.ed_name.setText(inst.get("name", ""))
        # 远程运维字段（0.8.40）：页签控件与实例值双向同步（collect() 是取值唯一入口）
        self.remote_ops.load(inst)
        # 连接方式与远程参数（2026-11-23，0.8.39）
        _remote = (inst.get("kind") or "local") == "remote"
        self.cb_kind.blockSignals(True)
        self.cb_kind.setCurrentIndex(1 if _remote else 0)
        self.cb_kind.blockSignals(False)
        self.ed_url.setText(inst.get("base_url") or "")
        _auth = str(inst.get("auth_type") or "none").lower()
        self.cb_auth.blockSignals(True)
        self.cb_auth.setCurrentIndex(
            {"none": 0, "bearer": 1, "basic": 2, "header": 3}.get(_auth, 0))
        self.cb_auth.blockSignals(False)
        self.ed_hname.setText(inst.get("auth_header_name") or "X-API-KEY")
        # 凭证不回填真值：只提示「已保存，留空不修改」，避免明文停在界面上
        self.ed_token.clear()
        self.ed_token.setPlaceholderText(
            "已保存（留空则不修改）" if str(inst.get("auth_value") or "").strip()
            else "粘贴平台给的 Token / 请求头的值")
        self.ed_user.setText(inst.get("auth_user") or "")
        self.ed_pass.clear()
        self.ed_pass.setPlaceholderText(
            "已保存（留空则不修改）" if str(inst.get("auth_pass") or "").strip() else "密码")
        self.ck_tls.setChecked(bool(inst.get("tls_verify", True)))
        self._apply_kind_visibility()
        self.ed_path.setText(inst.get("comfyui_path", ""))
        # 模型目录：配置为空 = 跟随默认（<comfyui_path>/models）。输入框留空、
        # 灰字显示默认路径；别把默认路径写死进配置，否则以后挪 ComfyUI 目录时
        # 这一栏还钉在旧位置（2026-11-07）。已经写死成默认路径的老配置也顺手净化回空
        _dflt_models = os.path.join(inst.get("comfyui_path") or "", "models")
        _md = (inst.get("models_dir") or "").strip()
        if _md and os.path.normpath(_md) == os.path.normpath(_dflt_models):
            _md = ""
        self.ed_models.setText(_md)
        self.ed_models.setPlaceholderText(_dflt_models)
        # 挂载模型目录（2026-11-08）：按实例存，切实例各自独立
        self.ck_mount.setChecked(bool(inst.get("mount_enabled")))
        # 全局共享库是否生效 → 决定是否显示继承提示
        try:
            from app.config import load_config as _load_cfg
            _st_g = (_load_cfg().get("settings") or {})
            _sroot = str(_st_g.get("shared_models_root") or "").strip()
            _on = bool(_st_g.get("shared_models_enabled")) and bool(_sroot)
            self.lb_share_hint.setVisible(_on)
            if _on:
                self.lb_sh_path.setText(_sroot)
                self.lb_sh_path.setToolTip(_sroot)
        except Exception:
            self.lb_share_hint.setVisible(False)
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
        # 远程实例没有本地内核目录（0.8.39）
        if not inst or (inst.get("kind") or "local") == "remote":
            return None
        # git fetch/pull 走实例代理（2026-10-29：GitHub 直连慢时靠它）
        return KernelManager(inst["comfyui_path"],
                             proxy=inst.get("proxy") or "") if inst else None

    def _refresh_kernel_info(self):
        """内核信息展示：同步 git 子进程改为「缓存秒画 + 后台补读」
        （2026-10-31 卡顿修复，见 _KernelInfoWorker 注释）"""
        inst = self.mainwin.current_inst()
        if not inst or not hasattr(self, "lb_kver"):
            return
        if (inst.get("kind") or "local") == "remote":
            # 远程实例拿不到本地内核版本（0.8.39）；真有需要可查 /system_stats
            self.lb_kver.setText("远程实例（内核版本请在该机器查看）")
            self.lb_kver.setStyleSheet("color:#9aa4b2;")
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
            k.setForeground(QColor(theme.fg("#4cd47c" if v["kind"] == "稳定版" else "#7f8b9c")))
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

    def _set_lamp(self, running: bool, text: str):
        """状态灯唯一写入口：文案 + objectName + 重刷 QSS"""
        self.lb_state.setText(text)
        self.lb_state.setObjectName("StatusRun" if running else "StatusStop")
        self.lb_state.style().unpolish(self.lb_state)
        self.lb_state.style().polish(self.lb_state)

    def _update_state(self):
        inst = self.mainwin.current_inst()
        procs = self.mainwin.procs
        remote = bool(inst and (inst.get("kind") or "local") == "remote")
        if remote:
            # 远程实例（0.8.40）：按钮态按 PRD 5.1.2 表格走，本机分支完全不动
            self._update_remote_state(inst)
            return
        own = bool(inst and procs.is_own(inst))
        running = bool(inst and procs.is_running(inst))
        self.b_start.setText(T("▶ 启动"))
        self.b_start.setEnabled(bool(inst) and not running)
        self.b_stop.setText(T("■ 停止"))
        self.b_stop.setEnabled(own)
        self.b_web.setEnabled(running)
        self.b_start.setToolTip("")
        self.b_stop.setToolTip(
            "" if own else T("该实例不是本程序启动的，请到它的窗口/命令行停止"))
        self.b_web.setToolTip(T("打开当前实例网页（自动识别实际端口）")
                              if running else T("实例启动后可用"))
        self._set_lamp(running, T("● 运行中") if running else T("未运行"))

    def _update_remote_state(self, inst: dict):
        """远程实例的按钮态与状态灯（PRD 5.1.2 表格 + ARCH §4.7.1）

        状态灯**只在这里改**（T06 的隧道状态变化也走 `_update_state()` 刷新），
        避免「页签里改了状态行、顶栏还是旧文案」的分裂。
        """
        from app import api_base as apib
        from app import remote_ops as ro
        procs = self.mainwin.procs
        st = ro.ops.state_of(inst) or {}
        state = str(st.get("state") or ro.ST_IDLE)
        via = False
        try:
            via = bool(apib.effective_base_info(inst).get("via_tunnel"))
        except Exception:                   # noqa: BLE001 - 诊断信息绝不能抛
            via = False
        running = bool(procs.is_running(inst))
        start_ch = ro.ops.available_channels(inst, action="start")
        stop_ch = ro.ops.available_channels(inst, action="stop")
        starting = state in (ro.ST_CONNECTING, ro.ST_EXECUTING, ro.ST_WAITING_READY)
        stopping = state == ro.ST_STOPPING

        self.b_start.setText(T("启动中…") if starting else T("▶ 远程启动"))
        self.b_start.setEnabled(bool(start_ch) and not starting and not stopping
                                and not running)
        self.b_stop.setText(T("■ 远程停止"))
        # 「启动中」也允许点停止（中断误启动，PRD 边界 #5）
        self.b_stop.setEnabled(bool(stop_ch) and not stopping)
        self.b_web.setEnabled(running)
        if not start_ch:
            self.b_start.setToolTip(T(
                "还没配远程启动方式。到「ComfyUI → 🛠 远程运维」"
                "填 SSH 或云平台开关机接口"))
        elif starting:
            self.b_start.setToolTip(T("正在启动，等待实例就绪（最多 5 分钟）"))
        elif running:
            self.b_start.setToolTip(T("实例已在运行，如需重启请先「远程停止」"))
        else:
            self.b_start.setToolTip(T("在远端执行启动命令，或请求云平台开机"))
        if not stop_ch:
            self.b_stop.setToolTip(T(
                "还没配远程停止方式。到「ComfyUI → 🛠 远程运维」"
                "填 SSH 停止命令或云平台关机接口"))
        elif starting:
            self.b_stop.setToolTip(T("中止本轮等待并执行停止命令（不会杀无关进程）"))
        else:
            self.b_stop.setToolTip(T("在远端执行停止命令，或请求云平台关机"))
        self.b_web.setToolTip(T("在浏览器打开远端 ComfyUI")
                             + (T("（经隧道）") if via and running else ""))
        if starting:
            self._set_lamp(True, T("● 启动中…"))
        elif stopping:
            self._set_lamp(True, T("● 停止中…"))
        elif running:
            self._set_lamp(True, T("● 运行中") + (T("（经隧道）") if via else ""))
            # PRD D3：连接成功那一刻给一次明文告警条（去重与「不再提示」在主窗）
            try:
                self.mainwin.maybe_insecure_banner(inst)
            except Exception:               # noqa: BLE001 - 提示不能反过来添乱
                pass
        else:
            self._set_lamp(False, "未运行")

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
        # 连接方式（0.8.39）
        "kind": "连接方式", "base_url": "访问地址", "auth_type": "认证方式",
        "auth_value": "凭证", "auth_header_name": "请求头名称",
        "auth_user": "账号", "auth_pass": "密码", "tls_verify": "校验 HTTPS 证书",
        # 远程运维（0.8.40）：SSH / 云钩子 / 隧道
        "ssh_host": "SSH 主机", "ssh_port": "SSH 端口", "ssh_user": "SSH 用户名",
        "ssh_auth": "SSH 认证方式", "ssh_password": "SSH 密码",
        "ssh_key_path": "SSH 密钥文件", "ssh_key_passphrase": "SSH 密钥口令",
        "ssh_start_cmd": "远程启动命令", "ssh_stop_cmd": "远程停止命令",
        "ssh_host_fingerprint": "SSH 主机指纹",
        "cloud_start": "云平台开机请求", "cloud_stop": "云平台关机请求",
        "tunnel_local_port": "隧道本地端口", "tunnel_remote_host": "隧道远端主机",
        "tunnel_remote_port": "隧道远端端口",
        "insecure_warn_muted": "已忽略明文告警", "last_ops_channel": "上次远程通道",
    }
    # 这些字段留空 = 用默认值，对比框里别显示成「（空）」让人以为没配
    _SAVE_EMPTY_TEXT = {
        "models_dir": "默认（<根目录>/models）",
        "mount_dir": "未选择",
        "extra_args": "无",
        "base_url": "未填写",
        "ssh_password": "未填写",
        "ssh_key_passphrase": "未填写",
        "cloud_start": "未配置",
        "cloud_stop": "未配置",
        "ssh_host_fingerprint": "未记录",
    }

    # 参与保存/对比的字段
    _TRACK = ("name", "models_dir", "python_path", "port", "vram_mode",
              "cross_attn", "preview_method", "precision", "fast_mode",
              "no_smart_memory", "cpu_vae", "deterministic", "channels_last",
              "cuda_malloc", "multi_user", "lan_access", "hf_offline",
              "auto_open_browser", "extra_args",
              "mount_enabled", "mount_dir",
              # 连接方式（0.8.39）
              "kind", "base_url", "auth_type", "auth_value", "auth_header_name",
              "auth_user", "auth_pass", "tls_verify",
              # 远程运维（0.8.40）：cloud_start_timeout_s 不暴露给用户，故不入表
              "ssh_host", "ssh_port", "ssh_user", "ssh_auth", "ssh_password",
              "ssh_key_path", "ssh_key_passphrase", "ssh_start_cmd",
              "ssh_stop_cmd", "ssh_host_fingerprint",
              "cloud_start", "cloud_stop",
              "tunnel_local_port", "tunnel_remote_host", "tunnel_remote_port",
              "insecure_warn_muted", "last_ops_channel")

    # ---------- 远程运维字段（2026-12-01，0.8.40）----------
    # 约定（ARCH §9）：新增实例字段必须同时出现在 _INST_DEFAULTS / _TRACK /
    # _SAVE_LABELS / _snapshot / remote_ops_page.collect 五处，漏一处就会
    # 「改了不保存」或「保存了不显示」。第 4 处（_snapshot）在这里接线，
    # 第 5 处是 `self.remote_ops.collect()`，两边的键名逐字一致。
    #
    # 两个字段**不经页签回写**（所以 collect() 不返回它们）：
    #   · ssh_host_fingerprint —— 首次连接时由 remote_fingerprint 写入；
    #   · insecure_warn_muted  —— 明文告警模态 / 设置页「恢复明文告警」写入。
    # 若把它们也塞进 collect()，用户在页签点保存就会把后台刚记下的指纹冲回旧值。

    # ---------- 连接方式（2026-11-23，0.8.39）----------
    def _is_remote_form(self) -> bool:
        return self.cb_kind.currentIndex() == 1

    def _on_kind_changed(self, *_a):
        self._apply_kind_visibility()

    def _apply_kind_visibility(self):
        """本机专属行 vs 远程连接行，二选一显示

        坑：_FlowBox 自己维护 _items（不是 QLayout），所以按索引取控件，
        不能用 widget(i)。隐藏后 QGridLayout 会收回占位，不会留空白。
        """
        if not hasattr(self, "_remote_rows"):
            return
        remote = self._is_remote_form()
        for w in self._remote_rows:
            w.setVisible(remote)
        items = getattr(self._t1v, "_items", [])
        for i in range(self._local_start, min(self._local_end, len(items))):
            w = items[i][1]
            # _items 里混着 ("gap", 10) 这种整数占位，只处理真控件
            if w is not None and hasattr(w, "setVisible"):
                w.setVisible(not remote)
        self._apply_auth_rows()
        # 内核版本 / 共享外挂库都是本地文件操作，远程实例上没意义
        for idx in (1, 2):
            try:
                self.tabs.setTabVisible(idx, not remote)
            except Exception:
                pass
        # 「🛠 远程运维」只在远程实例上出现（PRD A1）；隐藏即收起按钮占位，不留空位
        try:
            self.tabs.setTabVisible(3, remote)
        except Exception:
            pass
        if remote:
            # 用户可能正停在远程运维页签上填表，别硬拽回第 1 个页签
            if self.tabs.currentIndex() not in (0, 3):
                self.tabs.setCurrentIndex(0)
        elif self.tabs.currentIndex() == 3:
            self.tabs.setCurrentIndex(0)

    def _apply_auth_rows(self):
        if not hasattr(self, "_remote_rows"):
            return
        remote = self._is_remote_form()
        kind = self.cb_auth.currentIndex()      # 0 无 / 1 bearer / 2 basic / 3 header
        self.row_token.setVisible(remote and kind in (1, 3))
        self.row_hname.setVisible(remote and kind == 3)
        self.row_basic.setVisible(remote and kind == 2)

    def _collect_remote(self, inst: dict) -> dict:
        """表单里的远程字段写进 inst（保存与「测试连接」共用一份逻辑）"""
        idx = self.cb_auth.currentIndex()
        inst["kind"] = "remote"
        inst["base_url"] = self.ed_url.text().strip()
        inst["auth_type"] = ("none", "bearer", "basic", "header")[idx]
        # 凭证留空 = 保持原值。否则「打开设置→点保存」会把已存 token 清空，
        # 用户下次连不上还得重新找平台要一遍（这类投诉最难查）。
        tok = self.ed_token.text().strip()
        if tok:
            inst["auth_value"] = tok
        inst["auth_header_name"] = self.ed_hname.text().strip() or "X-API-KEY"
        if self.ed_user.text().strip():
            inst["auth_user"] = self.ed_user.text().strip()
        if self.ed_pass.text():
            inst["auth_pass"] = self.ed_pass.text()
        inst["tls_verify"] = self.ck_tls.isChecked()
        return inst

    def _test_remote(self):
        """测试连接：真打一次 /system_stats，报错按原因分开说"""
        url = self.ed_url.text().strip()
        if not url:
            QMessageBox.warning(self, "ComfyHub",
                                "请先填写访问地址，例如 http://192.168.1.20:8188")
            return
        inst = self._collect_remote(dict(self._current_inst() or {}))
        from app import api_base as apib
        self.b_test.setEnabled(False)
        self.b_test.setText("测试中…")
        QApplication.processEvents()
        try:
            # saved=True（0.8.40）：隧道开着时也必须测**用户填的真地址**，
            # 否则隧道通了会把「远端其实连不上」测成「成功」，是假成功。
            ok, reason = apib.probe_reason(inst, timeout=8, saved=True)
        finally:
            self.b_test.setEnabled(True)
            self.b_test.setText("测试连接")
        if ok:
            QMessageBox.information(self, "ComfyHub",
                                    "连接成功：该地址上是一个运行中的 ComfyUI。")
        else:
            QMessageBox.warning(self, "ComfyHub", f"连接失败：\n{reason}")

    def _snapshot(self) -> dict:
        """界面当前取值（键与 _TRACK 一致，外加 engine）

        保存对比一律用它做基准：老配置里缺的字段（None / ""）在界面上已经被
        load_current 填成默认值，快照里就是那个默认值，于是「没动过 = 没改动」，
        不会再刷出一串「（空）→ 自动」的假改动（2026-11-08 用户报障）。
        """
        d = {
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
            # 连接方式（0.8.39）
            "kind": "remote" if self._is_remote_form() else "local",
            "base_url": self.ed_url.text().strip(),
            "auth_type": ("none", "bearer", "basic", "header")[self.cb_auth.currentIndex()],
            "auth_value": self.ed_token.text().strip(),
            "auth_header_name": self.ed_hname.text().strip() or "X-API-KEY",
            "auth_user": self.ed_user.text().strip(),
            "auth_pass": self.ed_pass.text(),
            "tls_verify": self.ck_tls.isChecked(),
        }
        # 远程运维字段（0.8.40）：唯一来源 = 「🛠 远程运维」页签
        # ⚠ 这一行就是「页签改了保存不生效」的分水岭：换成 collect() 之后，
        # 页签里的 15 个字段才真正进入 _save 的写回循环。
        d.update(self.remote_ops.collect())
        return d

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
        # 远程连接（0.8.39）：凭证留空 = 保持原值，避免「打开设置→保存」把
        # 已存的 Token 清空（用户下次连不上还得回平台重新要一遍）
        if new.get("kind") == "remote":
            for _k in ("auth_value", "auth_user", "auth_pass"):
                if not new.get(_k):
                    new[_k] = inst.get(_k) or ""
            if not new.get("base_url"):
                QMessageBox.warning(self, "ComfyHub",
                                    "连接方式选了「远程实例」，但访问地址是空的。\n"
                                    "请填写对方 ComfyUI 的地址（例：http://192.168.1.20:8188），"
                                    "或把连接方式改回「本机实例」。")
                return
            # SSH 密码 / 私钥口令同样「留空 = 保持原值」（页签 collect() 已实现，
            # 这里再兜一层：万一将来有别的入口改了表单，也不能把密文冲掉）
            for _k in ("ssh_password", "ssh_key_passphrase"):
                if not str(new.get(_k) or ""):
                    new[_k] = inst.get(_k) or ""
            # PRD A4：启动/停止命令清空后不许保存（否则远程启停必然 E3）
            if not str(new.get("ssh_start_cmd") or "").strip():
                QMessageBox.warning(self, "ComfyHub",
                                    "启动命令不能为空。\n"
                                    "到「🛠 远程运维」页签填一条能在远端执行起来 ComfyUI 的命令，"
                                    "或点那一行的「恢复默认」。")
                return
            if not str(new.get("ssh_stop_cmd") or "").strip():
                QMessageBox.warning(self, "ComfyHub",
                                    "停止命令不能为空。\n"
                                    "到「🛠 远程运维」页签填一条能停掉远端 ComfyUI 的命令，"
                                    "或点那一行的「恢复默认」。")
                return
        # 凭证不回显：确认框只用于「看清改了什么」，把 token/密码原文打在屏幕上
        # 没必要（旁边可能有人看着）。**新旧两侧都要遮**：先前只遮了新值，
        # 用户改密码时确认框里就把旧口令明文打了出来（QA V2 L1b 抓到的缺陷）。
        _disp = dict(new)
        _old_disp = dict(old)
        for _k in ("auth_value", "auth_pass", "ssh_password", "ssh_key_passphrase"):
            _o = old.get(_k) or ""
            _n = _disp.get(_k) or ""
            if _o and _n != _o:
                _old_disp[_k] = "（已设置，不回显）"
            if _n and _n != _o:
                _disp[_k] = "（已更新，不回显）"
        changes = diff_changes(_old_disp, _disp, self._SAVE_LABELS,
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
        # PRD D2：改动确认之后、写盘之前弹一次 http 明文告警（可永久忽略）
        if self._insecure_modal(inst, new) == "abort":
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

    def _insecure_modal(self, inst: dict, new: dict) -> str:
        """保存时的 http 明文告警（PRD D2 / D1）

        返回 `"ok"`（继续保存）/ `"abort"`（用户去改地址了）。
        判定用**生效基址**（`remote_ops.insecure_reason`）：隧道激活时是 127.0.0.1，
        本身已经加密 → 不弹。勾「不再提示这个实例」= 立即把标记写进 new（跟着本次
        保存落盘），不阻断保存。
        """
        from app import remote_ops
        if bool(new.get("insecure_warn_muted")):
            return "ok"
        merged = dict(inst or {})
        for k in ("kind", "base_url", "tls_verify", "insecure_warn_muted"):
            if k in new:
                merged[k] = new[k]
        try:
            base = remote_ops.ops.insecure_reason(merged)
        except Exception:                   # noqa: BLE001 - 提示不能反过来阻断保存
            base = ""
        if not base:
            return "ok"
        name = str(new.get("name") or inst.get("name") or "这个实例")
        box = QMessageBox(self)
        box.setWindowTitle("ComfyHub")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"「{name}」用的是 http 明文连接（{base}）。")
        box.setInformativeText(
            "Token 和生成的图会以明文经过网络，同一网络里的人能看到。\n"
            "内网自用一般可以接受；跨公网或多人共用的网络，"
            "建议在远端加 HTTPS 反代，或用隧道。")
        ck = QCheckBox("不再提示这个实例")
        box.setCheckBox(ck)
        b_ok = box.addButton("我知道了", QMessageBox.AcceptRole)
        b_edit = box.addButton("改地址", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_edit:
            self.ed_url.setFocus()
            self.ed_url.selectAll()
            return "abort"
        if ck.isChecked():
            new["insecure_warn_muted"] = True
            # 立刻落盘：用户可能接着点「取消」这次保存，但「不再提示」的意愿是真的
            inst["insecure_warn_muted"] = True
            try:
                save_config(self.cfg)
            except Exception:               # noqa: BLE001
                pass
        if box.clickedButton() is b_ok:
            return "ok"
        return "ok"

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
        # 删实例前先收掉它的隧道与运行期基址覆盖（PRD C5 / 边界 #20）：
        # 留着的话「127.0.0.1:8189」会继续 listen，看起来像这个实例还活着。
        if clicked in (b_keep, b_del):
            self._drop_remote_resources(inst)
        if clicked is b_keep:
            self.mainwin.procs.get(inst).stop()
            self.cfg["instances"].remove(inst)
            save_config(self.cfg)
            self.mainwin._sync_instances()
            self.load_current()
        elif clicked is b_del:
            self._delete_files(inst)

    def _drop_remote_resources(self, inst: dict):
        """停掉该实例的隧道 + 清注册表覆盖（异常全吞：删除流程不能被它带崩）"""
        iid = str((inst or {}).get("id") or "")
        if not iid:
            return
        mgr = getattr(self.mainwin, "tunnel_manager", None)
        if mgr is not None:
            try:
                mgr.stop(iid)
            except Exception:               # noqa: BLE001
                pass
        try:
            from app import remote_registry
            remote_registry.registry.clear_override(iid)
        except Exception:                   # noqa: BLE001
            pass
        try:
            from app import remote_ssh
            remote_ssh.manager.release(iid)
        except Exception:                   # noqa: BLE001
            pass

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
        # 远程实例（0.8.40）：走 SSH / 云钩子启停，本机分支一个字节都不动
        if (inst.get("kind") or "local") == "remote":
            self._remote_start()
            return
        # ★ 启动前环境预检弹窗（2026-11-09）：CPU 版 torch + CUDA 引擎时
        #   ComfyUI import 阶段就崩（Torch not compiled with CUDA enabled），
        #   提前拦下给三条路：去修复 / 仍以 CPU 模式启动 / 取消。
        from app.process import env_block_reason
        why = env_block_reason(inst)
        if why:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("ComfyHub")
            box.setText("启动前检查未通过，已阻止启动")
            box.setInformativeText(why)
            btn_fix = box.addButton("去修复（推荐）", QMessageBox.AcceptRole)
            btn_cpu = box.addButton("仍以 CPU 模式启动", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_fix:
                self.mainwin.goto(NAV.ENV)   # 环境检测页的「修复」按钮重装 CUDA 版 torch
                return
            if clicked is btn_cpu:
                inst = dict(inst)            # 仅本次生效，不落盘
                inst["engine_backend"] = "cpu"
            else:
                return
        # device 字段由 build_command 统一处理（--cpu 等）
        self.mainwin.procs.get(inst).start(inst)
        self._update_state()
        self.mainwin.goto(NAV.CONSOLE)   # 启动后自动切到「控制台」看实时日志

    def stop(self):
        inst = self._current_inst()
        if not inst:
            return
        if (inst.get("kind") or "local") == "remote":
            self._remote_stop()
            return
        self.mainwin.procs.get(inst).stop()
        self._update_state()

    # ---------- 远程启停（2026-12-01，0.8.40 T05）----------
    def _on_hook_request(self, action: str, inst: dict):
        """页签「发开机/关机请求」：与「远程启动」走同一条链路（PRD 5.2.1）"""
        if str(action) == "stop":
            self._run_ops(dict(inst or {}), "stop", "cloud")
            return
        self._run_ops(dict(inst or {}), "start", "cloud")

    def _remote_start(self, channel: str | None = None, inst: dict | None = None):
        """远程启动：通道预检 → 进度对话框 → RemoteOpsWorker

        无通道时**不弹失败框**，直接写一行控制台并跳到「🛠 远程运维」页签
        （PRD 5.1.2 流程图 B1「提示：还没配 SSH / 自动跳到远程运维页签」）。
        """
        from app import remote_ops as ro
        inst = dict(inst or self._current_inst() or {})
        if not inst:
            return
        chans = ro.ops.available_channels(inst, action="start")
        if not chans:
            self.mainwin.console_page.append_line(
                "[ComfyHub] 还没配远程启动方式。到「ComfyUI → 🛠 远程运维」"
                "填 SSH 或云平台开关机接口")
            try:
                self.tabs.setCurrentIndex(3)
            except Exception:
                pass
            return
        chan = str(channel or "").lower()
        if chan not in chans:
            chan = str(inst.get("last_ops_channel") or "").lower()
        if chan not in chans:
            chan = chans[0]
        self._run_ops(inst, "start", chan)

    def _remote_stop(self):
        """远程停止：先打断正在等待的启动（PRD 边界 #5），再下发停止"""
        from app import remote_ops as ro
        inst = dict(self._current_inst() or {})
        if not inst:
            return
        chans = ro.ops.available_channels(inst, action="stop")
        if not chans:
            self.mainwin.console_page.append_line(
                "[ComfyHub] 还没配远程停止方式。到「ComfyUI → 🛠 远程运维」"
                "填 SSH 停止命令或云平台关机接口")
            try:
                self.tabs.setCurrentIndex(3)
            except Exception:
                pass
            return
        chan = str(inst.get("last_ops_channel") or "").lower()
        if chan not in chans:
            chan = chans[0]
        # 启动等待中 → 先中止轮询（只中止，不杀远端），stop worker 会对 busy 短暂重试
        w = getattr(self, "_ops_worker", None)
        if w is not None and w.isRunning():
            try:
                w.cancel()
            except Exception:
                pass
        self._run_ops(inst, "stop", chan)

    def _run_ops(self, inst: dict, action: str, channel: str):
        """起一次远程启停任务：worker 归本页持有，对话框只负责显示"""
        from app import remote_ops as ro
        from app.ui.remote_start_dialog import RemoteStartDialog
        verb = "远程停止" if action == "stop" else "远程启动"
        prefix = ro.log_prefix(verb, inst)
        dlg = None
        if action == "start":
            dlg = RemoteStartDialog(self, inst, action, channel)
            self._ops_dialog = dlg
        w = RemoteOpsWorker(inst, action, channel)
        w.sig_log.connect(lambda line, _p=prefix: self._ops_log(_p, line))
        w.sig_state.connect(lambda text, _d=dlg: self._ops_state(text, _d))
        w.sig_done.connect(lambda payload, _d=dlg, _p=prefix, _i=inst:
                           self._ops_done(payload, _d, _p, _i))
        if dlg is not None:
            dlg.sig_cancel_wait.connect(w.cancel)
            dlg.show()
        swap_worker(self, "_ops_worker", w)
        w.start()
        self._update_state()

    def _ops_log(self, prefix: str, line: str):
        text = str(line or "")
        if not text:
            return
        try:
            self.mainwin.console_page.append_line(f"{prefix} {text}")
        except Exception:                   # noqa: BLE001 - 日志写不进去也不能中断任务
            pass
        dlg = getattr(self, "_ops_dialog", None)
        if dlg is not None:
            try:
                dlg.append_log(text)
            except Exception:
                pass

    def _ops_state(self, text: str, dlg=None):
        if dlg is not None:
            try:
                dlg.set_state(str(text or ""))
            except Exception:
                pass
        self._update_state()

    def _ops_done(self, payload: dict, dlg=None, prefix: str = "", inst=None):
        ok = bool((payload or {}).get("ok"))
        text = self._ops_result_text(payload or {})
        if dlg is not None:
            try:
                dlg.finish(ok, text.splitlines()[0] if text else "")
            except Exception:
                pass
        if prefix:
            try:
                self.mainwin.console_page.append_line(f"{prefix} {text.splitlines()[0]}")
            except Exception:
                pass
        self._update_state()
        name = str((inst or {}).get("name") or (payload or {}).get("iid") or "实例")
        try:
            from app.ui.toast import show_toast
            if ok:
                # D-02：本来就在跑时提示「已在运行」，与结果行文案一致
                _pre = bool((payload or {}).get("pre_ready"))
                show_toast(self.window(),
                           f"{name} " + (T("已在运行") if _pre else T("已就绪")))
            else:
                show_toast(self.window(), f"{name}：{text.splitlines()[0]}",
                           level="error")
        except Exception:                   # noqa: BLE001 - toast 失败不影响结果
            pass

    def _ops_result_text(self, payload: dict) -> str:
        """结果 dict → 中文原因（只按 err_kind 查，绝不匹配错误字符串）"""
        from app import cloud_hooks
        from app import remote_ops as ro
        if payload.get("ok"):
            # D-02：预探针就绪（启动前实例本来就在跑）不能报「启动成功」，
            # 否则用户会以为是自己刚点的那次启动生效了。
            # 注意措辞：命令/请求**确实发出去了**（`_probe` 之后仍照常执行），
            # 所以不能写「未执行启动命令」——那反而是假话。
            if payload.get("pre_ready"):
                return "✅ 实例本来就在运行（启动命令已执行，未计入本次启动）"
            return "✅ 已就绪"
        kind = str(payload.get("err_kind") or "")
        detail = str(payload.get("detail") or "")
        lines = list(payload.get("last_lines") or [])
        if kind == ro.ERR_CANCELLED or payload.get("cancelled"):
            return "已停止等待（远端进程未受影响，仍在继续启动）"
        if kind == ro.ERR_BUSY:
            return "该实例已有一次远程操作在进行，等它结束再试"
        if kind == ro.ERR_NO_CHANNEL:
            return ("这个实例还没配好远程启动方式。到「ComfyUI → 🛠 远程运维」"
                    "填 SSH 或云平台开关机接口")
        if kind == ro.ERR_CMD:
            return ("命令执行失败（退出码 "
                    f"{payload.get('code')}）。\n"
                    f"多半是启动命令里的路径不对：{detail[:200]}\n"
                    "到「远程运维」页签核对 ComfyUI 目录与 python 解释器路径，"
                    "改完再点「远程启动」。")
        if kind == ro.ERR_SUDO:
            return ("远端命令需要交互式 sudo 密码，ComfyHub 不做交互式提权。\n"
                    "把命令改成不需要 sudo 的形式（例如用有权限的目录与端口）。")
        if kind == ro.ERR_NOT_READY:
            tail = "\n".join(str(x) for x in lines[-20:]) or "（没有输出）"
            label = ("平台已受理开机" if str(payload.get("channel")) == "cloud"
                     else "命令已执行")
            return (f"{label}，但等满 5 分钟仍没响应。\n"
                    "常见原因：ComfyUI 还在加载模型（首次启动更久）；"
                    "远端监听的地址/端口和这里填的不一致；"
                    "云平台安全组没放行该端口。\n"
                    f"远端输出最后 20 行：\n{tail}")
        if kind == ro.ERR_STILL_REACHABLE:
            return ("命令返回成功，但实例仍然可以访问，停止命令可能没匹配到进程。\n"
                    "把停止命令改成能匹配到远端真实命令行"
                    "（例如 pkill -f \"ComfyUI/main.py\"），或到那台机器上手动停。")
        if kind in cloud_hooks.KINDS:
            # 云钩子失败：用 HookError 装一次，交给 cloud_hooks.describe 出逐字文案
            err = cloud_hooks.HookError(
                kind, detail, status=int(payload.get("code") or 0), body=detail)
            return cloud_hooks.describe(err)
        if kind:
            # SSH 认证/网络类：与「测试 SSH」用同一份逐字文案
            return self.remote_ops.ssh_failure_text(kind, detail)
        return "远程操作失败（原因未知）。可以到「控制台」页看详细日志。"

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
        # 远程实例（0.8.39）：直接开 base_url，没有「本机端口」这回事
        url = self.mainwin.procs.web_url(inst)
        if not url:
            self.mainwin.console_page.append_line(
                "[ComfyHub] 远程实例还没填访问地址，无法打开网页。")
            return
        if (inst.get("kind") or "local") == "remote":
            webbrowser.open(url)
            return
        port = self.mainwin.procs.actual_port(inst) or cfg
        if port != cfg:
            self.mainwin.console_page.append_line(
                f"[ComfyHub] 实际端口 {port}（配置 {cfg}），已按实际端口打开")
        webbrowser.open(f"http://127.0.0.1:{port}")
