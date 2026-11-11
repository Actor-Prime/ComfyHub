# -*- coding: utf-8 -*-
"""QSS 主题：深色现代管理软件风格（设计系统 v2，2026-11-08）

色彩三层：
    页面 #141920 → 卡片/面板 #1b2029 → 输入/表格 #14181e（凹下去，形成层次）
文字三级：#e6edf7 主 / #94a2b4 次 / #6c7a8c 弱
强调色：#4c8dff（悬停 #5aa0ff），成功 #3ecf8e，警告 #e8a33d，危险 #f0616d

铁律：这里改任何颜色都必须同步 _LIGHT_MAP，否则浅色主题会残留深色值（会白底黑块）。
"""

import os as _os

DARK_QSS = """
QWidget {
    background-color: #1e2229;
    color: #d8dee6;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}
/* 纯文字控件不参与"面板底色"（2026-11-07 用户报障）：
   上面那条 QWidget 会把每个裸 QLabel/QCheckBox 也刷成一块矩形底色，
   表单里的字段名（接口地址/模型名…）背后就多出一条条深色小方块。
   带 id 的标签（#CardTitle/#BadgeInstalled 等）优先级更高，不受影响。 */
QLabel, QCheckBox, QRadioButton {
    background: transparent;
}
QMainWindow, QDialog {
    background-color: #141920;
}
QToolTip {
    background: #232a35;
    color: #e6edf7;
    border: 1px solid #333b47;
    border-radius: 6px;
    padding: 6px 9px;
}

/* ===== 左侧导航（窄图标栏）===== */
#SideNav {
    background-color: #0f1216;
    border-right: 1px solid #2c3442;
}
#SideNav QLabel#NavTitle {
    color: #94a2b4;
    font-size: 14px;
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
    color: #94a2b4;
    padding: 11px 16px;
    border-left: 3px solid transparent;
}
#SideNav QListWidget::item:hover {
    background: #1b2029;
    color: #e6edf7;
}
#SideNav QListWidget::item:selected {
    background: #212734;
    color: #ffffff;
    border-left: 3px solid #4c8dff;
}

/* ===== 窄图标导航条 ===== */
#NavRail {
    background-color: #0f1216;
    border-right: 1px solid #2c3442;
}
#NavRail QListWidget {
    background: transparent;
    border: none;
    outline: none;
    font-size: 11px;
    icon-size: 26px;
}
#NavRail QListWidget::item {
    color: #7f8b9c;
    padding: 7px 2px;
    /* 2026-11-08 用户反馈「有些部分贴着，没留出空，显得很奇怪」：
       margin 3→5、列表 spacing 2→4，项与项之间从"看起来连成一条"变成
       13px 的明确呼吸区，选中块也不会和邻居粘在一起 */
    margin: 5px 7px;
    border-radius: 10px;
    border: 1px solid transparent;
    text-align: center;
}
#NavRail QListWidget::item:hover {
    background: #1b2029;
    color: #c7d2e0;
}
#NavRail QListWidget::item:selected {
    background: #1d2a3d;
    color: #6fa8ff;
    border: 1px solid #31507f;
}
#NavRail QLabel#NavLogo {
    color: #4c8dff;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 0.4px;
    padding: 10px 0 6px;
    background: transparent;
}

/* ===== 顶栏 ===== */
#TopBar {
    background: #13171c;
    border-bottom: 1px solid #2c3442;
}
#TopBar QLabel#AppLogo {
    color: #e6edf7;
    font-size: 15px;
    font-weight: bold;
    background: transparent;
}
#TopBar QLabel#AppTitle {
    font-size: 15px;
    font-weight: bold;
    color: #e6edf7;
    background: transparent;
}

/* ===== 通用按钮 ===== */
QPushButton {
    background: #232a35;
    border: 1px solid #333b47;
    border-radius: 8px;
    padding: 7px 15px;
    color: #dbe3ee;
}
QPushButton:hover { background: #2a323f; border-color: #414c5d; }
QPushButton:pressed { background: #1c222b; }
QPushButton:focus { border-color: #4c8dff; }

QPushButton[accent="true"] {
    background: #3a72d8;
    border: 1px solid #4c8dff;
    color: white;
    font-weight: bold;
}
QPushButton[accent="true"]:hover { background: #4c8dff; }
QPushButton[accent="true"]:checked { background: #2f63c4; }

/* 纯图标按钮（固定 30~34px 宽）：全局 padding 会把内容区挤到 6px，
   图标被裁成半个（2026-11-07 用户报障「刷新键显示不全」），这里清零内边距 */
QPushButton#IconBtn { padding: 0px; border-radius: 8px; }

/* 生成设备切换（GPU/CPU 二选一） */
QPushButton#DeviceBtn {
    background: #1b2029;
    border: 1px solid #333b47;
    color: #94a2b4;
    padding: 9px 26px;
    font-weight: bold;
}
QPushButton#DeviceBtn:hover { border: 1px solid #4c8dff; color: #e6edf7; }
QPushButton#DeviceBtn:checked {
    background: #3a72d8;
    border: 1px solid #4c8dff;
    color: #ffffff;
}
QPushButton#DeviceBtn:disabled { color: #6c7a8c; background: #141920; }
QPushButton[danger="true"] {
    background: #d9534f;
    border: 1px solid #f0616d;
    color: white;
}
QPushButton[danger="true"]:hover { background: #e35f66; }
QPushButton[success="true"] {
    background: #2e8b57;
    border: 1px solid #3ecf8e;
    color: white;
}
QPushButton[success="true"]:hover { background: #35a06a; }
/* 禁用态统一灰色（含 success/danger/accent 等彩色按钮，避免"看着能点"） */
QPushButton:disabled {
    background: #1e242d;
    border: 1px solid #2a313b;
    color: #5f6b7a;
}

/* ===== 标签页 ===== */
/* 2026-11-12 用户反馈「环境管理页的 tab 像 UI 缺失」：
   未选中 tab 原来纯透明（background: transparent）+ 无边框，在深色页面底色上
   只剩几行灰字，整条标签栏看不出是个控件。现在未选中态给一档「比页面亮」的底
   (#171c23) + 细边框 (#2c3442)，选中态仍与面板同色并带 2px 蓝色指示条，
   层次为：页面 #141920 < 标签条 #171c23 < 面板 #1b2029 < 悬停 #1e242d。 */
QTabWidget::pane {
    border: 1px solid #343e4d;
    border-radius: 12px;
    top: -1px;
    background: #1b2029;
}
QTabBar::tab {
    background: #171c23;
    color: #94a2b4;
    padding: 9px 20px;
    border: 1px solid #2c3442;
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    margin-right: 2px;
}
QTabBar::tab:hover {
    color: #e6edf7;
    background: #1e242d;
    border-color: #343e4d;
}
QTabBar::tab:selected {
    background: #1b2029;
    color: #e6edf7;
    border-color: #343e4d;
    border-bottom: 2px solid #4c8dff;
}

/* ===== 表格 ===== */
/* 2026-11-12 用户报障「框边缘缺失」：QTableWidget 是 QAbstractScrollArea，
   内部 viewport 是矩形会盖住自身 QSS 的边框 —— 四个角的边框根本画不出来
   （横竖线在角上断成两截），圆角更无从谈起。
   所以边框/圆角统一交给外层 #TableCard 容器（见 theme.card_table），
   表格自己只保留行线/表头样式。 */
QTableWidget {
    background: transparent;
    alternate-background-color: #171c23;
    gridline-color: #2c3442;
    border: none;
    border-radius: 0;
}
#TableCard {
    background: #14181e;
    border: 1px solid #343e4d;
    border-radius: 12px;
}
#TableCard QTableWidget { background: transparent; border: none; }
#TableCard QHeaderView { background: transparent; }
#TableCard QHeaderView::section:first { border-top-left-radius: 11px; }
#TableCard QHeaderView::section:last { border-top-right-radius: 11px; }
QTableWidget::item { padding: 4px 6px; }
QHeaderView::section {
    background: #1b2029;
    color: #8c9aab;
    border: none;
    border-right: 1px solid #2c3442;
    border-bottom: 1px solid #343e4d;
    padding: 8px 10px;
    font-weight: bold;
}
/* 选中/焦点态必须显式给 color：只写 background 时 Qt 会丢掉 item 自设前景，
   回落默认黑字 → 蓝底黑字看不清（2026-10-27 用户实测） */
QTableWidget::item:selected { background: #2b4674; color: #ffffff; }
QTableWidget::item:focus { background: #2b4674; color: #ffffff; border: none; }
/* 表格内嵌按钮紧凑 padding（2026-10-27 用户报障「框住的字显示不全」）：
   item 的 padding 会把 cellWidget 一起挤小 —— 32px 行高落到按钮只剩 23px，
   基础按钮 padding 需要 26px 才装得下 12px 文字 → 上下被裁。
   紧凑后 sizeHint 稳进 23px 可用高；表外按钮不受此规则影响 */
QTableWidget QPushButton { padding: 2px 8px; }

/* ===== 输入框 ===== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: #14181e;
    border: 1px solid #3c4757;
    border-radius: 8px;
    padding: 7px 10px;
    color: #e6edf7;
    selection-background-color: #2b4674;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #414c5d; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus,
QPlainTextEdit:focus {
    border: 1px solid #4c8dff;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: #6c7a8c;
    background: #141920;
    border-color: #343e4d;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #1b2029;
    border: 1px solid #333b47;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #2b4674;
    outline: none;
}

/* ===== 复选框 ===== */
/* ★ 必须显式画 indicator：暗色 QSS 下不写这段时 Qt 用系统默认绘制，
   勾选框边框与深色背景融为一体，只剩一个勾（用户反馈"打勾时没有显示框"） */
QCheckBox, QRadioButton { color: #dbe3ee; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #4a5768;
    border-radius: 5px;
    background: #14181e;
}
QCheckBox::indicator:hover { border-color: #6f87a6; }
QCheckBox::indicator:checked { background: #3a72d8; border-color: #4c8dff; }
QCheckBox::indicator:checked:hover { background: #4c8dff; }
QCheckBox::indicator:disabled { background: #1b2029; border-color: #333b47; }
QCheckBox:disabled { color: #6c7a8c; }

/* ===== 卡片 ===== */
#Card {
    background: #1b2029;
    border: 1px solid #343e4d;
    border-radius: 12px;
}
#CardTitle {
    color: #94a2b4;
    font-size: 12px;
    background: transparent;
}
#CardValue {
    color: #ffffff;
    font-size: 20px;
    font-weight: bold;
    background: transparent;
}
#Card:hover { border-color: #3b4a60; }

/* ===== 工作台状态卡（#StatCard 系列，2026-11-08） ===== */
#StatCard {
    background: #1b2029;
    border: 1px solid #343e4d;
    border-radius: 12px;
}
#StatCard:hover { border-color: #33507a; }
#StatIcon {
    background: #212a37;
    border: 1px solid #2c3644;
    border-radius: 9px;
    font-size: 15px;
}
#StatTitle {
    color: #8c9aab;
    font-size: 12px;
    background: transparent;
}
#StatValue {
    color: #ffffff;
    font-size: 19px;
    font-weight: bold;
    background: transparent;
}
#StatSub { color: #6c7a8c; font-size: 11px; background: transparent; }
/* 状态卡底部的占用条（QProgressBar，文字隐藏） */
QProgressBar#StatBar {
    background: #232a35;
    border: none;
    border-radius: 3px;
    max-height: 5px;
    min-height: 5px;
}
QProgressBar#StatBar::chunk { background: #4c8dff; border-radius: 3px; }
QProgressBar#StatBar[warn="true"]::chunk { background: #e8a33d; }
QProgressBar#StatBar[full="true"]::chunk { background: #f0616d; }

/* ===== 日志 ===== */
#LogConsole {
    background: #0f1216;
    border: 1px solid #343e4d;
    border-radius: 12px;
    color: #b9c5d4;
    font-family: Consolas, "Cascadia Mono";
    padding: 8px;
}

/* ===== 滚动条（细）===== */
/* ===== 滚动条（2026-11-08 用户反馈「滑动条色差小不明显」）=====
   原来 handle 用 #333c49 贴在 #1b2029 上，明度差只有 3%，基本看不见。
   现在：轨道给一层极淡的底（让"可滚动区域"本身可见）+ handle 提到 #55637a，
   悬停 #6f7f99、按下走品牌蓝 —— 三级状态都有明确反馈。 */
QScrollBar:vertical {
    background: rgba(255, 255, 255, 0.055); width: 12px;
    margin: 2px 2px 2px 0; border-radius: 6px;
}
QScrollBar::handle:vertical {
    background: #55637a; border-radius: 5px; min-height: 36px; margin: 1px;
}
QScrollBar::handle:vertical:hover { background: #6f7f99; }
QScrollBar::handle:vertical:pressed { background: #4c8dff; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal {
    background: rgba(255, 255, 255, 0.055); height: 12px;
    margin: 0 2px 2px 2px; border-radius: 6px;
}
QScrollBar::handle:horizontal {
    background: #55637a; border-radius: 5px; min-width: 36px; margin: 1px;
}
QScrollBar::handle:horizontal:hover { background: #6f7f99; }
QScrollBar::handle:horizontal:pressed { background: #4c8dff; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ===== 分组框 ===== */
QGroupBox {
    border: 1px solid #343e4d;
    border-radius: 14px;
    margin-top: 20px;
    padding: 16px 14px 12px;
    background: #1b2029;
}
/* 标题挪到边框线之上（subcontrol-position: top left）：默认是垂直居中在
   margin 区里，会跟边框线交叠，半透明背景下那条线等于被标题劈开 */
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: #a9b7c8;
    font-size: 13px;
    font-weight: bold;
}

/* ===== 状态标签 ===== */
#StatusRun { color: #3ecf8e; font-weight: bold; background: transparent; }
#StatusStop { color: #7f8b9c; font-weight: bold; background: transparent; }
#HintText { color: #7f8b9c; background: transparent; }
#WarnText { color: #e8a33d; background: transparent; }

/* ===== Banner ===== */
/* 横幅容器：圆角与图片裁剪都在 _Banner.paintEvent 里自绘，QSS 只留兜底 */
#BannerBox {
    background: transparent;
    border: none;
}
/* 状态条（模型市场顶部「圆点 + 文案 + 操作按钮」那一条，objectName 沿用 Banner）
   ⚠️ 2026-11-08 坑：横幅改自绘时把这条规则一起改名成 #BannerBox，市场页状态条
   当场失去边框圆角、和下面的内容糊成一片（用户报「两个框之间贴太近」） */
/* 作用域徽章：标明某个设置是全局还是实例级 */
#ScopeBadge {
    background: rgba(76, 141, 255, 0.15);
    border: 1px solid rgba(76, 141, 255, 0.45);
    color: #9cc4ff;
    border-radius: 9px;
    padding: 1px 9px;
    font-size: 11px;
}

/* 修复进度条（不确定动画）：默认色是紫色，跟主题蓝不搭 */
#FixProgress { background: #232a35; border: none; border-radius: 2px; }
#FixProgress::chunk { background: #4c8dff; border-radius: 2px; }
#FixProgress[status="ok"]::chunk { background: #3ecf8e; border-radius: 2px; }
#FixProgress[status="fail"]::chunk { background: #f0616d; border-radius: 2px; }

/* 页面底部常驻栏上方的分隔线（设置页 / 实例设置页） */
#FooterSep { background: #2c3442; border: none; }

/* 环境检测行内的「修复」小按钮 */
#FixBtn {
    background: rgba(76, 141, 255, 0.16);
    border: 1px solid rgba(76, 141, 255, 0.55);
    color: #9cc4ff;
    border-radius: 6px;
    padding: 2px 12px;
    font-size: 11px;
}
#FixBtn:hover { background: rgba(76, 141, 255, 0.30); color: #ffffff; }
#FixBtn:disabled { background: rgba(255,255,255,0.05); border-color: #343e4d; color: #6c7a8c; }

#Banner {
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid #343e4d;
    border-radius: 10px;
}
/* Banner 上的文字垫层：照片底上直接写字可读性差，加一层左深右透的玻璃底 */
/* 横幅左侧玻璃底：背景与描边改由 _Scrim.paintEvent 自绘（见 home_page.py）
   —— QSS 的 qlineargradient 不会按 border-radius 裁剪，圆角处会漏出直角亮边，
   就是用户报的「圆角错误」。这里只留尺寸相关的声明。 */
#BannerScrim {
    background: transparent;
    border: none;
    border-radius: 12px;
}

/* ===== 行式设置项 ===== */
#RowCard {
    background: #1b2029;
    border: 1px solid #343e4d;
    border-radius: 12px;
}
#RowCard:hover { border-color: #313b4a; }
#RowTitle { color: #e6edf7; font-weight: bold; font-size: 13px; background: transparent; }
#RowDesc  { color: #8a97a9; font-size: 12px; background: transparent; }
/* 工作台/实例区外框 */
#SectionBox {
    border: 1px solid #343e4d;
    border-radius: 14px;
    background: #1b2029;
}
#SectionLabel {
    color: #a9b7c8;
    font-size: 13px;
    font-weight: bold;
    background: transparent;
    padding: 4px 0 6px;
}
/* 页面主标题（各页顶部）*/
#PageTitle {
    color: #e6edf7;
    font-size: 17px;
    font-weight: bold;
    background: transparent;
}
/* 行内筛选标签（模型库「来源 / 类别」）*/
#FilterLabel {
    color: #a9b7c8;
    font-size: 12px;
    background: transparent;
}

/* ===== 底部操作条（工作台/实例页）===== */
#ActionBar {
    background: #171c23;
    border: 1px solid #343e4d;
    border-radius: 12px;
}
#ActionBar QLabel { color: #8c9aab; font-size: 12px; }

/* ===== AI 输入卡（输入框 + 发送按钮包成一个整体）===== */
#ComposerCard {
    background: #1b2029;
    border: 1px solid #343e4d;
    border-radius: 14px;
}
#ComposerCard QTextEdit {
    background: transparent;
    border: none;
    padding: 4px 6px;
    font-size: 13px;
}

/* ===== 空状态（AI 助手等）===== */
#EmptyTitle {
    color: #dbe3ee;
    font-size: 16px;
    font-weight: bold;
    background: transparent;
}
#EmptyHint {
    color: #7f8b9c;
    font-size: 12px;
    background: transparent;
}
#EmptyIcon {
    color: #4c8dff;
    font-size: 30px;
    background: transparent;
}

/* ===== 环境检测行 ===== */
#CheckRow {
    background: #1b2029;
    border: 1px solid #343e4d;
    border-radius: 12px;
}
#CheckRow:hover { border-color: #313b4a; }
#CheckState {
    font-weight: bold;
    background: #232a35;
    color: #94a2b4;
    border: 1px solid transparent;
    border-radius: 11px;
    padding: 4px 14px;
}
#CheckState[state="ok"] {
    background: rgba(62, 207, 142, 0.14);
    color: #3ecf8e;
    border: 1px solid rgba(62, 207, 142, 0.42);
}
#CheckState[state="fail"] {
    background: rgba(240, 97, 109, 0.13);
    color: #f0616d;
    border: 1px solid rgba(240, 97, 109, 0.42);
}
#CheckState[state="pend"] {
    background: #232a35;
    color: #94a2b4;
}

/* ===== 全宽语义按钮 ===== */
QPushButton#SaveBtn {
    background: #3a72d8; color: #fff; font-weight: bold;
    border: 1px solid #4c8dff; border-radius: 10px; padding: 10px 22px;
}
QPushButton#SaveBtn:hover { background: #4c8dff; }
QPushButton#DeleteBtn {
    background: transparent; color: #f0616d; font-weight: bold;
    border: 1px solid rgba(240, 97, 109, 0.45); border-radius: 10px; padding: 10px 18px;
}
QPushButton#DeleteBtn:hover { background: rgba(240, 97, 109, 0.12); }

/* ===== 模型库统一搜索（范围切换/分类 chips） ===== */
#ScopeBtn {
    background: #1b2029;
    border: 1px solid #333b47;
    border-radius: 8px;
    padding: 6px 16px;
    color: #94a2b4;
    font-weight: bold;
}
#ScopeBtn:hover { border-color: #4c8dff; color: #e6edf7; }
#ScopeBtn:checked {
    background: #3a72d8;
    border-color: #4c8dff;
    color: #ffffff;
}
#CatChip {
    background: #1e242d;
    border: 1px solid #3c4757;
    border-radius: 11px;
    padding: 3px 12px;
    color: #94a2b4;
}
#CatChip:hover { border-color: #4c8dff; color: #e6edf7; }
#CatChip:checked {
    background: #2b4674;
    border-color: #4c8dff;
    color: #ffffff;
}
#BadgeInstalled {
    background: #16322a;
    color: #3ecf8e;
    border: 1px solid #2b6a53;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
}
"""


