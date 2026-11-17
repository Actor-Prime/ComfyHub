# -*- coding: utf-8 -*-
"""组件包卡片（模型页顶部，2026-11-15）

一条清单 = 一整套能跑起来的东西。卡片只做三件事：
    1) 按磁盘现状显示「已就绪 / 还需下 N GB」
    2) 一键下载缺的文件（后台线程，进度按 .part 实时算）
    3) 下载完把随包工作流放进实例 workflows 目录

状态不做显式枚举，每次 refresh() 重算（2026-11-08 的教训：先显示再刷新
的状态机很容易和磁盘对不上）。
"""
import os
import time

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from app import component_packs as cp
from app.i18n import T


class _InstallWorker(QThread):
    """下载整包（download_multi 是阻塞的，必须离开 UI 线程）"""

    sig_log = Signal(str)
    sig_progress = Signal(str, int, int)     # file, index, total
    sig_done = Signal(bool, str)

    def __init__(self, pack_id: str, comfyui_path: str, models_dir: str,
                 cancel=None, parent=None):
        super().__init__(parent)
        self._pid = pack_id
        self._cp = comfyui_path
        self._md = models_dir
        self._cancel = cancel

    def run(self):
        try:
            res = cp.install(self._pid, self._cp, self._md,
                             log=lambda m: self.sig_log.emit(str(m)),
                             on_progress=lambda d: self.sig_progress.emit(
                                 d["file"], d["index"], d["total"]),
                             cancel=self._cancel)
            ok = bool(res.get("ok"))
            msg = "" if ok else ("还有 %d 个文件没下完，进度已保留，重试会续传"
                                 % len(res.get("failed") or []))
            self.sig_done.emit(ok, msg)
        except Exception as e:                 # 线程里异常必须自己咽掉
            self.sig_done.emit(False, "%s: %s" % (type(e).__name__, e))


