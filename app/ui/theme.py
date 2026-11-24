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

/* ===== 自绘标题栏（2026-11-14：窗口 Frameless 后，这条栏就是标题栏。
   布局保持旧顶栏形态，底色透明 —— 和内容区（Backdrop）完全一致，
   深浅主题都自动对，不再有「两条杠两种色」的割裂感） ===== */
#TitleBar {
    background: transparent;
    border: none;
}
#TitleBar QLabel#AppLogo {
    color: #e6edf7;
    font-size: 15px;
    font-weight: bold;
    background: transparent;
}
/* 窗口控制三键：图标是矢量自绘（title_bar._glyph_pixmap），这里只管底板 ——
   常态透明、悬停浅灰、关闭悬停红 */
#TbMin, #TbMax, #TbClose {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0px;
}
#TbMin:hover, #TbMax:hover { background: #2a323f; color: #e6edf7; }
#TbClose:hover { background: #c42b1c; color: #ffffff; }
#TbMin:pressed, #TbMax:pressed, #TbClose:pressed { background: #1c222b; }

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

/* 插件加载失败横幅（plugin_banner.py）：暗琥珀底 + 同族描边。
   放全局而不是控件里 setStyleSheet——半透明色在浅色主题下没有映射，
   叠出来是一块看不懂的深棕（2026-11-14 实测），进全局表才能跟主题走。 */
#PluginBanner {
    background: #332b1a;
    border-bottom: 1px solid #59492a;
}
#PluginBanner QLabel { background: transparent; color: #d8c9a3; }

/* http 明文告警条（remote_banner.py，0.8.40）：暗琥珀偏黄，比插件条更"警示"一点。
   同样放全局表，浅色主题交给 _LIGHT_MAP。 */
#RemoteBanner {
    background: #3d3418;
    border-bottom: 1px solid #6a5a20;
}
#RemoteBanner QLabel { background: transparent; color: #e8c46a; }

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

/* ===== AI 输入卡（2026-11-20 参考 WorkBuddy：上层输入、下层工具栏）===== */
#ComposerCard {
    background: #1b2029;
    border: 1px solid #343e4d;
    border-radius: 16px;
}
/* 2026-11-20 P2：字号 13 → 14，行距放开一点；输入框是每天都用的地方，
   13px 在这个尺度下显得局促。占位符颜色 QSS 管不到（Qt 的 PlaceholderText
   是调色板角色），由 ai_page._apply_placeholder_style() 按主题设置。 */
#ComposerCard QTextEdit {
    background: transparent;
    border: none;
    padding: 6px 8px;
    font-size: 14px;
}
/* 输入卡聚焦态（2026-11-20 P2）：描边点亮，用户一眼确认「现在能打字了」。
   颜色用项目既有的强调色 #4c8dff，与 QPushButton:focus 保持一致；
   强调色在深浅两套主题下通用，故不进 _LIGHT_MAP。 */
#ComposerCard[focused="true"] {
    border: 1px solid #4c8dff;
}
/* 工具栏上的次要按钮（会话设定 / 停止） */
QPushButton#ComposerGhost {
    background: transparent;
    border: 1px solid #343e4d;
    border-radius: 8px;
    color: #94a2b4;
    font-size: 12px;
    padding: 0 10px;
}
QPushButton#ComposerGhost:hover {
    background: #232a34;
    color: #e6edf7;
    border-color: #3c4757;
}
QPushButton#ComposerGhost:disabled {
    color: #5a636f;
    border-color: #2a313b;
}
/* 圆形发送键 */
QPushButton#ComposerSend {
    background: #4c8dff;
    border: none;
    border-radius: 16px;
    color: #ffffff;
    font-size: 16px;
    font-weight: bold;
}
QPushButton#ComposerSend:hover { background: #5aa0ff; }
QPushButton#ComposerSend:pressed { background: #3d78e0; }
QPushButton#ComposerSend:disabled { background: #2a313b; color: #5a636f; }
/* 生成中 = 停止态（2026-11-20 合并键）：中性深底 + 白方块，
   与空闲的蓝色形成明确区分，但尺寸完全不变，切换时按钮不跳。
   ⚠️ ■ 在 16px 下只有 ~8px 宽，配深底看着像一根细竖条，认不出是「停止」，
   所以深色态单独把字号提到 22px（方块约 15px，在 32px 圆里刚好饱满）。 */
