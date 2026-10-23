# -*- coding: utf-8 -*-
"""ComfyUI 编辑页：分区表单（基础/高级设置）+ 启停 + 保存/删除（对标截图2/3）"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSpinBox, QCheckBox, QFileDialog, QMessageBox, QFrame,
    QScrollArea, QGroupBox,
)

from app.config import VRAM_MODES, save_config
from app.log import log_exc
from app.ui.nav import NAV
from app.workers import BgWorker, RmWorker, swap_worker, wait_all
from app.process import build_command, port_in_use


class _Row(QFrame):
    """行式设置项：左标题+说明（2），右控件（3，宽度优先给控件）"""
    def __init__(self, title, desc, widget):
        super().__init__()
        self.setObjectName("RowCard")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 7, 14, 7)
        h.setSpacing(12)
        left = QVBoxLayout()
        t = QLabel(title);  t.setObjectName("RowTitle")
        d = QLabel(desc);   d.setObjectName("RowDesc")
        d.setWordWrap(True)
        left.addWidget(t)
        left.addWidget(d)
        h.addLayout(left, 2)
        if widget:
            widget.setMinimumWidth(380)
            if hasattr(widget, "setToolTip"):
                # 路径类控件：悬停看全量内容
                pass
            h.addWidget(widget, 3, Qt.AlignVCenter)




class _KernelWorker(QThread):
    """内核版本：拉标签 / 切换+装依赖（git 与 pip 都可能慢，必须后台）"""
    sig_log = Signal(str)
    sig_tags = Signal(list)
    sig_switch = Signal(bool, str)

    def __init__(self, km, action, tag="", python=""):
        super().__init__()
        self.km = km
        self.action = action
        self.tag = tag
        self.python = python

    def run(self):
        try:
            if self.action == "tags":
                self.sig_log.emit("[内核] 拉取版本标签…")
                tags = self.km.list_tags()
                self.sig_log.emit(f"[内核] 取到 {len(tags)} 个标签")
                self.sig_tags.emit(tags)
                return
            # switch
            self.sig_log.emit(f"[内核] 切换到 {self.tag} …")
            ok, msg = self.km.switch_to(self.tag)
            self.sig_log.emit(f"[内核] checkout: {'OK' if ok else '失败'} {msg[:200]}")
            if not ok:
                self.sig_switch.emit(False, msg)
                return
            if self.python:
                from app import bootstrap
                self.sig_log.emit("[内核] 安装该版本要求的依赖…")
                try:
                    bootstrap.install_comfyui_requirements(
                        self.python, self.km.path, log=self.sig_log.emit)
                    self.sig_log.emit("[内核] 依赖已同步")
                except Exception as e:
                    self.sig_log.emit(f"[内核] 依赖安装异常（可稍后手动装）: {e}")
            self.sig_switch.emit(True, msg)
        except Exception as e:
            self.sig_switch.emit(False, str(e))


class ComfyuiPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._build()
        self.load_current()

    # ---------- 构建 ----------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(18, 14, 18, 18)
        root.setSpacing(8)

        # ---- 快捷操作行 ----
        bar = QHBoxLayout()
        self.b_start = QPushButton("▶ 启动")
        self.b_start.setProperty("success", True)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("■ 停止")
        self.b_stop.clicked.connect(self.stop)
        self.b_web = QPushButton("🌐 打开网页")
        self.b_web.clicked.connect(self.open_browser)
        self.lb_state = QLabel("未运行")
        self.lb_state.setObjectName("StatusStop")
        bar.addWidget(self.b_start)
        bar.addWidget(self.b_stop)
        bar.addWidget(self.b_web)
        bar.addStretch()
        bar.addWidget(self.lb_state)
        root.addLayout(bar)

        # ---- 基础设置 ----
        lb1 = QLabel("基础设置")
        lb1.setObjectName("SectionLabel")
        root.addWidget(lb1)

        self.ed_name = QLineEdit()
        root.addWidget(_Row("整合包名称", "实例显示名称", self.ed_name))

        h_dir = QHBoxLayout()
        self.ed_path = QLineEdit();  self.ed_path.setReadOnly(True)
        b_dir = QPushButton("修改目录")
        b_dir.clicked.connect(self._pick_dir)
        h_dir.addWidget(self.ed_path, 1)
        h_dir.addWidget(b_dir)
        wrap1 = QWidget(); wrap1.setLayout(h_dir)
        root.addWidget(_Row("根目录", "包含 main.py 的 ComfyUI 目录", wrap1))

        h_models = QHBoxLayout()
        self.ed_models = QLineEdit(); self.ed_models.setReadOnly(True)
        b_models = QPushButton("修改目录")
        b_models.clicked.connect(self._pick_models)
        h_models.addWidget(self.ed_models, 1)
        h_models.addWidget(b_models)
        wrap2 = QWidget(); wrap2.setLayout(h_models)
        root.addWidget(_Row("模型目录", "checkpoints/loras/vae 等模型的存放位置", wrap2))

        h_py = QHBoxLayout()
        self.ed_python = QLineEdit()
        b_py = QPushButton("修改目录")
        b_py.clicked.connect(self._pick_python)
        h_py.addWidget(self.ed_python, 1)
        h_py.addWidget(b_py)
        wrap3 = QWidget(); wrap3.setLayout(h_py)
        root.addWidget(_Row("Python 路径", "实例配套的解释器（建议用独立 venv）", wrap3))

        # ---- 内核版本 ----
        lb_k = QLabel("内核版本")
        lb_k.setObjectName("SectionLabel")
        root.addWidget(lb_k)

        h_k = QHBoxLayout()
        self.lb_kver = QLabel("…")
        self.lb_kver.setStyleSheet("color:#e8eef7;")
        self.cb_ktag = QComboBox()
        self.cb_ktag.setMinimumWidth(150)
        b_krefresh = QPushButton("刷新版本")
        b_krefresh.clicked.connect(self._refresh_tags)
        b_kswitch = QPushButton("切换并装依赖")
        b_kswitch.setProperty("accent", True)
        b_kswitch.clicked.connect(self._switch_kernel)
        h_k.addWidget(self.lb_kver, 1)
        h_k.addWidget(QLabel("目标:"))
        h_k.addWidget(self.cb_ktag)
        h_k.addWidget(b_krefresh)
        h_k.addWidget(b_kswitch)
        wrap_k = QWidget(); wrap_k.setLayout(h_k)
        root.addWidget(_Row("版本切换", "按 git tag 切换内核（如回退到旧版救火），切换后自动安装配套依赖", wrap_k))

        # ---- 高级设置 ----
        lb2 = QLabel("高级设置")
        lb2.setObjectName("SectionLabel")
        root.addWidget(lb2)

        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        root.addWidget(_Row("启动端口", "用于访问 ComfyUI 的 HTTP 端口（默认 8188）", self.sp_port))

        # 生成设备：GPU/CPU 切换（objectName 驱动选中样式）
        dev = QWidget()
        dv = QHBoxLayout(dev); dv.setContentsMargins(0, 0, 0, 0)
        self.b_gpu = QPushButton("GPU (默认)")
        self.b_gpu.setObjectName("DeviceBtn")
        self.b_gpu.setCheckable(True); self.b_gpu.setChecked(True)
        self.b_cpu = QPushButton("CPU")
        self.b_cpu.setObjectName("DeviceBtn")
        self.b_cpu.setCheckable(True)
        self.b_gpu.clicked.connect(lambda: self._set_device(True))
        self.b_cpu.clicked.connect(lambda: self._set_device(False))
        dv.addWidget(self.b_gpu)
        dv.addWidget(self.b_cpu)
        root.addWidget(_Row("生成设备", "GPU 使用显卡加速 / CPU 仅调试用", dev))
        self._use_gpu = True

        self.cb_vram = QComboBox()
        self.cb_vram.addItems(VRAM_MODES.keys())
        root.addWidget(_Row("CPU/显存优化", "按显存大小选择优化级别（低配选低显存）", self.cb_vram))

        self.cb_ca = QComboBox()
        self.cb_ca.addItems(["自动", "Quad（省显存）", "Split（更快）", "PyTorch SDP"])
        root.addWidget(_Row("Cross Attention 优化", "注意力算法：省显存 / 提速 / PyTorch 原生，出问题回自动", self.cb_ca))

        self.cb_preview = QComboBox()
        self.cb_preview.addItems(["无", "latent2rgb（快）", "TAESD（精细）"])
        root.addWidget(_Row("采样预览", "出图过程中的实时预览画质（TAESD 更清晰但略耗性能）", self.cb_preview))

        self.ck_fp16 = QCheckBox("开启")
        root.addWidget(_Row("强制 FP16", "半精度运行，低显存显卡提速（画质略降）", self.ck_fp16))

        self.ck_fast = QCheckBox("开启")
        root.addWidget(_Row("快速模式", "启用 PyTorch 加速技巧（新显卡提速明显，老卡慎用）", self.ck_fast))

        self.ck_nosm = QCheckBox("开启")
        root.addWidget(_Row("禁用智能显存", "显存紧张/爆显存时开启，稍降速度换稳定", self.ck_nosm))

        self.ck_lan = QCheckBox("开启")
        root.addWidget(_Row("局域网访问", "允许同一局域网其他设备（手机/平板）打开 ComfyUI", self.ck_lan))

        self.ck_browser = QCheckBox("开启")
        root.addWidget(_Row("自动打开", "启动成功后自动用浏览器打开 ComfyUI 界面", self.ck_browser))

        self.ed_extra = QLineEdit()
        self.ed_extra.setPlaceholderText("例如: --reserve-vram 2")
        root.addWidget(_Row("启动参数", "附加的命令行参数（逗号分隔的高阶用法）", self.ed_extra))

        root.addSpacing(6)
        b_save = QPushButton("保存设置")
        b_save.setObjectName("SaveBtn")
        b_save.clicked.connect(self._save)
        root.addWidget(b_save)

        self.b_del = QPushButton("🗑 删除此实例（可选仅移出管理 / 彻底删除文件）")
        self.b_del.setObjectName("DeleteBtn")
        self.b_del.clicked.connect(self._delete)
        root.addWidget(self.b_del)

    # ---------- 数据 ----------
    def load_current(self):
        inst = self.mainwin.current_inst()
        self.b_del.setEnabled(inst is not None)
        if not inst:
            for w in (self.ed_name, self.ed_path, self.ed_models, self.ed_python):
                w.clear()
            return
        self.ed_name.setText(inst.get("name", ""))
        self.ed_path.setText(inst.get("comfyui_path", ""))
        # 模型目录：优先读实例配置（持久化），无则默认 comfyui_path/models
        self.ed_models.setText(inst.get("models_dir")
                               or os.path.join(inst["comfyui_path"], "models"))
        self.ed_python.setText(inst.get("python_path", ""))
        # 路径框：光标移到末尾（显示尾部目录名），tooltip 存完整路径
        for ed in (self.ed_path, self.ed_models, self.ed_python):
            ed.setToolTip(ed.text())
            ed.setCursorPosition(len(ed.text()))
        self.sp_port.setValue(int(inst.get("port") or 8188))
        self.cb_vram.setCurrentText(inst.get("vram_mode", "自动"))
        self.cb_ca.setCurrentText(inst.get("cross_attn", "自动"))
        self.cb_preview.setCurrentText(inst.get("preview_method", "无"))
        self.ck_fp16.setChecked(bool(inst.get("force_fp16")))
        self.ck_fast.setChecked(bool(inst.get("fast_mode")))
        self.ck_nosm.setChecked(bool(inst.get("no_smart_memory")))
        self.ck_lan.setChecked(bool(inst.get("lan_access")))
        self.ck_browser.setChecked(bool(inst.get("auto_open_browser", True)))
        self.ed_extra.setText(inst.get("extra_args", ""))
        self._use_gpu = inst.get("device", "gpu") != "cpu"
        self._sync_device_buttons()
        self._refresh_kernel_info()
        self._update_state()

    # ---------- 内核版本 ----------
    def _km(self):
        from app.tools import KernelManager
        inst = self.mainwin.current_inst()
        return KernelManager(inst["comfyui_path"]) if inst else None

    def _refresh_kernel_info(self):
        inst = self.mainwin.current_inst()
        if not inst or not hasattr(self, "lb_kver"):
            return
        try:
            km = self._km()
            ver = km.current_version() or "未知"
            if km.is_git_repo:
                self.lb_kver.setText(f"当前：{ver}　·　git 仓库 ✓")
                self.lb_kver.setStyleSheet("color:#4cd47c;")
            else:
                self.lb_kver.setText(f"当前：{ver}　·　非 git 仓库（无法切换版本）")
                self.lb_kver.setStyleSheet("color:#e8a33d;")
        except Exception as e:
            self.lb_kver.setText(f"读取版本失败：{e}")
            self.lb_kver.setStyleSheet("color:#e8635a;")

    def _refresh_tags(self):
        inst = self.mainwin.current_inst()
        if not inst:
            return
        km = self._km()
        if not km or not km.is_git_repo:
            QMessageBox.information(self, "内核版本",
                                    "该实例不是 git 仓库（可能是 zip 安装），无法按标签切换版本。")
            return
        if getattr(self, "_kw", None) and self._kw.isRunning():
            return
        self.lb_kver.setText("正在拉取版本标签…")
        self._kw = _KernelWorker(km, "tags")
        self._kw.sig_log.connect(self.mainwin.console_page.append_line)
        self._kw.sig_tags.connect(self._on_tags)
        self._kw.start()

    def _on_tags(self, tags):
        self.cb_ktag.clear()
        if tags:
            self.cb_ktag.addItems(tags)
            self._refresh_kernel_info()
            self.lb_kver.setText(self.lb_kver.text() + f"　·　{len(tags)} 个版本可选")
        else:
            self.lb_kver.setText("未取到任何版本标签（检查网络 / 代理设置）")

    def _switch_kernel(self):
        inst = self.mainwin.current_inst()
        tag = self.cb_ktag.currentText().strip()
        if not inst or not tag:
            QMessageBox.information(self, "内核版本", "请先「刷新版本」并选择目标版本")
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "内核版本", "请先停止该实例再切换内核版本")
            return
        ans = QMessageBox.question(
            self, "切换内核",
            f"将把内核切换到 {tag}，并自动安装该版本要求的依赖。\n"
            "当前未提交的改动会被 git stash 暂存。继续？")
        if ans != QMessageBox.Yes:
            return
        if getattr(self, "_kw", None) and self._kw.isRunning():
            return
        self._kw = _KernelWorker(self._km(), "switch", tag, inst.get("python_path") or "")
        self._kw.sig_log.connect(self.mainwin.console_page.append_line)
        self._kw.sig_switch.connect(self._on_switched)
        self._kw.start()
        self.lb_kver.setText(f"正在切换到 {tag} …（含依赖安装，可能需要几分钟）")

    def _on_switched(self, ok, msg):
        self._refresh_kernel_info()
        if ok:
            QMessageBox.information(self, "切换完成", "内核版本已切换，依赖已同步。\n请重新启动该实例。")
        else:
            QMessageBox.warning(self, "切换失败", msg[:400])

    def on_show(self):
        self.load_current()

    def on_instance_changed(self):
        self.load_current()

    def _set_device(self, gpu):
        self._use_gpu = gpu
        self._sync_device_buttons()
        # CPU 模式下显存优化无意义，置灰防语义打架
        self.cb_vram.setEnabled(gpu)

    def _sync_device_buttons(self):
        self.b_gpu.setChecked(self._use_gpu)
        self.b_cpu.setChecked(not self._use_gpu)

    def _update_state(self):
        inst = self.mainwin.current_inst()
        running = bool(inst and self.mainwin.procs.is_running(inst))
        self.b_start.setEnabled(bool(inst) and not running)
        self.b_stop.setEnabled(running)
        self.lb_state.setText("● 运行中" if running else "未运行")
        self.lb_state.setObjectName("StatusRun" if running else "StatusStop")
        # QSS objectName 变更后需重刷样式
        self.lb_state.style().unpolish(self.lb_state)
        self.lb_state.style().polish(self.lb_state)

    # ---------- 动作 ----------
    def _current_inst(self):
        return self.mainwin.current_inst()

    def _save(self):
        inst = self._current_inst()
        if not inst:
            return
        inst["name"] = self.ed_name.text().strip() or inst["name"]
        inst["models_dir"] = self.ed_models.text().strip()
        inst["python_path"] = self.ed_python.text().strip()
        inst["port"] = self.sp_port.value()
        inst["vram_mode"] = self.cb_vram.currentText()
        inst["cross_attn"] = self.cb_ca.currentText()
        inst["preview_method"] = self.cb_preview.currentText()
        inst["force_fp16"] = self.ck_fp16.isChecked()
        inst["fast_mode"] = self.ck_fast.isChecked()
        inst["no_smart_memory"] = self.ck_nosm.isChecked()
        inst["lan_access"] = self.ck_lan.isChecked()
        inst["auto_open_browser"] = self.ck_browser.isChecked()
        inst["extra_args"] = self.ed_extra.text().strip()
        inst["device"] = "gpu" if self._use_gpu else "cpu"
        save_config(self.cfg)
        self.mainwin._sync_instances()
        QMessageBox.information(self, "ComfyHub", "✅ 设置已保存")

    def _delete(self):
        inst = self._current_inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub",
                                    "当前没有可删除的实例。\n"
                                    "点右上角「＋ 添加 ComfyUI」先添加一个。")
            return
        path = inst.get("comfyui_path", "")
        box = QMessageBox(self)
        box.setWindowTitle("删除实例")
        box.setText(f"实例「{inst.get('name')}」\n根目录：{path}\n\n选择删除方式：")
        b_keep = box.addButton("仅移出管理", QMessageBox.AcceptRole)
        b_del = box.addButton("彻底删除（含磁盘文件）", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_keep:
            self.mainwin.procs.get(inst).stop()
            self.cfg["instances"].remove(inst)
            save_config(self.cfg)
            self.mainwin._sync_instances()
            self.load_current()
        elif clicked is b_del:
            self._delete_files(inst)

    def _swap_worker(self, attr, worker, timeout=60000):
        """替换 worker：旧线程断开信号后放入墓碑（持引用防 GC，后台自然结束）。
        绝不 wait 阻塞 UI——上一版用 wait 导致界面卡死在检测中"""
        old_w = getattr(self, attr, None)
        if old_w is not None and old_w.isRunning():
            for sig_name in ("sig_done", "sig_log", "sig_progress", "sig_prog"):
                sig = getattr(old_w, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except Exception:
                        pass
            self._graveyard = [w for w in getattr(self, "_graveyard", [])
                               if w.isRunning()]
            self._graveyard.append(old_w)
        setattr(self, attr, worker)


    def closeEvent(self, event):
        wait_all(self, ("_del_worker", "_rm_worker"), 30000)
        event.accept()

    def _delete_files(self, inst):
        """彻底删除（全异步，不卡 UI）：
        停进程 → 后台算体积 → 确认框 → 后台删除 → 移出管理"""
        import shutil
        path = inst.get("comfyui_path", "")
        if not os.path.isdir(path):
            QMessageBox.warning(self, "ComfyHub", f"目录不存在：\n{path}")
            return
        self.mainwin.procs.get(inst).stop()
        self.b_del.setEnabled(False)

        def _size():
            total = 0
            for root, _d, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total

        self._del_inst = inst
        self._del_path = path
        self.b_del.setText("⏳ 正在计算目录体积…")
        swap_worker(self, "_del_worker", BgWorker(_size))
        self._del_worker.sig_done.connect(self._on_size_done)
        self._del_worker.start()

    def _on_size_done(self, size):
        inst, path = self._del_inst, self._del_path
        self.b_del.setEnabled(True)
        self.b_del.setText("🗑 删除此实例（可选仅移出管理 / 彻底删除文件）")
        size_gb = size / 1024 ** 3
        r = QMessageBox.warning(
            self, "⚠️ 彻底删除确认",
            f"将永久删除以下内容，不可恢复：\n\n"
            f"目录：{path}\n"
            f"体积：约 {size_gb:.1f} GB\n\n"
            f"注意：\n"
            f"· 便携 Python / MinGit 在共享 tools 目录，不受影响\n"
            f"· 自定义模型目录（models_dir）若指向本目录之外，不会被删除\n\n"
            f"确认要删除吗？（删除在后台进行，大目录需几分钟）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.b_del.setEnabled(False)
        self.b_del.setText("🗑 正在后台删除，请勿关闭软件…")

        def _collect():
            out = []
            for root, _d, files in os.walk(path):
                for f in files:
                    out.append(os.path.join(root, f))
            return out

        swap_worker(self, "_del_worker", BgWorker(_collect))
        try:
            self._del_worker.sig_done.disconnect()
        except Exception:
            pass
        self._del_worker.sig_done.connect(self._on_collect_done)
        self._del_worker.start()

    def _on_collect_done(self, files):
        from PySide6.QtWidgets import QProgressDialog
        files = files or []
        self._rm_prog = QProgressDialog(
            f"正在删除 0/{len(files)} 个文件…", None, 0, max(len(files), 1), self)
        self._rm_prog.setWindowTitle("彻底删除实例")
        self._rm_prog.setCancelButton(None)
        self._rm_prog.setWindowModality(Qt.WindowModal)
        self._rm_prog.setMinimumDuration(0)
        self._rm_prog.setValue(0)
        swap_worker(self, "_rm_worker", RmWorker(self._del_path, files))
        self._rm_worker.sig_prog.connect(self._on_rm_prog)
        self._rm_worker.sig_done.connect(self._on_rm_done)
        self._rm_worker.start()

    def _on_rm_prog(self, cur_file, done, total):
        self._rm_prog.setMaximum(max(total, 1))
        self._rm_prog.setValue(done)
        self._rm_prog.setLabelText(
            f"正在删除 {done}/{total} 个文件（{done * 100 // max(total, 1)}%）\n"
            f"当前：{cur_file}")

    def _on_rm_done(self, ok, err=""):
        try:
            self._rm_prog.close()
        except Exception:
            pass
        inst, path = self._del_inst, self._del_path
        self.b_del.setEnabled(True)
        self.b_del.setText("🗑 删除此实例（可选仅移出管理 / 彻底删除文件）")
        if not ok:
            QMessageBox.critical(self, "ComfyHub",
                                 f"删除失败（可能有文件被占用）：\n{path}\n\n"
                                 "请确认 ComfyUI 已停止后重试。")
            return
        if inst in self.cfg["instances"]:
            self.cfg["instances"].remove(inst)
            save_config(self.cfg)
        try:
            self.mainwin._sync_instances()
            self.load_current()
        except Exception:
            pass   # 刷新失败不阻断删除完成提示
        QMessageBox.information(self, "ComfyHub",
                                f"实例「{inst.get('name')}」已彻底删除。")

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择 ComfyUI 根目录",
                                             self.ed_path.text() or "F:\\")
        if d and os.path.isfile(os.path.join(d, "main.py")):
            self.ed_path.setText(os.path.normpath(d))
        elif d:
            QMessageBox.warning(self, "ComfyHub", "该目录下没有 main.py")

    def _pick_models(self):
        d = QFileDialog.getExistingDirectory(self, "选择模型目录",
                                             self.ed_models.text() or "F:\\")
        if d:
            d = os.path.normpath(d)
            self.ed_models.setText(d)
            self.ed_models.setToolTip(d)
            # 立即写 yaml 并持久化到实例配置
            inst = self._current_inst()
            if inst:
                inst["models_dir"] = d
                from app.config import save_config
                save_config(self.cfg)
                self._write_model_paths(inst, d)

    def _pick_python(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 Python 解释器",
                                           self.ed_python.text(), "Python (python.exe)")
        if f:
            self.ed_python.setText(f)

    @staticmethod
    def _write_model_paths(inst, models_dir):
        try:
            yaml = (
                "comfyui:\n"
                f"  base_path: {models_dir}\n"
                "  checkpoints: checkpoints\n  loras: loras\n  vae: vae\n"
                "  clip: clip\n  clip_vision: clip_vision\n  configs: configs\n"
                "  controlnet: controlnet\n  diffusion_models: diffusion_models\n"
                "  embeddings: embeddings\n  unet: unet\n"
                "  upscale_models: upscale_models\n")
            open(os.path.join(inst["comfyui_path"], "extra_model_paths.yaml"),
                 "w", encoding="utf-8").write(yaml)
        except OSError:
            pass

    # ---------- 启停（供首页/控制台调用） ----------
    def start(self):
        inst = self._current_inst()
        if not inst:
            return
        # device 字段由 build_command 统一处理（--cpu 等）
        self.mainwin.procs.get(inst).start(inst)
        self._update_state()
        self.mainwin.goto(NAV.CONSOLE)   # 启动后自动切到「控制台」看实时日志

    def stop(self):
        inst = self._current_inst()
        if inst:
            self.mainwin.procs.get(inst).stop()
            self._update_state()

    def open_browser(self):
        import webbrowser
        inst = self._current_inst()
        if inst:
            webbrowser.open(f"http://127.0.0.1:{int(inst.get('port') or 8188)}")
