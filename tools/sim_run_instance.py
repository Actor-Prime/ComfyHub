# -*- coding: utf-8 -*-
"""启动 ComfyUI 实例并探活（验证"安装后能跑起来"）

用法：.venv_build/Scripts/python.exe tools/sim_run_instance.py <ComfyUI目录> [端口]
"""
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET = sys.argv[1] if len(sys.argv) > 1 else r"F:\_sim\ComfyUI"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8199
PY = os.path.join(os.environ["APPDATA"], "ComfyHub", "tools", "python", "python.exe")

print(f"启动: {TARGET} | 端口 {PORT}", flush=True)
print(f"解释器: {PY}", flush=True)

env = dict(os.environ)
for k in ("PYTHONPATH", "PYTHONHOME"):
    env.pop(k, None)
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
log_path = os.path.join(os.environ["TEMP"], f"comfy_{PORT}.log")
lf = open(log_path, "w", encoding="utf-8", errors="replace")
proc = subprocess.Popen(
    [PY, "-s", "main.py", "--port", str(PORT), "--listen", "127.0.0.1"],
    cwd=TARGET, stdout=lf, stderr=subprocess.STDOUT,
    creationflags=flags, env=env)

url = f"http://127.0.0.1:{PORT}/"
ok = False
t0 = time.time()
while time.time() - t0 < 180:
    if proc.poll() is not None:
        print(f"❌ 进程提前退出（code {proc.returncode}）", flush=True)
        break
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body = r.read(200)
            print(f"✅ HTTP {r.status} | {len(body)} 字节响应 | 耗时 {time.time()-t0:.0f}s", flush=True)
            ok = True
            break
    except Exception:
        time.sleep(2)

print(f"\n=== 运行验证: {'✅ 通过' if ok else '❌ 失败'} ===", flush=True)
print(f"进程 PID: {proc.pid} | 页面: {url}", flush=True)
print(f"实例日志: {log_path}", flush=True)
if not ok:
    print("--- 日志尾部 ---", flush=True)
    lf.flush()
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            print("".join(f.readlines()[-15:]), flush=True)
    except Exception:
        pass
    proc.terminate()
    sys.exit(1)

# 探活成功：保持运行几秒后停止（模拟用户看到页面后关闭）
time.sleep(3)
proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
lf.close()
print("已停止实例（模拟关闭）", flush=True)
sys.exit(0)
