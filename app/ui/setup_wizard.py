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
        # GPU 加速验证
        try:
            r = bootstrap.run([self.state["python_path"], "-c",
                "import torch;print('CUDA_OK' if torch.cuda.is_available() else 'CUDA_NO')"],
                capture_output=True, text=True, timeout=120)
            out = (r.stdout or "").strip()
            log(f"torch 安装完成，GPU 加速: {'✅ 可用' if 'CUDA_OK' in out else '❌ 不可用'}")
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
        color = {"ok": "#4cd47c", "missing": "#e8635a", "cpu": "#e8a33d",
                 "outdated": "#e8a33d"}.get(status, "#9aa4b2")
        self.lb_state.setStyleSheet(f"color:{color};")
        self.state.update(payload)
        icon = {"ok": "✅", "missing": "❌", "cpu": "⚠️", "outdated": "⚠️"}.get(status, "")
        self.lb_state.setText(f"{icon} {info}")
        if status == "ok":
            self._show_decision("ok", info)
        elif status == "missing":
            self._show_decision("missing", info)
        else:  # cpu / outdated
            self._show_decision("update", info)

    def _show_decision(self, kind, info):
        # 自动模式（全新安装）：不等待点击，直接执行默认动作
        if getattr(self, "_auto", False) and kind in ("ok", "missing", "update"):
            from PySide6.QtCore import QTimer as _QT
            if kind == "ok":
                _QT.singleShot(200, self._on_skip)
            else:
                _QT.singleShot(200, self._on_act)
        """kind: ok=建议跳过 / missing=需安装 / update=建议更新 / retry=检测失败重试"""
        wiz = self.wizard()
        if wiz:
            wiz.button(QWizard.BackButton).setEnabled(True)   # 决策阶段允许返回
        self.b_act.show()
        self.b_skip.show()
        if kind == "ok":
            self.b_act.setText("重新安装")
            self.b_skip.setText("跳过此步 ▶")
            self.b_skip.setDefault(True)
        elif kind == "missing":
            self.b_act.setText("📦 安装")
            self.b_skip.setText("跳过此步 ▶")
            self.b_act.setDefault(True)
            # 必需步骤缺失时禁止跳过（跳过=环境残缺，后续启动必失败）
            # 仅 PyTorch 步骤（最后一步，索引 == total-1）允许跳过
            is_optional = getattr(self, "cur", 0) >= self.state.get("total", 4) - 1
            self.b_skip.setEnabled(is_optional)
            if not is_optional:
                self.b_skip.setText("此步必需，不可跳过")
        if kind != "missing":
            self.b_skip.setEnabled(True)
        elif kind == "update":
            self.b_act.setText("⬆ 更新")
            self.b_skip.setText("跳过（保留现状）▶")
            self.b_act.setDefault(True)
        else:
            self.b_act.setText("🔄 重试")
            self.b_skip.setText("跳过此步 ▶")
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
        self._verify_worker = StepWorker(
            lambda: bootstrap.verify_comfyui_runnable(py, cp))
        self._verify_worker.sig_done.connect(self._on_final_verify)
        self._verify_worker.start()

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

    def _on_log(self, msg):
        self.log.appendPlainText(msg)
        from PySide6.QtGui import QTextCursor
        self.log.moveCursor(QTextCursor.End)
        # 同步落盘（失败静默，不打扰安装）
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
            _fresh = not self.install_opts.isHidden()
            _next = self.button(QWizard.NextButton)
            if _fresh:
                if _next.text().strip() == "完成":
                    self.setButtonText(QWizard.NextButton, "下一步 >")
                if not _next.isEnabled():
                    _next.setEnabled(True)
            else:
                _txt = self.lb_scan.text()
                if _txt.startswith("❌ 没有可用的 Python") or _txt.startswith("❌ 该目录不是"):
                    return
                if _next.text().strip() not in ("仍要添加", "完成"):
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
        except Exception:
            pass

    def _set_mode(self, mode: str):
        """统一控制两种模式的界面差异。
        existing = 添加已有实例：只需要选目录 + 验证，不显示任何安装参数
        fresh    = 全新安装：显示 Python 环境 / 镜像 / 代理 / torch 等安装参数
        """
        if mode == "existing":
            self.install_opts.hide()          # 添加已有实例不需要安装参数
            if hasattr(self, "lb_mode"):
                self.lb_mode.setStyleSheet("color:#4cd47c; font-weight:600;")
                self.lb_mode.setText("🔗 添加已有实例（不会下载任何东西，点「完成」直接注册）")
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

    def _sync_path_to_candidate(self):
        """把「安装到」/「实例名称」同步为当前选中候选的真实路径。
        否则路径框会停在用户输入的（可能不存在的）路径，与实际实例不一致"""
        try:
            idx = max(0, self.cb_found.currentIndex())
            cands = getattr(self, "_scan_candidates", None) or []
            if not cands:
                return
            real = cands[min(idx, len(cands) - 1)]
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
        """
        self._scan_seq = getattr(self, "_scan_seq", 0) + 1
        _seq = self._scan_seq
        swap_worker(self, "_scan_worker", ScanWorker(path))
        self._scan_worker.sig_done.connect(
            lambda root, cands, s=_seq: self._on_scan_done(root, cands, s))
        self._scan_worker.start()

    def _detect_existing(self):
        """后台扫描目录（含两层子目录）：找到 ComfyUI 直接注册；没找到自动补全并展开安装选项"""
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
                    f"📁 未发现现成实例（已检查上级目录 {root}）\n"
                    f"点「下一步」将在此位置全新安装：{_orig}\n"
                    "（现在不会动你的磁盘，安装开始时才创建）")
                p1.setFinalPage(False)
                self.button(QWizard.NextButton).setEnabled(True)
                self.button(QWizard.FinishButton).setEnabled(False)
                return
        if candidates:
            self.cb_found.clear()
            for c in candidates:
                rel = os.path.relpath(c, root)
                self.cb_found.addItem(rel if rel != "." else "(此目录)")
            self.cb_found.setEnabled(len(candidates) > 1)
            self.cb_found.show()
            self.lb_found_title.show()
            self._set_mode("existing")
            def _on_candidate_changed(_i):
                self._sync_path_to_candidate()
                if self._scan_candidates:
                    self._begin_verify()

            self.cb_found.currentIndexChanged.connect(_on_candidate_changed)
            n_py = self._match_pythons(candidates)
            # ★ 必须先赋值再同步（否则 _sync_path_to_candidate 读到空列表直接 return）
            self._scan_candidates = candidates
            self._scan_pys = self._py_map
            self._sync_path_to_candidate()
            # 验证选中候选能否运行——通过才解锁「完成」
            self._phase = "scan_verify"
            self.lb_scan.setStyleSheet("color:#9aa4b2;")
            self.lb_scan.setText(f"检测到 {len(candidates)} 个 ComfyUI 环境和 {n_py} 个 Python 解释器，正在验证能否运行…")
            self._begin_verify()
            return
        self.cb_found.hide()
        self.lb_found_title.hide()
        # ★ 安装目标规则（检测确认此处没有实例后才走这里）：
        #   · 选中盘根（F:\）            → F:\ComfyUI
        #   · 目录名已含 comfyui          → 就用它（用户明确指定了安装目录）
        #   · 其余（如程序目录、D:\AI）  → 补一层 \ComfyUI，避免源码散进现有目录
        _norm = root.rstrip("\\/")
        _base_name = os.path.basename(_norm).casefold()
        if len(_norm) <= 2 and _norm[1:2] == ":":        # "F:" 这种盘根
            target = os.path.join(_norm + os.sep, "ComfyUI")
        elif "comfyui" in _base_name:
            target = root
        else:
            target = os.path.join(root, "ComfyUI")
        if not os.path.isfile(os.path.join(target, "main.py")):
            self.ed_dir.setText(target)
        self._set_mode("fresh")
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        self.lb_scan.setText(
            f"📁 空文件夹：未检测到 ComfyUI 实例，点「下一步」将安装到\n"
            f"{target}\n"
            "（不会自动套子目录；如想换位置请用上方「浏览…」）")
        self._lock_inputs(False)
        # 无候选 = 确定的全新安装状态：末尾统一校正按钮语义（防止早前 existing 状态残留）
        self._apply_fresh_buttons()

    def _begin_verify(self):
        """后台验证选中候选的 ComfyUI 能否运行；通过才解锁「完成」。
        多个 Python 候选时逐个试，选第一个真正能 import torch 的"""
        from app import bootstrap
        idx = max(0, self.cb_found.currentIndex())
        comfy = self._scan_candidates[min(idx, len(self._scan_candidates) - 1)]
        # 缓存命中：这个实例之前验证通过过，直接复用结果（切换候选不再等 2~4 秒）
        _cached_py = self._verify_cache.get(comfy)
        if _cached_py and os.path.isfile(_cached_py):
            self._verify_comfy = comfy
            self.lb_scan.setStyleSheet("color:#4cd47c;")
            self.lb_scan.setText(
                f"✅ 验证通过（已缓存）\n使用解释器: {_cached_py}\n"
                "选好候选后点「完成」直接添加为实例（免下载）。")
            p1 = self.page(0)
            p1.setFinalPage(True)
            self.setButtonText(QWizard.NextButton, "完成")
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.FinishButton).hide()
            self._lock_inputs(False)
            return
        self.button(QWizard.FinishButton).setEnabled(False)
        self.button(QWizard.NextButton).setEnabled(False)
        # 验证要真跑 python（import torch 等，约 2~4 秒）——必须明确告知，否则像卡住
        self.lb_scan.setStyleSheet("color:#9aa4b2;")
        self.lb_scan.setText(
            f"⏳ 正在验证「{os.path.basename(comfy)}」能否运行（约 2~4 秒，需实际加载 torch）…")
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
            self.lb_scan.setText(f"✅ {info}\n使用解释器: {py}\n选好候选后点「完成」直接添加为实例（免下载）。")
            # Qt 的 Finish 按钮只认"向导最后一页"，手动 setEnabled 有时会被 Qt 收回。
            # 标准做法：把本页设为 final page，Next 按钮即"完成"，点它触发 accept()。
            p1.setFinalPage(True)
            self.setButtonText(QWizard.NextButton, "完成")
            self.button(QWizard.NextButton).setEnabled(True)     # 本页的「完成」= Next
            self.button(QWizard.FinishButton).hide()             # 避免两个"完成"按钮并存
        else:
            self._verify_fail(info)

    def _verify_fail(self, why):
        """验证未通过：**保留添加实例的能力**（多数情况只是找不到解释器，实例本身是好的）。
        ★ 绝不擅自切「全新安装」——那会在已有实例目录里重新拉源码，覆盖用户文件。
        """
        self._lock_inputs(False)
        # 失败原因三级分类：
        #   ① 实例本身坏（无 main.py/comfy）      → 禁止添加
        #   ② 根本没有任何 Python 环境            → 禁止添加（加了也跑不了），引导去装
        #   ③ 有 Python 但缺依赖（缺 torch 等）   → 允许「仍要添加」，之后补依赖
        _no_python = ("没有可用的 Python" in why) or ("共享便携 Python 也未安装" in why) \
                     or ("未找到 Python 解释器" in why)
        _instance_broken = ("main.py" in why) or ("源码" in why) or ("不是 ComfyUI" in why)
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        if _no_python:
            # 没有任何 Python 环境 → 不允许添加（加进去也启动不了），也不允许误触安装覆盖
            self.lb_scan.setStyleSheet("color:#e8635a;")
            self.lb_scan.setText(
                f"❌ 没有可用的 Python 环境，无法添加或启动实例：{why}\n"
                "请先安装 Python 环境：主界面 →「工具管理」→ 安装共享便携 Python，"
                "装好后回到这里重新检测。\n"
                "（此页面不会改动你选的实例目录）")
            self._set_mode("existing")
            p1 = self.page(0)
            p1.setFinalPage(False)
            self.button(QWizard.NextButton).setEnabled(False)   # 防止在实例目录上误装
            self.button(QWizard.FinishButton).setEnabled(False)
            return
        if _instance_broken:
            self.lb_scan.setText(
                f"❌ 该目录不是可用的 ComfyUI 实例：{why}\n"
                "请确认是否选错了目录（应包含 main.py 与 comfy/ 目录）。")
            self._set_mode("fresh")
            p1 = self.page(0)
            p1.setFinalPage(False)
            self.button(QWizard.NextButton).setEnabled(True)
            self.button(QWizard.FinishButton).setEnabled(False)
            return
        self.lb_scan.setText(
            f"⚠️ 实例本身正常，但当前解释器缺依赖：{why}\n"
            "→ 仍可点「仍要添加」先把它加进列表；\n"
            "→ 之后在主界面「依赖管理」里补齐依赖（如 PyTorch）即可启动。")
        self._set_mode("existing")      # 保持"添加实例"形态：不显示安装参数
        p1 = self.page(0)
        p1.setFinalPage(True)                                  # 允许完成
        self.setButtonText(QWizard.NextButton, "仍要添加")      # 明确"这条路仍可走"
        self.button(QWizard.NextButton).setEnabled(True)
        self.button(QWizard.FinishButton).hide()

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
                     os.path.join(cp, "..", "python", "python.exe")]
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
        if getattr(self, "_scan_candidates", None) and self.cb_found.isVisible():
            idx = max(0, self.cb_found.currentIndex())
            comfy = self._scan_candidates[min(idx, len(self._scan_candidates) - 1)]
            pyv = self._py_map.get(comfy) or ""
            python_exe = pyv[0] if isinstance(pyv, list) else pyv
            if not python_exe:
                QMessageBox.warning(self, "ComfyHub",
                                    "该 ComfyUI 未找到配套 Python，无法直接添加。\n"
                                    "请改走安装流程。")
                return
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
            self._prepare_result()
            if self.setup_result and self.setup_result.get("comfyui_path"):
                super().done(r)
                return
            busy = any(
                getattr(self, a, None) is not None
                and hasattr(getattr(self, a), "isRunning")
                and getattr(self, a).isRunning()
                for a in ("_scan_worker", "_verify_worker"))
            if busy:
                QMessageBox.information(
                    self, "ComfyHub",
                    "⏳ 正在检测/验证环境，请稍候……\n"
                    "验证完成后「完成」即可正常使用（通常只需几秒）。")
                return   # 向导保持打开，不静默关闭
            super().reject()
            return
        super().done(r)

    def accept(self):
        """代码路径 accept：转交 done() 统一处理（done 不再回头调 accept）"""
        self.done(QWizard.Accepted)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "选择安装位置", self.ed_dir.text())
        if d:
            self.ed_dir.setText(os.path.join(d, "ComfyUI"))
            self._detect_existing()   # 每次选择文件夹自动扫描

    def _on_page_change(self, page_id: int):
        # 进入进度页时自动开跑（page_id 是 int，必须换算成页对象再比）
        if self.page(page_id) is self.p2 and not self._started:
            self._started = True
            target = self.ed_dir.text().strip()
            py = self.cb_python.currentData()
            mirror = self.cb_mirror.currentText()
            proxy = self.ed_proxy.text().strip()
            self.p2.start(target, py, mirror, proxy, self.ck_torch.isChecked(),
                          self._on_done)

    def _on_done(self, ok: bool, res: dict, err: str):
        if ok:
            res["name"] = self.ed_name.text().strip()
            self.setup_result = res
            self.p2.lb_step.setText("✅ 安装完成！点击“完成”自动添加为实例。")
            self.button(QWizard.FinishButton).setEnabled(True)
            self.button(QWizard.CancelButton).setEnabled(False)
        else:
            QMessageBox.critical(self, "安装失败", f"错误信息：\n{err}\n\n详情见日志，可修复后重试。")
            self.button(QWizard.BackButton).setEnabled(True)