QPushButton#ComposerSend[busy="true"] {
    background: #2a313b;
    color: #e6edf7;
    font-size: 22px;
}
QPushButton#ComposerSend[busy="true"]:hover { background: #343e4d; }
QPushButton#ComposerSend[busy="true"]:pressed { background: #232830; }

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
/* 组件包还没下完时用橙色，跟「已就绪」的绿分开 */
#BadgeInstalled[ok="false"] {
    background: #3a2a16;
    color: #e8a33e;
    border-color: #6a512b;
}
#CompBar { background: transparent; }
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
#
# 2026-11-14 用户反馈「浅色模式太白，看久了眼睛疼」：
# 原来底/面用的是 #ffffff / #eef2f7（冷白），整屏亮度拉满，对比强烈但刺眼。
# 现在换成**暖米白渐变**（色相偏黄、亮度整体降一档），层次仍靠明度差区分：
#   页面 #f0ede4 < 凹陷 #e9e6dc < 卡片 #faf8f2 < 悬停 #f5f3ec
# 文字改成暖近黑（#231f18 系），暖底 + 暖字比冷白 + 纯黑柔和一个量级。
# 按钮做成「比卡片深一档 + 更明显边框」，否则浅色下按钮和卡片糊成一片（用户报障「按钮没有色差」）。
_LIGHT_MAP = {
    # ---- 底 / 面板 / 边框 ----
    "#12151a": "#edeae1", "#16191f": "#f0eee6", "#181c22": "#f3f1ea",
    "#1a1e25": "#eeece3", "#1c212a": "#ebe8df", "#1e2229": "#f7f5ee",
    "#20252d": "#e7e4da", "#20252e": "#faf8f2", "#22262e": "#e4e1d7",
    "#232830": "#e0ddd3", "#232a34": "#dfe6f4", "#232c3a": "#dbe4f2",
    "#242932": "#e1ded4", "#262c36": "#dddad0", "#2a2f38": "#e2dfd5",
    "#2a3038": "#e6e3d9", "#2a313c": "#e0e4ee", "#333c49": "#c9c5ba",
    "#363f4d": "#c3bfb4", "#3a4250": "#b8b4a9", "#414c5d": "#ada99e",
    "#2f4a75": "#cfe0fb", "#1e3a28": "#e3f0e6",
    # ---- v2 新增底色（2026-11-08）----
    "#141920": "#eeece3", "#0f1216": "#e8e5db", "#13171c": "#ece9e0",
    "#1b2029": "#faf8f2", "#14181e": "#e9e6dc", "#171c23": "#f5f3ec",
    "#1e242d": "#efede4", "#212734": "#e6ecf7", "#212a37": "#eaf0f8",
    "#2c3442": "#e2dfd5", "#232a35": "#ece7d9", "#343e4d": "#d8d4c8",
    "#2a313b": "#e5e2d8", "#2b4674": "#d5e3f9", "#2c3644": "#e0dcd2",
    "#3c4757": "#cac6bb", "#313b4a": "#d0cdc2", "#31507f": "#a9c3ea",
    # 滚动条 handle（2026-11-08 提高对比度后新增）
    "#55637a": "#c3beb2", "#6f7f99": "#a7a296",
    "#33507a": "#b6cbe8", "#3b4a60": "#c1bdb2", "#1d2a3d": "#dbe6f8",
    "#16322a": "#e3f0e6", "#2b6a53": "#9fd3bc",
    # ---- 文本 / 中性灰（暖近黑，别用纯黑：纯黑压在米白上反差过猛）----
    "#d8dee6": "#231f18", "#e8edf3": "#171310", "#b8c2ce": "#3f3a31",
    "#9aa4b2": "#5c574c", "#8fa3bf": "#4f6480", "#8a94a2": "#736e63",
    "#7d8797": "#6d685d", "#5a636f": "#979186", "#5a6472": "#989287",
    "#5a6a80": "#7b8798",
    # v2 文本
    "#e6edf7": "#1f1b14", "#dbe3ee": "#2a251d", "#a9b7c8": "#4e493f",
    "#94a2b4": "#5c574c", "#8c9aab": "#676157", "#7f8b9c": "#5b564c",
    "#8a97a9": "#666156",
    "#6c7a8c": "#787268", "#5f6b7a": "#918b80", "#4a5768": "#8a8478",
    "#6f87a6": "#6b7f97", "#b9c5d4": "#3f3a30", "#c7d2e0": "#332e25",
    # 控件级样式表里出现过的（2026-11-14 补：AI 页会话列表/搜索框/气泡）
    "#c9d3e0": "#2f2a21", "#8a93a3": "#6b6559", "#a9b1bd": "#565146",
    "#c3c9d4": "#3a352c", "#e6eaf0": "#2f2a21", "#7f8894": "#6f695e",
    "#a9b7c8": "#4e493f", "#262e3a": "#e8e5db", "#2f3a4a": "#dbe3f2",
    "#1a1f27": "#f3f1e9", "#2a3340": "#d5d1c5", "#232b36": "#e6e3d9",
    "#12161c": "#eae7de", "#1f2430": "#221e17", "#2a323f": "#e3ddcd",
    "#1c222b": "#dbd5c3", "#333b47": "#cdc7b8",
    # ---- 强调蓝 ----
    "#3d8bff": "#2470e0", "#3d7ee2": "#2a6ad0", "#4da3ff": "#1f5fc4",
    "#4a83f2": "#2a6ad0", "#2f6fd6": "#1f5fc4", "#2f6fed": "#1a56c4",
    "#1d5bb8": "#1a4fa0",
    # v2 强调蓝
    "#4c8dff": "#1f5fc4", "#5aa0ff": "#2a6ad0", "#3a72d8": "#1f5fc4",
    "#2f63c4": "#1a4fa0", "#6fa8ff": "#1f5fc4", "#2f6fb2": "#1f5fc4",
    # ---- 语义色（绿 / 红 / 橙）----
    "#4cd47c": "#0b7a3c", "#37a063": "#1b7d4e", "#379a63": "#1c7a4d",
    "#2e8b57": "#1f7a4a", "#e46e6e": "#c9503f", "#e8635a": "#cf4436",
    "#d95f5f": "#c0392b", "#b74646": "#b42318", "#b34848": "#b02a1f",
    "#a63a3a": "#b42318", "#e8a33d": "#a9750f", "#e8871e": "#b8670a",
    # v2 语义色
    "#3ecf8e": "#0b7a3c", "#f0616d": "#cf222e", "#d9534f": "#c0392b",
    "#e35f66": "#c9503f", "#35a06a": "#1b7d4e", "#47536a": "#aeb9c8",
    # 插件加载失败横幅（#PluginBanner）
    "#332b1a": "#f3ead6", "#59492a": "#d9c697", "#d8c9a3": "#5a4a22",
    # 明文告警条（#RemoteBanner）
    "#3d3418": "#fbf2d4", "#6a5a20": "#e0c268", "#e8c46a": "#7a5b00",
    "#a9944f": "#9a7c1e",
    "#e2888a": "#c0392b", "#e24b4a": "#c0392b", "#ff9c9c": "#b42318",
    "#ff6b6b": "#b42318",
    # 运行期着色（QColor / 富文本）用到的
    "#6fb3ff": "#1f5fc4", "#7aa2d0": "#456080", "#ffb347": "#a9750f", "#b78cff": "#6b4bb8",
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


_RGBA_RE_SRC = r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+%?)\s*\)"


