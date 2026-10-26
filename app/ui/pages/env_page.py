# -*- coding: utf-8 -*-
"""环境管理页：环境检测清单（对标 environment.js）+ 快照备份（envSnapshot.js）"""
import os
import shutil

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QPlainTextEdit,
    QTabWidget, QFrame, QLineEdit, QFileDialog, QDialog, QCheckBox,
    QScrollArea, QComboBox,
)

from app.tools import EnvBackup, Rescue
from app.bootstrap import find_git
from app.winsub import run as sub_run
from app.workers import swap_worker


# ---------------- 检测线程 ----------------
class DetectWorker(QThread):
    """逐项检测，每完成一项发一次信号 (索引, 状态文本, 是否OK)"""
    sig_item = Signal(int, str, bool)
    sig_done = Signal()

    def __init__(self, inst):
        super().__init__()
        self.inst = inst

    def run(self):
        py = self.inst.get("python_path", "")

        def probe(code, timeout=90):
            r = sub_run([py, "-c", code], capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "").strip()

        # 0 GIT
        git = find_git()
        if git:
            r = sub_run([git, "--version"], capture_output=True, text=True, timeout=15)
            self.sig_item.emit(0, (r.stdout or "git").strip()[:60], True)
        else:
            self.sig_item.emit(0, "未找到（可在设置中安装便携 MinGit）", False)
        # 1 Python
        if os.path.isfile(py):
            r = sub_run([py, "-c", "import sys;print(sys.version.split()[0])"],
                        capture_output=True, text=True, timeout=30)
            self.sig_item.emit(1, (r.stdout or "?").strip(), True)
        else:
            self.sig_item.emit(1, "路径无效", False)
        # 2/3 PyTorch + CUDA
        torch_ver, cuda = "未安装", "未安装"
        try:
            out = probe(
                "import torch;print(torch.__version__)\n"
                "print('yes' if torch.cuda.is_available() else 'no')\n"
                "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')\n",
                timeout=180)
            lines = [l for l in out.splitlines() if l.strip()]
            if lines:
                torch_ver = lines[0]
                if lines[1] == "yes":
                    cuda = f"CUDA 可用（{lines[2] if len(lines) > 2 else ''}）"
                else:
                    cuda = "仅 CPU"
            self.sig_item.emit(2, torch_ver, torch_ver != "未安装")
            self.sig_item.emit(3, cuda, "可用" in cuda)
        except Exception as e:
            self.sig_item.emit(2, f"检测失败: {type(e).__name__}", False)
            self.sig_item.emit(3, "-", False)
        # 4/5 显卡/驱动
        try:
            from app.bootstrap import detect_gpu
            g = detect_gpu()
            if g.vendor == "nvidia":
                self.sig_item.emit(4, f"{g.name}（{g.vram_mb}MB）", True)
                self.sig_item.emit(5, f"驱动 {g.driver} → 推荐写法 {g.torch_variant}",
                                   g.driver >= 525)
            elif g.vendor:
                self.sig_item.emit(4, g.name, True)
                self.sig_item.emit(5, "非 N 卡，暂用 CPU 模式", False)
            else:
                self.sig_item.emit(4, "未检测到显卡", False)
                self.sig_item.emit(5, "-", False)
        except Exception as e:
            self.sig_item.emit(4, f"检测失败: {e}", False)
            self.sig_item.emit(5, "-", False)
        # 6 CPU
        import psutil
        import platform
        self.sig_item.emit(6, platform.processor() or "未知", True)
        self.sig_done.emit()


class EnvPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._task = None
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_detect(), "环境检测")
        self.tabs.addTab(self._tab_snapshot(), "快照 / 备份")
        self.tabs.addTab(self._tab_deps(), "依赖管理")

    # ---------- 环境检测 ----------
    def _tab_detect(self):
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(self.lb_inst)
        bar.addStretch()
        b_run = QPushButton("🔍 采集检测")
        b_run.setProperty("accent", True)
        b_run.clicked.connect(self._run_detect)
        bar.addWidget(b_run)
        v.addLayout(bar)

        self.rows = []   # (row_frame, state_label)
        CHECKS = [
            ("GIT", "检测 Git，插件安装/更新的基础设施"),
            ("Python", "管理当前实例的 Python 运行环境"),
            ("PyTorch", "深度学习框架的版本检测"),
            ("PyTorch CUDA", "检测 PyTorch 是否可用 GPU 加速"),
            ("显卡", "检测显卡型号与显存"),
            ("显卡驱动", "检测驱动版本，判断支持的 CUDA 上限"),
            ("处理器 (CPU)", "处理器型号"),
        ]
        for name, desc in CHECKS:
            f = QFrame()
            f.setObjectName("CheckRow")
            h = QHBoxLayout(f)
            h.setContentsMargins(14, 10, 14, 10)
            left = QVBoxLayout()
            t = QLabel(name);  t.setObjectName("RowTitle")
            d = QLabel(desc);  d.setObjectName("RowDesc")
            left.addWidget(t)
            left.addWidget(d)
            h.addLayout(left, 1)
            st = QLabel("未检测")
            st.setObjectName("CheckState")
            st.setProperty("state", "pend")
            h.addWidget(st)
            v.addWidget(f)
            self.rows.append(st)
        v.addStretch()
        return w

    def _set_state(self, i, text, ok):
        st = self.rows[i]
        st.setText(text[:60] or "-")
        st.setProperty("state", "ok" if ok else "fail")
        st.style().unpolish(st)
        st.style().polish(st)
        st.setToolTip(text)

    def _run_detect(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先添加实例")
            return
        for st in self.rows:
            st.setText("检测中…")
            st.setProperty("state", "pend")
            st.style().unpolish(st)
            st.style().polish(st)
        w = DetectWorker(inst)
        w.sig_item.connect(self._set_state)
        w.sig_done.connect(lambda: self.lb_inst.setText(
            f"实例: {inst.get('name')} — 检测完成 ✅"))
        swap_worker(self, "_worker", w)
        w.start()

    def on_show(self):
        self.lb_inst.setText(
            f"实例: {self._inst().get('name')}" if self._inst() else "（未选择实例）")
        self._refresh_snapshots()

    def on_instance_changed(self):
        self.on_show()

    # ---------- 快照 / 备份 ----------
    def _tab_snapshot(self):
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.ed_label = QLineEdit()
        self.ed_label.setPlaceholderText("快照/备份备注（可空）")
        b_backup = QPushButton("创建环境备份")
        b_backup.setProperty("accent", True)
        b_backup.clicked.connect(self._create_backup)
        b_snap = QPushButton("创建救援快照")
        b_snap.clicked.connect(self._snapshot)
        b_restore = QPushButton("还原选中备份")
        b_restore.setProperty("danger", True)
        b_restore.clicked.connect(self._restore)
        b_rb = QPushButton("回滚选中快照")
        b_rb.setProperty("danger", True)
        b_rb.clicked.connect(self._rollback)
        bar.addWidget(self.ed_label, 1)
        bar.addWidget(b_backup)
        bar.addWidget(b_snap)
        bar.addWidget(b_restore)
        bar.addWidget(b_rb)
        v.addLayout(bar)

        self.tb = QTableWidget(0, 2)
        self.tb.setHorizontalHeaderLabels(["类型", "名称（时间_备注）"])
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        v.addWidget(self.tb, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogConsole")
        v.addWidget(self.log, 1)
        return w

    def _log(self, m):
        self.log.appendPlainText(str(m))

    def _refresh_snapshots(self):
        inst = self._inst()
        if not inst:
            self.tb.setRowCount(0)
            return
        try:
            bk = EnvBackup(inst["comfyui_path"], inst["python_path"])
            rs = Rescue(inst["comfyui_path"])
            items = ([("备份", x) for x in bk.list_backups()] +
                     [("快照", x) for x in rs.list_snapshots()])
        except Exception:
            items = []
        self.tb.setRowCount(len(items))
        for i, (kind, name) in enumerate(items):
            self.tb.setItem(i, 0, QTableWidgetItem(kind))
            self.tb.setItem(i, 1, QTableWidgetItem(name))

    def _selected(self, kind):
        row = self.tb.currentRow()
        if row < 0 or self.tb.item(row, 0).text() != kind:
            return None
        return self.tb.item(row, 1).text()

    def _create_backup(self):
        inst = self._inst()
        if not inst:
            return
        bk = EnvBackup(inst["comfyui_path"], inst["python_path"])
        self._task = _RunTask(lambda: bk.create(self.ed_label.text().strip(),
                                                log=self._log))
        self._task.sig_done.connect(lambda name: (self._log(f"备份完成: {name}"),
                                                  self._refresh_snapshots()))
        self._task.start()

    def _snapshot(self):
        inst = self._inst()
        if not inst:
            return
        Rescue(inst["comfyui_path"]).snapshot(log=self._log)
        self._refresh_snapshots()

    def _restore(self):
        name = self._selected("备份")
        if not name:
            QMessageBox.information(self, "ComfyHub", "请选择一个[备份]行")
            return
        r = QMessageBox.question(self, "还原", "将按备份重装 pip 依赖（耗时较长），继续？")
        if r != QMessageBox.Yes:
            return
        inst = self._inst()
        bk = EnvBackup(inst["comfyui_path"], inst["python_path"])
        self._task = _RunTask(lambda: bk.restore(name, log=self._log))
        self._task.sig_done.connect(lambda ok: (
            self._log(f"还原{'成功' if ok else '失败'}: {name}"),
            QMessageBox.information(self, "ComfyHub", "还原完成" if ok else "还原失败")))
        self._task.start()

    def _rollback(self):
        name = self._selected("快照")
        if not name:
            QMessageBox.information(self, "ComfyHub", "请选择一个[快照]行")
            return
        r = QMessageBox.question(self, "救援回滚",
                                 "禁用快照后新装的插件并恢复启停状态（不删文件）。继续？")
        if r != QMessageBox.Yes:
            return
        inst = self._inst()
        ok, msg = Rescue(inst["comfyui_path"]).rollback(name, log=self._log)
        self._log(f"回滚{'完成' if ok else '失败'}: {msg}")
        self._refresh_snapshots()




# ================= 依赖管理（对标管理大师 deps 管理） =================


    def _tab_deps(self):
        """依赖管理 tab（对标管理大师依赖管理）"""
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        b_detect = QPushButton("📦 依赖检测")
        b_detect.setProperty("accent", True)
        b_detect.clicked.connect(self._deps_detect)
        b_install = QPushButton("➕ 安装新依赖")
        b_install.setProperty("success", True)
        b_install.clicked.connect(self._deps_install)
        self.ed_pkg_kw = QLineEdit()
        self.ed_pkg_kw.setPlaceholderText("🔍 搜索已装依赖…")
        self.ed_pkg_kw.textChanged.connect(lambda _t: self._deps_filter())
        bar.addWidget(b_detect)
        bar.addWidget(b_install)
        bar.addWidget(self.ed_pkg_kw, 1)
        v.addLayout(bar)

        self.deps_log = QPlainTextEdit()
        self.deps_log.setReadOnly(True)
        self.deps_log.setObjectName("LogConsole")
        self.deps_log.setPlaceholderText("依赖检测/安装日志…")
        self.deps_log.setMaximumHeight(130)
        v.addWidget(self.deps_log)

        self.tb_deps = QTableWidget(0, 3)
        self.tb_deps.setHorizontalHeaderLabels(["依赖名", "版本号", "操作"])
        self.tb_deps.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb_deps.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tb_deps.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.tb_deps, 1)

        hint = QLabel("首次加载需要点上方【依赖检测】；卸载前请先停止 ComfyUI。")
        hint.setStyleSheet("color:#7d8797;")
        v.addWidget(hint)
        return w

    # ---- 依赖管理逻辑 ----
    def _deps_log(self, m):
        self.deps_log.appendPlainText(str(m))

    def _deps_detect(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先选择实例")
            return
        self._deps_log("正在检测已装依赖（pip list）…")
        w = PipListWorker(inst)
        w.sig_done.connect(self._deps_fill)
        swap_worker(self, "_pip_list_worker", w)
        w.start()

    def _deps_fill(self, pkgs, err):
        if err:
            self._deps_log(f"❌ 检测失败: {err}")
            return
        self._all_pkgs = pkgs
        self._deps_log(f"✅ 检测完成，共 {len(pkgs)} 个依赖")
        self._deps_render()

    def _deps_render(self):
        kw = self.ed_pkg_kw.text().strip().lower()
        self.tb_deps.setRowCount(0)
        for name, ver in getattr(self, "_all_pkgs", []):
            if kw and kw not in name.lower():
                continue
            r = self.tb_deps.rowCount()
            self.tb_deps.insertRow(r)
            self.tb_deps.setItem(r, 0, QTableWidgetItem(name))
            self.tb_deps.setItem(r, 1, QTableWidgetItem(ver))
            b = QPushButton("卸载")
            b.setStyleSheet("color:#e8635a; border:none; background:transparent;")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, n=name: self._deps_uninstall(n))
            self.tb_deps.setCellWidget(r, 2, b)

    def _deps_filter(self):
        self._deps_render()

    def _deps_uninstall(self, name):
        inst = self._inst()
        if not inst:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "卸载依赖前请先停止 ComfyUI")
            return
        r = QMessageBox.question(self, "卸载依赖",
                                 f"确定卸载 {name} 吗？\n（部分插件依赖它可能报错）")
        if r != QMessageBox.Yes:
            return
        self._deps_log(f"卸载 {name} …")
        w = PipOpWorker(inst["python_path"], ["uninstall", "-y", name])
        w.sig_log.connect(self._deps_log)
        w.sig_done.connect(
            lambda ok: (self._deps_log("✅ 卸载完成" if ok else "❌ 卸载失败"),
                        self._deps_detect()))
        swap_worker(self, "_deps_op_worker", w)
        w.start()

    def _deps_install(self):
        inst = self._inst()
        if not inst:
            QMessageBox.information(self, "ComfyHub", "请先选择实例")
            return
        mirror = "https://mirrors.aliyun.com/pypi/simple/"
        dlg = DepsDialog(self, mirror)
        dlg.sig_install.connect(lambda args: self._deps_run_install(inst, args))
        dlg.exec()

    def _deps_run_install(self, inst, args):
        if args and args[0] == "__RUN_PY__":
            # 通过 Python 文件安装（运行脚本）
            script = args[1]
            self._deps_log(f"运行脚本 {script} …")
            # 直接 run python 脚本
            w = _RunTask(
                lambda: sub_run([inst["python_path"], script],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=3600))
            w.sig_done.connect(
                lambda r: self._deps_log("脚本输出尾: "
                                         + ((r.stdout or r.stderr or "")[-300:] if hasattr(r, "stdout") else str(r))))
            swap_worker(self, "_deps_op_worker", w)
            w.start()
            return
        w = PipOpWorker(inst["python_path"], args)
        w.sig_log.connect(self._deps_log)
        w.sig_done.connect(
            lambda ok: (self._deps_log("✅ 安装完成" if ok else "❌ 安装失败"),
                        self._deps_detect()))
        swap_worker(self, "_deps_op_worker", w)
        w.start()

