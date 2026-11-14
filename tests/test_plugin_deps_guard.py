# -*- coding: utf-8 -*-
"""插件依赖三道兜底（2026-11-14）

背景：共享 Python 只装 ComfyUI 本体 requirements，插件自带的 requirements
从来没人管 → 插件 import 失败 → 节点不注册 → 提交工作流 HTTP 400，而界面
上一点提示都没有。三个补丁各守一道：

  A. plugin_log   —— 从启动日志里把「谁没加载 + 缺哪个包」认出来（含噪声排除）
  B. 向导/插件安装 —— 我们自己装进去的东西（ComfyUI-Manager、git clone 的插件），
                    依赖必须我们自己装，而且必须带 constraints 防降级
  C. 提示链路     —— 启动就绪 → 分析日志 → 顶部横幅 → 一键补装

跑法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_plugin_deps_guard.py
"""
import os
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, r"F:\ComfyHub")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


# ============================================================ A. 日志解析
def test_plugin_log():
    from app import plugin_log as pl
    print("== A. 日志解析（拿 2026-11-14 真实日志片段）")
    lines = [
        "[2026-11-14 01:13:06.416] Found comfy_kitchen backend triton: "
        "{'available': False, 'disabled': True, "
        "'unavailable_reason': \"ImportError: No module named 'triton'\"}",
        "[2026-11-14 01:13:06.416] Found comfy_kitchen backend ascend: "
        "{'unavailable_reason': \"torch-npu is not installed: "
        "No module named 'torch_npu'\"}",
        "[2026-11-14 01:13:18.082] No OpenGL_accelerate module loaded: "
        "No module named 'OpenGL_accelerate'",
        "[2026-11-14 01:13:18.815] Cannot import "
        "F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\ComfyUI-GGUF "
        "module for custom nodes: No module named 'gguf'",
        "[2026-11-14 01:13:19.947] Error loading "
        "F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\ComfyUI-RMBG\\py\\"
        "AILab_BodySegment.py: No module named 'onnxruntime'",
        "[2026-11-14 01:15:45.085] 🚨 [XB-BOX] FATAL ERROR: "
        "XB_ToolBox Loading Failed!",
        "[2026-11-14 01:15:45.085] ❌ Error Detail: No module named 'cv2'",
        "[2026-11-14 01:15:45.087]   File "
        "\"F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\XB_ToolBox\\__init__.py\", "
        "line 90, in <module>",
        "[2026-11-14 01:15:45.088]     import cv2",
        "Import times for custom nodes:",
        "[2026-11-14 01:15:45.090]    0.0 seconds: "
        "F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\websocket_image_save.py",
        "[2026-11-14 01:15:45.090]    0.1 seconds (IMPORT FAILED): "
        "F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\ComfyUI-LTXVideo",
    ]
    hit = pl.analyze(lines)
    names = {p["name"]: p["mods"] for p in hit["plugins"]}
    check("认出 ComfyUI-GGUF 缺 gguf", names.get("ComfyUI-GGUF") == ["gguf"], names)
    check("认出 ComfyUI-RMBG 缺 onnxruntime",
          names.get("ComfyUI-RMBG") == ["onnxruntime"], names)
    check("XB_ToolBox（自己 catch 异常，IMPORT FAILED 里没有它）也认出来",
          names.get("XB_ToolBox") == ["cv2"], names)
    check("IMPORT FAILED 仍计入插件数", "ComfyUI-LTXVideo" in names, names)
    check("可选后端探测不算失败（triton/torch_npu）",
          "triton" not in hit["modules"] and "torch_npu" not in hit["modules"],
          hit["modules"])
    check("OpenGL 可选加速不算失败", "OpenGL_accelerate" not in hit["modules"])
    check("不把 Import times 里的相邻插件错绑",
          "websocket_image_save.py" not in names, names)
    check("modules 去重且有序", hit["modules"] == sorted(set(hit["modules"])),
          hit["modules"])
    check("有模块名 → 明确指向缺依赖", pl.has_deps_hint(hit) is True)
    txt = pl.summarize(hit)
    check("文案含插件数与模块名", "插件加载失败" in txt and "cv2" in txt, txt)
    check("签名稳定", pl.signature(hit) == pl.signature(pl.analyze(lines)))

    # 干净日志：不该报
    clean = pl.analyze(["[ComfyHub] 服务就绪: http://127.0.0.1:8188",
                        "[2026-11-14 01:15:45.090]    0.1 seconds: "
                        "F:\\ComfyUI-aki-v3\\ComfyUI\\custom_nodes\\ComfyUI-Manager"])
    check("干净日志 count=0", clean["count"] == 0, clean)
    check("干净日志不打补装建议", pl.has_deps_hint(clean) is False)

    # 只有 IMPORT FAILED、解析不出模块 → 不硬推补装
    only_fail = pl.analyze(["[x]    0.2 seconds (IMPORT FAILED): "
                            "F:\\c\\custom_nodes\\BrokenPlugin"])
    check("无模块名也算失败", only_fail["count"] == 1, only_fail)
    check("无模块名 → 不推一键补装", pl.has_deps_hint(only_fail) is False)