def _replace_colors(qss: str, mapping: dict) -> str:
    """把模板里的 hex / rgba 颜色按表替换（大小写不敏感，未入表的原样保留）

    rgba 也要换（2026-11-14）：页面里大量半透明面板写的是
    `background: rgba(27,32,41,0.92)`，只认 hex 的话浅色主题下这些面板会留在深色，
    表现就是「白底里嵌一块黑板」。规则：用 RGB 三元组去表里找浅色，alpha 原样保留。
    """
    import re
    out = re.sub(r"#[0-9a-fA-F]{6}",
                 lambda m: mapping.get(m.group(0).lower(), m.group(0)), qss)

    def _sub(m):
        key = "#%02x%02x%02x" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        tgt = mapping.get(key)
        if not tgt or not tgt.startswith("#") or len(tgt) != 7:
            return m.group(0)          # 表里没有 / 目标不是 hex（自定义主题）→ 不动
        r, g, b = _hex_rgb(tgt)
        return "rgba(%d,%d,%d,%s)" % (r, g, b, m.group(4))

    return re.sub(_RGBA_RE_SRC, _sub, out)


_SURFACE_SET = None

# ============ 控件级样式表的主题重染（2026-11-14） ============
# 为什么需要：全局 QSS 走 _replace_colors，但页面里还有 ~200 处
# `w.setStyleSheet("background:#1b2029; color:#c9d3e0")` —— 控件级样式，
# 完全不经过主题表。浅色主题下这些控件就留在深色（截图里 AI 页会话列表整块黑），
# 而主题切换时控件级样式也不会重建。
# 做法：在 QWidget.setStyleSheet 上装一层「记住深色原文 + 按当前模式重染」，
# 主题切换时 retint_all() 重放。以后新增代码照常 setStyleSheet 就自动跟随主题，
# 不需要每处手写判断 —— 这条规则要一直保留。
_CUR_MODE = "dark"
_HOOKED = False