class PipListWorker(QThread):
    """pip list 后台检测"""
    sig_done = Signal(list, str)

    def run(self):
        inst = self._inst_ref()
        try:
            r = sub_run([inst["python_path"], "-m", "pip", "list",
                         "--format=freeze", "--disable-pip-version-check"],
                        capture_output=True, text=True, timeout=300)
            pkgs = []
            for ln in (r.stdout or "").splitlines():
                if "==" in ln:
                    name, ver = ln.split("==", 1)
                    pkgs.append((name.strip(), ver.strip()))
            self.sig_done.emit(pkgs, "")
        except Exception as e:
            self.sig_done.emit([], str(e))

    _inst = None
    def _inst_ref(self):
        return self._inst


class PipOpWorker(QThread):
    """通用 pip 操作（安装/卸载），日志实时回调"""
    sig_log = Signal(str)
    sig_done = Signal(bool)

    def __init__(self, python_exe, args, timeout=7200):
        super().__init__()
        self.python_exe = python_exe
        self.args = args
        self.timeout = timeout

    def run(self):
        self.sig_log.emit("$ " + " ".join([self.python_exe] + self.args[:14]))
        try:
            r = sub_run([self.python_exe, "-m", "pip"] + self.args,
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=self.timeout)
        except Exception as e:
            self.sig_log.emit(f"❌ 异常: {e}")
            self.sig_done.emit(False)
            return
        tail = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines()
                if "[safe-delete]" not in ln and ln.strip()]
        for ln in tail[-15:]:
            self.sig_log.emit("  " + ln[:160])
        self.sig_done.emit(r.returncode == 0)

