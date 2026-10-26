# -*- coding: utf-8 -*-
"""设置页：镜像源 / GitHub 代理 / 高级设置（专家模式）/ 关于

2026-10-26：按用户需求新增「高级设置」——
  · 专家模式切换框：默认关闭，普通用户看不到启动参数（避免误触）
  · 全局启动参数：显存调度 / 注意力引擎 / 缓存策略 / 精度提升（单选）
    + 常用开关（复选）+ 自定义参数框，对所有实例生效
  · 合并规则：实例单独设置 > 全局（实例留「自动」才吃全局值），
    拼装逻辑在 app/process.py build_command()
  注：所列参数均为 ComfyUI 官方公开 CLI 参数；分组思路参考同类启动器，
  说明文案为本项目自撰（见 docs/COMPETITORS.md）
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QGroupBox, QFormLayout, QMessageBox, QCheckBox,
    QRadioButton, QButtonGroup, QScrollArea, QGridLayout,
)

from app.config import save_config, APP_VERSION
from app.bootstrap import PYPI_MIRRORS


# ---- 全局启动参数选项表（value="", 空串 = 不加该参数）----
# (显示文字, 参数值, 悬停说明)；说明文案自撰
_VRAM_OPTS = [
    ("自动（推荐）", "", "交给 ComfyUI 自动决定显存策略"),
    ("高显存 --highvram", "--highvram",
     "尽量把模型留在显存里，减少与内存的交换；显存 ≥12GB 推荐"),
    ("普通显存 --medvram", "--medvram",
     "显存与内存之间动态加载/卸载模型层；8GB 左右的常用档位"),
    ("低显存 --lowvram", "--lowvram",
     "只把当前计算的网络层放进显存，算完立刻搬走；≤6GB 或爆显存时用"),
    ("零显存 --novram", "--novram",
     "几乎不用显存全靠内存，极特殊排错场景才用，速度很慢"),
]
_ATTN_OPTS = [
    ("自动（推荐）", "", "不指定，由 ComfyUI 选择默认注意力实现"),
    ("Sage 注意力", "--use-sage-attention",
     "SageAttention 加速，需先安装 sageattention 包；提速明显"),
    ("Flash 注意力", "--use-flash-attention",
     "FlashAttention 加速，NVIDIA 显卡专用"),
    ("PyTorch SDP", "--use-pytorch-cross-attention",
     "PyTorch 自带的注意力实现，兼容性最好"),
    ("切片 Split", "--use-split-cross-attention",
     "切片计算注意力，低显存友好的兜底方案"),
    ("四分 Quad", "--use-quad-cross-attention",
     "切片的变体，进一步减少显存碎片"),
]
_CACHE_OPTS = [
    ("默认", "", "标准缓存策略"),
    ("经典缓存 --cache-classic", "--cache-classic",
     "旧版缓存实现；新缓存有兼容问题时可切回这个"),
]
_UPCAST_OPTS = [
    ("默认", "", "半精度运行，速度优先"),
    ("强制提升精度", "--force-upcast-attention",
     "把注意力相关计算提升到 FP32；出图有黑块/撕裂时开"),
    ("禁止提升精度", "--dont-upcast-attention",
     "彻底禁用精度提升，牺牲稳定性换性能和更低显存"),
]
# (显示文字, 参数值, 说明)
_FLAGS = [
    ("禁用 xformers", "--disable-xformers",
     "屏蔽 xformers 加速库；它引发崩溃/报错时勾选"),
    ("Channels Last 布局", "--force-channels-last",
     "改用 channels_last 张量布局，部分场景有推理提速"),
    ("不自动开浏览器", "--disable-auto-launch",
     "启动后不自动打开网页（多实例管理时建议勾）"),
]


class SettingsPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        st = self.cfg.setdefault("settings", {})
        self._build(st)

    # ------------------------------------------------------------ UI
    def _build(self, st):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 12, 16, 12)

        t = QLabel("设置")
        t.setStyleSheet("font-size:17px; font-weight:bold; color:#fff;")
        root.addWidget(t)

        g1 = QGroupBox("网络")
        f1 = QFormLayout(g1)
        self.cb_mirror = QComboBox()
        self.cb_mirror.addItems(list(PYPI_MIRRORS.keys()))
        self.cb_mirror.setCurrentText(st.get("mirror", "阿里云"))
        f1.addRow("PyPI 镜像:", self.cb_mirror)
        self.ed_ghproxy = QLineEdit(st.get("gh_proxy", ""))
        self.ed_ghproxy.setPlaceholderText("留空直连；如 https://ghproxy.net/ （自动拼在 github URL 前）")
        f1.addRow("GitHub 代理:", self.ed_ghproxy)
        self.ed_gitproxy = QLineEdit(st.get("git_proxy", ""))
        self.ed_gitproxy.setPlaceholderText("git clone 专用代理（留空则同上）")
        f1.addRow("Git Clone 代理:", self.ed_gitproxy)
        root.addWidget(g1)

        g2 = QGroupBox("界面")
        f2 = QFormLayout(g2)
        self.cb_lang = QComboBox()
        self.cb_lang.addItems(["简体中文"])
        self.cb_lang.setEnabled(False)   # v0.5 接入 .mo 翻译后开放
        f2.addRow("语言:", self.cb_lang)
        self.cb_theme = QComboBox()
        self.cb_theme.addItems(["深色"])
        self.cb_theme.setEnabled(False)  # v0.5 加浅色主题后开放
        f2.addRow("主题:", self.cb_theme)
        root.addWidget(g2)

        root.addWidget(self._build_expert_group(st))

        b_save = QPushButton("保存设置")
        b_save.setProperty("accent", True)
        b_save.clicked.connect(self._save)
        root.addWidget(b_save)
        root.addStretch()

        g3 = QGroupBox("关于")
        v3 = QVBoxLayout(g3)
        about = QLabel(
            f"ComfyHub v{APP_VERSION}\n"
            "PySide6 轻量 ComfyUI 多实例管理器（46MB 便携包）\n"
            "功能对标 ComfyUI管理大师 / TE启动器\n\n"
            "Python 3.13 · PySide6 · MIT")
        about.setStyleSheet("color:#9aa4b2;")
        v3.addWidget(about)
        root.addWidget(g3)

        scroll.setWidget(body)
        outer.addWidget(scroll)

    # ---------------- 高级设置（专家模式 + 全局启动参数） ----------------
    def _build_expert_group(self, st) -> QGroupBox:
        g = QGroupBox("高级设置")
        v = QVBoxLayout(g)

        self.chk_expert = QCheckBox("专家模式（显示全局启动参数等高级选项）")
        self.chk_expert.setChecked(bool(st.get("expert_mode")))
        self.chk_expert.setToolTip(
            "开启后可配置对所有实例生效的全局启动参数。\n"
            "普通使用无需打开；参数配错可能导致 ComfyUI 无法启动。")
        self.chk_expert.toggled.connect(self._on_expert_toggled)
        v.addWidget(self.chk_expert)

        tip = QLabel("全局参数对所有实例生效；实例设置里已单独指定的项目以实例为准。"
                     "改动需重启 ComfyUI 实例后生效。")
        tip.setStyleSheet("color:#7d8797;")
        tip.setWordWrap(True)
        v.addWidget(tip)

        # ---- 参数面板（专家模式才展开）----
        self.panel = QWidget()
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(0, 4, 0, 0)
        pv.setSpacing(8)
        la = st.get("launch") or {}

        pv.addWidget(self._radio_group(
            "显存调度", _VRAM_OPTS, la.get("vram", ""), "vram"))
        pv.addWidget(self._radio_group(
            "注意力引擎", _ATTN_OPTS, la.get("attn", ""), "attn"))
        pv.addWidget(self._radio_group(
            "缓存策略", _CACHE_OPTS, la.get("cache", ""), "cache"))
        pv.addWidget(self._radio_group(
            "精度提升", _UPCAST_OPTS, la.get("upcast", ""), "upcast"))

        # 常用开关（复选）
        gflags = QGroupBox("常用开关（可多选）")
        gl_grid = QGridLayout(gflags)
        self._flag_boxes = []
        saved_flags = set(la.get("flags") or [])
        for i, (text, val, desc) in enumerate(_FLAGS):
            cb = QCheckBox(text)
            cb.setToolTip(desc)
            cb.setProperty("flag_val", val)
            cb.setChecked(val in saved_flags)
            self._flag_boxes.append(cb)
            gl_grid.addWidget(cb, i // 2, i % 2)
        pv.addWidget(gflags)

        # 自定义参数
        gex = QGroupBox("自定义参数")
        vex = QVBoxLayout(gex)
        self.ed_global_extra = QLineEdit(la.get("extra", ""))
        self.ed_global_extra.setPlaceholderText(
            "原样追加到启动命令，如 --reserve-vram 4 --deterministic （空格分隔）")
        vex.addWidget(self.ed_global_extra)
        pv.addWidget(gex)

        v.addWidget(self.panel)
        self.panel.setVisible(self.chk_expert.isChecked())
        return g

    def _radio_group(self, title, opts, cur_val, obj_name) -> QGroupBox:
        """单选组：opts = [(显示文字, 参数值, 说明), ...]
        两列网格排布，压缩页面高度（竖排会让设置页过长）"""
        g = QGroupBox(title + "（单选）")
        grid = QGridLayout(g)
        grid.setHorizontalSpacing(16)
        bg = QButtonGroup(self)
        for i, (text, val, desc) in enumerate(opts):
            rb = QRadioButton(text)
            rb.setToolTip(desc)
            rb.setProperty("opt_val", val)
            bg.addButton(rb, i)
            rb.setChecked(val == (cur_val or ""))
            grid.addWidget(rb, i // 2, i % 2)
        setattr(self, "bg_" + obj_name, bg)
        return g

    def _on_expert_toggled(self, on):
        self.panel.setVisible(on)

    def _radio_val(self, bg) -> str:
        b = bg.checkedButton()
        return (b.property("opt_val") if b is not None else "") or ""

    # ------------------------------------------------------------ 保存
    def _save(self):
        st = self.cfg["settings"]
        st["mirror"] = self.cb_mirror.currentText()
        st["gh_proxy"] = self.ed_ghproxy.text().strip()
        st["git_proxy"] = self.ed_gitproxy.text().strip()
        # 专家模式 + 全局启动参数
        st["expert_mode"] = self.chk_expert.isChecked()
        st["launch"] = {
            "vram": self._radio_val(self.bg_vram),
            "attn": self._radio_val(self.bg_attn),
            "cache": self._radio_val(self.bg_cache),
            "upcast": self._radio_val(self.bg_upcast),
            "flags": [cb.property("flag_val") for cb in self._flag_boxes
                      if cb.isChecked()],
            "extra": self.ed_global_extra.text().strip(),
        }
        save_config(self.cfg)
        n_args = sum(1 for k in ("vram", "attn", "cache", "upcast")
                     if st["launch"][k]) + len(st["launch"]["flags"]) \
            + (1 if st["launch"]["extra"] else 0)
        if st["expert_mode"] and n_args:
            QMessageBox.information(
                self, "ComfyHub",
                f"设置已保存（全局启动参数 {n_args} 项，重启实例后生效）")
        else:
            QMessageBox.information(self, "ComfyHub", "设置已保存")