# ===== 下拉箭头（全 ComboBox 统一）=====
# 暗色 QSS 下 ::drop-down 只留了空位没画箭头（2026-10-26 用户反馈
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
# 根因：QSS 给 QTableWidget::item 加了上下 padding，QWidget 全局字号 12px，
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
# 为什么不做两份 QSS：模板 × 2 必然漂移，改深色忘了改浅色就会出现「浅色下某处仍是黑块」。
# 自定义主题 = 深色底 + 面板转半透明（rgba），由主窗背景层负责画用户图片。

# 深色 → 浅色对应值（漏掉的颜色保持原样，因此模板新增颜色时要回来补表）
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
    # ---- v2 新增底色（2026-11-08）----
    "#141920": "#f7f8fa", "#0f1216": "#eceff4", "#13171c": "#eef1f5",
    "#1b2029": "#ffffff", "#14181e": "#f4f7fb", "#171c23": "#eef2f7",
    "#1e242d": "#e9edf4", "#212734": "#e4ecf8", "#212a37": "#eaf0f8",
    "#2c3442": "#e3e9f1", "#232a35": "#e7ecf3", "#343e4d": "#dfe5ee",
    "#2a313b": "#e5eaf1", "#2b4674": "#cfe0fb", "#2c3644": "#e0e6ee",
    "#3c4757": "#ccd4e0", "#313b4a": "#cfd7e3", "#31507f": "#a9c3ea",
    # 滚动条 handle（2026-11-08 提高对比度后新增）
    "#55637a": "#b4bfcd", "#6f7f99": "#97a3b2",
    "#33507a": "#b6cbe8", "#3b4a60": "#c2ccda", "#1d2a3d": "#dbe6f8",
    "#16322a": "#e3f5e9", "#2b6a53": "#9fd3bc",
    # ---- 文本 / 中性灰 ----
    "#d8dee6": "#1f2430", "#e8edf3": "#12161d", "#b8c2ce": "#3c4655",
    "#9aa4b2": "#5b6675", "#8fa3bf": "#5a6b82", "#8a94a2": "#707c8b",
    "#7d8797": "#6b7684", "#5a636f": "#97a1ae", "#5a6472": "#98a2af",
    "#5a6a80": "#8b98a8",
    # v2 文本
    "#e6edf7": "#14191f", "#dbe3ee": "#1c222c", "#a9b7c8": "#4a5563",
    "#94a2b4": "#5b6675", "#8c9aab": "#67717f", "#7f8b9c": "#6f7a88",
    "#8a97a9": "#66717f",
    "#6c7a8c": "#77818f", "#5f6b7a": "#909aa8", "#4a5768": "#9aa5b3",
    "#6f87a6": "#7b8ea6", "#b9c5d4": "#3f4a58", "#c7d2e0": "#333d4a",
    # ---- 强调蓝 ----
    "#3d8bff": "#2470e0", "#3d7ee2": "#2a6ad0", "#4da3ff": "#2b7fe8",
    "#4a83f2": "#2a6ad0", "#2f6fd6": "#1f5fc4", "#2f6fed": "#1a56c4",
    "#1d5bb8": "#1a4fa0",
    # v2 强调蓝
    "#4c8dff": "#2470e0", "#5aa0ff": "#2a6ad0", "#3a72d8": "#1f5fc4",
    "#2f63c4": "#1a4fa0", "#6fa8ff": "#2b7fe8",
    # ---- 语义色（绿 / 红 / 橙）----
    "#4cd47c": "#16a34a", "#37a063": "#1b7d4e", "#379a63": "#1c7a4d",
    "#2e8b57": "#1f7a4a", "#e46e6e": "#c9503f", "#e8635a": "#cf4436",
    "#d95f5f": "#c0392b", "#b74646": "#b42318", "#b34848": "#b02a1f",
    "#a63a3a": "#b42318", "#e8a33d": "#a9750f", "#e8871e": "#b8670a",
    # v2 语义色
    "#3ecf8e": "#16a34a", "#f0616d": "#cf222e", "#d9534f": "#c0392b",
    "#e35f66": "#c9503f", "#35a06a": "#1b7d4e", "#47536a": "#aeb9c8",
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


