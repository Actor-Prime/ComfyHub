# -*- coding: utf-8 -*-
"""防残留双层方案单测：Job Object kill-on-close + 启动孤儿检测
（.venv_build/Scripts/python.exe 直跑）
"""
import ctypes
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import process as P  # noqa: E402

fails = []


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    tail = f"  [{extra}]" if (extra and not cond) else ""
    print(f"{tag} {name}{tail}")
    if not cond:
        fails.append(name)


# ---------- 1. Job Object kill-on-close 语义（模拟管理器死亡=关句柄） ----------
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hjob = k32.CreateJobObjectW(None, None)
info = P._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
info.BasicLimitInformation.LimitFlags = P._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
ok_set = k32.SetInformationJobObject(
    hjob, P._JobObjectExtendedLimitInformation,
    ctypes.byref(info), ctypes.sizeof(info))
check("job 创建+KILL_ON_JOB_CLOSE 设置", bool(ok_set))

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
hproc = k32.OpenProcess(0x0101, False, child.pid)
ok_assign = bool(hproc) and bool(k32.AssignProcessToJobObject(hjob, hproc))
if hproc:
    k32.CloseHandle(hproc)
time.sleep(0.3)
check("子进程绑定 job", ok_assign)
check("绑定期子进程存活", child.poll() is None)

k32.CloseHandle(hjob)   # ← 模拟管理器进程死亡（句柄关闭）
time.sleep(1.0)
check("关句柄后子进程被内核杀掉(kill-on-close)", child.poll() is not None,
      f"poll={child.poll()}")
try:
    child.wait(timeout=5)
except Exception:
    pass

# ---------- 2. assign_to_kill_job / kill_tree API ----------
child2 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
ok = P.assign_to_kill_job(child2.pid)
check("assign_to_kill_job 成功", ok)
P.kill_tree(child2.pid)
time.sleep(0.5)
check("kill_tree 杀掉子进程", child2.poll() is not None)
try:
    child2.wait(timeout=5)
except Exception:
    pass

# ---------- 3. pid_listening_on ----------
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]
pids = P.pid_listening_on(port)
check("pid_listening_on 找到本进程", os.getpid() in pids, str(pids))
srv.close()
time.sleep(0.3)
check("端口关闭后返回空", P.pid_listening_on(port) == [],
      str(P.pid_listening_on(port)))

# ---------- 4. scan_orphans ----------
srv2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv2.bind(("127.0.0.1", 0))
srv2.listen(1)
p2 = srv2.getsockname()[1]
insts = [{"name": "测试实例", "port": p2},
         {"name": "已关闭端口", "port": port}]
orph = P.scan_orphans(insts)
hit = [o for o in orph if o["inst"]["name"] == "测试实例"]
check("scan_orphans 识别 python 监听为孤儿",
      len(hit) == 1 and hit[0]["pid"] == os.getpid(), str(orph))
check("已关闭端口的实例不报", not any(
    o["inst"]["name"] == "已关闭端口" for o in orph))
check("scan_orphans 排除 own pid",
      P.scan_orphans(insts, exclude_pids=[os.getpid()]) == [])
check("空实例表安全",
      P.scan_orphans(None) == [] and P.scan_orphans([]) == [])
srv2.close()

print()
if fails:
    print(f"RESULT: FAIL ({len(fails)}) -> {fails}")
    sys.exit(1)
print("RESULT: ALL PASS")
