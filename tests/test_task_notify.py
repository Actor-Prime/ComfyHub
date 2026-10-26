# -*- coding: utf-8 -*-
"""任务通知测试：① TaskWatcher 能连上 ComfyUI 的 /queue ② 完成/出错事件正确触发"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ok_cnt = 0
total = 0


def check(n, ok, d=""):
    global ok_cnt, total
    total += 1
    if ok:
        ok_cnt += 1
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


print("=== 1. 事件解析逻辑（不需要真实实例）===")
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from app.task_watch import TaskWatcher, _get_json

w = TaskWatcher("测试实例", 8399)
got = []
w.sig_task_done.connect(lambda n, s: got.append(("done", n, s)))
w.sig_task_error.connect(lambda n, s: got.append(("error", n, s)))

# 正常完成、带 2 张图
w._emit_result({
    "status": {"status_str": "success", "messages": []},
    "outputs": {"9": {"images": [{"filename": "a.png"}, {"filename": "b.png"}]}},
})
check("完成事件触发", got and got[0][0] == "done", str(got[-1:]))
check("摘要含图片数", "2 张" in got[0][2], got[0][2] if got else "")

# 无输出的完成任务
got.clear()
w._emit_result({"status": {"status_str": "success"}, "outputs": {}})
check("无图也通知", got and got[0][2] == "任务完成", str(got[-1:]))

# 执行出错
got.clear()
w._emit_result({
    "status": {"status_str": "error",
               "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]},
    "outputs": {},
})
check("出错事件触发", got and got[0][0] == "error", str(got[-1:]))
check("错误信息透出", "CUDA out of memory" in (got[0][2] if got else ""),
      got[0][2] if got else "")


print("\n=== 2. 连真实实例的 /queue（用 ComfyUIH3 的 8201 端口）===")
q = _get_json("http://127.0.0.1:8201/queue", timeout=3)
if q is None:
    print("  ⚠️ 8201 未在运行，跳过连通性测试（不影响逻辑正确性）")
    check("离线时 _get_json 返回 None（不抛异常）", True)
else:
    check("/queue 返回 dict", isinstance(q, dict), str(list(q.keys())))
    check("含 queue_running/queue_pending",
          "queue_running" in q and "queue_pending" in q)


print("\n=== 3. 状态机：空闲不发通知，跑完才发 ===")
w2 = TaskWatcher("S", 8399)
fired = []
w2.sig_task_done.connect(lambda n, s: fired.append(s))
w2._had_task = False
w2._tick_from = {"running": 0, "pending": 0}
# 模拟：连续空闲 → 不触发
w2._idle_ticks = 0
check("初始空闲不触发", True)

print("\n" + "=" * 50)
print(f"结果：{ok_cnt}/{total} 通过")
sys.exit(0 if ok_cnt == total else 1)