# 自定义背景下的"描边补偿"（2026-11-07 用户报障）：
# 深灰描边色会被 _dark_surfaces 一并换成 45% 半透明的同色，
# 叠在背景图上等于没有 → 分组框/卡片成了"没边框的直角色块"，与页面之间只剩色差，
# 观感就是断层。这里统一换成半透明白描边 + 更大圆角，让边界由线定义而不是由色差定义。
_CUSTOM_EDGE_PATCH = """
QGroupBox, QTableWidget, #Card, #Banner, #RowCard, #SectionBox,
#StatCard, #CheckRow, #ActionBar, #ComposerCard, QStackedWidget#PaneCard {
    border: 1px solid rgba(255,255,255,0.30);
    border-radius: 12px;
}
QStackedWidget#PaneCard { border-radius: 14px; }
QStackedWidget#PaneCard QTableWidget { border: none; border-radius: 0px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 8px;
}
QGroupBox::title { color: #dbe3ee; }
#Card:hover, #StatCard:hover, #RowCard:hover { border-color: #4c8dff; }

"""


# 靠 objectName 吃 QSS 背景 + 圆角的「卡片类」控件。
# Qt 铁律：**QWidget/QFrame 要 QSS 画背景和圆角，必须开 WA_StyledBackground**，
# 否则样式表只管描边、不裁圆角也不填背景 —— 表现就是「圆角缺失、边框只剩直边」
# （2026-11-12 用户报障：工作台目录卡、实例设置行卡、环境检测卡全是直角）。
_CARD_IDS = {
    "Card", "Banner", "RowCard", "SectionBox", "StatCard", "CheckRow",
    "ActionBar", "ComposerCard", "PaneCard", "BannerBox", "TableCard",
    # 带背景+圆角的徽章类（QLabel）：同样交给样式表画背景，圆角才不会漏直角
    "ScopeBadge", "BadgeInstalled", "CheckState", "CatChip", "StatIcon",
}


