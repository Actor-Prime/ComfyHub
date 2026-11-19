# -*- coding: utf-8 -*-
"""AMD 核显/独显本机 AI 环境诊断（780M / 680M / RX 系 / 其他）

用途
----
在一台**陌生机器**上先跑这个，回答三个问题，再决定装什么：

  1. 这台机器能不能跑 qwen-image 2.1？（内存/显存够不够，装哪一档量化）
  2. 该走哪条加速后端？（ROCm / Vulkan / DirectML / 纯 CPU）
  3. 缺什么？（驱动版本、UMA 显存分配、ROCm 是否已装）

设计约束
--------
· **纯标准库**，不依赖 third-party 包 —— 客户机可能连 pip 都没有；
· **只读**，不安装、不改注册表、不写环境变量；
· 结论优先：先给「能/不能」和「建议配置」，再列明细。

用法
----
    python amd_gpu_doctor.py

在 ComfyHub 便携 Python 上跑：
    "%APPDATA%\\ComfyHub\\tools\\python\\python.exe" amd_gpu_doctor.py
"""
import ctypes
import os
import re
import sys

if sys.platform == "win32":
    try:
        # 控制台默认 GBK，输出中文/特殊符号会炸；这里强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- 模型档位
# qwen-image 2.1 的三件套：扩散主干(unet) + 文本编码器(text_encoder) + VAE。
# 单位统一用 GB（十进制），与 HuggingFace 页面一致，避免 GiB/GB 混算。
QWEN_VAE_GB = 0.68
# 物理内存最少留这么多，否则 Windows 自己抖动就触发 OOM。
# 没有这条，16GB 机器会被推荐 15.5GB 的档位（只剩 0.5GB），实测必炸。
MIN_SLACK_GB = 1.0
# 文本编码器可选量化（数值来自 unsloth / Comfy-Org 仓库实际文件大小）
ENCODERS = [
    ("int8_convrot", 9.35, "官方模板默认，画质最好，占内存最多"),
    ("w4a8", 6.31, "Comfy-Org 的 4bit 权重版，官方推荐 8~12G 显存"),
    ("UD-Q4_K_XL", 5.15, "unsloth GGUF，**16GB 内存的推荐档**"),
    ("Q4_K_M", 5.03, "unsloth GGUF，比 Q4_K_XL 略小"),
    ("IQ2_S(Heretic)", 2.90, "最小档，画质明显下降，仅极限情况用"),
]
# 扩散主干 GGUF 档位
DENOISERS = [
    ("Q4_K_M", 4.20, "官方推荐档，8G 显存可跑"),
    ("Q3_K_M", 3.17, "内存紧张时首选，画质损失可接受"),
    ("Q2_K", 2.47, "最后手段，细节明显变糊"),
]
# 每档的组合建议：(显示名, 主干GB, 编码器名, 编码器GB, 评价)
QWEN_TIERS = [
    ("推荐 · Q4_K_M + Q4_K_XL 编码器", 4.20, "UD-Q4_K_XL", 5.15, "平衡最好"),
    ("稳妥 · Q3_K_M + Q4_K_XL 编码器", 3.17, "UD-Q4_K_XL", 5.15, "余量更多"),
    ("极限 · Q3_K_M + IQ2_S 编码器", 3.17, "IQ2_S", 2.90, "画质下降明显"),
    ("不建议 · Q2_K + IQ2_S 编码器", 2.47, "IQ2_S", 2.90, "细节发糊"),
]

# 其它常见模型的粗略显存/内存参考（帮客户理解"能跑什么"）
REF_MODELS = [
    ("SD1.5（512×768, 25步）", "~2 GB", "780M 约 28 秒/张（ZLUDA）"),
    ("SDXL（1024×1024, 30步）", "~7 GB", "780M 约 6~7 分钟/张"),
    ("Flux.1-dev Q4（8步）", "~8 GB", "780M 约 12 分钟/张，内存峰值 21GB"),
    ("qwen-image 2.1 Q4（40步）", "~10 GB", "780M 预估 20~40 分钟/张"),
    ("qwen-image 2.1 Turbo（8步）", "~10 GB", "780M 预估 5~12 分钟/张"),
]


