# -*- coding: utf-8 -*-
"""关闭行为选择对话框：退出程序 / 最小化到任务栏 + 「记住我的选择」

2026-10-31 用户需求：第一次点关闭按钮弹此框询问；勾「记住我的选择」后
写入 settings.close_behavior（close/minimize），之后直接执行不再询问；
系统设置页可随时改回「每次询问」。
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
)


class CloseChoiceDialog(QDialog):
    """结果：self.choice = 'close' | 'minimize' | None（取消）；self.remembered()"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关闭 ComfyHub？")
        self.setModal(True)
        self.resize(430, 210)
        self._choice = None

        root = QVBoxLayout(self)
        root.setSpacing(10)

        t = QLabel("请选择点击关闭按钮时的行为：")
        t.setStyleSheet("font-size:14px; font-weight:bold;")
        root.addWidget(t)

        d = QLabel("· 退出程序：关闭 ComfyHub（正在运行的 ComfyUI 实例会一并停止）\n"
                   "· 最小化到托盘：程序在后台继续运行，实例不受影响")
        d.setWordWrap(True)
        d.setStyleSheet("color:#9aa4b2;")
        root.addWidget(d)

        self.ck_remember = QCheckBox("记住我的选择（可在「系统设置」中更改）")
        root.addWidget(self.ck_remember)
        root.addStretch()

        bb = QHBoxLayout()
        bb.addStretch()
        b_min = QPushButton("最小化到托盘")
        b_min.clicked.connect(lambda: self._done("minimize"))
        b_close = QPushButton("退出程序")
        b_close.setProperty("accent", True)
        b_close.clicked.connect(lambda: self._done("close"))
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        bb.addWidget(b_min)
        bb.addWidget(b_close)
        bb.addWidget(b_cancel)
        root.addLayout(bb)

    def _done(self, choice):
        self._choice = choice
        self.accept()

    @property
    def choice(self):
        return self._choice

    def remembered(self):
        return self.ck_remember.isChecked()
