# -*- coding: utf-8 -*-
"""torch CUDA 安装链修复回归（2026-10-31）

覆盖：
1. 全新安装（ModuleNotFoundError）不带 --force-reinstall --no-deps（防残缺 torch）
2. 安装损坏（WinError 193）才带强制重装参数
3. detect_torch_step：CPU 版仅在检测到 N 卡时判 "cpu"；无 N 卡判 "ok"
4. _pip_report_wheels 解析 pip --report 清单（文件名/URL/sha256）
5. cu 安装：多源断点续传预下载 wheel（含国内镜像轮换+sha 透传）→ 本地 wheel
   显式安装 → 变体感知自检（装 cu 出 +cpu 判失败）
6. 复用本地 wheel：sha 校验通过则跳过下载

用法：.venv_build/Scripts/python.exe tests/test_torch_install_fix.py
"""
import os
import sys
import json
import hashlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app import bootstrap


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def make_run(detect, post):
    """按命令行内容分派的 run 桩：detect=import 检测结果，post=安装后自检结果"""
    def fake_run(cmd, **kw):
        s = " ".join(str(c) for c in cmd)
        if "pip" in cmd and "uninstall" in s:
            return R(0)
        if "torch.__version__" in s:
            return post
        if "getattr(torch.version" in s:
            return detect
        return R(0)
    return fake_run


captured = {}


def fake_pip_install(python_exe, pkgs, index_url, log=print, extra=None, timeout=5400):
    captured["extra"] = list(extra or [])
    captured["pkgs"] = list(pkgs)
    return True


bootstrap.pip_install = fake_pip_install
bootstrap._free_gb = lambda p: 100.0
bootstrap.detect_gpu = lambda: bootstrap.GpuInfo()

# -------------------------------------------------- 1. 全新安装不带强制重装参数
bootstrap.run = make_run(R(1, err="ModuleNotFoundError: No module named 'torch'"),
                         R(0, out="2.15.0+cpu\ncpu"))
ok = bootstrap.install_torch("python.exe", "cpu")
check("全新安装不带 --force-reinstall --no-deps",
      ok and "--force-reinstall" not in captured.get("extra", [])
      and "--no-deps" not in captured.get("extra", []), str(captured.get("extra")))

# -------------------------------------------------- 2. 损坏才强制重装
bootstrap.run = make_run(
    R(1, err="OSError: [WinError 193] %1 is not a valid Win32 application"),
    R(0, out="2.15.0+cpu\ncpu"))
ok = bootstrap.install_torch("python.exe", "cpu")
check("损坏安装带 --force-reinstall --no-deps",
      ok and "--force-reinstall" in captured.get("extra", [])
      and "--no-deps" in captured.get("extra", []), str(captured.get("extra")))

# -------------------------------------------------- 3. CPU 版判定看有没有 N 卡
bootstrap.run = make_run(R(0, out="2.15.0+cpu\ncpu"), R(0, out="2.15.0+cpu\ncpu"))
bootstrap.detect_gpu = lambda: bootstrap.GpuInfo(vendor="nvidia", name="RTX 4070 Ti")
st, info, _ = bootstrap.detect_torch_step("python.exe")
check("CPU torch + N 卡 → cpu（未达标）", st == "cpu", info)
bootstrap.detect_gpu = lambda: bootstrap.GpuInfo()
st, info, _ = bootstrap.detect_torch_step("python.exe")
check("CPU torch + 无 N 卡 → ok（CPU 即合适）", st == "ok", info)

# -------------------------------------------------- 4. wheel 清单解析
PLAN_JSON = {
    "install": [
        {"download_info": {
            "url": "https://download.pytorch.org/whl/cu130/"
                   "torch-2.15.0%2Bcu130-cp313-cp313-win_amd64.whl",
            "archive_info": {"hashes": {"sha256": "abc"}}}},
        {"download_info": {
            "url": "https://download.pytorch.org/whl/cu130/"
                   "torchvision-0.29.2%2Bcu130-cp313-cp313-win_amd64.whl",
            "archive_info": {"hashes": {"sha256": "def"}}}},
        {"download_info": {
            "url": "https://download.pytorch.org/whl/cu130/"
                   "torchaudio-2.11.1%2Bcu130-cp313-cp313-win_amd64.whl",
            "archive_info": {"hashes": {"sha256": "ghi"}}}},
    ]
}


