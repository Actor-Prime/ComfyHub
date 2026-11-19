# -*- coding: utf-8 -*-
"""本地 LLM 组件（可选安装）：llama.cpp 推理服务器 + GGUF 模型。

设计要点（2026-11-15 用户需求：作为可选插件，别占安装包体积）
    · **不进安装包**：二进制 24 MB + 模型 0.4~1.1 GB 全部按需下载，默认不启用；
    · **二进制必须校验 sha256**——下载后要执行它，防篡改是硬要求；
    · 模型走 hf-mirror（国内直连），复用 bootstrap.download_multi 的断点续传；
    · **进程隔离**：llama-server 是独立子进程，崩了不拖主程序；退出时回收；
    · 默认 `-ngl 0`（纯 CPU）——GPU 会和 ComfyUI 抢显存，桌面助手不值得抢。

对外接口：
    is_installed() / download_binary() / remove_installed()
    MODELS / get_model() / model_file() / download_model()
    LocalServer().start() / stop() / base_url
"""
import collections
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile

from app.paths import data_root

COMPONENT_ID = "local_llm"
COMPONENT_VERSION = "1.0.0"
DEFAULT_PORT = 8089          # 固定端口便于把 ai_base_url 写进配置；占用时自动顺延
_REL = os.path.join("extras", COMPONENT_ID)

# 上下文长度（2026-11-16 用户报「请求被拒绝 400：4605 tokens exceeds 4096」）
# 4096 是 llama.cpp 的默认值，而我们这边光「22 个工具 schema」就占了约 2100 tokens，
# 加上系统提示 + 技能 + 历史，第一轮就顶穿。实测 8192 启动只要 2.1 秒、KV 约 1GB
# （2B 模型 28 层 GQA，纯 CPU 完全吃得下），所以默认抬到 8192；
# 启动失败（老机器内存不够）会自动往下退到 4096。
DEFAULT_CTX = 8192
CTX_CHOICES = (4096, 8192, 16384)

# 二进制包按推理后端分两个（2026-11-19 用户需求：兼容 AMD 显卡和核显）。
# 体积与 sha256 为实测值，换版本必须同步更新。
#
# 为什么 AMD/Intel 用 Vulkan 而不是 ROCm / SYCL：
#   · Vulkan 包 38MB，跨厂商（AMD / Intel / NVIDIA 通吃），核显与老卡都能用，
#     只要驱动支持 Vulkan 1.2+——这是覆盖率最高的兜底；
#   · ROCm 包 280MB 且依赖 HIP runtime，官方构建的 gfx 目标列表里没有 gfx1103
#     （780M）这类核显，装上去大概率直接不认；
#   · SYCL 包 172MB 只为 Intel 独显服务，性价比低。
# NVIDIA 仍默认走 CPU 包：本地助手没必要跟 ComfyUI 抢显存（见文件头设计要点），
# 想要 GPU 的用户在设置里手动切 Vulkan 即可。
BIN_VARIANTS = {
    "cpu": {
        "label": "CPU",
        "name": "llama-b12383-bin-win-cpu-x64.zip",
        "size": 25124601,
        "sha256": "ad5bf0d826ea1f39aaf9721b7e26fde9be0bef5d9b69b9ba88c2d083b9a3bca7",
        "urls": [
            "https://github.com/ggml-org/llama.cpp/releases/download/b12383/"
            "llama-b12383-bin-win-cpu-x64.zip",
        ],
    },
    "vulkan": {
        "label": "Vulkan",
        "name": "llama-b12383-bin-win-vulkan-x64.zip",
        "size": 38118260,
        "sha256": "bb6c4a8e51618269a063a87a1ecdee465d76fea483b7ee1824bb1f92ce997c0b",
        "urls": [
            "https://github.com/ggml-org/llama.cpp/releases/download/b12383/"
            "llama-b12383-bin-win-vulkan-x64.zip",
        ],
    },
}
DEFAULT_BACKEND = "cpu"
# 兼容旧引用（bootstrap / UI 里按 BIN["name"] 取过包名）
BIN = BIN_VARIANTS[DEFAULT_BACKEND]


# ------------------------------------------------- 后端选择（2026-11-19）
# 两个纯函数，不碰磁盘也不调 WMI，方便单测。真机探测结果由 bootstrap.detect_gpu()
# 注入，这样本模块不必反向依赖 bootstrap（避免循环 import）。
def pick_binary_backend(vendor: str = "", is_igpu: bool = False) -> str:
    """按显卡厂商决定装哪个二进制包 → "cpu" / "vulkan"

    规则（有实测依据，见 BIN_VARIANTS 上方注释）：
      · AMD / Intel   → vulkan：Vulkan 是唯一同时覆盖独显 + 核显 + 老卡的路径
      · NVIDIA        → cpu：CUDA 包 200MB，且 GPU 会和 ComfyUI 抢显存，
                        桌面助手不值得抢；想用 GPU 的用户可在设置里手动切
      · 无显卡        → cpu
    is_igpu 目前不影响选包（核显照样用 Vulkan 包），保留参数是为了将来按核显
    分支时不用改调用方签名。
    """
    v = str(vendor or "").strip().lower()
    if v in ("amd", "intel"):
        return "vulkan"
    return "cpu"


