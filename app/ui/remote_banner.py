# -*- coding: utf-8 -*-
"""http 明文告警条（2026-12-01，0.8.40 T06）

为什么要有（PRD D）：远程实例如果走 `http://` 且不是回环地址，Token 与生成的图
会以明文经过网络，同一网络里的人能看到。用户保存配置时弹一次模态（能动手改），
**连接成功时**再给一条不打断的非模态黄条 —— 因为真正「跑起来在用」的时刻才
是风险发生的时候，而他这时候多半正盯着出图，不该被模态框拽走。

形态照 `plugin_banner.py`：主窗顶栏下方 38px 一条，右侧「知道了」+ ✕，
同一实例同一会话只出现一次（去重在 `MainWindow._insecure_shown`）。

判定**必须用生效基址**（`remote_ops.insecure_reason` → `api_base.effective_base_info`）：
隧道激活时生效基址是 127.0.0.1，本身已经加密，不该再报明文。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

# 与 plugin_banner 同理：横幅里的按钮固定 26px 高，全局 QPushButton 的
# padding:7px 15px 会把 12px 字上下裁掉，这里只覆盖内边距（颜色仍走全局 QSS）
_TIGHT_BTN = 'QPushButton{padding:0 12px;}'

TXT_WARN = ("⚠ 这个实例走 http 明文：Token 和生成的图同网络可见。"
            "\u3000加 HTTPS 或用隧道可避免。")
TXT_ACK = "知道了"


class RemoteBanner(QFrame):
    """主窗口顶栏下方的一条明文告警（知道了 / ✕）"""

    sig_close = Signal()        # 本次关闭（同实例同会话不再出现）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RemoteBanner")
        self.setFixedHeight(38)
        # 底色/描边/文字色都在 theme.py 的全局 QSS（#RemoteBanner）里，
        # 浅色主题由 _LIGHT_MAP 换色 —— 这里再写一份会跟主题打架
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 0, 12, 0)
        h.setSpacing(10)
        self.lb = QLabel(TXT_WARN)
        self.lb.setStyleSheet("background:transparent; font-size:12px;")
        self.lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(self.lb)
        h.addStretch(1)
        b_ack = QPushButton(TXT_ACK)
        b_ack.setFixedHeight(26)
        b_ack.setStyleSheet(_TIGHT_BTN)
        b_ack.setToolTip("关闭本次提示；下次连接这个实例还会再提醒一次")
        b_ack.clicked.connect(self._on_close)
        h.addWidget(b_ack)
        b_close = QPushButton("✕")
        b_close.setFixedSize(24, 26)
        b_close.setToolTip("关闭这条提示")
        b_close.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#a9944f;padding:0;}")
        b_close.clicked.connect(self._on_close)
        h.addWidget(b_close)
        self.hide()

    # ---------- 对外 ----------
    def show_hit(self, base: str = "", name: str = ""):
        """显示一条告警；`base` / `name` 只进 tooltip（整句文案不含动态变量）"""
        tip = TXT_WARN
        extra = []
        if name:
            extra.append(f"实例：{name}")
        if base:
            extra.append(f"地址：{base}")
        if extra:
            tip = TXT_WARN + "\n" + "\n".join(extra)
        self.lb.setToolTip(tip)
        self.show()

    def _on_close(self):
        self.hide()
        self.sig_close.emit()
