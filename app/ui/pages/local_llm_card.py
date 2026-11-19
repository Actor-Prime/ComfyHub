# -*- coding: utf-8 -*-
"""本地模型卡片（可选组件 UI）

2026-11-15 用户需求：本地 LLM 做成「选装」，别占安装包体积、别默认吃性能。
本卡片负责三件事：
    1) 组件安装（llama-server，24 MB，sha256 校验）
    2) 模型下载（0.4~1.1 GB，hf-mirror 断点续传）
    3) 启停服务，并把「本地模型」填进上面的 AI 服务商三项

状态机不做显式枚举——每次 refresh() 按磁盘现状重算，避免状态不同步
（图标/进度这类"先显示再刷新"的坑在 2026-11-08 踩过）。
"""
import os

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from app import local_llm
from app.i18n import T
from app.ui import theme


def _mb(n: int) -> str:
    return "%.1f MB" % (n / 1048576.0) if n < 1073741824 else "%.2f GB" % (n / 1073741824.0)


def pending_model_switch(want: str, loaded: str, running: bool,
                         want_ready: bool) -> bool:
    """运行中「下拉选的模型」是否与「正在加载的模型」不一致（= 待切换）

    抽成纯函数是为了能单测——它决定按钮是「停止本地服务」还是「应用并重启」，
    判错会让用户点一下就把服务停了却没重启，或者以为切了其实没切。

    2026-11-19 用户反馈（客户疑问）：「启动后能直接切模型吗？还是要先保存再重启？」
    原实现里切下拉只 refresh()，服务照跑旧模型、界面也无任何提示，很容易误解。
    现在：有差异且新模型已就绪 → 按钮变「应用并重启」，一次点击完成停+启。
    新模型没下载则不算待切换（重启也没用），由界面另外提示先下载。
    """
    if not running:
        return False
    w = str(want or "").strip()
    c = str(loaded or "").strip()
    if not w or not c or w == c:
        return False
    return bool(want_ready)


def _backend_hint(vision=None) -> str:
    """说明文案：必须按本机实际会走的后端写，否则是误导（2026-11-19）

    老文案一律写「纯 CPU 运行，不占用显存」，但 AMD / Intel 机器现在会装
    Vulkan 包跑在显卡上——继续写「纯 CPU」就是骗人。核显又必须说清它仍走
    CPU 推理（-ngl 0），否则用户会以为装完就变快。

    **刻意写成整句分支，不用字符串拼接**：i18n 是查表翻译，把两段 T()
    拼起来的整句在英文包里查不到，会漏译（tests/i18n_leftover.py 直接报）。
    vision 传 None 表示还没选模型（此时不追加看图说明）。
    """
    backend = local_llm.detect_backend()
    igpu = False
    if backend == "vulkan":
        try:
            from app.bootstrap import detect_gpu
            igpu = bool(getattr(detect_gpu(), "is_igpu", False))
        except Exception:
            igpu = False

    if vision is None:
        if backend != "vulkan":
            return T("可选组件：不进安装包，按需下载。纯 CPU 运行，"
                     "不占用显存，出图时互不影响。")
        if igpu:
            return T("可选组件：不进安装包，按需下载。检测到核显，按 CPU 推理运行"
                     "（核显 GPU 卸载实测更慢，不启用）。")
        return T("可选组件：不进安装包，按需下载。将用 Vulkan 后端在显卡上推理，"
                 "占用少量显存；若与出图冲突可切回 CPU。")

    if backend != "vulkan":
        return (T("启动后为纯 CPU 推理，不占用显存；可看图、可调用工具。")
                if vision else
                T("启动后为纯 CPU 推理，不占用显存；该模型只能对话与调工具，不能看图。"))
    if igpu:
        return (T("启动后按 CPU 推理（核显不启用 GPU 卸载）；可看图、可调用工具。")
                if vision else
                T("启动后按 CPU 推理（核显不启用 GPU 卸载）；该模型只能对话与调工具，"
                  "不能看图。"))
    return (T("启动后用 Vulkan 后端在显卡上推理；可看图、可调用工具。")
            if vision else
            T("启动后用 Vulkan 后端在显卡上推理；该模型只能对话与调工具，不能看图。"))


