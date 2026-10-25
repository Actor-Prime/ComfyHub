# -*- coding: utf-8 -*-
"""批量运行对话框：同一工作流跑 N 次，可设每 K 次重启实例释放显存（防 OOM）

入口：ComfyUI 页「🔁 批量运行」
"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QSpinBox, QCheckBox, QFileDialog, QMessageBox, QProgressBar, QTextEdit,
)

from app import batch_runner as br


class BatchWorker(QThread):
    """后台执行批量任务：每次结果都通过信号回主线程更新界面"""
    sig_log = Signal(str)
    sig_progress = Signal(int, int, int, int)     # done, failed, current(1-based), total
    sig_finished = Signal(dict)

    def __init__(self, inst, prompt, times, restart_every, restart_cb=None, autostart_cb=None):
        super().__init__()
        self.inst = inst
        self.prompt = prompt
        self.times = times
        self.restart_every = restart_every
        self.restart_cb = restart_cb
        self.autostart_cb = autostart_cb
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        port = int(self.inst.get("port") or 8188)
        done = failed = 0
        errors = []
        try:
            if self.autostart_cb:
                self.sig_log.emit("检查实例状态…")
                self.autostart_cb()          # 由界面负责启动 + 等待就绪
            for i in range(1, self.times + 1):
                if self._stop:
                    self.sig_log.emit("⏹ 已手动停止")
                    break
                self.sig_log.emit(f"第 {i}/{self.times} 次…")
                try:
                    pid = br.submit(port, self.prompt)
                    entry = br.wait_done(port, pid, log=self.sig_log.emit)
                    ok, summary = br.summarize(entry)
                    if ok:
                        done += 1
                        self.sig_log.emit(f"  ✅ {summary}")
                    else:
                        failed += 1
                        errors.append(summary)
                        self.sig_log.emit(f"  ❌ {summary}")
                except Exception as e:
                    failed += 1
                    errors.append(str(e))
                    self.sig_log.emit(f"  ❌ {e}")
                self.sig_progress.emit(done, failed, i, self.times)
                if (self.restart_every and i % self.restart_every == 0
                        and i < self.times and not self._stop):
                    self.sig_log.emit(f"已完成 {i} 次，重启实例释放显存…")
                    try:
                        if self.restart_cb:
                            self.restart_cb()
                    except Exception as e:
                        self.sig_log.emit(f"  重启失败: {e}")
        finally:
            out = {"ok": failed == 0 and not self._stop, "done": done,
                   "failed": failed, "errors": errors, "stopped": self._stop}
            self.sig_finished.emit(out)


class BatchRunDialog(QDialog):
    def __init__(self, mainwin, inst, parent=None):
        super().__init__(parent or mainwin)
        self.mainwin = mainwin
        self.inst = inst
        self.prompt = None
        self.worker = None
        self.setWindowTitle(f"批量运行 — {inst.get('name', '')}")
        self.setMinimumWidth(620)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)

        tip = QLabel("把同一个工作流连续跑多次；可设置每跑 N 次重启实例，释放显存碎片防 OOM。\n"
                     "工作流需为 API 格式（ComfyUI 里 Workflow → Export (API)）。")
        tip.setStyleSheet("color:#9aa4b2;")
        tip.setWordWrap(True)
        root.addWidget(tip)

        # 工作流选择
        h1 = QHBoxLayout()
        self.ed_wf = QLineEdit()
        self.ed_wf.setReadOnly(True)
        self.ed_wf.setPlaceholderText("选择工作流 JSON…")
        b_pick = QPushButton("浏览…")
        b_pick.clicked.connect(self._pick)
        h1.addWidget(QLabel("工作流:"))
        h1.addWidget(self.ed_wf, 1)
        h1.addWidget(b_pick)
        root.addLayout(h1)

        # 次数 / 重启间隔
        h2 = QHBoxLayout()
        self.sp_times = QSpinBox(); self.sp_times.setRange(1, 9999); self.sp_times.setValue(10)
        self.sp_every = QSpinBox(); self.sp_every.setRange(0, 999); self.sp_every.setValue(5)
        self.ck_autostart = QCheckBox("实例未运行时自动启动")
        self.ck_autostart.setChecked(True)
        h2.addWidget(QLabel("执行次数:"))
        h2.addWidget(self.sp_times)
        h2.addSpacing(16)
        h2.addWidget(QLabel("每"))
        h2.addWidget(self.sp_every)
        h2.addWidget(QLabel("次重启实例（0 = 不重启）"))
        h2.addStretch()
        root.addLayout(h2)
        h3 = QHBoxLayout()
        h3.addWidget(self.ck_autostart)
        h3.addStretch()
        root.addLayout(h3)

        # 进度
        self.pb = QProgressBar()
        self.pb.setTextVisible(True)
        self.pb.setRange(0, 100)      # 初始要有非零范围，否则 Qt 不渲染文字
        self.pb.setValue(0)
        self.pb.setFormat("等待开始（0%）")
        # 主题里没有 QProgressBar 样式，深色背景下默认槽不可见 → 显式给一套
        self.pb.setMinimumHeight(22)
        self.pb.setStyleSheet(
            "QProgressBar{border:1px solid #2b3444;border-radius:4px;"
            "background:#10151d;text-align:center;color:#dbe4f0;}"
            "QProgressBar::chunk{background:#3f8cff;border-radius:3px;}")
        root.addWidget(self.pb)
        self.lb_stat = QLabel("")
        self.lb_stat.setStyleSheet("color:#9aa4b2;")
        root.addWidget(self.lb_stat)

        # 日志
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        self.log.setMinimumHeight(180)
        root.addWidget(self.log, 1)

        # 按钮
        hb = QHBoxLayout()
        hb.addStretch()
        self.b_start = QPushButton("▶ 开始")
        self.b_start.setProperty("accent", True)
        self.b_start.clicked.connect(self._start)
        self.b_stop = QPushButton("■ 停止")
        self.b_stop.setProperty("danger", True)
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        hb.addWidget(self.b_stop)
        hb.addWidget(self.b_start)
        hb.addWidget(b_close)
        root.addLayout(hb)

    # ---------------- 交互 ----------------
    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择工作流（API 格式）", "", "JSON (*.json)")
        if not p:
            return
        try:
            self.prompt = br.load_api_prompt(p)
        except Exception as e:
            QMessageBox.warning(self, "工作流无法使用", str(e))
            return
        self.ed_wf.setText(p)
        self.ed_wf.setToolTip(p)
        self._append(f"已载入工作流：{os.path.basename(p)}（{len(self.prompt)} 个节点）")

    def _append(self, m):
        self.log.append(m)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _port_ready(self):
        port = int(self.inst.get("port") or 8188)
        return br.wait_ready(port, timeout=1)

    def _start(self):
        if not self.prompt:
            QMessageBox.information(self, "批量运行", "请先选择工作流文件")
            return
        if self.worker and self.worker.isRunning():
            return
        times = self.sp_times.value()
        every = self.sp_every.value()
        self.b_start.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.sp_times.setEnabled(False)
        self.sp_every.setEnabled(False)
        self.pb.setRange(0, times)
        self.pb.setValue(0)
        self.pb.setFormat(f"0 / {times}")

        procs = self.mainwin.procs
        inst = self.inst

        def _autostart():
            if self._port_ready():
                self._append("实例已在运行")
                return
            if not self.ck_autostart.isChecked():
                raise RuntimeError("实例未运行，且未勾选自动启动")
            self._append("实例未运行，正在启动…")
            procs.get(inst).start(inst)
            if not br.wait_ready(int(inst.get("port") or 8188), timeout=300,
                                 log=lambda m: None):
                raise RuntimeError("实例启动超时（300s）")

        def _restart():
            self._append("停止实例…")
            procs.get(inst).stop()
            import time as _t
            for _ in range(60):
                _t.sleep(1)
                if not self._port_ready():
                    break
            self._append("重新启动实例…")
            procs.get(inst).start(inst)
            if not br.wait_ready(int(inst.get("port") or 8188), timeout=300, log=lambda m: None):
                raise RuntimeError("重启后等待就绪超时")

        self.worker = BatchWorker(inst, self.prompt, times, every,
                                  restart_cb=_restart, autostart_cb=_autostart)
        self.worker.sig_log.connect(self._append)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, done, failed, cur, total):
        self.pb.setValue(cur)
        self.pb.setFormat(f"{cur} / {total}")
        self.lb_stat.setText(f"成功 {done} · 失败 {failed}　（第 {cur}/{total} 次）")

    def _on_finished(self, out):
        self.b_start.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.sp_times.setEnabled(True)
        self.sp_every.setEnabled(True)
        if out.get("stopped"):
            self.lb_stat.setText(f"已停止：成功 {out['done']} · 失败 {out['failed']}")
        else:
            self.lb_stat.setText(f"完成：成功 {out['done']} · 失败 {out['failed']}")
            if out["ok"]:
                self.pb.setFormat("全部完成")
            else:
                self.pb.setFormat("有失败项")

    def _stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self._append("已请求停止（当前任务结束后退出）…")
            self.b_stop.setEnabled(False)

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            ans = QMessageBox.question(self, "批量运行进行中", "仍在运行，确定关闭？（当前任务会跑完）")
            if ans != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.request_stop()
        e.accept()
