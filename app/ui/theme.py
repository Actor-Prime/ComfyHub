# -*- coding: utf-8 -*-
"""QSS 主题：深色现代管理软件风格（对标管理大师的视觉调性）"""

import os as _os

DARK_QSS = """
QWidget {
    background-color: #1e2229;
    color: #d8dee6;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}
QMainWindow, QDialog {
    background-color: #1e2229;
}

/* ===== 左侧导航 ===== */
#SideNav {
    background-color: #16191f;
    border-right: 1px solid #2a2f38;
}
#SideNav QLabel#NavTitle {
    color: #7d8797;
    font-size: 15px;
    font-weight: bold;
    padding: 14px 12px 10px;
    background: transparent;
}
#SideNav QListWidget {
    background: transparent;
    border: none;
    outline: none;
    font-size: 13px;
}
#SideNav QListWidget::item {
    color: #9aa4b2;
    padding: 11px 16px;
    border-left: 3px solid transparent;
}
#SideNav QListWidget::item:hover {
    background: #20252d;
    color: #d8dee6;
}
#SideNav QListWidget::item:selected {
    background: #232a34;
    color: #ffffff;
    border-left: 3px solid #3d8bff;
}

/* ===== 顶部工具条 ===== */
#TopBar {
    background: #181c22;
    border-bottom: 1px solid #2a2f38;
}
#TopBar QLabel#AppTitle {
    font-size: 14px;
    font-weight: bold;
    color: #e8edf3;
    background: transparent;
}

/* ===== 通用按钮 ===== */
QPushButton {
    background: #2a313c;
    border: 1px solid #363f4d;
    border-radius: 4px;
    padding: 6px 14px;
    color: #d8dee6;
}
QPushButton:hover { background: #333c49; }
QPushButton:pressed { background: #232a34; }
QPushButton:disabled { color: #5a636f; background: #242932; }

QPushButton[accent="true"] {
    background: #2f6fd6;
    border: 1px solid #2f6fd6;
    color: white;
    font-weight: bold;
}
QPushButton[accent="true"]:hover { background: #3d7ee2; }
QPushButton[accent="true"]:disabled { color: #5a636f; background: #242932; }
QPushButton[accent="true"]:checked { background: #1d5bb8; }

/* 生成设备切换（GPU/CPU 二选一） */
QPushButton#DeviceBtn {
    background: #20252e;
    border: 1px solid #363f4d;
    color: #9aa4b2;
    padding: 9px 26px;
    font-weight: bold;
}
QPushButton#DeviceBtn:hover { border: 1px solid #3d8bff; color: #d8dee6; }
QPushButton#DeviceBtn:checked {
    background: #2f6fd6;
    border: 1px solid #3d7ee2;
    color: #ffffff;
}
QPushButton#DeviceBtn:disabled { color: #5a636f; background: #1a1e25; }
QPushButton[danger="true"] {
    background: #a63a3a;
    border: 1px solid #b34848;
    color: white;
}
QPushButton[danger="true"]:hover { background: #b74646; }
QPushButton[success="true"] {
    background: #2e8b57;
    border: 1px solid #379a63;
    color: white;
}
QPushButton[success="true"]:hover { background: #37a063; }
/* 禁用态统一灰色（含 success/danger/accent 等彩色按钮，避免"看着能点"） */
QPushButton:disabled {
    background: #232830;
    border: 1px solid #2a3038;
    color: #5a6472;
}

/* ===== 标签页 ===== */
QTabWidget::pane {
    border: 1px solid #2a2f38;
    border-radius: 4px;
    top: -1px;
}
QTabBar::tab {
    background: #181c22;
    color: #8a94a2;
    padding: 8px 18px;
    border: 1px solid #2a2f38;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #232a34;
    color: #ffffff;
    border-bottom: 2px solid #3d8bff;
}

/* ===== 表格 ===== */
QTableWidget {
    background: #181c22;
    alternate-background-color: #1c212a;
    gridline-color: #262c36;
    border: 1px solid #2a2f38;
    border-radius: 4px;
}
QHeaderView::section {
    background: #20252e;
    color: #9aa4b2;
    border: none;
    border-right: 1px solid #2a2f38;
    border-bottom: 1px solid #2a2f38;
    padding: 6px 8px;
    font-weight: bold;
}
QTableWidget::item { padding: 4px 6px; }
/* 选中/焦点态必须显式给 color：只写 background 时 Qt 会丢掉 item 自设前景，
   回落默认黑字 → 蓝底黑字看不清（2026-10-27 用户实测） */
QTableWidget::item:selected { background: #2f4a75; color: #ffffff; }
QTableWidget::item:focus { background: #2f4a75; color: #ffffff; border: none; }
/* 表格内嵌按钮紧凑 padding（2026-10-27 用户报障「框住的字显示不全」）：
   item 的 4px padding 会把 cellWidget 一起挤小 —— 32px 行高落到按钮只剩 23px，
   而基础按钮 padding 6px 需要 26px 才装得下 12px 文字 → 上下被裁。
   紧凑到 2px 后 sizeHint 降到 18px，稳进 23px 可用高；表外按钮不受此规则影响 */
QTableWidget QPushButton { padding: 2px 8px; }

/* ===== 输入框 ===== */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: #181c22;
    border: 1px solid #333c49;
    border-radius: 4px;
    padding: 5px 8px;
    color: #d8dee6;
    selection-background-color: #2f4a75;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #3d8bff;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #20252e;
    border: 1px solid #333c49;
    selection-background-color: #2f4a75;
}

/* ===== 复选框 ===== */
/* ★ 必须显式画 indicator：暗色 QSS 下不写这段时 Qt 用系统默认绘制，
   勾选框边框与深色背景融为一体，只剩一个勾（用户反馈"打勾时没有显示框"） */
QCheckBox { color: #d8dee6; spacing: 6px; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid #5a6a80;
    border-radius: 3px;
    background: #181c22;
}
QCheckBox::indicator:hover { border-color: #8fa3bf; }
QCheckBox::indicator:checked { background: #2f6fed; border-color: #3d8bff; }
QCheckBox::indicator:checked:hover { background: #4a83f2; }
QCheckBox::indicator:disabled { background: #22262e; border-color: #3a4250; }
QCheckBox:disabled { color: #667; }

/* ===== 卡片 ===== */
#Card {
    background: #20252e;
    border: 1px solid #2a2f38;
    border-radius: 6px;
}
#CardTitle {
    color: #9aa4b2;
    font-size: 12px;
    background: transparent;
}
#CardValue {
    color: #ffffff;
    font-size: 17px;
    font-weight: bold;
    background: transparent;
}

/* ===== 日志 ===== */
#LogConsole {
    background: #12151a;
    border: 1px solid #2a2f38;
    color: #b8c2ce;
    font-family: Consolas;
}

/* ===== 滚动条 ===== */
QScrollBar:vertical {
    background: #181c22; width: 10px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #333c49; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #414c5d; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal {
    background: #181c22; height: 10px; margin: 0;
}
QScrollBar::handle:horizontal {
    background: #333c49; border-radius: 5px; min-width: 30px;
}

/* ===== 分组框 ===== */
QGroupBox {
    border: 1px solid #2a2f38;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
    color: #9aa4b2;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}

/* ===== 状态标签 ===== */
#StatusRun { color: #4cd47c; font-weight: bold; background: transparent; }
#StatusStop { color: #7d8797; font-weight: bold; background: transparent; }
#HintText { color: #7d8797; background: transparent; }
#WarnText { color: #e8a33d; background: transparent; }

/* ===== 窄图标导航条（对标管理大师） ===== */
#NavRail {
    background-color: #12151a;
    border-right: 1px solid #262c36;
}
#NavRail QListWidget {
    background: transparent;
    border: none;
    outline: none;
    font-size: 11px;
    icon-size: 26px;
}
#NavRail QListWidget::item {
    color: #8a94a2;
    padding: 4px 2px;
    margin: 4px 4px;
    border-radius: 6px;
    border: 1px solid transparent;
    text-align: center;
}
#NavRail QListWidget::item:hover { background: #1c212a; }
#NavRail QListWidget::item:selected {
    background: #232c3a;
    color: #4da3ff;
    border: 1px solid #2f4a75;
}
#NavRail QLabel#NavLogo {
    color: #e8871e;
    font-size: 15px;
    font-weight: bold;
    padding: 14px 0 8px;
    background: transparent;
}

/* ===== 顶栏 ===== */
#TopBar {
    background: #16191f;
    border-bottom: 1px solid #262c36;
}
#TopBar QLabel#AppLogo {
    color: #e8871e;
    font-size: 14px;
    font-weight: bold;
    background: transparent;
}

/* ===== Banner ===== */
#Banner {
    border: 1px solid #2a2f38;
    border-radius: 8px;
}

/* ===== 行式设置项 ===== */
#RowCard {
    background: #181c22;
    border: 1px solid #262c36;
    border-radius: 6px;
}
#RowTitle { color: #d8dee6; font-weight: bold; font-size: 12px; background: transparent; }
#RowDesc  { color: #7d8797; font-size: 11px; background: transparent; }
#SectionLabel {
    color: #9aa4b2;
    font-size: 13px;
    font-weight: bold;
    background: transparent;
    padding: 6px 0;
}

/* ===== 环境检测行 ===== */
#CheckRow {
    background: #181c22;
    border: 1px solid #262c36;
    border-radius: 6px;
}
#CheckState {
    font-weight: bold;
    background: #262c36;
    color: #9aa4b2;
    border: 1px solid transparent;
    border-radius: 11px;
    padding: 5px 16px;
}
#CheckState[state="ok"] {
    background: rgba(76, 212, 124, 0.13);
    color: #4cd47c;
    border: 1px solid rgba(76, 212, 124, 0.45);
}
#CheckState[state="fail"] {
    background: rgba(232, 99, 90, 0.12);
    color: #e8635a;
    border: 1px solid rgba(232, 99, 90, 0.45);
}
#CheckState[state="pend"] {
    background: #262c36;
    color: #9aa4b2;
}

/* ===== 全宽语义按钮 ===== */
QPushButton#SaveBtn {
    background: #2f6fd6; color: #fff; font-weight: bold;
    border: none; border-radius: 6px; padding: 11px;
}
QPushButton#SaveBtn:hover { background: #3d7ee2; }
QPushButton#DeleteBtn {
    background: #d95f5f; color: #fff; font-weight: bold;
    border: none; border-radius: 6px; padding: 11px;
}
QPushButton#DeleteBtn:hover { background: #e46e6e; }

/* ===== 模型库统一搜索（范围切换/分类 chips/卡片） ===== */
#ScopeBtn {
    background: #20252e;
    border: 1px solid #363f4d;
    border-radius: 4px;
    padding: 6px 18px;
    color: #9aa4b2;
    font-weight: bold;
}
#ScopeBtn:hover { border-color: #3d8bff; color: #d8dee6; }
#ScopeBtn:checked {
    background: #2f6fd6;
    border-color: #3d7ee2;
    color: #ffffff;
}
#CatChip {
    background: #2a313c;
    border: 1px solid #363f4d;
    border-radius: 11px;
    padding: 3px 12px;
    color: #9aa4b2;
}
#CatChip:hover { border-color: #3d8bff; color: #d8dee6; }
#CatChip:checked {
    background: #2f4a75;
    border-color: #3d8bff;
    color: #ffffff;
}
#Card:hover { border-color: #3d8bff; }
#BadgeInstalled {
    background: #1e3a28;
    color: #4cd47c;
    border: 1px solid #2e8b57;
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 11px;
}
"""


