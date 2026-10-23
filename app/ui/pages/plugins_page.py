# -*- coding: utf-8 -*-
"""插件管理页：已装列表 / 一键更新 / URL 安装 / 启禁用 / 启动模式（插件方案）/ 重复节点检查"""
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog, QComboBox,
)

from app import instance as inst_lib
from app import plugin_profiles as pp
from app.config import save_config


class UpdateAllWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(int, int)

    def __init__(self, repos):
        super().__init__()
        self.repos = repos

    def run(self):
        ok = 0
        for name, path in self.repos:
            self.sig_log.emit(f"更新 {name} …")
            success, msg = inst_lib.git_pull_dir(path)
            self.sig_log.emit(f"  -> {'OK' if success else '失败'}: {msg[:120]}")
            if success:
                ok += 1
        self.sig_done.emit(ok, len(self.repos))


class InstallWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool, str)

    def __init__(self, url, dest):
        super().__init__()
        self.url, self.dest = url, dest

    def run(self):
        self.sig_log.emit(f"克隆 {self.url} …")
        ok, msg = inst_lib.git_clone(self.url, self.dest)
        self.sig_done.emit(ok, msg)


class ApplyProfileWorker(QThread):
    """应用插件方案：批量重命名可能耗时（插件多时），放后台"""
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, enabled_names):
        super().__init__()
        self.comfyui_path = comfyui_path
        self.enabled_names = enabled_names

    def run(self):
        try:
            out = pp.apply_profile(self.comfyui_path, self.enabled_names,
                                   log=self.sig_log.emit)
        except Exception as e:
            out = {"ok": False, "changed": [], "errors": [str(e)], "rolled_back": False}
        self.sig_done.emit(out)


class ScanDupWorker(QThread):
    """扫描重复节点：要读大量 .py 文本，放后台"""
    sig_log = Signal(str)
    sig_done = Signal(dict)

    def __init__(self, comfyui_path, only_enabled=True):
        super().__init__()
        self.comfyui_path = comfyui_path
        self.only_enabled = only_enabled

    def run(self):
        try:
            r = pp.scan_duplicate_nodes(self.comfyui_path, self.only_enabled,
                                        log=self.sig_log.emit)
        except Exception as e:
            r = {"conflicts": {}, "scanned": 0, "keys_total": 0, "error": str(e)}
        self.sig_done.emit(r)


