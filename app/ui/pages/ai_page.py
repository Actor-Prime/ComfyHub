# -*- coding: utf-8 -*-
"""ai_page.py - AI 助手页（方案 C：卡片消息流，2026-11-03）

布局：
  顶部状态行（实例点 · 端口 · 清空）
  消息流（用户胶囊气泡 / 助手整宽卡片（复制·重跑）/ 工具调用时间线）
  快捷指令条（体检·队列·工作流·最近报错）
  输入区（Ctrl+Enter 发送，运行中可停止）

关键实现：换行文本高度用 QFontMetrics.boundingRect 精确计算——
QLabel.heightForWidth 在带样式表 padding 时返回单行高度，会把多行文本裁掉
（2026-11-03 用户截图报障的真因）。
"""
import threading

import json
import os
import time

from PySide6.QtCore import (QBuffer, QEvent, QRect, QSettings, QSize, Qt, QThread,
                            QTime, QTimer, Signal)
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QStyle, QTabWidget, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from app import ai_agent, ai_chat_store, ai_skills
from app.i18n import T


def _fmt_usage(u) -> str:
    """把用量渲染成一行（服务商没返回 usage 时按估算值并标注）"""
    if not isinstance(u, dict) or not (u.get("prompt") or u.get("completion")):
        return "—"

    def _k(n):
        n = int(n or 0)
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    parts = [f"{T('输入')} {_k(u.get('prompt'))}",
             f"{T('输出')} {_k(u.get('completion'))}"]
    p, c = int(u.get("prompt") or 0), int(u.get("cached") or 0)
    if p > 0 and c > 0:
        parts.append(f"{T('缓存命中')} {round(c * 100.0 / p)}%")
    line = " · ".join(parts)
    if u.get("exact") is False:
        # 面板只有 ~250px 可用宽，长说明会折成两行且断在词中间；
        # 详细原因放 tooltip（_refresh_right 里设）
        line = "≈ " + line + "（" + T("估算") + "）"
    return line


