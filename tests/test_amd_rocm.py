# -*- coding: utf-8 -*-
"""AMD ROCm 接入回归测试 — 2026-11-19

需求背景：用户要求「开始兼容 AMD 显卡和核显」。ComfyUI 侧在 Windows 上只有
ROCm 一条正路，而 ROCm wheel 按 gfx 架构分发，所以「商品名 → 架构 → 装哪个包」
这条链路必须严丝合缝。

覆盖（全部离线，不下载、不装包）：
    A. rocm_plan：官方支持的卡 / 家族冒充（gfx1103）/ 无解的老卡 / 非 AMD
    B. is_rocm_variant
    C. torch_build_of 认出 ROCm 版 torch（cuda=None + hip 有值）
    D. _torch_post_check 的 ROCm 分支（不能把 HIP 版误判成 CPU 版，也不许
       把 CPU 版误认为装成功）
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.bootstrap as B
import app.process as P

FAILS = []
_N = [0]


def check(label, cond, detail=""):
    _N[0] += 1
    if cond:
        print("[PASS] %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("[FAIL] %s %s" % (label, detail))


def _gpu(vendor="amd", arch="", rocm_ok=False, name=""):
    g = B.GpuInfo()
    g.vendor = vendor
    g.arch = arch
    g.rocm_ok = rocm_ok
    g.name = name or "TEST GPU"
    return g


def t_rocm_plan():
    """A. 架构 → 安装方案"""
    # 非 AMD 一律不掺和
    for v in ("nvidia", "intel", "none", ""):
        check("%s 不走 ROCm" % (v or "空"),
              B.rocm_plan(_gpu(vendor=v, arch="gfx1100", rocm_ok=True)) == {})

    # 官方支持的独显：原样装，不用 HSA
    p = B.rocm_plan(_gpu(arch="gfx1100", rocm_ok=True, name="RX 7900 XTX"))
    check("gfx1100 原生支持", bool(p) and p["variant"] == "rocm-gfx1100", str(p))
    check("gfx1100 无需 HSA 冒充", p.get("hsa") == "", str(p.get("hsa")))
    check("gfx1100 native=True", p.get("native") is True)
    check("gfx1100 指向 gfx110X-all 源",
          "gfx110X-all" in p.get("index", ""), p.get("index"))

    p = B.rocm_plan(_gpu(arch="gfx1201", rocm_ok=True))
    check("gfx1201 映射到 gfx120X-all 源",
          bool(p) and "gfx120X-all" in p["index"], str(p))
    # variant 保留真实架构名（便于日志与排查），源才做归一
    check("gfx1201 variant 保留真实架构名",
          p["variant"] == "rocm-gfx1201", p.get("variant"))
    check("rocm-gfx1201 能被 install_torch 反解出源",
          B.amd_rocm_index("gfx1201") != "", B.amd_rocm_index("gfx1201"))

    p = B.rocm_plan(_gpu(arch="gfx1151", rocm_ok=True))
    check("gfx1151（Strix Halo）走 gfx1151 源",
          bool(p) and "gfx1151" in p["index"], str(p))

    # 家族冒充：gfx1103 = 780M/760M/740M，不在官方列表但同属 RDNA3
    p = B.rocm_plan(_gpu(arch="gfx1103", rocm_ok=False,
                         name="AMD Radeon(TM) 780M Graphics"))
    check("gfx1103 允许家族冒充", bool(p), str(p))
    check("gfx1103 用 gfx1100 的 wheel",
          p.get("variant") == "rocm-gfx1100", p.get("variant"))
    check("gfx1103 必须带 HSA_OVERRIDE=11.0.0",
          p.get("hsa") == "11.0.0", p.get("hsa"))
    check("gfx1103 标记为非原生", p.get("native") is False)
    check("gfx1103 note 里写明实验性", "实验" in p.get("note", ""), p.get("note"))

    # 无解：RDNA2 / RDNA1（Windows ROCm 不支持），必须明确返回 {}
    for arch in ("gfx1030", "gfx1032", "gfx1010"):
        check("%s 不给 ROCm 方案（Windows 无支持）" % arch,
              B.rocm_plan(_gpu(arch=arch, rocm_ok=False)) == {})
    check("认不出架构时返回 {}（不猜）",
          B.rocm_plan(_gpu(arch="", rocm_ok=False)) == {})
    check("未知架构返回 {}",
          B.rocm_plan(_gpu(arch="gfx9999", rocm_ok=False)) == {})


def t_is_rocm_variant():
    """B. 变体名判定"""
    for v, want in (("rocm-gfx1100", True), ("ROCM-gfx1200", True),
                    ("cu130", False), ("cu126", False), ("cpu", False), ("", False)):
        check("is_rocm_variant(%r) == %s" % (v, want),
              B.is_rocm_variant(v) is want)


def t_torch_build_of():
    """C. 从 version.py 认 ROCm 构建（不 import torch）"""
    tmp = tempfile.mkdtemp(prefix="cfrocm_")
    try:
        py = os.path.join(tmp, "python.exe")
        open(py, "wb").close()
        tv = os.path.join(tmp, "Lib", "site-packages", "torch")
        os.makedirs(tv, exist_ok=True)

        def write(body):
            with open(os.path.join(tv, "version.py"), "w", encoding="utf-8") as f:
                f.write(body)

        write('cuda = None\nhip = "7.13.0a20260513"\n')
        check("ROCm 版被认成 rocm", P.torch_build_of(py) == "rocm",
              str(P.torch_build_of(py)))

        write('cuda: Optional[str] = None\nhip = "6.2.41134"\n')
        check("带类型注解的 ROCm 也认得",
              P.torch_build_of(py) == "rocm", str(P.torch_build_of(py)))

        write('cuda: Optional[str] = "13.0"\nhip = None\n')
        check("CUDA 版仍是 cuda", P.torch_build_of(py) == "cuda",
              str(P.torch_build_of(py)))

        write('cuda: Optional[str] = None\nhip = None\n')
        check("纯 CPU 版仍是 cpu", P.torch_build_of(py) == "cpu",
              str(P.torch_build_of(py)))

        write('# nothing\n')
        check("没有 cuda/hip 字段返回 None", P.torch_build_of(py) is None)

        os.remove(os.path.join(tv, "version.py"))
        check("version.py 不存在返回 None", P.torch_build_of(py) is None)
        check("解释器不存在返回 None",
              P.torch_build_of(os.path.join(tmp, "nope.exe")) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_post_check():
    """D. 安装后自检的 ROCm 分支"""
    real_run = B.run

    class R:
        def __init__(self, code, out):
            self.returncode = code
            self.stdout = out
            self.stderr = ""

    cases = [
        # (variant, 输出, 期望, 说明)
        ("rocm-gfx1100", "2.9.1+rocm7.13\ncpu\n7.13.0a\n", True,
         "HIP 版应通过（cuda 字段是 cpu 也不能误判）"),
        ("rocm-gfx1100", "2.9.1\ncpu\n\n", False,
         "没有 HIP 运行时必须失败（装成了 CPU 版）"),
        ("rocm-gfx1100", "2.9.1\n13.0\n\n", False,
         "ROCm 变体却装出 CUDA 版，必须失败"),
        ("cu130", "2.9.1\n13.0\n\n", True, "CUDA 版正常通过"),
        ("cu130", "2.9.1\ncpu\n\n", False, "CUDA 变体装成 CPU 版必须失败"),
        ("cpu", "2.9.1\ncpu\n\n", True, "CPU 版正常通过"),
    ]
    try:
        for variant, out, want, label in cases:
            B.run = lambda *a, **k: R(0, out)
            got = B._torch_post_check("python.exe", variant, log=lambda *_: None)
            check(label, got is want, "got=%s want=%s" % (got, want))

        B.run = lambda *a, **k: R(1, "")
        check("import 崩了返回 False",
              B._torch_post_check("python.exe", "rocm-gfx1100",
                                  log=lambda *_: None) is False)
    finally:
        B.run = real_run


def main():
    print("=" * 62)
    t_rocm_plan()
    print("-" * 62)
    t_is_rocm_variant()
    print("-" * 62)
    t_torch_build_of()
    print("-" * 62)
    t_post_check()
    print("=" * 62)
    print("结果: %d/%d 通过" % (_N[0] - len(FAILS), _N[0]))
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