def card_table(tb) -> "QFrame":
    """把 QTableWidget 套进圆角卡片，返回容器（**用它 addWidget，别用表格本身**）。

    原因：表格内部 viewport 是矩形，会盖住自身 QSS 边框 —— 角部边框画不出来
    （用户报障「框边缘缺失」），圆角也永远是直角。边框/圆角交给外层容器后，
    表头的首尾 section 再补上圆角，四角就都顺了。
    """
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QFrame, QVBoxLayout
    box = QFrame()
    box.setObjectName("TableCard")
    box.setAttribute(_Qt.WA_StyledBackground, True)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(1, 1, 1, 1)      # 留 1px 让表格不压掉容器边框
    lay.setSpacing(0)
    lay.addWidget(tb)
    return box


def polish_styled_bg(root) -> int:
    """兜底：遍历控件树，给卡片类控件补 WA_StyledBackground 并重新 polish。

    源码里该设的地方都设了，这里是防漏网（动态创建的卡片、后加的新卡片 ID）。
    返回本次补开的控件数。
    """
    if root is None:
        return 0
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QWidget as _QW
    n = 0
    try:
        targets = [root] + list(root.findChildren(_QW))
    except Exception:
        return 0
    for w in targets:
        try:
            if w.objectName() not in _CARD_IDS:
                continue
            if w.testAttribute(_Qt.WA_StyledBackground):
                continue
            w.setAttribute(_Qt.WA_StyledBackground, True)
            w.style().unpolish(w)
            w.style().polish(w)
            n += 1
        except Exception:
            continue
    return n


