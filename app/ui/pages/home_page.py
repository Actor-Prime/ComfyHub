# -*- coding: utf-8 -*-
"""工作台首页：Banner + 快捷入口 + 目录网格（对标管理大师工作台）"""
import os
import sys

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QFrame, QPushButton, QProgressBar, QScrollArea, QSizePolicy,
)

from app import sysinfo
from app.i18n import T
from app.ui.nav import NAV

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "assets")


class _ElidedLabel(QLabel):
    """超宽自动省略的 QLabel

    QLabel 原生不做 elide：带长文本时要么把整个布局撑宽、要么被硬裁成半截字。
    状态卡里的「NVIDIA GeForce RTX 4070 Ti · 12GB」就是典型 —— 窗口窄到 1120 时
    放不下，会把第 4 张卡顶出可视区（2026-11-08 报障）。

    做法（关键细节）：
      · sizeHint 仍按**完整文本**给宽 —— 布局空间够时照旧完整显示（和原来一样）；
      · minimumSizeHint 压到 0 —— 空间不够时允许被压缩，而不是撑破布局；
      · 压缩后 resizeEvent 里画省略号。
    注意不能图省事设 QSizePolicy.Ignored：那等于彻底放弃宽度需求，1600 宽下
    也会被平均分配挤成 "NVIDIA GeForc…"（实测踩过）。
    """
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text or ""
        self.setToolTip(self._full)
        super().setText(self._full)

    def sizeHint(self):                      # noqa: N802
        # +8 给布局留余量（卡片内边距有取整误差，不留余量时「25.0 / 64 GB」
        # 会永远差 1~2px 被画成「25.0 / 64…」）
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full) + 8,
                     super().sizeHint().height())

    def minimumSizeHint(self):               # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text):                 # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._apply()

    def _apply(self):
        fm = self.fontMetrics()
        need = fm.horizontalAdvance(self._full)
        w = self.width() - 2
        if w <= 0:
            super().setText(self._full)
            return
        # 放得下就原样显示，只有真的超宽才画省略号
        super().setText(self._full if w >= need
                        else fm.elidedText(self._full, Qt.ElideRight, w))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()