class _FetchWorker(QThread):
    """下载二进制或模型（下载器本身是阻塞的，必须离开 UI 线程）"""

    sig_done = Signal(bool, str)

    def __init__(self, kind: str, mid: str = "", parent=None):
        super().__init__(parent)
        self._kind = kind
        self._mid = mid

    def run(self):
        try:
            if self._kind == "bin":
                ok = local_llm.download_binary(log=lambda *_: None)
                self.sig_done.emit(ok, "" if ok else "组件下载失败，检查网络后重试")
            else:
                ok = local_llm.download_model(self._mid, log=lambda *_: None)
                self.sig_done.emit(ok, "" if ok else "模型下载失败，检查网络后重试")
        except Exception as e:                     # 线程里异常必须自己咽掉
            self.sig_done.emit(False, str(e))


class _ServerWorker(QThread):
    """启动要等 /health 就绪（最多 90s），同样不能占 UI 线程"""

    sig_done = Signal(bool, str)

    def __init__(self, mid: str, port: int = 0, ctx: int = 0, parent=None):
        super().__init__(parent)
        self._mid = mid
        self._port = port
        self._ctx = ctx

    def run(self):
        try:
            ok = local_llm.SERVER.start(self._mid,
                                        ctx=self._ctx or local_llm.DEFAULT_CTX,
                                        port=self._port, log=lambda *_: None)
            # 文案必须诚实：这里最常见的失败是进程崩溃退出，而不是"端口被占"
            # （端口冲突时组件自己会顺延，压根不会走到这里）。确切原因看控制台日志。
            self.sig_done.emit(ok, "" if ok else "启动失败：进程异常退出，详情见「控制台」日志")
        except Exception as e:
            self.sig_done.emit(False, str(e))


class _ProbeWorker(QThread):
    """后台读 /props（会发 HTTP，最多 2.5s，绝不能放 UI 线程）

    用途：程序重启后服务还在跑、或 info 因异常为空时，补一次「实际加载了什么」。
    """

    sig_done = Signal()

    def run(self):
        try:
            local_llm.SERVER.info = local_llm.SERVER.loaded()
        except Exception:
            pass
        self.sig_done.emit()


