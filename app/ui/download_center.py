# -*- coding: utf-8 -*-
"""下载中心：全局下载队列 + 右上角任务指示器（下拉面板显示每个任务的进度）

用法（models_page）：
    self.dl_center = DownloadCenter()
    head.addWidget(self.dl_center)              # 指示器按钮放进标题栏
    self.dl_center.sig_queue_done.connect(self.refresh_stats)

入队：
    self.dl_center.enqueue([(repo_id, file_path, dest_dir), ...])

2026-11-11 UI 重做（方案 B）：
  - 行结构规范化：图标 + 文件名（两行内折行）/ 进度条 / 状态 + 数值
  - 失败原因友好化翻译（WinError 10060 → 连接超时…），原始异常进 tooltip + 「复制详情」
  - 单任务可取消、失败可原地重试；**失败行保留到手动清空**（清了就没法重试）
  - 面板加标题栏：任务计数 + 「清空已完成」
"""
import itertools
import os

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QToolButton, QMenu, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QWidgetAction, QScrollArea, QSizePolicy, QFrame,
)

from app import model_store as ms
from app.i18n import T
from app.workers import swap_worker

# ---- 设计系统 v2 令牌（与 app/ui/theme.py 一致）----
_C_TEXT = "#e6edf7"      # 主文字
_C_SUB = "#94a2b4"       # 次文字
_C_MUTED = "#6c7a8c"     # 弱文字
_C_ACCENT = "#4c8dff"    # 强调（下载中）
_C_OK = "#3ecf8e"        # 成功
_C_ERR = "#f0616d"       # 危险 / 失败
_C_IDLE = "#4a5260"      # 已取消 / 未开始
_C_AI = "#7aa2d0"        # AI 来源徽标

# 面板宽度（2026-11-12 改自适应）：原来是写死 440，用户反馈「不同电脑分辨率下
# 下载栏内容显示不全」——高 DPI 机器上字体实测宽度比 100% 缩放时大，440 逻辑像素
# 放不下「文件名 + 取消 + 长失败文案 + 复制详情」，QLabel 没有 wordWrap 时
# minimumSizeHint 就是全宽，压不动 → 整块被撑出屏幕被裁。
# 现在按字体度量算宽度，并受屏幕可用宽度约束（两边留白），同时面板限高可滚动。
_PANEL_W_MIN = 420
_PANEL_W_MAX = 640
_PANEL_H_RATIO = 0.55    # 面板最高占屏幕可用高度的比例（任务多时滚动）


def panel_width(font=None) -> int:
    """面板宽度：约 30 个汉字宽的排版空间，随字体/屏幕变化（420~640）。"""
    fm = QFontMetrics(font or QApplication.font())
    want = fm.horizontalAdvance("测") * 30 + 72
    scr = QGuiApplication.primaryScreen()
    cap = int(scr.availableGeometry().width() * 0.42) if scr else 760
    return max(_PANEL_W_MIN, min(want, cap, _PANEL_W_MAX))


def panel_max_height() -> int:
    scr = QGuiApplication.primaryScreen()
    return int(scr.availableGeometry().height() * _PANEL_H_RATIO) if scr else 520

# 常见网络/磁盘异常 → 人话。命中即用，未命中回落原文（截断显示）
_FRIENDLY_RULES = (
    (("10060",), "连接超时（网络不通或镜像站无响应）"),
    (("10061",), "连接被拒绝（目标服务器未响应）"),
    (("11001", "getaddrinfo", "Name or service not known"), "域名解析失败（DNS 不可用）"),
    (("10054", "reset by peer", "ConnectionReset", "Connection aborted"),
     "连接被重置（代理或网络中断）"),
    (("404",), "文件不存在（可能已改名或删除）"),
    (("401", "403", "Unauthorized", "Forbidden"), "需要授权（登录态或令牌无效）"),
    (("certificate", "SSL", "TLS", "CERTIFICATE"), "证书校验失败（可换源或关闭校验）"),
    (("timed out", "timeout", "超时", "TimeoutError"), "请求超时（服务端响应过慢）"),
    (("No space", "ENOSPC", "磁盘空间"), "磁盘空间不足"),
    (("Incomplete", "incomplete read"), "文件不完整（可重试续传）"),
    (("Connection refused",), "连接被拒绝（目标服务器未响应）"),
)


