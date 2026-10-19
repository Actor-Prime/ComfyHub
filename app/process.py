# -*- coding: utf-8 -*-
"""进程管理：QProcess 启动 ComfyUI、实时日志、停止进程树、端口探测"""
import os
import re
import socket
import subprocess

from app.winsub import run

from PySide6.QtCore import QProcess, QProcessEnvironment, QObject, Signal, QTimer

from app.config import VRAM_MODES


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """探测端口是否被监听"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def kill_tree(pid: int) -> None:
    """Windows 下强杀整棵进程树（ComfyUI 可能带子进程）"""
    try:
        run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def build_command(inst: dict) -> list:
    """根据实例配置构造启动命令行（含高级设置字段）"""
    python = inst.get("python_path") or "python"
    main_py = os.path.join(inst["comfyui_path"], "main.py")
    cmd = [python, "-s", main_py]
    # 显存模式
    flag = VRAM_MODES.get(inst.get("vram_mode", "自动"), "")
    if flag:
        cmd.append(flag)
    # 生成设备
    if inst.get("device") == "cpu":
        cmd.append("--cpu")
    # 端口
    port = int(inst.get("port") or 8188)
    listen_all = bool(inst.get("lan_access"))
    cmd += ["--listen", "0.0.0.0" if listen_all else "127.0.0.1",
            "--port", str(port)]
    # Cross Attention 优化方案
    ca = inst.get("cross_attn", "自动")
    if ca == "Quad（省显存）":
        cmd.append("--use-quad-cross-attention")
    elif ca == "Split（更快）":
        cmd.append("--use-split-cross-attention")
    elif ca == "PyTorch SDP":
        cmd.append("--use-pytorch-cross-attention")
    # 预览方式
    pv = inst.get("preview_method", "无")
    if pv != "无":
        cmd += ["--preview-method", "taesd" if pv == "TAESD（精细）" else "latent2rgb"]
    # 精度 / 性能开关
    if inst.get("force_fp16"):
        cmd.append("--force-fp16")
    if inst.get("fast_mode"):
        cmd.append("--fast")
    if inst.get("no_smart_memory"):
        cmd.append("--disable-smart-memory")
    # 自定义输出/输入目录
    if inst.get("output_dir"):
        cmd += ["--output-directory", inst["output_dir"]]
    if inst.get("input_dir"):
        cmd += ["--input-directory", inst["input_dir"]]
    # 额外参数（原样拆分，支持 --reserve-vram 等高级用法）
    extra = (inst.get("extra_args") or "").strip()
    if extra:
        cmd += extra.split()
    return cmd


class ComfyProcess(QObject):
    """单个 ComfyUI 实例的进程封装"""
    log_line = Signal(str)          # 一行日志
    state_changed = Signal(bool)    # 运行状态变化（True=运行中）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = ""
        self._pid = None
        # 就绪轮询：启动后探测 HTTP 端口
        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(1000)
        self._ready_timer.timeout.connect(self._check_ready)
        self._port = 8188
        self._ready_announced = False
        self._browser_on_ready = False

    # ---------- 状态 ----------
    @property
    def is_running(self) -> bool:
        return self._proc is not None and \
            self._proc.state() != QProcess.NotRunning

    @property
    def pid(self):
        return self._pid

    # ---------- 启动 ----------
    def start(self, inst: dict):
        if self.is_running:
            self.log_line.emit("[ComfyHub] 已在运行中，请先停止。")
            return
        cmd = build_command(inst)
        self._port = int(inst.get("port") or 8188)
        self._ready_announced = False
        self._browser_on_ready = bool(inst.get("auto_open_browser", True))

        port = self._port
        if port_in_use(port):
            self.log_line.emit(
                f"[ComfyHub] 端口 {port} 已被占用。若已有一个 ComfyUI 在跑，可直接用浏览器打开；"
                f"否则请更换端口后重试。")
            return

        workdir = inst["comfyui_path"]
        env = QProcessEnvironment.systemEnvironment()
        # UTF-8 输出，避免中文日志乱码
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")

        self._proc = QProcess(self)
        self._proc.setProgram(cmd[0])
        self._proc.setArguments(cmd[1:])
        self._proc.setWorkingDirectory(workdir)
        self._proc.setProcessEnvironment(env)
        # 合并 stdout/stderr
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        self.log_line.emit("[ComfyHub] " + " ".join(cmd))
        self.log_line.emit(f"[ComfyHub] 工作目录: {workdir}")
        self._proc.start()
        self._pid = self._proc.processId()
        self.state_changed.emit(True)
        self._ready_timer.start()

    # ---------- 停止 ----------
    def stop(self):
        if not self.is_running:
            return
        self._ready_timer.stop()
        if self._pid:
            kill_tree(self._pid)   # Windows 下直接杀树最可靠
        if self._proc:
            self._proc.waitForFinished(5000)
        self._cleanup()

    def _cleanup(self):
        self._proc = None
        self._pid = None
        self.state_changed.emit(False)

    # ---------- 内部 ----------
    def _on_output(self):
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += data
        lines, sep, rest = self._buf.rpartition("\n")
        if sep:
            for ln in lines.splitlines():
                self.log_line.emit(ln.rstrip("\r"))
            self._buf = rest

    def _check_ready(self):
        if self._ready_announced or not self.is_running:
            return
        if port_in_use(self._port):
            self._ready_announced = True
            self._ready_timer.stop()
            self.log_line.emit(f"[ComfyHub] 服务就绪: http://127.0.0.1:{self._port}")
            if self._browser_on_ready:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{self._port}")

    def _on_finished(self, code, status):
        self.log_line.emit(f"[ComfyHub] 进程退出，code={code}")
        self._ready_timer.stop()
        self._cleanup()

    def _on_error(self, err):
        mapping = {
            QProcess.FailedToStart: "启动失败：请检查 Python 路径是否正确",
            QProcess.Crashed: "进程崩溃",
            QProcess.Timedout: "超时",
            QProcess.WriteError: "写入错误",
            QProcess.ReadError: "读取错误",
            QProcess.UnknownError: "未知错误",
        }
        self.log_line.emit(f"[ComfyHub] {mapping.get(err, str(err))}")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")  # 清理终端色码


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)
