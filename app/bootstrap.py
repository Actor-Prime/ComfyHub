# -*- coding: utf-8 -*-
"""环境引导模块：为最终用户一键装齐 ComfyUI 运行环境
覆盖：显卡检测 / 便携 Python / ComfyUI 源码 / torch / 依赖 / 便携 Git
所有下载内置断点续传 + 国内镜像回退，不依赖外部脚本。
"""
import os
import re
import sys
import ssl
import json
import shutil
import hashlib
import urllib.request
import subprocess

from app.winsub import run
from dataclasses import dataclass, field

# ---------------- 常量 ----------------
# 便携 Python：python-build-standalone 免安装版（完整标准库 + pip，解压即用，
# 不写注册表、无安装器、无 Burn 缓存 —— 彻底绕开官方安装器的静默跳过/挂起问题）
PY_VERSION_TAG = "20261013"
PY_VERSION_STR = "3.13.16"
PY_TARBALL = (f"cpython-{PY_VERSION_STR}+{PY_VERSION_TAG}"
              "-x86_64-pc-windows-msvc-install_only_stripped.tar.gz")
PY_TARBALL_URLS = [
    # npmmirror 二进制镜像（国内首选，实测 7MB/s）
    f"https://registry.npmmirror.com/-/binary/python-build-standalone/"
    f"{PY_VERSION_TAG}/{PY_TARBALL.replace('+', '%2B')}",
    # gh-proxy 加速
    "https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PY_VERSION_TAG}/{PY_TARBALL.replace('+', '%2B')}",
    # 可配置代理 + GitHub 直连兜底
    "{proxy}https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PY_VERSION_TAG}/{PY_TARBALL.replace('+', '%2B')}",
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PY_VERSION_TAG}/{PY_TARBALL.replace('+', '%2B')}",
]
GET_PIP_URLS = [
    "https://mirrors.aliyun.com/pypi/get-pip.py",
    "https://bootstrap.pypa.io/get-pip.py",
]
COMFYUI_ZIP_URLS = [
    # 可配置代理前缀（ghproxy 系，失效时用户可在界面改）
    "{proxy}https://codeload.github.com/comfyanonymous/ComfyUI/zip/refs/heads/master",
    "https://codeload.github.com/comfyanonymous/ComfyUI/zip/refs/heads/master",
]
COMFYUI_MANAGER_URLS = [
    "{proxy}https://codeload.github.com/Comfy-Org/ComfyUI-Manager/zip/refs/heads/main",
    "https://codeload.github.com/Comfy-Org/ComfyUI-Manager/zip/refs/heads/main",
    "https://gitee.com/mirrors/ComfyUI-Manager/repository/archive/main.zip",
]
MINGIT_VERSION = "2.47.1"
MINGIT_URLS = [
    f"https://registry.npmmirror.com/-/binary/git-for-windows/v{MINGIT_VERSION}.windows.1/MinGit-{MINGIT_VERSION}-64-bit.zip",
    f"https://github.com/git-for-windows/git/releases/download/v{MINGIT_VERSION}.windows.1/MinGit-{MINGIT_VERSION}-64-bit.zip",
]
# PyPI 镜像（安装依赖用）
PYPI_MIRRORS = {
    "阿里云": "https://mirrors.aliyun.com/pypi/simple/",
    "上交": "https://mirrors.sjtug.sjtu.edu.cn/pypi/web/simple/",
    "官方": "https://pypi.org/simple/",
}
# torch 变体：驱动版本 → 推荐索引
TORCH_VARIANTS = {
    "cu130": {"index": "https://download.pytorch.org/whl/cu130", "min_driver": 580},
    "cu126": {"index": "https://download.pytorch.org/whl/cu126", "min_driver": 525},
    "cpu":   {"index": "https://pypi.org/simple",               "min_driver": 0},
}


# ---------------- 数据类 ----------------
@dataclass
class GpuInfo:
    vendor: str = "none"        # nvidia / amd / intel / none
    name: str = ""
    vram_mb: int = 0
    driver: int = 0             # NVIDIA 驱动主版本号
    torch_variant: str = "cpu"  # cu130 / cu126 / cpu

    @property
    def label(self) -> str:
        if self.vendor == "nvidia":
            return f"{self.name}（{self.vram_mb}MB，驱动 {self.driver}）→ 推荐 torch {self.torch_variant}"
        if self.vendor in ("amd", "intel"):
            return f"{self.name} → 暂用 CPU 版 torch（加速支持开发中）"
        return "未检测到独立显卡 → CPU 版 torch"