def friendly_error(raw) -> str:
    """把底层异常翻成人话；翻不出来就截断原文（保留可读性）。

    注意：**不吞错**——原始文本仍进 tooltip 与「复制详情」，供排查。
    """
    s = str(raw or "").strip()
    for keys, text in _FRIENDLY_RULES:
        if any(k in s for k in keys):
            return T(text)
    # 没命中规则：去掉模块前缀让文本短一点（urllib.error.URLError: xxx → xxx）
    short = s
    if ": " in short:
        head, tail = short.split(": ", 1)
        if "." in head or "Error" in head:
            short = tail
    return short if len(short) <= 80 else short[:78] + "…"


class _QueueWorker(QThread):
    """顺序下载队列。items = [(repo_id, file_path, dest_dir, task_id), ...]"""
    # 2026-10-31 修：大模型动辄 7GB+，字节数超 int32 上限（2147483647），
    # Signal(str, int, int) emit 时 shiboken 溢出报 OverflowError，进度条全程
    # 死亡 + stderr 刷屏。改用 object 承载 Python int 不做窄化转换。
    sig_progress = Signal(str, object, object)    # task_id, done, total（字节数）
    sig_state = Signal(str, str)                  # task_id, 排队中/下载中/完成/已取消
    sig_fail = Signal(str, str, str)              # task_id, 友好文案, 原始异常
    sig_queue_done = Signal(int, int)             # ok, bad

    def __init__(self, items):
        super().__init__()
        self.items = items
        self._cancelled = set()                   # 被取消的 task_id 集合（可多个）
        self._cancel_all = False

    def cancel_task(self, task_id):
        """取消单个任务（仍在排队的轮到时直接跳过）"""
        self._cancelled.add(task_id)

    def cancel_all(self):
        self._cancel_all = True
        for _r, _f, _d, tid in self.items:
            self._cancelled.add(tid)

    def _is_cancelled(self, tid):
        return self._cancel_all or tid in self._cancelled

    def run(self):
        ok = bad = 0
        for repo_id, file_path, dest_dir, task_id in self.items:
            if self._is_cancelled(task_id):
                self.sig_state.emit(task_id, "已取消")
                continue
            self.sig_state.emit(task_id, "下载中")
            try:
                ms.download_file(
                    repo_id, file_path, dest_dir,
                    progress_cb=lambda d, t, tid=task_id: self.sig_progress.emit(tid, d, t),
                    cancel_flag=lambda tid=task_id: self._is_cancelled(tid),
                )
                self.sig_state.emit(task_id, "完成")
                ok += 1
            except KeyboardInterrupt:
                self.sig_state.emit(task_id, "已取消")
                bad += 1
            except Exception as e:
                self.sig_fail.emit(task_id, friendly_error(e), str(e))
                bad += 1
        self.sig_queue_done.emit(ok, bad)


