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

QComboBox 默认不扫描：页面逻辑里有按 currentText() 取值的写法（如镜像源下拉、
显存模式），翻译会改掉**落盘的存储值**（"自动" 变 "Auto" 就再也没法匹配）。
所以下拉走**逐控件显式登记**：只有把 item 存进 itemData（`addItem(显示文, 值)`）
且取值一律走 currentData() 的下拉，才调 `w.setProperty("i18n_items", True)` 打开。
2026-11-25 起登记的：远程运维页的 认证方式 / 默认通道 / 平台模板 / 方法，
实例对话框的 类型。

语言可即时切换：apply_language(root, lang) 双向替换（英文查 _EN、
中文查反向表 _EN_REV），设置页保存后立刻扫一遍整窗。
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
    "保存后立即生效（表格行/日志等动态文案切页后对齐）":
        "Applies immediately after saving (dynamic text aligns on page switch)",
}

# 符号键词表（gettext 式）：给「带 {占位符} 的多行模板」和超长文案用。
# 这类串逐字抄进 _EN 当键有两个毛病：① 抄错一个标点就永远不命中；
# ② 中文原文一改，词表里的键悄悄失配、英文静默退回中文。
# 改成稳定符号键后，中文原文只存在于业务模块里，词表只放译文。
_KEYED = {}

# 首批词表在上，完整词表在 app/i18n_en.py（第二批按页补齐，数据与逻辑分离便于维护）
try:
    from app.i18n_en import EN_DICT as _EN_FULL
    _EN.update(_EN_FULL)
except Exception:       # 数据文件缺失也不能让界面起不来
    pass

try:
    from app.i18n_en import EN_KEYED as _KEYED_FULL
    _KEYED.update(_KEYED_FULL)
except Exception:       # 同上：符号键词表缺失不影响界面起不来
    pass

# 反向表（英文 → 中文）：即时切回中文时用。译文重复时取第一个键，
# 因为切换回中文只需要一个合理的中文原文，不保证和当初那一条完全同源。
_EN_REV = {}
for _k, _v in _EN.items():
    _EN_REV.setdefault(_v, _k)


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


def TK(name: str) -> str:
    """按符号键取译文（模板类文案用）

    中文模式或键未登记时返回空串，调用点写成 `TK("x") or 中文原串` 即可：
    中文永不查表、英文查不到也仍显示中文（不会出现空白）。
    """
    if _LANG == ZH:
        return ""
    return _KEYED.get(str(name or ""), "")


def _tr(text: str, lang: str) -> str:
    """按目标语言翻译一条文案（en=查正向表，zh=查反向表恢复中文）

    逐行翻译：侧栏导航项是「🏠\\n工作台」这种 emoji + 换行的多行文案，
    整串查表永远查不到，所以按行拆开分别翻译再拼回去。
    """
    if not isinstance(text, str) or not text:
        return text
    table = _EN if lang == EN else _EN_REV
    if "\n" in text:
        parts = text.split("\n")
        new = [table.get(p, p) for p in parts]
        return "\n".join(new) if new != parts else text
    return table.get(text, text)


def _translate_tree(root, lang: str = EN) -> int:
    """把控件树里命中的静态文案换成目标语言，返回替换条数"""
    from PySide6.QtWidgets import (
        QLabel, QAbstractButton, QGroupBox, QLineEdit, QTabWidget,
        QTableWidget, QListWidget, QMainWindow, QDialog, QComboBox,
        QPlainTextEdit,
    )
    n = 0

    def _set(obj, attr, value):
        nonlocal n
        new = _tr(value, lang)
        if new != value:
            getattr(obj, attr)(new)
            n += 1

    for w in root.findChildren(QLabel):
        if w.text():
            _set(w, "setText", w.text())
        if w.toolTip():
            _set(w, "setToolTip", w.toolTip())
    for w in root.findChildren(QAbstractButton):
        if w.text():
            _set(w, "setText", w.text())
        # 2026-11-25：按钮/单选框的 tooltip 之前完全没扫（只扫了 QLineEdit 的），
        # 远程运维页的「建立隧道」「重连」灰字提示因此永远是中文。
        if w.toolTip():
            _set(w, "setToolTip", w.toolTip())
    for w in root.findChildren(QGroupBox):
        if w.title():
            _set(w, "setTitle", w.title())
    for w in root.findChildren(QLineEdit):
        if w.placeholderText():
            _set(w, "setPlaceholderText", w.placeholderText())
        if w.toolTip():
            _set(w, "setToolTip", w.toolTip())
    # 2026-11-25：QPlainTextEdit 的占位符（远程运维的启动/停止命令、请求头、请求体）。
    # 占位符只做显示、不参与任何取值判断，全量扫是安全的。
    for w in root.findChildren(QPlainTextEdit):
        if w.placeholderText():
            _set(w, "setPlaceholderText", w.placeholderText())
    # 2026-11-25：下拉项 —— **只翻显式登记过 i18n_items 的**（见模块 docstring）。
    # 全量翻会把 "自动"/"强制 FP16" 之类的 currentText() 落盘值改成英文。
    for w in root.findChildren(QComboBox):
        if not w.property("i18n_items"):
            continue
        for i in range(w.count()):
            cur = w.itemText(i)
            new = _tr(cur, lang)
            if new != cur:
                w.setItemText(i, new)
                n += 1
    for w in root.findChildren(QTabWidget):
        for i in range(w.count()):
            new = _tr(w.tabText(i), lang)
            if new != w.tabText(i):
                w.setTabText(i, new)
                n += 1
    for w in root.findChildren(QTableWidget):
        hh = w.horizontalHeader()
        for c in range(w.columnCount()):
            item = w.horizontalHeaderItem(c)
            if item and item.text():
                new = _tr(item.text(), lang)
                if new != item.text():
                    item.setText(new)
                    n += 1
    for w in root.findChildren(QMainWindow) + root.findChildren(QDialog):
        if w.windowTitle():
            _set(w, "setWindowTitle", w.windowTitle())
    # 根节点自己也是窗口时（findChildren 不含自身）标题要单独翻一遍，
    # 否则主窗标题栏永远停在中文（2026-11-02 修）
    if isinstance(root, (QMainWindow, QDialog)) and root.windowTitle():
        _set(root, "setWindowTitle", root.windowTitle())
    # 侧栏导航 / 各类列表项（2026-11-02 补：QListWidget 的 item 之前完全没翻译，
    # 导致英文模式下左侧导航仍是中文，看着像"切换没生效"）
    for w in root.findChildren(QListWidget):
        for i in range(w.count()):
            it = w.item(i)
            if it is None or not it.text():
                continue
            new = _tr(it.text(), lang)
            if new != it.text():
                it.setText(new)
                n += 1
    return n


def apply_language(root, lang: str = ZH) -> int:
    """设置语言并翻译整棵控件树；返回替换条数（0 = 没有可译项）

    2026-11-02：支持**双向**即时切换。之前中文模式直接 return 0，
    导致「英文 → 中文」只能重启才恢复（界面上残留英文）。
    现在英文查 _EN、中文查反向表 _EN_REV，两个方向都能就地换回来。
    """
    set_lang(lang)
    return _translate_tree(root, _LANG)