@dataclass
class SetupReport:
    lines: list = field(default_factory=list)
    def log(self, msg: str):
        self.lines.append(msg)
        return msg


# ---------------- 显卡检测 ----------------
_GPU_CACHE = None   # 显卡不会变，进程内只探测一次


def detect_gpu() -> GpuInfo:
    global _GPU_CACHE
    if _GPU_CACHE is None:
        _GPU_CACHE = _detect_gpu_impl()
    return _GPU_CACHE


def _detect_gpu_impl() -> GpuInfo:
    gpu = GpuInfo()
    # 1) NVIDIA：优先 nvidia-smi
    smi = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "nvidia-smi.exe")
    if os.path.isfile(smi):
        try:
            out = run(
                [smi, "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"], capture_output=True, timeout=15, text=True)
            if out.returncode == 0 and out.stdout.strip():
                name, mem, drv = [x.strip() for x in out.stdout.splitlines()[0].split(",")]
                gpu.vendor, gpu.name = "nvidia", name
                try:
                    # nvidia-smi 返回 "12282 MiB"，剥掉单位再转
                    import re as _re
                    gpu.vram_mb = int(_re.sub(r"[^\d]", "", mem) or 0)
                except (ValueError, TypeError):
                    gpu.vram_mb = 0
                try: gpu.driver = int(drv.split(".")[0])
                except (ValueError, IndexError): pass
                # 驱动够新用 cu130，否则 cu126
                if gpu.driver >= TORCH_VARIANTS["cu130"]["min_driver"]:
                    gpu.torch_variant = "cu130"
                else:
                    gpu.torch_variant = "cu126"
                return gpu
        except Exception:
            pass
    # 2) WMI 兜底：AMD / Intel / 老驱动 N 卡
    try:
        import wmi  # 可选依赖
        cards = wmi.WMI().Win32_VideoController()
        for c in cards:
            nm = c.Name or ""
            if "NVIDIA" in nm:
                gpu.vendor, gpu.name = "nvidia", nm
                gpu.torch_variant = "cu126"
                return gpu
            if "AMD" in nm or "Radeon" in nm:
                gpu.vendor, gpu.name = "amd", nm
            elif "Intel" in nm and gpu.vendor == "none":
                gpu.vendor, gpu.name = "intel", nm
    except Exception:
        pass
    return gpu


# ---------------- 下载器（断点续传 + 多源重试） ----------------
def _http_get(url: str, timeout: int = 60) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ComfyHub/0.2",
    })
    return req


