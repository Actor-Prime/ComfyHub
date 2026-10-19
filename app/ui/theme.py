# -*- coding: utf-8 -*-
"""QSS 主题：深色现代管理软件风格（对标管理大师的视觉调性）"""

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
QTableWidget::item:selected { background: #2f4a75; }

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
"""
