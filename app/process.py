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
    """端口是否已被任何进程监听（一次性检查用）。

    2026-11-02 改为 netstat 判定：原实现 socket.connect_ex 探测在本机实测
    对「关闭的本地端口」不会秒拒，而是吃满整个超时（0.5s/次、返回 10035），
    放进 UI 线程周期路径后把界面卡到不能动（用户实测：点启动后持续卡顿，
    直到 ComfyUI 绑上端口探测变秒回才恢复）。
    netstat 一次子进程约 60ms——只适合一次性检查；周期性探测一律走
    ComfyProcess 的 QTcpSocket 异步就绪探测（不占 UI 线程）。
    host 参数保留兼容；netstat 判定不区分绑定地址。
    """
    p = int(port)
    return any(pl == p for _pid, pl in _netstat_listening())


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

def _netstat_listening() -> list:
    """返回 [(pid, local_port)]：netstat -ano -p TCP 全部 LISTENING 行解析
    （GBK 解码；孤儿检测与实际端口识别共用）"""
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
    pat = re.compile(r"^\s+TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$")
    res = []
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            res.append((int(m.group(2)), int(m.group(1))))
    return res


def pid_listening_on(port: int) -> list:
    """返回正在 LISTENING 指定 TCP 端口的 PID 列表"""
    pids = []
    for pid, p in _netstat_listening():
        if p == port and pid not in pids:
            pids.append(pid)
    return pids


def listening_ports(pid: int) -> list:
    """返回指定 PID 正在 LISTENING 的本地端口（升序去重）"""
    return sorted({p for p_, p in _netstat_listening() if p_ == int(pid)})


def actual_port_for_pid(pid: int, prefer_port=None) -> "int | None":
    """该进程实际对外服务的 HTTP 端口（启动端口被占顺延后 ≠ 配置端口时用它）

    判定：配置端口在监听列表 → 用它；否则列表唯一 → 用它；
    多端口 → 取最小（保守猜测）；无监听 → None
    """
    ports = listening_ports(pid)
    if not ports:
        return None
    if prefer_port and int(prefer_port) in ports:
        return int(prefer_port)
    return ports[0]


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


def detect_gpus() -> list:
    """枚举本机 CUDA 显卡（nvidia-smi）。返回 [{"id":int,"name":str,"vram_mib":int}]，
    无 nvidia-smi / 无 N 卡时返回 []（调用方据此决定是否显示设备下拉）。"""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=6,
            creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        gpus = []
        for line in out.decode("utf-8", "replace").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    gpus.append({"id": int(parts[0]), "name": parts[1],
                                 "vram_mib": int(parts[2])})
                except ValueError:
                    continue
        return gpus
    except Exception:
        return []