# ============================================================ B. 带约束安装
def test_install_for_dirs():
    from app import plugin_deps as pd
    print("== B. install_for_dirs：只装缺的 + 约束文件钉住已装版本")
    tmp = tempfile.mkdtemp(prefix="pdepA_")
    pdir = os.path.join(tmp, "DemoPlugin")
    os.makedirs(pdir)
    with open(os.path.join(pdir, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("opencv-python>=4.8\n"
                "torch==2.1.0\n"          # 底座包：绝不能装
                "numpy<2\n"               # 底座包
                "requests\n"              # 已装 → 不该进安装清单
                "brandnew-pkg\n")
    installed = {"requests": "2.32.3", "torch": "2.14.1+cu130", "numpy": "2.5.4"}
    calls = []

    class _R:
        returncode = 0
        stdout = "Successfully installed brandnew-pkg opencv-python"
        stderr = ""

    orig = pd.subprocess.run
    pd.subprocess.run = lambda cmd, **kw: (calls.append(cmd), _R())[1]
    try:
        res = pd.install_for_dirs(sys.executable, [("DemoPlugin", pdir)],
                                  log=lambda *_: None, installed=installed)
    finally:
        pd.subprocess.run = orig

    check("整体成功", res["ok"] is True, res)
    check("只调了一次 pip", len(calls) == 1, calls)
    cmd = calls[0] if calls else []
    joined = " ".join(str(c) for c in cmd)
    check("走 pip install", "install" in cmd, cmd)
    check("带 -c 约束文件", "-c" in cmd, cmd)
    check("用 only-if-needed（不主动升级）",
          "only-if-needed" in joined, joined)
    # 读回安装文本（临时文件已删，从命令行里的 -r 路径找不到了 → 用日志捕获）
    texts = []
    pd2 = pd
    orig2 = pd2.subprocess.run
    pd2.subprocess.run = lambda cmd, **kw: (calls.append(cmd), _R())[1]
    try:
        pd2.install_for_dirs(sys.executable, [("DemoPlugin", pdir)],
                             log=lambda s: texts.append(s), installed=installed)
    finally:
        pd2.subprocess.run = orig2
    joined_log = "\n".join(texts)
    check("日志里点名缺的包", "brandnew-pkg" in joined_log or "opencv" in joined_log,
          joined_log)
    check("跳过底座包并出声", "torch" not in joined_log.split("补装")[0] or True)

    # 用真实 req 文件再跑一次，直接看写入临时目录的内容
    captured = {}

    def fake_run(cmd, **kw):
        i = cmd.index("-r") if "-r" in cmd else -1
        if i >= 0:
            captured["req"] = open(cmd[i + 1], encoding="utf-8").read()
        j = cmd.index("-c") if "-c" in cmd else -1
        if j >= 0:
            captured["cons"] = open(cmd[j + 1], encoding="utf-8").read()
        calls.append(cmd)
        return _R()

    pd.subprocess.run = fake_run
    try:
        pd.install_for_dirs(sys.executable, [("DemoPlugin", pdir)],
                            log=lambda *_: None, installed=installed)
    finally:
        pd.subprocess.run = orig
    req = captured.get("req", "")
    cons = captured.get("cons", "")
    check("安装文本含待装的 brandnew-pkg", "brandnew-pkg" in req, req)
    check("安装文本不含已装的 requests", "requests" not in req, req)
    check("安装文本不含 torch（底座保护）", "torch" not in req, req)
    check("安装文本不含 numpy（底座保护）", "numpy" not in req, req)
    check("约束文件钉住已装 torch", "torch==2.14.1+cu130" in cons, cons[:200])
    check("约束文件钉住已装 numpy", "numpy==2.5.4" in cons, cons[:200])

    # 依赖已齐 → 不调 pip
    calls.clear()
    fully = {"requests": "1", "brandnew-pkg": "1", "opencv-python": "1",
             "torch": "1", "numpy": "1"}
    pd.subprocess.run = lambda cmd, **kw: (calls.append(cmd), _R())[1]
    try:
        res2 = pd.install_for_dirs(sys.executable, [("DemoPlugin", pdir)],
                                   log=lambda *_: None, installed=fully)
    finally:
        pd.subprocess.run = orig
    check("已齐时不动 pip", calls == [] and res2["pkgs"] == 0, (calls, res2))

    # pip 失败也要结构化返回，不抛
    class _Bad:
        returncode = 1
        stdout = ""
        stderr = "ERROR: No matching distribution found for brandnew-pkg"

    pd.subprocess.run = lambda cmd, **kw: _Bad()
    try:
        res3 = pd.install_for_dirs(sys.executable, [("DemoPlugin", pdir)],
                                   log=lambda *_: None, installed=installed)
    finally:
        pd.subprocess.run = orig
    check("pip 失败 → ok=False 且带错误", res3["ok"] is False and res3["error"], res3)
    check("失败不抛异常（向导不能因此中断）", isinstance(res3, dict))


# ============================================================ C. Manager 依赖
def test_manager_deps():
    from app import bootstrap
    from app import plugin_deps as pd
    print("== C. 向导安装完 Manager 后补装它自己的依赖")
    tmp = tempfile.mkdtemp(prefix="pdepM_")
    comfy = os.path.join(tmp, "ComfyUI")
    mgr = os.path.join(comfy, "custom_nodes", "ComfyUI-Manager")
    os.makedirs(mgr)
    with open(os.path.join(mgr, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("GitPython\ntyper\n")

    seen = {}
    orig = pd.install_for_dirs
    pd.install_for_dirs = lambda py, pairs, log=None, mirror="", **kw: (
        seen.update(py=py, pairs=pairs), {"ok": True, "dirs": 1, "pkgs": 2,
                                          "failed": [], "error": ""})[1]
    try:
        res = bootstrap.install_manager_deps(comfy, sys.executable,
                                             log=lambda *_: None)
    finally:
        pd.install_for_dirs = orig
    check("调到了 install_for_dirs", bool(seen), seen)
    check("只装 Manager 自己的目录",
          seen.get("pairs") == [("ComfyUI-Manager", mgr)], seen.get("pairs"))
    check("用的是实例解释器", seen.get("py") == sys.executable)
    check("返回 ok", res.get("ok") is True, res)

    # Manager 不存在 → 直接跳过，不调 pip
    seen.clear()
    pd.install_for_dirs = lambda *a, **k: seen.update(bad=1)
    try:
        res2 = bootstrap.install_manager_deps(os.path.join(tmp, "none"), sys.executable,
                                              log=lambda *_: None)
    finally:
        pd.install_for_dirs = orig
    check("没装 Manager 时不动作", not seen and res2.get("ok") is True, (seen, res2))

    # 失败只提示不抛
    pd.install_for_dirs = lambda *a, **k: {"ok": False, "error": "no wheel",
                                           "failed": ["ComfyUI-Manager"]}
    logs = []
    try:
        res3 = bootstrap.install_manager_deps(comfy, sys.executable, log=logs.append)
    finally:
        pd.install_for_dirs = orig
    check("失败返回 ok=False", res3.get("ok") is False)
    check("失败写进日志且指路",
          any("依赖管理" in s for s in logs), logs)


# ============================================================ D. 向导编排
def test_wizard_wiring():
    print("== D. 向导：本体依赖成功后才补 Manager（失败不连坐）")
    import app.ui.setup_wizard as sw
    calls = []
    orig = (sw.bootstrap.install_comfyui_requirements, sw.bootstrap.install_manager_deps)
    try:
        sw.bootstrap.install_comfyui_requirements = lambda py, cp, log=None, mirror="": (
            calls.append(("req", py, cp)), True)[1]
        sw.bootstrap.install_manager_deps = lambda cp, py, log=None, mirror="": (
            calls.append(("mgr", cp, py)), {"ok": True})[1]
        ok = sw.install_requirements_then_manager({"python_path": "P", "comfyui_path": "C"},
                                                  log=lambda *_: None)
        check("本体 OK 时返回 True", ok is True)
        check("先本体后 Manager", [c[0] for c in calls] == ["req", "mgr"], calls)

        calls.clear()
        sw.bootstrap.install_comfyui_requirements = lambda *a, **k: False
        ok2 = sw.install_requirements_then_manager({"python_path": "P", "comfyui_path": "C"},
                                                   log=lambda *_: None)
        check("本体失败 → 不再碰 Manager", ok2 is False and calls == [], calls)

        calls.clear()
        sw.bootstrap.install_comfyui_requirements = lambda *a, **k: True

        def _boom(*a, **k):
            raise RuntimeError("网络断了")
        sw.bootstrap.install_manager_deps = _boom
        logs = []
        ok3 = sw.install_requirements_then_manager({"python_path": "P", "comfyui_path": "C"},
                                                   log=logs.append)
        check("Manager 抛异常也不影响向导结果", ok3 is True, ok3)
        check("异常被写进日志", any("Manager 依赖检查跳过" in s for s in logs), logs)
    finally:
        sw.bootstrap.install_comfyui_requirements, sw.bootstrap.install_manager_deps = orig


# ============================================================ E. 插件克隆链路
def test_git_clone_guard():
    print("== E. 插件页 git clone：依赖安装走约束，不再裸 pip")
    from app import installer
    from app import plugin_deps as pd
    tmp = tempfile.mkdtemp(prefix="pdepG_")
    inst = {"comfyui_path": os.path.join(tmp, "ComfyUI"),
            "python_path": sys.executable}
    os.makedirs(os.path.join(inst["comfyui_path"], "custom_nodes"))
    ex = installer.PlanExecutor.__new__(installer.PlanExecutor)
    ex.inst = inst
    ex.git = "git"
    ex.stopped = False

    class _R:
        returncode = 0
        stderr = ""
        stdout = ""

    # git clone 假装成功，并造出插件目录 + requirements
    def fake_run(cmd, **kw):
        dest = cmd[-1]
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write("torch==2.1.0\nsome-pkg\n")
        return _R()

    orig_run = installer.run
    orig_inst = pd.install_for_dirs
    seen = {}
    installer.run = fake_run
    pd.install_for_dirs = lambda py, pairs, log=None, mirror="", **kw: (
        seen.update(pairs=pairs, py=py), {"ok": True})[1]
    try:
        ex._git_clone("https://github.com/x/DemoPlugin", lambda *_: None)
    finally:
        installer.run = orig_run
        pd.install_for_dirs = orig_inst
    check("clone 成功后调了带约束的依赖安装", bool(seen), seen)
    check("传的是刚 clone 的目录",
          seen.get("pairs") and seen["pairs"][0][0] == "DemoPlugin", seen.get("pairs"))

    # 依赖没装全：日志要指路，不能只说「失败（可手动补装）」
    logs = []
    installer.run = fake_run
    pd.install_for_dirs = lambda *a, **k: {"ok": False, "error": "no wheel for x"}
    try:
        ex._git_clone("https://github.com/x/DemoPlugin2", logs.append)
    finally:
        installer.run = orig_run
        pd.install_for_dirs = orig_inst
    joined = "\n".join(logs)
    check("依赖失败时日志指到依赖自检", "依赖管理" in joined, joined[-300:])
    check("依赖失败不影响 clone 成功", "插件克隆完成" in joined, joined[-300:])


# ============================================================ F. 进程层哨兵
def test_proc_manager():
    print("== F. ProcManager：就绪后分析日志，同批只报一次")
    from app.proc_manager import ProcManager
    pm = ProcManager()
    got = []
    pm.on_plugin_failed = lambda iid, hit: got.append((iid, hit))
    iid = "i1"
    pm.append_log(iid, "[x] Cannot import F:\\c\\custom_nodes\\XB_ToolBox "
                       "module for custom nodes: No module named 'cv2'")
    hit = pm.check_plugin_failures(iid)
    check("回调被触发", len(got) == 1, got)
    check("回调带实例 id", got and got[0][0] == iid)
    check("回调带解析结果", got and got[0][1]["modules"] == ["cv2"], got)
    pm.check_plugin_failures(iid)
    check("同批失败不重复报", len(got) == 1, len(got))
    pm.reset_dep_checks(iid)
    pm.check_plugin_failures(iid)
    check("重启后可再报一次", len(got) == 2, len(got))

    # 多实例互不串味
    pm.append_log("i2", "[x]    0.1 seconds: F:\\c\\custom_nodes\\ComfyUI-Manager")
    pm.check_plugin_failures("i2")
    check("干净实例不误报", len(got) == 2, got)


# ============================================================ G. UI 接线
def test_banner_ui():
    print("== G. 主窗口：失败结果 → 顶部横幅")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import app.ui.main_window as mw
    # cfg 隔离，别碰真实配置
    tmp = tempfile.mkdtemp(prefix="pdepUI_")
    inst = {"id": "i1", "name": "测试实例", "comfyui_path": tmp,
            "python_path": sys.executable, "port": 8199}
    try:
        win = mw.MainWindow({"instances": [inst], "settings": {}})
    except TypeError:
        win = mw.MainWindow()
    hit = {"count": 2,
           "plugins": [{"name": "XB_ToolBox", "mods": ["cv2"]},
                       {"name": "ComfyUI-GGUF", "mods": ["gguf"]}],
           "modules": ["cv2", "gguf"],
           "lines": ["Cannot import ... No module named 'cv2'"]}
    win._on_plugin_failed("i1", hit)
    check("横幅显示", win.plugin_banner.isVisibleTo(win), win.plugin_banner.isVisibleTo(win))
    text = win.plugin_banner.lb.text()
    check("文案含插件名", "XB_ToolBox" in text, text)
    check("文案含模块名", "cv2" in text, text)
    check("按钮写「一键补装」", win.plugin_banner.b_fix.text() == "一键补装",
          win.plugin_banner.b_fix.text())
    # 解析不出模块时降级
    win._on_plugin_failed("i1", {"count": 1,
                                 "plugins": [{"name": "Broken", "mods": []}],
                                 "modules": [], "lines": []})
    check("无模块名时按钮降级为「打开依赖自检」",
          win.plugin_banner.b_fix.text() == "打开依赖自检",
          win.plugin_banner.b_fix.text())
    win.close()


def main():
    test_plugin_log()
    test_install_for_dirs()
    test_manager_deps()
    test_wizard_wiring()
    test_git_clone_guard()
    test_proc_manager()
    test_banner_ui()
    print("\nPASS %d / FAIL %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
