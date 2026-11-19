# -*- coding: utf-8 -*-
"""本地模型「推理后端」回归测试 — 2026-11-19

需求背景：开始兼容 AMD 显卡与核显。llama.cpp 官方 Windows 包分 cpu / vulkan 两套，
产品要按显卡厂商自动选包，并按「独显 / 核显」决定 `-ngl`。

覆盖（全部离线，不下载、不启动真实进程）：
    A. 后端选择：AMD/Intel → vulkan，NVIDIA/无卡 → cpu
    B. -ngl 策略：Vulkan 独显 99，核显 0（实测核显卸载更慢），CPU 包恒 0
    C. 包表完整性：体积 / sha256 / urls 齐全，sha256 不许留占位符
    D. 包落点：cpu 与 vulkan 指向不同文件（否则进度条 / 重装会互串）
    E. 已装后端反推：backend.txt 优先，缺失时按 ggml-vulkan.dll 反推
    F. GPU 名从启动日志提取（含取不到时的安全返回）
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.local_llm as L

FAILS = []
_N = [0]


def check(label, cond, detail=""):
    _N[0] += 1
    if cond:
        print("[PASS] %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("[FAIL] %s %s" % (label, detail))


def t_pick_backend():
    """A. 后端选择"""
    cases = [
        ("amd", False, "vulkan", "AMD 独显 → Vulkan"),
        ("amd", True, "vulkan", "AMD 核显 → Vulkan 包（核显仍要 Vulkan 驱动栈）"),
        ("intel", False, "vulkan", "Intel 独显 → Vulkan"),
        ("intel", True, "vulkan", "Intel 核显 → Vulkan"),
        ("nvidia", False, "cpu", "NVIDIA → CPU（不跟 ComfyUI 抢显存）"),
        ("", False, "cpu", "无显卡 → CPU"),
        ("none", False, "cpu", "vendor=none → CPU"),
    ]
    for vendor, igpu, want, label in cases:
        got = L.pick_binary_backend(vendor, igpu)
        check(label, got == want, "got=%r want=%r" % (got, want))
    # 大小写 / 空白容错：注册表与 WMI 给的字符串不规范
    check("厂商名大小写与空白容错",
          L.pick_binary_backend("  AMD  ", False) == "vulkan")
    check("未知厂商安全回退 CPU",
          L.pick_binary_backend("qualcomm", False) == "cpu")


def t_pick_ngl():
    """B. -ngl 策略"""
    check("Vulkan + AMD 独显 → 99", L.pick_ngl("amd", False, "vulkan") == 99)
    check("Vulkan + Intel 独显 → 99", L.pick_ngl("intel", False, "vulkan") == 99)
    # 这条是实测结论：核显 offload 会把 pp 打到 1/12，必须固定 0
    check("Vulkan + AMD 核显 → 0（实测卸载更慢）",
          L.pick_ngl("amd", True, "vulkan") == 0)
    check("Vulkan + Intel 核显 → 0", L.pick_ngl("intel", True, "vulkan") == 0)
    check("CPU 包 → 恒 0（没有 GPU 后端）",
          L.pick_ngl("amd", False, "cpu") == 0)
    check("后端名大小写容错",
          L.pick_ngl("amd", False, "VULKAN") == 99)


def t_variants_table():
    """C. 包表完整性"""
    check("两个后端都已登记", set(L.BIN_VARIANTS) == {"cpu", "vulkan"},
          str(sorted(L.BIN_VARIANTS)))
    for key, pkg in L.BIN_VARIANTS.items():
        check("%s: 有 zip 名" % key, str(pkg.get("name", "")).endswith(".zip"))
        check("%s: 体积 > 10MB" % key, int(pkg.get("size", 0)) > 10 * 1048576,
              str(pkg.get("size")))
        sha = str(pkg.get("sha256", ""))
        check("%s: sha256 是 64 位十六进制" % key,
              len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), sha)
        check("%s: sha256 不是占位符" % key, "PLACEHOLDER" not in sha, sha)
        check("%s: urls 非空" % key, bool(pkg.get("urls")))
        # 下载源必须是 https，且按 sha256 校验后才执行——防篡改是硬要求
        check("%s: 下载源为 https" % key,
              all(u.startswith("https://") for u in pkg.get("urls") or []))
        check("%s: 文件名含版本与平台" % key,
              "b12383" in pkg["name"] and "win" in pkg["name"]
              and "x64" in pkg["name"], pkg["name"])
    check("BIN 仍指向 cpu（向后兼容旧引用）", L.BIN is L.BIN_VARIANTS["cpu"])
    check("两个包文件名不同",
          L.BIN_VARIANTS["cpu"]["name"] != L.BIN_VARIANTS["vulkan"]["name"])


def t_pkg_path():
    """D. 包落点区分"""
    p_cpu = L.bin_pkg_path("cpu")
    p_vk = L.bin_pkg_path("vulkan")
    check("cpu 包落点含 cpu-x64", "cpu-x64" in p_cpu, p_cpu)
    check("vulkan 包落点含 vulkan-x64", "vulkan-x64" in p_vk, p_vk)
    check("两者不是同一个文件", p_cpu != p_vk)
    check("未知后端回退 cpu 落点", L.bin_pkg_path("opencl") == p_cpu,
          L.bin_pkg_path("opencl"))


def t_current_backend():
    """E. 已装后端反推（用临时目录冒充安装目录）"""
    tmp = tempfile.mkdtemp(prefix="cftest_bk_")
    real_root = L.install_root
    try:
        L.install_root = lambda: tmp
        bd = os.path.join(tmp, "bin")
        os.makedirs(bd, exist_ok=True)
        # 1) 目录空 → 默认 cpu
        check("未安装时反推为 cpu", L.current_binary_backend() == "cpu")
        # 2) 只有 vulkan dll、没有 backend.txt → 反推 vulkan（老版本升级上来）
        open(os.path.join(bd, "ggml-vulkan.dll"), "wb").close()
        check("按 ggml-vulkan.dll 反推 vulkan",
              L.current_binary_backend() == "vulkan")
        # 3) backend.txt 优先于 dll 探测
        with open(os.path.join(tmp, "backend.txt"), "w", encoding="utf-8") as f:
            f.write("cpu\n")
        check("backend.txt 优先（容忍换行与空格）",
              L.current_binary_backend() == "cpu")
        # 4) backend.txt 内容非法 → 不能崩溃，退回探测
        with open(os.path.join(tmp, "backend.txt"), "w", encoding="utf-8") as f:
            f.write("garbage")
        check("backend.txt 内容非法时回退探测",
              L.current_binary_backend() == "vulkan")
        # 5) active_bin 未装时按探测走，装了之后以实际为准
        with open(os.path.join(tmp, "backend.txt"), "w", encoding="utf-8") as f:
            f.write("vulkan")
        open(os.path.join(bd, "llama-server.exe"), "wb").close()
        check("已安装时 active_bin 取实际后端",
              L.active_bin()["name"] == L.BIN_VARIANTS["vulkan"]["name"],
              L.active_bin()["name"])
    finally:
        L.install_root = real_root
        shutil.rmtree(tmp, ignore_errors=True)


def t_gpu_from_tail():
    """F. 从启动日志提取 GPU 名"""
    tail = [
        "load_backend: loaded Vulkan backend from ggml-vulkan.dll",
        "ggml_vulkan: Found 1 Vulkan devices:",
        "ggml_vulkan: 0 = NVIDIA GeForce RTX 4070 Ti (NVIDIA) | uma: 0 | "
        "fp16: 1 | bf16: 1 | warp size: 32",
        "llama_model_load: ok",
    ]
    got = L._gpu_from_tail(tail)
    # llama.cpp 的格式是「0 = <型号> (<后端厂商>) | ...」，两段都留着——
    # AMD 的 (RADV NAVI31) 是有用信息，不能一刀切掉括号部分
    check("提取 Vulkan 设备名（含后端厂商）",
          got == "NVIDIA GeForce RTX 4070 Ti (NVIDIA)", got)
    amd = ["ggml_vulkan: 0 = AMD Radeon RX 7900 XTX (RADV NAVI31) | uma: 0"]
    check("提取 AMD 设备名",
          L._gpu_from_tail(amd) == "AMD Radeon RX 7900 XTX (RADV NAVI31)",
          L._gpu_from_tail(amd))
    check("纯 CPU 日志返回空串", L._gpu_from_tail(
        ["llama threadpool init, n_threads = 10"]) == "")
    check("空输入安全返回", L._gpu_from_tail([]) == "")
    check("None 输入安全返回", L._gpu_from_tail(None) == "")


def t_cmd_ngl():
    """G. 命令行真的带上了 -ngl"""
    srv = L.LocalServer()
    cmd = srv._cmd(8089, "m.gguf", 8192, 8, True, "", 99)
    check("命令含 -ngl 99", "-ngl" in cmd and cmd[cmd.index("-ngl") + 1] == "99",
          str(cmd[-8:]))
    cmd0 = srv._cmd(8089, "m.gguf", 8192, 8, True, "", 0)
    check("命令含 -ngl 0", cmd0[cmd0.index("-ngl") + 1] == "0")
    cmd_neg = srv._cmd(8089, "m.gguf", 8192, 8, True, "", -5)
    check("负数 -ngl 被钳到 0", cmd_neg[cmd_neg.index("-ngl") + 1] == "0",
          cmd_neg[cmd_neg.index("-ngl") + 1])


def t_list_devices():
    """H. --list-devices 解析（Vulkan 设备名与显存的唯一可靠来源）"""
    tmp = tempfile.mkdtemp(prefix="cftest_dev_")
    real_exe, real_bin = L.server_exe, L.bin_dir
    real_run, real_plat = L.subprocess.run, L.sys.platform
    fake_exe = os.path.join(tmp, "llama-server.exe")
    open(fake_exe, "wb").close()
    L.server_exe = lambda: fake_exe
    L.bin_dir = lambda: tmp

    class _R:
        def __init__(self, txt):
            self.stdout = txt.encode("utf-8")

    def _install(txt):
        L.subprocess.run = lambda *a, **k: _R(txt)
        L._DEV_CACHE.pop(fake_exe, None)
        return L.list_devices()

    try:
        d = _install("Available devices:\n"
                     "  Vulkan0: NVIDIA GeForce RTX 4070 Ti (11994 MiB, 11226 MiB free)\n")
        check("解析单设备", len(d) == 1, str(d))
        if d:
            check("设备名正确", d[0]["name"] == "NVIDIA GeForce RTX 4070 Ti", d[0]["name"])
            check("显存解析正确", d[0]["vram_mb"] == 11994, str(d[0]["vram_mb"]))
            check("可用显存解析正确", d[0]["free_mb"] == 11226, str(d[0]["free_mb"]))
            check("设备 id 正确", d[0]["id"] == "Vulkan0", d[0]["id"])

        d2 = _install("Available devices:\n"
                      "  Vulkan0: AMD Radeon RX 7900 XTX (24560 MiB, 24000 MiB free)\n"
                      "  Vulkan1: AMD Radeon(TM) Graphics (512 MiB, 480 MiB free)\n")
        check("解析多设备（独显 + 核显）", len(d2) == 2, str(len(d2)))
        check("多设备顺序保持", d2[0]["name"].startswith("AMD Radeon RX 7900"),
              d2[0]["name"] if d2 else "")

        # 无 free 信息时，可用显存回退到总量（老版本 llama.cpp 只报一个数）
        d3 = _install("Available devices:\n"
                      "  Vulkan0: AMD Radeon(TM) 780M Graphics (512 MiB)\n")
        check("缺 free 字段时回退总量",
              bool(d3) and d3[0]["free_mb"] == 512, str(d3))

        # 只有 CPU（没有 GPU 后端）→ 空列表，上层据此放弃卸载
        d4 = _install("Available devices:\n\n")
        check("无 GPU 设备返回空", d4 == [], str(d4))

        # 探测异常不能让启动崩掉
        def _boom(*a, **k):
            raise OSError("no such process")
        L.subprocess.run = _boom
        L._DEV_CACHE.pop(fake_exe, None)
        check("探测异常安全返回空列表", L.list_devices() == [])

        # exe 不存在时直接返回，不启进程
        L.server_exe = lambda: os.path.join(tmp, "nope.exe")
        check("exe 不存在时不启进程", L.list_devices() == [])
    finally:
        L.server_exe, L.bin_dir = real_exe, real_bin
        L.subprocess.run, L.sys.platform = real_run, real_plat
        L._DEV_CACHE.pop(fake_exe, None)
        shutil.rmtree(tmp, ignore_errors=True)


def t_omp_vs_gpu_order():
    """I. OMP 崩溃不能触发 GPU→CPU 降级（实测踩过的顺序 bug）

    现场：N 卡装 vulkan 包首次启动，libomp 初始化越界写堆 → 0xC0000374。
    如果这时候先降 ngl、再换 OpenMP 运行时，换好后就会「修好了却永久跑在 CPU
    上」——用户永远得不到 GPU 加速，而且没有任何报错。所以 OMP 修复必须
    保留原本的 -ngl。
    """
    real = {}
    keys = ("is_installed", "has_model", "find_free_port", "apply_omp_compat",
            "list_devices", "current_binary_backend")
    for k in keys:
        real[k] = getattr(L, k)
    try:
        L.is_installed = lambda: True
        L.has_model = lambda mid: True
        L.find_free_port = lambda p: 18089
        L.list_devices = lambda force=False: [
            {"id": "Vulkan0", "name": "FAKE GPU", "vram_mb": 8000, "free_mb": 7000}]
        L.current_binary_backend = lambda: "vulkan"
        L.apply_omp_compat = lambda log=print: True

        # --- 用例 1：首次 0xC0000374，第二次（换完运行时）成功 ---
        seen = []
        srv = L.LocalServer()
        srv.loaded = lambda: {}

        def fake_try(model, mmproj, ctx, threads, quiet, flags, log,
                     ready_timeout, ngl=0):
            seen.append(int(ngl))
            if len(seen) == 1:
                srv.last_exit = -1073740940      # 0xC0000374 有符号形式
                return "died"
            return "ok"

        srv._try_start = fake_try
        ok = srv.start("m", ctx=4096, ngl=99, log=lambda s: None, ready_timeout=1)
        check("OMP 崩溃后仍能启动成功", ok is True)
        check("OMP 重试保留 -ngl 99（不因 OMP 降级成 CPU）",
              seen[-1] == 99, "调用序列=%s" % seen)
        check("没有任何一次以 ngl=0 重试", 0 not in seen, "调用序列=%s" % seen)
        check("记录的实际 ngl 是 99", srv._ngl == 99, str(srv._ngl))

        # --- 用例 2：非 OMP 崩溃（GPU 真的起不来）→ 才回退纯 CPU ---
        seen2 = []
        srv2 = L.LocalServer()
        srv2.loaded = lambda: {}

        def fake_try2(model, mmproj, ctx, threads, quiet, flags, log,
                      ready_timeout, ngl=0):
            seen2.append(int(ngl))
            if int(ngl) > 0:
                srv2.last_exit = 1               # 普通失败，不是 OMP
                return "died"
            return "ok"

        srv2._try_start = fake_try2
        ok2 = srv2.start("m", ctx=4096, ngl=99, log=lambda s: None, ready_timeout=1)
        check("GPU 起不来时回退纯 CPU 并成功", ok2 is True)
        check("回退后 ngl 变 0", seen2 and seen2[-1] == 0, "调用序列=%s" % seen2)
        check("回退只尝试一次（不反复重试）",
              seen2.count(0) == 1, "调用序列=%s" % seen2)

        # --- 用例 3：设备探测为空时，压根不该尝试 GPU ---
        seen3 = []
        srv3 = L.LocalServer()
        srv3.loaded = lambda: {}
        L.list_devices = lambda force=False: []

        def fake_try3(model, mmproj, ctx, threads, quiet, flags, log,
                      ready_timeout, ngl=0):
            seen3.append(int(ngl))
            return "ok"

        srv3._try_start = fake_try3
        srv3.start("m", ctx=4096, ngl=99, log=lambda s: None, ready_timeout=1)
        check("无可用设备时直接纯 CPU 启动（不做无用尝试）",
              seen3 == [0], "调用序列=%s" % seen3)

        # --- 用例 4：卸载前探测到空设备表也要能正常起来（不卡死） ---
        seen4 = []
        srv4 = L.LocalServer()
        srv4.loaded = lambda: {}

        def fake_try4(model, mmproj, ctx, threads, quiet, flags, log,
                      ready_timeout, ngl=0):
            seen4.append(int(ngl))
            return "ok"

        srv4._try_start = fake_try4
        L.list_devices = lambda force=False: []
        ok4 = srv4.start("m", ctx=4096, ngl=None, log=lambda s: None,
                         ready_timeout=1)
        check("探测为空时 ngl=None 也能正常启动", ok4 is True)
        check("自动判定路径下 ngl 最终为 0", seen4 and seen4[-1] == 0,
              "调用序列=%s" % seen4)
    finally:
        for k, v in real.items():
            setattr(L, k, v)


def main():
    print("=" * 60)
    t_pick_backend()
    print("-" * 60)
    t_pick_ngl()
    print("-" * 60)
    t_variants_table()
    print("-" * 60)
    t_pkg_path()
    print("-" * 60)
    t_current_backend()
    print("-" * 60)
    t_gpu_from_tail()
    print("-" * 60)
    t_cmd_ngl()
    print("-" * 60)
    t_list_devices()
    print("-" * 60)
    t_omp_vs_gpu_order()
    print("=" * 60)
    print("结果: %d/%d 通过" % (_N[0] - len(FAILS), _N[0]))
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
