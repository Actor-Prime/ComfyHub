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
    # 公共加速（200-400KB/s；偶发超时/断流会被自动换源）
    # 注：gitee.com/mirrors 的 archive 地址对程序请求只返回 HTML 登录页，不可用
    "https://gh-proxy.com/https://codeload.github.com/comfyanonymous/ComfyUI/zip/refs/heads/master",
    # 用户自定义代理（界面填了才生效；为空时该项会被自动过滤）
    "{proxy}https://codeload.github.com/comfyanonymous/ComfyUI/zip/refs/heads/master",
    # 直连兜底（支持断点续传，速度慢但稳）
    "https://codeload.github.com/comfyanonymous/ComfyUI/zip/refs/heads/master",
]
COMFYUI_MANAGER_URLS = [
    "https://gh-proxy.com/https://codeload.github.com/Comfy-Org/ComfyUI-Manager/zip/refs/heads/main",
    "{proxy}https://codeload.github.com/Comfy-Org/ComfyUI-Manager/zip/refs/heads/main",
    "https://codeload.github.com/Comfy-Org/ComfyUI-Manager/zip/refs/heads/main",
]
MINGIT_VERSION = "2.47.1"
MINGIT_URLS = [
    f"https://registry.npmmirror.com/-/binary/git-for-windows/v{MINGIT_VERSION}.windows.1/MinGit-{MINGIT_VERSION}-64-bit.zip",
    f"https://github.com/git-for-windows/git/releases/download/v{MINGIT_VERSION}.windows.1/MinGit-{MINGIT_VERSION}-64-bit.zip",
]
# PyPI 镜像（安装依赖用）
PYPI_MIRRORS = {
    "阿里云": "https://mirrors.aliyun.com/pypi/simple/",
    "清华": "https://pypi.tuna.tsinghua.edu.cn/simple",
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
# 公共加速代理域名（这些域名证书常过期/自签，需宽松校验；直连仍严格校验）
_PROXY_HOSTS = ("gh-proxy.com", "ghproxy.", "gh.llkk.cc", "ghfast.top",
                "gh.ddlc.top", "ghp.ci", "ghproxy.cc")
_SSL_LOOSE = None


def _ssl_ctx_for(url: str):
    """代理加速域名 → 宽松 SSL（证书过期也能用）；其余 → 默认严格校验"""
    global _SSL_LOOSE
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    if any(h in host for h in _PROXY_HOSTS):
        if _SSL_LOOSE is None:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            _SSL_LOOSE = ctx
        return _SSL_LOOSE
    return None   # None = urllib 默认严格校验


def _http_get(url: str, timeout: int = 60) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ComfyHub/0.2",
    })
    return req