def fake_run_report(cmd, **kw):
    rpt = cmd[cmd.index("--report") + 1]
    with open(rpt, "w", encoding="utf-8") as f:
        json.dump(PLAN_JSON, f)
    return R(0)


_run_real = bootstrap.run
bootstrap.run = fake_run_report
plan = bootstrap._pip_report_wheels(
    "python.exe", ["torch", "torchvision", "torchaudio"], "https://idx")
check("wheel 清单解析（文件名/URL/sha）",
      bool(plan) and plan[0][0] == "torch-2.15.0+cu130-cp313-cp313-win_amd64.whl"
      and plan[0][1].endswith(".whl") and plan[0][2] == "abc" and len(plan) == 3,
      str(plan))
bootstrap.run = _run_real

# -------------------------------------------------- 5. cu 本地 wheel 流
NAMES = ["torch-2.15.0+cu130-cp313-cp313-win_amd64.whl",
         "torchvision-0.29.2+cu130-cp313-cp313-win_amd64.whl",
         "torchaudio-2.11.1+cu130-cp313-cp313-win_amd64.whl"]
PLAN5 = [(n, f"https://download.pytorch.org/whl/cu130/{n}", f"sha-{i}")
         for i, n in enumerate(NAMES)]
dl_calls = []


def fake_download_multi(srcs, dest, log=print, max_retry=8, proxy="",
                        expected_sha=None, timeout=20):
    dl_calls.append((list(srcs), dest, expected_sha))
    with open(dest, "wb") as f:
        f.write(b"wheel-bytes")
    return True


bootstrap.download_multi = fake_download_multi
bootstrap._pip_report_wheels = lambda *a, **k: PLAN5
bootstrap.run = make_run(R(1, err="ModuleNotFoundError: No module named 'torch'"),
                         R(0, out="2.15.0+cu130\n13.0"))
captured.clear()
ok = bootstrap.install_torch("python.exe", "cu130", wheels_dir=tempfile.mkdtemp())
check("cu 安装走本地 wheel 流且成功",
      ok and len(dl_calls) == 3 and len(captured.get("pkgs", [])) == 3
      and all(os.path.basename(d) in " ".join(captured["pkgs"]) for _, d, _ in dl_calls),
      f"dl={len(dl_calls)} pkgs={captured.get('pkgs')}")
check("下载源轮换含阿里+SJTU 镜像",
      all(len(srcs) == 3 and "mirrors.aliyun.com" in srcs[1]
          and "sjtu" in srcs[2] for srcs, _, _ in dl_calls),
      str(dl_calls[0][0] if dl_calls else ""))
check("期望 sha 透传给下载校验",
      all(s == f"sha-{i}" for i, (_, _, s) in enumerate(dl_calls)), str(dl_calls))

# 装 cu 出 +cpu → 判失败（防 pip"已满足"装了个寂寞）
bootstrap.run = make_run(R(1, err="ModuleNotFoundError: No module named 'torch'"),
                         R(0, out="2.15.0+cpu\ncpu"))
dl_calls.clear()
ok = bootstrap.install_torch("python.exe", "cu130", wheels_dir=tempfile.mkdtemp())
check("装 cu 出 +cpu 判失败（安装未生效）", ok is False, f"ok={ok}")

# -------------------------------------------------- 6. sha 命中复用本地 wheel
wdir = tempfile.mkdtemp()
PLAN6 = []
for i, n in enumerate(NAMES):
    body = f"reuse-{i}".encode()
    with open(os.path.join(wdir, n), "wb") as f:
        f.write(body)
    PLAN6.append((n, f"https://download.pytorch.org/whl/cu130/{n}",
                  hashlib.sha256(body).hexdigest()))
bootstrap._pip_report_wheels = lambda *a, **k: PLAN6
bootstrap.run = make_run(R(1, err="ModuleNotFoundError: No module named 'torch'"),
                         R(0, out="2.15.0+cu130\n13.0"))
dl_calls.clear()
captured.clear()
ok = bootstrap.install_torch("python.exe", "cu130", wheels_dir=wdir)
check("sha 命中复用本地 wheel（不重新下载）",
      ok and len(dl_calls) == 0 and len(captured.get("pkgs", [])) == 3,
      f"dl={len(dl_calls)}")

# -------------------------------------------------- 结果汇总
fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
sys.exit(1 if fails else 0)