def pick_ngl(vendor: str = "", is_igpu: bool = False,
             backend: str = DEFAULT_BACKEND) -> int:
    """决定 `-ngl`（卸载到 GPU 的层数）→ 99 = 全卸载，0 = 纯 CPU

    **核显必须返回 0**，这是实测结论而不是保守估计：RX 9070 XT 同机对比，
    走独显 pp128 427 t/s / tg64 7.83 t/s；同一工作负载改走核显只剩
    pp128 31 t/s / tg64 3.04 t/s——prompt 处理慢到 1/12，比纯 CPU 还差。
    核显共享系统内存、没有独立带宽，Vulkan 的 transfer 开销吃掉全部收益。
    """
    if str(backend or "").lower() != "vulkan":
        return 0                     # CPU 包没有 GPU 后端，给了也没用
    if is_igpu:
        return 0                     # 核显：显式不卸载（见上面实测）
    return 99                        # 独显：全量卸载

# 模型清单：体积为实测 Content-Length。sha256 留空 = 只校验长度（模型来自
# 官方仓库，且 GGUF 太大不便逐一固定；仍校验"下完大小是否等于声明值"）
# vision=True 的模型必须带 mmproj（视觉投影），llama-server 用 --mmproj 加载；
# 缺它就只能当纯文本模型用，看图直接答非所问。
#
# 2026-11-16 用户口径：「不能识图的也删掉」。清单只留**能看图**的模型：
#   · Qwen3-0.6B（396MB）：完整提示 0/2、精简提示 1/18 → 撑不住 22 个工具
#   · Qwen3-1.7B（1.1GB）：精简提示工具 18/18，但**不能看图**
#   · Qwen3-1.7B 创作向（1.1GB）：同上，且工具调用未实测
#   · 全删。VL-2B 是功能超集（看图 + 工具 18/18），留着纯文本版只是白占 2.2GB。
# 以后要加模型，必须同时满足「能看图」+「真实链路工具调用达标」，否则不进表。
MODELS = [
    {
        # 2026-11-15 用户需求：本地模型要能看截图报错 + 调工具
        "id": "qwen3-vl-2b",
        "name": "Qwen3-VL-2B（看图 + 工具）",
        "file": "Qwen3-VL-2B-Instruct-Q4_K_M.gguf",
        "size": 1107410624,
        "vision": True,
        "mmproj": {
            "file": "mmproj-F16.gguf",
            "size": 819395232,
            "urls": ["https://hf-mirror.com/unsloth/Qwen3-VL-2B-Instruct-GGUF/"
                     "resolve/main/mmproj-F16.gguf"],
        },
        "note": "能看报错截图/界面，也支持工具调用（22 工具实测 18/18）；"
                "内存约 3 GB",
        "urls": ["https://hf-mirror.com/unsloth/Qwen3-VL-2B-Instruct-GGUF/"
                 "resolve/main/Qwen3-VL-2B-Instruct-Q4_K_M.gguf"],
    },
    {
        # 2026-11-17 用户需求：8G 显存跟 H3 共存，要识图 + 工具调用 + uncensored
        "id": "qwen3.5-4b-uncensored",
        "name": "Qwen3.5-4B-Uncensored（看图 + 工具 + 无审查）",
        "file": "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
        # 2026-11-17 实测修正：原声明 2791833764 是错的（比真实值大 84MB），
        # _file_complete 按 >=size*0.98 判定，导致完整文件永远被当成
        # 「没下完」→ 启动按钮永久禁用。以 hf-mirror X-Linked-Size 为准。
        "size": 2707513696,   # ~2.52 GB（远端实测）
        "vision": True,
        "mmproj": {
            "file": "mmproj-Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
            "size": 675568768,  # ~644 MB（远端实测，原 676331520 也偏大）
            "urls": ["https://hf-mirror.com/HauhauCS/"
                     "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive/"
                     "resolve/main/"
                     "mmproj-Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-BF16.gguf"],
        },
        "note": "HauhauCS Aggressive 去审查（0/465 拒绝），Qwen3.5 混合架构；"
                "识图 + 工具调用原生支持；内存约 3.5 GB，适合 8G 显存跟 H3 共存",
        "urls": ["https://hf-mirror.com/HauhauCS/"
                 "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive/"
                 "resolve/main/"
                 "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"],
    },
]

_BY_ID = {m["id"]: m for m in MODELS}


# GitHub release 的国内加速代理（2026-11-19 用户反馈「组件下载一直 0M」）
#
# 现场：组件包（llama.cpp 的 zip，25~38MB）只托管在 GitHub Releases。国内直连实测
# 65 KB/s（本机 38MB 的 Vulkan 包下了 17 分钟），且经常出现「连上了但长时间不给
# 数据」——下载器 20 秒超时、重试 8 次，全程进度条都是 0，用户以为卡死。
# 模型走 hf-mirror 不受影响，所以只有「组件」这一步会卡。
#
# 做法：把代理地址追加在官方源**之后**当备选，单源失败/卡死才接上，官方源永远排第一。
# 安全性：下载完仍按 BIN_VARIANTS 里固定的 sha256 校验，代理返回错内容会被当场抓住。
# 实测（2026-11-19，本机国内网络）：gh-proxy.com 185 KB/s、ghproxy.net 24 KB/s、
# ghfast.top 已失效。
GITHUB_MIRRORS = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
)


def _is_github_release(url: str) -> bool:
    u = str(url or "").lower()
    return "github.com/" in u and "/releases/download/" in u