def _refresh_right_safe(page):
    """刷新右侧概要栏（页面替身 / 早期构造阶段调用也不炸）

    测试里常用 SimpleNamespace 假页面直接调 AiPage 的未绑定方法，
    这里统一兜一层，别让「右侧栏刷新」把老测试带崩。
    """
    fn = getattr(page, "_refresh_right", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass


# ---------------------------------------------------------------- 图片预览
class _ClickableLabel(QLabel):
    """可点击的图片缩略图（QLabel 本身没有 clicked 信号）"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ImagePreviewDialog(QDialog):
    """点缩略图弹出的大图预览：自适应屏幕，点击任意处 / Esc 关闭"""

    def __init__(self, pm: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片预览")
        self.setStyleSheet("QDialog{background:#12161c;}")
        scr = QApplication.primaryScreen().availableGeometry()
        max_w = int(scr.width() * 0.85)
        max_h = int(scr.height() * 0.85)
        w = min(max(pm.width(), 420), max_w)
        h = min(max(pm.height(), 300), max_h)
        self.resize(w + 16, h + 40)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)
        lb = QLabel()
        lb.setAlignment(Qt.AlignCenter)
        lb.setPixmap(pm.scaled(QSize(w, h), Qt.KeepAspectRatio,
                               Qt.SmoothTransformation))
        v.addWidget(lb, 1)
        hint = QLabel(f"{pm.width()} × {pm.height()} · 点击或按 Esc 关闭")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#8a93a3; font-size:11px;")
        v.addWidget(hint)

    def mouseReleaseEvent(self, e):
        self.accept()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.accept()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- Markdown 轻渲染
_MD_BOLD = None


def _md_to_html(text: str) -> str:
    """把模型常输出的轻量 Markdown 转成 QLabel 能显示的 HTML。
    只处理 **粗体**、`行内代码`、- / * 列表、数字列表，其余原样转义。"""
    import html as _html
    import re
    out, in_ul, in_ol = [], False, False

    def _close():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for raw in (text or "").split("\n"):
        line = _html.escape(raw.rstrip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`([^`]+)`",
                      r'<code style="background:rgba(127,127,127,0.22);'
                      r'border-radius:3px; padding:0 3px;">\1</code>', line)
        m_ul = re.match(r"^\s*[-*]\s+(.*)$", line)
        m_ol = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m_ul:
            if in_ol:
                _close()
            if not in_ul:
                out.append('<ul style="margin:2px 0 2px 16px; padding:0;">')
                in_ul = True
            out.append(f"<li>{m_ul.group(1)}</li>")
        elif m_ol:
            if in_ul:
                _close()
            if not in_ol:
                out.append('<ol style="margin:2px 0 2px 18px; padding:0;">')
                in_ol = True
            out.append(f"<li>{m_ol.group(1)}</li>")
        else:
            _close()
            out.append(("" if not line else line + "<br/>"))
    _close()
    return "".join(out)


def chat_label(c: dict) -> str:
    """会话列表显示文案：标题 + 用途标 + 条数（2026-11-11）

    视频/生图/音乐三条会话并排时，光看标题分不清谁配了哪个工作流，
    所以把用途（或工作流名）缀在标题后面。
    """
    label = (c or {}).get("title") or "新对话"
    prof = ai_skills.norm_profile((c or {}).get("profile"))
    tag = prof.get("topic") or os.path.splitext(prof.get("workflow") or "")[0]
    if tag and tag not in label:
        label += f"  ·  {tag}"
    if (c or {}).get("count"):
        label += f"（{c['count']} 条）"
    return label


class _AgentWorker(QThread):
    """ai_agent.run_agent 的线程壳；事件经 sig_event(dict) 回 UI"""
    sig_event = Signal(dict)
    sig_done = Signal()

    def __init__(self, cfg, inst, text, history, attachments=None, mainwin=None,
                 summary="", summary_upto=0, skills=None, profile=None,
                 chat_id=""):
        super().__init__()
        self.cfg, self.inst, self.text, self.history = cfg, inst, text, history
        self.attachments = attachments or []
        self.mainwin = mainwin          # 工具里启停实例要用（QTimer 编组回 UI 线程）
        self.summary = summary          # 已压缩的早期对话摘要（2026-11-06）
        self.summary_upto = summary_upto
        self.skills = list(skills or [])  # 本会话勾选的技能（2026-11-11）
        self.profile = dict(profile or {})  # 本会话工作台设定（2026-11-11）
        self.chat_id = str(chat_id or "")   # 会话 id：压缩原文落盘 + recall_history
        self.stop_flag = threading.Event()

    def run(self):
        try:
            ai_agent.run_agent(self.cfg, self.inst, self.text, self.history,
                               emit=lambda kind, **kw: self.sig_event.emit(
                                   {"kind": kind, **kw}),
                               stop_flag=self.stop_flag,
                               attachments=self.attachments,
                               mainwin=self.mainwin,
                               summary=self.summary,
                               summary_upto=self.summary_upto,
                               skills=self.skills,
                               profile=self.profile,
                               chat_id=self.chat_id)
        finally:
            self.sig_done.emit()


class AiPage(QWidget):
    # ---- 样式（深浅主题都用半透明底，避免白底白字）----
    ST_USER = ("background:#2f6fb2; color:#ffffff; border-radius:12px;"
               "border:1px solid rgba(255,255,255,0.14); padding:8px 12px;")
    ST_ERR = ("background:rgba(226,75,74,0.14);"
              "border:1px solid rgba(226,75,74,0.45); color:#ff9c9c;"
              "border-radius:10px; padding:8px 12px;")
    ST_CARD = ("QFrame#aiCard{background:rgba(127,127,127,0.08);"
               "border:1px solid rgba(127,127,127,0.18);"
               "border-radius:12px;}")
    ST_TL = "QFrame#tl{border:none; border-left:2px solid rgba(55,138,221,0.55);}"
    ST_CHIP = ("QPushButton{background:rgba(127,127,127,0.12);"
               "border:1px solid rgba(127,127,127,0.22); border-radius:12px;"
               "padding:3px 10px; color:#a9b1bd; font-size:12px;}"
               "QPushButton:hover{background:rgba(127,127,127,0.20);}")
    ST_LINK = ("QPushButton{background:transparent; border:none;"
               "color:#8a93a3; font-size:12px; padding:2px 6px;}"
               "QPushButton:hover{color:#c3c9d4;}")
    ST_MINI = ("QPushButton{background:transparent;"
               "border:1px solid rgba(127,127,127,0.28); border-radius:10px;"
               "padding:2px 9px; color:#a9b1bd; font-size:12px;}"
               "QPushButton:hover{border-color:rgba(127,127,127,0.5);"
               "color:#e6eaf0;}")

    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._history = []          # [{"role","content"}] 仅文本消息
        self._worker = None
        self._last_user = ""
        self._tl_widget = None      # 当前未完成的工具时间线项
        self._think_lbl = None      # 「思考中…」占位
        self._think_timer = None
        self._think_t0 = 0.0
        self._atts = []             # 待发送附件：[{kind:'image'|'text',...}]
        self._stream = None         # 流式中的卡片状态
        self._stream_timer = None
        self._chat_id = ""          # 当前会话 id（2026-11-06 多会话持久化）
        self._summary = ""          # 早期对话压缩摘要（2026-11-06）
        self._summary_inst = ""     # 摘要对应的实例 id（2026-11-14：换实例后作废）
        self._summary_upto = 0
        self._chat_skills = []      # 本会话勾选的技能 id（2026-11-11）
        self._profile = {}          # 本会话工作台设定（2026-11-11：用途/实例/工作流/模型）
        self._chats_all = []        # 会话列表缓存（2026-11-13 搜索用，避免边打字边读盘）
        self._pending_options = []  # ask_user 工具待渲染的选项按钮（2026-11-11）
        self._artifacts = []        # 本会话产物：[{kind,path,label,ts}]（2026-11-12）
        # token 用量（2026-11-14）：按会话存内存，不落盘——重启后重新计。
        # 服务商不一定返回 usage，所以存 exact 标记决定界面怎么写。
        self._usage_map = {}        # chat_id -> {prompt,completion,cached,exact,rounds}
        try:
            self._right_open = bool(QSettings("LeiMengStudio", "ComfyHub")
                                    .value("ai_right_open", False, type=bool))
        except Exception:
            self._right_open = False
        self._build()
        self._boot_chats()

    # -------------------------------------------------- UI
    def _build(self):
        # 聊天区限宽 1100 居中（2026-11-08 第 3 版）：2560 全屏下输入框和气泡原来被
        # 拉满 2100px，一行字横跨整个屏幕根本没法读。1100 对应约 60~70 字符的行宽，
        # 是阅读舒适的经典 measure 区间。
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # ---- 左侧会话栏（2026-11-11：常驻 + 可折叠，会话级 skill 挂这里）----
        self.side = QWidget()
        self.side.setObjectName("AiSide")
        self.side.setAttribute(Qt.WA_StyledBackground, True)
        self.side.setFixedWidth(232)
        side_root = QVBoxLayout(self.side)
        side_root.setContentsMargins(10, 14, 10, 12)
        side_root.setSpacing(8)
        b_new = QPushButton("＋ 新建对话")
        b_new.setProperty("accent", True)
        b_new.setCursor(Qt.PointingHandCursor)
        b_new.setToolTip("开一个新对话（当前对话自动保存）")
        b_new.clicked.connect(self._new_chat)
        side_root.addWidget(b_new)

        # 会话检索（2026-11-13）：列表只显示标题前 24 字，长对话靠首条消息检索回来。
        # 只重渲染不过滤磁盘：list_chats 每次要读全部 json，输入时不能反复读。
        self.ed_chat_search = QLineEdit()
        self.ed_chat_search.setPlaceholderText(T("搜索对话内容"))
        self.ed_chat_search.setClearButtonEnabled(True)
        self.ed_chat_search.setFixedHeight(26)
        self.ed_chat_search.setStyleSheet(
            "QLineEdit { background:#1a1f27; border:1px solid #2a3340;"
            " border-radius:7px; padding:2px 8px; color:#c9d3e0; font-size:12px; }"
            "QLineEdit:focus { border-color:#4c8dff; }")
        self.ed_chat_search.textChanged.connect(lambda *_: self._fill_chat_list())
        side_root.addWidget(self.ed_chat_search)

        self.ck_chat_only_cur = QCheckBox(T("仅当前实例"))
        self.ck_chat_only_cur.setStyleSheet(
            "QCheckBox { color:#8a93a3; font-size:11px; }")
        self.ck_chat_only_cur.setToolTip(
            T("只看绑定到当前实例的对话；没设过「实例」的对话仍然显示"))
        self.ck_chat_only_cur.toggled.connect(lambda *_: self._fill_chat_list())
        side_root.addWidget(self.ck_chat_only_cur)
        self.lst_chat = QListWidget()
        self.lst_chat.setObjectName("ChatList")
        self.lst_chat.setFrameShape(QFrame.NoFrame)
        self.lst_chat.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lst_chat.currentItemChanged.connect(self._on_chat_item_changed)
        self.lst_chat.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lst_chat.customContextMenuRequested.connect(self._chat_menu)
        side_root.addWidget(self.lst_chat, 1)
        self.side.setStyleSheet(
            "QWidget#AiSide { background: rgba(27,32,41,0.92); border-radius: 12px; }"
            "QListWidget#ChatList { background: transparent; border: none;"
            "  color: #c9d3e0; font-size: 12px; outline: none; }"
            "QListWidget#ChatList::item { padding: 8px 10px; border-radius: 8px;"
            "  margin: 2px 0; }"
            "QListWidget#ChatList::item:hover { background: #262e3a; }"
            "QListWidget#ChatList::item:selected { background: #2f3a4a; color: #e6edf7; }")

        body = QWidget()
        body.setMaximumWidth(1520)
        root = QVBoxLayout(body)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        # ---- 顶部状态行（2026-11-07：套一层圆角描边容器，别让文字裸飘在背景图上）----
        head_host = QWidget()
        head_host.setObjectName("AiHead")
        head_host.setAttribute(Qt.WA_StyledBackground, True)
        head = QHBoxLayout(head_host)
        head.setContentsMargins(10, 4, 8, 4)
        head.setSpacing(6)
        self.lb_dot = QLabel("●")
        self.lb_dot.setStyleSheet("color:#4cd47c; font-size:11px;")
        head.addWidget(self.lb_dot)
        self.lb_inst = QLabel("未选择实例")
        self.lb_inst.setStyleSheet("color:#a9b1bd; font-size:12px;")
        head.addWidget(self.lb_inst)
        head.addStretch(1)
        # ---- 会话切换（2026-11-06 多对话；2026-11-11 顶栏下拉 → 左侧常驻栏）----
        self.b_side = QPushButton("☰ 会话")
        self.b_side.setStyleSheet(self.ST_LINK)
        self.b_side.setCursor(Qt.PointingHandCursor)
        self.b_side.setToolTip("收起/展开左侧会话列表")
        self.b_side.clicked.connect(self._toggle_side)
        head.addWidget(self.b_side)
        root.addWidget(head_host)

        # ---- 消息流 ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.flow_host = QWidget()
        self.flow = QVBoxLayout(self.flow_host)
        self.flow.setContentsMargins(2, 6, 10, 6)
        self.flow.setSpacing(12)
        self.flow.addStretch(1)
        self.scroll.setWidget(self.flow_host)
        root.addWidget(self.scroll, 1)

        # ---- 快捷指令 ----
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for text, prompt in (("体检", "做一次实例体检，简要汇报状态"),
                             ("队列", "看看当前队列里有什么"),
                             ("工作流", "列一下实例里有哪些工作流"),
                             ("最近报错", "读最近 50 行日志，找有没有报错")):
            b = QPushButton("⚡ " + text)
            b.setStyleSheet(self.ST_CHIP)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=prompt: self._quick(p))
            chips.addWidget(b)
        chips.addStretch(1)
        root.addLayout(chips)

        # ---- 附件条（粘贴图片 / JSON 后出现） ----
        self.att_row = QHBoxLayout()
        self.att_row.setSpacing(6)
        self.att_row.addStretch(1)
        self.att_host = self._row()
        self.att_host.setLayout(self.att_row)
        self.att_host.setVisible(False)
        root.addWidget(self.att_host)

        # ---- 输入区（2026-11-08：套一层输入卡，按钮与输入框成为一个整体）----
        composer = QFrame()
        composer.setObjectName("ComposerCard")
        composer.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(composer)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(8)
        self.ed = QTextEdit()
        self.ed.setFixedHeight(64)
        self.ed.setPlaceholderText("问点什么…（回车发送，Shift+回车换行；可直接粘贴图片或工作流 JSON）")
        self.ed.installEventFilter(self)
        row.addWidget(self.ed, 1)
        self.b_skills = QPushButton("⚙ 会话设定")
        self.b_skills.setFixedHeight(34)
        self.b_skills.setCursor(Qt.PointingHandCursor)
        self.b_skills.setToolTip("给这条对话固定一套工作台：用途 / 实例 / 工作流 / 模型 / 技能")
        self.b_skills.clicked.connect(self._pick_session)
        row.addWidget(self.b_skills, 0, Qt.AlignBottom)
        self.b_stop = QPushButton("停止")
        self.b_stop.setFixedSize(64, 34)
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        row.addWidget(self.b_stop, 0, Qt.AlignBottom)
        self.b_send = QPushButton("发送")
        self.b_send.setFixedSize(78, 34)
        self.b_send.setProperty("accent", True)
        self.b_send.clicked.connect(self._send)
        row.addWidget(self.b_send, 0, Qt.AlignBottom)
        root.addWidget(composer)
        self._composer = composer

        outer.addStretch(1)
        outer.addWidget(self.side)
        outer.addWidget(body, 50)
        # ---- 右侧概要抽屉（2026-11-12 用户建议）：贴边把手 + 可收起面板 ----
        self._build_right_drawer()
        outer.addWidget(self.right_handle)
        outer.addWidget(self.side_r)
        outer.addStretch(1)

        # 首屏欢迎语由 _boot_chats 负责（_open_chat / 新建会话都会渲染）；
        # 这里别再 _greet()——否则空会话会叠出两张一模一样的欢迎卡（2026-11-12 实测）
        self.refresh_backdrop()
        self._apply_right_open(getattr(self, "_right_open", False), remember=False)

    # -------------------------------------------------- 右侧抽屉（2026-11-12）
    def _build_right_drawer(self):
        """右侧「简介 / 产物 / 技能」抽屉 + 贴边把手

        用户建议（2026-11-12）：右边来一个隐藏按钮，点开能看当前对话的简介，
        还能看到这条对话产出的文件、以及挂了哪些 skill 的内容。
        """
        self.right_handle = QToolButton()
        self.right_handle.setObjectName("AiDrawHandle")
        self.right_handle.setCursor(Qt.PointingHandCursor)
        self.right_handle.setFixedWidth(18)
        self.right_handle.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.right_handle.clicked.connect(self._toggle_right)
        self.right_handle.setStyleSheet(
            "QToolButton#AiDrawHandle { background: rgba(27,32,41,0.92);"
            " color:#8a93a3; border:none; border-radius:9px; font-size:14px; }"
            "QToolButton#AiDrawHandle:hover { background:#262e3a; color:#e6edf7; }")

        self.side_r = QWidget()
        self.side_r.setObjectName("AiRight")
        self.side_r.setAttribute(Qt.WA_StyledBackground, True)
        self.side_r.setFixedWidth(304)
        self.side_r.setStyleSheet(
            "QWidget#AiRight { background: rgba(27,32,41,0.92);"
            " border-radius: 12px; }"
            "QTabWidget#AiRightTab::pane { border:none; }"
            "QTabWidget#AiRightTab QTabBar::tab { background:transparent;"
            "  color:#8a93a3; padding:6px 10px; border:none; font-size:12px; }"
            "QTabWidget#AiRightTab QTabBar::tab:selected { color:#e6edf7;"
            "  border-bottom:2px solid #4c8dff; }"
            "QListWidget#RArt, QListWidget#RSkill { background:transparent;"
            "  border:none; color:#c9d3e0; font-size:12px; outline:none; }"
            "QListWidget#RArt::item, QListWidget#RSkill::item { padding:7px 8px;"
            "  border-radius:7px; margin:2px 0; }"
            "QListWidget#RArt::item:hover, QListWidget#RSkill::item:hover"
            " { background:#262e3a; }"
            "QListWidget#RArt::item:selected, QListWidget#RSkill::item:selected"
            " { background:#2f3a4a; color:#e6edf7; }"
            "QPlainTextEdit#RSkillBody, QPlainTextEdit#RSummary"
            " { background:#14181e; border:1px solid #232b36; border-radius:8px;"
            "   color:#a9b7c8; font-size:11px; padding:6px; }")
        v = QVBoxLayout(self.side_r)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        lb = QLabel(T("对话概要"))
        lb.setStyleSheet("color:#e6edf7; font-size:13px; font-weight:600;")
        head.addWidget(lb)
        head.addStretch(1)
        b_close = QPushButton("›")
        b_close.setCursor(Qt.PointingHandCursor)
        b_close.setFixedSize(20, 20)
        b_close.setToolTip(T("收起面板"))
        b_close.setStyleSheet(self.ST_LINK)
        b_close.clicked.connect(self._toggle_right)
        head.addWidget(b_close)
        v.addLayout(head)

        self.tabs_r = QTabWidget()
        self.tabs_r.setObjectName("AiRightTab")
        self.tabs_r.addTab(self._page_brief(), T("简介"))
        self.tabs_r.addTab(self._page_artifacts(), T("产物"))
        self.tabs_r.addTab(self._page_skills(), T("技能"))
        v.addWidget(self.tabs_r, 1)
        self.side_r.setVisible(False)

    def _page_brief(self) -> QWidget:
        """简介页：用途 / 实例 / 工作流 / 模型 / 消息数 + 上下文压缩摘要"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 2, 4)
        lay.setSpacing(7)

        def _kv(key):
            row = QHBoxLayout()
            row.setSpacing(6)
            lk = QLabel(key)
            # 48px 才放得下「工作流」三个 11px 汉字；42 会被挤成两行
            lk.setFixedWidth(48)
            lk.setWordWrap(False)
            lk.setStyleSheet("color:#6c7a8c; font-size:11px;")
            lv = QLabel("—")
            lv.setWordWrap(True)
            lv.setStyleSheet("color:#c9d3e0; font-size:12px;")
            row.addWidget(lk, 0, Qt.AlignTop)
            row.addWidget(lv, 1)
            lay.addLayout(row)
            return lv

        self.r_topic = _kv(T("用途"))
        self.r_inst = _kv(T("实例"))
        self.r_wf = _kv(T("工作流"))
        self.r_model = _kv(T("模型"))
        self.r_count = _kv(T("消息"))
        self.r_usage = _kv(T("用量"))

        lb = QLabel(T("压缩摘要"))
        lb.setStyleSheet("color:#6c7a8c; font-size:11px; padding-top:2px;")
        lay.addWidget(lb)
        self.r_summary = QPlainTextEdit()
        self.r_summary.setObjectName("RSummary")
        self.r_summary.setReadOnly(True)
        self.r_summary.setPlaceholderText(T("对话变长后会自动把早期内容压缩成摘要"))
        lay.addWidget(self.r_summary, 1)
        return w

    def _page_artifacts(self) -> QWidget:
        """产物页：这条对话落盘的东西（图 / 工作流 / 模型 / 日志）"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 2, 4)
        lay.setSpacing(6)
        self.lst_art = QListWidget()
        self.lst_art.setObjectName("RArt")
        self.lst_art.setFrameShape(QFrame.NoFrame)
        self.lst_art.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lst_art.itemDoubleClicked.connect(lambda it: self._open_artifact(it))
        lay.addWidget(self.lst_art, 1)
        self.lb_art_empty = QLabel(
            T("这条对话还没有产物") + "\n"
            + T("生成的图 / 写出的工作流 / 下载的模型都会列在这里"))
        self.lb_art_empty.setWordWrap(True)
        self.lb_art_empty.setStyleSheet(
            "color:#6c7a8c; font-size:11px; padding:6px 2px;")
        lay.addWidget(self.lb_art_empty)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.b_art_open = QPushButton(T("打开"))
        self.b_art_open.setCursor(Qt.PointingHandCursor)
        self.b_art_open.setStyleSheet(self.ST_CHIP)
        self.b_art_open.clicked.connect(
            lambda: self._open_artifact(self.lst_art.currentItem()))
        row.addWidget(self.b_art_open)
        self.b_art_dir = QPushButton(T("所在文件夹"))
        self.b_art_dir.setCursor(Qt.PointingHandCursor)
        self.b_art_dir.setStyleSheet(self.ST_CHIP)
        self.b_art_dir.clicked.connect(
            lambda: self._open_artifact(self.lst_art.currentItem(), folder=True))
        row.addWidget(self.b_art_dir)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _page_skills(self) -> QWidget:
        """技能页：本会话挂的 skill 及正文（想看它到底给模型灌了什么）"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 2, 4)
        lay.setSpacing(6)
        self.lst_skill = QListWidget()
        self.lst_skill.setObjectName("RSkill")
        self.lst_skill.setFrameShape(QFrame.NoFrame)
        self.lst_skill.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lst_skill.setMaximumHeight(132)
        self.lst_skill.currentItemChanged.connect(self._show_skill_body)
        lay.addWidget(self.lst_skill)
        self.lb_skill_empty = QLabel(
            T("这条对话还没挂技能") + "\n"
            + T("点输入框旁「⚙ 会话设定」添加"))
        self.lb_skill_empty.setWordWrap(True)
        self.lb_skill_empty.setStyleSheet(
            "color:#6c7a8c; font-size:11px; padding:2px;")
        lay.addWidget(self.lb_skill_empty)
        self.txt_skill = QPlainTextEdit()
        self.txt_skill.setObjectName("RSkillBody")
        self.txt_skill.setReadOnly(True)
        self.txt_skill.setPlaceholderText(T("选一个技能查看正文"))
        lay.addWidget(self.txt_skill, 1)
        return w

    def _toggle_right(self):
        self._apply_right_open(not self.side_r.isVisible())

    def _apply_right_open(self, on: bool, remember: bool = True):
        """展开/收起右侧抽屉；状态记进 QSettings（下次开程序还是老样子）"""
        self._right_open = bool(on)
        self.side_r.setVisible(self._right_open)
        self.right_handle.setText("›" if self._right_open else "‹")
        self.right_handle.setToolTip(
            T("收起面板") if self._right_open
            else T("对话概要：简介 / 产物 / 技能（点击展开）"))
        if self._right_open:
            _refresh_right_safe(self)
        # 抽屉一开一合会改变聊天区宽度，但 AiPage 自身尺寸没变、resizeEvent 不会来，
        # 历史气泡用的是 setFixedWidth → 不重排就会被右边裁掉字（2026-11-12 实测）。
        QTimer.singleShot(0, self._relayout_messages)
        if remember:
            try:
                QSettings("LeiMengStudio", "ComfyHub").setValue(
                    "ai_right_open", self._right_open)
            except Exception:
                pass

    def _set_usage_label(self, u):
        """刷新「用量」那一行（文案 + tooltip）"""
        if not hasattr(self, "r_usage"):
            return
        self.r_usage.setText(_fmt_usage(u))
        self.r_usage.setToolTip(
            T("服务商未返回用量，为估算值")
            if (u and u.get("exact") is False) else
            T("本会话累计输入/输出 token（含缓存命中比例）"))

    def _refresh_right(self):
        """按当前会话刷新三页内容（幂等，随时可调）"""
        if not hasattr(self, "r_topic"):
            return
        p = self._profile or {}
        self.r_topic.setText(p.get("topic") or T("未设定"))
        self.r_inst.setText(self._inst_name(p.get("inst_id"))
                            if p.get("inst_id") else T("跟随当前实例"))
        self.r_wf.setText(p.get("workflow") or T("未指定"))
        self.r_model.setText(p.get("model") or T("未指定"))
        upd = ""
        try:
            upd = next((c.get("updated") for c in ai_chat_store.list_chats()
                        if c.get("id") == self._chat_id), 0) or 0
        except Exception:
            pass
        stamp = (time.strftime("%m-%d %H:%M", time.localtime(upd)) if upd else "")
        self.r_count.setText(f"{len(self._history)}" + (f" · {stamp}" if stamp else ""))
        try:
            self._set_usage_label((getattr(self, "_usage_map", {}) or {})
                                  .get(self._chat_id))
        except Exception:
            pass
        self.r_summary.setPlainText(self._summary or "")

        # ---- 产物 ----
        self.lst_art.clear()
        ICON = {"image": "🖼", "model": "🧩", "workflow": "🧬", "log": "📄"}
        for a in self._artifacts:
            it = QListWidgetItem(ICON.get(a.get("kind"), "📄") + " " + a.get("label", ""))
            it.setData(Qt.UserRole, a.get("path", ""))
            it.setToolTip(a.get("path", ""))
            self.lst_art.addItem(it)
        has_art = bool(self._artifacts)
        self.lst_art.setVisible(has_art)
        self.lb_art_empty.setVisible(not has_art)
        self.b_art_open.setEnabled(has_art)
        self.b_art_dir.setEnabled(has_art)

        # ---- 技能 ----
        self.lst_skill.clear()
        picked = []
        for sid in (self._chat_skills or []):
            sk = ai_skills.get_skill(sid)
            if not sk:
                continue
            picked.append(sk)
            it = QListWidgetItem("🧠 " + sk.get("name", sid))
            it.setData(Qt.UserRole, sid)
            _tip = sk.get("desc") or ""
            if sk.get("tools") is not None:
                _tip += "\n" + T("本技能的声明会收窄本对话的工具表")
            it.setToolTip(_tip)
            self.lst_skill.addItem(it)
        has_sk = bool(picked)
        self.lst_skill.setVisible(has_sk)
        self.lb_skill_empty.setVisible(not has_sk)
        if has_sk:
            self.lst_skill.setCurrentRow(0)
        else:
            self.txt_skill.clear()

    def _show_skill_body(self, cur, _prev=None):
        if not cur:
            self.txt_skill.clear()
            return
        sk = ai_skills.get_skill(str(cur.data(Qt.UserRole) or ""))
        # body 本身已包含「# 标题 + 描述段」，再前置一遍描述会重复两行
        self.txt_skill.setPlainText(sk.get("body") or "")

    def _add_artifact(self, a: dict):
        """记录一条产物（按路径去重）；同一轮的 image 事件不会重复记两遍"""
        path = str((a or {}).get("path") or "").strip()
        if not path:
            return
        if any(x.get("path") == path for x in self._artifacts):
            return
        self._artifacts.append({
            "kind": str(a.get("kind") or "file"),
            "path": path,
            "label": str(a.get("label") or os.path.basename(path.replace("\\", "/"))),
            "ts": time.time()})
        self._artifacts = ai_chat_store._clip_artifacts(self._artifacts)
        _refresh_right_safe(self)

    def _open_artifact(self, item, folder: bool = False):
        path = (item.data(Qt.UserRole) if item is not None else "") or ""
        if not path:
            return
        target = os.path.dirname(path) if folder else path
        try:
            os.startfile(target)          # noqa: S606 —— 打开用户自己的产物文件
        except Exception as e:
            self._notice(T("打不开：") + str(e)[:80])

    def refresh_backdrop(self, info: dict = None):
        """自定义背景图时给整页加一层半透明底板（2026-11-06 用户报障）

        现象：换了自定义背景图后，AI 页的输入框、快捷指令、顶部状态行都跟着变透明，
        文字直接浮在背景图上，看着像"UI 丢了"。
        做法：页面范围内铺一层深色半透明底板（透明度跟面板不透明度挂钩），
        既压住花哨背景保证可读，又还能隐约看到图。
        """
        st = (self.cfg.get("settings") or {})
        custom = bool(str(st.get("bg_image") or "").strip())
        if info is not None and info.get("mode"):
            custom = custom and info.get("mode") == "custom"
        self._custom_bg = custom
        # 2026-11-07：全局 QSS 的 `QWidget{background-color:...}` 会给每个消息行容器
        # 刷上一层不透明/半透明底色，自定义背景下就成了一条条"横带"（用户报障）。
        # 行容器统一 objectName=ChatRow，这里恒置透明；气泡/卡片自己带样式不受影响。
        rows = ("QWidget#ChatRow{background:transparent;}"
                "QLabel#ChatBody{background:transparent;}")
        # 顶部状态条：必须是有边有角的容器，否则状态文字和下拉框裸飘在背景图上
        if custom:
            rows += ("QWidget#AiHead{background:rgba(14,17,22,0.50);"
                     "border:1px solid rgba(255,255,255,0.10); border-radius:10px;}")
        else:
            rows += ("QWidget#AiHead{background:rgba(127,127,127,0.06);"
                     "border:1px solid rgba(127,127,127,0.14); border-radius:10px;}")
        if not custom:
            self.setAttribute(Qt.WA_StyledBackground, False)
            self.setStyleSheet(rows)
            self._restyle_cards()
            return
        op = max(0, min(100, int(st.get("bg_opacity") or 72)))
        from app.ui.theme import page_scrim_rgba
        # 2026-11-07：聊天容器被 custom 主题强制透明、卡片很淡，
        # AI 页需要整层底板（strength=1.0，等效单层左栏）才有可读性；
        # 表单页有分组框兜底，走 main_window 里的薄层（0.25）
        alpha = page_scrim_rgba(op / 100.0, strength=1.0)
        self.setObjectName("AiPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#AiPage{{background:{alpha};}}"
            + rows +
            "QWidget#AiPage QScrollArea{background:transparent;}"
            "QWidget#AiPage QScrollArea>QWidget>QWidget{background:transparent;}")
        self._restyle_cards()

    # -------------------------------------------------- 卡片/气泡外观（2026-11-07）
    def _card_qss(self) -> str:
        """AI 消息卡：自定义背景下用深一档的实色 + 细描边，边界清楚但不抢背景

        之前卡片只有 8% 灰 + 0.18 描边，叠在背景图上完全看不见——
        正文和按钮就像裸飘着，用户反馈"没有边框、没有圆角、色差断层"。
        """
        if getattr(self, "_custom_bg", False):
            return ("QFrame#aiCard{background:rgba(14,17,22,0.56);"
                    "border:1px solid rgba(255,255,255,0.12);"
                    "border-radius:12px;}")
        return ("QFrame#aiCard{background:rgba(127,127,127,0.08);"
                "border:1px solid rgba(127,127,127,0.18);"
                "border-radius:12px;}")

    def _restyle_cards(self):
        """把已存在的消息卡刷成当前主题的样式（历史卡片不会自己重建）"""
        if not hasattr(self, "flow"):
            return
        qss = self._card_qss()
        for i in range(self.flow.count()):
            it = self.flow.itemAt(i)
            w = it.widget() if it is not None else None
            if w is not None and w.objectName() == "aiCard":
                w.setStyleSheet(qss)

    # -------------------------------------------------- 键盘：回车发送 / Shift+回车换行
    def eventFilter(self, obj, event):
        """输入框通用习惯（2026-11-03 用户要求）：
        · Enter          → 发送
        · Shift+Enter    → 换行
        · Ctrl+Enter     → 发送（兼容旧习惯）
        中文输入法组字中的 Enter 由 IME 自己消费，不会走到这里。"""
        if obj is getattr(self, "ed", None) and event.type() == QEvent.KeyPress:
            if (event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier
                    and self._try_paste_attachment()):
                return True                       # 附件已接住，别让输入框再粘一遍
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    return False                     # 交给 QTextEdit 插入换行
                self._send()
                return True                          # 吞掉按键，别再多插一个换行
        return super().eventFilter(obj, event)

    # -------------------------------------------------- 附件（粘贴图片 / JSON）
    def _try_paste_attachment(self) -> bool:
        """剪贴板优先看图片，其次看"是不是工作流 JSON"。接住返回 True"""
        cb = QApplication.clipboard()
        md = cb.mimeData()
        if md is not None and md.hasImage():
            img = QImage(md.imageData())
            if not img.isNull():
                self._add_image_att(img)
                return True
        if md is not None and md.hasUrls():
            for u in md.urls():
                f = u.toLocalFile()
                if f.lower().endswith(".json") and os.path.isfile(f):
                    try:
                        self._add_text_att(open(f, encoding="utf-8").read(),
                                           os.path.basename(f))
                        return True
                    except OSError:
                        pass
        txt = (md.text() if md is not None else "") or ""
        if len(txt) > 500 and txt.strip().startswith("{"):
            try:
                obj = json.loads(txt)
            except Exception:
                return False
            if isinstance(obj, dict) and ("nodes" in obj
                                          or any(isinstance(v, dict) and "class_type" in v
                                                 for v in obj.values())):
                self._add_text_att(txt, "粘贴的工作流 JSON")
                return True
        return False

    def _add_image_att(self, img: "QImage"):
        import base64
        import io
        # 长边压到 1024 以内：base64 体积直接决定请求耗时与费用
        if max(img.width(), img.height()) > 1024:
            img = img.scaled(1024, 1024, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buf = QBuffer()
        buf.open(QBuffer.WriteOnly)
        img.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        self._atts.append({"kind": "image", "mime": "image/png",
                           "b64": base64.b64encode(data).decode(),
                           "thumb": QPixmap.fromImage(img)})
        self._refresh_atts()

    def _add_text_att(self, text: str, label: str):
        self._atts.append({"kind": "text", "text": text, "label": label})
        self._refresh_atts()

    def _remove_att(self, idx: int):
        if 0 <= idx < len(self._atts):
            self._atts.pop(idx)
            self._refresh_atts()

    def _refresh_atts(self):
        while self.att_row.count():
            it = self.att_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for i, a in enumerate(self._atts):
            chip = QFrame()
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip("点右下 ✕ 或直接点这张卡片即可移除附件")
            chip.setStyleSheet("QFrame{background:rgba(127,127,127,0.12);"
                               "border:1px solid rgba(127,127,127,0.25);"
                               "border-radius:8px;}"
                               "QFrame:hover{border-color:rgba(226,75,74,0.55);}")
            h = QHBoxLayout(chip)
            h.setContentsMargins(6, 3, 4, 3)
            h.setSpacing(6)
            if a["kind"] == "image":
                lb = QLabel()
                lb.setPixmap(a["thumb"].scaledToHeight(34, Qt.SmoothTransformation))
            else:
                lb = QLabel("🧩 " + str(a.get("label") or "JSON")[:24])
                lb.setStyleSheet("color:#a9b1bd; font-size:12px;")
            h.addWidget(lb)
            # 用标准图标而不是字符：✕/× 在部分字体/主题下渲染成空白，用户会以为"删不掉"
            b = QToolButton()
            b.setIcon(self.style().standardIcon(QStyle.SP_TitleBarCloseButton))
            b.setIconSize(QSize(12, 12))
            b.setFixedSize(18, 18)
            b.setAutoRaise(True)
            b.setToolTip("移除这个附件")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QToolButton{background:transparent;border:none;}"
                            "QToolButton:hover{background:rgba(226,75,74,0.22);"
                            "border-radius:9px;}")
            b.clicked.connect(lambda _=False, k=i: self._remove_att(k))
            h.addWidget(b)
            chip._att_idx = i
            chip.mousePressEvent = (
                lambda ev, k=i, w=chip: (self._remove_att(k), ev.accept()))
            self.att_row.addWidget(chip)
        self.att_row.addStretch(1)
        self.att_host.setVisible(bool(self._atts))

    # -------------------------------------------------- 尺寸计算
    @staticmethod
    def _rich_height(html: str, width: int, font) -> int:
        """富文本精确高度（QLabel 对 HTML 的 sizeHint 同样不可靠）"""
        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(html)
        doc.setTextWidth(max(40, width))
        return max(30, int(doc.size().height()) + 4)

    @staticmethod
    def _wrap_height(lb: QLabel, text: str, width: int, pad_h: int, pad_v: int) -> int:
        """换行文本精确高度：QLabel.heightForWidth 在样式表 padding 下会算成单行"""
        fm = lb.fontMetrics()
        rect = fm.boundingRect(QRect(0, 0, max(40, width - pad_h), 10000),
                               Qt.TextWordWrap, text)
        return max(30, rect.height() + pad_v)

    def _chat_width(self) -> int:
        return max(320, self.scroll.viewport().width() - 24)

    @staticmethod
    def _row() -> QWidget:
        """消息行容器（透明底）

        2026-11-07：全局 QSS 第一条 `QWidget{background-color:...}` 会给每个裸
        QWidget 刷一层底色——自定义背景下每条消息外面就多出一条横带（用户报障）。
        统一打 ChatRow 标记，由 refresh_backdrop 的 `QWidget#ChatRow{...}` 置透。
        """
        w = QWidget()
        w.setObjectName("ChatRow")
        return w

    def _fit_ai_body(self, body: QLabel, html: str):
        """AI 卡片正文按当前聊天区宽度定型（窗口缩放后需重算）

        注意：QLabel 内部富文本排版比 QTextDocument 按同宽算的要**略宽**一点
        （字体舍入 + 段落 margin），按同宽算高度会差半行，末行被裁掉几个字
        （2026-11-12 实测：1366 宽展开右栏后「…运行工作流。点上方快捷指令」
        变成「…运行工作流」直接接「快捷指令」）。这里宽度留 8px、高度留 6px 兜住。
        """
        # 关键：先把 QSS 落到控件上再取字体（global QSS 会改字号）
        body.ensurePolished()
        # 宽度预算：viewport 宽 - 消息流内边距(2+10) - 卡片内边距(14+14)。
        # 旧版只减了 28，把 flow 的 12px 也算进去扣了两次 → label 每行短 12px，
        # 窗口一窄就有行尾文字放不下被裁（1366 展开右栏时每行少三四个字）。
        cw = self._chat_width() - 16
        body.setFixedWidth(cw)
        body.setFixedHeight(
            self._rich_height(html, cw, body.font()) + 6)

    def _relayout_messages(self):
        """窗口尺寸变化后重排已有消息（2026-11-07 用户报障）

        历史对话在 __init__ 里渲染，那一刻页面还是 640x480 的默认尺寸，
        气泡/正文宽度被 setFixedWidth 钉死；窗口放大后卡片被布局拉满、
        正文却还是窄的，右边留一大片空白，看着像"半截横条"。
        这里按新宽度就地重算，控件复用——不重建、不丢工具时间线。
        """
        if getattr(self, "_stream", None):
            return                              # 流式中别动，等结束
        rows = []
        for i in range(self.flow.count()):
            it = self.flow.itemAt(i)
            w = it.widget() if it is not None else None
            if w is not None:
                rows.append(w)
        # 布局里没挂上的遗留控件也一并纳入（历史上出现过孤儿卡不跟随重排）
        for ch in self.flow_host.children():
            if isinstance(ch, QWidget) and ch not in rows:
                rows.append(ch)
        for w in rows:
            for lb in w.findChildren(QLabel):
                kind = lb.property("chatKind")
                if kind == "user":
                    self._fit_user_bubble(lb, lb.property("chatText") or "")
                elif kind == "err":
                    self._fit_err_bubble(lb, lb.property("chatText") or "")
                elif kind == "ai":
                    self._fit_ai_body(lb, lb.text())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not hasattr(self, "flow"):
            return
        # 拖窗口时每帧重排会卡：停手 80ms 再算一次
        t = getattr(self, "_relayout_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(80)
            t.timeout.connect(self._relayout_messages)
            self._relayout_timer = t
        t.start()

    # -------------------------------------------------- 消息组件
    def _greet(self):
        # 配了工作台的会话，开场就把配置摊开（2026-11-11）：
        # 用户开着三条不同用途的对话，进来第一眼要能确认「我现在在哪个工作台」。
        p = ai_skills.norm_profile(self._profile)
        if p:
            bits = []
            if p.get("topic"):
                bits.append("用途：" + p["topic"])
            if p.get("workflow"):
                bits.append("工作流：" + p["workflow"])
            if p.get("model"):
                bits.append("模型：" + p["model"])
            if p.get("inst_id"):
                bits.append("实例：" + self._inst_name(p["inst_id"]))
            names = [s["name"] for s in ai_skills.list_skills()
                     if s["id"] in (self._chat_skills or [])]
            if names:
                bits.append("技能：" + "、".join(names))
            self._ai_card("本对话工作台：\n"
                          + "\n".join("· " + b for b in bits)
                          + "\n\n直接说需求就行，我只在这套配置下干活。")
            return
        self._ai_card(
            "我是 ComfyHub 的 AI 助手，可以帮你查看实例状态、读日志排错、"
            "管理模型、检查和运行工作流。点上方快捷指令，或直接输入问题。\n\n"
            "想把这条对话固定成某个用途（视频 / 生图 / 音乐）？点底部「⚙ 会话设定」"
            "绑好工作流和技能，之后它只在这套配置下干活，不同对话互不干扰。")

    def _fit_user_bubble(self, lb: QLabel, text: str):
        """按当前聊天区宽度给气泡定型（2026-11-07：抽出以便窗口缩放后重排）"""
        fm = lb.fontMetrics()
        cw = min(self._chat_width() - 140, fm.horizontalAdvance(text) + 6)
        cw = max(80, cw)
        lb.setFixedWidth(cw + 24)                       # +padding
        lb.setFixedHeight(self._wrap_height(lb, text, cw + 24, 24, 16))

    def _user_bubble(self, text: str):
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lb.setStyleSheet(self.ST_USER)
        lb.setProperty("chatKind", "user")
        lb.setProperty("chatText", text)
        self._fit_user_bubble(lb, text)
        w = self._row()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)
        h.addWidget(lb)
        self.flow.insertWidget(self.flow.count() - 1, w)
        self._scroll_bottom()

    def _user_thumb(self, pm: QPixmap):
        lb = _ClickableLabel()
        lb.setPixmap(pm.scaledToHeight(90, Qt.SmoothTransformation))
        lb.setToolTip("点击查看大图")
        lb.setStyleSheet("border:1px solid rgba(127,127,127,0.25); border-radius:6px;")
        lb.clicked.connect(lambda p=pm: ImagePreviewDialog(p, self).exec())
        wrap = self._row()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 8, 0)
        h.addStretch(1)
        h.addWidget(lb)
        self.flow.insertWidget(self.flow.count() - 1, wrap)

    def _ai_card(self, text: str, streaming: bool = False):
        model = str((self.cfg.get("settings") or {}).get("ai_model") or "") or "AI"
        card = QFrame()
        card.setObjectName("aiCard")
        card.setStyleSheet(self._card_qss())
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)

        hd = QHBoxLayout()
        hd.setSpacing(6)
        dot = QLabel("●")
        dot.setStyleSheet("color:#4cd47c; font-size:11px;")
        hd.addWidget(dot)
        cap = QLabel("AI 助手 · " + model + " · " +
                     ("生成中…" if streaming else QTime.currentTime().toString("HH:mm")))
        cap.setStyleSheet("color:#8a93a3; font-size:11px;")
        hd.addWidget(cap)
        hd.addStretch(1)
        b_copy = QPushButton("复制")
        b_copy.setStyleSheet(self.ST_MINI)
        b_copy.setCursor(Qt.PointingHandCursor)
        b_copy.clicked.connect(lambda _=False: self._copy(b_copy.property("payload") or text, b_copy))
        b_copy.setProperty("payload", text)
        b_copy.setEnabled(not streaming)
        hd.addWidget(b_copy)
        b_re = QPushButton("重跑")
        b_re.setStyleSheet(self.ST_MINI)
        b_re.setCursor(Qt.PointingHandCursor)
        b_re.setEnabled(bool(self._last_user) and not streaming)
        b_re.clicked.connect(self._resend)
        hd.addWidget(b_re)
        v.addLayout(hd)

        body = QLabel()
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        html = _md_to_html(text)
        body.setText(html)
        body.setProperty("chatKind", "ai")
        body.setObjectName("ChatBody")
        self._fit_ai_body(body, html)
        v.addWidget(body)

        self.flow.insertWidget(self.flow.count() - 1, card)
        self._scroll_bottom()
        self._last_card = {"card": card, "body": body, "copy": b_copy,
                           "re": b_re, "cap": cap, "text": text}
        return card

    def _show_images(self, paths: list):
        """把工作流产出的图片贴进对话（缩放显示，点开看原图）"""
        shown = 0
        for path in paths:
            pm = QPixmap(path)
            if pm.isNull():
                continue
            lb = _ClickableLabel()          # 2026-11-06：出图也能点开看原图
            lb.setPixmap(pm.scaledToWidth(min(420, self._chat_width() - 60),
                                          Qt.SmoothTransformation))
            lb.setToolTip(f"{path}\n点击查看原图")
            lb.clicked.connect(lambda p=pm: ImagePreviewDialog(p, self).exec())
            lb.setStyleSheet("border:1px solid rgba(127,127,127,0.25);"
                             "border-radius:8px; padding:2px;")
            wrap = self._row()
            h = QHBoxLayout(wrap)
            h.setContentsMargins(16, 0, 0, 0)
            h.addWidget(lb)
            h.addStretch(1)
            self.flow.insertWidget(self.flow.count() - 1, wrap)
            shown += 1
        if shown:
            self._scroll_bottom()

    def _fit_err_bubble(self, lb: QLabel, text: str):
        cw = self._chat_width() - 60
        lb.setFixedWidth(cw)
        lb.setFixedHeight(self._wrap_height(lb, text, cw, 24, 16))

    def _err_bubble(self, text: str, raw: str = ""):
        """错误气泡：主文案已经是人话；原始异常塞 tooltip（用户排查时才看）

        2026-11-12 用户反馈「看不懂这是什么以上的报错」——原来这里直接显示
        「HTTPError: HTTP Error 401: Unauthorized」。现在文本由
        ai_assist.describe_error() 翻译过，raw 只作 tooltip。
        """
        lb = QLabel(text)
        lb.setTextFormat(Qt.PlainText)      # 错误里常含 <urlopen ...>，别被当 HTML 吃掉
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lb.setStyleSheet(self.ST_ERR)
        lb.setProperty("chatKind", "err")
        lb.setProperty("chatText", text)
        if raw:
            lb.setToolTip(T("原始错误（排查用）：") + "\n" + raw[:800])
        self._fit_err_bubble(lb, text)
        w = self._row()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(lb)
        self.flow.insertWidget(self.flow.count() - 1, w)
        self._scroll_bottom()

    def _tool_item(self, name: str, args: dict):
        """时间线节点：左侧竖线 + 工具名 + 状态"""
        f = QFrame()
        f.setObjectName("tl")
        f.setStyleSheet(self.ST_TL)
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 2, 0, 2)
        arg_str = ", ".join(f"{k}={v}" for k, v in list((args or {}).items())[:2])
        lb = QLabel(f"● {name}" + (f"（{arg_str}）" if arg_str else "") + " …")
        lb.setObjectName("ChatBody")
        lb.setStyleSheet("color:#8a93a3; font-size:12px;")
        lb.setToolTip(str(args)[:500])
        h.addWidget(lb)
        h.addStretch(1)
        wrap = self._row()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(16, 0, 0, 0)     # 时间线缩进
        wl.addWidget(f)
        self.flow.insertWidget(self.flow.count() - 1, wrap)
        self._scroll_bottom()
        self._tl_widget = (wrap, lb, name)
        return wrap

    def _finish_tool_item(self, name: str, ok: bool, brief: str, ms: int = 0):
        cur = self._tl_widget
        if not (cur and cur[2] == name):
            self._tool_item(name, {})
            cur = self._tl_widget
        lb = cur[1]
        mark = "✓" if ok else "✗"
        cost = f" · {ms}ms" if ms else ""
        lb.setText(f"● {name} · {mark} {brief}{cost}")
        lb.setStyleSheet("color:#a9b1bd; font-size:12px;" if ok
                         else "color:#e2888a; font-size:12px;")
        self._tl_widget = None

    # -------------------------------------------------- 交互
    def _inst(self):
        """当前生效实例：会话绑了实例就用它，否则跟随主窗口当前实例"""
        return self._profile_inst()

    def _quick(self, text):
        self.ed.setPlainText(text)
        self._send()

    def _send(self):
        text = self.ed.toPlainText().strip()
        if not text or self._worker is not None:
            return
        inst = self._inst()
        if not inst or not inst.get("comfyui_path"):
            self._err_bubble("当前没有可用实例，先在工作台添加并选中一个实例")
            return
        atts = list(self._atts)
        self.ed.clear()
        self._user_bubble(text)
        for a in atts:
            if a["kind"] == "image":
                self._user_thumb(a["thumb"])
        self._atts.clear()
        self._refresh_atts()
        self._last_user = text
        self._history.append({"role": "user", "content": text})
        self._save_chat()               # 先落盘：即便进程被杀，这句也不会丢
        self._worker = _AgentWorker(self.cfg, inst, text, list(self._history), atts,
                                    mainwin=self.mainwin,
                                    summary=self._summary,
                                    summary_upto=self._summary_upto,
                                    skills=self._chat_skills,
                                    profile=self._profile,
                                    chat_id=self._chat_id)
        self._worker.sig_event.connect(self._on_event)
        self._worker.sig_done.connect(self._on_done)
        self.b_send.setEnabled(False)
        self.b_stop.setEnabled(True)
        self._think_start()
        self._worker.start()

    def _resend(self):
        if self._last_user and self._worker is None:
            self._quick(self._last_user)

    def _copy(self, text: str, btn: QPushButton):
        QApplication.clipboard().setText(text)
        old = btn.text()
        btn.setText("已复制")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1200, lambda: btn.setText(old))

    def _stop(self):
        if self._worker is not None:
            self._worker.stop_flag.set()

    # -------------------------------------------------- 会话（2026-11-06）
    def _boot_chats(self):
        """启动时恢复上次的对话；没有就开一个新的"""
        self._reload_chat_list()
        cid = self._chat_id or ai_chat_store.latest_id()
        if cid:
            self._open_chat(cid)
        else:
            self._chat_id = ai_chat_store.new_id()
            self._greet()

    def _reload_chat_list(self, select: str = ""):
        """读盘 + 渲染；搜索/筛选只走 _fill_chat_list，不重复读磁盘"""
        self._chats_all = ai_chat_store.list_chats()
        self._fill_chat_list(select)

    def _fill_chat_list(self, select: str = ""):
        kw = (self.ed_chat_search.text() or "").strip().lower()
        only_cur = bool(self.ck_chat_only_cur.isChecked())
        cur_id = ""
        if only_cur:
            # 必须用**主窗口当前实例**，不能用 self._inst()——后者是「当前会话
            # 自己绑的实例」，拿它当筛选项会永远只剩一条
            try:
                cur_id = str((self.mainwin.current_inst() or {}).get("id") or "")
            except Exception:
                cur_id = ""
        target = select or self._chat_id

        rows = []
        for c in (getattr(self, "_chats_all", None) or []):
            prof = ai_skills.norm_profile(c.get("profile"))
            # 「仅当前实例」只排除明确绑到别的实例的对话；没绑定的照常显示，
            # 否则大量「通用」对话会被一起藏起来，反而更难找
            if only_cur and cur_id and prof.get("inst_id") \
                    and str(prof["inst_id"]) != cur_id:
                continue
            if kw:
                hay = " ".join(str(x or "") for x in (
                    c.get("title"), c.get("first"), prof.get("topic"),
                    prof.get("workflow"), prof.get("model"))).lower()
                if kw not in hay:
                    continue
            rows.append((c, prof))

        self.lst_chat.blockSignals(True)
        self.lst_chat.clear()
        for c, prof in rows:
            label = chat_label(c)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, c["id"])
            tips = [label]
            if c.get("first") and c["first"] != label:
                tips.append(c["first"][:140])
            if prof.get("workflow"):
                tips.append("工作流：" + prof["workflow"])
            if prof.get("model"):
                tips.append("模型：" + prof["model"])
            it.setToolTip("\n".join(tips))
            self.lst_chat.addItem(it)
        if target:
            for i in range(self.lst_chat.count()):
                if self.lst_chat.item(i).data(Qt.UserRole) == target:
                    self.lst_chat.setCurrentRow(i)
                    break
        # 搜索时当前会话可能被过滤掉：只丢高亮，**不改 self._chat_id**，
        # 否则用户一边打字一边被强制切会话
        self.lst_chat.blockSignals(False)

    def _open_chat(self, cid: str):
        if not cid:
            return
        self._save_chat()                 # 切走前先存旧对话
        self._chat_id = cid
        d = ai_chat_store.load(cid)
        self._history = [m for m in (d.get("messages") or [])
                         if m.get("role") in ("user", "assistant")]
        self._summary = str(d.get("summary") or "")
        self._summary_upto = int(d.get("summary_upto") or 0)
        self._summary_inst = str(d.get("summary_inst") or "")
        # 换了实例 → 旧摘要里的端口/路径/模型全是错的，直接作废（2026-11-14）
        _iid = str((self._inst() or {}).get("id") or "")
        if self._summary and _iid and self._summary_inst \
                and self._summary_inst != _iid:
            self._summary, self._summary_upto, self._summary_inst = "", 0, ""
        self._chat_skills = list(d.get("skills") or [])
        self._artifacts = ai_chat_store._clip_artifacts(d.get("artifacts") or [])
        # 工作台设定随会话走：切过去就是另一套工作流/模型/技能，
        # 三个状态必须一起换（2026-11-11），漏一个就会「串台」
        self._profile = ai_skills.norm_profile(d.get("profile"))
        self._update_session_btn()
        self._pending_options = []
        # 待发附件属于上一条会话的上下文，切走必须清掉（2026-11-11）
        self._atts.clear()
        self._refresh_atts()
        self._clear_flow()
        if self._history:
            for m in self._history:
                if m["role"] == "user":
                    self._user_bubble(m["content"])
                else:
                    self._ai_card(m["content"])
            self._last_user = next(
                (m["content"] for m in reversed(self._history)
                 if m["role"] == "user"), "")
        else:
            self._greet()
        _refresh_right_safe(self)

    def _save_chat(self):
        if not self._chat_id:
            return
        # 只配了工作台、还没说话的会话也要落盘（2026-11-11）：
        # 否则「先建会话、配好 H3 工作流、再发第一条」会把刚配的设定丢掉。
        if not self._history and ai_skills.is_empty(self._profile):
            return
        title = ""
        if not self._history:        # 还没消息：拿用途当标题，列表里一眼能认
            title = str((self._profile or {}).get("topic") or "") or "新对话"
        ai_chat_store.save(self._chat_id, self._history, title=title,
                           summary=self._summary, upto=self._summary_upto,
                           skills=self._chat_skills, profile=self._profile,
                           artifacts=getattr(self, "_artifacts", None),
                           summary_inst=self._summary_inst)

    def _on_chat_item_changed(self, cur, _prev):
        if self._worker is not None:      # 正在回答时不许切，免得串台
            self.lst_chat.blockSignals(True)
            for i in range(self.lst_chat.count()):
                if self.lst_chat.item(i).data(Qt.UserRole) == self._chat_id:
                    self.lst_chat.setCurrentRow(i)
                    break
            self.lst_chat.blockSignals(False)
            return
        cid = cur.data(Qt.UserRole) if cur else ""
        if cid and cid != self._chat_id:
            self._open_chat(cid)

    def _new_chat(self):
        if self._worker is not None:
            return
        self._save_chat()
        self._chat_id = ai_chat_store.new_id()
        self._history = []
        self._summary = ""
        self._summary_upto = 0
        self._summary_inst = ""
        self._chat_skills = []
        self._profile = {}
        self._artifacts = []
        self._update_session_btn()
        self._pending_options = []
        self._last_user = ""
        self._atts.clear()          # 换会话清附件，别把上一条的图带过去
        self._refresh_atts()
        self._clear_flow()
        self._greet()
        self._reload_chat_list()
        _refresh_right_safe(self)

    def _rename_chat(self):
        if not self._chat_id or self._worker is not None:
            return
        cur = next((c["title"] for c in ai_chat_store.list_chats()
                    if c["id"] == self._chat_id), "")
        name, ok = QInputDialog.getText(self, "重命名对话", "对话名称：", text=cur)
        if ok and name.strip():
            ai_chat_store.rename(self._chat_id, name.strip())
            self._reload_chat_list(select=self._chat_id)

    def _delete_chat(self):
        if self._worker is not None:
            return
        if self._chat_id:
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, "删除对话",
                                    "删除当前对话？此操作不可恢复。") != QMessageBox.Yes:
                return
            ai_chat_store.delete(self._chat_id)
        self._chat_id = ai_chat_store.new_id()
        self._history = []
        self._summary = ""
        self._summary_upto = 0
        self._summary_inst = ""
        self._chat_skills = []
        self._profile = {}
        self._artifacts = []
        self._update_session_btn()
        self._pending_options = []
        self._last_user = ""
        self._atts.clear()          # 换会话清附件，别把上一条的图带过去
        self._refresh_atts()
        self._clear_flow()
        self._greet()
        self._reload_chat_list()
        _refresh_right_safe(self)

    # ------------------------------------------ 会话栏折叠 / 右键菜单（2026-11-11）
    def _toggle_side(self):
        self.side.setVisible(not self.side.isVisible())
        self.b_side.setText("☰ 会话" if self.side.isVisible() else "» 会话")

    def _chat_menu(self, pos):
        it = self.lst_chat.itemAt(pos)
        if not it:
            return
        cid = it.data(Qt.UserRole)
        if cid and cid != self._chat_id and self._worker is None:
            self._open_chat(cid)          # 右键先切到该会话，操作作用于当前
        menu = QMenu(self)
        act_r = menu.addAction("重命名")
        act_c = menu.addAction("以此工作台新建")
        act_c.setEnabled(not ai_skills.is_empty(self._profile))
        act_d = menu.addAction("删除")
        act = menu.exec(self.lst_chat.mapToGlobal(pos))
        if act == act_r:
            self._rename_chat()
        elif act == act_c:
            self._new_from_profile()
        elif act == act_d:
            self._delete_chat()

    def _new_from_profile(self):
        """复制当前工作台开一个新对话（同用途多条会话，省得重配）"""
        if self._worker is not None:
            return
        prof = ai_skills.norm_profile(self._profile)
        skills = list(self._chat_skills or [])
        self._new_chat()
        if not ai_skills.is_empty(prof):
            self._profile, self._chat_skills = prof, skills
            self._update_session_btn()
            self._save_chat()
            self._reload_chat_list(select=self._chat_id)
            self._clear_flow()
            self._greet()

    # -------------------------------------- 会话工作台设定（2026-11-11）
    def _update_session_btn(self):
        # 按钮文字恒定（i18n 词表精确匹配）；动态信息一律走 tooltip
        p = ai_skills.norm_profile(self._profile)
        bits = []
        if p.get("topic"):
            bits.append("用途：" + p["topic"])
        if p.get("workflow"):
            bits.append("工作流：" + p["workflow"])
        if p.get("model"):
            bits.append("模型：" + p["model"])
        if p.get("inst_id"):
            bits.append("实例：" + self._inst_name(p["inst_id"]))
        if self._chat_skills:
            bits.append(f"技能 {len(self._chat_skills)} 个")
        self.b_skills.setToolTip(
            ("本对话工作台：\n· " + "\n· ".join(bits) + "\n（点击修改）") if bits
            else "给这条对话固定一套工作台：用途 / 实例 / 工作流 / 模型 / 技能")
        # 设定/技能一变，右侧概要页立刻跟着变（2026-11-12）
        try:
            _refresh_right_safe(self)
        except Exception:
            pass

    def _inst_name(self, iid: str) -> str:
        try:
            for i in self.mainwin.cfg.get("instances") or []:
                if str(i.get("id") or "") == str(iid):
                    return i.get("name") or "实例"
        except Exception:
            pass
        return "（已删除的实例）"

    def _profile_inst(self):
        """会话绑定的实例；没绑或已删除则回落当前实例"""
        iid = str((self._profile or {}).get("inst_id") or "")
        if iid:
            try:
                for i in self.mainwin.cfg.get("instances") or []:
                    if str(i.get("id") or "") == iid:
                        return i
            except Exception:
                pass
        try:
            return self.mainwin.current_inst()
        except Exception:
            return {}

    def _pick_session(self):
        """一条对话 = 一套工作台：用途 / 实例 / 工作流 / 模型 / 技能

        用户场景（2026-11-11）：A 对话做 H3 视频、B 对话做 Qwen 生图、
        C 对话做 minimax 音乐。三条对话各挂各的技能与工作流，
        切换时上下文与配置都不串——所以配置必须存在**会话**里，不是全局。
        """
        prof = ai_skills.norm_profile(self._profile)
        try:
            insts = list(self.mainwin.cfg.get("instances") or [])
        except Exception:
            insts = []
        dlg = QDialog(self)
        dlg.setWindowTitle("会话设定")
        dlg.setMinimumWidth(560)
        v = QVBoxLayout(dlg)
        v.setSpacing(8)
        tip = QLabel("给这条对话固定一套工作台。不同对话各配各的——"
                     "比如「H3 视频」挂视频工作流、「Qwen 生图」挂生图工作流，"
                     "切换对话时配置与上下文互不影响。")
        tip.setWordWrap(True)
        v.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        ed_topic = QLineEdit(prof.get("topic") or "")
        ed_topic.setPlaceholderText("例：H3 视频制作 / Qwen 生图 / minimax 音乐")
        form.addRow("用途", ed_topic)

        cb_inst = QComboBox()
        cb_inst.addItem("跟随当前实例", "")
        for i in insts:
            cb_inst.addItem(i.get("name") or "实例", str(i.get("id") or ""))
        j = cb_inst.findData(prof.get("inst_id") or "")
        cb_inst.setCurrentIndex(j if j >= 0 else 0)
        form.addRow("实例", cb_inst)

        cb_wf = QComboBox()
        form.addRow("工作流", cb_wf)

        def _fill_wf():
            want = cb_wf.currentData()
            if want is None:                  # 首次填充：用已保存的绑定
                want = prof.get("workflow") or ""
            cb_wf.blockSignals(True)
            cb_wf.clear()
            cb_wf.addItem("不绑定（让 AI 自己找）", "")
            iid = cb_inst.currentData() or ""
            path = ""
            for i in insts:
                if str(i.get("id") or "") == str(iid):
                    path = i.get("comfyui_path") or ""
                    break
            if not iid:      # 跟随当前实例：取主窗口当下选中的那个，不是会话绑定值
                try:
                    path = (self.mainwin.current_inst() or {}).get("comfyui_path") or ""
                except Exception:
                    path = ""
            wd = os.path.join(path, "user", "default", "workflows") if path else ""
            names = []
            if wd and os.path.isdir(wd):
                names = sorted(f for f in os.listdir(wd)
                               if f.lower().endswith(".json"))
            for fn in names:
                cb_wf.addItem(fn, fn)
            if want and want not in names:
                # 换实例/文件改名时不要把绑定静默清空：显式标出来让用户决定
                cb_wf.addItem(f"{want}（当前实例未找到）", want)
            k = cb_wf.findData(want)
            cb_wf.setCurrentIndex(k if k >= 0 else 0)
            cb_wf.blockSignals(False)

        _fill_wf()
        cb_inst.currentIndexChanged.connect(lambda _i: _fill_wf())

        ed_model = QLineEdit(prof.get("model") or "")
        ed_model.setPlaceholderText("例：qwen_image_2512.safetensors（可留空）")
        form.addRow("模型", ed_model)
        v.addLayout(form)

        skills = ai_skills.list_skills()
        boxes = []
        if skills:
            v.addWidget(QLabel("本对话启用的技能："))
            for s in skills:
                # 声明了 tools 的技能会收窄本对话的工具表（2026-11-14）。
                # 直接标在名字后面 + tooltip 说明，用户才知道勾了会少哪些能力。
                declared = s.get("tools")
                label = s["name"]
                tip = (s["desc"] or "") + "\n\n" + (s["body"] or "")[:300]
                if declared is not None:
                    # 用固定词而不是「只带 N 个工具」：计数是动态值，
                    # 拼进标签就翻译不了（i18n 只做精确匹配）
                    label += "（" + T("工具收窄") + "）"
                    tip += ("\n\n" + T("勾选后本对话只保留该技能声明的工具，"
                                       "外加 get_health / ask_user / recall_history "
                                       "三个基础能力；工具表更短，模型更不容易选错。"))
                cbx = QCheckBox(label)
                cbx.setToolTip(tip)
                cbx.setChecked(s["id"] in self._chat_skills)
                v.addWidget(cbx)
                boxes.append((s["id"], cbx))
        else:
            hint = QLabel("没有技能文件。把 markdown 放进：\n" + ai_skills.user_dir())
            hint.setWordWrap(True)
            v.addWidget(hint)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        self._profile = ai_skills.norm_profile({
            "topic": ed_topic.text(), "inst_id": cb_inst.currentData() or "",
            "workflow": cb_wf.currentData() or "", "model": ed_model.text()})
        self._chat_skills = [sid for sid, cbx in boxes if cbx.isChecked()]
        self._update_session_btn()
        self._save_chat()                          # 设定本身也落盘（可能还没说话）
        self._reload_chat_list(select=self._chat_id)   # 标题/用途标即时可见

    def _clear_flow(self):
        self._tl_widget = None
        while self.flow.count() > 1:
            it = self.flow.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)      # 立刻脱离：只 deleteLater 的话在下一轮事件循环前
                w.deleteLater()        # 还挂在页面上，会露出一张 640×480 的孤儿卡
        self._sweep_orphans()

    def _sweep_orphans(self):
        """清掉没挂在 flow 布局里的遗留子控件（2026-11-08 修显示问题）

        现象：切到历史会话后，页面上浮着一张 ~640×480 的空白卡片（问候卡没被真正移除）。
        根因：takeAt() 只把控件从布局里摘掉，控件本身仍是 flow_host 的子控件，
        在 deleteLater 生效前一直以初始尺寸显示。
        """
        keep = set()
        for i in range(self.flow.count()):
            w = self.flow.itemAt(i).widget()
            if w is not None:
                keep.add(w)
        for ch in list(self.flow_host.children()):
            if isinstance(ch, QWidget) and ch not in keep:
                ch.hide()
                ch.setParent(None)
                ch.deleteLater()

    def _on_event(self, ev: dict):
        kind = ev.get("kind")
        if kind == "reasoning":       # 模型在思考：更新占位文案，不撤占位
            if self._think_lb is not None:
                self._think_lb.setText(f"思考中… {int(ev.get('chars') or 0)} 字")
            return
        if kind != "delta":
            self._think_stop()        # 正文/工具/错误到达即撤占位
        if kind == "delta":
            self._stream_append(ev.get("text", ""))
        elif kind == "notice":
            self._notice(ev.get("text", ""))
        elif kind == "usage":
            # 只更新这一行，不调 _refresh_right（那里会 list_chats 读盘，
            # 每轮调一次等于每轮遍历所有会话文件）
            u = {k: ev.get(k) for k in ("prompt", "completion", "cached",
                                        "exact", "rounds")}
            self._usage_map[self._chat_id] = u
            self._set_usage_label(u)
        elif kind == "compress":
            self._notice(f"上下文较长，正在压缩较早的 {int(ev.get('count') or 0)} 条对话…")
        elif kind == "compress_done":
            self._summary = str(ev.get("summary") or "")
            self._summary_upto = int(ev.get("upto") or 0)
            # 记下这份摘要属于哪个实例：换实例后要作废（2026-11-14）
            self._summary_inst = str((self._inst() or {}).get("id") or "")
            self._save_chat()
            self._notice(f"已把早期对话压缩为 {int(ev.get('chars') or 0)} 字摘要，"
                         f"后续问答会带着它继续")
            _refresh_right_safe(self)
        elif kind == "assistant":
            self._stream_finalize(ev.get("text", ""))
            self._history.append({"role": "assistant", "content": ev.get("text", "")})
            self._save_chat()
        elif kind == "tool":
            self._tool_item(ev.get("name", "?"), ev.get("args") or {})
            if ev.get("name") == "ask_user":
                opts = [str(o).strip() for o in ((ev.get("args") or {})
                        .get("options") or []) if str(o).strip()]
                if opts:
                    self._pending_options = opts[:6]
        elif kind == "tool_result":
            self._finish_tool_item(ev.get("name", "?"), ev.get("ok", False),
                                   ev.get("brief", ""), int(ev.get("ms") or 0))
            # 产物：工具落盘的文件（图 / 工作流 / 模型 / 日志）→ 右侧概要栏
            for a in (ev.get("artifacts") or []):
                self._add_artifact(a)
            # 2026-11-07：AI 帮忙起/停实例后，顶部状态点还挂着旧结论
            # （用户截图里 AI 说"已就绪"、上面仍写"未启动"）→ 立刻重探一次，
            # 不等 5 秒轮询；再补一拍给刚监听的端口留点时间
            if ev.get("name") == "control_instance":
                self.refresh_inst()
                QTimer.singleShot(2500, self.refresh_inst)
        elif kind == "image":
            self._show_images(ev.get("paths") or [])
            for p in (ev.get("paths") or []):
                self._add_artifact({"kind": "image", "path": p})
        elif kind == "error":
            self._err_bubble(ev.get("text", "未知错误"), ev.get("raw") or "")

    def _on_done(self):
        self._think_stop()
        self.b_send.setEnabled(True)
        self.b_stop.setEnabled(False)
        self._worker = None
        self._save_chat()
        self._reload_chat_list(select=self._chat_id)   # 刷新标题/条数
        self._option_row()                             # ask_user 选项按钮（2026-11-11）
        _refresh_right_safe(self)

    # ---------- ask_user 选项按钮（2026-11-11）----------
    def _option_row(self):
        """轮次结束时把 ask_user 的选项渲染成按钮行（选完即焚，防重复点）"""
        if not self._pending_options:
            return
        opts, self._pending_options = self._pending_options, []
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(8)
        for o in opts:
            b = QPushButton(o)
            b.setStyleSheet(self.ST_CHIP)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=o, rr=row: self._pick_option(t, rr))
            h.addWidget(b)
        h.addStretch(1)
        self.flow.addWidget(row)

    def _pick_option(self, text: str, row):
        for b in row.findChildren(QPushButton):
            b.setEnabled(False)               # 选完禁用整行，防重复发送
        self._quick(text)

    # ---------- 流式渲染 ----------
    def _stream_begin(self):
        if self._stream is not None:
            return
        self._ai_card("", streaming=True)
        self._stream = {"buf": "", "dirty": False}
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(60)      # 60ms 合并刷新，避免每个 token 重排
        self._stream_timer.timeout.connect(self._stream_flush)
        self._stream_timer.start()

    def _stream_append(self, text: str):
        if self._stream is None:
            self._stream_begin()
        self._stream["buf"] += text
        self._stream["dirty"] = True

    def _stream_flush(self):
        st = self._stream
        if not st or not st.get("dirty"):
            return
        card = getattr(self, "_last_card", None)
        if not card:
            return
        st["dirty"] = False
        body = card["body"]
        card["text"] = st["buf"]
        html = _md_to_html(st["buf"])
        body.setText(html)
        w = self._chat_width() - 28
        body.setFixedWidth(w)
        body.setFixedHeight(self._rich_height(html, w, body.font()))
        self._scroll_bottom()

    def _stream_finalize(self, full_text: str):
        st, card = self._stream, getattr(self, "_last_card", None)
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        self._stream = None
        if card is None:
            if full_text.strip():
                self._ai_card(full_text)
            return
        text = full_text or (st or {}).get("buf", "") or ""
        self._stream_flush_direct(text)
        card["copy"].setEnabled(True)
        card["copy"].setProperty("payload", text)
        card["re"].setEnabled(bool(self._last_user))
        model = str((self.cfg.get("settings") or {}).get("ai_model") or "") or "AI"
        card["cap"].setText("AI 助手 · " + model + " · " + QTime.currentTime().toString("HH:mm"))
        self._relayout_messages()      # 流式期间 resize 被跳过，收尾补一次重排

    def _stream_flush_direct(self, text: str):
        card = getattr(self, "_last_card", None)
        if not card:
            return
        body = card["body"]
        card["text"] = text
        card["copy"].setProperty("payload", text)
        html = _md_to_html(text)
        body.setText(html)
        w = self._chat_width() - 28
        body.setFixedWidth(w)
        body.setFixedHeight(self._rich_height(html, w, body.font()))
        self._scroll_bottom()

    def _notice(self, text: str):
        """灰色提示行（如：该模型不支持关闭思考）"""
        lb = QLabel("· " + text)
        lb.setObjectName("ChatBody")
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#7f8894; font-size:11px;")
        lb.setFixedWidth(self._chat_width() - 40)
        lb.setFixedHeight(self._wrap_height(lb, text, self._chat_width() - 40, 0, 2))
        wrap = self._row()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(2, 0, 0, 0)
        h.addWidget(lb)
        self.flow.insertWidget(self.flow.count() - 1, wrap)
        self._scroll_bottom()

    # ---------- 「思考中…」占位（非流式时的等待反馈） ----------
    def _think_start(self):
        import time
        self._think_t0 = time.time()
        lb = QLabel("思考中… 0.0s")
        lb.setObjectName("ChatBody")
        lb.setStyleSheet("color:#8a93a3; font-size:12px;")
        wrap = self._row()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(2, 0, 0, 0)
        h.addWidget(lb)
        h.addStretch(1)
        self.flow.insertWidget(self.flow.count() - 1, wrap)
        self._think_lbl = wrap
        self._think_lb = lb
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(200)
        self._think_timer.timeout.connect(self._think_tick)
        self._think_timer.start()
        self._scroll_bottom()

    def _think_tick(self):
        import time
        if self._think_lb is None:
            return
        sec = time.time() - self._think_t0
        if sec > 45:
            # 2026-11-06：实测系统代理抖动时请求会长时间无数据，用户只看到「思考中」会很懵
            self._think_lb.setText(
                f"已等待 {sec:.0f}s，模型还没有返回数据（常见于网络/代理不稳，可点「停止」重试）")
        elif sec > 20:
            self._think_lb.setText(f"仍在等待模型响应… {sec:.0f}s")
        else:
            self._think_lb.setText(f"思考中… {sec:.1f}s")

    def _think_stop(self):
        if self._think_timer is not None:
            self._think_timer.stop()
            self._think_timer = None
        if self._think_lbl is not None:
            self._think_lbl.setParent(None)
            self._think_lbl.deleteLater()
            self._think_lbl = None
            self._think_lb = None

    def _scroll_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    # -------------------------------------------------- 对外（兼容旧调用）
    def _add_bubble(self, role: str, text: str):
        if role == "user":
            self._user_bubble(text)
        elif role == "error":
            self._err_bubble(text)
        else:
            self._ai_card(text)

    def _add_tool_card(self, name: str, args: dict, brief=None):
        self._tool_item(name, args or {})

    def _finish_tool_card(self, name: str, ok: bool, brief: str, ms: int = 0):
        self._finish_tool_item(name, ok, brief, ms)

    def refresh_inst(self):
        """显示实例信息并**异步探测端口**：状态点必须反映"真的在跑"，不是"配置里有" """
        inst = self._inst()
        if not inst:
            self.lb_inst.setText("未选择实例")
            self.lb_dot.setStyleSheet("color:#888; font-size:11px;")
            return
        self.lb_inst.setText(f"{inst.get('name', '?')} · :{inst.get('port')}")
        self._probe_port(int(inst.get("port") or 8188))

    def _probe_port(self, port: int):
        from PySide6.QtNetwork import QTcpSocket
        sock = QTcpSocket(self)
        done = {"v": False}

        def finish(ok: bool):
            if done["v"]:
                return
            done["v"] = True
            color = "#4cd47c" if ok else "#e24b4a"
            self.lb_dot.setStyleSheet(f"color:{color}; font-size:11px;")
            base = self.lb_inst.text().split("（")[0]
            self.lb_inst.setText(base if ok else base + "（未启动）")
            self.lb_inst.setStyleSheet(
                "color:#a9b1bd; font-size:12px;" if ok
                else "color:#e2888a; font-size:12px;")
            try:
                sock.abort()
                sock.deleteLater()
            except Exception:
                pass

        sock.connected.connect(lambda: finish(True))
        sock.errorOccurred.connect(lambda *_: finish(False))
        QTimer.singleShot(600, lambda: finish(False))
        sock.connectToHost("127.0.0.1", port)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_inst()
        # 2026-11-08 修显示问题：历史消息/问候卡是在页面还没拿到真实宽度时渲染的，
        # 正文被 setFixedWidth 钉在 ~300px，显示成一坨窄卡片（截图可见）。
        # 首次显示后强制重排一次，按真实聊天区宽度重算。
        if not getattr(self, "_relayout_done", False):
            self._relayout_done = True
            QTimer.singleShot(0, self._relayout_messages)
        if getattr(self, "_status_timer", None) is None:
            self._status_timer = QTimer(self)
            self._status_timer.setInterval(5000)
            self._status_timer.timeout.connect(self.refresh_inst)
            self._status_timer.start()
