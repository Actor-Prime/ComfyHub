# -*- coding: utf-8 -*-
"""AI 补全队列回归（2026-11-01）

背景：工作流扫描常给出「0 步可自动执行 / 2 个节点要升级内核 / 3 个模型待下载」，
用户要求一键补齐。覆盖：
1. build_solve_queue：内核合并成一步、顺序 内核→插件→模型、无直链模型不入队
2. PlanExecutor 支持 upgrade_kernel：调 update_stable/switch_to + 同步依赖，失败即抛
3. 工作流页：有「AI 补全并执行」按钮、有升级项时自动点亮、点击后按队列起任务

用法：.venv_build/Scripts/python.exe tests/test_ai_solve.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication([])
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


_said = []
QMessageBox.warning = staticmethod(lambda *a, **k: _said.append(("warn", a[2] if len(a) > 2 else "")))
QMessageBox.information = staticmethod(lambda *a, **k: _said.append(("info", a[2] if len(a) > 2 else "")))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

from app import installer                      # noqa: E402
from app import core_nodes                     # noqa: E402

NOLOG = lambda *a, **k: None                   # noqa: E731

# -------------------------------------------------- 1. 队列构建
plan = {
    "steps": [{"type": "git_clone", "content": "https://github.com/x/Impact-Pack",
               "reason": "节点 ComfySwitchNode"}],
    "upgrades": [
        {"node": "QwenImage21Cache", "min_version": "0.37.0", "kernel_ver": "0.35.0"},
        {"node": "TextEncodeQwenImage21", "min_version": "0.37.0", "kernel_ver": "0.35.0"},
    ],
    "unresolvable": [],
    "models": [
        {"category": "unet", "file": "qwen_image_2.1_int8.safetensors", "downloadable": True,
         "source": {"repo": "Comfy-Org/Qwen", "path": "split_files/diffusion_models/q.safetensors",
                    "subdir": "diffusion_models"}},
        {"category": "vae", "file": "no_link.safetensors", "downloadable": False, "source": None},
    ],
}
q = installer.build_solve_queue(plan, log=NOLOG)
kinds = [s["type"] for s in q]
check("1a 内核只占一步（多个节点合并）", kinds.count("upgrade_kernel") == 1, str(kinds))
check("1b 顺序 = 内核 → 插件 → 模型", kinds == ["upgrade_kernel", "git_clone", "model_url"], str(kinds))
check("1c 内核步骤带上所需最低版本",
      "0.37.0" in q[0]["reason"] and "QwenImage21Cache" in q[0]["reason"], q[0]["reason"])
check("1d 无直链模型不入队（避免制造失败项）",
      all("no_link" not in str(s.get("content")) for s in q))
check("1e 模型步骤带 repo/path/subdir",
      q[-1]["content"]["repo"] == "Comfy-Org/Qwen"
      and q[-1]["content"]["subdir"] == "diffusion_models", str(q[-1]["content"]))

plan2 = {"steps": [], "upgrades": [], "unresolvable": ["MysteryNode"],
         "models": [{"file": "x", "downloadable": False, "source": None}]}
check("1f 什么都没有 → 空队列", installer.build_solve_queue(plan2, log=NOLOG) == [])

q3 = installer.build_solve_queue(plan, include_models=False, log=NOLOG)
check("1g 关掉「同时下载模型」后不含模型步",
      [s["type"] for s in q3] == ["upgrade_kernel", "git_clone"], str([s["type"] for s in q3]))

q4 = installer.build_solve_queue(plan, kernel_target="v0.40.0", log=NOLOG)
check("1h 指定内核 tag 时用该 tag", q4[0]["content"] == "v0.40.0", q4[0]["content"])
check("1i 默认走最新稳定版", q[0]["content"] == "stable", q[0]["content"])

# -------------------------------------------------- 2. 升级内核步骤
calls = {}


class FakeKM:
    def __init__(self, path):
        self.path = path
        calls["path"] = path

    def update_stable(self):
        calls["action"] = "stable"
        return True, "已更新到 v0.37.2"

    def switch_to(self, tag):
        calls["action"] = ("switch", tag)
        return True, f"已切到 {tag}"


import app.tools as tools_mod                  # noqa: E402
import app.bootstrap as boot_mod               # noqa: E402

tools_mod.KernelManager = FakeKM
boot_mod.install_comfyui_requirements = lambda py, path, log=print: calls.setdefault(
    "deps", (py, path))

ex = object.__new__(installer.PlanExecutor)
ex.inst = {"comfyui_path": "F:/fake/ComfyUI", "python_path": "F:/fake/py.exe"}
ex.stopped = False

ex._upgrade_kernel("stable", NOLOG)
check("2a stable → 调 update_stable", calls.get("action") == "stable", str(calls.get("action")))
check("2b 升级后按实例解释器同步依赖", calls.get("deps") == ("F:/fake/py.exe", "F:/fake/ComfyUI"),
      str(calls.get("deps")))

calls.clear()
ex._upgrade_kernel("v0.36.0", NOLOG)
check("2c 指定 tag → 调 switch_to", calls.get("action") == ("switch", "v0.36.0"),
      str(calls.get("action")))


class FailKM(FakeKM):
    def update_stable(self):
        return False, "checkout 失败：有本地修改"


tools_mod.KernelManager = FailKM
try:
    ex._upgrade_kernel("stable", NOLOG)
    raised = False
except RuntimeError as e:
    raised = "checkout 失败" in str(e)
check("2d 升级失败抛错（让计划停下而不是假装成功）", raised)
tools_mod.KernelManager = FakeKM

# -------------------------------------------------- 2e. AI 兜底限流
import app.ai_assist as ai_mod                 # noqa: E402

_asked = []


def _fake_ask(node, settings, log=print, timeout=25):
    _asked.append(node)
    return ""      # 全部识别失败，逼出最大调用次数


_orig_ask = ai_mod.ask_node_origin
ai_mod.ask_node_origin = _fake_ask
ai_mod.enabled = lambda st: True

scan8 = {"missing": {"__unknown__": [f"XNode{i}" for i in range(8)]},
         "models": {}, "comfyui_path": "F:/fake/ComfyUI", "kernel_ver": "0.35.0"}
p8 = installer.plan_from_scan(scan8, log=NOLOG, resolve_online=False,
                              ai_settings={"ai_enabled": True,
                                           "ai_base_url": "https://x.test/v1",
                                           "ai_api_key": "k", "ai_model": "m"})
check("2e 8 个未识别节点只问 AI 6 次（限流）", len(_asked) == 6, f"asked={len(_asked)}")
check("2f 超预算的节点进 unresolvable", len(p8["unresolvable"]) == 8,
      str(len(p8["unresolvable"])))
ai_mod.ask_node_origin = _orig_ask

# -------------------------------------------------- 3. 工作流页
from app.ui.pages import workflow_page as wf_mod   # noqa: E402


class FakeMain:
    def current_inst(self):
        return None

    def goto(self, *a, **k):
        pass


class FakeWorker:
    """替身 ExecWorker：记录步骤，不真跑"""
    last = None

    def __init__(self, inst, steps):
        FakeWorker.last = steps
        self._running = False
        from PySide6.QtCore import QObject, Signal  # noqa: F401

    def isRunning(self):
        return False

    def start(self):
        pass

    class _Sig:
        def connect(self, *a, **k):
            pass

    sig_log = _Sig()
    sig_done = _Sig()


wf_mod.ExecWorker = FakeWorker
page = wf_mod.WorkflowPage({"settings": {}, "instances": []}, FakeMain())
check("3a 有「AI 补全并执行」按钮", page.b_ai.text() == "🤖 AI 补全并执行", page.b_ai.text())
check("3b 初始禁用", not page.b_ai.isEnabled())

page.plan = plan
page._counts = (0, 1)          # 模拟图2 的状态：0 步可自动执行、1 个模型有直链
page._has_upgrades = True
page._sync_exec_btn()
check("3c 有升级项时 AI 按钮点亮", page.b_ai.isEnabled())
page._counts = (0, 0)
page._has_upgrades = False
page._sync_exec_btn()
check("3c2 全空时 AI 按钮保持灰", not page.b_ai.isEnabled())

page._current_inst = lambda: {"comfyui_path": "F:/fake/ComfyUI", "python_path": "p",
                              "name": "t", "id": "1"}
page.ck_models.setChecked(True)
page._ai_solve()
steps = FakeWorker.last or []
check("3d 点 AI 补全 → 队列为 内核+插件+模型",
      [s["type"] for s in steps] == ["upgrade_kernel", "git_clone", "model_url"],
      str([s["type"] for s in steps]))
check("3e 执行时记住「升级过内核」（用于提示重启）", page._last_had_kernel is True)

page.plan = {"steps": [], "upgrades": [], "unresolvable": [], "models": []}
_said.clear()
page._ai_solve()
check("3f 无需补的项 → 提示而不是空跑",
      any("没有需要补的项" in str(x[1]) for x in _said), str(_said[-1:]))

# -------------------------------------------------- 汇总
fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"AI 补全队列回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
if fails:
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
sys.exit(1 if fails else 0)