def _endpoint_urls(urls) -> list:
    """下载前按用户当前镜像端点改写地址（2026-11-16）。

    上面 BIN / MODELS 表里写的是默认镜像的直链，便于阅读与固定 sha256；
    真正去哪儿取由 app.mirror.endpoint() 决定，用户可在设置里改成自建反代。
    非 HF 域名（如 llama.cpp 的 GitHub release）保持原样，但会**追加**国内加速
    代理作为备选源（官方源仍排第一，见 GITHUB_MIRRORS 上方说明）。
    """
    from app import mirror
    out = [mirror.rewrite(u) for u in (urls or [])]
    extra = []
    for u in out:
        if _is_github_release(u):
            extra.extend(m + u for m in GITHUB_MIRRORS)
    # 代理**排在官方源前面**：本机实测 185 KB/s vs 直连 65 KB/s，差 2.8 倍。
    # 代理不可用（企业内网白名单、代理挂掉）时 download_multi 会自然落到官方源，
    # 所以顺序反过来不影响可用性，只影响「多快能下完」。
    return [u for u in extra if u not in out] + out


# ---------------------------------------------------------------- 路径
def install_root() -> str:
    return os.path.join(data_root(), _REL)


def bin_dir() -> str:
    return os.path.join(install_root(), "bin")


def models_dir() -> str:
    return os.path.join(install_root(), "models")


def server_exe() -> str:
    return os.path.join(bin_dir(), "llama-server.exe")


def version_file() -> str:
    return os.path.join(install_root(), "version.txt")


def bin_pkg_path(backend: str = "") -> str:
    """二进制压缩包的落点（进度条按它算已下载量）

    backend 留空时取「这台机器正在用/将要下的那个包」：已装则以实际装的为准，
    未装则按显卡探测——否则 UI 会去轮询 cpu 包的 .part，进度条永远 0。
    """
    if not backend:
        backend = current_binary_backend() if is_installed() else detect_backend()
    pkg = BIN_VARIANTS.get(backend) or BIN_VARIANTS[DEFAULT_BACKEND]
    return os.path.join(install_root(), "_pkg", pkg["name"])


def backend_file() -> str:
    """记录已装二进制属于哪个后端（cpu / vulkan）"""
    return os.path.join(install_root(), "backend.txt")


def current_binary_backend() -> str:
    """当前 bin 目录里实际装的是哪个后端

    以文件记录为准，读不到就按 dll 反推（老版本装的是 cpu 包，没这个文件）。
    反推很关键：老用户升级上来时 backend.txt 不存在，不能因此误判成「需要重装」。
    """
    try:
        with open(backend_file(), "r", encoding="utf-8") as f:
            b = f.read().strip().lower()
        if b in BIN_VARIANTS:
            return b
    except OSError:
        pass
    if os.path.isfile(os.path.join(bin_dir(), "ggml-vulkan.dll")):
        return "vulkan"
    return DEFAULT_BACKEND


def active_bin() -> dict:
    """本机「应该用」的包信息（UI 显示体积、进度分母都取它）"""
    try:
        from app.bootstrap import detect_gpu
        g = detect_gpu()
        key = pick_binary_backend(getattr(g, "vendor", ""),
                                  getattr(g, "is_igpu", False))
    except Exception:
        key = DEFAULT_BACKEND
    # 已经装了别的后端时，以实际装的为准：用户可能在设置里手动切过
    if is_installed():
        key = current_binary_backend()
    return BIN_VARIANTS.get(key) or BIN_VARIANTS[DEFAULT_BACKEND]


def detect_backend() -> str:
    """给 UI / 启动用的后端名（含真机探测）→ cpu / vulkan"""
    try:
        from app.bootstrap import detect_gpu
        g = detect_gpu()
        return pick_binary_backend(getattr(g, "vendor", ""),
                                   getattr(g, "is_igpu", False))
    except Exception:
        return DEFAULT_BACKEND


def is_installed() -> bool:
    return os.path.isfile(server_exe())


# ------------------------------------------------ OpenMP 运行时兼容（2026-11-18）
# 现场：Intel i5-13600KF（Alder Lake 混合架构，6P + 8E）上，本地模型服务一启动
# 就 0xC0000374（STATUS_HEAP_CORRUPTION）退出，UI 只留一条红条。
#
# 取证结论（PageHeap + 全量 dump + 栈扫描）：越界写的指令位于 **libomp.dll+0xFD87**
# —— llama.cpp 官方 CPU 包自带的 LLVM OpenMP 运行时，在 DLL 初始化阶段就写坏了堆，
# `llama threadpool init` 只是第一次被发现的地方。已逐一排除：
#   · llama.cpp 版本——官方 b12383 与 b12503 表现完全一致
#   · 线程数——-t 1 / 2 / 4 / 6 / 8 全崩（与线程池大小无关）
#   · 进程亲和性——改到全 20 核掩码照样崩
#   · 文件损坏——两版 libomp.dll 字节完全一致（同 sha256）
#   · 第三方注入——进程模块表与 AppCertDlls/IFEO/KnownDLLs 全干净
#
# 解法：换 Intel OpenMP 运行时（libiomp5md）——与 LLVM OpenMP 同一代码谱系、
# 导出符号兼容，实测加载 + 推理正常。包里带一份（assets/omp/），只在检测到
# 这类崩溃时才替换，正常机器（AMD / 老 Intel）完全不受影响。
OMP_CRASH_CODE = 0xC0000374