# ---------------------------------------------------------------- 采集
def _reg_class_base() -> str:
    return (r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}")


def read_gpus() -> list:
    """从注册表读显示适配器：name / driver / vram_mb / pnp

    注册表而不是 WMI：AdapterRAM 是 uint32，超过 4GB 会回绕；
    且 wmi 是可选第三方包，客户机不一定有。
    """
    out = []
    try:
        import winreg
    except ImportError:
        return out
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _reg_class_base())
    except OSError:
        return out
    try:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            k = None
            try:
                k = winreg.OpenKey(root, sub)
                try:
                    name = str(winreg.QueryValueEx(k, "DriverDesc")[0] or "").strip()
                except OSError:
                    name = ""
                if not name:
                    continue
                ver = ""
                try:
                    ver = str(winreg.QueryValueEx(k, "DriverVersion")[0] or "")
                except OSError:
                    pass
                pnp = ""
                for fld in ("MatchingDeviceId", "HardwareID"):
                    try:
                        v = winreg.QueryValueEx(k, fld)[0]
                        if isinstance(v, (list, tuple)):
                            v = v[0] if v else ""
                        if v and not pnp:
                            pnp = str(v)
                    except OSError:
                        pass
                vram = 0
                try:
                    size = winreg.QueryValueEx(
                        k, "HardwareInformation.qwMemorySize")[0]
                    if isinstance(size, int) and size > 0:
                        vram = int(size // (1024 * 1024))
                except OSError:
                    pass
                out.append({"name": name, "driver": ver,
                            "vram_mb": vram, "pnp": pnp})
            except OSError:
                pass
            finally:
                if k is not None:
                    try:
                        k.Close()
                    except OSError:
                        pass
    finally:
        try:
            root.Close()
        except OSError:
            pass
    return out


class _MEMSTATUSEX(ctypes.Structure):
    """必须字段齐全：GlobalMemoryStatusEx 依赖 dwLength 校验结构版本，
    少一个字段就会直接返回失败（表现为读数恒为 0）。"""
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram_info() -> tuple:
    """返回 (物理内存总量 GB, 当前可用 GB)；取不到返回 (0, 0)"""
    try:
        st = _MEMSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return (st.ullTotalPhys / (1000 ** 3),
                    st.ullAvailPhys / (1000 ** 3))
    except Exception:
        pass
    return (0.0, 0.0)


def total_ram_gb() -> float:
    return ram_info()[0]


def avail_ram_gb() -> float:
    return ram_info()[1]


class _PERFINFO(ctypes.Structure):
    """GetPerformanceInfo 的返回结构（字段顺序不能错，否则读数全乱）"""
    _fields_ = [("cb", ctypes.c_ulong),
                ("CommitTotal", ctypes.c_size_t),
                ("CommitLimit", ctypes.c_size_t),
                ("CommitPeak", ctypes.c_size_t),
                ("PhysicalTotal", ctypes.c_size_t),
                ("PhysicalAvailable", ctypes.c_size_t),
                ("SystemCache", ctypes.c_size_t),
                ("KernelTotal", ctypes.c_size_t),
                ("KernelPaged", ctypes.c_size_t),
                ("KernelNonpaged", ctypes.c_size_t),
                ("PageSize", ctypes.c_size_t),
                ("HandleCount", ctypes.c_ulong),
                ("ProcessCount", ctypes.c_ulong),
                ("ThreadCount", ctypes.c_ulong)]


def commit_info() -> dict:
    """提交量信息：{total_gb, limit_gb, physical_gb}

    CommitLimit ≈ 物理内存 + pagefile 上限 —— 这是 Windows 能「承诺」给所有
    进程的地址空间总量，越过它就会触发系统级「内存不足」。
    对 ComfyUI 的意义：pagefile 只能兜住 **CPU 侧**（文本编码器）的溢出；
    GPU 侧（扩散主干）走 HIP 分配，要的是可锁定的物理内存，分配不到直接
    hipErrorOutOfMemory，pagefile 帮不上。
    """
    out = {"total_gb": 0.0, "limit_gb": 0.0, "physical_gb": 0.0}
    try:
        pi = _PERFINFO()
        pi.cb = ctypes.sizeof(_PERFINFO)
        if ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
            ps = float(pi.PageSize)
            out["total_gb"] = pi.CommitTotal * ps / (1000 ** 3)
            out["limit_gb"] = pi.CommitLimit * ps / (1000 ** 3)
            out["physical_gb"] = pi.PhysicalTotal * ps / (1000 ** 3)
    except Exception:
        pass
    return out


def os_name() -> str:
    try:
        import platform
        return "%s %s (build %s)" % (platform.system(), platform.release(),
                                     platform.version())
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- 判定
_FAKE = ("microsoft basic display", "remote display", "virtual", "idd",
         "parsec", "todesk", "gameviewer", "oray")


def is_fake(name: str) -> bool:
    low = str(name or "").lower()
    return any(k in low for k in _FAKE)


_IGPU = ("780m", "760m", "740m", "680m", "660m", "890m", "880m", "8060s",
         "8050s", "radeon(tm) graphics", "radeon graphics",
         "iris", "uhd graphics", "hd graphics", "xe graphics")


def is_igpu(name: str) -> bool:
    low = str(name or "").lower()
    if any(k in low for k in _IGPU):
        return True
    if "vega" in low:
        if "rx vega" in low:
            return False
        return bool(re.search(r"vega\s*(11|8|7|6|3)\b", low))
    return False


def vendor_of(name: str) -> str:
    low = str(name or "").lower()
    if any(k in low for k in ("nvidia", "geforce", "quadro", "rtx ", "gtx ")):
        return "NVIDIA"
    if any(k in low for k in ("amd", "radeon", "advanced micro")):
        return "AMD"
    if any(k in low for k in ("intel", "iris", "uhd graphics", "arc ")):
        return "Intel"
    return "?"


# gfx 架构 → Windows ROCm 官方支持状态
AMD_ARCH = (
    (("rx 9070", "r9700"), "gfx1201", True),
    (("rx 9060", "rx 9050"), "gfx1200", True),
    (("rx 7900", "w7900", "w7800"), "gfx1100", True),
    (("rx 7800", "rx 7700"), "gfx1101", True),
    (("rx 7600",), "gfx1102", True),
    (("8060s", "8050s"), "gfx1151", True),
    (("780m", "760m", "740m"), "gfx1103", False),
    (("890m", "880m"), "gfx1150", False),
    (("rx 6950", "rx 6900", "rx 6800", "rx 6750", "rx 6700",
      "rx 6650", "rx 6600", "rx 6500"), "gfx1030", False),
    (("680m", "660m"), "gfx1032", False),
)


def amd_arch(name: str) -> tuple:
    low = str(name or "").lower()
    for keys, arch, ok in AMD_ARCH:
        if any(k in low for k in keys):
            return arch, ok
    return "", False


def has_vulkan() -> bool:
    """vulkan-1.dll 是否存在（loader 装了就说明 Vulkan 可用）"""
    win = os.environ.get("WINDIR", r"C:\Windows")
    return os.path.isfile(os.path.join(win, "System32", "vulkan-1.dll"))


def has_rocm() -> str:
    """返回已安装的 HIP/ROCm 版本目录（没装返回空串）"""
    roots = [r"C:\Program Files\AMD\ROCm",
             os.environ.get("HIP_PATH", ""),
             os.environ.get("ROCM_PATH", "")]
    for r in roots:
        if r and os.path.isdir(r):
            try:
                for sub in sorted(os.listdir(r), reverse=True):
                    p = os.path.join(r, sub)
                    if os.path.isdir(p):
                        return "%s (%s)" % (r, sub)
                return r
            except OSError:
                return r
    return ""


def tier_need(den_gb: float, enc_gb: float) -> float:
    """某一档需要的**物理内存总量**（GB）

    系统占用取 4GB（Win11 空载实测 3.5~4.5），运行时余量取 1.5GB
    （ComfyUI 加载峰值、Python 堆、文件缓存）。
    注意：核显的 UMA 显存也是从这块物理内存里划的，所以不重复计算。
    """
    return 4.0 + den_gb + enc_gb + QWEN_VAE_GB + 1.5


def tier_fits(ram_gb: float, den_gb: float, enc_gb: float) -> bool:
    """该档位是否可装：物理内存够 + 至少留 MIN_SLACK_GB"""
    return ram_gb >= tier_need(den_gb, enc_gb) + MIN_SLACK_GB


def backend_plan(gpu: dict) -> tuple:
    """返回 (后端, 说明)"""
    v = vendor_of(gpu["name"])
    arch, rocm_ok = amd_arch(gpu["name"]) if v == "AMD" else ("", False)
    if v == "NVIDIA":
        return "CUDA", "N 卡走官方 CUDA wheel"
    if v == "AMD":
        if arch and rocm_ok:
            return "ROCm", "官方支持 %s，装 rocm wheel 即可" % arch
        if arch:
            return "ROCm(需改架构)", ("%s 官方不支持 Windows ROCm；"
                                      "需 HSA_OVERRIDE_GFX_VERSION=11.0.0 "
                                      "+ 社区 ROCmLibs（GPL-3.0，不可随产品分发）" % arch)
        return "DirectML/CPU", "型号未识别，建议 DirectML 兜底或纯 CPU"
    if v == "Intel":
        return "DirectML", "Intel 核显走 DirectML（注意 torch-directml 只到 torch 2.4）"
    return "CPU", "未识别到可用显卡"


# ---------------------------------------------------------------- 输出
def hr(ch="─", n=66):
    print(ch * n)


def head(t):
    print()
    hr("═")
    print("  " + t)
    hr("═")


def _argv_ram(default: float) -> tuple:
    """解析 `--ram 16`：返回 (内存GB, 是否为模拟值)

    用途：在别的机器上评估「如果这台机器是 16GB 会怎样」，
    不用真的把内存拔了。只覆盖内存这一项，其余仍读真实硬件。
    """
    argv = sys.argv[1:]
    for i, x in enumerate(argv):
        if x == "--ram" and i + 1 < len(argv):
            try:
                return float(argv[i + 1]), True
            except ValueError:
                pass
    return default, False


def main():
    head("AMD / Radeon 本机 AI 环境诊断")
    print("  生成时间系统:", os_name())

    # ---- 1. 内存 ----
    head("1. 内存（qwen-image 2.1 的第一硬门槛）")
    real_ram = total_ram_gb()
    ram, simulated = _argv_ram(real_ram)
    avail = avail_ram_gb()
    if simulated:
        print("  物理内存总量 : %.1f GB  ⚠️ 由 --ram 指定（模拟值，非本机实测）" % ram)
        print("  本机实际     : %.1f GB" % real_ram)
    else:
        print("  物理内存总量 : %.1f GB" % ram)
    print("  当前可用     : %.1f GB" % avail)
    if ram >= 32:
        print("  判定 : ✅ 充裕，可用 int8 编码器（画质最好）")
    elif ram >= 15:
        print("  判定 : ✅ 够用，必须用 GGUF Q4 级编码器（≤5.15GB）")
    elif ram >= 11:
        print("  判定 : ⚠️ 紧张，只能上 IQ2_S 编码器，画质会降")
    else:
        print("  判定 : ❌ 不足，qwen-image 2.1 跑不动，建议换模型或云端")

    ci = commit_info()
    if ci["limit_gb"] > 0:
        pf_gb = max(0.0, ci["limit_gb"] - ci["physical_gb"])
        print()
        if simulated:
            print("  （以下为真实系统数据，不受 --ram 影响）")
        print("  提交上限     : %.1f GB（物理 %.1f + pagefile 约 %.1f）"
              % (ci["limit_gb"], ci["physical_gb"], pf_gb))
        print("  当前已提交   : %.1f GB" % ci["total_gb"])
        if pf_gb < 8:
            if ci["physical_gb"] >= 24:
                # 物理内存本身宽裕时，Windows 不给大 pagefile 也没关系
                print("  ○ pagefile 偏小（%.1f GB），但物理内存充裕，够用" % pf_gb)
            else:
                print("  ⚠️ pagefile 偏小（%.1f GB）：在 NVMe 系统盘固定设 20~32GB，"
                      % pf_gb)
                print("     否则后台程序一波动就把 ComfyUI 顶爆（变成『直接崩溃』）")
        elif pf_gb >= 16:
            print("  ✅ pagefile 容量充足，可作安全垫")
        else:
            print("  ○ pagefile 够用（8~16GB），一般场景无需调整")
        print("  说明：pagefile **只能兜 CPU 侧溢出**（文本编码器）；")
        print("        GPU 侧主干溢出会直接 hipErrorOutOfMemory，pagefile 救不了。")
        print("        溢出 ≤2GB 基本无感；>8GB 会慢到不可用。")

    # ---- 2. 显卡 ----
    head("2. 显卡与驱动")
    gpus = [g for g in read_gpus() if not is_fake(g["name"])]
    if not gpus:
        print("  ❌ 没读到显卡信息（注册表不可访问？）")
    for g in gpus:
        tag = "核显" if is_igpu(g["name"]) else "独显"
        print("  · %s" % g["name"])
        print("      厂商=%s  类型=%s  显存=%d MB  驱动=%s"
              % (vendor_of(g["name"]), tag, g["vram_mb"],
                 g["driver"] or "未知"))
        if vendor_of(g["name"]) == "AMD":
            arch, ok = amd_arch(g["name"])
            if arch:
                print("      gfx 架构=%s  Windows ROCm 官方支持=%s"
                      % (arch, "是" if ok else "否"))
            else:
                print("      gfx 架构=未识别（名字太通用，需读设备 ID）")
        if is_igpu(g["name"]):
            print("      ⚠️ 核显共享系统内存，必须在 BIOS/Adrenalin 里分配 UMA 显存")
            print("         （16GB 机器建议给 4GB；有 VGM/Variable Graphics Memory 更好）")

    # ---- 3. 后端可用性 ----
    head("3. 加速后端可用性")
    print("  Vulkan loader     : %s" % ("✅ 有" if has_vulkan() else "❌ 无"))
    rocm = has_rocm()
    print("  HIP/ROCm 安装     : %s" % ("✅ " + rocm if rocm else "❌ 未安装"))
    print("  （DirectML 需 torch-directml，且它最高只发到 cp312）")

    main_gpu = gpus[0] if gpus else {"name": "", "vram_mb": 0}
    if gpus and any(not is_igpu(g["name"]) for g in gpus):
        # 有独显就优先独显
        main_gpu = sorted(gpus, key=lambda x: (is_igpu(x["name"]), -x["vram_mb"]))[0]
    backend, why = backend_plan(main_gpu)
    print()
    print("  → 推荐后端 : %s" % backend)
    print("     %s" % why)

    # ---- 4. qwen-image 2.1 可行性 ----
    head("4. qwen-image 2.1 可行性（三件套内存预算）")
    print("  说明：三件套 = 扩散主干 + 文本编码器 + VAE；含系统占用与运行时余量")
    print()
    for label, den_gb, enc_name, enc_gb, quality in QWEN_TIERS:
        need = tier_need(den_gb, enc_gb)
        if ram >= need + MIN_SLACK_GB + 1.0:
            mark = "✅"
        elif ram >= need + MIN_SLACK_GB:
            mark = "⚠️"
        else:
            mark = "❌"
        print("  %s %-34s 需要 %5.1f GB  %s" % (mark, label, need, quality))
    print()
    print("  ✅ 充裕（余量 2GB+）  ⚠️ 勉强（余量 1~2GB）  ❌ 装不下（<1GB）")
    print()

    # 推荐策略：QWEN_TIERS 本身按画质从好到差排列，取**第一个装得下的**。
    # 不能按「余量最大」挑——那会挑到画质最差的 IQ2_S 档，客户拿到的是糊图。
    # 「装得下」= 物理内存够 + 至少留 MIN_SLACK_GB。
    top = None
    for label, den_gb, enc_name, enc_gb, quality in QWEN_TIERS:
        if not tier_fits(ram, den_gb, enc_gb):
            continue
        need = tier_need(den_gb, enc_gb)
        top = {"label": label, "need": need, "quality": quality,
               "den_gb": den_gb, "slack": ram - need}
        break

    if top is None:
        print("  ❌ 这台机器装不下任何一档 qwen-image 2.1")
        print("     可行替代：SDXL（权重约 7GB）/ Flux Q4（约 8GB），或改用云端 API")
    else:
        if top["slack"] >= 2.0:
            print("  ✅ 建议配置：%s" % top["label"])
            print("     预计占用 %.1f GB，余量 %.1f GB，可开 1024×1024"
                  % (top["need"], top["slack"]))
        else:
            print("  ⚠️ 建议配置：%s" % top["label"])
            print("     预计占用 %.1f GB，余量仅 %.1f GB —— 能跑但很紧"
                  % (top["need"], top["slack"]))
            print("     必须同时做到：768×768 起步 / 关 prompt enhancer /")
            print("     关浏览器等占内存程序 / 出图期间别开别的软件")

        # 列出其它可选（画质更低但不那么吃内存）
        others = []
        for label, den_gb, enc_name, enc_gb, quality in QWEN_TIERS:
            if label == top["label"]:
                continue
            if tier_fits(ram, den_gb, enc_gb):
                others.append((label, tier_need(den_gb, enc_gb), quality))
        if others and len(others) > 0:
            print()
            print("  更省内存的备选（画质依次下降）：")
            for label, need, quality in others[:3]:
                print("     · %-34s 需 %.1f GB  %s" % (label, need, quality))

        # 核显的关键约束：UMA 显存必须装得下扩散主干
        if main_gpu and is_igpu(main_gpu.get("name", "")):
            den = top["den_gb"]
            vram_mb = int(main_gpu.get("vram_mb") or 0)
            if vram_mb and vram_mb < den * 1024:
                print()
                print("  ⚠️ 核显当前显存 %d MB < 扩散主干 %.1f GB" % (vram_mb, den))
                print("     需在 BIOS 或在 Adrenalin 开 Variable Graphics Memory，")
                print("     把 UMA 显存调到 ≥ %d MB；否则主干会在内存/显存间反复搬运，"
                      % int(den * 1024 + 512))
                print("     速度会慢一个量级（不是不能跑，是很难等）")

        print()
        print("  配套必做（否则必 OOM 或出图崩坏）：")
        print("    · 文本编码器 Cache 节点 device 设为 cpu（省 5~9GB 显存）")
        print("    · 关闭 prompt enhancer（它会额外加载 9.5GB 的 Qwen3.5-9B！）")
        print("    · 分辨率从 768×768 起步，别一上来 1024/2048")
        print("    · batch size = 1；Turbo 版用 8 步 + CFG 1")
        print("    · 文本编码器加载节点的 type 必须显式设成 qwen_image"
              "（不设会出图崩坏且不报错）")
        print("    · GGUF 插件用 leejet 的 fork，别用 city96 的旧版")

    # ---- 5. 其它模型参考 ----
    head("5. 780M 级核显的参考速度（同代实测）")
    print("  %-30s %-10s %s" % ("模型", "权重", "780M 实测/预估"))
    hr()
    for name, w, t in REF_MODELS:
        print("  %-30s %-10s %s" % (name, w, t))
    print()
    print("  依据：7840HS + 780M + 32GB DDR5-5600 实测（ZLUDA / Vulkan 后端）")
    print("  核显带宽是硬瓶颈（双通道 DDR5 约 83~89 GB/s，独显是 200~500+）")

    # ---- 6. 结论 ----
    head("6. 结论与下一步")
    if not gpus:
        print("  · 先确认显卡驱动已装、注册表可读")
    elif backend.startswith("ROCm(需改架构)"):
        print("  · 官方不支持 gfx1103，推荐顺序：")
        print("      1) torch ROCm wheel（gfx110X-all）+ HSA_OVERRIDE_GFX_VERSION=11.0.0")
        print("         —— wheel 自带 gfx1100 kernel，无需第三方库，许可干净")
        print("      2) 上面不通才考虑社区 ROCmLibs（GPL-3.0，只能指导用户自取）")
    if top:
        print("  · qwen-image 2.1 可跑：%s" % top["label"])
        print("    → 详见 docs/AMD_780M_QWEN_IMAGE.md 第 4、6 节")
    else:
        print("  · qwen-image 2.1 跑不动，改用 SDXL / Flux Q4 或云端 API")
    print("  · 出图前先跑一张 768×768 最小图验证链路，再调提示词与分辨率")
    print()
    hr("═")
    print("  诊断结束（只读，未做任何修改）")
    hr("═")
    return 0


if __name__ == "__main__":
    sys.exit(main())