class ComponentCard(QFrame):
    """组件包一行卡：标题 + 清单摘要 + 一键下载"""

    def __init__(self, pack_id: str, inst_provider, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.pack_id = pack_id
        self._inst = inst_provider          # callable -> 当前实例 dict
        self._worker = None
        self._cancel = None
        self._part = ""                     # 正在下的 .part
        self._dest = ""
        self._want = 0
        self._last_bytes, self._last_ts = 0, 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._tick)
        self._build()
        self.refresh()

    # ---------- UI ----------
    def _build(self):
        pack = cp.get_pack(self.pack_id)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.lb_title = QLabel(T(pack.get("name") or self.pack_id))
        self.lb_title.setStyleSheet("font-size:14px; font-weight:bold; color:#e6edf7;")
        row.addWidget(self.lb_title)

        self.lb_badge = QLabel()
        self.lb_badge.setObjectName("BadgeInstalled")
        row.addWidget(self.lb_badge)
        row.addStretch()

        self.lb_state = QLabel()
        self.lb_state.setStyleSheet("color:#94a2b4;")
        row.addWidget(self.lb_state)

        self.b_main = QPushButton(T("一键下载"))
        self.b_main.setProperty("accent", True)
        self.b_main.clicked.connect(self._on_main)
        row.addWidget(self.b_main)

        self.b_open = QPushButton(T("打开目录"))
        self.b_open.clicked.connect(self._open_dir)
        row.addWidget(self.b_open)
        root.addLayout(row)

        desc = QLabel(T(pack.get("desc") or ""))
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#8a94a2; font-size:12px;")
        root.addWidget(desc)

        req = pack.get("requires") or ""
        if req:
            lb_req = QLabel(T("· " + req))
            lb_req.setStyleSheet("color:#6c7a8c; font-size:11px;")
            lb_req.setWordWrap(True)
            root.addWidget(lb_req)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        root.addWidget(self.bar)

        self.lb_log = QLabel("")
        self.lb_log.setStyleSheet("color:#7d8797; font-size:11px;")
        self.lb_log.hide()
        root.addWidget(self.lb_log)

    # ---------- 状态 ----------
    def _ctx(self):
        inst = self._inst() or {}
        return (inst.get("comfyui_path") or "", inst.get("models_dir") or "")

    def refresh(self):
        if self._worker is not None:
            return                       # 下载中别刷状态，免得把进度盖掉
        cpath, mdir = self._ctx()
        if not cpath:
            self.lb_state.setText(T("未选择实例"))
            self.b_main.setEnabled(False)
            return
        st = cp.state(self.pack_id, cpath, mdir)
        if not st:
            self.lb_state.setText(T("组件定义缺失"))
            self.b_main.setEnabled(False)
            return
        if st["ready"]:
            self.lb_badge.setText(T("已就绪"))
            self.lb_badge.setProperty("ok", True)
            self.lb_state.setText(T("%d/%d · 共 %s")
                                  % (st["done"], st["total"],
                                     cp.fmt_size(cp.pack_bytes(cp.get_pack(self.pack_id)))))
            self.b_main.setText(T("已就绪"))
            self.b_main.setEnabled(False)
        else:
            self.lb_badge.setText(T("待下载"))
            self.lb_badge.setProperty("ok", False)
            self.lb_state.setText(T("%d/%d · 还需 %s")
                                  % (st["done"], st["total"], st["need_text"]))
            self.b_main.setText(T("一键下载"))
            self.b_main.setEnabled(True)
        self.lb_badge.style().unpolish(self.lb_badge)
        self.lb_badge.style().polish(self.lb_badge)
        self.b_open.setEnabled(True)

    # ---------- 动作 ----------
    def _on_main(self):
        if self._worker is not None:
            return
        cpath, mdir = self._ctx()
        if not cpath:
            return
        import threading
        self._cancel = threading.Event()
        self.b_main.setText(T("下载中…"))
        self.b_main.setEnabled(False)
        self.bar.show()
        self.bar.setValue(0)
        self.lb_log.show()
        self.lb_log.setText(T("准备下载…"))
        self._worker = _InstallWorker(self.pack_id, cpath, mdir, self._cancel, self)
        self._worker.sig_log.connect(self._on_log)
        self._worker.sig_progress.connect(self._on_progress)
        self._worker.sig_done.connect(self._on_done)
        self._worker.start()
        self._timer.start()

    def _on_log(self, msg: str):
        """下载器日志里挑「↓ 文件名: 12.3/456.7 MB」这种进度行显示出来"""
        s = (msg or "").strip()
        if not s:
            return
        if s.startswith("↓") or s.startswith("下载完成") or s.startswith("[组件"):
            self.lb_log.setText(s)

    def _on_progress(self, fname: str, idx: int, total: int):
        self.lb_log.setText("[%d/%d] %s" % (idx, total, fname))
        cpath, mdir = self._ctx()
        pack = cp.get_pack(self.pack_id)
        item = next((i for i in pack["items"] if i["file"] == fname), {})
        self._dest = cp.item_path(cpath, mdir, item) if item else ""
        self._part = self._dest + ".part" if self._dest else ""
        self._want = int(item.get("size") or 0)
        self._last_bytes, self._last_ts = 0, 0.0

    def _tick(self):
        """按 .part 实际大小算单文件进度（下载器不回调字节数）"""
        try:
            if self._part and os.path.isfile(self._part) and self._want:
                got = os.path.getsize(self._part)
                self.bar.setValue(int(got * 1000 / self._want))
                now = time.time()
                if not self._last_ts:
                    self._last_ts, self._last_bytes = now, got
                elif now - self._last_ts >= 1.0:
                    sp = (got - self._last_bytes) / (now - self._last_ts)
                    self.lb_log.setText("%s  %.1f/%.1f GB  %s/s"
                                        % (os.path.basename(self._dest),
                                           got / 1073741824.0,
                                           self._want / 1073741824.0,
                                           cp.fmt_size(sp) if sp > 0 else "-"))
                    self._last_bytes, self._last_ts = got, now
        except Exception:
            pass

    def _on_done(self, ok: bool, msg: str):
        self._timer.stop()
        if self._worker is not None:
            self._worker.wait(2000)
            self._worker = None
        self.bar.hide()
        self.lb_log.setText(msg or T("下载完成"))
        if ok:
            self.lb_log.setText(T("下载完成，起步工作流已放入实例的 workflows 目录"))
        self.b_main.setEnabled(True)
        self.refresh()

    def _open_dir(self):
        cpath, mdir = self._ctx()
        d = cp.models_root(cpath, mdir)
        try:
            if d and os.path.isdir(d):
                os.startfile(d)          # noqa: S606 —— Windows 专用，打开资源管理器
        except Exception:
            pass


class ComponentBar(QFrame):
    """模型页顶部的组件区容器（目前只有 H3 一个包，按列表渲染方便以后加）"""

    def __init__(self, inst_provider, parent=None):
        super().__init__(parent)
        self.setObjectName("CompBar")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.cards = []
        for p in cp.list_packs():
            c = ComponentCard(p["id"], inst_provider, self)
            v.addWidget(c)
            self.cards.append(c)

    def refresh(self):
        for c in self.cards:
            c.refresh()