# ---------------------------------------------------------------- 启动诊断
# llama.cpp 的所有日志（含致命错误）都写 stderr。原先直接 DEVNULL，进程一崩就
# 什么都不剩——用户只看到 UI 上一条红条，远程也拿不到任何线索（2026-11-18 排查
# 0xC0000374 时深受其害，最后只能靠外部 procdump 抓 dump 才定位到 libomp）。
# 现在改成后台线程读进环形缓冲：正常启动时开销可忽略，崩溃时把尾部打进日志。
LOG_TAIL_LINES = 60


def _drain_stream(stream, sink) -> None:
    """后台把子进程管道逐行读进环形缓冲（读到 EOF 自然退出）"""
    try:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                sink.append(line)
    except (ValueError, OSError):
        pass                       # 进程被 kill / 管道已关闭，属正常退出路径
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _log_tail(tail, log, label="llama-server 输出尾部") -> None:
    """把崩溃前的 stderr 尾巴写进日志（没有内容就跳过）"""
    if not tail:
        return
    log("  ---- %s（末 %d 行）----" % (label, len(tail)))
    for line in tail:
        log("  | " + line)
    log("  ---- 结束 ----")


_DEV_CACHE = {}


def list_devices(force: bool = False) -> list:
    """列出 llama.cpp 能用的计算设备（含名称与显存）

    返回 [{"id": "Vulkan0", "name": "...", "vram_mb": 11994, "free_mb": 11226}]

    为什么不用「解析启动日志」：b12383 的 Vulkan 后端不再打印
    `ggml_vulkan: Found N devices`，日志里只剩一条 `layer 3 is assigned to
    device Vulkan0` 的警告，抠不出显卡名。`--list-devices` 是官方提供、
    格式稳定的路子。

    关键实测结论：**它不初始化 OpenMP 线程池**，所以即使 bin 里那份 libomp
    在 Intel 混合架构上是坏的（0xC0000374），本调用仍能正常返回——可以安全地
    放在「启动前探测」的位置。
    """
    exe = server_exe()
    if not os.path.isfile(exe):
        return []
    if not force and exe in _DEV_CACHE:
        return _DEV_CACHE[exe]
    out = []
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run([exe, "--list-devices"], cwd=bin_dir(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=25,
                           creationflags=flags)
        txt = (r.stdout or b"").decode("utf-8", "replace")
        for line in txt.splitlines():
            m = re.match(r"\s*([A-Za-z]+\d+)\s*:\s*(.+?)\s*\(([\d,]+)\s*MiB"
                         r"(?:\s*,\s*([\d,]+)\s*MiB free)?\)", line)
            if not m:
                continue
            out.append({
                "id": m.group(1),
                "name": m.group(2).strip(),
                "vram_mb": int(m.group(3).replace(",", "")),
                "free_mb": int((m.group(4) or m.group(3)).replace(",", "")),
            })
    except Exception:
        out = []                       # 探测失败不该影响启动，调用方按「无设备」处理
    _DEV_CACHE[exe] = out
    return out


def _gpu_from_tail(tail) -> str:
    """从 llama-server 启动日志里取实际使用的 GPU 名（取不到返回空串）

    为什么要从日志反查、而不是直接信「本机探测」：后端包和显卡是两回事。
    用户可能装了 CPU 包但机器是 A 卡，或者 Vulkan 初始化失败自动退回 CPU——
    这两种情况都会让「探测到的显卡」和「真正在跑的算力」不一致。
    llama.cpp 加载 Vulkan 后端时会打：
        ggml_vulkan: Found 1 Vulkan devices:
        ggml_vulkan: 0 = AMD Radeon RX 7900 XTX (...) | uma: 0 | ...
    """
    for line in reversed(list(tail or [])):
        s = str(line or "")
        if "ggml_vulkan" in s and "=" in s:
            name = s.split("=", 1)[1].split("|")[0].strip()
            if name:
                return name
    return ""


def _is_omp_crash(code) -> bool:
    """退出码是否 OpenMP 初始化崩溃（0xC0000374）

    Windows 下 subprocess 拿到的是有符号数（-1073740940），按 32 位无符号比。
    """
    if code is None:
        return False
    try:
        return (int(code) & 0xFFFFFFFF) == OMP_CRASH_CODE
    except (TypeError, ValueError):
        return False


def omp_compat_src() -> str:
    """随包的备用 OpenMP 运行时（Intel 版，符号名与 libomp 一致）

    打包后资源在 _internal/assets/omp/，源码态在项目根 assets/omp/。先按
    __file__ 上溯两级（两种形态都能算对），算不出来再退回 _MEIPASS。
    """
    base = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "assets")
    if not os.path.isdir(base):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            base = os.path.join(meipass, "assets")
    return os.path.join(base, "omp", "libomp-intel.dll")


def _same_content(a: str, b: str) -> bool:
    """两个文件是否完全相同（1.6MB 级，分块比对足够快）"""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(1 << 20), fb.read(1 << 20)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def omp_patched() -> bool:
    """bin/libomp.dll 是否已经是兼容版

    按**文件内容**判定，不能只看 libomp.dll.orig 在不在：重装组件会重新解压
    zip，把 libomp.dll 覆盖回官方原版，但 .orig 还留着——那样会被误判成
    「已打过补丁」而放弃修复，用户又要再崩一次。
    """
    return _same_content(os.path.join(bin_dir(), "libomp.dll"), omp_compat_src())


