# -*- coding: utf-8 -*-
"""UI 线程防阻塞回归（2026-11-02 启动卡顿修复）

背景：用户点「启动」后管理器 UI 持续卡顿，直到 ComfyUI 启动完成才恢复。
根因（probe_stall.py 实测定位）：UI 线程同步 connect 探测——
  · ComfyProcess._check_ready 每 1 秒 port_in_use(connect_ex 0.5s 超时)
  · TaskWatcher._tick 每 2 秒 urlopen(4s 超时)
本机 connect 对「关闭的本地端口」不秒拒、吃满超时（实测 500ms/次、10035），
ComfyUI 启动期间端口未监听 → 每次探测都堵死事件循环；绑上端口后探测秒回，
所以「启动完就恢复流畅」。

断言修复后：
  1. port_in_use 走 netstat 快照（不发 socket connect）
  2. _check_ready 对关闭端口立即返回（<150ms）且不误报就绪
  3. 端口进入监听后异步报「服务就绪」（日志格式与 e2e_port_bump 兼容）
  4. TaskWatcher 对关闭端口轮询期间事件循环无长卡顿；stop() 非阻塞、线程可退出
  5. TaskWatcher 任务完成检测功能不回归（假 HTTP 服务端验证）
"""
import os
import sys
import json
import time
import socket
import threading
import http.server

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# APPDATA 重定向，防污染真实配置
os.environ["APPDATA"] = r"F:\_ui_resp_test"

_ok = 0
_fail = 0


def check(cond, msg):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ✓ {msg}")
    else:
        _fail += 1
        print(f"  ✗ {msg}")


from PySide6.QtCore import QCoreApplication, QTimer

app = QCoreApplication(sys.argv)


def pump(seconds):
    """泵事件循环 seconds 秒（0.05s 步进），返回心跳最大间隔 ms"""
    gaps = []
    last = [time.perf_counter()]
    hb = QTimer()
    hb.setInterval(50)

    def beat():
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now

    hb.timeout.connect(beat)
    hb.start()
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        app.processEvents()
        time.sleep(0.02)
    hb.stop()
    return max(gaps) if gaps else 0.0


# ---------- 1) port_in_use：netstat 判定，不再 socket connect ----------
print("[1] port_in_use 走 netstat 快照")
import app.process as pr

_orig_nl = pr._netstat_listening
pr._netstat_listening = lambda: [(111, 8188), (222, 8189)]
check(pr.port_in_use(8188) is True, "监听中端口 → True")
check(pr.port_in_use(8189) is True, "监听中端口(第二) → True")
check(pr.port_in_use(9999) is False, "未监听端口 → False")
pr._netstat_listening = _orig_nl

# ---------- 2) _check_ready：关闭端口秒回、不误报；监听后异步就绪 ----------
print("[2] _check_ready 异步探测")
probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.bind(("127.0.0.1", 0))
free_port = probe.getsockname()[1]
probe.close()          # 关闭端口（本机 connect 会吃满超时——修复前的卡顿源）

p = pr.ComfyProcess()
logs = []
p.log_line.connect(logs.append)
from PySide6.QtCore import QProcess

qp = QProcess()
qp.setProgram(sys.executable)
qp.setArguments(["-c", "import time; time.sleep(120)"])
qp.start()
ok_start = qp.waitForStarted(5000)
check(ok_start, "假进程启动（占 is_running 状态）")
p._proc = qp               # 直接挂上，模拟运行中

p._port = free_port
p._ready_announced = False
p._browser_on_ready = False
t0 = time.perf_counter()
p._check_ready()
dt = (time.perf_counter() - t0) * 1000
check(dt < 150, f"关闭端口探测立即返回（{dt:.0f}ms < 150ms，修复前约 500ms）")
pump(2.4)                  # 覆盖 2s 放弃窗口
check(not p._ready_announced, "未监听端口不误报就绪")
check(not any("服务就绪" in s for s in logs), "无就绪日志")

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", free_port))
srv.listen(1)
p._check_ready()           # 槽位已空，再发一轮
t0 = time.perf_counter()
while time.perf_counter() - t0 < 3 and not p._ready_announced:
    app.processEvents()
    time.sleep(0.02)
check(p._ready_announced, "端口监听后异步报就绪")
check(any(f":{free_port}" in s and "服务就绪" in s for s in logs),
      f"就绪日志含实际端口（e2e_port_bump 断言格式兼容）")
srv.close()
p._cleanup()
qp.kill()
qp.waitForFinished(3000)

# ---------- 3) TaskWatcher：对关闭端口轮询不卡事件循环 ----------
print("[3] TaskWatcher 轮询不阻塞 UI 线程")
from app.task_watch import TaskWatcher

w = TaskWatcher("测试实例", free_port)      # 端口刚被释放 → 关闭端口
w.start()
max_gap = pump(5.0)                        # 覆盖 2~3 拍轮询（每拍 connect 吃满超时）
check(max_gap < 250, f"轮询期间事件循环最大间隔 {max_gap:.0f}ms < 250ms（修复前 4000ms）")
t0 = time.perf_counter()
w.stop()
dt = (time.perf_counter() - t0) * 1000
check(dt < 50, f"stop() 非阻塞（{dt:.1f}ms）")
w.wait(8000)
check(not w.isRunning(), "线程已退出")

# ---------- 4) TaskWatcher 任务完成检测功能不回归 ----------
print("[4] TaskWatcher 任务完成检测（假 HTTP 服务端）")
STATE = {"phase": 0}


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/queue"):
            body = ({"queue_running": [["x", {}]], "queue_pending": []}
                    if STATE["phase"] == 0
                    else {"queue_running": [], "queue_pending": []})
        elif self.path.startswith("/history"):
            body = ({"old": {}}
                    if STATE["phase"] == 0
                    else {"old": {},
                          "new": {"status": {"status_str": "success"},
                                  "outputs": {"1": {"images": [{"n": 1}]}}}})
        else:
            body = {}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


httpsrv = http.server.HTTPServer(("127.0.0.1", 0), _H)
http_port = httpsrv.server_address[1]
threading.Thread(target=httpsrv.serve_forever, daemon=True).start()

w2 = TaskWatcher("测试实例", http_port)
done = []
w2.sig_task_done.connect(lambda name, msg: done.append((name, msg)))
w2.start()
pump(2.6)                  # 第 1 拍：队列有任务 → _had_task
STATE["phase"] = 1         # 翻转：队列空 + 历史新增
t0 = time.perf_counter()
while time.perf_counter() - t0 < 6 and not done:
    app.processEvents()
    time.sleep(0.02)
check(bool(done), f"任务完成信号发出 {done}")
check(done and "生成 1 张图" in done[0][1], "摘要含出图张数")
w2.stop()
w2.wait(8000)
httpsrv.shutdown()

print(f"\n结果: {_ok}/{_ok + _fail} 通过")
sys.exit(1 if _fail else 0)
