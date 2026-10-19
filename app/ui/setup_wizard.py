# -*- coding: utf-8 -*-
"""一键安装 ComfyUI 环境向导（QWizard 三步式）"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QLabel, QVBoxLayout, QLineEdit, QHBoxLayout,
    QPushButton, QFileDialog, QComboBox, QCheckBox, QProgressBar,
    QPlainTextEdit, QFormLayout, QMessageBox,
)

from app import bootstrap
from app.bootstrap import PYPI_MIRRORS


# ---------------- 后台安装线程 ----------------
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
        b_detect = QPushButton("🔍 检测已有环境")
        b_detect.setToolTip("扫描该目录（含两层子目录）是否已有完整 ComfyUI，\n"
                            "检测到可直接添加为实例，跳过下载安装")
        b_detect.clicked.connect(self._detect_existing)
        row = QHBoxLayout()
        row.addWidget(self.ed_dir, 1)
        row.addWidget(b_pick)
        row.addWidget(b_detect)
        form.addRow("安装到:", row)

        self.ed_name = QLineEdit("")
        self.ed_name.setPlaceholderText("不填则使用安装目录名")
        form.addRow("实例名称:", self.ed_name)

        self.cb_python = QComboBox()
        self.cb_python.addItem("自动安装便携 Python（推荐，不污染系统）", userData=None)
        # 探测已有解释器供选择
        for cand in (r"F:\ComfyUI_env\Scripts\python.exe",):
            if os.path.isfile(cand):
                self.cb_python.addItem(f"使用已有: {cand}", userData=cand)
        form.addRow("Python 环境:", self.cb_python)

        self.cb_mirror = QComboBox()
        self.cb_mirror.addItems(list(PYPI_MIRRORS.keys()))
        self.cb_mirror.setCurrentText("阿里云")
        form.addRow("PyPI 镜像:", self.cb_mirror)

        self.ed_proxy = QLineEdit("")
        self.ed_proxy.setPlaceholderText("留空直连；GitHub 下载慢可填 https://ghproxy.net/ 等")
        form.addRow("GitHub 代理:", self.ed_proxy)

        self.ck_torch = QCheckBox("自动安装 PyTorch（按显卡自动选 CUDA/CPU 版，约 1.5~2.5GB）")
        self.ck_torch.setChecked(True)
        form.addRow("", self.ck_torch)

        tip = QLabel("说明：便携 Python 与 MinGit 安装在 %APPDATA%\\ComfyHub\\tools，"
                     "可随时整目录删除卸载，不影响系统。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;")
        form.addRow("", tip)
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
        """扫描指定目录（含两层子目录）找完整 ComfyUI 环境：
        main.py + 可用 python.exe → 直接添加为实例，跳过安装"""
        root = os.path.normpath(self.ed_dir.text().strip())
        if not os.path.isdir(root):
            QMessageBox.warning(self, "ComfyHub", f"目录不存在：\n{root}")
            return
        # 1) 找 main.py：root 本身 + 一级/二级子目录（浅搜防全盘遍历）
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
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            QMessageBox.information(
                self, "ComfyHub",
                f"在 {root}（含两层子目录）中未找到 ComfyUI（main.py）。\n\n"
                "请确认目录，或走下方一键安装流程。")
            return
        if len(candidates) == 1:
            idx = 0
        else:
            labels = [os.path.relpath(c, root) if os.path.relpath(c, root) != "."
                      else "(此目录)" for c in candidates]
            idx = self._choose("检测到多个 ComfyUI，选择一个：", labels)
            if idx is None:
                return
        comfy_path = os.path.normpath(candidates[idx])

        # 2) 找 python.exe：常见布局
        py_cands = [
            os.path.join(comfy_path, "python.exe"),
            os.path.join(comfy_path, "python", "python.exe"),
            os.path.join(comfy_path, "venv", "Scripts", "python.exe"),
            os.path.join(comfy_path, ".venv", "Scripts", "python.exe"),
            os.path.join(comfy_path, "python_embeded", "python.exe"),
            os.path.join(comfy_path, "..", "python.exe"),
            os.path.join(comfy_path, "..", "python", "python.exe"),
        ]
        py_cands = [os.path.normpath(p) for p in py_cands if os.path.isfile(p)]
        if not py_cands:
            # 同级目录里找 python 环境（如 F:\python、F:\ComfyUI_env）
            for d1 in os.listdir(os.path.dirname(comfy_path)):
                base = os.path.join(os.path.dirname(comfy_path), d1)
                for sub in ("", "Scripts", "python"):
                    p = os.path.join(base, sub, "python.exe")
                    if os.path.isfile(p):
                        py_cands.append(os.path.normpath(p))
        py_cands = list(dict.fromkeys(py_cands))
        if not py_cands:
            QMessageBox.warning(
                self, "ComfyHub",
                f"找到了 ComfyUI：{comfy_path}\n\n"
                "但没找到配套的 Python 解释器（venv / 内嵌 python）。\n"
                "建议走一键安装，或在安装后手动指定解释器。")
            return
        if len(py_cands) == 1:
            python_exe = py_cands[0]
        else:
            pi = self._choose("检测到多个 Python，选择一个：", py_cands)
            if pi is None:
                return
            python_exe = py_cands[pi]
        if not python_exe:
            return

        # 3) 确认并直接完成（跳过安装）
        name = self.ed_name.text().strip() or os.path.basename(comfy_path)
        r = QMessageBox.question(
            self, "检测到完整环境",
            f"ComfyUI：{comfy_path}\nPython：{python_exe}\n\n"
            f"直接添加为实例「{name}」？（跳过下载安装）")
        if r != QMessageBox.Yes:
            return
        self.setup_result = {
            "comfyui_path": comfy_path,
            "python_path": python_exe,
            "name": name,
            "skipped_install": True,
        }
        self.accept()

    def _choose(self, title, items):
        """简单单选对话框，返回选中索引（int），取消返回 None"""
        from PySide6.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(self, title, "选择：", items, 0, False)
        return items.index(item) if ok else None

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