def current_mode() -> str:
    """当前实际渲染模式（dark / light / custom）"""
    return _CUR_MODE


def tint(qss: str) -> str:
    """按当前模式重染一段控件级 QSS（dark/custom 原样返回）"""
    if not qss or _CUR_MODE != "light":
        return qss
    return _replace_colors(qss, _LIGHT_MAP)


def style(widget, qss: str) -> None:
    """给控件套主题感知样式表（等价 setStyleSheet，但浅色下自动换色）

    setStyleSheet 已被 install_widget_hooks() 接管，这里只是给新代码一个
    语义明确、读起来知道「这段样式会跟着主题走」的入口。
    """
    widget.setStyleSheet(qss)


def fg(color: str) -> str:
    """运行期前景色（QColor / 富文本 span）：浅色主题下换成语义相同的深色

    QSS 那条路有 _replace_colors，但 `QColor("#4cd47c")`、`<span style='color:#...'>`
    这类写死在代码里的颜色 QSS 管不到 —— 浅色主题下就成了「浅底 + 粉彩字」，
    控制台日志实测几乎读不出来。这些地方统一走 fg()。
    """
    if _CUR_MODE != "light" or not color:
        return color
    return _LIGHT_MAP.get(str(color).strip().lower(), color)


def install_widget_hooks() -> bool:
    """给 QWidget.setStyleSheet 装主题重染（幂等，返回是否本次新装）"""
    global _HOOKED
    if _HOOKED:
        return False
    from PySide6.QtWidgets import QWidget
    _orig = QWidget.setStyleSheet

    def _set_style_sheet(self, qss):
        # 只记"作者写的深色原文"，主题切换时才能重染出正确结果
        try:
            self._theme_src_qss = qss
        except Exception:
            pass
        _orig(self, tint(qss) if qss else qss)

    QWidget.setStyleSheet = _set_style_sheet
    _HOOKED = True
    return True


def retint_all(app=None) -> int:
    """主题切换后重放所有控件级样式表，返回重染的数量"""
    from PySide6.QtWidgets import QApplication
    app = app or QApplication.instance()
    if app is None:
        return 0
    n = 0
    for w in app.allWidgets():
        src = getattr(w, "_theme_src_qss", None)
        if not src:
            continue
        try:
            w.setStyleSheet(src)
            n += 1
        except Exception:
            continue
    return n


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


