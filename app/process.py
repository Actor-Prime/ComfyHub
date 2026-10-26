# -*- coding: utf-8 -*-
"""进程管理：QProcess 启动 ComfyUI、实时日志、停止进程树、端口探测"""
import ctypes
import os
import re
import socket
import subprocess
from ctypes import wintypes

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


# ==================== Job Object：防崩溃残留 ====================
# 2026-10-26：优雅退出（closeEvent→stop_all）会带走实例，但 ComfyHub 崩溃 /
# 被 taskkill 强杀时 closeEvent 没机会跑 → ComfyUI 残留后台。
# 解法：把启动的 ComfyUI 绑进 Windows Job Object（KILL_ON_JOB_CLOSE），
# 管理器进程无论怎么死，内核都会连带杀光 job 内所有进程。

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]

_job_handle = None


def _get_kill_job():
    """全局 Job Object（懒创建）；失败返回 None（回退旧行为，不影响启动）"""
    global _job_handle
    if os.name != "nt":
        return None
    if _job_handle is not None:
        return _job_handle
    try:
        h = _k32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = \
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(
                h, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            _k32.CloseHandle(h)
            return None
        _job_handle = h
        return h
    except Exception:
        return None


def assign_to_kill_job(pid: int) -> bool:
    """把进程绑进全局 kill-on-close job；失败静默返回 False（回退旧行为）"""
    if os.name != "nt" or not pid:
        return False
    job = _get_kill_job()
    if not job:
        return False
    try:
        # AssignProcessToJobObject 需要 PROCESS_SET_QUOTA|PROCESS_TERMINATE
        hproc = _k32.OpenProcess(0x0100 | 0x0001, False, int(pid))
        if not hproc:
            return False
        try:
            return bool(_k32.AssignProcessToJobObject(job, hproc))
        finally:
            _k32.CloseHandle(hproc)
    except Exception:
        return False


# ==================== 孤儿实例检测（启动时清理历史残留） ====================

def pid_listening_on(port: int) -> list:
    """返回正在 LISTENING 指定 TCP 端口的 PID 列表（netstat -ano -p TCP 解析）"""
    if os.name != "nt":
        return []
    try:
        out = run(["netstat", "-ano", "-p", "TCP"],
                  capture_output=True, timeout=10)
        raw = out.stdout
        text = raw.decode("gbk", "replace") if isinstance(raw, bytes) \
            else (raw or "")
    except Exception:
        return []
    pat = re.compile(rf"^\s+TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$")
    pids = []
    for line in text.splitlines():
        m = pat.match(line)
        if m and int(m.group(1)) not in pids:
            pids.append(int(m.group(1)))
    return pids


def process_image_name(pid: int) -> str:
    """进程映像完整路径（QueryFullProcessImageNameW）；失败返回空串"""
    if os.name != "nt":
        return ""
    try:
        hproc = _k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
        if not hproc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(4096)
            if _k32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            _k32.CloseHandle(hproc)
    except Exception:
        return ""


def scan_orphans(instances, exclude_pids=None) -> list:
    """启动时孤儿检测：端口在监听 + 映像是 python + 不在 exclude_pids
    → 大概率是上次会话异常退出（崩溃/被强杀）留下的 ComfyUI。

    返回 [{"inst":..., "pid":..., "image":...}]，每个实例最多报一个 PID。
    """
    exclude = {int(p) for p in (exclude_pids or [])}
    found = []
    for inst in (instances or []):
        try:
            port = int(inst.get("port") or 8188)
        except Exception:
            continue
        for pid in pid_listening_on(port):
            if pid in exclude:
                continue
            img = process_image_name(pid)
            if os.path.basename(img).lower().startswith("python"):
                found.append({"inst": inst, "pid": pid, "image": img})
                break   # 同端口多监听极罕见，每实例只报一个
    return found


def _global_launch() -> dict:
    """专家模式的全局启动参数（设置页配置，对所有实例生效）

    返回 {"vram","attn","cache","upcast","flags":[...],"extra"}；
    专家模式未开启或读取失败时返回 {}（不影响普通启动）。
    """
    try:
        from app.config import load_config
        st = load_config().get("settings") or {}
        if st.get("expert_mode"):
            return st.get("launch") or {}
    except Exception:
        pass
    return {}


def build_command(inst: dict) -> list:
    """根据实例配置构造启动命令行（含高级设置字段）

    合并规则（2026-10-26 专家模式全局参数）：
      实例单独设置 > 全局设置 —— 实例的显存/注意力留「自动」时才吃全局值，
      全局的缓存/精度/开关/自定义参数无实例对应项，直接追加（去重）。
    """
    gl = _global_launch()
    python = inst.get("python_path") or "python"
    main_py = os.path.join(inst["comfyui_path"], "main.py")
    cmd = [python, "-s", main_py]
    # 显存模式：实例优先，实例为「自动」时用全局
    flag = VRAM_MODES.get(inst.get("vram_mode", "自动"), "")
    if not flag and gl.get("vram"):
        flag = gl["vram"]
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
    # Cross Attention 优化方案：实例优先，实例为「自动」时用全局
    ca = inst.get("cross_attn", "自动")
    ca_flag = ""
    if ca == "Quad（省显存）":
        ca_flag = "--use-quad-cross-attention"
    elif ca == "Split（更快）":
        ca_flag = "--use-split-cross-attention"
    elif ca == "PyTorch SDP":
        ca_flag = "--use-pytorch-cross-attention"
    if not ca_flag and gl.get("attn"):
        ca_flag = gl["attn"]
    if ca_flag:
        cmd.append(ca_flag)
    # 预览方式
    pv = inst.get("preview_method", "无")
    if pv != "无":
        cmd += ["--preview-method", "taesd" if pv == "TAESD（精细）" else "latent2rgb"]
    # 精度 / 性能开关（实例）
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
    # 全局参数（专家模式）：缓存 / 精度提升 / 常用开关
    for k in ("cache", "upcast"):
        v = (gl.get(k) or "").strip()
        if v and v not in cmd:
            cmd.append(v)
    for f in (gl.get("flags") or []):
        if f and f not in cmd:
            cmd.append(f)
    # 额外参数：全局在前、实例在后（实例可覆盖同名参数的取值型 flag）
    for src in (gl.get("extra"), inst.get("extra_args")):
        s = (src or "").strip()
        if s:
            cmd += s.split()
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
        # ★ 未指定 Python 解释器（如向导「仍要添加」先注册后补环境）→ 明确引导，不静默用 PATH 里的 python
        _py = inst.get("python_path") or ""
        if not _py or not os.path.isfile(_py):
            self.log_line.emit(
                f"[ComfyHub] ❌ 该实例未配置 Python 解释器（当前值: {_py or '空'}），无法启动。\n"
                "请到「ComfyUI」页 →「实例设置」里指定 Python 路径，"
                "或到「工具管理」安装共享便携 Python 后重试。")
            return
        # ★ 启动前：① 恢复上次未完成的插件方案切换 ② 按 last_profile 自动应用方案
        try:
            from app import plugin_profiles as pp
            cp = inst.get("comfyui_path") or ""
            pp.recover_if_needed(cp, log=self.log_line.emit)
            if inst.get("auto_apply_profile", True):
                name = inst.get("last_profile") or ""
                prof = None
                for pr in (inst.get("plugin_profiles") or []):
                    if pr.get("name") == name:
                        prof = pr
                        break
                if prof is not None and isinstance(prof.get("enabled"), list):
                    out = pp.apply_profile(cp, prof["enabled"], log=self.log_line.emit)
                    if out.get("ok"):
                        if out.get("changed"):
                            self.log_line.emit(
                                f"[ComfyHub] 已应用插件方案「{name}」"
                                f"（{len(out['changed'])} 项变更）")
                    else:
                        self.log_line.emit(
                            f"[ComfyHub] ⚠️ 插件方案应用失败：{out.get('errors')}")
        except Exception as e:
            self.log_line.emit(f"[ComfyHub] 插件方案处理异常（不影响启动）：{e}")
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
        # 实例级代理（ComfyUI-Manager / 插件下载都要用）
        _proxy = (inst.get("proxy") or "").strip()
        if _proxy:
            env.insert("HTTP_PROXY", _proxy)
            env.insert("HTTPS_PROXY", _proxy)
            self.log_line.emit(f"[ComfyHub] 已为实例设置代理 {_proxy}")

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
        # 绑进 kill-on-close job：管理器崩溃/被强杀时 Windows 连带杀掉该实例
        if self._pid and not assign_to_kill_job(int(self._pid)):
            self.log_line.emit(
                "[ComfyHub] ⚠️ 进程未绑定防护 Job（异常退出时可能残留，"
                "下次启动会自动检测清理）")
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