class _TaskRow(QWidget):
    """面板里的单任务行。

    结构：
        [图标] 文件名（最多两行，超出截断，完整名进 tooltip）   [取消/重试]
        [进度条]
        [状态词 / 失败原因]                         [1.2 / 2.4 GB · 50%]
    """

    sig_cancel = Signal(str)
    sig_retry = Signal(str)

    def __init__(self, task_id, fname, parent=None, source="ui"):
        super().__init__(parent)
        self.task_id = task_id
        self.fname = fname
        self.source = source            # "ui" 页面内下载 / "ai" AI 助手触发
        self.raw_error = ""
        self.state = "排队中"
        self.setObjectName("dlRow")
        self.setStyleSheet(
            "QWidget#dlRow { background: transparent;"
            " border-bottom: 1px solid #232b36; }")
        # 行本身不横向撑开面板：宽度永远由面板给
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 8, 6, 8)
        v.setSpacing(5)

        # ---- 第一行：图标 + [AI] + 文件名 + 行内按钮 ----
        h1 = QHBoxLayout()
        h1.setSpacing(7)
        self.lb_icon = QLabel("•")
        self.lb_icon.setFixedWidth(12)
        self.lb_icon.setStyleSheet(f"color:{_C_MUTED}; font-size:13px;")
        h1.addWidget(self.lb_icon, 0, Qt.AlignTop)

        # AI 来源徽标：用户反馈「AI 帮忙下载的不知道去哪看进度」，
        # 入队来源要能一眼分清（徽标只在 AI 触发时出现）
        self.lb_src = QLabel(T("AI"))
        self.lb_src.setStyleSheet(
            f"color:{_C_AI}; background:#1b2836; border:1px solid #2b3d52;"
            " border-radius:4px; font-size:10px; padding:0 4px;")
        self.lb_src.setAlignment(Qt.AlignCenter)
        self.lb_src.setFixedHeight(16)
        self.lb_src.setVisible(source == "ai")
        h1.addWidget(self.lb_src, 0, Qt.AlignTop)

        self.lb_name = QLabel(fname)
        self.lb_name.setWordWrap(True)
        self.lb_name.setToolTip(fname)
        self.lb_name.setStyleSheet(f"color:{_C_TEXT}; font-size:13px;")
        # 长文件名不能反过来撑宽布局（Ignored 让布局可以压到比 sizeHint 更窄，
        # 配合 wordWrap 折行；否则 QLabel 的 minimumSizeHint=全文宽，会把
        # 面板顶宽、超出屏幕后被裁——正是「显示不全」的根因之一）
        self.lb_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        # 最多两行高，再长就截断（行高稳定，面板各行不会高矮不齐）
        fm = QFontMetrics(self.lb_name.font())
        self.lb_name.setMaximumHeight(fm.lineSpacing() * 2 + 6)
        h1.addWidget(self.lb_name, 1)

        self.b_action = QPushButton(T("取消"))
        self.b_action.setFixedHeight(22)
        self.b_action.setMinimumWidth(46)
        self.b_action.setCursor(Qt.PointingHandCursor)
        self.b_action.setStyleSheet(
            "QPushButton { background:#232b36; color:%s; border:none;"
            " border-radius:6px; font-size:11px; padding:0 8px; }"
            "QPushButton:hover { background:#2c3542; color:%s; }" % (_C_SUB, _C_TEXT))
        self.b_action.clicked.connect(self._on_action)
        h1.addWidget(self.b_action, 0, Qt.AlignTop)
        v.addLayout(h1)

        # ---- 第二行：进度条 ----
        self.pb = QProgressBar()
        self.pb.setFixedHeight(6)
        self.pb.setTextVisible(False)
        self.pb.setRange(0, 1000)
        self.pb.setValue(0)
        self._paint_bar(_C_IDLE)
        v.addWidget(self.pb)

        # ---- 第三行：状态 + 数值 / 复制详情 ----
        h3 = QHBoxLayout()
        h3.setSpacing(6)
        self.lb_state = QLabel(T("排队中"))
        self.lb_state.setStyleSheet(f"color:{_C_MUTED}; font-size:11px;")
        # 失败原因可能是长句（「✕ 连接超时（网络不通或镜像站无响应）」）：
        # 允许折行 + 允许被压窄，绝不把面板顶宽
        self.lb_state.setWordWrap(True)
        self.lb_state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h3.addWidget(self.lb_state, 1)

        self.lb_num = QLabel("")
        self.lb_num.setStyleSheet(f"color:{_C_MUTED}; font-size:11px;")
        self.lb_num.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        h3.addWidget(self.lb_num, 0, Qt.AlignRight)

        self.b_copy = QPushButton(T("复制详情"))
        self.b_copy.setFixedHeight(20)
        self.b_copy.setCursor(Qt.PointingHandCursor)
        self.b_copy.setStyleSheet(
            "QPushButton { background:transparent; color:%s; border:none;"
            " font-size:11px; text-decoration:underline; padding:0 2px; }"
            "QPushButton:hover { color:#ff8f98; }" % _C_ERR)
        self.b_copy.clicked.connect(self._copy_error)
        self.b_copy.setVisible(False)
        h3.addWidget(self.b_copy, 0, Qt.AlignRight)
        v.addLayout(h3)

    # ------------------------------------------------ 内部
    def _paint_bar(self, color):
        self.pb.setStyleSheet(
            "QProgressBar { background:#14181e; border:none; border-radius:3px; }"
            "QProgressBar::chunk { background:%s; border-radius:3px; }" % color)

    def _on_action(self):
        if self.state in ("失败", "已取消"):
            self.sig_retry.emit(self.task_id)
        else:
            self.sig_cancel.emit(self.task_id)

    def _copy_error(self):
        text = f"{self.fname}\n{self.lb_state.text()}\n{self.raw_error}"
        try:
            QGuiApplication.clipboard().setText(text.strip())
        except Exception:
            pass

    # ------------------------------------------------ 状态机
    def reset(self):
        """重试：回到排队态，清掉上次的失败痕迹"""
        self.state = "排队中"
        self.raw_error = ""
        self.lb_icon.setText("•")
        self.lb_icon.setStyleSheet(f"color:{_C_MUTED}; font-size:13px;")
        self.lb_name.setStyleSheet(f"color:{_C_TEXT}; font-size:13px;")
        self.pb.setValue(0)
        self._paint_bar(_C_IDLE)
        self.lb_state.setText(T("排队中"))
        self.lb_state.setStyleSheet(f"color:{_C_MUTED}; font-size:11px;")
        self.lb_num.setText("")
        self.lb_num.setVisible(True)
        self.b_copy.setVisible(False)
        self.b_action.setText(T("取消"))
        self.b_action.setVisible(True)
        self.setToolTip("")

    def set_progress(self, done, total):
        if total and total > 0:
            # 2026-10-31 修：total 可达 7GB+，Qt int 参数同样溢出；
            # 进度条固定 0-1000 按比例走，字节数只进文字标签
            pct = min(1000, int(done * 1000 / total))
            self.pb.setValue(pct)
            gb = 1024 ** 3
            if total >= gb:
                num = f"{done / gb:.1f} / {total / gb:.1f} GB · {pct / 10:.0f}%"
            else:
                num = (f"{done / 1048576:,.0f} / {total / 1048576:,.0f} MB"
                       f" · {pct / 10:.0f}%")
            self.lb_num.setText(num)

    def set_state(self, kind):
        """kind: 下载中 / 完成 / 已取消"""
        self.state = kind
        if kind == "下载中":
            self.lb_icon.setText("↓")
            self.lb_icon.setStyleSheet(f"color:{_C_ACCENT}; font-size:13px;")
            self.lb_state.setText(T("下载中"))
            self.lb_state.setStyleSheet(f"color:{_C_SUB}; font-size:11px;")
            self._paint_bar(_C_ACCENT)
            self.lb_num.setVisible(True)
            self.b_copy.setVisible(False)
            self.b_action.setText(T("取消"))
            self.b_action.setVisible(True)
        elif kind == "完成":
            self.lb_icon.setText("✓")
            self.lb_icon.setStyleSheet(f"color:{_C_OK}; font-size:13px;")
            self.lb_state.setText(T("完成"))
            self.lb_state.setStyleSheet(f"color:{_C_OK}; font-size:11px;")
            self.pb.setValue(self.pb.maximum())
            self._paint_bar(_C_OK)
            self.b_action.setVisible(False)
            self.b_copy.setVisible(False)
        elif kind == "已取消":
            self.lb_icon.setText("×")
            self.lb_icon.setStyleSheet(f"color:{_C_MUTED}; font-size:13px;")
            self.lb_state.setText(T("已取消（断点保留，可重试）"))
            self.lb_state.setStyleSheet(f"color:{_C_MUTED}; font-size:11px;")
            self._paint_bar(_C_IDLE)
            self.lb_num.setVisible(False)
            self.b_action.setText(T("重试"))
            self.b_action.setVisible(True)

    def set_fail(self, friendly, raw):
        """失败态：友好原因给人看，原始异常留给「复制详情」"""
        self.state = "失败"
        self.raw_error = raw or ""
        self.lb_icon.setText("!")
        self.lb_icon.setStyleSheet(f"color:{_C_ERR}; font-size:13px; font-weight:700;")
        self.lb_state.setText("✕ " + friendly)
        self.lb_state.setStyleSheet(f"color:{_C_ERR}; font-size:11px;")
        self._paint_bar(_C_ERR)
        self.lb_num.setVisible(False)
        self.b_copy.setToolTip(T("复制完整错误：") + "\n" + (self.raw_error or "")[:500])
        self.b_copy.setVisible(True)
        self.b_action.setText(T("重试"))
        self.b_action.setVisible(True)
        self.setToolTip((self.raw_error or "")[:800])

    def is_terminal(self):
        return self.state in ("完成", "已取消", "失败")