# ===== 下拉箭头（全 ComboBox 统一）=====
# 暗色 QSS 下 ::drop-down 只留了 22px 空位没画箭头（2026-10-26 用户反馈
# "稳定版/开发版筛选看不出是下拉"），这里显式指定箭头图标。
# 路径：theme.py 在 app/ui/ 下，资源在项目根 assets/（打包后同构：
# _internal/app/ui/theme.py → _internal/assets/，与 main.py 取 icon 同口径）
_ARROW = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "assets", "arrow_down.png")
if _os.path.isfile(_ARROW):
    DARK_QSS += """
QComboBox::down-arrow { image: url("%s"); width: 12px; height: 12px; }
""" % (_ARROW.replace("\\", "/"))


# ===== 表格行高统一（2026-10-27 用户报障「字体显示不全」）=====
# 根因：QSS 给 QTableWidget::item 加了 4px 上下 padding，QWidget 全局字号 12px，
# 文字实际需要 ~25px 行高，而 Qt 默认行高仅 24~26px —— 临界裁字（截图实测贴边）。
# 只有插件市场表当年单独设了 30，其余表全是默认值。这里统一 32px，
# 主窗建成后调 fit_all_tables() 一次性扫全部表格，以后新页面/新表自动覆盖。
TABLE_ROW_H = 32


