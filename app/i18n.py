# -*- coding: utf-8 -*-
"""界面多语言（2026-11-01 首批：中文 / 英文）

设计取舍（重要）：
  项目 1655 处中文字面量是硬编码的，一次性改成 Qt tr() + .ts/.qm 成本极高且易漏。
  这里用「词表 + 控件树扫描」的轻量方案：
    · 中英对照表 _EN 在下面维护，新增一条 = 新增一行；
    · apply_language(window, "en") 在窗口建完后扫一遍控件树，把命中的静态文案换成英文；
    · 没收录的文案原样保留中文（不会报错、不会空白），可分批补齐。
  局限：动态文本（运行日志、弹窗正文、表格行数据）不在扫描范围内，
        需要翻译时在调用点写 T("...")。

QComboBox 暂不扫描：页面逻辑里有按 currentText() 取值的写法（如镜像源下拉），
翻译会改掉存储值。等这批下拉统一改成 currentData() 后再开。

切换语言需要重启生效（页面在构建时完成翻译）。
"""

EN = "en"
ZH = "zh"

_LANG = ZH

# 中英对照表：键 = 代码里的中文原文（必须逐字一致，含标点）
_EN = {
    # ---------- 主窗 / 侧栏 / 顶栏 ----------
    "ComfyHub — ComfyUI 多实例管理器": "ComfyHub — ComfyUI Multi-Instance Manager",
    "工作台": "Workbench",
    "插件管理": "Plugins",
    "环境管理": "Environment",
    "模型库": "Models",
    "工作流": "Workflows",
    "控制台": "Console",
    "系统设置": "Settings",
    "当前实例": "Current instance",
    "＋ 添加 ComfyUI": "+ Add ComfyUI",
    "新装 ComfyUI": "Install ComfyUI",
    "（无实例，点右上角添加）": "(no instance yet — add one at the top right)",
    "显示主窗口": "Show main window",
    "退出 ComfyHub": "Quit ComfyHub",
    "自动": "Auto",
    # ---------- 设置页 ----------
    "设置": "Settings",
    "网络": "Network",
    "目录": "Directories",
    "界面": "Interface",
    "PyPI 镜像:": "PyPI mirror:",
    "GitHub 代理:": "GitHub proxy:",
    "Git Clone 代理:": "Git Clone proxy:",
    "工具目录:": "Tools directory:",
    "语言:": "Language:",
    "主题:": "Theme:",
    "关闭主窗口时:": "On window close:",
    "浏览…": "Browse…",
    "恢复默认": "Reset to default",
    "每次询问": "Ask every time",
    "直接退出程序": "Quit directly",
    "最小化到托盘": "Minimize to tray",
    "AI 接入（可选）": "AI assist (optional)",
    "启用 AI 接入（仅确定性查不到时调用）":
        "Enable AI assist (only when deterministic lookup fails)",
    "接口地址:": "Endpoint:",
    "模型名，如 deepseek-chat / glm-4-flash": "Model, e.g. deepseek-chat / glm-4-flash",
    "保存设置": "Save settings",
    "背景图片:": "Background image:",
    "选择图片…": "Choose image…",
    "清除": "Clear",
    "面板不透明度:": "Panel opacity:",
    "自定义主题的底图与通透度；改完点『保存设置』立即生效":
        "Backdrop and translucency for the custom theme; applied on Save",
    "重启 ComfyHub 后生效": "Takes effect after restarting ComfyHub",
}

# 首批词表在上，完整词表在 app/i18n_en.py（第二批按页补齐，数据与逻辑分离便于维护）
try:
    from app.i18n_en import EN_DICT as _EN_FULL
    _EN.update(_EN_FULL)
except Exception:       # 数据文件缺失也不能让界面起不来
    pass


def get_lang() -> str:
    return _LANG


def set_lang(lang: str) -> str:
    global _LANG
    _LANG = EN if str(lang or "").strip().lower().startswith("en") else ZH
    return _LANG


def T(text: str) -> str:
    """单条翻译：英文模式查表，查不到原样返回（中文模式下恒等）"""
    if _LANG == ZH or not isinstance(text, str):
        return text
    return _EN.get(text, text)


def _translate_tree(root) -> int:
    """把控件树里命中的静态文案换成当前语言，返回替换条数"""
    from PySide6.QtWidgets import (
        QLabel, QAbstractButton, QGroupBox, QLineEdit, QTabWidget,
        QTableWidget, QMainWindow, QDialog,
    )
    n = 0

    def _set(obj, attr, value):
        nonlocal n
        new = T(value)
        if new != value:
            getattr(obj, attr)(new)
            n += 1

    for w in root.findChildren(QLabel):
        if w.text():
            _set(w, "setText", w.text())
    for w in root.findChildren(QAbstractButton):
        if w.text():
            _set(w, "setText", w.text())
    for w in root.findChildren(QGroupBox):
        if w.title():
            _set(w, "setTitle", w.title())
    for w in root.findChildren(QLineEdit):
        if w.placeholderText():
            _set(w, "setPlaceholderText", w.placeholderText())
        if w.toolTip():
            _set(w, "setToolTip", w.toolTip())
    for w in root.findChildren(QTabWidget):
        for i in range(w.count()):
            new = T(w.tabText(i))
            if new != w.tabText(i):
                w.setTabText(i, new)
                n += 1
    for w in root.findChildren(QTableWidget):
        hh = w.horizontalHeader()
        for c in range(w.columnCount()):
            item = w.horizontalHeaderItem(c)
            if item and item.text():
                new = T(item.text())
                if new != item.text():
                    item.setText(new)
                    n += 1
    for w in root.findChildren(QMainWindow) + root.findChildren(QDialog):
        if w.windowTitle():
            _set(w, "setWindowTitle", w.windowTitle())
    return n


def apply_language(root, lang: str = ZH) -> int:
    """设置语言并翻译整棵控件树；返回替换条数（0 = 中文模式或没有可译项）"""
    set_lang(lang)
    if _LANG == ZH:
        return 0
    return _translate_tree(root)
