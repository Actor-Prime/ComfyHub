# -*- coding: utf-8 -*-
"""工作台显卡卡百分比 = GPU 使用率（utilization.gpu）回归用例（v0.8.41）

2026-11-22 用户要求：显卡卡进度条/副标题的百分比从「显存占用率」改成「GPU 使用率」，
显存 GB 数仍留卡面（2026-11-10），显存占比挪到 tooltip。

锁定三件事：
  1. sysinfo.gpu_vram_usage 同一次 nvidia-smi 查询带 utilization.gpu，
     返回 dict 含 gpu_percent（0-100）；percent（显存占比）保留给 tooltip 不删
  2. home_page 显卡卡副标题 = "显存 x / y GB · 使用率 z%"，进度条 pct 用 gpu_percent，
     tooltip 补显存占比一行；显存查询失败的兜底分支、AMD 分支保持原样
  3. i18n_en 有「使用率」英文词条

mock 方式：桩掉 app.sysinfo.run（nvidia-smi 子进程）返回三列 CSV，
不依赖测试机是否真有 N 卡。

用法: QT_QPA_PLATFORM=offscreen python tests/test_gpu_percent.py
"""
import os
import sys
import types
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  [{detail}]" if detail and not cond else ""))


# =====================================================================
# 1. gpu_vram_usage：三列 CSV 解析（桩掉 nvidia-smi）
# =====================================================================
from app import sysinfo  # noqa: E402


class _FakeRun:
    """桩：记录被调用的命令，返回固定 stdout（模拟 nvidia-smi 三列 CSV）"""

    def __init__(self, stdout):
        self.stdout = stdout
        self.cmds = []          # 每次调用收到的完整 argv

    def __call__(self, *args, **kw):
        self.cmds.append(list(args[0]) if args else [])
        return types.SimpleNamespace(stdout=self.stdout, returncode=0,
                                     stderr="")


def _usage(fake):
    """清 3s 缓存 → 用桩替换 sysinfo.run → 跑一次 gpu_vram_usage"""
    sysinfo._VRAM_CACHE["ts"] = 0.0
    sysinfo._VRAM_CACHE["data"] = {}
    with mock.patch.object(sysinfo, "run", fake):
        return sysinfo.gpu_vram_usage()


# ---- 1a. 正常三列：gpu_percent 存在且 percent 保留 ----
fake = _FakeRun("2148, 12288, 37")
v = _usage(fake)
check("返回 dict 含 gpu_percent", "gpu_percent" in v, v)
check("gpu_percent == 37.0", v.get("gpu_percent") == 37.0, v.get("gpu_percent"))
check("percent（显存占比）保留不删",
      abs(v.get("percent", -1) - 2148 / 12288 * 100) < 0.01, v.get("percent"))
check("used_mb/total_mb 正确",
      v.get("used_mb") == 2148.0 and v.get("total_mb") == 12288.0, v)
check("查询命令带 utilization.gpu",
      fake.cmds and "utilization.gpu" in fake.cmds[0][1],
      fake.cmds[0][1] if fake.cmds else "未调用")
check("查询命令仍是 csv,noheader,nounits",
      fake.cmds and "csv,noheader,nounits" in fake.cmds[0][2],
      fake.cmds[0][2] if fake.cmds else "未调用")

# ---- 1b. 3s 缓存：窗口内第二次调用不再 spawn nvidia-smi ----
fake2 = _FakeRun("100, 2000, 5")
sysinfo._VRAM_CACHE["ts"] = 0.0
sysinfo._VRAM_CACHE["data"] = {}
with mock.patch.object(sysinfo, "run", fake2):
    sysinfo.gpu_vram_usage()
    sysinfo.gpu_vram_usage()
check("3s 缓存：两次调用只 spawn 一次 nvidia-smi", len(fake2.cmds) == 1,
      len(fake2.cmds))

# ---- 1c. utilization 异常值 [N/A]（休眠/驱动异常）→ 0，不整段判失败 ----
v = _usage(_FakeRun("2148, 12288, [N/A]"))
check("utilization [N/A] → gpu_percent=0 且显存字段仍在",
      v.get("gpu_percent") == 0.0 and v.get("total_mb") == 12288.0, v)

# ---- 1d. 查询失败（无 N 卡 / nvidia-smi 缺失）→ {} ----
def _boom(*args, **kw):
    raise FileNotFoundError("nvidia-smi not found")


v = _usage(_boom)
check("查询抛异常 → {}", v == {}, v)

# ---- 1e. 空输出 → {}（splitlines()[0] 越界被 except 兜住） ----
v = _usage(_FakeRun(""))
check("空输出 → {}", v == {}, v)

# ---- 1f. gpu_percent 越界夹取 0-100 ----
v = _usage(_FakeRun("2148, 12288, 150"))
check("utilization 越界 → 夹到 100", v.get("gpu_percent") == 100.0,
      v.get("gpu_percent"))

