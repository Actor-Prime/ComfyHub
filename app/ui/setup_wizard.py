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


class _LogPage(QWizardPage):
    """安装进度页（步骤3）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("正在安装环境")
        self.setCommitPage(True)
        v = QVBoxLayout(self)
        self.bar = QProgressBar()
        self.lb_step = QLabel("准备中…")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.lb_step)
        v.addWidget(self.bar)
        v.addWidget(self.log, 1)
        self.worker = None
        self.result_data = {}

    def start(self, target_dir, python_exe, mirror, proxy, torch_flag, on_done):
        self.log.clear()
        self.bar.setRange(0, 4 if torch_flag else 3)
        # 运行期间锁住向导按钮，防误点（QWizardPage 需经 wizard() 取按钮）
        wiz = self.wizard()
        if wiz:
            for btn in (QWizard.BackButton, QWizard.CommitButton,
                        QWizard.NextButton, QWizard.FinishButton):
                wiz.button(btn).setEnabled(False)
        self.worker = SetupWorker(target_dir, python_exe, mirror, proxy, torch_flag)
        self.worker.sig_log.connect(self._on_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_done.connect(lambda ok, res, err: on_done(ok, res, err))
        self.worker.start()

    def _on_log(self, msg):
        self.log.appendPlainText(msg)
        from PySide6.QtGui import QTextCursor
        self.log.moveCursor(QTextCursor.End)

    def _on_progress(self, step, total):
        self.bar.setMaximum(total)
        self.bar.setValue(step)
        names = {1: "准备 Python", 2: "下载 ComfyUI 源码",
                 3: "安装依赖库", 4: "安装 PyTorch（按显卡匹配）"}
        self.lb_step.setText(f"步骤 {step}/{total}：{names.get(step, '')}")


class SetupWizard(QWizard):
    """三步向导：位置与选项 → （安装页）→ 完成"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("一键安装 ComfyUI 环境")
        self.setWizardStyle(QWizard.ModernStyle)
        self.resize(720, 560)
        self.setup_result = {}

        # ---------- 页1：位置与选项 ----------
        p1 = QWizardPage()
        p1.setTitle("选择安装位置")
        form = QFormLayout(p1)

        self.ed_dir = QLineEdit(os.path.join("F:\\", "ComfyUI"))
        b_pick = QPushButton("浏览…")
        b_pick.clicked.connect(self._pick)
        b_detect = QPushButton("🔍 扫描")
        b_detect.setToolTip("扫描该目录（含两层子目录）是否已有完整 ComfyUI：\n"
                            "有 → 直接添加为实例（免下载）；\n"
                            "无 → 自动补全子目录并展开安装选项")
        self.b_detect = b_detect
        b_detect.clicked.connect(self._detect_existing)
        row = QHBoxLayout()
        row.addWidget(self.ed_dir, 1)
        row.addWidget(b_pick)
        row.addWidget(b_detect)
        form.addRow("安装到:", row)

        self.ed_name = QLineEdit("")
        self.ed_name.setPlaceholderText("不填则使用安装目录名")
        form.addRow("实例名称:", self.ed_name)

        # 扫描结果提示 + 候选下拉（扫描到已有环境时显示）
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
        # 探测已有解释器供选择
        for cand in (r"F:\ComfyUI_env\Scripts\python.exe",):
            if os.path.isfile(cand):
                self.cb_python.addItem(f"使用已有: {cand}", userData=cand)
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
        self.b_detect.setEnabled(False)
        self.b_detect.setText("扫描中…")
        self.lb_scan.setText("正在扫描，请稍候…")
        self.lb_scan.setStyleSheet("color:#9aa4b2;")
        self.lb_scan.show()
        self._scan_worker = _ScanWorker(root)
        self._scan_worker.sig_done.connect(self._on_scan_done)
        self._scan_worker.start()

    def _on_scan_done(self, root, candidates):
        self.b_detect.setEnabled(True)
        self.b_detect.setText("🔍 扫描")
        candidates = list(dict.fromkeys(candidates))
        p1 = self.page(0)
        if candidates:
            self.cb_found.clear()
            for c in candidates:
                rel = os.path.relpath(c, root)
                self.cb_found.addItem(rel if rel != "." else "(此目录)")
            self.cb_found.setEnabled(len(candidates) > 1)
            self.cb_found.show()
            n_py = self._match_pythons(candidates)
            self.lb_scan.setStyleSheet("color:#4cd47c;")
            self.lb_scan.setText(
                f"✅ 检测到 {len(candidates)} 个 ComfyUI 环境和 {n_py} 个 Python 解释器："
                "选好候选后点「完成」直接添加为实例（免下载）。")
            self._scan_candidates = candidates
            self._scan_pys = self._py_map
            p1.setFinalPage(True)
            self.button(QWizard.FinishButton).setEnabled(True)
            self.button(QWizard.NextButton).setEnabled(False)
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
                py_map[cp] = cands[0]
        self._py_map = py_map
        return len(total)

    def accept(self):
        """完成前拦截：检测模式下组装 setup_result 直接注册"""
        if getattr(self, "_scan_candidates", None) and self.cb_found.isVisible():
            idx = max(0, self.cb_found.currentIndex())
            comfy = self._scan_candidates[min(idx, len(self._scan_candidates) - 1)]
            python_exe = self._py_map.get(comfy) or ""
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
            res["name"] = self.p1.ed_name.text().strip()
            self.setup_result = res
            self.p2.lb_step.setText("✅ 安装完成！点击“完成”自动添加为实例。")
            self.button(QWizard.FinishButton).setEnabled(True)
            self.button(QWizard.CancelButton).setEnabled(False)
        else:
            QMessageBox.critical(self, "安装失败", f"错误信息：\n{err}\n\n详情见日志，可修复后重试。")
            self.button(QWizard.BackButton).setEnabled(True)