def apply_omp_compat(log=print) -> bool:
    """把 bin/libomp.dll 换成随包的 Intel OpenMP 运行时（幂等）

    原版先备份成 libomp.dll.orig：万一换上去更差，用户（或我们远程指导）
    把 .orig 改回 libomp.dll 就能恢复原状，不需要重新下载组件。
    """
    dst = os.path.join(bin_dir(), "libomp.dll")
    src = omp_compat_src()
    if not (os.path.isfile(src) and os.path.isfile(dst)):
        return False
    if _same_content(dst, src):              # 已经是兼容版
        return True
    orig = dst + ".orig"
    try:
        if not os.path.isfile(orig):         # 只备份真正的官方原版
            shutil.copy2(dst, orig)
        shutil.copy2(src, dst)
    except OSError as e:                     # 文件被占用 / 权限不足
        log("  替换 OpenMP 运行时失败：%s" % e)
        return False
    log("  已换用兼容版 OpenMP 运行时（原版备份为 libomp.dll.orig）")
    return True


def installed_version() -> str:
    try:
        with open(version_file(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def get_model(mid: str) -> dict:
    return _BY_ID.get(str(mid or "").strip(), MODELS[0])


def model_file(mid: str) -> str:
    return os.path.join(models_dir(), get_model(mid)["file"])


def mmproj_file(mid: str) -> str:
    """视觉投影文件（没有 mmproj 的模型返回空串）"""
    mm = get_model(mid).get("mmproj") or {}
    return os.path.join(models_dir(), mm["file"]) if mm.get("file") else ""


def model_total(mid: str) -> int:
    """该模型需要下载的总字节数（含 mmproj）"""
    m = get_model(mid)
    return int(m["size"]) + int((m.get("mmproj") or {}).get("size") or 0)


def model_progress(mid: str) -> int:
    """已下载字节（权重 + mmproj 相加，给进度条用）"""
    m = get_model(mid)
    got = progress_of(model_file(mid))
    if (m.get("mmproj") or {}).get("file"):
        got += progress_of(mmproj_file(mid))
    return got


def _file_complete(path: str, want: int) -> bool:
    if not os.path.isfile(path):
        return False
    # 大小校验：.part 残留或下到一半被强杀时不能当"已就绪"
    return os.path.getsize(path) >= int(want) * 0.98


def has_model(mid: str) -> bool:
    m = get_model(mid)
    ok = _file_complete(model_file(mid), int(m["size"]))
    if not ok:
        return False
    mm = m.get("mmproj") or {}
    if mm.get("file"):      # 视觉模型缺 mmproj 等于不能用，别当就绪
        return _file_complete(mmproj_file(mid), int(mm["size"]))
    return True


def orphans() -> list:
    """模型目录里不被当前清单引用的权重文件（换过清单后的残留）

    2026-11-16 移除 Qwen3-0.6B 之后，用户磁盘上那份 396 MB 就成了孤儿：
    `has_model` 查不到它、下拉里也选不到，纯粹占地方。这里扫出来给用户清。
    只认 .gguf / .safetensors，别的一概不动。
    """
    root = models_dir()
    if not os.path.isdir(root):
        return []
    keep = set()
    for m in MODELS:
        keep.add(str(m.get("file") or "").lower())
        mm = (m.get("mmproj") or {})
        if mm.get("file"):
            keep.add(str(mm["file"]).lower())
    out = []
    for fn in sorted(os.listdir(root)):
        low = fn.lower()
        if low in keep or not low.endswith((".gguf", ".safetensors")):
            continue
        full = os.path.join(root, fn)
        if not os.path.isfile(full):
            continue
        try:
            out.append({"file": fn, "path": full, "size": os.path.getsize(full)})
        except OSError:
            pass
    return out


def remove_orphans() -> tuple:
    """删除残留权重，返回 (删除数, 释放字节)。失败的文件跳过，不中断"""
    n, freed = 0, 0
    for it in orphans():
        try:
            os.remove(it["path"])
            n += 1
            freed += int(it.get("size") or 0)
        except OSError:
            pass
    return n, freed


def partial_path(dest: str) -> str:
    """下载中的临时文件（bootstrap.download_multi 的 .part 约定）"""
    return dest + ".part"


def progress_of(dest: str) -> int:
    """已下载字节（给进度条用）"""
    for p in (dest, partial_path(dest)):
        try:
            if os.path.isfile(p):
                return os.path.getsize(p)
        except OSError:
            pass
    return 0


# ---------------------------------------------------------------- 下载
def download_binary(backend: str = "", log=print) -> bool:
    """下载并解压 llama-server。已装且版本/后端一致则跳过。

    backend 留空 = 按本机显卡自动选（AMD/Intel → vulkan，其余 → cpu）。
    切换后端时必须**先清空 bin 目录**：两个包的 exe 同名，不清就会出现
    「新包只覆盖了一部分 dll」的混装状态，症状是启动时报找不到入口点。
    """
    backend = (backend or detect_backend()).lower()
    pkg = BIN_VARIANTS.get(backend)
    if not pkg:
        log("  未知的二进制后端：%s" % backend)
        return False
    if (is_installed() and installed_version() == COMPONENT_VERSION
            and current_binary_backend() == backend):
        return True
    if is_installed() and current_binary_backend() != backend:
        log("  切换推理后端：%s → %s（重装二进制）"
            % (current_binary_backend(), backend))
        shutil.rmtree(bin_dir(), ignore_errors=True)
    os.makedirs(bin_dir(), exist_ok=True)
    pkg_dir = os.path.join(install_root(), "_pkg")
    os.makedirs(pkg_dir, exist_ok=True)
    zp = bin_pkg_path(backend)
    from app.bootstrap import download_multi
    ok = download_multi(_endpoint_urls(pkg["urls"]), zp, log=log,
                        expected_sha=pkg["sha256"])
    if not ok:
        log("  组件下载失败")
        return False
    try:
        _safe_extract(zp, bin_dir())
    except Exception as e:                      # 解压失败要清干净，防半装状态
        log("  解压失败：%s" % e)
        shutil.rmtree(bin_dir(), ignore_errors=True)
        return False
    finally:
        try:
            os.remove(zp)
        except OSError:
            pass
    if not is_installed():
        log("  包内没有 llama-server.exe，可能是源被替换")
        return False
    with open(version_file(), "w", encoding="utf-8") as f:
        f.write(COMPONENT_VERSION)
    try:
        with open(backend_file(), "w", encoding="utf-8") as f:
            f.write(backend)
    except OSError:
        pass
    return True


def _safe_extract(zip_path: str, dest: str) -> None:
    """防 zip slip：条目路径不能逃出 dest"""
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.abspath(os.path.join(dest_abs, name))
            if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
                raise RuntimeError("非法压缩包路径: %s" % name)
        z.extractall(dest_abs)


def download_model(mid: str, log=print) -> bool:
    if not is_installed():
        log("  请先安装组件")
        return False
    m = get_model(mid)
    os.makedirs(models_dir(), exist_ok=True)
    from app.bootstrap import download_multi
    mm = m.get("mmproj") or {}
    # 先下 mmproj（视觉模型没它起不来），再下权重
    if mm.get("file") and not _file_complete(mmproj_file(mid), int(mm["size"])):
        if not download_multi(_endpoint_urls(mm["urls"]), mmproj_file(mid), log=log,
                              expected_sha=mm.get("sha256") or None):
            log("  视觉投影文件下载失败")
            return False
    dest = model_file(m["id"])
    if not _file_complete(dest, int(m["size"])):
        ok = download_multi(_endpoint_urls(m["urls"]), dest, log=log,
                            expected_sha=m.get("sha256") or None)
        if not ok:
            return False
    if not has_model(m["id"]):
        # 源返回了短文件（镜像被截断）：删掉，别让下次误判为已就绪
        for p in (dest, mmproj_file(m["id"])):
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        log("  文件大小与声明不符，已删除")
        return False
    return True


def remove_installed() -> None:
    """删除整个组件目录（二进制 + 全部模型）"""
    shutil.rmtree(install_root(), ignore_errors=True)


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 进程
def find_free_port(preferred: int = DEFAULT_PORT, tries: int = 20) -> int:
    for p in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return 0


def _urlopen_ok(url: str, timeout: float = 1.5) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:
        return False


def _props(port: int, timeout: float = 2.5) -> dict:
    """读 llama-server 的 /props：服务端自报的「到底加载了什么」

    为什么不只信自己的记录：进程可以被手动启动、参数可以被改、模型文件可以换名。
    以服务端自报为准，UI 上写「当前加载」才不是自说自话。
    返回的字段里 model_path / model_ftype / default_generation_settings.n_ctx
    是实测可用的（llama.cpp b12383）。
    """
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/props" % port, timeout=timeout) as r:
            d = _json.loads(r.read().decode("utf-8", "ignore"))
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


class LocalServer:
    """llama-server 子进程管理（单例式使用：一个进程一个模型）"""

    def __init__(self):
        self.proc = None
        self.port = 0
        self.model_id = ""
        self.ctx = 0                 # 实际请求的上下文长度
        self.info = {}               # loaded() 的结果，UI 只读这个（不发 HTTP）
        self.last_exit = None        # 上次进程退出码（判 OpenMP 崩溃用）
        self._omp_tried = False      # 本轮启动是否已尝试过换 OpenMP 运行时
        self._ngl = 0                # 本轮实际用的 -ngl（GPU 回退时要改成 0）
        self._ngl_fallback = False   # 是否已从 GPU 卸载回退到纯 CPU 重试过
        self.gpu_name = ""           # 后端实际用到的 GPU 名（从启动日志里取）
        self.tail = collections.deque(maxlen=LOG_TAIL_LINES)  # 运行时最近日志

    # ---- 状态 ----
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.port if self.port else ""

    def ready(self, timeout: float = 1.0) -> bool:
        if not self.running:
            return False
        return _urlopen_ok("http://127.0.0.1:%d/health" % self.port, timeout)

    # ---- 启动 ----
    def _cmd(self, port: int, model: str, ctx: int, threads: int,
             quiet: bool, mmproj: str = "", ngl: int = 0) -> list:
        cmd = [server_exe(), "-m", model,
               "--host", "127.0.0.1", "--port", str(port),
               "-c", str(ctx), "-ngl", str(max(0, int(ngl or 0))), "--jinja",
               "-t", str(threads)]
        if mmproj:
            # 视觉投影：不加它，图片输入会被静默忽略（模型只会瞎编）
            cmd += ["--mmproj", mmproj]
        if quiet:
            # Qwen3 默认开思考模式：先吐一大段"让我想想"再回答，小模型上
            # 等于响应慢一倍，而且思考文本会混进 content 里污染助手输出。
            # 两道保险：--reasoning-budget 0（llama.cpp 层）+ 模板变量
            # enable_thinking=false（Qwen3 模板层，实测单独用 budget 仍会漏）。
            # 老版本 llama.cpp 没这些参数，start() 失败会自动去掉重试。
            cmd += ["--reasoning-budget", "0",
                    "--chat-template-kwargs", '{"enable_thinking": false}']
        return cmd

    def _try_start(self, model: str, mmproj: str, ctx: int, threads: int,
                   quiet: bool, flags: int, log, ready_timeout: float,
                   ngl: int = 0) -> str:
        """起一次，返回 ok / died（进程退出，可换参数重试）/ timeout / error"""
        cmd = self._cmd(self.port, model, ctx, threads, quiet, mmproj, ngl)
        log("  启动 llama-server：端口 %d，线程 %d，上下文 %d，GPU 层 %d"
            % (self.port, threads, ctx, int(ngl or 0)))
        self.tail.clear()
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=bin_dir(), creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL)
        except Exception as e:
            log("  启动失败：%s" % e)
            return "error"
        # 后台把 stderr 读进环形缓冲：不读会把管道写满，反过来卡死 llama-server
        reader = threading.Thread(target=_drain_stream,
                                  args=(self.proc.stderr, self.tail),
                                  daemon=True)
        reader.start()
        if self._wait_ready(ready_timeout):
            return "ok"
        code = self.proc.poll()
        timed_out = code is None
        self.stop()
        try:
            reader.join(timeout=1.5)     # 进程已退出，管道 EOF，读线程很快收尾
        except RuntimeError:
            pass
        if timed_out:
            _log_tail(list(self.tail), log, "启动超时，llama-server 输出尾部")
            return "timeout"
        self.last_exit = code           # 上层据此判断是不是 OpenMP 初始化崩溃
        log("  进程退出（code=%s）" % code)
        _log_tail(list(self.tail), log)  # 崩溃诊断：把最后几十行打出来
        return "died"

    def start(self, model_id: str, ctx: int = DEFAULT_CTX, threads: int = 0,
              port: int = 0, log=print, ready_timeout: float = 90,
              ngl=None) -> bool:
        self.stop()
        self.last_exit = None
        self._omp_tried = False
        if not is_installed():
            log("  组件未安装")
            return False
        if not has_model(model_id):
            log("  模型未下载")
            return False
        self.port = find_free_port(port or DEFAULT_PORT)
        if not self.port:
            log("  找不到空闲端口")
            return False
        if threads <= 0:
            threads = max(1, (os.cpu_count() or 4) // 2)
        # GPU 卸载层数：调用方没指定就按「已装后端 + 显卡类型」自动判定。
        # 注意用「已装后端」而不是「探测后端」——用户可能装了 cpu 包但机器是 A 卡，
        # 此时给 -ngl 99 也不会生效，反而容易让人误以为在跑 GPU。
        installed_backend = current_binary_backend()
        if ngl is None:
            try:
                from app.bootstrap import detect_gpu
                g = detect_gpu()
                ngl = pick_ngl(getattr(g, "vendor", ""),
                               getattr(g, "is_igpu", False), installed_backend)
            except Exception:
                ngl = 0
        ngl = max(0, int(ngl or 0))
        if ngl > 0:
            # 卸载前先确认真的有可用设备：省掉「崩了才发现没有 Vulkan 驱动」
            # （老卡 / 虚拟机 / 精简系统上很常见，白等 30 秒才失败）
            if not list_devices():
                log("  未发现可用的 GPU 计算设备（Vulkan 驱动缺失？），改走纯 CPU")
                ngl = 0
        elif installed_backend == "vulkan":
            log("  Vulkan 后端已装，但本次按纯 CPU 运行（核显不卸载 / 已指定）")
        want_ngl = ngl          # 期望值：OMP 修复后要按这个恢复（OMP 与 GPU 无关）
        model = model_file(model_id)
        mmproj = mmproj_file(model_id)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        # 上下文降级序列（2026-11-16）：默认 8192 解决 400 超限，但老机器内存
        # 不够时 llama-server 会直接退出，此时自动往下退，别让用户看到「启动失败」
        self._ngl = ngl
        self._ngl_fallback = False
        self.gpu_name = ""
        want = max(int(ctx or DEFAULT_CTX), 1)
        plan = []
        for c in (want, DEFAULT_CTX, 4096):
            if c and c not in plan and c <= want:
                plan.append(c)
        if not plan:
            plan = [want]
        plan.sort(reverse=True)

        def _mark_ok(ctx_used):
            self.model_id = model_id
            self.ctx = ctx_used
            self.info = self.loaded()      # 已经在后台线程里，HTTP 安全
            # 显卡名三种来源依次退化：启动日志 → --list-devices → 空（纯 CPU）
            self.gpu_name = _gpu_from_tail(self.tail)
            if not self.gpu_name and int(self._ngl or 0) > 0:
                devs = list_devices()
                if devs:
                    self.gpu_name = "%s（%s）" % (devs[0]["name"], devs[0]["id"])
            log("  就绪：%s（上下文 %d%s）"
                % (self.base_url, ctx_used,
                   "，GPU：" + self.gpu_name if self.gpu_name else "，纯 CPU"))

        for i, ctx_try in enumerate(plan):
            for quiet in (True, False):      # 先带 --reasoning-budget，失败退回
                st = self._try_start(model, mmproj, ctx_try, threads, quiet,
                                     flags, log, ready_timeout, ngl)
                if st == "ok":
                    _mark_ok(ctx_try)
                    return True
                if st in ("error", "timeout"):
                    log("  等待就绪超时" if st == "timeout" else "  放弃")
                    return False
                # died。GPU 卸载本身起不来（Vulkan 驱动老旧、显存不够、
                # 虚拟机里的软件渲染器）→ 退回纯 CPU 重试。AMD 老卡/核显用户至少
                # 还能用 CPU 推理，不能因为一块 GPU 让整个组件不可用。
                #
                # 但必须排除 OpenMP 崩溃：0xC0000374 是 libomp 初始化越界写堆，
                # 与用不用 GPU 完全无关。若在这里误降 ngl，等下一步换好运行时，
                # 就会「修好了却永久跑在 CPU 上」——实测踩过（N 卡 Vulkan 包首启）。
                if (ngl > 0 and not self._ngl_fallback
                        and not _is_omp_crash(self.last_exit)):
                    self._ngl_fallback = True
                    log("  GPU 卸载启动失败（code=%s），回退纯 CPU 重试"
                        % self.last_exit)
                    ngl = 0
                    self._ngl = 0
                    st = self._try_start(model, mmproj, ctx_try, threads, quiet,
                                         flags, log, ready_timeout, 0)
                    if st == "ok":
                        _mark_ok(ctx_try)
                        return True
                    if st in ("error", "timeout"):
                        log("  等待就绪超时" if st == "timeout" else "  放弃")
                        return False
                # died：先看是不是 OpenMP 初始化崩溃（Intel 混合架构已知问题）。
                # 换运行时只做一次，换完仍崩就继续走下面的常规降级逻辑。
                if _is_omp_crash(self.last_exit) and not self._omp_tried:
                    self._omp_tried = True
                    log("  检测到 OpenMP 初始化崩溃（0xC0000374），尝试换运行时")
                    if apply_omp_compat(log):
                        # 换好运行时后按「期望的 ngl」重试：如果 GPU 已被判定
                        # 不可用（_ngl_fallback），才保持纯 CPU。
                        retry_ngl = 0 if self._ngl_fallback else want_ngl
                        self._ngl = retry_ngl
                        st = self._try_start(model, mmproj, ctx_try, threads,
                                             quiet, flags, log, ready_timeout,
                                             retry_ngl)
                        if st == "ok":
                            _mark_ok(ctx_try)
                            return True
                        if st in ("error", "timeout"):
                            log("  等待就绪超时" if st == "timeout" else "  放弃")
                            return False
                        log("  换 OpenMP 运行时后仍然崩溃")
                # died：老版本 llama.cpp 可能不认思考开关，去掉再试一次
                if quiet:
                    log("  去掉思考开关重试")
            if i + 1 < len(plan):
                log("  上下文 %d 起不来（内存不足？），降到 %d 重试"
                    % (ctx_try, plan[i + 1]))
        return False

    def _wait_ready(self, timeout: float) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc is None or self.proc.poll() is not None:
                return False
            if self.ready():
                return True
            time.sleep(0.4)
        return False

    def loaded(self) -> dict:
        """「实际加载」的模型信息：服务端 /props 优先，取不到退回本地记录

        什么时候必须用这份、而不是下拉框的值：用户把下拉切到别的模型但没重启
        服务时，下拉里是「想用的」，这份才是「正在跑的」。UI 写「当前加载」就
        不能自说自话。

        注意：这里会发一次 HTTP（≤2.5s），只能在后台线程调用，别在 UI 线程调。
        """
        info = {"model_id": self.model_id, "ctx": self.ctx, "ftype": "",
                "path": "", "source": "local", "vision": False, "name": ""}
        d = _props(self.port) if self.port else {}
        if d:
            info["source"] = "server"
            g = d.get("default_generation_settings") or {}
            try:
                info["ctx"] = int(g.get("n_ctx") or info["ctx"] or 0)
            except (TypeError, ValueError):
                pass
            info["ftype"] = str(d.get("model_ftype") or "")
            mp = str(d.get("model_path") or "")
            info["path"] = mp
            base = os.path.basename(mp).lower()
            if base:
                for m in MODELS:            # 按文件名反查（服务也可能不是我们起的）
                    if str(m.get("file") or "").lower() == base:
                        info["model_id"] = m["id"]
                        break
        m = get_model(info["model_id"]) if info["model_id"] else {}
        info["name"] = ((m or {}).get("name")
                        or (os.path.splitext(os.path.basename(info["path"]))[0]
                            if info["path"] else ""))
        mods = d.get("modalities") or {}
        if isinstance(mods, dict):
            vision = bool(mods.get("vision"))
        elif isinstance(mods, (list, tuple, set)):
            vision = "vision" in mods
        else:
            vision = False
        info["vision"] = bool((m or {}).get("vision")) or vision
        return info

    # ---- 停止 ----
    def stop(self) -> None:
        p, self.proc = self.proc, None
        self.model_id = ""
        self.ctx = 0
        self.info = {}
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
        except Exception:
            pass

    def self_check(self) -> str:
        """给 UI 用的单行状态描述（带上「实际加载的是谁」）"""
        if self.running and self.ready():
            info = self.info or {}
            bits = []
            if info.get("name"):
                bits.append(str(info["name"]))
            if info.get("ctx"):
                bits.append("上下文 %s" % info["ctx"])
            tail = (" · " + " · ".join(bits)) if bits else ""
            return "运行中%s · %s" % (tail, self.base_url.replace("/v1", ""))
        if self.running:
            return "启动中…"
        return "未运行"


# 全局唯一实例：卡片 UI、主窗退出、服务商切换都操作同一个进程
SERVER = LocalServer()
