# -*- coding: utf-8 -*-
"""更新脚本生成单测（2026-11-09 弹黑窗事故回归）。

事故复盘：`cmd /c start "" /min` 会弹可见控制台（/min 只是最小化）；
脚本末尾 `del "%~f0"` 自删 → cmd 报 "The batch file cannot be found."
并转入交互态残留提示符；timeout 在无控制台下报错不等待会变忙循环。

断言四条修复不变量：
  1. 脚本等待用 ping，绝不用 timeout
  2. 脚本绝不自删（不含 del "%~f0"）
  3. Popen 用 CREATE_NO_WINDOW 直跑 cmd /c，绝不含 start "" /min
  4. clean_stale 回收旧工作目录与旧安装包，保留 keep 指定的那个

用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/test_updater_script.py
"""
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import updater  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}  {detail}")


def make_env():
    """临时 updates 目录 + 桩掉 verify/_stop_instances/Popen，返回 (dir, captured)"""
    tmp = tempfile.mkdtemp(prefix="updtest_")
    captured = {}
    orig = (updater.updates_dir, updater.verify_signature,
            updater._stop_instances, updater.subprocess.Popen)
    updater.updates_dir = lambda: tmp
    updater.verify_signature = lambda path, expect="": {"ok": True}
    updater._stop_instances = lambda log=print: None

    def fake_popen(args, shell=False, creationflags=0, **kw):
        captured["args"] = list(args)
        captured["shell"] = shell
        captured["flags"] = creationflags
        return types.SimpleNamespace(pid=99999)

    updater.subprocess.Popen = fake_popen
    return tmp, captured, orig


def restore(orig):
    (updater.updates_dir, updater.verify_signature,
     updater._stop_instances, updater.subprocess.Popen) = orig


def test_apply_installer():
    print("== apply_installer 脚本与启动方式 ==")
    tmp, captured, orig = make_env()
    try:
        setup = os.path.join(tmp, "comfyhub_setup_9.9.exe")
        with open(setup, "wb") as f:
            f.write(b"MZ fake")
        r = updater.apply_installer(setup, log=lambda *a: None)
        check("返回 ok", r.get("ok") is True, str(r))
        cmd_path = r.get("script", "")
        check("脚本文件已生成", os.path.isfile(cmd_path), cmd_path)
        with open(cmd_path, encoding="utf-8") as f:
            body = f.read()

        check("等待用 ping（不用 timeout）",
              "ping -n" in body and "timeout" not in body, body[:200])
        check("绝不自删（无 del \"%~f0\"）",
              "del /f /q \"%~f0\"" not in body and "del \"" not in body)
        check("记录 start 返回码（可观测）", "errorlevel=" in body)
        check("启动含结束日志", "更新脚本结束" in body)

        check("Popen 走 cmd /c 直跑（无 start /min）",
              captured["args"][:2] == ["cmd", "/c"]
              and "/min" not in captured["args"]
              and "start" not in captured["args"], str(captured["args"]))
        check("CREATE_NO_WINDOW 已设置",
              captured["flags"] == updater.NO_WINDOW and updater.NO_WINDOW != 0,
              hex(captured["flags"]))
        check("shell=False", captured["shell"] is False)
    finally:
        restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)


def test_make_portable_updater():
    print("== make_portable_updater 脚本与启动方式 ==")
    tmp, captured, orig = make_env()
    try:
        zpath = os.path.join(tmp, "comfyhub_portable_9.9.zip")
        with open(zpath, "wb") as f:
            f.write(b"PK fake")
        r = updater.make_portable_updater(zpath, log=lambda *a: None)
        check("返回 ok", r.get("ok") is True, str(r))
        with open(r.get("script", ""), encoding="utf-8") as f:
            body = f.read()
        check("等待用 ping（不用 timeout）",
              "ping -n" in body and "timeout" not in body)
        check("绝不自删（无 del \"%~f0\"）", "del /f /q \"%~f0\"" not in body)
        check("启动含结束日志", "更新脚本结束" in body)
        check("Popen 走 cmd /c 直跑（无 start /min）",
              captured["args"][:2] == ["cmd", "/c"] and "/min" not in captured["args"],
              str(captured["args"]))
        check("CREATE_NO_WINDOW 已设置",
              captured["flags"] == updater.NO_WINDOW and updater.NO_WINDOW != 0)
    finally:
        restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)


def test_clean_stale():
    print("== clean_stale 清理堆积 ==")
    tmp, _, orig = make_env()
    try:
        # 两个旧工作目录 + 三代安装包 + 一个保留包 + 一个无关文件
        for d in ("comfyhub_inst_old1", "comfyhub_upd_old2"):
            os.makedirs(os.path.join(tmp, d))
        keep = os.path.join(tmp, "comfyhub_setup_9.9.exe")
        for p in ("comfyhub_setup_0.8.10.exe", "comfyhub_setup_0.8.11.exe",
                  "comfyhub_portable_0.8.9.zip", keep, "update.log"):
            with open(os.path.join(tmp, p), "wb") as f:
                f.write(b"x")
        updater.clean_stale(keep=keep)
        left = sorted(os.listdir(tmp))
        check("旧工作目录已回收",
              not any(n.startswith(("comfyhub_inst_", "comfyhub_upd_")) for n in left),
              str(left))
        check("旧安装包已删、keep 保留",
              left == ["comfyhub_setup_9.9.exe", "update.log"], str(left))

        # 不带 keep：清掉所有安装包，update.log 不动
        with open(os.path.join(tmp, "comfyhub_setup_1.0.exe"), "wb") as f:
            f.write(b"x")
        updater.clean_stale()
        left = sorted(os.listdir(tmp))
        check("不带 keep 时安装包全清、日志保留",
              left == ["update.log"], str(left))
    finally:
        restore(orig)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_apply_installer()
    test_make_portable_updater()
    test_clean_stale()
    print(f"\n{PASS} ok, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
