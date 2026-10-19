# -*- coding: utf-8 -*-
"""添加/编辑实例对话框"""
import os

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QSpinBox, QComboBox,
    QPushButton, QHBoxLayout, QVBoxLayout, QFileDialog,
    QDialogButtonBox, QCheckBox, QLabel,
)

from app.config import VRAM_MODES
from app.instance import is_valid_comfyui, read_comfyui_version


class InstanceDialog(QDialog):
    """新建或编辑一个 ComfyUI 实例配置"""

    def __init__(self, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑实例" if data else "添加 ComfyUI 实例")
        self.setMinimumWidth(520)
        data = data or {}

        # --- 表单 ---
        self.ed_name = QLineEdit(data.get("name", ""))
        self.ed_path = QLineEdit(data.get("comfyui_path", ""))
        self.ed_python = QLineEdit(data.get("python_path", ""))
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        self.sp_port.setValue(int(data.get("port") or 8188))
        self.cb_vram = QComboBox()
        self.cb_vram.addItems(VRAM_MODES.keys())
        self.cb_vram.setCurrentText(data.get("vram_mode", "自动"))
        self.ed_extra = QLineEdit(data.get("extra_args", ""))
        self.ed_extra.setPlaceholderText("例如: --reserve-vram 2 --fast")
        self.ck_browser = QCheckBox("服务就绪后自动打开浏览器")
        self.ck_browser.setChecked(bool(data.get("auto_open_browser", True)))

        btn_pick_path = QPushButton("浏览…")
        btn_pick_path.clicked.connect(self._pick_comfyui)
        btn_pick_py = QPushButton("浏览…")
        btn_pick_py.clicked.connect(self._pick_python)

        row1 = QHBoxLayout()
        row1.addWidget(self.ed_path, 1)
        row1.addWidget(btn_pick_path)
        row2 = QHBoxLayout()
        row2.addWidget(self.ed_python, 1)
        row2.addWidget(btn_pick_py)

        form = QFormLayout()
        form.addRow("实例名称:", self.ed_name)
        form.addRow("ComfyUI 目录:", row1)
        form.addRow("Python 路径:", row2)
        form.addRow("端口:", self.sp_port)
        form.addRow("显存模式:", self.cb_vram)
        form.addRow("附加参数:", self.ed_extra)
        form.addRow("", self.ck_browser)

        self.lb_hint = QLabel("")
        self.lb_hint.setStyleSheet("color: #c0392b;")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.lb_hint)
        lay.addWidget(bb)

        # 已有路径时给个默认名
        if not self.ed_name.text() and self.ed_path.text():
            self.ed_name.setText(os.path.basename(self.ed_path.text()))

    # --- 选择目录 ---
    def _pick_comfyui(self):
        d = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 根目录（含 main.py）",
                                             self.ed_path.text() or "F:\\")
        if d:
            self.ed_path.setText(os.path.normpath(d))
            if not self.ed_name.text():
                self.ed_name.setText(os.path.basename(d))
            # 自动带出配套 Python
            if not self.ed_python.text():
                for cand in (
                    os.path.join(d, "python_embeded", "python.exe"),
                    os.path.join(os.path.dirname(d), "ComfyUI_env", "Scripts", "python.exe"),
                    os.path.join(d, ".venv", "Scripts", "python.exe"),
                    os.path.join(os.path.dirname(d), ".venv", "Scripts", "python.exe"),
                ):
                    if os.path.isfile(cand):
                        self.ed_python.setText(cand)
                        break

    def _pick_python(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 Python 解释器", self.ed_python.text(),
                                           "Python (python.exe)")
        if f:
            self.ed_python.setText(f)

    # --- 校验并返回 ---
    def _accept(self):
        path = self.ed_path.text().strip()
        if not is_valid_comfyui(path):
            self.lb_hint.setText("该目录下没有 main.py，不是有效的 ComfyUI 根目录。")
            return
        if not self.ed_python.text().strip():
            self.lb_hint.setText("请填写 Python 解释器路径。")
            return
        if not self.ed_name.text().strip():
            self.ed_name.setText(os.path.basename(path))
        self.accept()

    def result_data(self) -> dict:
        return {
            "name": self.ed_name.text().strip(),
            "comfyui_path": os.path.normpath(self.ed_path.text().strip()),
            "python_path": os.path.normpath(self.ed_python.text().strip()),
            "port": self.sp_port.value(),
            "vram_mode": self.cb_vram.currentText(),
            "extra_args": self.ed_extra.text().strip(),
            "auto_open_browser": self.ck_browser.isChecked(),
        }