def _sha256_of(path: str) -> str:
    """计算文件 sha256（大文件分块读取）"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unzip(zpath: str, dest: str, strip_root: bool = False):
    """解压 zip 到 dest；strip_root=True 时剥掉压缩包里的顶层目录。
    GitHub/Gitee 的 archive 都是 ComfyUI-master/ 这种单层结构，必须剥离。"""
    import zipfile
    import shutil as _sh
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        prefix = ""
        if strip_root and names:
            first = names[0].split("/", 1)[0]
            if all(nm.split("/", 1)[0] == first for nm in names[:50]):
                prefix = first + "/"
        for nm in names:
            if prefix and not nm.startswith(prefix):
                continue
            rel = nm[len(prefix):] if prefix else nm
            if not rel or rel.endswith("/"):
                if rel:
                    os.makedirs(os.path.join(dest, rel), exist_ok=True)
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(nm) as fsrc, open(target, "wb") as fdst:
                _sh.copyfileobj(fsrc, fdst)


def _probe_range(url: str, timeout: int = 15) -> tuple[bool, int]:
    """探测源是否支持 Range 并返回文件总长（0=未知）"""
    try:
        _kw = {"timeout": timeout}
        _ctx = _ssl_ctx_for(url)
        if _ctx is not None:
            _kw["context"] = _ctx
        req = _http_get(url)
        req.add_header("Range", "bytes=0-0")
        r = urllib.request.urlopen(req, **_kw)
        status = getattr(r, "status", 200)
        cr = r.headers.get("Content-Range", "")
        r.close()
        if status == 206 and "/" in cr:
            total = int(cr.rsplit("/", 1)[-1])
            return True, total
        return False, 0
    except Exception:
        return False, 0


def _download_threaded(url: str, dest: str, size: int, log=print,
                       threads: int = 8, timeout: int = 30) -> bool:
    """分段并发下载（同一 .part 文件按偏移写入）。失败返回 False 交给上层换源。"""
    import threading
    tmp = dest + ".part"
    try:
        with open(tmp, "wb") as f:
            f.truncate(size)          # 预分配，避免多线程 seek 越界
    except OSError as e:
        log(f"  预分配失败: {e}")
        return False

    seg = size // threads + 1
    errors: list = []
    done = [0]
    lock = threading.Lock()
    name = os.path.basename(dest)
    total_mb = size / 1048576

    def worker(i: int):
        start = i * seg
        end = min(start + seg - 1, size - 1)
        if start >= size:
            return
        try:
            _kw = {"timeout": timeout}
            _ctx = _ssl_ctx_for(url)
            if _ctx is not None:
                _kw["context"] = _ctx
            req = _http_get(url)
            req.add_header("Range", f"bytes={start}-{end}")
            r = urllib.request.urlopen(req, **_kw)
            if getattr(r, "status", 200) != 206:
                raise IOError("该源不支持分段")
            with open(tmp, "r+b") as f:
                f.seek(start)
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    with lock:
                        done[0] += len(chunk)
            r.close()
        except Exception as e:
            with lock:
                errors.append(f"段{i + 1}: {type(e).__name__}")

    ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
    import time as _t
    [t.start() for t in ts]
    # 总超时：按最慢 20KB/s 估算 + 90 秒余量，超时即放弃（避免卡死在个别段）
    _deadline = _t.time() + max(90, size / 1024 / 20 + 90)
    _last = 0.0
    _win_start = _t.time()
    _win_bytes = 0
    while any(t.is_alive() for t in ts):
        _t.sleep(0.5)
        _now = _t.time()
        if _now > _deadline:
            log(f"  分段下载总超时（{done[0] / 1048576:.1f}/{total_mb:.1f}MB），放弃该源")
            return False
        # 有效速度判定：每 45 秒结算，增长不足 2MB（≈45KB/s）就放弃换单连接
        if _now - _win_start >= 45.0:
            _grow = done[0] - _win_bytes
            if _grow < 2 * 1048576:
                log(f"  分段并发被限速（45 秒仅 {_grow / 1048576:.1f}MB），"
                    f"放弃并回退单连接")
                return False
            _win_start, _win_bytes = _now, done[0]
        if _now - _last >= 2.0:
            _last = _now
            cur = min(done[0], size)
            log(f"  ↓ {name}: {cur / 1048576:.1f}/{total_mb:.1f} MB "
                f"({cur * 100 // max(size, 1)}%) ×{threads} 线程")
    [t.join(timeout=2) for t in ts]

    def _drop_partial():
        try:
            os.remove(tmp)
        except OSError:
            pass

    if errors:
        log(f"  分段下载失败: {', '.join(errors[:3])}")
        _drop_partial()      # 稀疏文件含空洞，不能作为续传基础
        return False
    got = os.path.getsize(tmp)
    if got != size:
        log(f"  分段下载尺寸不符 {got}/{size}")
        _drop_partial()
        return False
    os.replace(tmp, dest)
    log(f"下载完成: {name} ({got / 1048576:.1f} MB，{threads} 线程)")
    return True


def download_multi(url_or_tpl: str | list, dest: str, log=print,
                   max_retry: int = 8, proxy: str = "", expected_sha: str | None = None,
                   timeout: int = 20) -> bool:
    """多源下载 + 断点续传。

    设计要点（踩过的坑）：
    · **选定源后下到底，绝不中途按速度切换**——切换 + 不支持 Range 的源会导致进度清零，
      形成"0~3MB 无限循环"（v0.5.73 的死循环事故）
    · 只有当前源**抛异常/断开**才换下一个源；有进度时，不支持续传(Range)的源直接跳过
    · 所有源都失败且全都不支持续传 → 清空 .part 用第一个可用源从头下（只做一次）
    """
    if isinstance(url_or_tpl, str):
        urls = [url_or_tpl]
    else:
        # proxy 为空时跳过 {proxy} 模板项（否则与直连重复，白占第一顺位）
        raw = [u for u in url_or_tpl if not ("{proxy}" in u and not proxy)]
        urls = [u.format(proxy=proxy) if "{proxy}" in u else u for u in raw]
    # 去重保序
    _seen = set()
    urls = [u for u in urls if not (u in _seen or _seen.add(u))]
    tmp = dest + ".part"
    name = os.path.basename(dest)
    no_range = set()          # 不支持断点续传的源
    failed_count = {}         # 每个源本轮累计失败次数
    restart_done = False      # 是否已经清空重下过

    for attempt in range(1, max_retry + 1):
        pos = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if expected_sha and pos > 0 and _sha256_of(tmp) == expected_sha:
            os.replace(tmp, dest)
            return True

        last_err = None
        # 有进度时，把候选源按"是否支持续传"排序：codeload 直连在末尾但支持 Range
        _ordered = urls
        if pos > 0:
            _ordered = sorted(urls, key=lambda u: (u in no_range, "codeload.github.com" not in u))
        for ui, url in enumerate(_ordered):
            if pos > 0 and url in no_range:
                continue      # 有进度时，不重头下不支持续传的源
            if failed_count.get(url, 0) >= 2:
                continue      # 该源已连续失败两次，本轮直接跳过（避免空转）
            try:
                headers = {"User-Agent": "Mozilla/5.0 ComfyHub/0.2"}
                if pos > 0:
                    headers["Range"] = f"bytes={pos}-"
                log(f"  下载 {name} ← {url[:78]}"
                    + (f"  (续传 {pos / 1048576:.1f}MB)" if pos else ""))
                # 多线程并发在 codeload 上会被限速到近乎停滞（实测卡在 67%~78%），
                # 故默认关闭，只走稳定的单连接续传。开关保留便于将来换源时启用。
                use_threaded = False
                if pos == 0 and use_threaded:
                    _ok_range, _size = _probe_range(url)
                    if _ok_range and _size > 4 * 1048576:
                        log(f"  该源支持分段，尝试 4 线程并发（共 {_size / 1048576:.1f}MB，"
                            f"被限速会自动回退）")
                        if _download_threaded(url, dest, _size, log=log,
                                              timeout=timeout):
                            if expected_sha and _sha256_of(dest) != expected_sha:
                                log("  sha256 校验失败，删除重试")
                                os.remove(dest)
                                raise IOError("sha256 校验不通过")
                            return True
                        log("  分段下载未成功，回退单连接重下")
                        pos = 0
                        continue      # 直接换下一个源（当前源分段都卡）
                _kw = {"timeout": timeout}
                _ctx = _ssl_ctx_for(url)
                if _ctx is not None:
                    _kw["context"] = _ctx
                req = _http_get(url)
                for k, v in headers.items():
                    req.add_header(k, v)
                r = urllib.request.urlopen(req, **_kw)
                if pos > 0 and getattr(r, "status", 200) != 206:
                    no_range.add(url)
                    log(f"  源{ui + 1} 不支持续传，跳过（保留下载进度）")
                    r.close()
                    continue
                total = pos + int(r.headers.get("Content-Length", 0) or 0)
                mode = "ab" if pos else "wb"
                import time as _t
                _last_log = 0.0
                _first = True
                with open(tmp, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        # ★ 首个数据块校验：必须是 ZIP(PK\x03\x04) 或 GZIP(\x1f\x8b) 或 TAR
                        #   否则说明拿到的是 HTML/错误页（镜像反爬、登录墙等），立刻换源
                        if _first:
                            _first = False
                            if not (chunk[:4] == b"PK\x03\x04" or chunk[:2] == b"\x1f\x8b"
                                    or chunk[:5] == b"ustar"):
                                raise IOError("响应不是压缩包（可能是网页/错误页）")
                        f.write(chunk)
                        _now = _t.time()
                        if _now - _last_log >= 2.0:     # 仅节流输出，不做任何切换判断
                            _last_log = _now
                            _cur = f.tell()
                            if total > 0:
                                log(f"  ↓ {name}: {_cur / 1048576:.1f}/{total / 1048576:.1f} MB "
                                    f"({_cur * 100 // max(total, 1)}%)")
                            else:
                                log(f"  ↓ {name}: {_cur / 1048576:.1f} MB")
                got = os.path.getsize(tmp)
                # 完整性：有 Content-Length 时校验大小，防止连接中断被当成成功
                if total > 0 and got < total:
                    raise IOError(f"下载不完整 {got / 1048576:.1f}/{total / 1048576:.1f}MB")
                if expected_sha and _sha256_of(tmp) != expected_sha:
                    raise IOError("sha256 校验不通过")
                os.replace(tmp, dest)
                log(f"下载完成: {name} ({got / 1048576:.1f} MB)")
                return True
            except Exception as e:
                last_err = e
                failed_count[url] = failed_count.get(url, 0) + 1
                now = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                log(f"  源{ui + 1} 失败（已得 {now / 1048576:.1f}MB）: {type(e).__name__}")

        # 一轮结束：判断是否还有源可用
        pos_now = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        usable = [u for u in urls
                  if not (pos_now > 0 and u in no_range) and failed_count.get(u, 0) < 2]
        if pos_now > 0 and not usable and not restart_done:
            # 有进度但没有任何源能续传 → 清空重下一次（只做一次）
            restart_done = True
            failed_count.clear()
            log("  暂无可用源可续传，清空进度从头下载（仅一次）")
            try:
                os.remove(tmp)
            except OSError:
                pass
            continue
        if pos_now == 0:
            log(f"  第 {attempt} 轮全部源失败: {last_err}")
        import time as _t2
        _t2.sleep(min(2 * attempt, 8))

    return False


# ---------------- 便携 Python ----------------
def install_portable_python(tools_dir: str, log=print, proxy: str = "",
                            force: bool = False) -> str:
    """部署 python-build-standalone 免安装 Python 到 tools_dir\\python

    ★ 原子性保证：先下载并解压到临时目录，验证 pip 可用后才替换正式目录。
      中途失败（断网/包损坏）时旧环境原样保留——绝不出现"旧的删了、新的没装上"。
    force=True 时即使已存在也重装（用于"重新安装便携 Python"）。
    """
    py_dir = os.path.join(tools_dir, "python")
    exe = os.path.join(py_dir, "python.exe")
    if os.path.isfile(exe) and not force:
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
    import shutil as _sh
    stage = os.path.join(tools_dir, "_python_stage")      # 临时落地区
    _sh.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    try:
        with tarfile.open(tgz, "r:gz") as tf:
            for m in tf.getmembers():
                if m.issym() or m.islnk():
                    continue   # Windows 包内 Linux 符号链接成员直接跳过
                tf.extract(m, stage)
        staged_exe = os.path.join(stage, "python", "python.exe")
        if not os.path.isfile(staged_exe):
            raise RuntimeError("解压后未找到 python.exe（压缩包结构异常）")
        # 先在临时目录验证 pip，通过后再替换正式目录
        r2 = run([staged_exe, "-m", "pip", "--version"],
                 capture_output=True, text=True, timeout=120)
        if r2.returncode != 0:
            raise RuntimeError(f"pip 不可用: {(r2.stderr or '')[:200]}")
        # 替换：旧目录改名备份 → 新目录就位 → 删备份；失败则回滚
        backup = None
        if os.path.isdir(py_dir):
            backup = py_dir + "_old"
            _sh.rmtree(backup, ignore_errors=True)
            os.rename(py_dir, backup)
        try:
            os.rename(os.path.join(stage, "python"), py_dir)
        except Exception:
            if backup and os.path.isdir(backup):
                os.rename(backup, py_dir)   # 回滚
            raise
        if backup:
            _sh.rmtree(backup, ignore_errors=True)
        log(f"便携 Python 就绪: {exe}（{(r2.stdout or '').strip()[:60]}）")
        return exe
    finally:
        _sh.rmtree(stage, ignore_errors=True)
        try:
            os.remove(tgz)
        except OSError:
            pass


def portable_pip(python_exe: str) -> str:
    """返回便携环境的 pip 启动参数（python -m pip 最稳）"""
    return python_exe


def uninstall_portable_python(tools_dir: str, log=print) -> bool:
    """卸载便携 Python（只删 python 子目录，不动 MinGit/wheels）。
    2026-10-29 用户需求：重装=先卸载原本的，再装新的。
    返回是否确实删除了目录。"""
    py_dir = os.path.join(tools_dir, "python")
    if not os.path.isdir(py_dir):
        log(f"便携 Python 不存在，无需卸载: {py_dir}")
        return False
    import shutil as _sh
    # 先杀可能残留的 python 进程句柄无从下手——直接删（便携版无后台常驻）
    _sh.rmtree(py_dir, ignore_errors=True)
    # 顺带清掉历史 stage/backup 残留
    for extra in ("_python_stage", "python_old"):
        _sh.rmtree(os.path.join(tools_dir, extra), ignore_errors=True)
    gone = not os.path.isdir(py_dir)
    log(f"已卸载便携 Python: {py_dir}" if gone else f"⚠️ 卸载未完全: {py_dir}")
    return gone


# ---------------- ComfyUI 源码 ----------------
def install_comfyui_source(dest: str, log=print, proxy: str = "") -> str:
    """下载 ComfyUI 源码 zip 并解压到 dest，返回实际目录"""
    if os.path.isfile(os.path.join(dest, "main.py")):
        log(f"ComfyUI 源码已存在: {dest}")
        return dest
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    zpath = dest.rstrip("\\/") + "_src.zip"   # 实例目录同级（不占 C 盘）
    log(f"ComfyUI 源码压缩包下载到: {zpath}")
    ok = download_multi(COMFYUI_ZIP_URLS, zpath, log=log, proxy=proxy)
    if not ok:
        raise RuntimeError(
            "ComfyUI 源码下载失败。\n"
            "常见原因：①网络/DNS 临时故障（getaddrinfo failed）②GitHub 被墙\n"
            "处理：检查网络后重试；仍失败请在「GitHub 代理」填 https://ghproxy.net/ 再试")
    _unzip(zpath, dest, strip_root=True)
    os.remove(zpath)
    log(f"✅ 已清理安装包: {zpath}（解压成功后自动删除）")
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
        _env = dict(os.environ)
        for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            _env.pop(_k, None)
        _flags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1, env=_env,
                         creationflags=_flags)
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


def _free_gb(path: str) -> float | None:
    """返回 path 所在盘的剩余空间（GB）；取不到返回 None"""
    import shutil
    try:
        return shutil.disk_usage(os.path.abspath(path)).free / 1e9
    except OSError:
        return None


def _zero_byte_dlls(pkg_dir: str) -> list:
    """扫描包目录下 0 字节 DLL（磁盘满/下载中断的典型残留）"""
    import glob as _g
    hits = []
    for pat in ("*.dll", "*.pyd"):
        for f in _g.glob(os.path.join(pkg_dir, "**", pat), recursive=True):
            try:
                if os.path.getsize(f) == 0:
                    hits.append(f)
            except OSError:
                pass
    return hits[:5]


def install_torch(python_exe: str, variant: str, log=print,
                  mirror: str = PYPI_MIRRORS["官方"], wheels_dir: str | None = None) -> bool:
    """按变体安装 torch 三件套。
    · 磁盘空间预检：cu 版需约 6GB（解压+安装），不足直接失败并明确提示
      （磁盘满会留下 0 字节 DLL → import 时报 WinError 193，事后极难排查）
    · cu 变体：已装 CPU 版 → 先卸载再装（防混装）；torch 已装但无法 import
      （安装损坏）→ --force-reinstall 强制重装
    · wheels_dir 存在匹配的本地 wheel 时用 --find-links 秒装（免 2.5GB 慢速下载）
    · 安装后 import 自检：pip 成功 ≠ 真的能用，损坏要当场抓住"""
    need_gb = 3.0 if variant == "cpu" else 6.0
    free = _free_gb(os.path.dirname(python_exe) or ".")
    if free is not None and free < need_gb:
        log(f"❌ 磁盘空间不足：{os.path.splitdrive(python_exe)[0] or '目标盘'} "
            f"剩余 {free:.1f} GB，安装 PyTorch（{variant}）需要约 {need_gb:.0f} GB。"
            "请清理磁盘后重试（torch 约 6GB，磁盘满会导致安装损坏）。")
        return False

    pkgs = ["torch", "torchvision", "torchaudio"]
    force_flags: list = []   # 安装损坏时的强制重装参数

    # 检测已装 torch 状态（能否 import + CUDA 版本）
    r = run([python_exe, "-c",
             "import torch;print(getattr(torch.version,'cuda') or 'cpu')"],
            capture_output=True, text=True, timeout=300)
    import_ok = r.returncode == 0
    if import_ok:
        installed_cuda = (r.stdout or "").strip()
        if variant != "cpu" and installed_cuda == "cpu":
            log("检测到已装 torch 为 CPU 版，先卸载避免混装…")
            run([python_exe, "-m", "pip", "uninstall", "-y", *pkgs],
                capture_output=True, text=True, timeout=600)
    else:
        # pip 认为已装、但 import 直接崩（典型：0 字节 DLL / WinError 193）
        err_tail = ((r.stderr or "").strip().splitlines() or ["未知错误"])[-1]
        log(f"⚠️ 检测到 torch 已安装但无法加载（安装损坏）：{err_tail[:140]}")
        zeros = _zero_byte_dlls(
            os.path.join(os.path.dirname(python_exe), "Lib", "site-packages", "torch"))
        if zeros:
            log(f"⚠️ 发现 {len(zeros)} 个 0 字节 DLL（如 {os.path.basename(zeros[0])}）"
                "——通常是磁盘空间不足/下载中断导致，将强制重装修复")
        force_flags = ["--force-reinstall", "--no-deps"]

    if variant == "cpu":
        return pip_install(python_exe, pkgs, mirror, log=log, extra=force_flags)

    idx = TORCH_VARIANTS[variant]["index"]

    # 本地 wheel 缓存加速（如 tools\wheels\torch-2.10.0+cu130-....whl）
    f_links = []
    if wheels_dir and os.path.isdir(wheels_dir):
        for f in os.listdir(wheels_dir):
            if variant in f and f.endswith(".whl"):
                f_links = ["--find-links", wheels_dir]
                log(f"发现本地 wheel 缓存（{f}），跳过大包下载")
                break

    cmd = [python_exe, "-s", "-m", "pip", "install", "--no-warn-script-location",
           "-i", idx, *force_flags, *f_links] + pkgs
    log("$ " + " ".join(cmd[:12]) + (" …" if len(cmd) > 12 else ""))
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=10800,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    tail = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines()
            if "[safe-delete]" not in ln]
    for ln in tail[-10:]:
        log("  " + ln[:160])
    if r.returncode != 0:
        return False

    # ★ 安装后 import 自检：磁盘满时 pip 也可能报成功但留下损坏文件
    r2 = run([python_exe, "-c", "import torch;print(torch.__version__)"],
             capture_output=True, text=True, timeout=300)
    if r2.returncode != 0:
        err_tail = ((r2.stderr or "").strip().splitlines() or ["未知错误"])[-1]
        log(f"❌ torch 安装后自检失败（pip 完成但 import 崩溃）：{err_tail[:140]}")
        zeros = _zero_byte_dlls(
            os.path.join(os.path.dirname(python_exe), "Lib", "site-packages", "torch"))
        if zeros:
            log(f"⚠️ 发现 {len(zeros)} 个 0 字节 DLL（如 {os.path.basename(zeros[0])}）"
                "——请确认磁盘空间充足后重新点「安装」强制重装")
        return False
    log(f"✅ torch {(r2.stdout or '').strip()} 安装完成并通过导入自检")
    return True


def install_comfyui_requirements(python_exe: str, comfyui_path: str, log=print,
                                 mirror: str = PYPI_MIRRORS["阿里云"]) -> bool:
    req = os.path.join(comfyui_path, "requirements.txt")
    if pip_install(python_exe, ["-r", req], mirror, log=log):
        return True
    # 失败（常见：SSL 中断/镜像抖动）→ 换清华源重试一次
    alt = PYPI_MIRRORS.get("清华") or PYPI_MIRRORS.get("上交")
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
    """工具存放根目录：<数据根>\\tools（便携模式=程序目录\\data\\ComfyHub\\tools）
    2026-10-29 用户需求：支持自定义——settings.tools_dir 非空且为绝对路径时优先。
    旧目录不自动搬迁：改路径只影响下次安装/修复（GB 级拷贝中断风险归零，
    旧目录由用户按提示手动删除）。"""
    custom = ""
    try:
        from app.config import CONFIG_PATH
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            custom = str(((json.load(f).get("settings") or {}).get("tools_dir")
                          or "")).strip()
    except Exception:
        custom = ""          # 配置损坏/不存在 → 走默认，绝不因此崩
    if custom and os.path.isabs(custom):
        d = custom
    else:
        from app.paths import data_root
        d = os.path.join(data_root(), "tools")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass   # 只读检测场景返回路径即可；真正安装写入时会明确报错
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
    """步骤2 检测：ComfyUI 源码（main.py + 特征文件，避免误判同名 main.py）"""
    from app.config import is_comfyui_dir
    if is_comfyui_dir(target_dir):
        return "ok", f"ComfyUI 源码已存在: {target_dir}", {"comfyui_path": target_dir}
    return "missing", "未检测到 ComfyUI 源码（main.py + comfy/ 等特征）", {}


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
    """步骤4 检测：torch 是否安装 / CUDA / CPU / 版本
    status 额外支持 "broken"：torch 已安装但无法 import（安装损坏，
    如磁盘满导致 0 字节 DLL / WinError 193）→ 引导强制重装而非"未安装"重装"""
    if not python_exe:
        return "missing", "缺少 Python，无法检测 torch", {}
    try:
        r = run([python_exe, "-c",
                 "import torch;print(torch.__version__);"
                 "print(getattr(torch.version,'cuda') or 'cpu')"],
                capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            err = (r.stderr or "") + (r.stdout or "")
            if "ModuleNotFoundError" in err and "torch" in err:
                return "missing", "未检测到 torch", {}
            # import 崩了但不是"没装" → 安装损坏
            tail = (err.strip().splitlines() or ["未知错误"])[-1]
            return ("broken",
                    f"torch 已安装但无法加载（安装损坏）：{tail[:120]}"
                    " —— 点「强制重装」修复", {})
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
        # ★ 门禁要贴近"真实能启动"：ComfyUI 启动必经的模块（曾因只查 3 个包导致
        #   "验证通过但启动报 No module named tqdm"）
        r = run([python_exe, "-c",
                 "import torch, rich, aiohttp, tqdm, einops, safetensors, yaml, PIL"],
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