# =====================================================================
# 2. 显卡卡 UI：副标题「使用率」+ 进度条用 gpu_percent + tooltip 显存占比
# =====================================================================
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication.instance() or QApplication(sys.argv[:1])

from app.bootstrap import GpuInfo  # noqa: E402
from app.ui.pages.home_page import HomePage  # noqa: E402


class _FakeMainWin:
    """HomePage 构造期只用到 current_inst()（磁盘卡兜底），其余入口不触发"""

    def current_inst(self):
        return None


NVIDIA_GPU = GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 4070 Ti",
                     vram_mb=12288, driver=560, torch_variant="cu130")
AMD_GPU = GpuInfo(vendor="amd", name="AMD Radeon RX 7900 XTX",
                  vram_mb=20480, arch="gfx1100")

# 构造页：N 卡 + 三列 CSV（2148/12288 MB，利用率 37%）
fake3 = _FakeRun("2148, 12288, 37")
sysinfo._VRAM_CACHE["ts"] = 0.0
sysinfo._VRAM_CACHE["data"] = {}
with mock.patch.object(sysinfo, "run", fake3), \
        mock.patch("app.bootstrap.detect_gpu", return_value=NVIDIA_GPU):
    page = HomePage({}, _FakeMainWin())
    page._t.stop()             # 别让 3s 定时器在测试期间乱刷

sub = page.card_gpu._sub.text()
tip = page.card_gpu.toolTip()
bar = page.card_gpu._bar

check("副标题含「使用率」", "使用率" in sub, sub)
check("副标题用 gpu_percent（37%）", "37%" in sub, sub)
check("副标题仍带显存 GB 数（2026-11-10 要求）",
      "显存" in sub and "2.1 / 12 GB" in sub, sub)
check("进度条值 = gpu_percent 37", bar.value() == 37, bar.value())
check("进度条可见（pct 非 None）", not bar.isHidden())
check("tooltip 含显存占比行（占用 17%）",
      "占用" in tip and "17%" in tip, tip)
check("tooltip 保留显存 GB 数字", "2.1 / 12 GB" in tip, tip)
check("tooltip 保留驱动/torch 行",
      "驱动" in tip and "cu130" in tip, tip)

# ---- 2b. 显存查询失败兜底分支（506-509）：静态显存 GB，无使用率、无进度条 ----
fake_empty = _FakeRun("")
sysinfo._VRAM_CACHE["ts"] = 0.0
sysinfo._VRAM_CACHE["data"] = {}
with mock.patch.object(sysinfo, "run", fake_empty), \
        mock.patch("app.bootstrap.detect_gpu", return_value=NVIDIA_GPU):
    page._refresh_gpu_card("检测到 NVIDIA")
sub2 = page.card_gpu._sub.text()
check("兜底分支：副标题是静态显存、无「使用率」",
      "使用率" not in sub2 and sub2 == "显存 12 GB", sub2)
check("兜底分支：进度条隐藏", page.card_gpu._bar.isHidden())
check("兜底分支：tooltip 仍有驱动行",
      "驱动" in page.card_gpu.toolTip(), page.card_gpu.toolTip())

# ---- 2c. AMD 分支（else）：副标题原样、tooltip 清空，保持不变 ----
with mock.patch("app.bootstrap.detect_gpu", return_value=AMD_GPU):
    page._refresh_gpu_card("检测到 AMD 显卡")
sub3 = page.card_gpu._sub.text()
check("AMD 分支：副标题 = fallback 原样", sub3 == "检测到 AMD 显卡", sub3)
check("AMD 分支：无「使用率」", "使用率" not in sub3, sub3)
check("AMD 分支：tooltip 清空", page.card_gpu.toolTip() == "",
      page.card_gpu.toolTip())
check("AMD 分支：进度条隐藏", page.card_gpu._bar.isHidden())

page.deleteLater()
qapp.processEvents()

# =====================================================================
# 3. i18n：「使用率」英文词条
# =====================================================================
from app.i18n import T, set_lang  # noqa: E402
from app.i18n_en import EN_DICT  # noqa: E402

check("i18n_en 有「使用率」词条", EN_DICT.get("使用率") == "Usage",
      EN_DICT.get("使用率"))
set_lang("en")
check("英文模式 T('使用率') == Usage", T("使用率") == "Usage", T("使用率"))
set_lang("zh")
check("中文模式 T('使用率') 原样返回", T("使用率") == "使用率", T("使用率"))

# =====================================================================
bad = [n for n, ok, _ in RESULTS if not ok]
print()
print("gpu_percent：%d/%d 通过" % (len(RESULTS) - len(bad), len(RESULTS)))
if bad:
    print("FAILED:")
    for n in bad:
        print("  - " + n)
sys.exit(1 if bad else 0)
