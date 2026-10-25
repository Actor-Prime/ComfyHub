# -*- coding: utf-8 -*-
"""工作台首页：Banner + 快捷入口 + 目录网格（对标管理大师工作台）"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QFrame, QPushButton,
)

from app import sysinfo
from app.ui.nav import NAV

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "assets")


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
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(14)

        # ---- Banner ----
        banner = QLabel()
        banner.setObjectName("Banner")
        banner.setFixedHeight(190)
        banner.setScaledContents(True)
        bp = os.path.join(ASSETS, "banner.jpg")
        if os.path.isfile(bp):
            banner.setPixmap(QPixmap(bp))
        else:
            banner.setStyleSheet("background:#20252e;")
        lay = QVBoxLayout(banner)
        lay.setContentsMargins(24, 20, 24, 16)
        t1 = QLabel("ComfyHub")
        t1.setStyleSheet("color:#fff; font-size:26px; font-weight:bold; background:transparent;")
        t2 = QLabel("轻量 ComfyUI 多实例管理器 · 一键装环境 · 工作流智能安装")
        t2.setStyleSheet("color:#dfe6ee; font-size:13px; background:transparent;")
        lay.addWidget(t1)
        lay.addWidget(t2)
        lay.addStretch()
        root.addWidget(banner)

        # ---- 状态卡 ----
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_ram = self._card()
        self.card_cpu = self._card()
        self.card_gpu = self._card()
        self.card_disk = self._card()
        for c in (self.card_ram, self.card_cpu, self.card_gpu, self.card_disk):
            cards.addWidget(c)
        root.addLayout(cards)

        # ---- 实例状态行 ----
        self.lb_inst = QLabel("当前实例: （未选择）")
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        root.addWidget(self.lb_inst)

        # ---- 目录网格 ----
        lb = QLabel("实例目录")
        lb.setObjectName("SectionLabel")
        root.addWidget(lb)
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
        root.addLayout(grid)

        # ---- 右下角操作按钮 ----
        root.addStretch()
        bar = QHBoxLayout()
        bar.addStretch()
        self.b_start = QPushButton("▶  启动")
        self.b_start.setProperty("success", True)
        self.b_start.setFixedSize(120, 42)
        self.b_start.clicked.connect(lambda: self._act("start"))
        self.b_stop = QPushButton("■  停止")
        self.b_stop.setFixedSize(100, 42)
        self.b_stop.clicked.connect(lambda: self._act("stop"))
        self.b_web = QPushButton("🌐  打开网页")
        self.b_web.setFixedSize(130, 42)
        self.b_web.clicked.connect(lambda: self._act("web"))
        self.b_edit = QPushButton("⚙  管理此实例")
        self.b_edit.setFixedSize(130, 42)
        self.b_edit.clicked.connect(lambda: self.mainwin.goto(NAV.COMFYUI))
        bar.addWidget(self.b_start)
        bar.addWidget(self.b_stop)
        bar.addWidget(self.b_web)
        bar.addWidget(self.b_edit)
        root.addLayout(bar)
        root.addStretch()

    def _card(self):
        w = QLabel()
        w.setObjectName("Card")
        w.setMinimumSize(230, 78)
        w.setTextFormat(Qt.RichText)
        return w

    def _set_card(self, w, title, value, sub=""):
        w.setText(
            f"<div style='padding:9px 12px;'>"
            f"<span style='color:#9aa4b2; font-size:11px;'>{title}</span><br>"
            f"<span style='color:#fff; font-size:15px; font-weight:bold;'>{value}</span>"
            + (f"<br><span style='color:#7d8797; font-size:11px;'>{sub}</span>" if sub else "")
            + "</div>")

    def _dir_card(self, icon, name, sub):
        f = QFrame()
        f.setObjectName("RowCard")
        f.setCursor(Qt.PointingHandCursor)
        f.setFixedHeight(64)
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 10, 14, 10)
        t = QLabel(f"{icon}  {name}")
        t.setStyleSheet("color:#d8dee6; font-size:13px; font-weight:bold; background:transparent;")
        d = QLabel(sub or "实例根目录")
        d.setStyleSheet("color:#7d8797; font-size:11px; background:transparent;")
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
        self.b_web.setEnabled(bool(inst))     # 无实例时禁用
        self.b_edit.setEnabled(bool(inst))
        self.lb_inst.setText(
            f"当前实例: {inst.get('name') if inst else '（未选择）'}"
            + ("   ● 运行中" if running else ""))

    def on_show(self):
        self._refresh()
        self._refresh_state()
        self._refresh_dir_names()

    def on_instance_changed(self):
        self._refresh_dir_names()
        self._refresh_state()   # 同步启停按钮可用态与实例名

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
                       f"占用 {r['percent']:.0f}%")
        c = sysinfo.cpu_info()
        self._set_card(self.card_cpu, "处理器",
                       f"{c['cores_physical']}核/{c['cores_logical']}线程",
                       f"占用 {c['usage']:.0f}%")
        g = sysinfo.gpu_info_display()
        # 拆出主名 / 显存 / 驱动 → 精简显示，避免副标题重复冗长
        try:
            from app.bootstrap import detect_gpu
            gpu = detect_gpu()
            if gpu.vendor == "nvidia":
                gb = f"{gpu.vram_mb / 1024:.0f}GB" if gpu.vram_mb else "显存未知"
                self._set_card(self.card_gpu, "显卡",
                               f"{gpu.name} · {gb}",
                               f"驱动 {gpu.driver} → 推荐 torch {gpu.torch_variant}")
            else:
                self._set_card(self.card_gpu, "显卡", gpu.name or "未检测到", g)
        except Exception:
            self._set_card(self.card_gpu, "显卡", g.split("（")[0], g)
        d = sysinfo.disk_info("F:\\")
        self._set_card(self.card_disk, "F 盘空间",
                       f"{d['free_gb']:.0f} GB 可用",
                       f"共 {d['total_gb']:.0f} GB")

    def _refresh_live(self):
        r = sysinfo.ram_info()
        self._set_card(self.card_ram, "内存",
                       f"{r['used_gb']:.1f} / {r['total_gb']:.0f} GB",
                       f"占用 {r['percent']:.0f}%")
        c = sysinfo.cpu_info()
        self._set_card(self.card_cpu, "处理器",
                       f"{c['cores_physical']}核/{c['cores_logical']}线程",
                       f"占用 {c['usage']:.0f}%")