def download_multi(url_or_tpl: str | list, dest: str, log=print,
                   max_retry: int = 60, proxy: str = "", expected_sha: str | None = None,
                   timeout: int = 60) -> bool:
    """断点续传下载。url_or_tpl 支持列表（多源轮换）或含 {proxy} 占位的模板。
    返回是否成功。"""
    if isinstance(url_or_tpl, str):
        urls = [url_or_tpl]
    else:
        urls = [u.format(proxy=proxy) if "{proxy}" in u else u for u in url_or_tpl]
    tmp = dest + ".part"
    total = 0
    for attempt in range(1, max_retry + 1):
        pos = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        # 完整性预校验（续传前先看是否已完整）
        if expected_sha and pos > 0:
            if _sha256_of(tmp) == expected_sha:
                os.replace(tmp, dest)
                return True
        last_err = None
        for ui, url in enumerate(urls):
            try:
                headers = {"User-Agent": "Mozilla/5.0 ComfyHub/0.2"}
                if pos > 0:
                    headers["Range"] = f"bytes={pos}-"
                log(f"  下载 {os.path.basename(dest)} ← {url[:80]}")
                r = urllib.request.urlopen(_http_get(url), timeout=timeout)
                if pos > 0 and r.status != 206:
                    pos = 0  # 该源不支持续传，重来
                total = pos + int(r.headers.get("Content-Length", 0) or 0)
                mode = "ab" if pos else "wb"
                import time as _t
                _last_log = 0.0
                _name = os.path.basename(dest)
                with open(tmp, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        # 节流进度输出（每 2 秒一行，避免刷屏）
                        _now = _t.time()
                        if _now - _last_log >= 2.0:
                            _last_log = _now
                            _cur = f.tell()
                            if total > 0:
                                log(f"  ↓ {_name}: {_cur / 1048576:.1f}/{total / 1048576:.1f} MB "
                                    f"({_cur * 100 // max(total, 1)}%)")
                            else:
                                log(f"  ↓ {_name}: {_cur / 1048576:.1f} MB")
                got = os.path.getsize(tmp)
                if expected_sha and _sha256_of(tmp) != expected_sha:
                    raise IOError("sha256 校验不通过")
                os.replace(tmp, dest)
                log(f"下载完成: {os.path.basename(dest)} ({got / 1048576:.1f} MB)")
                return True
            except Exception as e:
                last_err = e
                now = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                log(f"  源{ui + 1} 失败（已得 {now / 1048576:.1f}MB）: {type(e).__name__}")
                # 保留 .part，换下一个源续传
        log(f"  [重试 {attempt}] {last_err}")
    return False


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _unzip(zip_path: str, dest_dir: str, strip_root: bool = False):
    """解压；strip_root=True 时去掉压缩包顶层目录"""
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if strip_root and names and "/" in names[0]:
            root = names[0].split("/")[0]
            z.extractall(dest_dir)
            src = os.path.join(dest_dir, root)
            # 把 root 内的文件移到 dest_dir
            for item in os.listdir(src):
                shutil.move(os.path.join(src, item), os.path.join(dest_dir, item))
            os.rmdir(src)
        else:
            z.extractall(dest_dir)


# ---------------- 便携 Python ----------------
def install_portable_python(tools_dir: str, log=print, proxy: str = "") -> str:
    """部署 python-build-standalone 免安装 Python 到 tools_dir\\python
    （完整标准库 + 自带 pip；不写注册表、无安装器，返回 python.exe 路径）"""
    py_dir = os.path.join(tools_dir, "python")
    exe = os.path.join(py_dir, "python.exe")
    if os.path.isfile(exe):
        log(f"便携 Python 已存在: {exe}")
        return exe
    os.makedirs(tools_dir, exist_ok=True)
    tgz = os.path.join(tools_dir, PY_TARBALL)
    log(f"下载便携 Python {PY_VERSION_STR}（21MB，免安装版）…")
    ok = download_multi(PY_TARBALL_URLS, tgz, log=log, proxy=proxy)
    if not ok:
        raise RuntimeError("便携 Python 下载失败（可尝试在设置中更换 GitHub 代理）")
    log("解压中…")
    import tarfile
    with tarfile.open(tgz, "r:gz") as tf:
        for m in tf.getmembers():
            if m.issym() or m.islnk():
                continue   # Windows 包内 Linux 符号链接成员直接跳过
            tf.extract(m, tools_dir)
    os.remove(tgz)
    if not os.path.isfile(exe):
        raise RuntimeError("解压后未找到 python.exe（压缩包结构异常）")
    # 验证 pip 可用
    r2 = run([exe, "-m", "pip", "--version"],
             capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        raise RuntimeError(f"pip 不可用: {(r2.stderr or '')[:200]}")
    log(f"便携 Python 就绪: {exe}（{(r2.stdout or '').strip()[:60]}）")
    return exe


def portable_pip(python_exe: str) -> str:
    """返回便携环境的 pip 启动参数（python -m pip 最稳）"""
    return python_exe


# ---------------- ComfyUI 源码 ----------------
def install_comfyui_source(dest: str, log=print, proxy: str = "") -> str:
    """下载 ComfyUI 源码 zip 并解压到 dest，返回实际目录"""
    if os.path.isfile(os.path.join(dest, "main.py")):
        log(f"ComfyUI 源码已存在: {dest}")
        return dest
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    zpath = dest.rstrip("\\/") + "_src.zip"
    ok = download_multi(COMFYUI_ZIP_URLS, zpath, log=log, proxy=proxy)
    if not ok:
        raise RuntimeError(
            "ComfyUI 源码下载失败。\n"
            "常见原因：①网络/DNS 临时故障（getaddrinfo failed）②GitHub 被墙\n"
            "处理：检查网络后重试；仍失败请在「GitHub 代理」填 https://ghproxy.net/ 再试")
    _unzip(zpath, dest, strip_root=True)
    os.remove(zpath)
    log(f"ComfyUI 源码就绪: {dest}")
    return dest


def install_comfyui_manager(comfyui_path: str, log=print, proxy: str = "") -> str:
    """全新安装后附带装 ComfyUI-Manager（官方插件商店，custom_nodes/ComfyUI-Manager）"""
    dest = os.path.join(comfyui_path, "custom_nodes", "ComfyUI-Manager")
    if os.path.isdir(dest) and os.listdir(dest):
        log("ComfyUI-Manager 已存在，跳过")
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    zpath = os.path.join(comfyui_path, "custom_nodes", "_manager.zip")
    ok = download_multi(COMFYUI_MANAGER_URLS, zpath, log=log, proxy=proxy)
    if not ok:
        log("⚠️ ComfyUI-Manager 下载失败（不影响 ComfyUI 使用，可在插件管理页手动安装）")
        return ""
    try:
        _unzip(zpath, dest, strip_root=True)
        os.remove(zpath)
        log("ComfyUI-Manager 已安装（重启 ComfyUI 后在网页里出现 Manager 按钮）")
        return dest
    except Exception as e:
        log(f"⚠️ ComfyUI-Manager 解压失败: {e}")
        return ""


# ---------------- pip / torch / 依赖 ----------------
def pip_install(python_exe: str, pkgs: list, index_url: str, log=print,
                extra: list | None = None, timeout: int = 5400) -> bool:
    """流式执行 pip：逐行实时输出（装依赖可能要几分钟，必须可见进度）。
    --retries/--timeout 限制单包卡顿；--progress-bar off 避免 \r 进度条阻塞读取。"""
    import subprocess as _sp
    import time as _time
    cmd = [python_exe, "-s", "-m", "pip", "install", "--no-warn-script-location",
           "--retries", "3", "--timeout", "60", "--progress-bar", "off",
           "-i", index_url] + pkgs
    if extra:
        cmd += extra
    log("$ " + " ".join(cmd[:12]) + (" …" if len(cmd) > 12 else ""))
    try:
        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1)
    except Exception as e:
        log(f"❌ 无法启动 pip: {e}")
        return False
    start = _time.time()
    tail = []
    while True:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            if _time.time() - start > timeout:
                proc.kill()
                log(f"⏱ pip 超过 {timeout // 60} 分钟未完成，已终止")
                return False
            _time.sleep(0.05)
            continue
        line = line.strip()
        if not line or "[safe-delete]" in line:
            continue
        tail = (tail + [line])[-12:]
        log("  " + line[:160])
    ok = proc.returncode == 0
    if not ok:
        log("❌ pip 退出码: {}（最后输出见上）".format(proc.returncode))
    return ok


def install_torch(python_exe: str, variant: str, log=print,
                  mirror: str = PYPI_MIRRORS["官方"], wheels_dir: str | None = None) -> bool:
    """按变体安装 torch 三件套。
    cu 变体：先检测已装 torch 是否为 CPU 版——是则先卸载再装（防止 CPU/CUDA 混装）；
    wheels_dir 存在匹配的本地 wheel 时用 --find-links 秒装（免 2.5GB 慢速下载）。"""
    if variant == "cpu":
        return pip_install(python_exe, ["torch", "torchvision", "torchaudio"],
                           mirror, log=log)
    idx = TORCH_VARIANTS[variant]["index"]
    pkgs = ["torch", "torchvision", "torchaudio"]

    # 检测已装 torch 的 CUDA 状态
    r = run([python_exe, "-c",
             "import torch;print(getattr(torch.version,'cuda') or 'cpu')"],
            capture_output=True, text=True, timeout=180)
    installed_cuda = (r.stdout or "").strip()
    has_cpu_torch = installed_cuda == "cpu"   # torch 存在但无 CUDA
    if has_cpu_torch:
        log("检测到已装 torch 为 CPU 版，先卸载避免混装…")
        run([python_exe, "-m", "pip", "uninstall", "-y", *pkgs],
            capture_output=True, text=True, timeout=600)

    # 本地 wheel 缓存加速（如 tools\wheels\torch-2.10.0+cu130-....whl）
    f_links = []
    if wheels_dir and os.path.isdir(wheels_dir):
        for f in os.listdir(wheels_dir):
            if variant in f and f.endswith(".whl"):
                f_links = ["--find-links", wheels_dir]
                log(f"发现本地 wheel 缓存（{f}），跳过大包下载")
                break

    cmd = [python_exe, "-s", "-m", "pip", "install", "--no-warn-script-location",
           "-i", idx, *f_links] + pkgs
    log("$ " + " ".join(cmd[:12]) + (" …" if len(cmd) > 12 else ""))
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=10800)
    tail = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines()
            if "[safe-delete]" not in ln]
    for ln in tail[-10:]:
        log("  " + ln[:160])
    return r.returncode == 0