def fit_table(tb, row_h: int = TABLE_ROW_H) -> None:
    """统一表格行高：默认值管新建行，min 防止被拖小，循环兜底已存在的行"""
    vh = tb.verticalHeader()
    vh.setDefaultSectionSize(row_h)
    vh.setMinimumSectionSize(row_h)
    for r in range(tb.rowCount()):
        tb.setRowHeight(r, row_h)


def fit_all_tables(root, row_h: int = TABLE_ROW_H) -> int:
    """扫 root 子树里全部 QTableWidget 统一行高，返回处理数量"""
    from PySide6.QtWidgets import QTableWidget
    n = 0
    for tb in root.findChildren(QTableWidget):
        fit_table(tb, row_h)
        n += 1
    return n


# ================= 主题系统：深色 / 浅色 / 跟随系统 / 自定义（2026-11-01） =================
# 只有一份 QSS 模板（DARK_QSS），换主题 = 按调色板逐色替换。
# 为什么不做两份 QSS：453 行 × 2 必然漂移，改深色忘了改浅色就会出现「浅色下某处仍是黑块」。
# 自定义主题 = 深色底 + 面板转半透明（rgba），由主窗背景层负责画用户图片。

# 深色 53 色 → 浅色对应值（漏掉的颜色保持原样，因此模板新增颜色时要回来补表）
_LIGHT_MAP = {
    # ---- 底 / 面板 / 边框 ----
    "#12151a": "#e4e8ee", "#16191f": "#eef1f5", "#181c22": "#f2f5f9",
    "#1a1e25": "#eef1f6", "#1c212a": "#ebeff5", "#1e2229": "#f7f8fa",
    "#20252d": "#e9eef6", "#20252e": "#ffffff", "#22262e": "#e6ebf2",
    "#232830": "#e2e8f0", "#232a34": "#dbe6f8", "#232c3a": "#d9e3f4",
    "#242932": "#e3e9f1", "#262c36": "#dfe5ee", "#2a2f38": "#dfe4ec",
    "#2a3038": "#e7ebf2", "#2a313c": "#dfe6f0", "#333c49": "#ccd4e0",
    "#363f4d": "#c4ccd8", "#3a4250": "#b9c3d1", "#414c5d": "#aeb9c8",
    "#2f4a75": "#cfe0fb", "#1e3a28": "#e3f5e9",
    # ---- 文本 / 中性灰 ----
    "#d8dee6": "#1f2430", "#e8edf3": "#12161d", "#b8c2ce": "#3c4655",
    "#9aa4b2": "#5b6675", "#8fa3bf": "#5a6b82", "#8a94a2": "#707c8b",
    "#7d8797": "#6b7684", "#5a636f": "#97a1ae", "#5a6472": "#98a2af",
    "#5a6a80": "#8b98a8",
    # ---- 强调蓝 ----
    "#3d8bff": "#2470e0", "#3d7ee2": "#2a6ad0", "#4da3ff": "#2b7fe8",
    "#4a83f2": "#2a6ad0", "#2f6fd6": "#1f5fc4", "#2f6fed": "#1a56c4",
    "#1d5bb8": "#1a4fa0",
    # ---- 语义色（绿 / 红 / 橙）----
    "#4cd47c": "#16a34a", "#37a063": "#1b7d4e", "#379a63": "#1c7a4d",
    "#2e8b57": "#1f7a4a", "#e46e6e": "#c9503f", "#e8635a": "#cf4436",
    "#d95f5f": "#c0392b", "#b74646": "#b42318", "#b34848": "#b02a1f",
    "#a63a3a": "#b42318", "#e8a33d": "#a9750f", "#e8871e": "#b8670a",
}