class _Banner(QWidget):
    """横幅：图片按圆角裁剪后自绘

    Qt 用 QLabel + setPixmap + QSS `border-radius` 时，**边框圆了、图还是直角** ——
    四个角会露出图片方角（2026-11-08 用户报障「边框是圆角了，图片没跟着圆角」）。
    这里用 QPainterPath 做 clip 再 drawPixmap，图片与圆角严格一致。
    """
    RADIUS = 14

    def __init__(self, img_path="", parent=None):
        super().__init__(parent)
        self.setObjectName("BannerBox")
        self._pix = QPixmap(img_path) if (img_path and os.path.isfile(img_path)) else QPixmap()

    def sizeHint(self):                     # noqa: N802
        # 显式给高度：QWidget 默认 sizeHint 是 (-1,-1)，布局会退到 minimumSize，
        # banner 就被压成一条细带、和状态卡重叠（2026-11-08 实测踩过）
        return QSize(640, 240)

    def paintEvent(self, e):
        from PySide6.QtGui import (QPainter, QPainterPath, QColor, QPen)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r = self.rect()
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(r.width()), float(r.height()),
                            float(self.RADIUS), float(self.RADIUS))
        p.setClipPath(path)
        if self._pix.isNull():
            p.fillPath(path, QColor("#1b2029"))
        else:
            # 按「cover」缩放后居中裁剪，而不是直接拉伸 —— 内容列宽度会随窗口变，
            # 直接拉伸会让 1024x340 的原图在不同宽度下被压扁/拉长
            from PySide6.QtCore import QRect
            scaled = self._pix.scaled(r.size(), Qt.KeepAspectRatioByExpanding,
                                      Qt.SmoothTransformation)
            sx = max(0, (scaled.width() - r.width()) // 2)
            sy = max(0, (scaled.height() - r.height()) // 2)
            p.drawPixmap(r, scaled,
                         QRect(sx, sy, r.width(), r.height()))
        p.setClipping(False)
        p.setPen(QPen(QColor(52, 62, 77), 1))   # 描边跟卡片同色 (#343e4d)
        p.drawPath(path)
        p.end()


class _Scrim(QFrame):
    """横幅左侧的「玻璃底」：圆角 + 左深右透的横向渐变（自绘）

    为什么不用 QSS 的 `qlineargradient` + `border-radius`：Qt 的 QSS 渐变**不按
    border-radius 裁剪**，圆角四个角会漏出直角亮边，看起来就是"圆角画错了"
    （2026-11-08 用户报障）。用 QPainterPath 先把形状定好再填渐变，圆角与描边
    都是精确的。
    """
    RADIUS = 12

    def paintEvent(self, e):
        from PySide6.QtGui import (QPainter, QPainterPath, QLinearGradient,
                                   QColor, QPen)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(float(r.x()), float(r.y()),
                            float(r.width()), float(r.height()),
                            float(self.RADIUS), float(self.RADIUS))
        g = QLinearGradient(float(r.left()), 0.0, float(r.right()), 0.0)
        g.setColorAt(0.0, QColor(8, 11, 16, 222))
        g.setColorAt(0.72, QColor(8, 11, 16, 145))
        g.setColorAt(1.0, QColor(8, 11, 16, 16))
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawPath(path)
        p.end()


class HomePage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._build()
        self._refresh()
        self._t = QTimer(self)
        self._t.timeout.connect(self._refresh_live)
        self._t.start(3000)

    # ---------- UI ----------
    def _build(self):
        # 2026-11-08 第 2 版（用户要求「能拉大拉小，但别一拉就散」）：
        #   · 整页套 QScrollArea —— 原来没有滚动区，1024×680 下内容被强行压缩，
        #     6 张目录卡上下重叠成一坨（实测）。
        #   · 内容列上限 1280 居中 —— 窗口再宽也不会把 banner/状态卡拉成细长横条。
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        # 工作台是卡片/图表型页面，内容列 1600（第 3 版）：2560 全屏下 1280 只占 50%，
        # 状态卡和目录卡被压成窄条、四周大片空白
        body.setMaximumWidth(2280)
        root = QVBoxLayout(body)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(16)

        # ---- Banner ----
        # 自绘控件（见 _Banner）：QLabel + pixmap + QSS 圆角只圆边框、不裁图片
        banner = _Banner(os.path.join(ASSETS, "banner.jpg"))
        # 高度弹性 150~240：内容列放宽到 1600 后 200 高太扁，理想值是 240；
        # 但固定 240 会让窗口稍矮就撑出整页滚动条（用户报「这边不该有滚动条」）。
        # 改成垂直 Ignored + min/max，空间不够时先压 banner，内容不再溢出。
        banner.setMinimumHeight(160)
        banner.setMaximumHeight(240)
        banner.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        banner.setMinimumWidth(0)
        lay = QVBoxLayout(banner)
        lay.setContentsMargins(30, 24, 30, 24)
        # 文字压在照片上可读性差（用户报障"看着糊"）→ 垫一层左侧渐隐的玻璃底
        scrim = _Scrim()
        scrim.setObjectName("BannerScrim")
        scrim.setMaximumWidth(660)
        sv = QVBoxLayout(scrim)
        sv.setContentsMargins(22, 16, 24, 18)
        sv.setSpacing(6)
        t1 = QLabel("ComfyHub")
        t1.setStyleSheet("color:#fff; font-size:30px; font-weight:bold; "
                         "letter-spacing:0.5px; background:transparent;")
        t2 = QLabel("轻量 ComfyUI 多实例管理器 · 一键装环境 · 工作流智能安装")
        t2.setStyleSheet("color:#cfdbea; font-size:14px; background:transparent;")
        # 不折行：wordWrap 会让 QLabel 的 sizeHint 飘忽，实测被压成两行、
        # 底行贴着框边被裁（用户报障"文字显示不全"）。单行 + 框宽自适应更稳。
        t2.setWordWrap(False)
        sv.addWidget(t1)
        sv.addWidget(t2)
        # 上下各一段留白 → 玻璃底在横幅里垂直居中（原来只有下方 addStretch，
        # 文字块被顶到偏上，看着像"贴着头"）
        lay.addStretch(1)
        lay.addWidget(scrim, 0, Qt.AlignLeft)
        lay.addStretch(1)
        root.addWidget(banner)

        # ---- 状态卡：固定一行 4 张（用户 2026-11-08 定：折 2×2 太占高度，就一列）----
        # 窗口变窄时不折行，靠 _ElidedLabel 画省略号顶住，4 张始终并排。
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_ram = self._card()
        self.card_cpu = self._card()
        self.card_gpu = self._card()
        self.card_disk = self._card()
        for c in (self.card_ram, self.card_cpu, self.card_gpu, self.card_disk):
            # 不给 stretch：卡片按内容宽度分配，显卡那张自然宽一点（原来是这种
            # 排法，能完整显示「NVIDIA GeForce RTX 4070 Ti · 12GB」）。等分 stretch
            # 会把它压到 224px 直接省略（2026-11-08 实测）。
            cards.addWidget(c)
        root.addLayout(cards)

        # ---- 实例区（2026-11-07：外面套一层圆角描边框，把实例信息与目录网格收在一起）----
        box = QFrame()
        box.setObjectName("SectionBox")
        box.setAttribute(Qt.WA_StyledBackground, True)
        bx = QVBoxLayout(box)
        bx.setContentsMargins(14, 10, 14, 14)
        bx.setSpacing(6)

        self.lb_inst = QLabel("当前实例: （未选择）")
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        bx.addWidget(self.lb_inst)

        lb = QLabel("实例目录")
        lb.setObjectName("SectionLabel")
        bx.addWidget(lb)
        grid = QGridLayout()
        grid.setSpacing(10)
        self._dir_cards = []
        entries = [
            ("🗂️", "根目录", ""),
            ("🧠", "模型 models", "models"),
            ("🧩", "插件 custom_nodes", "custom_nodes"),
            ("📥", "输入 input", "input"),
            ("📤", "输出 output", "output"),
            ("🧬", "工作流 workflows", os.path.join("user", "default", "workflows")),
        ]
        for i, (icon, name, sub) in enumerate(entries):
            card = self._dir_card(icon, name, sub)
            grid.addWidget(card, i // 3, i % 3)
        bx.addLayout(grid)
        root.addWidget(box)

        # 说明：原「快捷操作」四宫格已删除（2026-11-08 用户决议）——
        # 启动/输出目录/控制台/添加 ComfyUI 这 4 个入口在工作台上本来就点得到
        # （底部操作条、实例目录卡、左侧导航、顶栏 + 添加），重复摆一排反而占位置。

        # ---- 底部操作条（2026-11-08：改成有底板的工具条，别再让按钮裸飘在空白里）----
        # 第 2 版：删掉四宫格后这里原来留了一大片空白（用户报障「一拉就出现很大空白」），
        # 改成操作条紧跟实例目录（间距 16），多余高度全部落到页面底部。
        root.addSpacing(16)
        bar_host = QFrame()
        bar_host.setObjectName("ActionBar")
        bar_host.setAttribute(Qt.WA_StyledBackground, True)
        bar_host.setFixedHeight(66)
        bar = QHBoxLayout(bar_host)
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(10)
        self.lb_bar = QLabel("")
        bar.addWidget(self.lb_bar)
        bar.addStretch()
        self.b_start = QPushButton("▶  启动")
        self.b_start.setProperty("success", True)
        self.b_start.setFixedHeight(38)
        self.b_start.setMinimumWidth(112)
        self.b_start.clicked.connect(lambda: self._act("start"))
        self.b_stop = QPushButton("■  停止")
        self.b_stop.setFixedHeight(38)
        self.b_stop.setMinimumWidth(96)
        self.b_stop.clicked.connect(lambda: self._act("stop"))
        self.b_web = QPushButton("🌐  打开网页")
        self.b_web.setFixedHeight(38)
        self.b_web.setMinimumWidth(124)
        self.b_web.clicked.connect(lambda: self._act("web"))
        self.b_edit = QPushButton("⚙  管理此实例")
        self.b_edit.setFixedHeight(38)
        self.b_edit.setMinimumWidth(130)
        self.b_edit.clicked.connect(lambda: self.mainwin.goto(NAV.COMFYUI))
        bar.addWidget(self.b_start)
        bar.addWidget(self.b_stop)
        bar.addWidget(self.b_web)
        bar.addWidget(self.b_edit)
        # 余量收在 body 内部：操作条不再参与滚动
        root.addStretch(1)

        scroll.setWidget(body)
        scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        wrap.addWidget(scroll, 1)

        # ---- 底部操作条：固定贴窗口底，不进滚动区 ----
        # 2026-11-08 用户要求「操作条应该跟窗口最下面」+「这边不该有进度条」。
        # 原来操作条在 body 末尾、随内容一起滚 —— 内容稍微超高整页就出滚动条，
        # 而且操作条被顶在内容下面、离窗口底还有一截。现在拆成：
        #   可滚动内容区（banner/状态卡/目录卡） + 固定底部操作条
        bar_row = QWidget()
        bh = QHBoxLayout(bar_row)
        bh.setContentsMargins(22, 0, 22, 18)
        bh.setSpacing(0)
        bar_host.setMaximumWidth(2280)
        bh.addStretch(1)
        bh.addWidget(bar_host, 50)
        bh.addStretch(1)
        wrap.addWidget(bar_row)

    def _open_dir(self, sub):
        inst = self.mainwin.current_inst()
        if not inst:
            return
        target = os.path.join(inst["comfyui_path"], sub)
        if os.path.isdir(target):
            os.startfile(target)

    def _card(self):
        """状态卡（2026-11-08 重做）：图标胶囊 + 标题 / 大号数值 / 副标题 / 占用条

        原来是一整个富文本 QLabel：字小、数值不突出、卡片拉到 600px 宽时
        右侧全是空，看着像"没设计"。改成结构化控件后数值有字号对比、
        底部有占用条，宽屏下也不空。
        """
        f = QFrame()
        f.setObjectName("StatCard")
        f.setAttribute(Qt.WA_StyledBackground, True)
        f.setMinimumHeight(92)
        h = QHBoxLayout(f)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)

        icon = QLabel("•")
        icon.setObjectName("StatIcon")
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignCenter)
        h.addWidget(icon, 0, Qt.AlignTop)

        v = QVBoxLayout()
        v.setSpacing(2)
        title = QLabel("")
        title.setObjectName("StatTitle")
        value = _ElidedLabel("")      # 超长自动省略（显卡型号在窄窗口下必用上）
        value.setObjectName("StatValue")
        sub = QLabel("")
        sub.setObjectName("StatSub")
        bar = QProgressBar()
        bar.setObjectName("StatBar")
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        bar.setVisible(False)
        v.addWidget(title)
        v.addWidget(value)
        v.addWidget(sub)
        v.addSpacing(2)
        v.addWidget(bar)
        h.addLayout(v, 1)

        f._icon, f._title, f._value, f._sub, f._bar = icon, title, value, sub, bar
        return f

    def _set_card(self, w, title, value, sub="", icon=None, pct=None):
        if icon is not None:
            w._icon.setText(icon)
        w._title.setText(T(title))
        w._value.setText(value)
        w._sub.setText(sub)
        w._sub.setVisible(bool(sub))
        if pct is None:
            w._bar.setVisible(False)
        else:
            p = max(0, min(100, int(round(pct))))
            w._bar.setVisible(True)
            w._bar.setValue(p)
            w._bar.setProperty("warn", p >= 80)
            w._bar.setProperty("full", p >= 92)
            # 动态属性改了要重 polish，否则 QSS 里的 [warn] 分支不生效
            w._bar.style().unpolish(w._bar)
            w._bar.style().polish(w._bar)

    def _dir_card(self, icon, name, sub):
        f = QFrame()
        f.setObjectName("RowCard")
        # QSS 的 background/border-radius 要 WA_StyledBackground 才会绘制，
        # 否则只剩 QFrame 自绘的直边 → 「圆角缺失、像缺了边框」（2026-11-12 用户报障）
        f.setAttribute(Qt.WA_StyledBackground, True)
        f.setCursor(Qt.PointingHandCursor)
        f.setFixedHeight(72)
        v = QVBoxLayout(f)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(3)
        t = QLabel(f"{icon}  {name}")
        t.setStyleSheet("color:#e6edf7; font-size:13px; font-weight:bold; background:transparent;")
        d = QLabel(sub or "实例根目录")
        d.setStyleSheet("color:#7f8b9c; font-size:11px; background:transparent;")
        v.addWidget(t)
        v.addWidget(d)
        subpath = sub

        def _open():
            inst = self.mainwin.current_inst()
            if not inst:
                return
            target = os.path.join(inst["comfyui_path"], subpath) if subpath \
                else inst["comfyui_path"]
            if os.path.isdir(target):
                os.startfile(target)
        f.mousePressEvent = lambda e: _open()
        self._dir_cards.append((f, subpath, d))
        return f

    # ---------- 数据 ----------
    def _act(self, what):
        page = self.mainwin.comfyui_page
        if what == "start":
            page.start()
        elif what == "stop":
            page.stop()
        elif what == "web":
            page.open_browser()
        self._refresh_state()

    def _refresh_state(self):
        inst = self.mainwin.current_inst()
        running = bool(inst and self.mainwin.procs.is_running(inst))
        self.b_start.setEnabled(bool(inst) and not running)
        self.b_stop.setEnabled(running)
        self.b_web.setEnabled(running)     # 未启动不能打开网页（实际端口逻辑在 open_browser）
        self.b_edit.setEnabled(bool(inst))
        self.lb_inst.setText(
            f"{T('当前实例')}: {inst.get('name') if inst else T('（未选择）')}"
            + ("   ● 运行中" if running else ""))
        # 操作条左侧同步显示状态，按钮不再裸飘
        if hasattr(self, "lb_bar"):
            self.lb_bar.setText(
                ("● " + T("运行中") + "  ·  " if running else "○ " + T("未运行") + "  ·  ")
                + (inst.get("name") if inst else T("（未选择）")))

    def on_show(self):
        self._refresh()
        self._refresh_state()
        self._refresh_dir_names()

    def on_instance_changed(self):
        self._refresh_dir_names()
        self._refresh_state()   # 同步启停按钮可用态与实例名
        self._refresh_disk_card()   # 磁盘卡随实例所在盘走

    def _refresh_dir_names(self):
        inst = self.mainwin.current_inst()
        for _f, sub, d_lb in self._dir_cards:
            hint = ("（未选择实例）" if not inst
                    else (sub or "实例根目录"))
            d_lb.setText(hint)

    def _refresh(self):
        r = sysinfo.ram_info()
        self._set_card(self.card_ram, "内存",
                       f"{r['used_gb']:.1f} / {r['total_gb']:.0f} GB",
                       f"{T('占用')} {r['percent']:.0f}%",
                       icon="🧠", pct=r['percent'])
        c = sysinfo.cpu_info()
        self._set_card(self.card_cpu, "处理器",
                       f"{c['cores_physical']}{T('核')}/{c['cores_logical']}{T('线程')}",
                       f"{T('占用')} {c['usage']:.0f}%",
                       icon="⚙", pct=c['usage'])
        g = sysinfo.gpu_info_display()
        self._refresh_gpu_card(g)
        self._refresh_disk_card()

    # ---- 显卡 / 磁盘卡（2026-11-10 用户要求：显卡下面显示显存、磁盘显示实例所在盘）----
    def _refresh_gpu_card(self, fallback_label=""):
        """显卡卡：大字=型号，副标题=显存实时占用（对齐内存/CPU 的数值+占用%模式）。
        驱动/torch 推荐信息降级为 tooltip，不占卡面。"""
        try:
            from app.bootstrap import detect_gpu
            gpu = detect_gpu()
        except Exception:
            self._set_card(self.card_gpu, "显卡", fallback_label.split("（")[0],
                           fallback_label, icon="🎮")
            return
        if gpu.vendor == "nvidia":
            v = sysinfo.gpu_vram_usage()
            if v:
                self._set_card(self.card_gpu, "显卡", gpu.name,
                               f"{T('显存')} {v['used_mb'] / 1024:.1f} / "
                               f"{v['total_mb'] / 1024:.0f} GB · {T('占用')} {v['percent']:.0f}%",
                               icon="🎮", pct=v['percent'])
            else:
                gb = f"{gpu.vram_mb / 1024:.0f} GB" if gpu.vram_mb else T("显存未知")
                self._set_card(self.card_gpu, "显卡", gpu.name,
                               f"{T('显存')} {gb}", icon="🎮")
            self.card_gpu.setToolTip(
                f"{T('驱动')} {gpu.driver} → {T('推荐')} torch {gpu.torch_variant}")
        else:
            self._set_card(self.card_gpu, "显卡", gpu.name or T("未检测到"),
                           fallback_label, icon="🎮")
            self.card_gpu.setToolTip("")

    def _refresh_disk_card(self):
        """磁盘卡：显示当前实例根目录所在盘的空间（2026-11-10 前硬编码 F:）。
        未选实例时回退到程序所在盘。切换实例时必须重刷（on_instance_changed 调 _refresh）。"""
        inst = self.mainwin.current_inst()
        root_path = (inst or {}).get("comfyui_path") or \
            os.path.dirname(os.path.abspath(sys.argv[0]))
        drive = os.path.splitdrive(os.path.abspath(root_path))[0] or "C:"
        d = sysinfo.disk_info(drive + "\\")
        used_pct = 0.0
        try:
            used_pct = (1 - d['free_gb'] / d['total_gb']) * 100 if d.get('total_gb') else 0
        except Exception:
            used_pct = 0
        self._set_card(self.card_disk, f"{drive} {T('盘空间')}",
                       f"{d['free_gb']:.0f} GB {T('可用')}",
                       f"{T('共')} {d['total_gb']:.0f} GB",
                       icon="💾", pct=used_pct)

    def _refresh_live(self):
        """只刷会变的（内存/CPU/显存占用）；磁盘容量是静态信息不用每秒重画"""
        r = sysinfo.ram_info()
        self._set_card(self.card_ram, "内存",
                       f"{r['used_gb']:.1f} / {r['total_gb']:.0f} GB",
                       f"{T('占用')} {r['percent']:.0f}%",
                       icon="🧠", pct=r['percent'])
        c = sysinfo.cpu_info()
        self._set_card(self.card_cpu, "处理器",
                       f"{c['cores_physical']}{T('核')}/{c['cores_logical']}{T('线程')}",
                       f"{T('占用')} {c['usage']:.0f}%",
                       icon="⚙", pct=c['usage'])
        # 显存占用是动态数据（sysinfo.gpu_vram_usage 内部 3s 节流，不会每秒 spawn nvidia-smi）
        self._refresh_gpu_card()