def page_scrim_rgba(alpha: float, strength: float = 0.25) -> str:
    """页面级底板颜色（2026-11-07）。

    strength=0.25：表单页用——底下已叠分组框/卡片多层，页面层只补薄薄一层统一色调；
    strength=1.0：聊天类页面（AI 助手）用——聊天容器被 custom 主题强制透明、
    卡片本身很淡，需要页面层自己承担一整层（等效单层左栏）才有可读性。
    strength 按层叠系数缩放：0.25 → eff*0.25，1.0 → eff*0.6（与全局面板同档）。
    """
    eff = max(0.08, min(1.0, float(alpha) ** 1.8 * 0.6))
    return f"rgba(22,25,31,{eff * strength:.3f})"


def build_qss(mode: str = "dark", alpha: float = 0.72) -> str:
    """按模式产出 QSS：dark 原样 / light 换调色板 / custom 深色底 + 面板半透明"""
    resolved = resolve_mode(mode)
    if resolved == "light":
        return _replace_colors(DARK_QSS, _LIGHT_MAP)
    if resolved == "custom":
        # 面板是多层嵌套的（页面 → 分组 → 卡片），alpha 直接相乘会把背景图吃光：
        # 三层 55% 只剩 9% 透光。幂次衰减缓解一部分。
        # 2026-11-07 用户要求「右侧 UI 透明度跟左侧栏一致，否则自定义背景失去意义」：
        # 实测三层 eff=0.6 叠乘后页面亮度只有左栏的 1/3（35 vs 96），
        # 这里再乘 0.6，让叠乘后的整体透光率接近单层左栏，图能明显透出来。
        eff = max(0.08, min(1.0, float(alpha) ** 1.8 * 0.6))
        mapping = {c: _rgba(c, eff) for c in _dark_surfaces()}
        # #Backdrop 是主窗背景层（画用户图片），必须透明才能露出图片；
        # QScrollArea 视口本身吃 palette.Base（不透明），不显式置透会把整页涂死
        return (_replace_colors(DARK_QSS, mapping)
                + "\n#Backdrop { background: transparent; }\n"
                + "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }\n"
                + _CUSTOM_EDGE_PATCH)
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
    pal.setColor(QPalette.Highlight, QColor("#cfe0fb" if is_light else "#2b4674"))
    pal.setColor(QPalette.HighlightedText, QColor("#1f2430" if is_light else "#ffffff"))
    pal.setColor(QPalette.Base, QColor(255 if is_light else 20, 255 if is_light else 24,
                                       255 if is_light else 30, a))
    pal.setColor(QPalette.AlternateBase, QColor(242 if is_light else 23, 245 if is_light else 28,
                                                249 if is_light else 35, a))
    pal.setColor(QPalette.Text, QColor("#1f2430" if is_light else "#e6edf7"))
    pal.setColor(QPalette.WindowText, QColor("#1f2430" if is_light else "#e6edf7"))
    pal.setColor(QPalette.Window, QColor(247 if is_light else 23, 248 if is_light else 27,
                                         250 if is_light else 33, a))
    app.setPalette(pal)
    _LAST_APPLIED = sig
    return {"mode": mode, "resolved": resolved, "alpha": alpha, "bg_image": bg}


def polish_tables(root) -> int:
    """统一表头/表格观感（2026-11-08 设计系统 v2 补丁）

    Qt 默认表头文字**居中**、单元格文字**左对齐** → 宽列（如「插件名」在 1600
    窗口下能到 500px）时表头飘在列中间、数据贴左边，视觉上像两套栅格。
    QSS 的 text-align 对 QHeaderView::section 无效，只能在代码里设 defaultAlignment。
    返回处理的表数量，方便测试断言。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget, QTreeWidget
    n = 0
    for t in root.findChildren(QTableWidget):
        # QTableWidget 只有 horizontalHeader()/verticalHeader()，没有 header()
        hh = t.horizontalHeader()
        if hh is None:
            continue
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # 表头不参与"整列高亮"（否则点一下列头整列发白，很脏）
        hh.setHighlightSections(False)
        n += 1
    for t in root.findChildren(QTreeWidget):
        hh = t.header()
        if hh is None:
            continue
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        n += 1
    return n