# 亮度阈值：低于它的颜色视为「底 / 面」，自定义主题下转半透明；
# 文本色（偏亮）与强调色保持不透明，否则字会跟着背景一起淡掉。
_SURFACE_MAX_LUM = 80.0

THEME_MODES = ("dark", "light", "system", "custom")


def _hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lum(h: str) -> float:
    r, g, b = _hex_rgb(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgba(h: str, alpha: float) -> str:
    """QSS 的 rgba() 第四参用百分比最稳：写 0-255 的整数时 Qt 会按
    「数值×255」再钳到 255 → 面板全不透明，背景图完全看不见（2026-11-01 实测踩到）"""
    r, g, b = _hex_rgb(h)
    pct = max(0, min(100, int(round(alpha * 100))))
    return "rgba(%d,%d,%d,%d%%)" % (r, g, b, pct)


def _replace_colors(qss: str, mapping: dict) -> str:
    """把模板里的 hex 颜色按表替换（大小写不敏感，未入表的原样保留）"""
    import re
    return re.sub(r"#[0-9a-fA-F]{6}",
                  lambda m: mapping.get(m.group(0).lower(), m.group(0)), qss)


_SURFACE_SET = None


def _dark_surfaces() -> set:
    """模板里所有「底 / 面」颜色（懒计算一次）"""
    global _SURFACE_SET
    if _SURFACE_SET is None:
        import re
        colors = {m.group(0).lower() for m in re.finditer(r"#[0-9a-fA-F]{6}", DARK_QSS)}
        _SURFACE_SET = {c for c in colors if _lum(c) <= _SURFACE_MAX_LUM}
    return _SURFACE_SET


def system_is_light() -> bool:
    """Windows 系统主题是否浅色（AppsUseLightTheme：1=浅色 0=深色）"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(val)
        finally:
            key.Close()
    except Exception:
        return False    # 读不到就当深色（应用默认观感，不会突然闪白）


def resolve_mode(mode: str) -> str:
    """设置值 → 实际渲染模式：system 折成 light / dark"""
    mode = (mode or "dark").strip().lower()
    if mode == "system":
        return "light" if system_is_light() else "dark"
    if mode in ("dark", "light", "custom"):
        return mode
    return "dark"


def build_qss(mode: str = "dark", alpha: float = 0.72) -> str:
    """按模式产出 QSS：dark 原样 / light 换调色板 / custom 深色底 + 面板半透明"""
    resolved = resolve_mode(mode)
    if resolved == "light":
        return _replace_colors(DARK_QSS, _LIGHT_MAP)
    if resolved == "custom":
        # 面板是多层嵌套的（页面 → 分组 → 卡片），alpha 直接相乘会把背景图吃光：
        # 三层 55% 只剩 9% 透光。这里做幂次衰减，让滑块的默认值真正肉眼可辨。
        eff = max(0.08, min(1.0, float(alpha) ** 1.8))
        mapping = {c: _rgba(c, eff) for c in _dark_surfaces()}
        # #Backdrop 是主窗背景层（画用户图片），必须透明才能露出图片；
        # QScrollArea 视口本身吃 palette.Base（不透明），不显式置透会把整页涂死
        return (_replace_colors(DARK_QSS, mapping)
                + "\n#Backdrop { background: transparent; }\n"
                + "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }\n")
    return DARK_QSS


def theme_conf(cfg: dict) -> tuple:
    """(模式, 面板不透明度, 背景图路径)，越界值兜底"""
    st = (cfg or {}).get("settings") or {}
    mode = str(st.get("theme") or "dark").strip().lower()
    if mode not in THEME_MODES:
        mode = "dark"
    try:
        alpha = float(st.get("bg_opacity", 72)) / 100.0
    except (TypeError, ValueError):
        alpha = 0.72
    alpha = max(0.35, min(1.0, alpha))    # 低于 35% 字看不清，直接夹住
    return mode, alpha, str(st.get("bg_image") or "")


_LAST_APPLIED = None     # (模式, 透明度)：同一组合不重复 setStyleSheet


def apply_theme(app, cfg: dict = None, force: bool = False) -> dict:
    """应用主题：QSS + 调色板（QSS 覆盖不到的回落路径）。返回生效信息供背景层使用。

    幂等：同一 (模式, 透明度) 重复调用直接跳过 —— app.setStyleSheet 会让全应用控件
    重新 polish，多窗场景（测试矩阵反复建窗）下开销会累积成分钟级（2026-11-01 实测）。
    """
    from PySide6.QtGui import QPalette, QColor
    global _LAST_APPLIED
    mode, alpha, bg = theme_conf(cfg)
    resolved = resolve_mode(mode)
    sig = (resolved, round(alpha, 3))
    if sig == _LAST_APPLIED and not force:
        return {"mode": mode, "resolved": resolved, "alpha": alpha, "bg_image": bg}
    app.setStyleSheet(build_qss(resolved if resolved == "custom" else resolved, alpha))

    # 调色板：单元格 current、输入框 base 等回落路径仍走系统 palette，
    # 深色系统下 highlight=黑会「单击单元格变黑」（2026-10-27 实测），跟着主题一起给值。
    is_light = (resolved == "light")
    is_custom = (resolved == "custom")
    # 自定义主题：palette 也带 alpha，否则走 palette 的回落路径（视口/输入框）
    # 会用不透明深色把背景图整片盖掉
    a = int(max(0.0, min(1.0, alpha)) * 255) if is_custom else 255
    pal = app.palette()
    pal.setColor(QPalette.Highlight, QColor("#cfe0fb" if is_light else "#2f4a75"))
    pal.setColor(QPalette.HighlightedText, QColor("#1f2430" if is_light else "#ffffff"))
    pal.setColor(QPalette.Base, QColor(255 if is_light else 24, 255 if is_light else 28,
                                       255 if is_light else 34, a))
    pal.setColor(QPalette.AlternateBase, QColor(242 if is_light else 28, 245 if is_light else 33,
                                                249 if is_light else 42, a))
    pal.setColor(QPalette.Text, QColor("#1f2430" if is_light else "#d8dee6"))
    pal.setColor(QPalette.WindowText, QColor("#1f2430" if is_light else "#d8dee6"))
    pal.setColor(QPalette.Window, QColor(247 if is_light else 30, 248 if is_light else 34,
                                         250 if is_light else 41, a))
    app.setPalette(pal)
    _LAST_APPLIED = sig
    return {"mode": mode, "resolved": resolved, "alpha": alpha, "bg_image": bg}
