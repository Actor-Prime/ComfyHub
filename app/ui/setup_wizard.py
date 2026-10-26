# -*- coding: utf-8 -*-
"""一键安装 ComfyUI 环境向导（QWizard 三步式）"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtCore import QTimer
from app.workers import (ScanWorker, SetupWorker, StepWorker,
                         swap_worker, wait_all)
from app.log import log_exc
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QLabel, QVBoxLayout, QLineEdit, QHBoxLayout,
    QPushButton, QFileDialog, QComboBox, QCheckBox, QProgressBar,
    QPlainTextEdit, QFormLayout, QMessageBox, QWidget,
)

from app import bootstrap
from app.bootstrap import PYPI_MIRRORS


# ---------------- 后台安装线程 ----------------
class _LogPage(QWizardPage):
    """安装进度页（步进制：每步 检测 → 询问 → 执行 → 复验 → 下一步）"""

    # ★ 跨线程日志信号：后台 worker 只能 emit，绝不能直接碰 Qt 控件
    #  （直接调用 UI 方法 = 非 GUI 线程操作 QPlainTextEdit → Qt 堆损坏 0xc0000374）
    sig_ui_log = Signal(str)

    STEP_TITLES = ["准备 Python 环境", "下载 ComfyUI 源码",
                   "安装 ComfyUI 依赖", "安装 PyTorch（按显卡匹配）"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("正在安装环境")
        self.setCommitPage(True)
        self.plan = []            # [{"title", "detect": fn, "install": fn}]
        self.cur = 0
        self.state = {}           # 跨步骤共享（python_path / comfyui_path）
        self.on_done = None
        self.done_ok = False

        v = QVBoxLayout(self)
        self.lb_step = QLabel("准备中…")
        self.bar = QProgressBar()
        self.lb_state = QLabel("")
        self.lb_state.setWordWrap(True)
        self.lb_state.setStyleSheet("color:#e8a33d;")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.lb_step)
        v.addWidget(self.bar)
        v.addWidget(self.lb_state)
        v.addWidget(self.log, 1)

        # 决策按钮行
        hb = QHBoxLayout()
        hb.addStretch()
        self.b_skip = QPushButton("跳过此步 ▶")
        self.b_act = QPushButton("安装")
        self.b_act.setProperty("accent", True)
        self.b_skip.clicked.connect(self._on_skip)
        self.b_act.clicked.connect(self._on_act)
        hb.addWidget(self.b_skip)
        hb.addWidget(self.b_act)
        v.addLayout(hb)
        self._decision_visible(False)
        self.worker = None
        self.result_data = {}
        # ★ 跨线程日志：worker 线程只 emit 信号，由主线程执行 _on_log 更新 UI
        self.sig_ui_log.connect(self._on_log)

    # ---------- 入口 ----------
    def start(self, target_dir, python_exe, mirror, proxy, torch_flag, on_done):
        from app import bootstrap
        # 本次安装日志落盘：%APPDATA%\ComfyHub\logs\install_YYYYmmdd_HHMMSS.log
        import datetime as _dt
        _logdir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                               "ComfyHub", "logs")
        os.makedirs(_logdir, exist_ok=True)
        self._install_log_path = os.path.join(
            _logdir, f"install_{_dt.datetime.now():%Y%m%d_%H%M%S}.log")
        try:
            with open(self._install_log_path, "w", encoding="utf-8") as f:
                f.write(f"# ComfyHub 安装日志 {_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                        f"# 目标: {target_dir}\n"
                        f"# Python: {python_exe or '（自动安装便携）'}\n"
                        f"# 镜像: {mirror}  代理: {proxy or '无'}  torch: {torch_flag}\n"
                        "--------------------------------------------------------\n")
        except OSError:
            self._install_log_path = ""
        self.log.clear()
        self.on_done = on_done
        self.done_ok = False
        self.state = {"target_dir": target_dir, "python_exe": python_exe,
                      "mirror_name": mirror, "proxy": proxy, "total": 4 if torch_flag else 3}
        # 分流：目录里已有 ComfyUI 实例 → 步进制（逐步检测询问）；
        #       空目录（全新安装）→ 全自动连跑（无需逐步确认）
        has_existing = os.path.isfile(os.path.join(target_dir, "main.py"))
        # 锁住前进类按钮（Back 保留：检测/决策阶段允许返回上页改配置）
        wiz = self.wizard()
        if wiz:
            for btn in (QWizard.CommitButton,
                        QWizard.NextButton, QWizard.FinishButton):
                wiz.button(btn).setEnabled(False)
        self._auto = not has_existing   # 空目录=全新安装：步进但自动连跑（无需点确认）
        self._start_stepwise(torch_flag)

    def start_repair(self, comfy_path, py_hint, mirror, proxy, on_done):
        """修复模式（2026-10-24 用户要求"能运行才能点完成"）：
        为验证失败的实例生成最小修复计划并 _auto 自动连跑。
        · ①准备 Python 环境：py_hint 有效 → 复验秒过；无 → 装共享便携 Python
        · ②补 ComfyUI 核心依赖   · ③装 PyTorch（按显卡；已装则自动跳过）
        最终验证通过 → on_done(True, {comfyui_path, python_path}) →
        向导回流第一页重新验证，通过才解锁「完成」。"""
        from app import bootstrap
        import datetime as _dt
        _logdir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                               "ComfyHub", "logs")
        os.makedirs(_logdir, exist_ok=True)
        self._install_log_path = os.path.join(
            _logdir, f"repair_{_dt.datetime.now():%Y%m%d_%H%M%S}.log")
        try:
            with open(self._install_log_path, "w", encoding="utf-8") as f:
                f.write(f"# ComfyHub 修复日志 {_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                        f"# 实例: {comfy_path}\n"
                        f"# 提示解释器: {py_hint or '（无，将自动安装共享便携 Python）'}\n"
                        f"# 镜像: {mirror}  代理: {proxy or '无'}\n"
                        "--------------------------------------------------------\n")
        except OSError:
            self._install_log_path = ""
        self.log.clear()
        self.on_done = on_done
        self.done_ok = False
        self.state = {
            "target_dir": comfy_path,
            "comfyui_path": comfy_path,
            "python_path": py_hint if (py_hint and os.path.isfile(py_hint)) else "",
            "mirror_name": mirror, "proxy": proxy, "total": 3,
        }
        # 锁前进/返回按钮（修复跑完或失败才放——防中途返回产生状态竞态）
        wiz = self.wizard()
        if wiz:
            for btn in (QWizard.CommitButton, QWizard.NextButton,
                        QWizard.FinishButton, QWizard.BackButton):
                wiz.button(btn).setEnabled(False)
        self._auto = True                 # 修复无需逐步确认：检测 ok 自动跳过、缺则自动装
        mirror_url = bootstrap.PYPI_MIRRORS.get(
            mirror, bootstrap.PYPI_MIRRORS["阿里云"])
        log = self.sig_ui_log.emit
        _tools_root = bootstrap.tools_root()
        self.plan = [
            {"title": "准备 Python 环境",
             "detect": lambda: bootstrap.detect_python_step(
                 self.state.get("python_path", ""), _tools_root),
             "install": lambda: bootstrap.install_portable_python(
                 _tools_root, log=log, proxy=proxy)},
            {"title": "补齐 ComfyUI 核心依赖",
             "detect": lambda: bootstrap.detect_requirements_step(
                 self.state.get("python_path"), comfy_path),
             "install": lambda: bootstrap.install_comfyui_requirements(
                 self.state.get("python_path"), comfy_path,
                 log=log, mirror=mirror_url)},
            {"title": "安装 PyTorch（按显卡匹配）",
             "detect": lambda: bootstrap.detect_torch_step(
                 self.state.get("python_path")),
             "install": lambda: self._install_torch_step(
                 mirror_url, os.path.join(_tools_root, "wheels"), log)},
        ]
        self.bar.setRange(0, 3)
        self.cur = 0
        self.lb_step.setText("修复模式：检测 → 补装 → 复验（自动连跑）…")
        self._begin_detect()

    def _start_auto(self, target_dir, python_exe, mirror, proxy, torch_flag):
        """空目录全新安装：全自动连跑"""
        self.lb_step.setText("全新安装（空目录）：自动执行全部步骤…")
        self.worker = SetupWorker(target_dir, python_exe, mirror, proxy, torch_flag)
        self.worker.sig_log.connect(self._on_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_done.connect(lambda ok, res, err: self.on_done(ok, res, err))
        self.worker.start()

    def _start_stepwise(self, torch_flag):
        """步进制（检测 → 询问 → 执行 → 复验）；_auto=True 时自动连跑不等待点击"""
        from app import bootstrap
        if self._auto:
            self.lb_step.setText("全新安装（空目录）：自动执行全部步骤…")
        total = 4 if torch_flag else 3
        self.bar.setRange(0, total)
        target_dir = self.state["target_dir"]
        python_exe = self.state["python_exe"]
        mirror = bootstrap.PYPI_MIRRORS.get(self.state["mirror_name"],
                                            bootstrap.PYPI_MIRRORS["阿里云"])
        wheels = os.path.join(bootstrap.tools_root(), "wheels")
        # ★ 关键：后台线程的日志只能走信号（emit），由主线程执行 _on_log。
        #   直接把 self._on_log 传进 worker 会导致跨线程操作 Qt 控件 → 堆损坏崩溃
        log = self.sig_ui_log.emit

        def _install_source_with_manager():
            """源码安装 + 附带 ComfyUI-Manager（全新安装的标配插件商店）"""
            res = bootstrap.install_comfyui_source(
                target_dir, log=log, proxy=self.state["proxy"])
            try:
                bootstrap.install_comfyui_manager(
                    res, log=log, proxy=self.state["proxy"])
            except Exception:
                pass   # Manager 失败不影响主流程
            return res

        plan = [
            {"title": self.STEP_TITLES[0],
             "detect": lambda: (
                 # 用户明确选了"重新安装"→ 强制走安装（覆盖共享版本）
                 ("missing", "已选择重新安装便携 Python（覆盖共享版本）", {})
                 if python_exe == "__reinstall__"
                 else bootstrap.detect_python_step(python_exe, bootstrap.tools_root())),
             "install": lambda: bootstrap.install_portable_python(
                 bootstrap.tools_root(), log=log, proxy=self.state["proxy"])},
            {"title": self.STEP_TITLES[1],
             "detect": lambda: bootstrap.detect_source_step(target_dir),
             "install": lambda: _install_source_with_manager()},
            {"title": self.STEP_TITLES[2],
             "detect": lambda: bootstrap.detect_requirements_step(
                 self.state.get("python_path"), self.state.get("comfyui_path")),
             "install": lambda: bootstrap.install_comfyui_requirements(
                 self.state.get("python_path"), self.state.get("comfyui_path"),
                 log=log, mirror=mirror)},
        ]
        if torch_flag:
            plan.append({"title": self.STEP_TITLES[3],
                         "detect": lambda: bootstrap.detect_torch_step(
                             self.state.get("python_path")),
                         "install": lambda: self._install_torch_step(mirror, wheels, log)})
        self.plan = plan[:total]
        self.cur = 0
        self._begin_detect()

    def _install_torch_step(self, mirror, wheels, log):
        from app import bootstrap
        gpu = bootstrap.detect_gpu()
        log(f"显卡: {gpu.label}")
        ok = bootstrap.install_torch(self.state.get("python_path"),
                                     gpu.torch_variant, log=log, mirror=mirror,
                                     wheels_dir=wheels)
        if not ok:
            log("CUDA 版安装失败，回退 CPU 版")
            bootstrap.install_torch(self.state.get("python_path"), "cpu",
                                    log=log, mirror=mirror)
        # GPU 加速验证（区分"torch 导入崩溃"与"正常但无 CUDA"两种失败）
        try:
            r = bootstrap.run([self.state["python_path"], "-c",
                "import torch;print('CUDA_OK' if torch.cuda.is_available() else 'CUDA_NO')"],
                capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                tail = ((r.stderr or "").strip().splitlines() or ["未知错误"])[-1]
                log(f"❌ torch 无法导入（安装损坏或依赖不全）：{tail[:140]}")
            else:
                out = (r.stdout or "").strip()
                if "CUDA_OK" in out:
                    log("torch 安装完成，GPU 加速: ✅ 可用")
                else:
                    log("⚠️ torch 安装完成，GPU 加速: 不可用（将以 CPU 运行）")
        except Exception:
            log("torch 验证跳过")

    # ---------- 步进状态机 ----------
    def _begin_detect(self):
        if self.cur >= len(self.plan):
            self._finish_all()
            return
        step = self.plan[self.cur]
        total = self.state["total"]
        self.bar.setValue(self.cur)
        self.lb_step.setText(f"步骤 {self.cur + 1}/{total}：{step['title']} —— 正在检测本地环境…")
        self._decision_visible(False)
        self.lb_state.setText("检测中…")
        swap_worker(self, "worker", StepWorker(step["detect"]))
        self.worker.sig_done.connect(self._on_detect_done)
        self.worker.start()

    def _on_detect_done(self, ok, res, err):
        step = self.plan[self.cur]
        if not ok:
            self.lb_state.setText(f"❌ 检测失败：{err}")
            self._show_decision("retry", f"检测出错：{err}")
            return
        status, info, payload = res
        color = {"ok": "#4cd47c", "missing": "#e8635a", "broken": "#e8635a",
                 "cpu": "#e8a33d", "outdated": "#e8a33d"}.get(status, "#9aa4b2")
        self.lb_state.setStyleSheet(f"color:{color};")
        self.state.update(payload)
        icon = {"ok": "✅", "missing": "❌", "broken": "🔧",
                "cpu": "⚠️", "outdated": "⚠️"}.get(status, "")
        self.lb_state.setText(f"{icon} {info}")
        if status == "ok":
            self._show_decision("ok", info)
        elif status == "missing":
            self._show_decision("missing", info)
        elif status == "broken":
            self._show_decision("broken", info)
        else:  # cpu / outdated
            self._show_decision("update", info)

    def _show_decision(self, kind, info):
        # 自动模式（全新安装）：不等待点击，直接执行默认动作
        if getattr(self, "_auto", False) and kind in ("ok", "missing", "broken", "update"):
            from PySide6.QtCore import QTimer as _QT
            if kind == "ok":
                _QT.singleShot(200, self._on_skip)
            else:
                _QT.singleShot(200, self._on_act)
        """kind: ok=建议跳过 / missing=需安装 / broken=已装但损坏需强制重装
                / update=建议更新 / retry=检测失败重试"""
        wiz = self.wizard()
        if wiz:
            wiz.button(QWizard.BackButton).setEnabled(True)   # 决策阶段允许返回
        self.b_act.show()
        self.b_skip.show()
        # 必需步骤缺失/损坏时禁止跳过（跳过=环境残缺，后续启动必失败）
        # 仅 PyTorch 步骤（最后一步，索引 == total-1）允许跳过
        is_optional = getattr(self, "cur", 0) >= self.state.get("total", 4) - 1
        if kind == "ok":
            self.b_act.setText("重新安装")
            self.b_skip.setText("跳过此步 ▶")
            self.b_skip.setEnabled(True)
            self.b_skip.setDefault(True)
        elif kind == "missing":
            self.b_act.setText("📦 安装")
            self.b_act.setDefault(True)
            self.b_skip.setEnabled(is_optional)
            self.b_skip.setText("跳过此步 ▶" if is_optional else "此步必需，不可跳过")
        elif kind == "broken":
            self.b_act.setText("🔧 强制重装")
            self.b_act.setDefault(True)
            self.b_skip.setEnabled(is_optional)
            self.b_skip.setText("跳过此步 ▶" if is_optional else "此步损坏，必须修复")
        elif kind == "update":
            self.b_act.setText("⬆ 更新")
            self.b_skip.setText("跳过（保留现状）▶")
            self.b_skip.setEnabled(True)
            self.b_act.setDefault(True)
        else:  # retry
            self.b_act.setText("🔄 重试")
            self.b_skip.setText("跳过此步 ▶")
            self.b_skip.setEnabled(True)
        self._decision_visible(True)

    def _decision_visible(self, vis):
        self.b_act.setVisible(vis)
        self.b_skip.setVisible(vis)

    def _lock(self, locked):
        self.b_act.setEnabled(not locked)
        self.b_skip.setEnabled(not locked)

    def _on_act(self):
        """执行安装/更新/重装（锁按钮 → 跑 → 复验）；最终验证阶段=重新验证"""
        if getattr(self, "_phase", "") == "final_verify":
            self._lock(True)
            self._finish_all()
            return
        self._lock(True)
        wiz = self.wizard()
        if wiz:
            wiz.button(QWizard.BackButton).setEnabled(False)   # 执行中禁止返回改配置
        step = self.plan[self.cur]
        _title = step.get("title", "")
        self.lb_state.setText(f"⏳ 正在执行：{_title}（完成后自动进入下一步）")
        swap_worker(self, "worker", StepWorker(step["install"]))
        self.worker.sig_done.connect(self._on_install_done)
        self.worker.start()

    def _on_install_done(self, ok, res, err):
        if not ok:
            self.lb_state.setText(f"❌ 执行失败：{err}")
            self._show_decision("retry", err)
            self._lock(False)
            return
        # 复验：重新检测，确认最新
        self.lb_state.setText("执行完成，正在复验…")
        step = self.plan[self.cur]
        self.worker = StepWorker(step["detect"])
        try:
            self.worker.sig_done.disconnect()
        except Exception:
            pass
        self.worker.sig_done.connect(self._on_verify_done)
        self.worker.start()

    def _on_verify_done(self, ok, res, err):
        if not ok:
            self.lb_state.setText(f"❌ 复验失败：{err}")
            self._show_decision("retry", err)
            self._lock(False)
            return
        status, info, payload = res
        self.state.update(payload)
        if status != "ok":
            self.lb_state.setText(f"⚠️ 复验未通过：{info}")
            self._show_decision("retry", info)
            self._lock(False)
            return
        self.lb_state.setStyleSheet("color:#4cd47c;")
        self.lb_state.setText(f"✅ {info}")
        self._lock(False)
        self._advance()

    def _on_skip(self):
        self._advance()

    def _advance(self):
        self.cur += 1
        self.bar.setValue(self.cur)
        if self.cur >= len(self.plan):
            self._finish_all()
        else:
            self._begin_detect()

    def _finish_all(self):
        total = self.state["total"]
        self.bar.setValue(total)
        self.lb_step.setText("全部步骤完成 —— 正在进行最终验证…")
        self._decision_visible(False)
        self._phase = "final_verify"
        from app import bootstrap
        py = self.state.get("python_path") or ""
        cp = self.state.get("comfyui_path") or self.state.get("target_dir")
        w = StepWorker(
            lambda: bootstrap.verify_comfyui_runnable(py, cp))
        w.sig_done.connect(self._on_final_verify)
        swap_worker(self, "_verify_worker", w)
        w.start()

    def _on_final_verify(self, ok, res, err):
        if not ok:
            self.lb_step.setText("最终验证执行失败")
            self.lb_state.setText(f"❌ {err}")
            self.b_skip.hide()
            self.b_act.setText("🔄 重新验证")
            self.b_act.show()
            self._lock(False)
            return
        v_ok, info = res
        if not v_ok:
            self.lb_step.setText("最终验证未通过")
            self.lb_state.setStyleSheet("color:#e8635a;")
            self.lb_state.setText(f"❌ {info}——请查看日志排查后点「重新验证」。")
            self.b_skip.hide()
            self.b_act.setText("🔄 重新验证")
            self.b_act.show()
            self._lock(False)
            return
        self.lb_step.setText("全部步骤完成，最终验证通过 ✅")
        self.lb_state.setStyleSheet("color:#4cd47c;")
        self.lb_state.setText(f"{info}——点「完成」自动添加为实例。")
        self._decision_visible(False)
        self.result_data = {
            "comfyui_path": self.state.get("comfyui_path") or self.state.get("target_dir"),
            "python_path": self.state.get("python_path") or "",
        }
        self.done_ok = True
        wiz = self.wizard()
        if wiz:
            wiz.button(QWizard.BackButton).setEnabled(False)
        if self.on_done:
            self.on_done(True, dict(self.result_data), "")

    @staticmethod
    def _log_css(msg):
        """按内容给日志行分类着色（阶段标题/命令/下载/成功/警告/错误）。
        返回 (颜色, 是否加粗)；判定顺序：错误 > 警告 > 成功 > 阶段 > 下载 > 命令 > 默认。"""
        s = msg.strip()
        if not s:
            return "#c8d2e0", False
        if ("❌" in s or "✗" in s or s.startswith("Error") or "Traceback" in s
                or "WinError" in s or "OSError" in s):
            return "#ff6b6b", False
        if "⚠️" in s or "警告" in s:
            return "#f0a04b", False
        if "失败" in s or "错误" in s:     # 无 ⚠️/❌ 标记的兜底失败行
            return "#ff6b6b", False
        if ("✅" in s or s.startswith("✓") or "就绪" in s or "已完成"
                in s or "安装成功" in s or "已清理" in s):
            return "#5cc98f", False
        if s.startswith("──") or s.startswith("═"):
            return "#6fc3ff", True
        if s.startswith("↓") or s.startswith("📥") or "下载" in s:
            return "#7ec7ff", False
        if s.startswith("$ "):
            return "#8fa3bf", False
        return "#c8d2e0", False

    def _on_log(self, msg):
        import html as _html
        # ★ 落盘（2026-10-26 修：此前只写 UI，install/repair 日志文件只有头部没有正文，
        #   出问题时无从排查）——追加写入本次安装/修复日志文件
        _path = getattr(self, "_install_log_path", "")
        if _path:
            try:
                with open(_path, "a", encoding="utf-8") as _f:
                    _f.write(msg.rstrip() + "\n")
            except OSError:
                pass
        _color, _bold = self._log_css(msg)
        _w = "font-weight:bold;" if _bold else ""
        self.log.appendHtml(
            f'<span style="color:{_color};{_w}">{_html.escape(msg)}</span>')
        from PySide6.QtGui import QTextCursor
        self.log.moveCursor(QTextCursor.End)
        # 同步落盘（失败静默，不打扰安装）——磁盘文件保持纯文本，便于排查
        _lp = getattr(self, "_install_log_path", "")
        if _lp:
            try:
                import datetime as _dt
                with open(_lp, "a", encoding="utf-8") as _f:
                    _f.write(f"[{_dt.datetime.now():%H:%M:%S}] {msg}\n")
            except OSError:
                pass

    def _on_progress(self, step, total):
        pass


class SetupWizard(QWizard):
    """三步向导：位置与选项 → （安装页）→ 完成"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("一键安装 ComfyUI 环境")
        self.setWizardStyle(QWizard.ModernStyle)
        self.resize(720, 560)
        self.setup_result = {}

        # ---------- 页1：位置与选项 ----------
        self.p1 = p1 = QWizardPage()
        p1.setTitle("选择安装位置")
        form = QFormLayout(p1)

        self.ed_dir = QLineEdit(os.path.join("F:\\", "ComfyUI"))
        self.b_pick = QPushButton("浏览…")
        self.b_pick.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.ed_dir, 1)
        row.addWidget(self.b_pick)
        form.addRow("安装到:", row)

        self.ed_dir.editingFinished.connect(self._detect_existing)
        self.ed_name = QLineEdit("")
        self.ed_name.setPlaceholderText("不填则使用安装目录名")
        form.addRow("实例名称:", self.ed_name)

        # 扫描结果提示 + 候选下拉（扫描到已有环境时显示）
        self.lb_warn = QLabel("")
        self.lb_warn.setWordWrap(True)
        self.lb_warn.setStyleSheet("color:#e8635a; font-weight:500;")
        self.lb_warn.hide()
        form.addRow("", self.lb_warn)
        self.lb_scan = QLabel("")
        self.lb_scan.setWordWrap(True)
        self.lb_scan.setStyleSheet("color:#4cd47c;")
        self.lb_scan.hide()
        form.addRow("", self.lb_scan)
        # 检测/验证中的不确定进度条（滚动动画）
        self.pbar_scan = QProgressBar()
        self.pbar_scan.setRange(0, 0)
        self.pbar_scan.setTextVisible(False)
        self.pbar_scan.setFixedHeight(6)
        self.pbar_scan.hide()
        form.addRow("", self.pbar_scan)
        # 当前模式提示：添加已有实例 / 全新安装（两者界面元素完全不同）
        self.lb_mode = QLabel("")
        self.lb_mode.setStyleSheet("color:#4cd47c; font-weight:600;")
        self.lb_mode.hide()
        form.addRow("", self.lb_mode)

        # 逃生口：验证失败（缺依赖/无 Python）时的「跳过修复」低调入口。
        # 主按钮走「下一步：修复」；此按钮仅供高级用户先加进列表再手动处理
        self.b_skip_repair = QPushButton("仍要添加（跳过修复，稍后到主界面手动处理）")
        self.b_skip_repair.setStyleSheet(
            "QPushButton{color:#667; border:none; text-align:left;"
            " padding:1px 0; font-size:11px;}"
            "QPushButton:hover{color:#9ab;} ")
        self.b_skip_repair.setCursor(Qt.PointingHandCursor)
        self.b_skip_repair.hide()
        self.b_skip_repair.clicked.connect(self._skip_repair)
        form.addRow("", self.b_skip_repair)

        self.cb_found = QComboBox()
        self.cb_found.hide()
        self.lb_found_title = QLabel("检测到:")
        self.lb_found_title.hide()
        form.addRow(self.lb_found_title, self.cb_found)

        # ---- 安装选项容器（未检测到已有环境时才展开） ----
        self.install_opts = QWidget()
        form_opts = QFormLayout(self.install_opts)
        form_opts.setContentsMargins(0, 0, 0, 0)

        self.cb_python = QComboBox()
        tools_py = os.path.join(bootstrap.tools_root(), "python", "python.exe")
        if os.path.isfile(tools_py):
            # 共享便携 Python 已存在：默认使用它（免重装），重装降级为可选项
            self.cb_python.addItem("使用 ComfyHub 共享便携 Python（已安装，实例共用）",
                                   userData=tools_py)
            self.cb_python.addItem("重新安装便携 Python（覆盖共享版本）", userData=None)
        else:
            self.cb_python.addItem("自动安装便携 Python（推荐，不污染系统）", userData=None)
        # 已注册实例用过的解释器也列为可选
        parent_cfg = getattr(self.parent(), "cfg", {}) if self.parent() else {}
        seen = {tools_py}
        for inst in parent_cfg.get("instances", []):
            cand = inst.get("python_path") or ""
            if cand and os.path.isfile(cand) and cand not in seen:
                self.cb_python.addItem(f"使用已有: {cand}", userData=cand)
                seen.add(cand)
        form_opts.addRow("Python 环境:", self.cb_python)

        # 解释器路径展示（跟随上方选择实时更新；选中"重新安装"时显示目标安装路径）
        self.ed_py_path = QLineEdit("")
        self.ed_py_path.setReadOnly(True)
        self.ed_py_path.setStyleSheet("color:#8fa3bf;")
        form_opts.addRow("解释器路径:", self.ed_py_path)

        def _sync_py_path(_idx=0):
            ud = self.cb_python.currentData()
            if ud == "__reinstall__":
                self.ed_py_path.setText(
                    "将安装到: " + os.path.join(bootstrap.tools_root(), "python", "python.exe"))
            elif ud:
                self.ed_py_path.setText(ud)
            else:
                self.ed_py_path.setText("（将自动下载安装便携 Python）")

        self.cb_python.currentIndexChanged.connect(_sync_py_path)
        _sync_py_path()

        self.cb_mirror = QComboBox()
        self.cb_mirror.addItems(list(PYPI_MIRRORS.keys()))
        self.cb_mirror.setCurrentText("阿里云")
        form_opts.addRow("PyPI 镜像:", self.cb_mirror)

        self.ed_proxy = QLineEdit("")
        self.ed_proxy.setPlaceholderText("留空直连；GitHub 下载慢可填 https://ghproxy.net/ 等")
        form_opts.addRow("GitHub 代理:", self.ed_proxy)

        self.ck_torch = QCheckBox("自动安装 PyTorch（按显卡自动选 CUDA/CPU 版，约 1.5~2.5GB）")
        self.ck_torch.setChecked(True)
        form_opts.addRow("", self.ck_torch)

        tip = QLabel("说明：便携 Python 与 MinGit 安装在 %APPDATA%\\ComfyHub\\tools，"
                     "可随时整目录删除卸载，不影响系统。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;")
        form_opts.addRow("", tip)

        self.install_opts.hide()   # 默认隐藏：扫描无结果后才展开
        form.addRow("", self.install_opts)

        self.addPage(p1)

        # 打开向导即自动探测默认路径（用户点添加 = 先看有没有现成的）
        QTimer.singleShot(150, self._auto_probe)

    def _auto_probe(self):
        """自动探测 _PROBE_ROOTS：找到现成 ComfyUI → 填入并触发扫描验证；
        都没有 → 提示用户选择安装位置"""
        from app.config import _PROBE_ROOTS
        for root in _PROBE_ROOTS:
            if os.path.isfile(os.path.join(root, "main.py")):
                self.ed_dir.setText(os.path.normpath(root))
                self._detect_existing()
                return
        # 默认路径全空：预填「程序所在目录/ComfyUI」作为安装位置（不用系统盘 C:）
        import sys as _sys
        if getattr(_sys, "frozen", False):
            base = os.path.dirname(_sys.executable)          # 打包后 = exe 所在目录
        else:
            base = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))                 # 源码运行 = 项目根
        # 默认扫「程序所在目录的上级」——现成实例常和启动器同级的任意命名目录
        # （例：F:\ComfyHub\ComfyUIImage 就在启动器目录 F:\ComfyHub 下）
        self.ed_dir.setText(base)
        _parent = os.path.dirname(base.rstrip("\\/"))
        # ★ 父目录是盘根（F:\）时绝不扫描——整盘遍历极慢且结果杂乱
        _is_drive_root = len(_parent.rstrip("\\/")) <= 2
        if (not _is_drive_root and _parent and os.path.isdir(_parent)
                and os.path.normpath(_parent) != os.path.normpath(base)):
            self._orig_root = os.path.join(base, "ComfyUI")   # 无实例时的全新安装目标
            self._scan_parent = True
            self.lb_scan.setStyleSheet("color:#9aa4b2;")
            self.lb_scan.setText(f"正在检查 {_parent} 下有没有现成 ComfyUI 实例…")
            self.lb_scan.show()
            self._start_scan(_parent)
        else:
            self._detect_existing()

        # ---------- 页2：安装进度 ----------
        self.p2 = _LogPage()
        self.addPage(self.p2)

        # 中文按钮文案
        self.setButtonText(QWizard.BackButton, "< 上一步")
        self.setButtonText(QWizard.NextButton, "下一步 >")
        self.setButtonText(QWizard.CommitButton, "开始安装 >")
        self.setButtonText(QWizard.FinishButton, "完成")
        self.setButtonText(QWizard.CancelButton, "取消")
        # 安装页是最后一页，Qt 显示 FinishButton 且默认可点——初始必须禁用，
        # 安装成功后由 _on_done 解锁
        self.button(QWizard.FinishButton).setEnabled(False)

        self.currentIdChanged.connect(self._on_page_change)
        self._started = False
        # 修复模式：失败态点「下一步：修复」进 p2 时为 True——p2 跑修复计划、
        # 完成回流第一页重验（不关向导）；新一轮检测/逃生口/验证通过时清回 False
        self._repair_mode = False
        # 验证结果缓存 {comfy路径: 可用解释器}——切回已验过的候选秒过，不必重跑 2~4 秒
        self._verify_cache = {}
        # ★ UI 操作审计：记录真实点击/选择到 %APPDATA%\ComfyHub\logs\ui_YYYYMMDD.log
        from app.ui.audit import log_ui
        self._audit = log_ui
        self.setObjectName("SetupWizard")
        self._hook_audit()
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.instance().installEventFilter(self)
        except Exception:
            pass

        # 按钮状态守卫：Qt 的 finalPage/按钮状态会被多处修改，这里按"当前模式"兜底校正
        from PySide6.QtCore import QTimer as _QT
        self._btn_guard = _QT(self)
        self._btn_guard.timeout.connect(self._guard_buttons)
        self._btn_guard.start(300)

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
        wait_all(self, ("_scan_worker", "_verify_worker", "worker",
                        "_del_worker", "_rm_worker"), 10000)
        event.accept()

    def _lock_inputs(self, locked, hint=""):
        """检测/验证期间锁住输入框与浏览按钮（防止中途改路径导致状态错乱）"""
        for w in (getattr(self, "ed_dir", None), getattr(self, "ed_name", None),
                  getattr(self, "b_pick", None)):
            if w is not None:
                w.setEnabled(not locked)
        if hasattr(self, "pbar_scan"):
            self.pbar_scan.setVisible(locked)
        if locked and hint and hasattr(self, "lb_scan"):
            self.lb_scan.setStyleSheet("color:#9ec3f0;")
            self.lb_scan.setText(f"🔍 {hint}")
            self.lb_scan.show()

    def _hook_audit(self):
        """记录向导被打开（后续真实点击由 eventFilter 捕获）"""
        try:
            from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLineEdit)
            self._audit_widgets = (QAbstractButton, QComboBox, QLineEdit)
            from app.config import APP_VERSION
            self._audit("WIZARD_OPEN", version=APP_VERSION)
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        """只记录属于本向导的、用户真实触发的交互"""
        try:
            from PySide6.QtCore import QEvent
            t = ev.type()
            _w = getattr(self, "_audit_widgets", None)
            if _w and t in (QEvent.MouseButtonRelease, QEvent.KeyPress) and isinstance(obj, _w):
                if self.isVisible() and self.isAncestorOf(obj):
                    if t == QEvent.KeyPress and ev.key() not in (0x01000004, 0x01000005):
                        pass   # 只关心 Enter/Return，其他按键忽略
                    else:
                        self._audit_click(obj)
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _audit_click(self, w):
        try:
            from PySide6.QtWidgets import QAbstractButton, QComboBox, QLineEdit
            cls = type(w).__name__
            if isinstance(w, QAbstractButton):
                self._audit("CLICK", btn=(w.text()[:24] if hasattr(w, "text") else cls))
            elif isinstance(w, QComboBox):
                self._audit("SELECT", combo=cls, value=w.currentText()[:30])
            elif isinstance(w, QLineEdit):
                self._audit("INPUT", field=cls, value=w.text()[:60])
            self._audit("STATE",
                        dir=self.ed_dir.text()[:70],
                        mode=("fresh" if not self.install_opts.isHidden() else "existing"),
                        next=f"{self.button(QWizard.NextButton).text()}/{self.button(QWizard.NextButton).isEnabled()}",
                        cands=self.cb_found.count())
        except Exception:
            pass

    def _guard_buttons(self):
        """每 300ms 按当前模式校正按钮语义（兜底：多处代码会改 finalPage/按钮状态，
        这里保证用户看到的永远自洽——fresh 模式绝不显示"完成"，
        添加实例模式绝不显示可点的"下一步"）"""
        try:
            if not self.isVisible():
                return
            # ★ 安装未完成前 Finish 必须保持禁用——Qt 翻页时会自动把末页的
            #   FinishButton 重新置为可用，覆盖手动 setEnabled(False)（2026-10-24 用户实测踩到）
            _p2 = getattr(self, "p2", None)
            if _p2 is not None and not getattr(_p2, "done_ok", False):
                try:
                    self.button(QWizard.FinishButton).setEnabled(False)
                except Exception:
                    pass
            # ★ 扫描进行中：「下一步」由 _start_scan 禁用、_on_scan_done 解锁，
            #   守卫不得插手（否则 fresh 分支会在 300ms 后把它重新打开，前功尽弃）
            if getattr(self, "_busy_scan", False):
                return
            _fresh = not self.install_opts.isHidden()
            _next = self.button(QWizard.NextButton)
            if _fresh:
                if _next.text().strip() == "完成":
                    self.setButtonText(QWizard.NextButton, "下一步 >")
                if not _next.isEnabled():
                    _next.setEnabled(True)
            else:
                # 添加实例模式：Finish 必须藏掉（它和改文案的 Next「完成」并存
                # 会出现两个「完成」——2026-10-26 用户截图实测）
                try:
                    self.button(QWizard.FinishButton).hide()
                except Exception:
                    pass
                _txt = self.lb_scan.text()
                # 用 in 而非 startswith——文案现在带"🔍 已扫描…"scope 前缀行
                if "❌ 没有可用的 Python" in _txt or "❌ 该目录不是" in _txt:
                    return
                if _next.text().strip() not in ("仍要添加", "完成", "下一步：修复"):
                    self.setButtonText(QWizard.NextButton, "完成")
        except Exception:
            pass

    def _apply_fresh_buttons(self):
        """全新安装状态下的按钮语义（统一出口，避免各处漏设导致显示"完成"）：
        Next = 「下一步 >」可用、Finish 禁用、本页不是 final page"""
        try:
            p1 = self.page(0)
            if p1.isFinalPage():
                p1.setFinalPage(False)
            self.setButtonText(QWizard.NextButton, "下一步 >")
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.FinishButton).setEnabled(False)
            self._repair_mode = False       # fresh 形态绝不走修复计划（防失败态残留误触发）
            try:
                self.b_skip_repair.hide()   # fresh 形态无修复语义，逃生口跟着藏
            except Exception:
                pass
        except Exception:
            pass

    def _set_mode(self, mode: str, keep_picker: bool = False, note: str = None):
        """统一控制两种模式的界面差异。
        existing = 添加已有实例：只需要选目录 + 验证，不显示任何安装参数
        fresh    = 全新安装：显示 Python 环境 / 镜像 / 代理 / torch 等安装参数
        keep_picker=True：fresh 形态但保留候选下拉（下拉第0项「新建新实例」被选中时——
               清掉下拉会让用户切不回已扫到的实例）
        note：existing 绿字可选覆盖（验证中/通过等状态由调用方传，避免写死"点完成"）
        """
        if mode == "existing":
            self.install_opts.hide()          # 添加已有实例不需要安装参数
            # ★ 恢复候选下拉左侧标题（2026-10-26 用户实测：下拉切过「新建新实例」后
            #   fresh 分支把 lb_found_title hide 了，切回实例却没人恢复 → 标题永久消失）
            if hasattr(self, "lb_found_title") and getattr(self, "_scan_candidates", None):
                _n = len(self._scan_candidates)
                self.lb_found_title.setText(
                    f"请选择实例（共 {_n} 个）:" if _n > 1 else "请选择实例:")
                self.lb_found_title.show()
            if hasattr(self, "lb_mode"):
                self.lb_mode.setStyleSheet("color:#4cd47c; font-weight:600;")
                self.lb_mode.setText(
                    note or "🔗 添加已有实例（不会下载任何东西，点「完成」直接注册）")
                self.lb_mode.show()
        else:
            # 还原 Next 按钮（全新安装需要"下一步"进入安装页，而非"完成"）
            try:
                p1 = self.page(0)
                p1.setFinalPage(False)
                self.setButtonText(QWizard.NextButton, "下一步 >")
                self.button(QWizard.FinishButton).show()
                self.button(QWizard.FinishButton).setEnabled(False)   # 安装页才允许完成
            except Exception:
                pass
            self.install_opts.show()          # 全新安装才需要这些参数
            # 切到全新安装时清掉上一次检测残留的候选，避免"检测到 A 却要装 B"的混淆
            # 注意：必须先清 _scan_candidates，否则 clear() 触发的 currentIndexChanged
            #       回调会用旧候选把路径又同步回去
            # keep_picker：下拉「新建新实例」场景不许清——候选还要留着让用户切回去
            if not keep_picker:
                self._scan_candidates = []
                if hasattr(self, "cb_found"):
                    self.cb_found.blockSignals(True)
                    self.cb_found.clear()
                    self.cb_found.blockSignals(False)
                    self.cb_found.hide()
            if hasattr(self, "lb_found_title"):
                self.lb_found_title.hide()
            if hasattr(self, "lb_mode"):
                self.lb_mode.setStyleSheet("color:#e8a33d; font-weight:600;")
                self.lb_mode.setText("📦 全新安装（将在下方选定位置创建环境）")
                self.lb_mode.show()
            # 末尾再校正一次：fresh 模式下绝不能停留在 final page（否则 Next 会显示成"完成"）
            try:
                _p = self.page(0)
                if _p.isFinalPage():
                    _p.setFinalPage(False)
                self.setButtonText(QWizard.NextButton, "下一步 >")
                self.button(QWizard.FinishButton).setEnabled(False)
            except Exception:
                pass

    def _selected_candidate(self):
        """当前下拉选中的候选实例路径；选中「新建新实例」或无候选 → None。
        ★ 下拉第 0 项固定是「➕ 新建新实例…」，候选从 idx 1 起——索引偏移只在这一处处理"""
        cands = getattr(self, "_scan_candidates", None) or []
        if not cands:
            return None
        idx = self.cb_found.currentIndex()
        has_new = self.cb_found.count() > 0 and "新建新实例" in self.cb_found.itemText(0)
        if has_new:
            if idx <= 0:
                return None                      # 选中「新建新实例」→ 走 fresh 安装
            return cands[min(idx - 1, len(cands) - 1)]
        return cands[min(max(idx, 0), len(cands) - 1)]

    def _sync_path_to_candidate(self):
        """把「安装到」/「实例名称」同步为当前选中候选的真实路径。
        否则路径框会停在用户输入的（可能不存在的）路径，与实际实例不一致"""
        try:
            real = self._selected_candidate()
            if not real:
                return                     # 「新建新实例」：保持用户自己填的路径
            if self.ed_dir.text().strip() and os.path.normpath(
                    self.ed_dir.text().strip()) == os.path.normpath(real):
                return
            self.ed_dir.setText(real)
            self.ed_name.setText(os.path.basename(real.rstrip("\\/")) or "ComfyUI")
        except Exception:
            pass

    def _start_scan(self, path: str):
        """发起目录扫描：创建 worker → 连接结果 → 启动。
        ★ 三步必须成套，缺任何一个都会卡在"正在检查"（v0.5.78 踩过）
        ★ 带序号：上一轮扫描（如自动探测）的结果可能延迟到达，必须丢弃，
          否则会用旧候选覆盖用户刚选的路径状态（v0.5.94 修复）
        ★ 扫描期间必须禁用「下一步」——否则结果未归就点下一步，
          会以"全新安装"状态闯进安装页，对着已有实例目录跑安装（2026-10-24 用户实测踩到）
        """
        self._scan_seq = getattr(self, "_scan_seq", 0) + 1
        _seq = self._scan_seq
        self._busy_scan = True
        try:
            self.button(QWizard.NextButton).setEnabled(False)
        except Exception:
            pass
        swap_worker(self, "_scan_worker", ScanWorker(path))
        self._scan_worker.sig_done.connect(
            lambda root, cands, s=_seq: self._on_scan_done(root, cands, s))
        # 实时进度（全深度递归可能扫上万个文件夹，没进度会像卡死）
        self._scan_worker.sig_progress.connect(
            lambda n, s=_seq: self._on_scan_progress(n, s))
        self._scan_worker.start()

    def _on_scan_progress(self, n, seq=None):
        """扫描中进度：仅本轮扫描有效时刷新文案（过期 worker 的延迟进度直接丢弃）"""
        if seq is not None and seq != getattr(self, "_scan_seq", 0):
            return
        if not getattr(self, "_busy_scan", False):
            return
        _root = os.path.normpath(self.ed_dir.text().strip())
        self.lb_scan.setStyleSheet("color:#9aa4b2;")
        self.lb_scan.setText(
            f"🔍 正在扫描 {_root} 的全部子目录…\n"
            f"已检查 {n} 个文件夹（全深度递归，正在寻找 ComfyUI 实例）")
        self.lb_scan.show()
        try:
            self.pbar_scan.show()
        except Exception:
            pass

    def _detect_existing(self):
        """后台扫描目录（含两层子目录）：找到 ComfyUI 直接注册；没找到自动补全并展开安装选项"""
        # ★ 入口先作废在途扫描：用户可能改了路径，旧扫描结果不能再落地（序号作废），
        #   同时解除 busy——否则走"目录不存在"等不发新扫描的分支时，「下一步」会永久卡禁用
        self._scan_seq = getattr(self, "_scan_seq", 0) + 1
        self._busy_scan = False
        self._repair_mode = False      # 新一轮检测 = 上一轮修复意图作废
        try:
            self.b_skip_repair.hide()  # 清掉上一轮失败态的逃生口残留
        except Exception:
            pass
        root = os.path.normpath(self.ed_dir.text().strip())
        self._scan_parent = False          # 清掉上一轮残留标记，避免误走父目录分支
        self._lock_inputs(True, "正在检测本机环境，请稍候…")
        if not os.path.isdir(root):
            # ★ 探测阶段绝不创建文件夹
            # ★ 路径不存在时，先扫「父目录」找现成实例
            #   （例：输入 F:\ComfyHub\ComfyUI 不存在，但兄弟目录 ComfyUIImage 里就有实例）
            _parent = os.path.dirname(root.rstrip("\\/"))
            _is_drive_root = len(_parent.rstrip("\\/")) <= 2
            if not _is_drive_root and _parent and os.path.isdir(_parent) and _parent != root:
                self._orig_root = root
                self._scan_parent = True
                self.lb_scan.setStyleSheet("color:#9aa4b2;")
                self.lb_scan.setText(
                    f"该路径不存在，正在检查上级目录 {_parent} 里有没有现成实例…")
                self.lb_scan.show()
                self._start_scan(_parent)
                return
            self._orig_root = root
            self.cb_found.hide()
            self.lb_found_title.hide()
            self._set_mode("fresh")
            self._lock_inputs(False)
            self.lb_scan.setStyleSheet("color:#e8a33d;")
            # 目录不存在：按与「空目录」一致的规则推导安装目标（末段不含 comfyui 就补一层）
            _bn = os.path.basename(root.rstrip("\\/")).casefold()
            _target = root if "comfyui" in _bn else os.path.join(root, "ComfyUI")
            self.ed_dir.setText(_target)
            self.lb_scan.setText(
                f"📁 目录不存在，将在此全新安装：\n{_target}\n"
                "（安装开始时才创建，现在不会动你的磁盘）")
            p1 = self.page(0)          # ← 此前漏了这行，导致输入不存在路径直接 NameError 崩溃
            self._apply_fresh_buttons()
            if not self.ed_name.text().strip():
                self.ed_name.setText(os.path.basename(root.rstrip("\\/")) or "")
            return
        # 记录用户选择的原始根目录（实例名默认取它的名字）
        self._orig_root = root
        self._prefer_abs = ""    # 清掉上一轮"默认选中某个候选"的残留
        # ★ 选中的路径本身就是一个 ComfyUI 实例（有 main.py）：只扫它自己只会找到它自己，
        #   兄弟实例（如 F:\\ComfyHub_testdata 下并列的 ComfyUIH3/ComfyUIImage）会被漏掉。
        #   改为扫「父目录」找齐所有实例，并默认选中用户点的这一个（2026-10-25 用户实测反馈）。
        from app.config import is_comfyui_dir
        _parent = os.path.dirname(root.rstrip("\\/"))
        _is_drive_root = len(_parent.rstrip("\\/")) <= 2
        if (not _is_drive_root and _parent and os.path.isdir(_parent)
                and _parent != root and is_comfyui_dir(root)):
            self._scan_parent = True
            self._prefer_abs = root            # 扫完默认选中用户点的这个实例
            if not self.ed_name.text().strip():
                self.ed_name.setText(os.path.basename(root) or "")
            self.lb_scan.setStyleSheet("color:#9aa4b2;")
            self.lb_scan.setText(
                f"该路径是现成实例，正在检查同级目录 {_parent} 里的其他实例…")
            self.lb_scan.show()
            self._start_scan(_parent)
            return
        # 中文/非 ASCII 路径检测：ComfyUI/pip/torch 对中文路径极易出玄学问题
        try:
            root.encode("ascii")
            self.lb_warn.hide()
        except UnicodeEncodeError:
            self.lb_warn.setText(
                "⚠️ 安装路径包含中文或非 ASCII 字符！ComfyUI / pip / PyTorch 在中文路径下"
                "极易出现依赖安装失败、启动报错等玄学问题，强烈建议改用纯英文路径"
                "（例如 F:////ComfyUI）。")
            self.lb_warn.show()
        # 实例名默认 = 用户选择的根目录名（可手动改）
        if not self.ed_name.text().strip():
            self.ed_name.setText(os.path.basename(root) or "")
        self.lb_scan.setText("正在扫描，请稍候…")
        self.lb_scan.setStyleSheet("color:#9aa4b2;")
        self.lb_scan.show()
        self._start_scan(root)

    def _on_scan_done(self, root, candidates, seq=None):
        # 过期扫描结果（用户已切到别的路径/发起新扫描）→ 直接忽略
        if seq is not None and seq != getattr(self, "_scan_seq", 0):
            return
        self._busy_scan = False
        try:
            self.pbar_scan.hide()      # 进度动画收起（结论文案接管）
        except Exception:
            pass
        # 记住扫描根（ed_dir 之后会被 _sync_path_to_candidate 改成候选路径，范围文案要用真根）
        self._scan_root = root
        candidates = list(dict.fromkeys(candidates))
        p1 = self.page(0)
        # 父目录扫描模式：用户输入的路径不存在，在这里扫到了兄弟实例
        if getattr(self, "_scan_parent", False):
            self._scan_parent = False
            if candidates:
                # 落到正常候选流程（root 就是父目录，rel 显示各子目录名）
                pass
            else:
                # 上级目录也没有实例 → 回到"将在此全新安装"（用户输入的路径）
                _orig = getattr(self, "_orig_root", root)
                # 无实例 → 安装目标补一层 ComfyUI（除非原路径已含 comfyui 或就是盘根）
                if "comfyui" not in os.path.basename(_orig.rstrip("\\/")).casefold():
                    _orig = os.path.join(_orig, "ComfyUI")
                self.ed_dir.setText(_orig)
                self._lock_inputs(False)
                self.cb_found.hide()
                self.lb_found_title.hide()
                self._set_mode("fresh")
                self._apply_fresh_buttons()
                self.lb_scan.setStyleSheet("color:#e8a33d;")
                self.lb_scan.setText(
                    f"🔍 已扫描 {root} 的全部子目录，未发现现成实例\n"
                    f"点「下一步」将在此位置全新安装：{_orig}\n"
                    "（现在不会动你的磁盘，安装开始时才创建）")
                p1.setFinalPage(False)
                self.button(QWizard.NextButton).setEnabled(True)
                self.button(QWizard.FinishButton).setEnabled(False)
                return
        if candidates:
            self.cb_found.clear()
            # ★ 固定第 0 项「新建新实例」：已有实例列表顶部总留一个全新安装入口
            #   （2026-10-24 用户要求：找到实例时也允许直接选新建，而不是没得选）
            self.cb_found.addItem("➕ 新建新实例…")
            for c in candidates:
                rel = os.path.relpath(c, root)
                self.cb_found.addItem(rel if rel != "." else "(此目录)")
            # 默认选中：优先选用户点的那个实例（扫父目录带出兄弟时，idx 1 不一定是它）；
            # 否则退回第一个【实例】（idx 1）——扫描出实例时主动线是添加已有
            # （在 connect 槽之前设置，不触发 _on_candidate_changed，下方手动跑一遍流程）
            _prefer = getattr(self, "_prefer_abs", "")
            self._prefer_abs = ""              # 用完即清，不影响后续手动切换
            _default_idx = 1
            if _prefer:
                _pnorm = os.path.normpath(_prefer).casefold()
                for _i, _c in enumerate(candidates):
                    if os.path.normpath(_c).casefold() == _pnorm:
                        _default_idx = _i + 1   # +1：下拉第 0 项是「新建新实例」
                        break
            self.cb_found.setCurrentIndex(_default_idx)
            # 恒可点（至少有"新建 + 1 个实例"两项）
            self.cb_found.setEnabled(True)
            self.cb_found.show()
            # 明确提示可下拉选择（多个时带数量），否则单看下拉框不知道还能切
            if len(candidates) > 1:
                self.lb_found_title.setText(f"请选择实例（共 {len(candidates)} 个）:")
            else:
                self.lb_found_title.setText("请选择实例:")
            self.lb_found_title.show()
            self._set_mode("existing")
            def _on_candidate_changed(_i):
                # 连接前已 disconnect() 全部旧槽（见下），这里不再重复断开——
                # 否则会把"正在执行的自己"断掉，下次切换就失联了
                self._sync_path_to_candidate()
                if self._scan_candidates:
                    self._begin_verify()

            # ★ 多轮扫描会重复连接同名槽 → 每次连接前全断（切一次候选触发 N 次验证的隐患）
            try:
                self.cb_found.currentIndexChanged.disconnect()
            except Exception:
                pass
            self.cb_found.currentIndexChanged.connect(_on_candidate_changed)
            n_py = self._match_pythons(candidates)
            # ★ 必须先赋值再同步（否则 _sync_path_to_candidate 读到空列表直接 return）
            self._scan_candidates = candidates
            self._scan_pys = self._py_map
            self._sync_path_to_candidate()
            # 验证选中候选能否运行——通过才解锁「完成」
            self._phase = "scan_verify"
            self.lb_scan.setStyleSheet("color:#9aa4b2;")
            self.lb_scan.setText(
                f"🔍 已扫描 {root} 的全部子目录：发现 {len(candidates)} 个实例、"
                f"{n_py} 个 Python 解释器\n正在验证能否运行…")
            self._begin_verify()
            return
        self.cb_found.hide()
        self.lb_found_title.hide()
        # ★ 安装目标规则（检测确认此处没有实例后才走这里）——推导统一收进 _derive_target
        target = self._derive_target(root)
        if not os.path.isfile(os.path.join(target, "main.py")):
            self.ed_dir.setText(target)
        self._set_mode("fresh")
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        self.lb_scan.setText(
            f"🔍 已扫描 {root} 的全部子目录，未发现 ComfyUI 实例\n"
            f"点「下一步」将全新安装到：{target}\n"
            "（不会自动套子目录；如想换位置请用上方「浏览…」）")
        self._lock_inputs(False)
        # 无候选 = 确定的全新安装状态：末尾统一校正按钮语义（防止早前 existing 状态残留）
        self._apply_fresh_buttons()

    def _derive_target(self, root):
        """全新安装目标推导（唯一真相源，fresh 分支与下拉「新建」共用）：
        · 盘根（F:\\）          → F:\\ComfyUI
        · 目录名已含 comfyui    → 就用它
        · 其余（如 D:\\AI）      → 补一层 \\ComfyUI，避免源码散进现有目录"""
        _norm = root.rstrip("\\/")
        _base = os.path.basename(_norm).casefold()
        if len(_norm) <= 2 and _norm[1:2] == ":":        # "F:" 这种盘根
            return os.path.join(_norm + os.sep, "ComfyUI")
        if "comfyui" in _base:
            return _norm
        return os.path.join(_norm, "ComfyUI")

    def _scope_txt(self):
        """扫描范围行：'已扫描 X 的全部子目录，发现 N 个实例'（验证/失败文案统一前缀，
        让用户随时知道扫描过什么、找到几个——2026-10-24 用户要求）"""
        try:
            _root = os.path.normpath(
                getattr(self, "_scan_root", "") or self.ed_dir.text().strip())
            n = len(getattr(self, "_scan_candidates", None) or [])
            if not n:
                return ""
            return f"🔍 已扫描 {_root} 的全部子目录：发现 {n} 个实例\n"
        except Exception:
            return ""

    def _enter_fresh_new(self):
        """下拉选中「新建新实例」：回到全新安装形态（展开安装参数、下一步=进安装页）。
        新建基准取用户输入的原始路径 _orig_root（不是 ed_dir——它可能已被候选同步改写）"""
        base = (getattr(self, "_orig_root", "") or getattr(self, "_scan_root", "")
                or self.ed_dir.text().strip() or "F:\\")
        target = self._derive_target(base)
        try:
            self.pbar_scan.hide()
        except Exception:
            pass
        self.ed_dir.setText(target)
        self._set_mode("fresh", keep_picker=True)   # 保留下拉：切回实例还要用
        self._apply_fresh_buttons()
        self._lock_inputs(False)
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        self.lb_scan.setText(
            f"🆕 已选「新建新实例」：点「下一步」将在此全新安装到\n{target}\n"
            "（现在不会动你的磁盘，安装开始时才创建；上方安装参数可按需调整）")
        self.lb_scan.show()

    def _begin_verify(self):
        """后台验证选中候选的 ComfyUI 能否运行；通过才解锁「完成」。
        多个 Python 候选时逐个试，选第一个真正能 import torch 的"""
        from app import bootstrap
        comfy = self._selected_candidate()
        if comfy is None:
            # 下拉选中「新建新实例」→ 全新安装形态，无需验证
            self._enter_fresh_new()
            return
        # ★ 收起安装参数（从「新建新实例」切回实例时，fresh 展开的安装区必须收回）
        #   绿字同步为"验证中"——避免验证期间显示"点完成"的错误承诺
        self._set_mode("existing", note="🔗 添加已有实例（正在验证能否运行，通过后点「完成」注册）")
        # 缓存命中：这个实例之前验证通过过，直接复用结果（切换候选不再等 2~4 秒）
        _cached_py = self._verify_cache.get(comfy)
        if _cached_py and os.path.isfile(_cached_py):
            self._verify_comfy = comfy
            self.lb_scan.setStyleSheet("color:#4cd47c;")
            self.lb_scan.setText(
                f"{self._scope_txt()}✅ 验证通过（已缓存）\n使用解释器: {_cached_py}\n"
                "选好候选后点「完成」直接添加为实例（免下载）。")
            self.lb_mode.setStyleSheet("color:#4cd47c; font-weight:600;")
            self.lb_mode.setText("🔗 添加已有实例（验证通过，点「完成」直接注册，不会下载任何东西）")
            try:
                self.b_skip_repair.hide()
            except Exception:
                pass
            p1 = self.page(0)
            p1.setFinalPage(True)
            self.setButtonText(QWizard.NextButton, "完成")
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.FinishButton).hide()
            self._repair_mode = False          # ★ 验证通过必须清掉失败态残留的修复标记
            self._lock_inputs(False)
            return
        self.button(QWizard.FinishButton).setEnabled(False)
        self.button(QWizard.NextButton).setEnabled(False)
        # 验证要真跑 python（import torch 等，约 2~4 秒）——必须明确告知，否则像卡住
        self.lb_scan.setStyleSheet("color:#9aa4b2;")
        self.lb_scan.setText(
            f"{self._scope_txt()}⏳ 正在验证「{os.path.basename(comfy)}」能否运行"
            "（约 2~4 秒，需实际加载 torch）…")
        self.lb_scan.show()
        # key 可能是 normpath 形式；再兜底用共享便携 Python（它带 torch，通常能直接跑）
        cands = (self._py_map.get(comfy)
                 or self._py_map.get(os.path.normpath(comfy))
                 or [])
        if not cands:
            _tools_py = os.path.join(bootstrap.tools_root(), "python", "python.exe")
            if os.path.isfile(_tools_py):
                cands = [os.path.normpath(_tools_py)]
        self._verify_comfy = comfy

        def _try_all():
            last = ""
            tried = 0
            # 防御：cands 万一不是列表（历史残留的字符串），先规整成列表
            _cl = cands if isinstance(cands, (list, tuple)) else ([cands] if cands else [])
            for py in _cl:
                # ★ 跳过不存在的解释器（否则会把"未找到 Python 解释器"当成实例本身的问题）
                if not py or not os.path.isfile(py):
                    continue
                tried += 1
                ok, info = bootstrap.verify_comfyui_runnable(py, comfy)
                if ok:
                    return (True, info, py)
                # 错误信息用短路径，避免界面截断成一串没头没尾的字符
                short = os.path.basename(os.path.dirname(os.path.dirname(py))) or py
                last = f"{short}: {info}"
            if tried == 0:
                return (False,
                        "没有可用的 Python 解释器（共享便携 Python 也未安装）——"
                        "可先添加实例，之后在主界面「实例设置」里指定", "")
            return (False, last, "")

        swap_worker(self, "_verify_worker", StepWorker(_try_all))
        self._verify_worker.sig_done.connect(self._on_verify2_done)
        self._verify_worker.start()

    def _on_verify2_done(self, ok, res, err):
        p1 = self.page(0)
        if not ok:
            self._verify_fail(f"验证执行失败: {err}")
            return
        v_ok, info, py = res
        if v_ok:
            # ★ py_map 的值必须是【列表】。此前这里无条件赋值成单个字符串，
            #   导致下轮验证 `for py in cands` 变成逐字符遍历 → 全部判"不存在"
            #   → 误报"没有可用的 Python 解释器"（切换候选时必现）
            self._verify_cache[self._verify_comfy] = py   # 记入缓存，切回来秒过
            _lst = self._py_map.setdefault(self._verify_comfy, [])
            if not isinstance(_lst, list):
                _lst = [_lst]
            if py and py not in _lst:
                _lst.insert(0, py)      # 验证通过的排在首位
            self._py_map[self._verify_comfy] = _lst
            self._lock_inputs(False)
            self.lb_scan.setStyleSheet("color:#4cd47c;")
            self.lb_scan.setText(
                f"{self._scope_txt()}✅ {info}\n使用解释器: {py}\n"
                "选好候选后点「完成」直接添加为实例（免下载）。")
            self.lb_mode.setStyleSheet("color:#4cd47c; font-weight:600;")
            self.lb_mode.setText("🔗 添加已有实例（验证通过，点「完成」直接注册，不会下载任何东西）")
            try:
                self.b_skip_repair.hide()
            except Exception:
                pass
            # Qt 的 Finish 按钮只认"向导最后一页"，手动 setEnabled 有时会被 Qt 收回。
            # 标准做法：把本页设为 final page，Next 按钮即"完成"，点它触发 accept()。
            p1.setFinalPage(True)
            self.setButtonText(QWizard.NextButton, "完成")
            self.button(QWizard.NextButton).setEnabled(True)     # 本页的「完成」= Next
            self.button(QWizard.FinishButton).hide()             # 避免两个"完成"按钮并存
        else:
            self._verify_fail(info)

    def _skip_repair(self):
        """逃生口：跳过修复、直接带病添加（高级用户自选；主推路径是「下一步：修复」）"""
        self._repair_mode = False           # 明确走"仍要添加"直接完成，不进修复
        p1 = self.page(0)
        p1.setFinalPage(True)
        self.setButtonText(QWizard.NextButton, "仍要添加")
        self.button(QWizard.NextButton).setEnabled(True)
        self.button(QWizard.FinishButton).hide()
        self.b_skip_repair.hide()
        self.lb_mode.setStyleSheet("color:#e8a33d; font-weight:600;")
        self.lb_mode.setText(
            "🔗 已选择跳过修复：点「仍要添加」直接注册（之后到主界面「依赖管理」再补）")

    def _verify_fail(self, why):
        """验证未通过：**保留添加实例的能力**（多数情况只是找不到解释器，实例本身是好的）。
        ★ 绝不擅自切「全新安装」——那会在已有实例目录里重新拉源码，覆盖用户文件。
        2026-10-24 起：缺依赖/无 Python 的主路径改为「下一步：修复」（进修复流程，
        能运行才解锁「完成」）；「仍要添加」降级为下方低调逃生口按钮。
        """
        self._lock_inputs(False)
        # 失败原因三级分类：
        #   ① 实例本身坏（无 main.py/comfy）      → 禁止添加
        #   ② 根本没有任何 Python 环境            → 引导修复（装共享 Python）
        #   ③ 有 Python 但缺依赖（缺 torch 等）   → 引导修复（补装依赖）
        _no_python = ("没有可用的 Python" in why) or ("共享便携 Python 也未安装" in why) \
                     or ("未找到 Python 解释器" in why)
        _instance_broken = ("main.py" in why) or ("源码" in why) or ("不是 ComfyUI" in why)
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        if _no_python:
            # 没有任何 Python 环境：主推「下一步：修复」——修复流程会装共享便携 Python。
            # 绝不切 fresh 模式，防止在实例目录上误触重新安装覆盖源码。
            self.lb_scan.setStyleSheet("color:#e8635a;")
            self.lb_scan.setText(
                f"{self._scope_txt()}⚠️ 暂未找到 Python 环境：{why}\n"
                "→ 点「下一步：修复」将自动安装便携 Python 并验证实例能否运行；\n"
                "→ 或点下方「仍要添加（跳过修复）」先加进列表，之后到主界面「工具管理」"
                "安装共享便携 Python /「实例设置」指定解释器。\n"
                "（修复与添加都不会改动你选的实例目录）")
            self._set_mode("existing")
            # 绿字跟随按钮状态（不再是写死的"点完成"）
            self.lb_mode.setStyleSheet("color:#e8635a; font-weight:600;")
            self.lb_mode.setText("🔗 该实例缺少 Python 环境：点「下一步：修复」自动补装")
            p1 = self.page(0)
            p1.setFinalPage(False)                                # 修复路径 = 前进，不是完成
            self.setButtonText(QWizard.NextButton, "下一步：修复")
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.FinishButton).hide()
            self.b_skip_repair.show()
            self._repair_mode = True        # 点「下一步：修复」→ p2 跑修复计划
            return
        if _instance_broken:
            self.lb_scan.setText(
                f"{self._scope_txt()}❌ 该目录不是可用的 ComfyUI 实例：{why}\n"
                "请确认是否选错了目录（应包含 main.py 与 comfy/ 目录）。")
            self._set_mode("fresh")
            self.b_skip_repair.hide()
            p1 = self.page(0)
            p1.setFinalPage(False)
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.FinishButton).setEnabled(False)
            return
        self.lb_scan.setText(
            f"{self._scope_txt()}⚠️ 实例本身正常，但当前解释器缺依赖：{why}\n"
            "→ 点「下一步：修复」将自动补装缺失依赖（如 PyTorch）并重新验证；\n"
            "→ 或点下方「仍要添加（跳过修复）」先加进列表，之后到主界面「依赖管理」补齐。")
        self._set_mode("existing")      # 保持"添加实例"形态：不显示安装参数
        self.lb_mode.setStyleSheet("color:#e8635a; font-weight:600;")
        self.lb_mode.setText("🔗 该实例缺依赖：点「下一步：修复」自动补装后再验证")
        p1 = self.page(0)
        p1.setFinalPage(False)                                  # 修复路径 = 前进，不是完成
        self.setButtonText(QWizard.NextButton, "下一步：修复")     # 主推：能运行才能完成
        self.button(QWizard.NextButton).setEnabled(True)
        self.button(QWizard.FinishButton).hide()
        self.b_skip_repair.show()
        self._repair_mode = True            # 点「下一步：修复」→ p2 跑修复计划

    def _match_pythons(self, candidates):
        """为每个候选 ComfyUI 找配套 Python，构建 {comfy_path: [候选列表]}
        共享便携 Python（tools）永远排第一位——它就是为所有实例准备的"""
        py_map = {}
        total = set()
        tools_py = os.path.join(bootstrap.tools_root(), "python", "python.exe")
        for cp in candidates:
            cands = []
            if os.path.isfile(tools_py):
                cands.append(os.path.normpath(tools_py))   # 共享便携优先
            cands += [os.path.join(cp, "python.exe"),
                     os.path.join(cp, "python", "python.exe"),
                     os.path.join(cp, "venv", "Scripts", "python.exe"),
                     os.path.join(cp, ".venv", "Scripts", "python.exe"),
                     os.path.join(cp, "python_embeded", "python.exe"),
                     os.path.join(cp, "..", "python.exe"),
                     os.path.join(cp, "..", "python", "python.exe"),
                     # 整合包布局：实例在 ComfyUI\ 子目录，Python 在根目录
                     # python_embeded\（ComfyUI_windows_portable 官方包）
                     os.path.join(cp, "..", "python_embeded", "python.exe")]
            cands = [os.path.normpath(p) for p in cands if os.path.isfile(p)]
            if not cands:
                parent = os.path.dirname(cp)
                try:
                    for d1 in os.listdir(parent):
                        base = os.path.join(parent, d1)
                        for sub in ("", "Scripts", "python"):
                            p = os.path.join(base, sub, "python.exe")
                            if os.path.isfile(p):
                                cands.append(os.path.normpath(p))
                except OSError:
                    pass
            # 排除打包/构建环境（.venv_build 等）——它们不是给 ComfyUI 用的运行时
            _skip = (".venv_build", "venv_build", "build_env", "_env_build")
            cands = [c for c in cands if not any(s in c.lower() for s in _skip)]
            for p in cands:
                total.add(p)
            if cands:
                py_map[os.path.normpath(cp)] = cands   # key 规范化，避免 normpath 差异导致取不到
        self._py_map = py_map
        return len(total)

    def _prepare_result(self):
        """组装 setup_result：扫描分支（检测直接注册）在此组装；
        安装分支的 setup_result 已由 _on_done 设置，这里不动它"""
        comfy = self._selected_candidate()
        # ★ 不能用 cb_found.isVisible() 做守卫（2026-10-26 修）：本函数会在
        #   currentIdChanged 换页信号里被调用，此时 Qt 已 hide 第 0 页 →
        #   所有子控件 isVisible()=False → 组装不出结果 → 点「完成」无反应。
        #   _selected_candidate() 非 None 已说明选中了实例候选，足够判据。
        if comfy:
            # ★ 优先用「验证通过」的解释器（_verify_cache）——_py_map[0] 是共享便携
            #   排序位，未必是实际验证过的那一个
            python_exe = self._verify_cache.get(comfy) or ""
            if not (python_exe and os.path.isfile(python_exe)):
                pyv = self._py_map.get(comfy) or self._py_map.get(os.path.normpath(comfy)) or ""
                python_exe = pyv[0] if isinstance(pyv, list) else pyv
            # 无配套 Python 也允许添加（python_path 留空）：
            # 与「仍要添加」的承诺一致——先加进列表，之后在实例设置里再指定解释器
            self.setup_result = {
                "comfyui_path": comfy,
                "python_path": python_exe,
                "name": self.ed_name.text().strip() or os.path.basename(comfy),
                "skipped_install": True,
            }

    def done(self, r):
        """QWizard「完成」按钮触发 done(Accepted)——组装 setup_result 后关闭。
        组装失败时区分两种情况：
        · 后台检测/验证还在跑 → 提示稍候，向导保持打开（绝不静默关闭）
        · 确认无候选 → 拒绝关闭"""
        if r == QWizard.Accepted:
            # ★ busy 检查必须放在最前、且必须涵盖安装线程——只要有任何后台线程
            #   在跑就绝不允许关闭（2026-10-24 用户实测：安装没完成点了「完成」
            #   向导直接关掉，安装被打断；此前只查了 scan/verify 两个线程）
            _p2 = getattr(self, "p2", None)
            _install_running = any(
                w is not None and hasattr(w, "isRunning") and w.isRunning()
                for w in (getattr(_p2, "worker", None),
                          getattr(_p2, "_verify_worker", None)))
            busy = _install_running or any(
                getattr(self, a, None) is not None
                and hasattr(getattr(self, a), "isRunning")
                and getattr(self, a).isRunning()
                for a in ("_scan_worker", "_verify_worker"))
            if busy:
                QMessageBox.information(
                    self, "ComfyHub",
                    "⏳ 正在检测/验证/安装环境，请稍候……\n"
                    "全部完成后再点「完成」（扫描验证通常只需几秒，"
                    "安装视网络约几分钟）。")
                return   # 向导保持打开，不静默关闭
            self._prepare_result()
            if self.setup_result and self.setup_result.get("comfyui_path"):
                super().done(r)
                return
            super().reject()
            return
        super().done(r)

    def accept(self):
        """代码路径 accept：转交 done() 统一处理（done 不再回头调 accept）"""
        self.done(QWizard.Accepted)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "选择安装位置", self.ed_dir.text())
        if d:
            # ★ 不再无条件拼 \\ComfyUI（2026-10-26 修：选 F:\\ 会被拼成不存在的
            #   F:\\ComfyUI，且把「添加实例」误判成「全新安装」）。原样填入选中目录，
            #   交给 _detect_existing 统一决策：全深度扫描有实例 → 添加模式列候选；
            #   没有 → _derive_target 推导安装目标（盘根/普通目录补 \\ComfyUI、
            #   目录名含 comfyui 则原样——与手动输入框同一口径）
            self.ed_dir.setText(d)
            self._detect_existing()   # 每次选择文件夹自动扫描

    def _on_page_change(self, page_id: int):
        # 进入进度页时自动开跑（page_id 是 int，必须换算成页对象再比）
        if self.page(page_id) is self.p2 and not self._started:
            # ★★★ 添加已有实例模式绝不允许进安装页（2026-10-26 用户实测踩到：
            #   aki 整合包验证通过点「完成」，Qt 却翻页到安装页，cb_python 无数据
            #   → py=None 跑全新安装步骤1 → 报"未检测到 Python 解释器"卡死）。
            #   existing 模式下「完成」的语义 = 注册实例并关闭向导。
            if (not getattr(self, "_repair_mode", False)
                    and self.install_opts.isHidden()):
                self._prepare_result()
                if self.setup_result and self.setup_result.get("comfyui_path"):
                    QTimer.singleShot(0, self.accept)   # 直接完成，不启动安装
                else:
                    # 兜底：结果没组装出来（理论不可达）——回第一页，别卡在安装页
                    self.setCurrentId(0)
                return
            self._started = True
            # ★ 修复模式分流：失败态点「下一步：修复」→ 跑修复计划（补依赖/装 Python），
            #   而不是全新安装（否则会把用户已选路径当成安装目标去拉源码）
            if getattr(self, "_repair_mode", False):
                comfy = self._selected_candidate() or getattr(
                    self, "_verify_comfy", "") or self.ed_dir.text().strip()
                py_hint = ""
                _cands = self._py_map.get(comfy) or self._py_map.get(
                    os.path.normpath(comfy)) or []
                if isinstance(_cands, (list, tuple)):
                    for p in _cands:
                        if p and os.path.isfile(p):
                            py_hint = p
                            break
                self.p2.start_repair(comfy, py_hint,
                                     self.cb_mirror.currentText(),
                                     self.ed_proxy.text().strip(),
                                     self._on_done)
                return
            target = self.ed_dir.text().strip()
            py = self.cb_python.currentData()
            mirror = self.cb_mirror.currentText()
            proxy = self.ed_proxy.text().strip()
            self.p2.start(target, py, mirror, proxy, self.ck_torch.isChecked(),
                          self._on_done)

    def _on_done(self, ok: bool, res: dict, err: str):
        # ★ 修复模式：完成不关向导——回第一页重新验证，通过才解锁「完成」
        #   （2026-10-24 用户要求"要达到能运行才能点完成"）
        if getattr(self, "_repair_mode", False):
            if ok:
                self._repair_mode = False
                self._started = False        # 复位：重验若仍失败，可再次点「下一步：修复」
                self._verify_cache.pop(res.get("comfyui_path", ""), None)  # 旧失败缓存清除（一般本就没有）
                self.p2.lb_step.setText("✅ 修复完成——正在返回并重新验证实例…")
                self.button(QWizard.BackButton).setEnabled(True)
                self.setCurrentId(0)         # 回第一页
                QTimer.singleShot(400, self._begin_verify)   # 稍候重验（能运行才算数）
            else:
                self._started = False        # 允许重试（再点「下一步：修复」）
                self.button(QWizard.BackButton).setEnabled(True)
                QMessageBox.critical(
                    self, "修复失败",
                    f"错误信息：\n{err}\n\n详情见日志（帮助/日志目录），可返回重试。")
            return
        if ok:
            res["name"] = self.ed_name.text().strip()
            self.setup_result = res
            self.p2.lb_step.setText("✅ 安装完成！点击“完成”自动添加为实例。")
            self.button(QWizard.FinishButton).setEnabled(True)
            self.button(QWizard.CancelButton).setEnabled(False)
        else:
            QMessageBox.critical(self, "安装失败", f"错误信息：\n{err}\n\n详情见日志，可修复后重试。")
            self.button(QWizard.BackButton).setEnabled(True)
