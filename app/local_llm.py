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
import os
import shutil
import socket
import subprocess
import sys
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

# llama.cpp 官方 CPU 构建（b12383）。体积与 sha256 为实测值，换版本必须同步更新。
BIN = {
    "name": "llama-b12383-bin-win-cpu-x64.zip",
    "size": 25124601,
    "sha256": "ad5bf0d826ea1f39aaf9721b7e26fde9be0bef5d9b69b9ba88c2d083b9a3bca7",
    "urls": [
        "https://github.com/ggml-org/llama.cpp/releases/download/b12383/"
        "llama-b12383-bin-win-cpu-x64.zip",
    ],
}

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


def _endpoint_urls(urls) -> list:
    """下载前按用户当前镜像端点改写地址（2026-11-16）。

    上面 BIN / MODELS 表里写的是默认镜像的直链，便于阅读与固定 sha256；
    真正去哪儿取由 app.mirror.endpoint() 决定，用户可在设置里改成自建反代。
    非 HF 域名（如 llama.cpp 的 GitHub release）原样返回。
    """
    from app import mirror
    return [mirror.rewrite(u) for u in (urls or [])]


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


def bin_pkg_path() -> str:
    """二进制压缩包的落点（进度条按它算已下载量）"""
    return os.path.join(install_root(), "_pkg", BIN["name"])


def is_installed() -> bool:
    return os.path.isfile(server_exe())


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
def download_binary(log=print) -> bool:
    """下载并解压 llama-server（24 MB）。已装且版本一致则跳过。"""
    if is_installed() and installed_version() == COMPONENT_VERSION:
        return True
    os.makedirs(bin_dir(), exist_ok=True)
    pkg_dir = os.path.join(install_root(), "_pkg")
    os.makedirs(pkg_dir, exist_ok=True)
    zp = os.path.join(pkg_dir, BIN["name"])
    from app.bootstrap import download_multi
    ok = download_multi(_endpoint_urls(BIN["urls"]), zp, log=log,
                        expected_sha=BIN["sha256"])
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
             quiet: bool, mmproj: str = "") -> list:
        cmd = [server_exe(), "-m", model,
               "--host", "127.0.0.1", "--port", str(port),
               "-c", str(ctx), "-ngl", "0", "--jinja",
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
                   quiet: bool, flags: int, log, ready_timeout: float) -> str:
        """起一次，返回 ok / died（进程退出，可换参数重试）/ timeout / error"""
        cmd = self._cmd(self.port, model, ctx, threads, quiet, mmproj)
        log("  启动 llama-server：端口 %d，线程 %d，上下文 %d"
            % (self.port, threads, ctx))
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=bin_dir(), creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL)
        except Exception as e:
            log("  启动失败：%s" % e)
            return "error"
        if self._wait_ready(ready_timeout):
            return "ok"
        code = self.proc.poll()
        self.stop()
        if code is None:
            return "timeout"
        log("  进程退出（code=%s）" % code)
        return "died"

    def start(self, model_id: str, ctx: int = DEFAULT_CTX, threads: int = 0,
              port: int = 0, log=print, ready_timeout: float = 90) -> bool:
        self.stop()
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
        model = model_file(model_id)
        mmproj = mmproj_file(model_id)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        # 上下文降级序列（2026-11-16）：默认 8192 解决 400 超限，但老机器内存
        # 不够时 llama-server 会直接退出，此时自动往下退，别让用户看到「启动失败」
        want = max(int(ctx or DEFAULT_CTX), 1)
        plan = []
        for c in (want, DEFAULT_CTX, 4096):
            if c and c not in plan and c <= want:
                plan.append(c)
        if not plan:
            plan = [want]
        plan.sort(reverse=True)

        for i, ctx_try in enumerate(plan):
            for quiet in (True, False):      # 先带 --reasoning-budget，失败退回
                st = self._try_start(model, mmproj, ctx_try, threads, quiet,
                                     flags, log, ready_timeout)
                if st == "ok":
                    self.model_id = model_id
                    self.ctx = ctx_try
                    self.info = self.loaded()      # 已经在后台线程里，HTTP 安全
                    log("  就绪：%s（上下文 %d）" % (self.base_url, ctx_try))
                    return True
                if st in ("error", "timeout"):
                    log("  等待就绪超时" if st == "timeout" else "  放弃")
                    return False
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
