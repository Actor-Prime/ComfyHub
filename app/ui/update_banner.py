# -*- coding: utf-8 -*-
"""update_banner.py - 版本更新提示条 + 更新对话框（2026-11-04）

两个组件：
  UpdateBanner  顶栏下方的一条非模态提示（立即更新 / 说明 / 跳过此版本 / ✕）
  UpdateDialog  下载与安装进度（复用 updater.apply_update，在子线程里跑）

设计取舍：
  · 提示条不打断操作——用户可能正在跑任务，绝不用模态框突然弹脸
  · 进度对话框中「下载完成 → 校验签名 → 应用」三阶段文案在同一个进度条上推进，
    因为对用户来说它们是一件事
  · 安装版/便携版的收尾不同（前者调安装器，后者写 updater.cmd），由 updater 内部决定，
    UI 只负责提示"即将重启完成更新"
"""
import threading
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout)

from app import updater


class UpdateBanner(QFrame):
    """顶栏下方的更新提示条"""
    sig_update = Signal()          # 用户请求立即更新
    sig_skip = Signal()            # 跳过此版本

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateBanner")
        self.setFixedHeight(38)
        self.setStyleSheet(
            "QFrame#UpdateBanner{background:rgba(47,111,178,0.16);"
            "border-bottom:1px solid rgba(55,138,221,0.45);}")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 0, 12, 0)
        h.setSpacing(10)
        self.lb = QLabel("")
        self.lb.setStyleSheet("color:#bcd4ea; font-size:12px; background:transparent;")
        h.addWidget(self.lb)
        h.addStretch(1)
        b_go = QPushButton("立即更新")
        b_go.setProperty("accent", True)
        b_go.setFixedHeight(26)
        b_go.clicked.connect(self.sig_update.emit)
        h.addWidget(b_go)
        b_note = QPushButton("说明")
        b_note.setFixedHeight(26)
        b_note.clicked.connect(self._show_notes)
        h.addWidget(b_note)
        b_skip = QPushButton("跳过此版本")
        b_skip.setFixedHeight(26)
        b_skip.clicked.connect(self.sig_skip.emit)
        h.addWidget(b_skip)
        b_close = QPushButton("✕")
        b_close.setFixedSize(24, 26)
        b_close.setStyleSheet("QPushButton{background:transparent;border:none;color:#93a3b8;}")
        b_close.clicked.connect(self.hide)
        h.addWidget(b_close)
        self._notes = ""
        self._latest = ""
        self.hide()

    def show_update(self, info: dict):
        self._notes = info.get("notes") or ""
        self._latest = info.get("latest") or ""
        size = ""
        self.lb.setText(f"发现新版本 {self._latest}（当前 {info.get('current')}）"
                        f"{size}"
                        + ("　·　该版本暂未提供%s包，可去发布页手动下载"
                           % ("便携" if info.get("portable") else "安装")
                           if info.get("missing_asset") else ""))
        self.show()

    def _show_notes(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"更新说明 · {self._latest}")
        v = QVBoxLayout(dlg)
        ed = QPlainTextEdit(self._notes or "（该版本没有填写说明）")
        ed.setReadOnly(True)
        v.addWidget(ed)
        b = QPushButton("关闭")
        b.clicked.connect(dlg.accept)
        v.addWidget(b, 0, Qt.AlignRight)
        dlg.resize(560, 420)
        dlg.exec()


class CheckWorker(QThread):
    """后台检查更新：Gitee 请求 1~3 秒，不能卡 UI"""
    sig_done = Signal(dict)

    def __init__(self, cfg, force: bool = False, parent=None):
        super().__init__(parent)
        self.cfg, self.force = cfg, force

    def run(self):
        try:
            info = updater.check(self.cfg, force=self.force)
        except Exception as e:
            info = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        updater.remember_check(self.cfg)
        self.sig_done.emit(info)


class _ApplyWorker(QThread):
    """下载 + 校验 + 应用（对 UI 只发信号）"""
    sig_progress = Signal(int, int, str)      # 已下载, 总字节, 阶段文案
    sig_done = Signal(dict)                   # updater.apply_update 的返回

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.cancelled = threading.Event()

    def run(self):
        try:
            res = updater.apply_update(
                self.info.get("asset_name") or "",
                self.info.get("asset_url") or "",
                progress=lambda d, t: self.sig_progress.emit(
                    int(d), int(t), "正在下载更新包"),
                cancel=lambda: self.cancelled.is_set(),
                expect_sha=self.info.get("sha256") or "")
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.sig_done.emit(res)


class UpdateDialog(QDialog):
    """下载/安装进度对话框；完成后返回是否应立即退出程序"""

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.need_restart = False
        self.setWindowTitle("更新 ComfyHub")
        self.setModal(True)
        v = QVBoxLayout(self)
        v.setSpacing(10)
        self.lb = QLabel(f"准备更新到 {info.get('latest')} …")
        self.lb.setWordWrap(True)
        v.addWidget(self.lb)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        v.addWidget(self.bar)
        self.lb_sub = QLabel("")
        self.lb_sub.setStyleSheet("color:#8a93a3; font-size:12px;")
        v.addWidget(self.lb_sub)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_cancel = QPushButton("取消")
        self.b_cancel.clicked.connect(self._cancel)
        row.addWidget(self.b_cancel)
        self.b_ok = QPushButton("关闭")
        self.b_ok.setEnabled(False)
        self.b_ok.clicked.connect(self.accept)
        row.addWidget(self.b_ok)
        v.addLayout(row)
        self.resize(460, 170)

        self._t0 = time.time()
        self.worker = _ApplyWorker(info, self)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_done.connect(self._on_done)
        self.worker.start()

    def _on_progress(self, done: int, total: int, stage: str):
        if total > 0:
            self.bar.setValue(int(done * 100 / total))
            speed = done / max(0.1, time.time() - self._t0)
            self.lb_sub.setText(f"{stage} · {updater.human_size(done)} / "
                                f"{updater.human_size(total)} · {updater.human_size(speed)}/s")
        else:
            self.lb_sub.setText(f"{stage} · {updater.human_size(done)}")

    def _cancel(self):
        self.worker.cancelled.set()
        self.lb_sub.setText("正在取消…")

    def _on_done(self, res: dict):
        self.b_cancel.setEnabled(False)
        self.b_ok.setEnabled(True)
        if not res.get("ok"):
            self.bar.setValue(0)
            self.lb.setText("更新失败")
            self.lb_sub.setText(str(res.get("error") or "未知错误")[:300])
            return
        mode = res.get("mode")
        self.bar.setValue(100)
        if mode == "portable":
            self.lb.setText("新版本已就绪")
            self.lb_sub.setText("程序即将关闭，更新脚本会自动覆盖文件并重新启动。")
        else:
            self.lb.setText("安装程序已启动")
            self.lb_sub.setText("ComfyHub 即将关闭，安装完成后会自动重新打开。")
        if res.get("warning"):
            self.lb_sub.setText(self.lb_sub.text() + f"（提示：{res['warning']}）")
        self.need_restart = True
        QTimer.singleShot(1200, self.accept)