class PluginsPage(QWidget):
    def __init__(self, cfg, mainwin):
        super().__init__()
        self.cfg = cfg
        self.mainwin = mainwin
        self._worker = None
        self._conflict_map = {}
        self._build()

    def _inst(self):
        return self.mainwin.current_inst()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)

        # ---- 第一行：实例信息 + 通用操作 ----
        bar = QHBoxLayout()
        self.lb_inst = QLabel()
        self.lb_inst.setStyleSheet("color:#9aa4b2;")
        bar.addWidget(self.lb_inst)
        bar.addStretch()
        b_update = QPushButton("🔄 一键更新所有插件")
        b_update.setProperty("accent", True)
        b_update.clicked.connect(self._update_all)
        b_install = QPushButton("＋ 从 Git URL 安装")
        b_install.setProperty("success", True)
        b_install.clicked.connect(self._install)
        b_open = QPushButton("打开目录")
        b_open.clicked.connect(self._open_dir)
        bar.addWidget(b_update)
        bar.addWidget(b_install)
        bar.addWidget(b_open)
        root.addLayout(bar)

        # ---- 第二行：启动模式（插件方案）+ 冲突检查 ----
        pbar = QHBoxLayout()
        lb_mode = QLabel("启动模式:")
        lb_mode.setStyleSheet("color:#9aa4b2;")
        self.cb_profile = QComboBox()
        self.cb_profile.setMinimumWidth(190)
        self.cb_profile.currentIndexChanged.connect(self._on_profile_changed)
        b_apply = QPushButton("▶ 应用")
        b_apply.setProperty("accent", True)
        b_apply.setToolTip("按此模式批量启用/停用插件（重启 ComfyUI 生效）")
        b_apply.clicked.connect(self._apply_profile)
        b_save = QPushButton("💾 存为方案")
        b_save.setToolTip("把当前插件的启停状态保存为一个模式")
        b_save.clicked.connect(self._save_profile)
        b_del = QPushButton("删除")
        b_del.clicked.connect(self._delete_profile)
        b_dup = QPushButton("🔍 检查节点冲突")
        b_dup.clicked.connect(self._check_duplicates)
        pbar.addWidget(lb_mode)
        pbar.addWidget(self.cb_profile)
        pbar.addWidget(b_apply)
        pbar.addWidget(b_save)
        pbar.addWidget(b_del)
        pbar.addStretch()
        pbar.addWidget(b_dup)
        root.addLayout(pbar)

        # ---- 表格 ----
        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["插件名", "Git 仓库", "状态", "节点冲突"])
        self.tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tb.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tb.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tb.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tb.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tb.setSelectionBehavior(QTableWidget.SelectRows)
        self.tb.doubleClicked.connect(self._toggle)
        root.addWidget(self.tb, 1)

        self.lb_tip = QLabel("双击行 = 启用/禁用；调好后点「存为方案」保存成启动模式。")
        self.lb_tip.setStyleSheet("color:#7d8797;")
        root.addWidget(self.lb_tip)
        self.refresh_list()

    # ---------------------------------------------------------------- 刷新
    def on_show(self):
        self.refresh_list()

    def on_instance_changed(self):
        self.refresh_list()

    def _log(self, m):
        self.mainwin.console_page.append_line(f"[插件] {m}")

    def refresh_list(self):
        inst = self._inst()
        self._refill_profiles()
        if not inst:
            self.lb_inst.setText("（未选择实例）")
            self.tb.setRowCount(0)
            return
        self.lb_inst.setText(f"实例: {inst.get('name')}")
        rows = inst_lib.list_custom_nodes(inst["comfyui_path"])
        self.tb.setRowCount(len(rows))
        for i, (name, is_git, enabled) in enumerate(rows):
            self.tb.setItem(i, 0, QTableWidgetItem(name))
            self.tb.setItem(i, 1, QTableWidgetItem("是" if is_git else "否"))
            it = QTableWidgetItem("启用" if enabled else "已禁用")
            it.setForeground(Qt.green if enabled else Qt.gray)
            self.tb.setItem(i, 2, it)
            conflicts = self._conflict_map.get(name)
            if conflicts:
                c = QTableWidgetItem("⚠ " + "、".join(conflicts[:3]))
                c.setForeground(Qt.red)
                c.setToolTip("与其他插件重复的节点名：" + "、".join(conflicts))
                self.tb.setItem(i, 3, c)
            else:
                self.tb.setItem(i, 3, QTableWidgetItem(""))
            # 标记"当前状态与该模式不一致"的行（灰色斜体提示）
            pr = self._current_profile()
            if pr is not None:
                want = name in (pr.get("enabled") or [])
                if want != bool(enabled):
                    for col in range(4):
                        cell = self.tb.item(i, col)
                        if cell:
                            cell.setToolTip("与当前模式不一致：点「▶ 应用」批量同步")

    # ---------------------------------------------------------------- 启动模式
    def _profiles(self):
        inst = self._inst()
        if not inst:
            return []
        if not isinstance(inst.get("plugin_profiles"), list) or not inst["plugin_profiles"]:
            inst["plugin_profiles"] = [{"name": "默认", "enabled": [], "args": ""}]
        return inst["plugin_profiles"]

    def _refill_profiles(self):
        inst = self._inst()
        self.cb_profile.blockSignals(True)
        self.cb_profile.clear()
        if inst:
            for pr in self._profiles():
                self.cb_profile.addItem(pr.get("name", "未命名"))
            idx = self.cb_profile.findText(inst.get("last_profile") or "")
            if idx >= 0:
                self.cb_profile.setCurrentIndex(idx)
        self.cb_profile.blockSignals(False)

    def _current_profile(self):
        name = self.cb_profile.currentText()
        for pr in self._profiles():
            if pr.get("name") == name:
                return pr
        return None

    def _on_profile_changed(self, _i):
        inst = self._inst()
        pr = self._current_profile()
        if not inst or pr is None:
            return
        inst["last_profile"] = pr.get("name")
        save_config(self.cfg)
        self.lb_tip.setText(
            f"已选模式「{pr.get('name')}」（{len(pr.get('enabled') or [])} 个插件）。"
            "点「▶ 应用」才会真正启停插件。")

    def _apply_profile(self):
        inst = self._inst()
        pr = self._current_profile()
        if not inst or pr is None:
            QMessageBox.information(self, "提示", "请先选择实例与模式")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行上一个操作，请稍候")
            return
        enabled = pr.get("enabled") or []
        if not enabled:
            ans = QMessageBox.question(
                self, "确认", "该模式没有记录任何插件，应用后会停用全部插件。继续？")
            if ans != QMessageBox.Yes:
                return
        if self.mainwin.procs.is_running(inst):
            ans = QMessageBox.question(
                self, "ComfyUI 正在运行",
                "改动插件需要重启 ComfyUI 才生效。现在就应用吗？")
            if ans != QMessageBox.Yes:
                return
        self._worker = ApplyProfileWorker(inst["comfyui_path"], enabled)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(self._on_applied)
        self._worker.start()

    def _on_applied(self, out):
        self.refresh_list()
        if out.get("ok"):
            ch = out.get("changed") or []
            on = len([c for c in ch if c[1] == "on"])
            off = len([c for c in ch if c[1] == "off"])
            self.lb_tip.setText(f"已应用：{on} 个启用、{off} 个停用　（重启 ComfyUI 生效）")
            self._log(f"应用模式完成：启用 {on}、停用 {off}")
        else:
            errs = "；".join(out.get("errors") or [])[:300]
            tail = "（已回滚到操作前状态）" if out.get("rolled_back") else ""
            QMessageBox.warning(self, "应用失败", errs + tail)

    def _save_profile(self):
        inst = self._inst()
        if not inst:
            return
        cur = pp.capture_current(inst["comfyui_path"])
        name, ok = QInputDialog.getText(
            self, "存为方案",
            f"把当前 {len(cur)} 个已启用插件存为一个模式，名称：",
            text=self.cb_profile.currentText() or "新模式")
        name = (name or "").strip()
        if not ok or not name:
            return
        profiles = self._profiles()
        for pr in profiles:
            if pr.get("name") == name:
                ans = QMessageBox.question(self, "已存在", f"模式「{name}」已存在，覆盖？")
                if ans != QMessageBox.Yes:
                    return
                pr["enabled"] = cur
                break
        else:
            profiles.append({"name": name, "enabled": cur, "args": ""})
        inst["last_profile"] = name
        save_config(self.cfg)
        self._refill_profiles()
        self.cb_profile.setCurrentText(name)
        self.lb_tip.setText(f"已保存模式「{name}」（{len(cur)} 个插件）")

    def _delete_profile(self):
        inst = self._inst()
        pr = self._current_profile()
        if not inst or pr is None:
            return
        if len(self._profiles()) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个模式")
            return
        ans = QMessageBox.question(self, "删除模式",
                                   f"删除模式「{pr.get('name')}」？（不影响插件本身）")
        if ans != QMessageBox.Yes:
            return
        inst["plugin_profiles"] = [x for x in self._profiles() if x is not pr]
        if inst.get("last_profile") == pr.get("name"):
            inst["last_profile"] = inst["plugin_profiles"][0].get("name", "默认")
        save_config(self.cfg)
        self._refill_profiles()

    # ---------------------------------------------------------------- 节点冲突
    def _check_duplicates(self):
        inst = self._inst()
        if not inst:
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在执行上一个操作，请稍候")
            return
        self.lb_tip.setText("正在扫描节点冲突（只查已启用插件，静态解析不执行插件代码）…")
        self._worker = ScanDupWorker(inst["comfyui_path"], only_enabled=True)
        self._worker.sig_log.connect(lambda m: None)
        self._worker.sig_done.connect(self._on_dup_done)
        self._worker.start()

    def _on_dup_done(self, r):
        conflicts = r.get("conflicts") or {}
        self._conflict_map = {}
        for node, owners in conflicts.items():
            for o in owners:
                self._conflict_map.setdefault(o, []).append(node)
        self.refresh_list()
        if r.get("error"):
            QMessageBox.warning(self, "扫描失败", r["error"])
            return
        if not conflicts:
            self.lb_tip.setText(
                f"✅ 未发现节点冲突（扫描 {r.get('scanned', 0)} 个插件、"
                f"{r.get('keys_total', 0)} 个节点）")
            return
        lines = [f"共 {len(conflicts)} 组同名节点：", ""]
        for node, owners in list(conflicts.items())[:15]:
            lines.append(f"· {node}")
            lines.append("    ← " + "、".join(owners))
        if len(conflicts) > 15:
            lines.append(f"…还有 {len(conflicts) - 15} 组")
        lines.append("")
        lines.append("同名节点后加载的会覆盖先加载的，可能导致出图异常。")
        lines.append("建议用「启动模式」只启用其中一方。")
        self.lb_tip.setText(f"⚠️ 发现 {len(conflicts)} 组节点冲突，见表格「节点冲突」列")
        QMessageBox.warning(self, "节点冲突", "\n".join(lines))

    # ---------------------------------------------------------------- 原有操作
    def _nodes_dir(self):
        return os.path.join(self._inst()["comfyui_path"], "custom_nodes")

    def _toggle(self, index):
        name = self.tb.item(index.row(), 0).text()
        src = os.path.join(self._nodes_dir(), name)
        dst = src[:-len(".disabled")] if name.endswith(".disabled") else src + ".disabled"
        try:
            os.rename(src, dst)
            self.refresh_list()
        except OSError as e:
            QMessageBox.warning(self, "ComfyHub", "重命名失败（可能被占用）:\n" + str(e))

    def _update_all(self):
        inst = self._inst()
        if not inst:
            return
        if self.mainwin.procs.is_running(inst):
            QMessageBox.warning(self, "ComfyHub", "更新插件前请先停止 ComfyUI")
            return
        repos = [(n, os.path.join(self._nodes_dir(), n))
                 for n, g, _e in inst_lib.list_custom_nodes(inst["comfyui_path"]) if g]
        if not repos:
            QMessageBox.information(self, "ComfyHub", "没有可更新的 Git 插件")
            return
        self._worker = UpdateAllWorker(repos)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(lambda ok, t: (
            self._log(f"更新完成 {ok}/{t}"), self.refresh_list()))
        self._worker.start()

    def _install(self):
        inst = self._inst()
        if not inst:
            return
        url, ok = QInputDialog.getText(self, "从 Git URL 安装插件", "插件仓库地址:")
        if not ok or not url.strip():
            return
        url = url.strip().rstrip("/")
        name = url.split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        dest = os.path.join(self._nodes_dir(), name)
        if os.path.exists(dest):
            QMessageBox.warning(self, "ComfyHub", f"目录已存在: {dest}")
            return
        self._worker = InstallWorker(url, dest)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(lambda ok2, msg: (
            self._log(f"安装{'成功' if ok2 else '失败'}: {msg}"), self.refresh_list()))
        self._worker.start()

    def _open_dir(self):
        inst = self._inst()
        if inst:
            inst_lib.open_in_explorer(self._nodes_dir())