def install_comfyui_requirements(python_exe: str, comfyui_path: str, log=print,
                                 mirror: str = PYPI_MIRRORS["阿里云"]) -> bool:
    req = os.path.join(comfyui_path, "requirements.txt")
    if pip_install(python_exe, ["-r", req], mirror, log=log):
        return True
    # 失败（常见：SSL 中断/镜像抖动）→ 换清华源重试一次
    alt = PYPI_MIRRORS["清华"]
    if alt and alt != mirror:
        log(f"⚠️ 首次安装失败，自动换镜像重试：{alt}")
        if pip_install(python_exe, ["-r", req], alt, log=log):
            return True
    log("❌ 依赖安装失败：网络中断或镜像不可用。可更换 PyPI 镜像后重试，"
        "或直接点「再次检测」重走本步。")
    return False


# ---------------- 便携 Git（MinGit） ----------------
def find_git() -> str | None:
    for c in (r"C:\Program Files\Git\cmd\git.exe",
              r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if os.path.isfile(c):
            return c
    from shutil import which
    p = which("git")
    if p:
        return p
    # ComfyHub 自带便携 Git
    cand = os.path.join(tools_root(), "MinGit", "cmd", "git.exe")
    return cand if os.path.isfile(cand) else None


def tools_root() -> str:
    """工具存放根目录：%APPDATA%\\ComfyHub\\tools"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ComfyHub", "tools")
    os.makedirs(d, exist_ok=True)
    return d


def install_min_git(log=print) -> str:
    """下载便携 MinGit，返回 git.exe 路径"""
    existing = find_git()
    if existing:
        log(f"Git 已可用: {existing}")
        return existing
    root = tools_root()
    dst = os.path.join(root, "MinGit")
    zpath = os.path.join(root, "MinGit.zip")
    ok = download_multi(MINGIT_URLS, zpath, log=log)
    if not ok:
        raise RuntimeError("MinGit 下载失败")
    _unzip(zpath, dst)
    os.remove(zpath)
    exe = os.path.join(dst, "cmd", "git.exe")
    if not os.path.isfile(exe):
        raise RuntimeError("MinGit 解压异常")
    log(f"便携 Git 就绪: {exe}")
    return exe


# ---------------- 一键全流程 ----------------


# ---------------- 步进式安装：每步检测函数 ----------------
# 返回 (status, info, payload)
# status: "ok"=已就绪建议跳过 / "missing"=未安装 / "cpu"=CPU 版建议更新 / "outdated"=旧版本

def detect_python_step(python_exe, tools_dir):
    """步骤1 检测：用户指定解释器 或 便携 Python"""
    if python_exe and os.path.isfile(python_exe):
        return "ok", f"使用指定解释器: {python_exe}", {"python_path": python_exe}
    std = os.path.join(tools_dir, "python", "python.exe")
    if os.path.isfile(std):
        return ("ok", f"ComfyHub 共享便携 Python（所有实例共用，装一次全用）: {std}",
                {"python_path": std})
    return "missing", "未检测到可用的 Python 解释器", {}


def detect_source_step(target_dir):
    """步骤2 检测：ComfyUI 源码"""
    if os.path.isfile(os.path.join(target_dir, "main.py")):
        return "ok", f"ComfyUI 源码已存在: {target_dir}", {"comfyui_path": target_dir}
    return "missing", "未检测到 ComfyUI 源码（main.py）", {}


_REQ_MARKS = ["rich", "aiohttp", "kornia", "yaml",
              "comfyui_frontend_package", "comfyui_workflow_templates"]


def detect_requirements_step(python_exe, comfyui_path):
    """步骤3 检测：标志性依赖包（全部在=ok，缺=missing）"""
    if not python_exe:
        return "missing", "缺少 Python，无法检测依赖", {}
    code = ";".join(f"import {m}" for m in _REQ_MARKS)
    try:
        r = run([python_exe, "-c", code], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return "ok", f"核心依赖已齐备（{_len_marks(r)}）——依据解释器: {python_exe}", {}
        return "missing", f"检测到依赖缺失（依据解释器: {python_exe}）", {}
    except Exception as e:
        return "missing", f"依赖检测失败: {e}", {}


def _len_marks(r):
    return "/".join(_REQ_MARKS)


def detect_torch_step(python_exe):
    """步骤4 检测：torch 是否安装 / CUDA / CPU / 版本"""
    if not python_exe:
        return "missing", "缺少 Python，无法检测 torch", {}
    try:
        r = run([python_exe, "-c",
                 "import torch;print(torch.__version__);"
                 "print(getattr(torch.version,'cuda') or 'cpu')"],
                capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            return "missing", "未检测到 torch", {}
        lines = (r.stdout or "").strip().splitlines()
        ver = lines[0].strip() if lines else "?"
        cuda = lines[1].strip() if len(lines) > 1 else "cpu"
        if cuda == "cpu":
            return "cpu", f"已装 torch {ver}（CPU 版）——建议更新为 CUDA 版以启用显卡加速", {}
        return "ok", f"已装 torch {ver}（CUDA {cuda}）——已就绪", {}
    except Exception as e:
        return "missing", f"torch 检测失败: {e}", {}




def verify_comfyui_runnable(python_exe, comfyui_path=None):
    """最终验证：ComfyUI 是否真的能运行。
    返回 (ok: bool, info: str)。检查：解释器存在 + torch/核心依赖可 import + main.py 存在"""
    if not python_exe or not os.path.isfile(python_exe):
        return False, "未找到 Python 解释器"
    try:
        r = run([python_exe, "-c", "import torch, rich, aiohttp"],
                capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            tail = ((r.stderr or "") or "").strip().splitlines()
            why = tail[-1][:120] if tail else "import 校验未通过"
            return False, f"核心依赖缺失（{why}）"
    except Exception as e:
        return False, f"验证执行失败: {e}"
    if comfyui_path and not os.path.isfile(os.path.join(comfyui_path, "main.py")):
        return False, "ComfyUI 源码（main.py）不存在"
    return True, "验证通过：Python + 核心依赖齐备，ComfyUI 可运行"


def full_setup(target_dir: str, python_exe: str | None, mirror_name: str,
               proxy: str, log=print, install_torch_flag: bool = True,
               progress=None) -> dict:
    """完整环境安装流程。
    python_exe 为 None 时自动安装便携 Python。
    返回 {comfyui_path, python_path} 或抛异常。
    progress: 可选回调 (step, total) 用于进度条。
    """
    steps_total = 4 if install_torch_flag else 3
    mirror = PYPI_MIRRORS.get(mirror_name, PYPI_MIRRORS["阿里云"])
    result = {}

    # Step 1: Python
    if progress: progress(1, steps_total)
    log(f"── 步骤 1/{steps_total}：准备 Python 环境 ──")
    py = python_exe or install_portable_python(tools_root(), log=log, proxy=proxy)
    result["python_path"] = py

    # Step 2: 源码
    if progress: progress(2, steps_total)
    log(f"── 步骤 2/{steps_total}：下载 ComfyUI 源码 ──")
    cpath = install_comfyui_source(target_dir, log=log, proxy=proxy)
    result["comfyui_path"] = cpath

    # Step 3: 依赖
    if progress: progress(3, steps_total)
    log(f"── 步骤 3/{steps_total}：安装 ComfyUI 依赖（{mirror_name} 镜像）──")
    if not install_comfyui_requirements(py, cpath, log=log, mirror=mirror):
        raise RuntimeError("依赖安装失败，请查看日志后重试")

    # Step 4: torch
    if install_torch_flag:
        if progress: progress(4, steps_total)
        log("── 步骤 4/4：检测显卡并安装 PyTorch ──")
        gpu = detect_gpu()
        log(f"显卡: {gpu.label}")
        log(f"开始安装 torch（{gpu.torch_variant}），CUDA 包约 1.5~2.5GB，请耐心等待…")
        ok = install_torch(py, gpu.torch_variant, log=log, mirror=mirror,
                           wheels_dir=os.path.join(tools_root(), "wheels"))
        if not ok:
            log("CUDA 版 torch 安装失败，回退 CPU 版（功能可用但速度慢）")
            install_torch(py, "cpu", log=log, mirror=mirror)
        # GPU 加速验证
        try:
            r = run([py, "-c",
                "import torch;print('CUDA_OK' if torch.cuda.is_available() else 'CUDA_NO')"],
                capture_output=True, text=True, timeout=120)
            out = (r.stdout or "").strip()
            log(f"torch 安装完成，GPU 加速: {'✅ 可用' if 'CUDA_OK' in out else '❌ 不可用（使用 CPU 模式）'}")
        except Exception:
            log("torch 验证跳过")
    return result