class LocalLlmCard(QGroupBox):
    """本地模型（可选组件）"""

    sig_apply = Signal(str, str, str)     # provider_id, base_url, model_id

    def __init__(self, cfg: dict, parent=None):
        super().__init__(T("本地模型（可选组件）"), parent)
        self.cfg = cfg
        self._worker = None
        self._kind = ""
        self._mid_dl = ""
        self._probe = None                # 后台读 /props 的线程
        self._probe_tried = 0             # 连续失败上限，防死循环探测
        self._pending = False             # 运行中改了模型选择、尚未重启生效
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # ---- 第一行：状态 + 主操作 ----
        row = QHBoxLayout()
        row.setSpacing(8)
        self.lb_state = QLabel("—")
        row.addWidget(self.lb_state)
        # 动态值（体积/端口/地址）单独成控件：i18n 是「控件文本精确匹配词表」，
        # 把数字拼进句子会让键每次都变，英文模式下永远退回中文（项目 i18n 铁律）
        self.lb_detail = QLabel("")
        self.lb_detail.setWordWrap(True)
        self.lb_detail.setStyleSheet("color:%s; font-size:11px;" % theme.fg("#6c7a8c"))
        row.addWidget(self.lb_detail, 1)
        self.b_install = QPushButton(T("安装组件"))
        self.b_install.clicked.connect(self._on_install)
        row.addWidget(self.b_install)
        v.addLayout(row)

        # ---- 当前加载（2026-11-16 用户需求：启动后要能看出「实际在用哪个模型」）----
        # 数据来源是 llama-server 的 /props 自报，不是我们启动时传的参数——
        # 下拉框可以随便切，只有这份代表「正在跑的那个」。
        self.w_loaded = QWidget()
        row_l = QHBoxLayout(self.w_loaded)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(6)
        self.lb_loaded_cap = QLabel(T("当前加载"))
        self.lb_loaded_cap.setStyleSheet(
            "color:%s; font-size:11px;" % theme.fg("#6c7a8c"))
        row_l.addWidget(self.lb_loaded_cap)
        self.lb_loaded = QLabel("")
        self.lb_loaded.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row_l.addWidget(self.lb_loaded)
        self.lb_loaded_meta = QLabel("")
        self.lb_loaded_meta.setStyleSheet(
            "color:%s; font-size:11px;" % theme.fg("#6c7a8c"))
        row_l.addWidget(self.lb_loaded_meta)
        row_l.addStretch(1)
        self.w_loaded.hide()
        v.addWidget(self.w_loaded)

        # ---- 进度条（下载中才显示）----
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setFixedHeight(16)
        self.bar.hide()
        v.addWidget(self.bar)

        # ---- 第二行：模型选择 + 下载 ----
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.cb_model = QComboBox()
        for m in local_llm.MODELS:
            self.cb_model.addItem(T(m["name"]), m["id"])
            i = self.cb_model.count() - 1
            self.cb_model.setItemData(
                i, "%s · %s" % (m["note"], _mb(local_llm.model_total(m["id"]))),
                Qt.ToolTipRole)
        saved = str((cfg.get("settings") or {}).get("local_llm_model") or "")
        idx = self.cb_model.findData(saved) if saved else -1
        if idx < 0:
            # 2026-11-16：清单变化后 saved 可能指向已移除的模型。以前一律回落到
            # 列表第一项（还要再下 1.1 GB），现在优先选磁盘上已经有的那个。
            idx = next((i for i in range(self.cb_model.count())
                        if local_llm.has_model(self.cb_model.itemData(i))), 0)
        self.cb_model.setCurrentIndex(idx)
        self.cb_model.currentIndexChanged.connect(lambda *_: self.refresh())
        row2.addWidget(self.cb_model, 1)
        self.b_model = QPushButton(T("下载模型"))
        self.b_model.clicked.connect(self._on_download_model)
        row2.addWidget(self.b_model)
        v.addLayout(row2)

        # ---- 第三行：启停 + 移除 ----
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.b_server = QPushButton(T("启动本地服务"))
        self.b_server.clicked.connect(self._on_toggle_server)
        row3.addWidget(self.b_server)
        self.b_apply = QPushButton(T("设为当前 AI 服务商"))
        self.b_apply.clicked.connect(self._on_apply)
        self.b_apply.hide()
        row3.addWidget(self.b_apply)
        row3.addStretch(1)
        # 清单换过之后，旧模型文件会留在 models 目录里白占空间（0.6B 那份 378 MB）。
        # 有孤儿才显示，点一下清掉。
        self.b_orphan = QPushButton(T("清理残留模型"))
        self.b_orphan.setToolTip(T("删除已不在模型清单里的权重文件"))
        self.b_orphan.clicked.connect(self._on_cleanup_orphans)
        row3.addWidget(self.b_orphan)
        self.b_remove = QPushButton(T("移除组件"))
        self.b_remove.clicked.connect(self._on_remove)
        row3.addWidget(self.b_remove)
        v.addLayout(row3)

        self.lb_hint = QLabel("")
        self.lb_hint.setWordWrap(True)
        self.lb_hint.setStyleSheet("color:%s; font-size:11px;" % theme.fg("#6c7a8c"))
        v.addWidget(self.lb_hint)

        self.refresh()

    # ------------------------------------------------------------ 状态渲染
    def _render_loaded(self, running: bool):
        """「当前加载」那一行：模型名 + 上下文 + 量化类型

        数据来自 SERVER.info（服务端 /props 自报）。拿不到就先写「读取中…」
        并在**后台线程**补一次——这个请求最长 2.5 秒，放 UI 线程会卡界面。
        """
        if not running:
            self.w_loaded.hide()
            self._probe_tried = 0
            return
        info = local_llm.SERVER.info or {}
        self.w_loaded.show()
        if not info:
            self.lb_loaded.setText(
                T("读取中…") if self._probe_tried < 3 else T("（服务无响应）"))
            self.lb_loaded_meta.setText("")
            self._probe_async()
            return
        self.lb_loaded.setText(str(info.get("name") or T("（未知模型）")))
        bits = []
        if info.get("ctx"):
            bits.append(T("上下文 %s") % info["ctx"])
        if info.get("ftype"):
            bits.append(str(info["ftype"]))
        if info.get("vision"):
            bits.append(T("可看图"))
        self.lb_loaded_meta.setText(" · ".join(bits))
        self.lb_loaded.setToolTip(str(info.get("path") or ""))

    def _probe_async(self):
        if self._probe is not None or self._probe_tried >= 3:
            return
        self._probe_tried += 1
        self._probe = _ProbeWorker(self)
        self._probe.sig_done.connect(self._on_probe_done)
        self._probe.start()

    def _on_probe_done(self):
        if self._probe is not None:
            self._probe.wait(1000)
            self._probe = None
        self.refresh()

    def refresh(self):
        installed = local_llm.is_installed()
        running = local_llm.SERVER.running
        # 两个模型身份必须分开（2026-11-19）：
        #   loaded_mid = 正在跑的（「当前加载」行以此为准，绝不跟着下拉变，否则骗人）
        #   sel_mid    = 下拉选中的（「下载模型」按钮与重启目标看它）
        # 以前只有一个 mid（运行中就等于 loaded），于是运行中切到「尚未下载」的模型时，
        # 下载按钮仍显示「模型已就绪」且点不动——客户那个困惑的直接来源。
        loaded_mid = ((local_llm.SERVER.model_id
                       or (local_llm.SERVER.info or {}).get("model_id"))
                      if running else "") or ""
        sel_mid = self.cb_model.currentData() or ""
        sel_ok = installed and bool(sel_mid) and local_llm.has_model(sel_mid)
        # 待切换：运行中且下拉与当前加载不一致，且新模型已就绪
        pending = pending_model_switch(sel_mid, loaded_mid, running, sel_ok)
        self._pending = pending
        busy = self._worker is not None
        self._render_loaded(running)

        # 只剩一个模型时（2026-11-16 起清单里只有 VL-2B）别让下拉可点：
        # 只有一项还弹出列表，用户会以为漏加载了。文本仍可见，只是选不动。
        self.cb_model.setEnabled(installed and not busy
                                 and self.cb_model.count() > 1)
        self.b_install.setVisible(not installed)
        self.b_install.setEnabled(not busy)
        # 下载按钮按「下拉选中的」算：运行中也能为下一次切换提前下载
        self.b_model.setVisible(installed)
        self.b_model.setEnabled(installed and not sel_ok and not busy)
        self.b_model.setText(T("模型已就绪") if sel_ok else T("下载模型"))
        self.b_server.setVisible(installed)
        self.b_server.setEnabled((sel_ok and not busy) or running)
        # 运行中切了模型 → 一键「应用并重启」，用户不用自己按「停止」再「启动」
        self.b_server.setText(
            T("应用并重启（约 15 秒）") if pending else
            (T("停止本地服务") if running else T("启动本地服务")))
        self.b_apply.setVisible(running)
        self.b_remove.setVisible(installed and not busy)
        self.b_remove.setEnabled(not running and not busy)
        orph = local_llm.orphans() if installed else []
        self.b_orphan.setVisible(bool(orph) and not busy)
        self.b_orphan.setToolTip(
            T("删除已不在模型清单里的权重文件：%s")
            % "、".join("%s（%s）" % (o["file"], _mb(o["size"])) for o in orph)
            if orph else "")

        if not installed:
            self.lb_state.setText(T("未安装"))
            self.lb_detail.setText("%s + 0.4~1.1 GB"
                                   % _mb(local_llm.active_bin()["size"]))
            self.lb_hint.setText(_backend_hint())
        elif running:
            # 运行中优先展示运行态：否则「切到未下载的模型」会被下面的
            # 「组件已就绪」分支盖掉，用户看不到服务其实还在跑。
            self.lb_state.setText(T("本地服务运行中"))
            # 地址后面补上「实际跑在哪」——GPU 名来自启动日志，比探测值可信
            gpu = getattr(local_llm.SERVER, "gpu_name", "")
            self.lb_detail.setText("%s  ·  %s"
                                   % (local_llm.SERVER.base_url.replace("/v1", ""),
                                      gpu or T("CPU 推理")))
            info = local_llm.SERVER.info or {}
            vision = bool(info.get("vision")) or bool(
                local_llm.get_model(loaded_mid or sel_mid).get("vision"))
            ctx_now = int(info.get("ctx") or 0)
            if pending:
                # 用户切了下拉但还没重启：这是本轮新增的最关键提示。
                # 注意说「上方」不是「右侧」——按钮在下拉框下面一行，提示在最底部。
                self.lb_hint.setText(
                    T("已选择「%s」，点上方「应用并重启」生效（当前仍在用「%s」）。")
                    % (local_llm.get_model(sel_mid)["name"],
                       local_llm.get_model(loaded_mid)["name"]))
            elif sel_mid and not sel_ok:
                # 切到了还没下载的模型：重启也没用，得先下载
                self.lb_hint.setText(
                    T("已选择「%s」，但它还没下载，先点「下载模型」再重启服务。")
                    % local_llm.get_model(sel_mid)["name"])
            elif ctx_now and ctx_now < 8000:
                # 内存不够时 start() 会自动退回小上下文，这时 400 会复发，
                # 必须让用户知道是「降级了」而不是「又出 bug 了」
                self.lb_hint.setText(
                    T("上下文只有 %s（内存不足已自动降级），长对话仍可能被拒（400）；"
                      "关掉其他程序后重启服务可恢复 8192。") % ctx_now)
            else:
                self.lb_hint.setText(
                    T("点「设为当前 AI 服务商」把地址填进上方表单，再点页面底部保存。")
                    if vision else
                    T("当前模型不支持看图，附图片会被忽略；需要看截图请换 Qwen3-VL-2B。"))
        elif not sel_ok:
            self.lb_state.setText(T("组件已就绪"))
            self.lb_detail.setText("%s" % _mb(local_llm.model_total(sel_mid)))
            self.lb_hint.setText(T("请选择并下载一个模型，模型从 hf-mirror 下载，支持断点续传。"))
        else:
            self.lb_state.setText(T("配置完成 · 未启动"))
            self.lb_detail.setText("127.0.0.1:%d" % local_llm.DEFAULT_PORT)
            vision = bool(local_llm.get_model(sel_mid).get("vision"))
            self.lb_hint.setText(_backend_hint(vision))

    # ------------------------------------------------------------ 操作
    def _start_fetch(self, kind: str, mid: str = ""):
        if self._worker is not None:
            return
        self._kind = kind
        self._mid_dl = mid
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.show()
        self.bar.setFormat(T("下载中 %p%"))
        self._worker = _FetchWorker(kind, mid, self)
        self._worker.sig_done.connect(self._on_fetch_done)
        self._worker.start()
        self._timer.start()
        self.refresh()

    def _on_install(self):
        self._start_fetch("bin")

    def _on_download_model(self):
        self._start_fetch("model", self.cb_model.currentData())

    def _on_fetch_done(self, ok: bool, msg: str):
        self._timer.stop()
        self.bar.hide()
        self._worker.wait(1000) if self._worker else None
        self._worker = None
        self._kind = ""
        self._mid_dl = ""
        self.refresh()
        if not ok:
            self.lb_hint.setText("⚠ " + (msg or T("下载失败")))

    def _tick(self):
        """进度条：轮询 .part 体积（下载器没有回调，轮询最省事也最稳）"""
        if self._kind == "bin":
            total = int(local_llm.active_bin()["size"])
            got = local_llm.progress_of(local_llm.bin_pkg_path())
        elif self._kind == "model" and self._mid_dl:
            # 视觉模型是「权重 + mmproj」两个文件，进度按合计算
            total = local_llm.model_total(self._mid_dl)
            got = local_llm.model_progress(self._mid_dl)
        else:
            return
        if total > 0:
            self.bar.setValue(min(1000, int(got * 1000 / total)))
            self.bar.setFormat("%s / %s  (%%p%%)" % (_mb(got), _mb(total)))

    def _on_toggle_server(self):
        # 运行中且下拉换了模型 → 一键「应用并重启」：停掉旧模型后继续走启动流程。
        # 只有「没换模型」时才是纯粹停止。
        if local_llm.SERVER.running:
            if not getattr(self, "_pending", False):
                local_llm.SERVER.stop()
                self.refresh()
                return
            local_llm.SERVER.stop()
            self.refresh()
        mid = self.cb_model.currentData()
        self.b_server.setEnabled(False)
        self.lb_state.setText(T("正在启动（首次加载模型约 10~30 秒）…"))
        # 上下文长度可配（settings.local_llm_ctx）；默认 8192 是为了不再撞
        # 「4605 tokens exceeds 4096」这个 400。内存不够会自动降到 4096。
        try:
            ctx = int((self.cfg.get("settings") or {}).get("local_llm_ctx")
                      or local_llm.DEFAULT_CTX)
        except (TypeError, ValueError):
            ctx = local_llm.DEFAULT_CTX
        self._probe_tried = 0
        self._worker = _ServerWorker(mid, 0, ctx, self)
        self._worker.sig_done.connect(self._on_server_done)
        self._worker.start()

    def _on_server_done(self, ok: bool, msg: str):
        if self._worker:
            self._worker.wait(1000)
        self._worker = None
        self.refresh()
        if not ok:
            self.lb_hint.setText("⚠ " + (msg or T("启动失败")))
            return
        self._on_apply()

    def _on_apply(self):
        mid = local_llm.SERVER.model_id or (self.cb_model.currentData() or "")
        self.sig_apply.emit("local", local_llm.SERVER.base_url, mid)

    def _on_cleanup_orphans(self):
        from PySide6.QtWidgets import QMessageBox
        orph = local_llm.orphans()
        if not orph:
            self.refresh()
            return
        total = sum(int(o.get("size") or 0) for o in orph)
        listing = "\n".join("· %s（%s）" % (o["file"], _mb(o["size"])) for o in orph)
        r = QMessageBox.question(
            self, "ComfyHub",
            T("这些权重已不在模型清单里，删掉可释放 %s：\n\n%s\n\n确认删除？")
            % (_mb(total), listing),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        n, freed = local_llm.remove_orphans()
        self.refresh()
        self.lb_hint.setText(T("已清理 %d 个文件，释放 %s") % (n, _mb(freed)))

    def _on_remove(self):
        from PySide6.QtWidgets import QMessageBox
        sz = local_llm.dir_size(local_llm.install_root())
        r = QMessageBox.question(
            self, "ComfyHub",
            T("确定删除本地模型组件？将释放约 %s，已下载的模型会一并删除。") % _mb(sz),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        local_llm.SERVER.stop()
        local_llm.remove_installed()
        self.refresh()

    # ------------------------------------------------------------ 生命周期
    def stop_server(self):
        local_llm.SERVER.stop()

    def shutdown(self):
        """页面销毁/程序退出：停服务、停线程、停定时器"""
        self._timer.stop()
        if self._worker is not None:
            self._worker.wait(2000)
            self._worker = None
        if self._probe is not None:
            self._probe.wait(3000)
            self._probe = None
        local_llm.SERVER.stop()