# ============ 背景图可读性自适应（2026-11-14 用户反馈「暗图黑字看不见、
# 亮图亮字看不清」方案 A） ============

_BG_STATS_CACHE = {}     # path -> ((mtime, size), stats)


def analyze_background(path: str) -> dict:
    """图片亮度统计：avg / p10 / p90（0~1 灰度），带 mtime 缓存。

    缩到 64×64 再采样 —— 人眼对背景图的「明暗分布」感知是低频的，
    4096 个点足够稳，且大图也瞬间完成。
    """
    from PySide6.QtGui import QImage
    from PySide6.QtCore import Qt as _Qt
    try:
        st = _os.stat(path)
        sig = (int(st.st_mtime), st.st_size)
    except OSError:
        sig = None
    hit = _BG_STATS_CACHE.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    stats = {"avg": 0.5, "p10": 0.5, "p90": 0.5}
    img = QImage(path)
    if not img.isNull():
        small = img.scaled(64, 64, _Qt.IgnoreAspectRatio,
                           _Qt.SmoothTransformation)
        vals = []
        for y in range(small.height()):
            for x in range(small.width()):
                vals.append(small.pixelColor(x, y).lightness() / 255.0)
        vals.sort()
        n = len(vals) or 1
        stats = {"avg": sum(vals) / n,
                 "p10": vals[int(n * 0.10)],
                 "p90": vals[min(n - 1, int(n * 0.90))]}
    _BG_STATS_CACHE[path] = (sig, stats)
    return stats


def auto_contrast(bg_path: str, user_alpha: float) -> dict:
    """按图片亮度决定 custom 主题的字色极性与面板最低不透明度。

    · 字色极性：图片平均亮度 ≥0.55 判为「偏亮图」→ 浅底深字（light 极性），
      否则维持深底亮字（dark 极性，UI 默认观感）。
    · 底板浓度：文字色由极性定死，最怕图里「和文字同亮度」的局部 ——
      透过底板后的最坏背景亮度 B' = B·(1-a) + S·a 必须与文字拉开 0.35：
        dark 极性取 B=p90（亮斑是威胁），light 极性取 B=p10（暗斑是威胁）。
    · 用户滑杆是不透明度「下限」：auto 只会往上抬，不会把用户调透的改实。
    """
    user_alpha = max(0.35, min(1.0, float(user_alpha or 0.72)))
    if not bg_path:
        return {"resolved": "dark", "alpha": user_alpha}
    st = analyze_background(bg_path)
    LIGHT_TEXT, DARK_TEXT = 0.87, 0.13      # 亮字/暗字的感知亮度
    SCRIM_DARK, SCRIM_LIGHT = 0.10, 0.92    # 深/浅底板的感知亮度
    REQ = 0.35                              # 文字与透底后背景的最小亮度差
    if st["avg"] >= 0.55:
        resolved = "light"
        B = st["p10"]                       # 浅底深字：怕图里暗的局部
        need = 0.0
        if B < DARK_TEXT + REQ and SCRIM_LIGHT > B:
            need = ((DARK_TEXT + REQ) - B) / (SCRIM_LIGHT - B)
    else:
        resolved = "dark"
        B = st["p90"]                       # 深底亮字：怕图里亮的局部
        need = 0.0
        if B > LIGHT_TEXT - REQ and B > SCRIM_DARK:
            need = (B - (LIGHT_TEXT - REQ)) / (B - SCRIM_DARK)
    a = max(0.35, min(0.97, max(user_alpha, need)))
    return {"resolved": resolved, "alpha": a}


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


