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

from PySide6.QtCore import (QBuffer, QEvent, QRect, QSize, Qt, QThread,
                            QTime, QTimer, Signal)
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame,
                               QHBoxLayout, QInputDialog, QLabel,
                               QPushButton, QScrollArea, QSizePolicy, QStyle,
                               QTextEdit, QToolButton, QVBoxLayout, QWidget)

from app import ai_agent, ai_chat_store


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


class _AgentWorker(QThread):
    """ai_agent.run_agent 的线程壳；事件经 sig_event(dict) 回 UI"""
    sig_event = Signal(dict)
    sig_done = Signal()

    def __init__(self, cfg, inst, text, history, attachments=None, mainwin=None,
                 summary="", summary_upto=0):
        super().__init__()
        self.cfg, self.inst, self.text, self.history = cfg, inst, text, history
        self.attachments = attachments or []
        self.mainwin = mainwin          # 工具里启停实例要用（QTimer 编组回 UI 线程）
        self.summary = summary          # 已压缩的早期对话摘要（2026-11-06）
        self.summary_upto = summary_upto
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
                               summary_upto=self.summary_upto)
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
        self._summary_upto = 0
        self._build()
        self._boot_chats()

    # -------------------------------------------------- UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

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
        # ---- 会话切换（2026-11-06：多对话 + 重启不丢）----
        self.cb_chat = QComboBox()
        self.cb_chat.setMinimumWidth(190)
        self.cb_chat.setToolTip("选择历史对话；切换后当前对话会自动保存")
        self.cb_chat.currentIndexChanged.connect(self._on_chat_changed)
        head.addWidget(self.cb_chat)
        for _txt, _tip, _fn in (("＋ 新建", "开一个新对话（当前对话自动保存）", self._new_chat),
                                ("重命名", "给当前对话改个名字", self._rename_chat),
                                ("删除", "删除当前对话", self._delete_chat)):
            _b = QPushButton(_txt)
            _b.setStyleSheet(self.ST_LINK)
            _b.setCursor(Qt.PointingHandCursor)
            _b.setToolTip(_tip)
            _b.clicked.connect(lambda _=False, f=_fn: f())
            head.addWidget(_b)
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

        # ---- 输入区 ----
        row = QHBoxLayout()
        row.setSpacing(8)
        self.ed = QTextEdit()
        self.ed.setFixedHeight(58)
        self.ed.setPlaceholderText("问点什么…（回车发送，Shift+回车换行；可直接粘贴图片或工作流 JSON）")
        self.ed.installEventFilter(self)
        row.addWidget(self.ed, 1)
        self.b_stop = QPushButton("停止")
        self.b_stop.setFixedSize(60, 30)
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        row.addWidget(self.b_stop, 0, Qt.AlignBottom)
        self.b_send = QPushButton("发送")
        self.b_send.setFixedSize(74, 30)
        self.b_send.setProperty("accent", True)
        self.b_send.clicked.connect(self._send)
        row.addWidget(self.b_send, 0, Qt.AlignBottom)
        root.addLayout(row)

        self._greet()
        self.refresh_backdrop()

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
        """AI 卡片正文按当前聊天区宽度定型（窗口缩放后需重算）"""
        cw = self._chat_width() - 28
        body.setFixedWidth(cw)
        body.setFixedHeight(self._rich_height(html, cw, body.font()))

    def _relayout_messages(self):
        """窗口尺寸变化后重排已有消息（2026-11-07 用户报障）

        历史对话在 __init__ 里渲染，那一刻页面还是 640x480 的默认尺寸，
        气泡/正文宽度被 setFixedWidth 钉死；窗口放大后卡片被布局拉满、
        正文却还是窄的，右边留一大片空白，看着像"半截横条"。
        这里按新宽度就地重算，控件复用——不重建、不丢工具时间线。
        """
        if getattr(self, "_stream", None):
            return                              # 流式中别动，等结束
        for i in range(self.flow.count()):
            it = self.flow.itemAt(i)
            w = it.widget() if it is not None else None
            if w is None:
                continue
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
        self._ai_card(
            "我是 ComfyHub 的 AI 助手，可以帮你查看实例状态、读日志排错、"
            "管理模型、检查和运行工作流。点上方快捷指令，或直接输入问题。")

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

    def _err_bubble(self, text: str):
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lb.setStyleSheet(self.ST_ERR)
        lb.setProperty("chatKind", "err")
        lb.setProperty("chatText", text)
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
        try:
            return self.mainwin.current_inst()
        except Exception:
            return {}

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
                                    summary_upto=self._summary_upto)
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
        self.cb_chat.blockSignals(True)
        self.cb_chat.clear()
        chats = ai_chat_store.list_chats()
        for c in chats:
            label = c.get("title") or "新对话"
            if c.get("count"):
                label += f"（{c['count']} 条）"
            self.cb_chat.addItem(label, c["id"])
        if not chats:
            self.cb_chat.addItem("（新对话）", "")
        if select:
            i = self.cb_chat.findData(select)
            if i >= 0:
                self.cb_chat.setCurrentIndex(i)
        self.cb_chat.blockSignals(False)

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

    def _save_chat(self):
        if not self._chat_id or not self._history:
            return
        ai_chat_store.save(self._chat_id, self._history,
                           summary=self._summary, upto=self._summary_upto)

    def _on_chat_changed(self, idx: int):
        if self._worker is not None:      # 正在回答时不许切，免得串台
            return
        cid = self.cb_chat.itemData(idx) or ""
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
        self._last_user = ""
        self._clear_flow()
        self._greet()
        self._reload_chat_list()

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
        self._last_user = ""
        self._clear_flow()
        self._greet()
        self._reload_chat_list()

    def _clear_flow(self):
        self._tl_widget = None
        while self.flow.count() > 1:
            it = self.flow.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

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
        elif kind == "compress":
            self._notice(f"上下文较长，正在压缩较早的 {int(ev.get('count') or 0)} 条对话…")
        elif kind == "compress_done":
            self._summary = str(ev.get("summary") or "")
            self._summary_upto = int(ev.get("upto") or 0)
            self._save_chat()
            self._notice(f"已把早期对话压缩为 {int(ev.get('chars') or 0)} 字摘要，"
                         f"后续问答会带着它继续")
        elif kind == "assistant":
            self._stream_finalize(ev.get("text", ""))
            self._history.append({"role": "assistant", "content": ev.get("text", "")})
            self._save_chat()
        elif kind == "tool":
            self._tool_item(ev.get("name", "?"), ev.get("args") or {})
        elif kind == "tool_result":
            self._finish_tool_item(ev.get("name", "?"), ev.get("ok", False),
                                   ev.get("brief", ""), int(ev.get("ms") or 0))
            # 2026-11-07：AI 帮忙起/停实例后，顶部状态点还挂着旧结论
            # （用户截图里 AI 说"已就绪"、上面仍写"未启动"）→ 立刻重探一次，
            # 不等 5 秒轮询；再补一拍给刚监听的端口留点时间
            if ev.get("name") == "control_instance":
                self.refresh_inst()
                QTimer.singleShot(2500, self.refresh_inst)
        elif kind == "image":
            self._show_images(ev.get("paths") or [])
        elif kind == "error":
            self._err_bubble(ev.get("text", "未知错误"))

    def _on_done(self):
        self._think_stop()
        self.b_send.setEnabled(True)
        self.b_stop.setEnabled(False)
        self._worker = None
        self._save_chat()
        self._reload_chat_list(select=self._chat_id)   # 刷新标题/条数

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
        if getattr(self, "_status_timer", None) is None:
            self._status_timer = QTimer(self)
            self._status_timer.setInterval(5000)
            self._status_timer.timeout.connect(self.refresh_inst)
            self._status_timer.start()
