# -*- coding: utf-8 -*-
"""插件依赖自检回归（2026-11-14 用户报障引出）

背景：共享 Python 只装 ComfyUI 本体 requirements，插件自带的 requirements
从来没人装 → 插件 import 失败 → 节点不注册 → 工作流提交 HTTP 400。
这里覆盖三件事：
  1. 解析/比对逻辑（含底座包保护、git 依赖单列）
  2. 补装参数（约束文件钉死已装版本，避免插件把 torch 降级）
  3. 对话框与安装线程的接线（真的点「补装」，不是直调函数）

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_plugin_deps.py
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import plugin_deps as pd  # noqa: E402
from app.ui.pages import env_page  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print(" FAIL " + name + ("  ← " + str(detail) if detail else ""))


def make_tree(tmp):
    """造一个 ComfyUI 目录：两个插件，一个齐、一个缺（含底座包与 git 依赖）"""
    comfy = os.path.join(tmp, "ComfyUI")
    cn = os.path.join(comfy, "custom_nodes")
    a = os.path.join(cn, "PluginFull")
    b = os.path.join(cn, "PluginBroken")
    os.makedirs(a)
    os.makedirs(b)
    for d in (a, b):
        # 2026-11-22：真实插件目录必有 __init__.py，没有就会被判「目录不完整」
        open(os.path.join(d, "__init__.py"), "w", encoding="utf-8").write("")
    with open(os.path.join(a, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("numpy>=1.25\n# 注释\nopencv-python-headless\n")
    with open(os.path.join(b, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("torch==2.1.0\nnumpy\nopencv-python>=4.8\n"
                "diffusers\n"
                "git+https://github.com/facebookresearch/sam2\n"
                "onnxruntime-gpu ; platform_system == 'Windows'\n"
                "-r extra.txt\n")
    return comfy


def main():
    tmp = tempfile.mkdtemp(prefix="pdep_test_")
    comfy = make_tree(tmp)

    print("== A. requirements 解析 ==")
    items = pd.parse_requirements(os.path.join(comfy, "custom_nodes",
                                               "PluginBroken", "requirements.txt"))
    kinds = [(i["name"], i["kind"]) for i in items]
    check("普通包解析出包名", ("opencv-python", "install") in kinds, kinds)
    check("带 marker 的也认", ("onnxruntime-gpu", "install") in kinds, kinds)
    check("git+ 归为 special", ("", "special") in kinds, kinds)
    check("-r 归为 special", ("", "special") in kinds, kinds)
    check("注释被丢弃", all("注释" not in i["raw"] for i in items))

    print("== B. 比对：底座包单列、git 单列、不重复报 ==")
    installed = {"numpy": "2.5.4", "opencv-python-headless": "4.12"}
    rows = pd.scan(comfy, installed)
    by = {r["name"]: r for r in rows}
    full = by["PluginFull"]
    check("齐的插件缺 0", not full["missing"], full["missing"])
    broken = by["PluginBroken"]
    miss = [m["name"] for m in broken["missing"]]
    check("缺的包被列出", set(miss) == {"opencv-python", "diffusers",
                                        "onnxruntime-gpu"}, miss)
    check("底座包不进 missing", "torch" not in miss and "numpy" not in miss, miss)
    check("torch 缺失时进 base 提示", broken["base"] == ["torch"], broken["base"])
    check("git 依赖单列", any("sam2" in s for s in broken["special"]),
          broken["special"])
    check("重包被标出", "onnxruntime-gpu" in broken["heavy"], broken["heavy"])

    s = pd.summarize(rows)
    check("摘要：1 个插件缺依赖", s["bad"] == 1, s)
    check("摘要：缺 3 个包", s["missing"] == 3, s)

    print("== C. 拼 requirements：去重 + 保留原始约束 ==")
    rows2 = pd.scan(comfy, installed)
    rows2[0]["missing"].append({"name": "diffusers", "raw": "diffusers==0.25"})
    text, base, special = pd.build_requirements(rows2)
    lines = [x for x in text.splitlines() if x.strip()]
    check("diffusers 只出现一次（去重）", sum(1 for x in lines if "diffusers" in x) == 1,
          lines)
    check("torch / numpy 不进安装文本",
          not any(x.startswith(("torch", "numpy")) for x in lines), lines)
    check("base 提示带出 torch", "torch" in base, base)
    check("special 带出 git 依赖", any("sam2" in x for x in special), special)

    print("== D. 约束文件与 pip 参数（保护已装版本）==")
    cons = pd.write_constraints({"torch": "2.14.1+cu130", "numpy": "2.5.4",
                                 "transformers": "5.22.0"},
                                os.path.join(tmp, "c.txt"))
    body = open(cons, encoding="utf-8").read()
    check("约束文件钉死已装版本", "torch==2.14.1+cu130" in body, body)
    check("约束覆盖 transformers", "transformers==5.22.0" in body, body)
    args = pd.install_args(os.path.join(tmp, "r.txt"), cons,
                           "https://mirrors.aliyun.com/pypi/simple/")
    check("带 -r 与 -c", "-r" in args and "-c" in args, args)
    check("带镜像与 trusted-host", "-i" in args and "--trusted-host" in args, args)
    check("只在不必要时升级", "only-if-needed" in args, args)

    print("== E. 装完自检 verify ==")
    bad = pd.verify(sys.executable, ["json", "definitely_not_a_real_pkg_zz9"])
    check("真实模块通过", "json" not in bad, bad)
    check("假模块被标出", "definitely_not_a_real_pkg_zz9" in bad, bad)

    print("== F. 对话框：勾选/统计 ==")
    inst = {"id": "i1", "name": "甲", "python_path": sys.executable,
            "comfyui_path": comfy}
    dlg = env_page.PluginDepsDialog(None, inst, rows, installed)
    check("表格行数 = 插件数", dlg.tb.rowCount() == len(rows), dlg.tb.rowCount())
    check("缺依赖的行默认勾选", len(dlg._selected()) == 1, len(dlg._selected()))
    row_full = [i for i in range(dlg.tb.rowCount())
                if dlg.tb.item(i, 1).text() == "PluginFull"][0]
    flags = dlg.tb.item(row_full, 0).flags()
    check("齐的插件不可勾选", not (flags & Qt.ItemIsUserCheckable), flags)
    dlg._check_all(False)
    check("全不选后为空", dlg._selected() == [])
    dlg._check_all(True)
    check("全选回来", len(dlg._selected()) == 1)

    print("== G. 安装线程真的跑起来（monkeypatch 流式 pip）==")
    calls = []

    def fake_stream(py, args, on_line=None, on_state=None, **kw):
        calls.append((py, args))
        for ln in ("Looking in indexes: https://mirrors.aliyun.com/pypi/simple/",
                   "Collecting opencv-python>=4.8",
                   "Successfully installed opencv-python diffusers"):
            if on_line:
                on_line(ln)
        return {"code": 0, "idle": False, "timeout": False, "cancelled": False,
                "lines": ["Successfully installed opencv-python diffusers"]}

    orig_stream = pd.run_pip_stream
    orig_verify = pd.verify
    pd.run_pip_stream = fake_stream
    pd.verify = lambda py, names, **kw: []
    try:
        dlg._worker = None
        rows_sel = [r for r in dlg.rows if r["missing"]]
        w = env_page.PluginInstallWorker(sys.executable, rows_sel, installed,
                                        mirror="https://mirrors.aliyun.com/pypi/simple/")
        logs, done = [], []
        w.sig_log.connect(logs.append)
        w.sig_done.connect(lambda bad, err: done.append((bad, err)))
        w.run()                        # 同步跑，避免线程时序抖动
        check("pip 被调用", len(calls) >= 1, calls)
        args = calls[0][1] if calls else []
        check("调用的是 pip install -r", args[:2] == ["install", "-r"], args[:5])
        check("带约束文件 -c", "-c" in args, args)
        check("日志第一行说清「已开始」", any("开始" in x for x in logs), logs[:2])
        check("日志里有补装记录", any("补装" in x for x in logs), logs[:3])
        check("pip 输出逐行流出（不再是最后才吐）",
              any("Collecting" in x for x in logs), logs[:6])
        check("完成回调有结果", len(done) == 1, done)
        check("成功时无错误", done and not done[0][1], done)

        # 取消：run 之前就置位 → 不跑 pip，直接收尾
        calls.clear()
        w2 = env_page.PluginInstallWorker(sys.executable, rows_sel, installed)
        w2.cancel()
        logs2, done2 = [], []
        w2.sig_log.connect(logs2.append)
        w2.sig_done.connect(lambda bad, err: done2.append((bad, err)))
        w2.run()
        check("取消后不再调 pip", calls == [], calls)
        check("取消写进日志", any("终止" in x or "取消" in x for x in logs2), logs2)
        check("取消也有完成回调", len(done2) == 1, done2)
    finally:
        pd.run_pip_stream = orig_stream
        pd.verify = orig_verify

    print("== I. 目录不完整（缺 __init__.py / 空目录 / 多套一层）==")
    bad_dir = os.path.join(comfy, "custom_nodes", "Hollow")
    os.makedirs(bad_dir)
    open(os.path.join(bad_dir, "requirements.txt"), "w", encoding="utf-8").write("diffusers\n")
    why = pd.incomplete_reason(bad_dir)
    check("没有 __init__.py 也没有 .py → 判不完整", "缺少 __init__.py" in why, why)
    rows3 = pd.scan(comfy, installed)
    hollow = [r for r in rows3 if r["name"] == "Hollow"]
    check("不完整目录也出现在自检里", bool(hollow), [r["name"] for r in rows3])
    check("不完整原因带在行上",
          bool(hollow and hollow[0].get("incomplete")), hollow)
    check("不完整的排最前面", rows3[0]["name"] == "Hollow", [r["name"] for r in rows3])
    s3 = pd.summarize(rows3)
    check("摘要统计出不完整个数", s3.get("broken") == 1, s3)

    empty_dir = os.path.join(comfy, "custom_nodes", "EmptyOne")
    os.makedirs(empty_dir)
    check("空目录被判不完整", "空的" in pd.incomplete_reason(empty_dir),
          pd.incomplete_reason(empty_dir))

    nested = os.path.join(comfy, "custom_nodes", "Nested")
    os.makedirs(os.path.join(nested, "Nested"))
    open(os.path.join(nested, "Nested", "__init__.py"), "w", encoding="utf-8").write("")
    check("多套一层同名目录被判不完整",
          "多套了一层" in pd.incomplete_reason(nested), pd.incomplete_reason(nested))

    ok_dir = os.path.join(comfy, "custom_nodes", "PluginFull")
    check("正常插件不误报", pd.incomplete_reason(ok_dir) == "",
          pd.incomplete_reason(ok_dir))

    print("== J. 流式 pip：空闲超时能快速失败 ==")
    import time as _t

    class _Silent:
        def __iter__(self):
            while True:
                _t.sleep(0.5)
                yield ""            # 永不产生真实行

    class _FakeProc:
        pid = 999999
        returncode = None
        def __init__(self):
            self.stdout = _Silent()
        def wait(self, timeout=None):
            return None
        def poll(self):
            return None

    orig_popen = pd.subprocess.Popen
    pd.subprocess.Popen = lambda *a, **k: _FakeProc()
    states, lines = [], []
    try:
        res = pd.run_pip_stream(sys.executable, ["install", "x"],
                                on_line=lines.append, on_state=states.append,
                                idle_warn=0.10, idle_kill=0.25, poll=0.02)
    finally:
        pd.subprocess.Popen = orig_popen
    check("无输出到点即中止", res["idle"] is True, res)
    check("中止前先提醒可取消", "idle" in states, states)
    check("提示文案进了日志", any("没有新输出" in x for x in lines), lines)


    print("== H. 页面接线：点按钮真的把线程 start() 起来 ==")
    # 记忆里踩过：swap_worker 挂上后漏了 start()，线程永不跑、对话框永停初始态。
    # 这里用桩替换 worker / 对话框，只验证「按钮 → 线程启动 → 结果回调」这条链。
    class _StubWorker:
        def __init__(self, inst, *a, **kw):
            self.inst = inst
            self.started = False
            _HUB["worker"] = self

        def start(self):
            self.started = True

        class _Sig:
            def connect(self, fn):
                _HUB["cb"] = fn
        sig_done = _Sig()

    class _StubDlg:
        def __init__(self, parent, inst, rows, installed):
            _HUB["dlg_rows"] = rows

        def exec(self):
            return 0

        class _Sig:
            def connect(self, fn):
                pass
        sig_installed = _Sig()

    _HUB = {}
    class _MW:
        cfg = {"instances": [{"id": "i1", "name": "甲", "python_path": sys.executable,
                              "comfyui_path": comfy}], "settings": {}}
        def current_inst(self):
            return self.cfg["instances"][0]
        class procs:
            @staticmethod
            def is_running(inst):
                return False

    orig_w, orig_d = env_page.PluginScanWorker, env_page.PluginDepsDialog
    env_page.PluginScanWorker, env_page.PluginDepsDialog = _StubWorker, _StubDlg
    try:
        page = env_page.EnvPage({}, _MW())
        page._plugin_deps_check()
        w = _HUB.get("worker")
        check("按钮创建了 worker", w is not None)
        check("worker 被 start()（漏了就永不跑）", bool(w and w.started))
        check("结果回调已接线", callable(_HUB.get("cb")))
        # 模拟线程回结果 → 应该弹出对话框
        rows_live = pd.scan(comfy, {"numpy": "2.5.4", "opencv-python-headless": "1"})
        _HUB["cb"](rows_live, {}, "")
        check("回调后对话框拿到 rows", _HUB.get("dlg_rows") is not None,
              _HUB.get("dlg_rows"))
    finally:
        env_page.PluginScanWorker, env_page.PluginDepsDialog = orig_w, orig_d

    shutil.rmtree(tmp, ignore_errors=True)
    print("\nPASS %d / FAIL %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