def page_scrim_rgba(alpha: float, strength: float = 0.25,
                    light: bool = False) -> str:
    """页面级底板颜色（2026-11-07）。

    strength=0.25：表单页用——底下已叠分组框/卡片多层，页面层只补薄薄一层统一色调；
    strength=1.0：聊天类页面（AI 助手）用——聊天容器被 custom 主题强制透明、
    卡片本身很淡，需要页面层自己承担一整层（等效单层左栏）才有可读性。
    strength 按层叠系数缩放：0.25 → eff*0.25，1.0 → eff*0.6（与全局面板同档）。
    light=True：自定义主题 + 偏亮背景图的浅色极性（2026-11-14 方案 A）——
    底板换成暖米白半透明，配深色字。
    """
    eff = max(0.08, min(1.0, float(alpha) ** 1.8 * 0.6))
    if light:
        return f"rgba(250,248,242,{eff * strength:.3f})"
    return f"rgba(22,25,31,{eff * strength:.3f})"


# 浅色专属纠偏（2026-11-14）：
# 模板里有些「白字」是压在深色底上的（如状态卡数值、表格选中行、CatChip 选中态）。
# 底色被换成浅色后白字就看不见了 —— 这些不能靠颜色映射修（#ffffff 同时是
# 强调按钮的前景色，映射成深色反而把蓝按钮写坏），只能按选择器单独纠偏。
_LIGHT_PATCH = """
#CardValue, #StatValue { color: #221e17; }
#SideNav QListWidget::item:selected { color: #221e17; }
QTableWidget::item:selected, QTableWidget::item:focus { color: #221e17; }
#CatChip:checked { color: #221e17; }
#FixBtn:hover { color: #221e17; }
#StatBar { background: #e0ddd3; }
"""