def torch_build_of(python_exe: str) -> "str | None":
    """快速判定解释器里 torch 的构建变体：'cuda' / 'cpu' / None（未装或判不出）。
    只读 Lib/site-packages/torch/version.py 的 cuda 字段，不 import torch
    （import 要秒级冷启动，启动门卫等不起）。"""
    try:
        base = os.path.join(os.path.dirname(os.path.abspath(python_exe)),
                            "Lib", "site-packages")
        vp = os.path.join(base, "torch", "version.py")
        if not os.path.isfile(vp):
            return None
        with open(vp, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip().replace(" ", "")
                # torch 2.15+ 的 version.py 带类型注解：cuda:Optional[str]=None
                if s.startswith("cuda=") or s.startswith("cuda:"):
                    val = s.split("=", 1)[-1].strip().strip("\"'")
                    return "cpu" if val in ("None", "") else "cuda"
    except Exception:
        pass
    return None


# 启动前环境预检的拦截文案（静态文案，i18n 词表可精确匹配）
REASON_CPU_TORCH_GPU = (
    "该实例的 Python 装的是 CPU 版 torch，无法使用显卡加速，"
    "ComfyUI 启动会直接崩溃（Torch not compiled with CUDA enabled）。\n"
    "点「去修复」到环境检测页一键重装 CUDA 版 torch（约 6GB 磁盘空间），"
    "或临时以 CPU 模式启动。")
REASON_CPU_TORCH_CPU_ENGINE = (
    "torch 是 CPU 版，但生成引擎设置为 CUDA：ComfyUI 启动会直接崩溃。\n"
    "点「去修复」到环境检测页重装对应版本的 torch，"
    "或在实例设置里把生成引擎改为 CPU。")


def env_block_reason(inst: dict) -> "str | None":
    """启动前环境预检：torch 构建与生成引擎不匹配时返回拦截原因，匹配/判不出返回 None。

    起因（2026-11-09）：共享 Python 装了 CPU 版 torch（安装链 CUDA 失败静默回退），
    ComfyUI 的 comfy/model_management.py 默认 GPU 分支会直接调
    torch.cuda.current_device()，CPU 版 torch 抛
    "AssertionError: Torch not compiled with CUDA enabled"，import 阶段就崩——
    环境检测页能体检出来但不拦启动，这里补上门卫。"""
    backend = inst.get("engine_backend")
    if backend is None:
        backend = "cpu" if inst.get("device") == "cpu" else "cuda"
    if backend in ("cpu", "dml"):
        return None        # --cpu / --directml 模式不走 CUDA 分支，CPU 版 torch 没问题
    py = inst.get("python_path") or ""
    if not py or not os.path.isfile(py):
        return None        # 解释器缺失由 start() 的前置检查负责
    if torch_build_of(py) != "cpu":
        return None        # CUDA 构建、或判不出（不误拦）
    try:
        from app.bootstrap import detect_gpu
        gpu = detect_gpu()
        if gpu is not None and gpu.vendor == "nvidia":
            return REASON_CPU_TORCH_GPU
    except Exception:
        pass
    return REASON_CPU_TORCH_CPU_ENGINE


def build_command(inst: dict, port: int | None = None) -> list:
    """根据实例配置构造启动命令行（含高级设置字段）

    port: 覆盖实例配置端口（启动时配置端口被占、自动顺延后传实际端口）。

    2026-10-28 专家模式（全局启动参数）整体移除：命令只吃实例级配置，
    旧配置里残留的 settings.launch 不再消费。
    2026-10-28 对齐绘世「高级选项」补齐：多卡 --cuda-device、CPU VAE、
    稳定计算、Channels-Last、CUDA 分配器、多用户、三档计算精度。
    """
    python = inst.get("python_path") or "python"
    main_py = os.path.join(inst["comfyui_path"], "main.py")
    cmd = [python, "-s", main_py]
    # 显存模式
    flag = VRAM_MODES.get(inst.get("vram_mode", "自动"), "")
    if flag:
        cmd.append(flag)
    # 生成引擎（后端 + 设备）：2026-10-29 对齐绘世统一为一个下拉
    #   backend: auto / cuda / dml / cpu；index: 设备序号（auto 可空）
    #   旧字段兼容：device=="cpu"、cuda_device>=0（无 backend 字段的存量配置）
    backend = inst.get("engine_backend")
    if backend is None:
        backend = "cpu" if inst.get("device") == "cpu" else "cuda"
    if backend == "cpu":
        cmd.append("--cpu")
    elif backend == "dml":
        # torch-directml；序号省略 = 默认设备（cli_args: const=-1 → device()）
        _dml = inst.get("engine_index")
        if _dml is not None and str(_dml).strip() != "":
            cmd += ["--directml", str(int(_dml))]
        else:
            cmd.append("--directml")
    else:  # cuda / auto
        _dev = inst.get("engine_index", inst.get("cuda_device"))
        if _dev is not None and str(_dev).strip() != "" and int(_dev) >= 0:
            cmd += ["--cuda-device", str(int(_dev))]
    # 端口（start() 自动顺延后会传入实际端口覆盖配置值）
    if port is None:
        port = int(inst.get("port") or 8188)
    listen_all = bool(inst.get("lan_access"))
    cmd += ["--listen", "0.0.0.0" if listen_all else "127.0.0.1",
            "--port", str(port)]
    # Cross Attention 优化方案（2026-10-29 对齐绘世五档：
    #   自动(xFormers优先) / Sage / SDP / Quad / Split；旧值"自动"仍兼容）
    ca = inst.get("cross_attn", "自动")
    ca_flag = ""
    if ca.startswith("Sage"):
        ca_flag = "--use-sage-attention"
    elif ca == "Quad（省显存）":
        ca_flag = "--use-quad-cross-attention"
    elif ca == "Split（更快）":
        ca_flag = "--use-split-cross-attention"
    elif ca == "PyTorch SDP":
        ca_flag = "--use-pytorch-cross-attention"
    # "自动*"：不加参数（ComfyUI 有 xformers 用 xformers，否则回落 SDP）
    if ca_flag:
        cmd.append(ca_flag)
    # 预览方式
    pv = inst.get("preview_method", "无")
    if pv != "无":
        cmd += ["--preview-method",
                "auto" if pv.startswith("自动")
                else ("taesd" if pv == "TAESD（精细）" else "latent2rgb")]
    # 计算精度（三档，互斥）：默认自动不加参数
    prec = inst.get("precision", "自动")
    if prec == "强制 FP16":
        cmd.append("--force-fp16")
    elif prec == "强制 FP32":
        cmd.append("--force-fp32")
    elif inst.get("force_fp16"):   # 旧字段兼容（老配置存的布尔）
        cmd.append("--force-fp16")
    if inst.get("fast_mode"):
        cmd.append("--fast")
    if inst.get("no_smart_memory"):
        cmd.append("--disable-smart-memory")
    # 对齐绘世补齐的开关
    if inst.get("cpu_vae"):
        cmd.append("--cpu-vae")
    if inst.get("deterministic"):
        cmd.append("--deterministic")
    if inst.get("channels_last"):
        cmd.append("--force-channels-last")
    if inst.get("multi_user"):
        cmd.append("--multi-user")
    # CUDA 内存分配器：自动(默认)/强制启用/禁用
    cm = inst.get("cuda_malloc", "自动")
    if cm == "强制启用":
        cmd.append("--cuda-malloc")
    elif cm == "禁用":
        cmd.append("--disable-cuda-malloc")
    # 自动打开浏览器：不勾时同时禁掉 ComfyUI 自己弹网页
    # （2026-10-28 补齐：原先只控制 ComfyHub 侧，ComfyUI 仍会自弹）
    if not inst.get("auto_open_browser", True):
        cmd.append("--disable-auto-launch")
    # 自定义输出/输入目录
    if inst.get("output_dir"):
        cmd += ["--output-directory", inst["output_dir"]]
    if inst.get("input_dir"):
        cmd += ["--input-directory", inst["input_dir"]]
    # 启动模式（2026-10-31）：官方基座隔离——custom_nodes/user/output 落
    # base_directory，模型目录单独共享。结构化字段，避免 extra_args 带空格
    # 路径被 split() 拆碎。旧内核无 --base-directory 时 profiles.prepare_launch
    # 走 junction 换头，不设这两个字段。
    if inst.get("base_directory"):
        cmd += ["--base-directory", inst["base_directory"]]
    # 模型目录：实例设置（models_dir）优先；旧 profile 线用 models_directory。
    # 只有跟「<comfyui_path>/models」默认值不同才显式传参——默认情况不改变启动命令，
    # 免得某些整合包对 --models-directory 的解析差异搞出意外（2026-11-07）。
    _md = (inst.get("models_dir") or "").strip() or (inst.get("models_directory") or "").strip()
    if _md:
        _dflt = os.path.join(inst.get("comfyui_path") or "", "models")
        if not _dflt or os.path.normpath(_md) != os.path.normpath(_dflt):
            cmd += ["--models-directory", _md]
    # 单实例挂载模型目录（2026-11-08 用户要求）：实例设置页「模型目录」下面
    # 那个开关开启时，把 ComfyHub 生成的 yaml 传给 ComfyUI（类别按标准子目录名
    # 自动映射）。和「共享外挂库」可叠加——ComfyUI 的该参数是 append 语义。
    extra = _instance_extra_config(inst)
    if extra:
        cmd += ["--extra-model-paths-config", extra]
    # 共享外挂模型库（2026-11-08）：设置里开启后，把 ComfyHub 托管的 yaml 传给
    # ComfyUI——一份挂载表所有实例通用，不用给每个实例写 extra_model_paths.yaml。
    # 实例自己目录里的 yaml 由 ComfyUI 自行加载，两者叠加。
    shared = _shared_extra_config()
    if shared:
        cmd += ["--extra-model-paths-config", shared]
    # 额外参数（实例）
    s = (inst.get("extra_args") or "").strip()
    if s:
        cmd += s.split()
    return cmd


def _instance_extra_config(inst) -> str:
    """单实例挂载 yaml 路径（未启用/目录无效返回空串）

    顺带做自愈：开关关了就删掉上次生成的 yaml，别留僵尸挂载让 ComfyUI 认出
    已经取消的模型库。
    """
    try:
        from app import model_paths
        return model_paths.sync_instance_config(inst or {})
    except Exception:                    # 任何异常都不能拦住启动
        return ""


def _shared_extra_config() -> str:
    """共享外挂库 yaml 路径（未启用/配置不全返回空串）

    每次启动顺手 sync 一次：配置改了没点保存、或 yaml 被手动删掉时能自愈。
    """
    try:
        from app.config import load_config
        from app import model_paths
        st = load_config().get("settings") or {}
        if not st.get("shared_models_enabled"):
            return ""
        root = str(st.get("shared_models_root") or "").strip()
        if not root or not os.path.isdir(root):
            return ""
        return model_paths.sync_shared_config(root, True)
    except Exception:                    # 任何异常都不能拦住启动
        return ""


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
        self._probe = None          # 就绪异步探测的 QTcpSocket（防并发堆探测）

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
        # ★ 启动前环境预检（2026-11-09）：torch 构建 vs 引擎模式。
        #   不匹配时 ComfyUI 在 import 阶段就崩，这里拦下给修复引导。
        _why = env_block_reason(inst)
        if _why:
            self.log_line.emit(f"[ComfyHub] ❌ 已阻止启动（环境预检未通过）：{_why}")
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
        # ★ 端口被占用 → 自动顺延下一个空闲端口（最多顺延 10 个）
        #   ComfyUI 本体遇到占用是直接崩退出、不会跳（server.py TCPSite 绑定异常），
        #   由管理器在启动前替它选好空闲端口；打开网页/就绪提示按实际端口走
        port = int(inst.get("port") or 8188)
        # 一次 netstat 快照判占用（2026-11-02：连打十几次 connect_ex 在
        # 「关闭端口不秒拒」的机器上会冻结 UI 数秒，改为一次子进程取全景）
        listening = {}
        for _pid, _p in _netstat_listening():
            listening.setdefault(_p, []).append(_pid)
        if port in listening:
            orig = port
            holder = ",".join(str(x) for x in listening.get(orig, [])) or "?"
            port = next((p for p in range(orig + 1, orig + 11)
                         if p not in listening), None)
            if port is None:
                self.log_line.emit(
                    f"[ComfyHub] ❌ 端口 {orig} 起连续 10 个均被占用，"
                    "请手动更换启动端口。")
                return
            self.log_line.emit(
                f"[ComfyHub] ⚠️ 配置端口 {orig} 已被占用（PID {holder}），"
                f"已自动顺延到 {port}；打开网页将按实际端口 {port} 走。")
        self._port = port
        self._ready_announced = False
        self._browser_on_ready = bool(inst.get("auto_open_browser", True))
        cmd = build_command(inst, port=port)

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
        # Huggingface 离线模式（环境变量，非命令行参数）
        if inst.get("hf_offline"):
            env.insert("HF_HUB_OFFLINE", "1")
            env.insert("TRANSFORMERS_OFFLINE", "1")
            self.log_line.emit("[ComfyHub] 已开启 Huggingface 离线模式（不联网加载模型）")
        # 启动模式 L1 依赖 overlay：PYTHONPATH 前置 site_overlay 压过共享
        # venv 同名包（profiles.prepare_launch 注入）
        for k, v in (inst.get("env") or {}).items():
            env.insert(k, v)
        if inst.get("env", {}).get("PYTHONPATH"):
            self.log_line.emit("[ComfyHub] 已启用模式依赖 overlay（PYTHONPATH）")

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
        if self._probe is not None:
            try:
                self._probe.abort()     # 中止未归的就绪探测（回调自带防重入守卫）
            except Exception:
                pass
            self._probe = None
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
        """就绪探测（2026-11-02 异步化）：QTcpSocket 信号回调判定连通，
        绝不在 UI 线程同步 connect——「关闭端口不秒拒」的机器上同步探测
        每次要堵满超时（实测 0.5s/次），ComfyUI 启动期间界面会被卡死。"""
        if self._ready_announced or not self.is_running:
            return
        if self._probe is not None:
            return                      # 上一轮探测未归，跳过本轮（不并发堆探测）
        from PySide6.QtNetwork import QTcpSocket
        s = QTcpSocket(self)
        self._probe = s
        state = {"done": False}

        def _done(ok: bool):
            if state["done"]:           # connected/失败信号/2s 放弃三路可能竞争
                return
            state["done"] = True
            if self._probe is s:
                self._probe = None
            try:
                s.abort()
            except Exception:
                pass
            s.deleteLater()
            if ok and self.is_running and not self._ready_announced:
                self._ready_announced = True
                self._ready_timer.stop()
                self.log_line.emit(f"[ComfyHub] 服务就绪: http://127.0.0.1:{self._port}")
                if self._browser_on_ready:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{self._port}")

        s.connected.connect(lambda: _done(True))
        s.errorOccurred.connect(lambda *_a: _done(False))
        s.connectToHost("127.0.0.1", int(self._port))
        # 网络过滤驱动可能拖住失败信号：2 秒没结论就放弃本轮，下轮再试
        QTimer.singleShot(2000, lambda: _done(False))

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