class DownloadCenter(QToolButton):
    """右上角下载指示器按钮 + 下拉任务面板 + 全局下载队列

    线程约定（2026-11-12）：UI 侧一律在主线程操作；AI 助手在 _AgentWorker
    线程里要入队必须走 request_download()（信号编组），绝不能直接 enqueue()。
    """

    sig_queue_done = Signal(int, int)             # 转发：队列结束（页面刷新列表）
    sig_request = Signal(object)                  # 跨线程入队请求：(items, source)
    sig_ai_queued = Signal(int)                   # AI 触发了 N 个下载（主窗口提示用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("⬇ 0")
        self.setToolTip(T("下载任务"))
        self.setPopupMode(QToolButton.InstantPopup)
        self.setFixedHeight(30)
        # 文字是「⬇ 3」这类短计数：给个下限，别让高 DPI 的大字号挤成一坨
        self.setMinimumWidth(64)
        self._w = panel_width(self.font())

        self._menu = QMenu(self)
        self._menu.setStyleSheet(
            "QMenu { background:#14181e; border:1px solid #2a3340;"
            " border-radius:10px; padding:6px; }")
        self._panel = QWidget()
        self._root_v = QVBoxLayout(self._panel)
        self._root_v.setContentsMargins(4, 2, 4, 4)
        self._root_v.setSpacing(0)

        # ---- 标题栏：计数 + 清空已完成 ----
        self._head = QWidget()
        head_h = QHBoxLayout(self._head)
        head_h.setContentsMargins(8, 4, 4, 6)
        head_h.setSpacing(6)
        self.lb_head = QLabel(T("下载任务"))
        self.lb_head.setStyleSheet(f"color:{_C_TEXT}; font-size:12px; font-weight:600;")
        head_h.addWidget(self.lb_head)
        head_h.addStretch(1)
        self.b_clear = QPushButton(T("清空已完成"))
        self.b_clear.setFixedHeight(20)
        self.b_clear.setCursor(Qt.PointingHandCursor)
        self.b_clear.setStyleSheet(
            "QPushButton { background:transparent; color:%s; border:none;"
            " font-size:11px; padding:0 2px; }"
            "QPushButton:hover { color:%s; }" % (_C_MUTED, _C_TEXT))
        self.b_clear.setToolTip(T("清掉完成 / 已取消 / 失败的任务记录"))
        self.b_clear.clicked.connect(self.clear_finished)
        head_h.addWidget(self.b_clear)
        self._root_v.addWidget(self._head)

        # ---- 任务行容器（限高滚动：任务多时不再把菜单顶出屏幕被裁）----
        self._rows_host = QWidget()
        self._rows_v = QVBoxLayout(self._rows_host)
        self._rows_v.setContentsMargins(0, 0, 0, 0)
        self._rows_v.setSpacing(0)
        self._rows_v.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setMaximumHeight(panel_max_height())
        self._scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
            "QScrollBar:vertical { background:transparent; width:8px; margin:0; }"
            "QScrollBar::handle:vertical { background:#39424f; border-radius:4px;"
            " min-height:24px; }"
            "QScrollBar::handle:vertical:hover { background:#4a5565; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height:0; }")
        self._scroll.setWidget(self._rows_host)
        self._scroll.setVisible(False)
        self._root_v.addWidget(self._scroll)

        self._empty = QLabel(T("暂无下载任务"))
        self._empty.setStyleSheet(
            f"color:{_C_MUTED}; padding:10px 8px; font-size:12px;")
        self._root_v.addWidget(self._empty)

        self._menu.setMinimumWidth(self._w)
        self._menu.setMaximumWidth(self._w)
        act = QWidgetAction(self._menu)
        act.setDefaultWidget(self._panel)     # QMenu 嵌入自定义面板（QWidgetAction）
        self._menu.addAction(act)
        self.setMenu(self._menu)

        self._rows = {}                           # task_id -> _TaskRow
        self._tasks = {}                          # task_id -> {repo_id,file_path,dest_dir}
        self._worker = None
        self._pending = []                        # 等入队的任务
        self._head.setVisible(False)
        # 跨线程入队：emit 自任意线程 → 排队投递回本对象所属（GUI）线程
        self.sig_request.connect(self._on_request, Qt.QueuedConnection)
        self._refresh_head()

    # -------------------------------------------------- 入队
    def request_download(self, items, source="ai"):
        """**线程安全**入队入口：AI 助手等后台线程必须走这里。

        直接在线程里调 enqueue() 会在非 GUI 线程 new 出 _TaskRow（QWidget），
        Qt 允许但对象归属该线程，layout 不会真正接管 → 任务行永远不显示
        （用户反馈 2026-11-12「AI 帮忙下载看不到进度」的根因）。
        """
        items = [tuple(x) for x in (items or []) if x]
        if not items:
            return 0
        self.sig_request.emit((items, source))
        return len(items)

    def _on_request(self, payload):
        """GUI 线程侧：把请求落地成任务行"""
        try:
            items, source = payload
        except Exception:
            return
        self.enqueue(items, source=source)
        if source == "ai":
            # AI 触发的下载用户容易「不知道去哪看」：按钮脉冲一下 + 通知主窗口
            self._pulse()
            self.sig_ai_queued.emit(len(items))

    def _pulse(self):
        """按钮短暂高亮（AI 刚入队时把视线引到右上角 ⬇）"""
        self.setStyleSheet(
            "QToolButton { border:1px solid %s; border-radius:8px;"
            " color:%s; padding:0 6px; }" % (_C_AI, _C_TEXT))
        QTimer.singleShot(1800, lambda: self.setStyleSheet(""))

    def enqueue(self, items, source="ui"):
        """items = [(repo_id, file_path, dest_dir), ...]（仅限 GUI 线程调用）"""
        for repo_id, file_path, dest_dir in items:
            task_id = f"t{next(self._ids)}"
            fname = os.path.basename(file_path)
            row = _TaskRow(task_id, fname, source=source)
            row.sig_cancel.connect(self._cancel_one)
            row.sig_retry.connect(self._retry)
            self._rows_v.insertWidget(self._rows_v.count() - 1, row)
            self._rows[task_id] = row
            self._tasks[task_id] = {"repo_id": repo_id, "file_path": file_path,
                                    "dest_dir": dest_dir, "source": source}
            self._pending.append((repo_id, file_path, dest_dir, task_id))
        self._empty.setVisible(False)
        self._scroll.setVisible(bool(self._rows))
        self._fit_scroll()
        self._refresh_head()
        self._pump()

    _ids = itertools.count(1)

    def cancel_all(self):
        """取消全部下载任务（进行中 + 排队），保留 .part 供续传"""
        for _r, _f, _d, tid in self._pending:
            row = self._rows.get(tid)
            if row:
                row.set_state("已取消")
        self._pending = []
        if self._worker and self._worker.isRunning():
            self._worker.cancel_all()
        self._refresh_head()

    # -------------------------------------------------- 单任务操作
    def _cancel_one(self, task_id):
        """取消单个任务：仍在排队的直接摘除，运行中的交给 worker 标记"""
        keep = [it for it in self._pending if it[3] != task_id]
        if len(keep) != len(self._pending):
            self._pending = keep
            row = self._rows.get(task_id)
            if row:
                row.set_state("已取消")
            self._refresh_head()
            return
        if self._worker and self._worker.isRunning():
            self._worker.cancel_task(task_id)

    def _retry(self, task_id):
        """失败/取消后原地重试：同一行重置回排队态，任务重进队列"""
        info = self._tasks.get(task_id)
        row = self._rows.get(task_id)
        if not info or not row:
            return
        row.reset()
        self._pending.append((info["repo_id"], info["file_path"],
                              info["dest_dir"], task_id))
        self._refresh_head()
        self._pump()

    def _fit_scroll(self):
        """滚动区高度 = 内容实际高度（封顶到限高）：任务少时不留大片空白。"""
        if not self._rows:
            return
        need = self._rows_v.sizeHint().height()
        self._scroll.setFixedHeight(min(max(need, 60), panel_max_height()))

    def clear_finished(self):
        """清掉所有终态行（完成 / 已取消 / 失败）"""
        for tid, row in list(self._rows.items()):
            if row.is_terminal():
                row.setParent(None)
                row.deleteLater()
                self._rows.pop(tid, None)
                self._tasks.pop(tid, None)
        self._empty.setVisible(not self._rows)
        self._scroll.setVisible(bool(self._rows))
        self._refresh_head()

    # -------------------------------------------------- 队列驱动
    def _pump(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._pending:
            return
        w = _QueueWorker(self._pending)
        self._pending = []
        w.sig_progress.connect(self._on_progress)
        w.sig_state.connect(self._on_state)
        w.sig_fail.connect(self._on_fail)
        w.sig_queue_done.connect(self._on_queue_done)
        swap_worker(self, "_worker", w)
        w.start()

    # -------------------------------------------------- 队列信号
    def _on_progress(self, task_id, done, total):
        row = self._rows.get(task_id)
        if row:
            row.set_progress(done, total)

    def _on_state(self, task_id, state):
        row = self._rows.get(task_id)
        if row:
            row.set_state(state)
        self._refresh_head()

    def _on_fail(self, task_id, friendly, raw):
        row = self._rows.get(task_id)
        if row:
            row.set_fail(friendly, raw)
        self._refresh_head()

    def _on_queue_done(self, ok, bad):
        self._pump()                              # 继续处理后续入队
        if not self._pending and not (self._worker and self._worker.isRunning()):
            QTimer.singleShot(8000, self._maybe_clear)   # 8 秒后清掉成功/取消的行
        self.sig_queue_done.emit(ok, bad)

    def _maybe_clear(self):
        if self._worker and self._worker.isRunning():
            return
        for tid, row in list(self._rows.items()):
            # 失败行不自动清：它是重试入口，清了就没法重试了
            if row.state in ("完成", "已取消"):
                row.setParent(None)
                row.deleteLater()
                self._rows.pop(tid, None)
                self._tasks.pop(tid, None)
        self._empty.setVisible(not self._rows)
        self._scroll.setVisible(bool(self._rows))
        self._refresh_head()

    def _refresh_head(self):
        """标题栏 + 徽标 + 按钮可用态。

        标题只放固定词（i18n 词表精确匹配）；计数走徽标与 tooltip——
        含动态数值的整串不进词表，否则英文界面必漏译。
        """
        active = sum(1 for r in self._rows.values()
                     if r.state in ("排队中", "下载中"))
        failed = sum(1 for r in self._rows.values() if r.state == "失败")
        self.lb_head.setText(T("下载任务"))
        self.lb_head.setStyleSheet(
            "color:%s; font-size:12px; font-weight:600;"
            % (_C_ERR if failed else _C_TEXT))
        self._head.setVisible(bool(self._rows))
        self.b_clear.setVisible(bool(self._rows))
        self.setText(f"⬇ {active}" if active else ("⬇ !" if failed else "⬇ 0"))
        # tooltip 是动态拼接（含计数），按固定词逐段翻译，避免英文模式留中文
        tip = f"{T('下载任务')} · {T('进行中')} {active}"
        if failed:
            tip += f" · {T('失败')} {failed}"
        self.setToolTip(tip)
        self._fit_scroll()