def build_qss(mode: str = "dark", alpha: float = 0.72,
              custom_light: bool = False) -> str:
    """按模式产出 QSS：dark 原样 / light 换调色板 / custom 面板半透明。

    custom_light（2026-11-14 方案 A）：自定义主题 + 偏亮背景图 → 整体换
    浅色调色板后再把「面板表面」换成半透明浅色，深色字压在浅底板上才可读。
    """
    resolved = resolve_mode(mode)
    if resolved == "light":
        return _replace_colors(DARK_QSS, _LIGHT_MAP) + _LIGHT_PATCH
    if resolved == "custom":
        # 面板是多层嵌套的（页面 → 分组 → 卡片），alpha 直接相乘会把背景图吃光：
        # 三层 55% 只剩 9% 透光。幂次衰减缓解一部分。
        # 2026-11-07 用户要求「右侧 UI 透明度跟左侧栏一致，否则自定义背景失去意义」：
        # 实测三层 eff=0.6 叠乘后页面亮度只有左栏的 1/3（35 vs 96），
        # 这里再乘 0.6，让叠乘后的整体透光率接近单层左栏，图能明显透出来。
        eff = max(0.08, min(1.0, float(alpha) ** 1.8 * 0.6))
        if custom_light:
            # 先整体换浅色调色板（含白字纠偏补丁），再把浅色表面换回半透明
            qss = _replace_colors(DARK_QSS, _LIGHT_MAP) + _LIGHT_PATCH
            mapping = {}
            for c in _dark_surfaces():
                lt = _LIGHT_MAP.get(c, c)
                mapping[lt] = _rgba(lt, eff)
            qss = _replace_colors(qss, mapping)
        else:
            mapping = {c: _rgba(c, eff) for c in _dark_surfaces()}
            qss = _replace_colors(DARK_QSS, mapping)
        # #Backdrop 是主窗背景层（画用户图片），必须透明才能露出图片；
        # QScrollArea 视口本身吃 palette.Base（不透明），不显式置透会把整页涂死
        return (qss
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

    幂等：同一 (模式, 极性, 透明度, 模糊, 背景图) 重复调用直接跳过 ——
    app.setStyleSheet 会让全应用控件重新 polish，多窗场景（测试矩阵反复建窗）
    下开销会累积成分钟级（2026-11-01 实测）。

    custom 模式（2026-11-14 方案 A）：auto_contrast() 按背景图亮度决定
    字色极性（偏亮图→浅底深字）并抬高面板不透明度下限，保证可读性。
    """
    from PySide6.QtGui import QPalette, QColor
    global _LAST_APPLIED, _CUR_MODE
    mode, alpha, bg = theme_conf(cfg)
    st = (cfg or {}).get("settings") or {}
    try:
        blur = max(0, min(24, int(st.get("bg_blur", 0) or 0)))
    except (TypeError, ValueError):
        blur = 0
    # 背景图位置/缩放（2026-11-14 调整窗）：zoom 1~4，off 归一化 -0.5~0.5
    def _f(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(st.get(key, default) if st.get(key) is not None else default)))
        except (TypeError, ValueError):
            return default
    bg_zoom = _f("bg_zoom", 1.0, 1.0, 4.0)
    bg_off_x = _f("bg_off_x", 0.0, -0.5, 0.5)
    bg_off_y = _f("bg_off_y", 0.0, -0.5, 0.5)
    resolved = resolve_mode(mode)
    custom_light = False
    if resolved == "custom":
        ac = auto_contrast(bg, alpha)
        custom_light = (ac["resolved"] == "light")
        alpha = ac["alpha"]
    sig = (resolved, custom_light, round(alpha, 3), blur, bg)
    info = {"mode": mode, "resolved": resolved, "custom_light": custom_light,
            "alpha": alpha, "bg_image": bg, "blur": blur,
            "bg_zoom": bg_zoom, "bg_off_x": bg_off_x, "bg_off_y": bg_off_y}
    if sig == _LAST_APPLIED and not force:
        return info
    # 顺序要紧：先切模式 → 再产 QSS → 再重染控件级样式，
    # 否则 tint() 会用上一轮的模式替换，浅色下残留深色（这就是之前的老毛病）
    # custom_light 也按 light 走：运行期 fg() 颜色要跟着换深色
    _CUR_MODE = "light" if (resolved == "light" or custom_light) else resolved
    install_widget_hooks()
    app.setStyleSheet(build_qss(resolved, alpha, custom_light=custom_light))
    retint_all(app)

    # 调色板：单元格 current、输入框 base 等回落路径仍走系统 palette，
    # 深色系统下 highlight=黑会「单击单元格变黑」（2026-10-27 实测），跟着主题一起给值。
    is_light = (resolved == "light" or custom_light)
    is_custom = (resolved == "custom")
    # 自定义主题：palette 也带 alpha，否则走 palette 的回落路径（视口/输入框）
    # 会用不透明深色把背景图整片盖掉
    a = int(max(0.0, min(1.0, alpha)) * 255) if is_custom else 255
    pal = app.palette()
    pal.setColor(QPalette.Highlight, QColor("#cfe0fb" if is_light else "#2b4674"))
    pal.setColor(QPalette.HighlightedText, QColor("#221e17" if is_light else "#ffffff"))
    # 浅色回落底色跟 QSS 同一档暖米白（2026-11-14）：palette 漏改会出现
    # 「输入框/视口是冷白 #ffffff、周围是米白」的花屏感
    pal.setColor(QPalette.Base, QColor(250 if is_light else 20, 248 if is_light else 24,
                                       242 if is_light else 30, a))
    pal.setColor(QPalette.AlternateBase, QColor(245 if is_light else 23, 243 if is_light else 28,
                                                236 if is_light else 35, a))
    pal.setColor(QPalette.Text, QColor("#221e17" if is_light else "#e6edf7"))
    pal.setColor(QPalette.WindowText, QColor("#221e17" if is_light else "#e6edf7"))
    pal.setColor(QPalette.Window, QColor(240 if is_light else 23, 237 if is_light else 27,
                                         228 if is_light else 33, a))
    app.setPalette(pal)
    _LAST_APPLIED = sig
    return info


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


# 模块导入即装钩子（2026-11-14）：
# 只在 apply_theme 里装就晚了 —— 主窗在 apply_theme 之前就构造好了页面，
# 那些控件级样式没被记录，之后主题切换/重染都摸不到它们（浅色下就是黑块）。
try:
    install_widget_hooks()
except Exception:
    pass
