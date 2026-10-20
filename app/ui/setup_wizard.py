# -*- coding: utf-8 -*-
"""一键安装 ComfyUI 环境向导（QWizard 三步式）"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QLabel, QVBoxLayout, QLineEdit, QHBoxLayout,
    QPushButton, QFileDialog, QComboBox, QCheckBox, QProgressBar,
    QPlainTextEdit, QFormLayout, QMessageBox, QWidget,
)

from app import bootstrap
from app.bootstrap import PYPI_MIRRORS


# ---------------- 后台安装线程 ----------------
class _ScanWorker(QThread):
    """目录扫描线程：找 main.py（含两层子目录）"""
    sig_done = Signal(str, list)

    def __init__(self, root):
        super().__init__()
        self.root = root

    def run(self):
        root = self.root
        candidates = []
        search_roots = [root]
        try:
            for d1 in os.listdir(root):
                p1 = os.path.join(root, d1)
                if os.path.isdir(p1):
                    search_roots.append(p1)
                    for d2 in os.listdir(p1):
                        p2 = os.path.join(p1, d2)
                        if os.path.isdir(p2):
                            search_roots.append(p2)
        except OSError:
            pass
        for sr in search_roots:
            if os.path.isfile(os.path.join(sr, "main.py")):
                candidates.append(sr)
        self.sig_done.emit(root, candidates)


class SetupWorker(QThread):
    sig_log = Signal(str)
    sig_progress = Signal(int, int)     # step, total
    sig_done = Signal(bool, dict, str)  # ok, result, errmsg

    def __init__(self, target_dir, python_exe, mirror, proxy, install_torch_flag):
        super().__init__()
        self.target_dir = target_dir
        self.python_exe = python_exe
        self.mirror = mirror
        self.proxy = proxy
        self.install_torch_flag = install_torch_flag

    def run(self):
        try:
            res = bootstrap.full_setup(
                self.target_dir, self.python_exe, self.mirror, self.proxy,
                log=lambda m: self.sig_log.emit(str(m)),
                install_torch_flag=self.install_torch_flag,
                progress=lambda s, t: self.sig_progress.emit(s, t))
            self.sig_done.emit(True, res, "")
        except Exception as e:
            self.sig_done.emit(False, {}, str(e))


class _StepWorker(QThread):
    """通用后台执行器：跑 fn(*args)，返回 (ok, result, err)"""
    sig_done = Signal(bool, object, str)

    def __init__(self, fn, args=()):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            r = self.fn(*self.args)
            self.sig_done.emit(True, r, "")
        except Exception as e:
            self.sig_done.emit(False, None, str(e))


class _LogPage(QWizardPage):
    """安装进度页（步进制：每步 检测 → 询问 → 执行 → 复验 → 下一步）"""

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

    # ---------- 入口 ----------
    def start(self, target_dir, python_exe, mirror, proxy, torch_flag, on_done):
        from app import bootstrap
        self.log.clear()
        self.on_done = on_done
        self.done_ok = False
        self.state = {"target_dir": target_dir, "python_exe": python_exe,
                      "mirror_name": mirror, "proxy": proxy, "total": 4 if torch_flag else 3}
        # 分流：目录里已有 ComfyUI 实例 → 步进制（逐步检测询问）；
        #       空目录（全新安装）→ 全自动连跑（无需逐步确认）
        has_existing = os.path.isfile(os.path.join(target_dir, "main.py"))
        # 锁住向导按钮
        wiz = self.wizard()
        if wiz:
            for btn in (QWizard.BackButton, QWizard.CommitButton,
                        QWizard.NextButton, QWizard.FinishButton):
                wiz.button(btn).setEnabled(False)
        if has_existing:
            self._start_stepwise(torch_flag)
        else:
            self._start_auto(target_dir, python_exe, mirror, proxy, torch_flag)

    def _start_auto(self, target_dir, python_exe, mirror, proxy, torch_flag):
        """空目录全新安装：全自动连跑"""
        self.lb_step.setText("全新安装（空目录）：自动执行全部步骤…")
        self.worker = SetupWorker(target_dir, python_exe, mirror, proxy, torch_flag)
        self.worker.sig_log.connect(self._on_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_done.connect(lambda ok, res, err: self.on_done(ok, res, err))
        self.worker.start()

    def _start_stepwise(self, torch_flag):
        """已有实例：步进制（检测 → 询问 → 执行 → 复验）"""
        from app import bootstrap
        total = 4 if torch_flag else 3
        self.bar.setRange(0, total)
        target_dir = self.state["target_dir"]
        python_exe = self.state["python_exe"]
        mirror = bootstrap.PYPI_MIRRORS.get(self.state["mirror_name"],
                                            bootstrap.PYPI_MIRRORS["阿里云"])
        wheels = os.path.join(bootstrap.tools_root(), "wheels")
        log = self._on_log

        plan = [
            {"title": self.STEP_TITLES[0],
             "detect": lambda: bootstrap.detect_python_step(python_exe, bootstrap.tools_root()),
             "install": lambda: bootstrap.install_portable_python(
                 bootstrap.tools_root(), log=log, proxy=self.state["proxy"])},
            {"title": self.STEP_TITLES[1],
             "detect": lambda: bootstrap.detect_source_step(target_dir),
             "install": lambda: bootstrap.install_comfyui_source(
                 target_dir, log=log, proxy=self.state["proxy"])},
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
        self.worker = _StepWorker(step["detect"])
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
        """kind: ok=建议跳过 / missing=需安装 / update=建议更新 / retry=检测失败重试"""
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
        step = self.plan[self.cur]
        self.lb_state.setText("⏳ 执行中…（按钮已锁定，完成后自动进入下一步）")
        self.worker = _StepWorker(step["install"])
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
        self.worker = _StepWorker(step["detect"])
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
        self._verify_worker = _StepWorker(
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
        if self.on_done:
            self.on_done(True, dict(self.result_data), "")

    def _on_log(self, msg):
        self.log.appendPlainText(msg)
        from PySide6.QtGui import QTextCursor
        self.log.moveCursor(QTextCursor.End)

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
        b_pick = QPushButton("浏览…")
        b_pick.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.ed_dir, 1)
        row.addWidget(b_pick)
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
        self.cb_found = QComboBox()
        self.cb_found.hide()
        form.addRow("检测到:", self.cb_found)

        # ---- 安装选项容器（未检测到已有环境时才展开） ----
        self.install_opts = QWidget()
        form_opts = QFormLayout(self.install_opts)
        form_opts.setContentsMargins(0, 0, 0, 0)

        self.cb_python = QComboBox()
        self.cb_python.addItem("自动安装便携 Python（推荐，不污染系统）", userData=None)
        # 动态探测已有解释器：共享便携 Python + 已注册实例用过的解释器（去重）
        seen = set()
        tools_py = os.path.join(bootstrap.tools_root(), "python", "python.exe")
        if os.path.isfile(tools_py):
            self.cb_python.addItem("使用 ComfyHub 共享便携 Python（已安装，实例共用）",
                                   userData=tools_py)
            seen.add(tools_py)
        parent_cfg = getattr(self.parent(), "cfg", {}) if self.parent() else {}
        for inst in parent_cfg.get("instances", []):
            cand = inst.get("python_path") or ""
            if cand and os.path.isfile(cand) and cand not in seen:
                self.cb_python.addItem(f"使用已有: {cand}", userData=cand)
                seen.add(cand)
        form_opts.addRow("Python 环境:", self.cb_python)

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

    def _detect_existing(self):
        """后台扫描目录（含两层子目录）：找到 ComfyUI 直接注册；没找到自动补全并展开安装选项"""
        root = os.path.normpath(self.ed_dir.text().strip())
        if not os.path.isdir(root):
            QMessageBox.warning(self, "ComfyHub", f"目录不存在：\n{root}")
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
        self._scan_worker = _ScanWorker(root)
        self._scan_worker.sig_done.connect(self._on_scan_done)
        self._scan_worker.start()

    def _on_scan_done(self, root, candidates):
        candidates = list(dict.fromkeys(candidates))
        p1 = self.page(0)
        if candidates:
            self.cb_found.clear()
            for c in candidates:
                rel = os.path.relpath(c, root)
                self.cb_found.addItem(rel if rel != "." else "(此目录)")
            self.cb_found.setEnabled(len(candidates) > 1)
            self.cb_found.show()
            self.cb_found.currentIndexChanged.connect(
                lambda _i: self._begin_verify() if self._scan_candidates else None)
            n_py = self._match_pythons(candidates)
            self._scan_candidates = candidates
            self._scan_pys = self._py_map
            # 验证选中候选能否运行——通过才解锁「完成」
            self._phase = "scan_verify"
            self.lb_scan.setStyleSheet("color:#9aa4b2;")
            self.lb_scan.setText(f"检测到 {len(candidates)} 个 ComfyUI 环境和 {n_py} 个 Python 解释器，正在验证能否运行…")
            self._begin_verify()
            return
        self.cb_found.hide()
        target = os.path.join(root, "ComfyUI")
        if not os.path.isfile(os.path.join(target, "main.py")):
            self.ed_dir.setText(target)
        self.install_opts.show()
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        self.lb_scan.setText(
            "未检测到已有 ComfyUI，已切换到安装模式：\n"
            "安装目录已自动指向子目录 ComfyUI，请确认下方安装选项后点「下一步」。")
        p1.setFinalPage(False)
        self.button(QWizard.NextButton).setEnabled(True)
        self.button(QWizard.FinishButton).setEnabled(False)

    def _begin_verify(self):
        """后台验证选中候选的 ComfyUI 能否运行；通过才解锁「完成」。
        多个 Python 候选时逐个试，选第一个真正能 import torch 的"""
        from app import bootstrap
        self.button(QWizard.FinishButton).setEnabled(False)
        self.button(QWizard.NextButton).setEnabled(False)
        idx = max(0, self.cb_found.currentIndex())
        comfy = self._scan_candidates[min(idx, len(self._scan_candidates) - 1)]
        cands = self._py_map.get(comfy) or []
        self._verify_comfy = comfy

        def _try_all():
            last = ""
            for py in cands:
                ok, info = bootstrap.verify_comfyui_runnable(py, comfy)
                if ok:
                    return (True, info, py)
                last = f"{py}: {info}"
            if not cands:
                return (False, "未找到任何可用的 Python 解释器", "")
            return (False, last, "")

        self._verify_worker = _StepWorker(_try_all)
        self._verify_worker.sig_done.connect(self._on_verify2_done)
        self._verify_worker.start()

    def _on_verify2_done(self, ok, res, err):
        p1 = self.page(0)
        if not ok:
            self._verify_fail(f"验证执行失败: {err}")
            return
        v_ok, info, py = res
        if v_ok:
            if self._verify_comfy in self._py_map:
                pass
            else:
                self._py_map[self._verify_comfy] = py
            self._py_map[self._verify_comfy] = py   # 记住验证通过的解验器
            self.lb_scan.setStyleSheet("color:#4cd47c;")
            self.lb_scan.setText(f"✅ {info}\n使用解释器: {py}\n选好候选后点「完成」直接添加为实例（免下载）。")
            p1.setFinalPage(True)
            self.button(QWizard.FinishButton).setEnabled(True)
            self.button(QWizard.NextButton).setEnabled(False)
        else:
            self._verify_fail(info)

    def _verify_fail(self, why):
        """验证失败：切回安装模式（展开选项，走安装修齐）"""
        self.lb_scan.setStyleSheet("color:#e8a33d;")
        self.lb_scan.setText(
            f"⚠️ 验证未通过：{why}\n"
            "已切换到安装模式：在下方「Python 环境」选择自动安装便携 Python 即可修齐，确认选项后点「下一步」。")
        self.cb_found.hide()
        self._scan_candidates = None
        self.install_opts.show()
        p1 = self.page(0)
        p1.setFinalPage(False)
        self.button(QWizard.NextButton).setEnabled(True)
        self.button(QWizard.FinishButton).setEnabled(False)

    def _match_pythons(self, candidates):
        """为每个候选 ComfyUI 找配套 Python，构建 {comfy_path: python_exe}"""
        py_map = {}
        total = set()
        for cp in candidates:
            cands = [os.path.join(cp, "python.exe"),
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
            for p in cands:
                total.add(p)
            if cands:
                py_map[cp] = cands   # 存全部候选，验证时逐个试
        self._py_map = py_map
        return len(total)

    def accept(self):
        """完成前拦截：检测模式下组装 setup_result 直接注册"""
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
            super().accept()
            return
        if self.setup_result and self.setup_result.get("comfyui_path"):
            super().accept()
            return
        super().reject()

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