COMMON_DEPS = [
    ("opencv-python", "图像处理（很多插件需要）"),
    ("rembg", "AI 抠图"),
    ("mediapipe", "人体/人脸关键点检测"),
    ("onnxruntime-gpu", "ONNX 推理（GPU）"),
    ("insightface", "人脸识别（IPAdapter/InstantID 用）"),
    ("huggingface_hub", "HF 模型下载工具"),
    ("comfyui-frontend-package", "ComfyUI 前端包（更新界面）"),
    ("scipy", "科学计算"),
]


class DepsDialog(QDialog):
    """安装新依赖：六种方式标签页（对标管理大师安装弹窗）"""
    sig_install = Signal(list)   # pip args

    def __init__(self, parent, mirror):
        super().__init__(parent)
        self.setWindowTitle("安装新依赖")
        self.resize(640, 420)
        self.mirror = mirror
        v = QVBoxLayout(self)
        self.tw = QTabWidget()
        v.addWidget(self.tw, 1)
        tip = QLabel("💡 温馨提示：可在【系统设置】切换国内镜像，提高安装速度！")
        tip.setStyleSheet("color:#4cd47c; background:rgba(76,212,124,.08);"
                          "border-radius:4px; padding:8px;")
        v.addWidget(tip)
        hb = QHBoxLayout()
        hb.addStretch()
        b_ok = QPushButton("开始安装")
        b_ok.setProperty("accent", True)
        b_ok.clicked.connect(self._collect)
        b_cancel = QPushButton("关闭")
        b_cancel.clicked.connect(self.reject)
        hb.addWidget(b_cancel)
        hb.addWidget(b_ok)
        v.addLayout(hb)

        # tab1 包名
        w1 = QWidget(); f1 = QVBoxLayout(w1)
        self.ed_names = QLineEdit()
        self.ed_names.setPlaceholderText("输入依赖包名（空格分隔多个），如: opencv-python rembg")
        f1.addWidget(self.ed_names)
        f1.addWidget(QLabel("等价于 pip install <包名>，走国内镜像。"))
        self.tw.addTab(w1, "根据名字安装")

        # tab2 requirements.txt
        w2 = QWidget(); f2 = QVBoxLayout(w2)
        h2 = QHBoxLayout()
        self.ed_req = QLineEdit()
        self.ed_req.setPlaceholderText("requirements.txt 文件路径")
        b2 = QPushButton("浏览…")
        b2.clicked.connect(lambda: self.ed_req.setText(QFileDialog.getOpenFileName(
            self, "选择 requirements.txt", "", "requirements (*.txt)")[0]))
        h2.addWidget(self.ed_req, 1)
        h2.addWidget(b2)
        f2.addLayout(h2)
        f2.addWidget(QLabel("等价于 pip install -r <文件>，适合批量补依赖。"))
        self.tw.addTab(w2, "根据 requirements.txt 安装")

        # tab3 whl 轮子
        w3 = QWidget(); f3 = QVBoxLayout(w3)
        h3 = QHBoxLayout()
        self.ed_whl = QLineEdit()
        self.ed_whl.setPlaceholderText("浏览轮子文件（.whl），如 torch-2.10.0+cu130-....whl")
        b3 = QPushButton("浏览 .whl")
        b3.clicked.connect(lambda: self.ed_whl.setText(QFileDialog.getOpenFileName(
            self, "选择 wheel 文件", "", "wheel (*.whl)")[0]))
        h3.addWidget(self.ed_whl, 1)
        h3.addWidget(b3)
        f3.addLayout(h3)
        f3.addWidget(QLabel("适合离线安装大包（torch 等），不受网络影响。"))
        self.tw.addTab(w3, "安装轮子文件")

        # tab4 python 文件
        w4 = QWidget(); f4 = QVBoxLayout(w4)
        h4 = QHBoxLayout()
        self.ed_py = QLineEdit()
        self.ed_py.setPlaceholderText("Python 脚本路径（如 get-pip.py）")
        b4 = QPushButton("浏览…")
        b4.clicked.connect(lambda: self.ed_py.setText(QFileDialog.getOpenFileName(
            self, "选择 Python 文件", "", "Python (*.py)")[0]))
        h4.addWidget(self.ed_py, 1)
        h4.addWidget(b4)
        f4.addLayout(h4)
        f4.addWidget(QLabel("以当前实例解释器运行该脚本（等价 python <文件>）。"))
        self.tw.addTab(w4, "通过 Python 文件安装")

        # tab5 常用依赖
        w5 = QWidget(); f5 = QVBoxLayout(w5)
        self.ck_list = []
        for name, desc in COMMON_DEPS:
            ck = QCheckBox(f"{name} — {desc}")
            self.ck_list.append((ck, name))
            f5.addWidget(ck)
        scroll5 = QScrollArea()
        scroll5.setWidgetResizable(True)
        scroll5.setFrameShape(QFrame.NoFrame)
        scroll5.setWidget(w5)
        self.tw.addTab(scroll5, "常用依赖")

        # tab6 执行命令
        w6 = QWidget(); f6 = QVBoxLayout(w6)
        self.ed_cmd = QLineEdit()
        self.ed_cmd.setPlaceholderText("pip 子命令与参数，如: install numpy --upgrade")
        f6.addWidget(self.ed_cmd)
        f6.addWidget(QLabel("以 python -m pip <你输入的内容> 执行，熟练玩家用。"))
        self.tw.addTab(w6, "执行命令")

    def _collect(self):
        i = self.tw.currentIndex()
        if i == 0:
            names = self.ed_names.text().split()
            if not names:
                return
            self.sig_install.emit(["install", *names, "-i", self.mirror,
                                   "--no-warn-script-location"])
        elif i == 1:
            f = self.ed_req.text().strip()
            if f:
                self.sig_install.emit(["install", "-r", f, "-i", self.mirror,
                                       "--no-warn-script-location"])
        elif i == 2:
            f = self.ed_whl.text().strip()
            if f:
                self.sig_install.emit(["install", f, "--no-warn-script-location"])
        elif i == 3:
            f = self.ed_py.text().strip()
            if f:
                self.sig_install.emit(["__RUN_PY__", f])
        elif i == 4:
            names = [n for ck, n in self.ck_list if ck.isChecked()]
            if not names:
                return
            self.sig_install.emit(["install", *names, "-i", self.mirror,
                                   "--no-warn-script-location"])
        elif i == 5:
            cmd = self.ed_cmd.text().strip().split()
            if cmd:
                self.sig_install.emit(cmd)
        self.accept()


from PySide6.QtCore import QThread as _QThread, Signal as _Signal


class _RunTask(_QThread):
    sig_done = _Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.sig_done.emit(self.fn())
        except Exception as e:
            self.sig_done.emit(f"失败: {e}")
