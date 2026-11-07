# -*- coding: utf-8 -*-
"""运行记录黑匣子回归：会话流水 / 闪退判定 / 卡死检测 / 截断

2026-11-06 用户要求「诊断要真有运行记录，能看出闪退或卡死」"""
import json
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

PASS = 0


def check(name, ok, detail=""):
    global PASS
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        sys.exit(1)
    PASS += 1


# 隔离数据根：整个测试都用临时 APPDATA，绝不碰真实记录
tmp = tempfile.mkdtemp(prefix="ch_runtime_")
os.environ["APPDATA"] = tmp

from app import runtime_log as rl  # noqa: E402

# ---- 1. 启动 / 正常退出 ----
rl.session_start("0.8.12")
evs = rl.events()
check("1a 启动写一条 start", len(evs) == 1 and evs[0]["ev"] == "start", str(evs))
check("1b 启动写了运行哨兵", os.path.isfile(rl._sentinel_path()))
rl.session_end()
evs = rl.events()
check("1c 正常退出写 exit 且带时长",
      evs[-1]["ev"] == "exit" and evs[-1].get("ok") is True and "dur" in evs[-1], str(evs[-1]))
check("1d 正常退出删除哨兵", not os.path.isfile(rl._sentinel_path()))

# ---- 2. 闪退判定：上一次没走 session_end ----
rl.session_start("0.8.12")
# 不调 session_end，模拟强杀/崩溃后再次启动
rl.session_start("0.8.12")
evs = rl.events()
crashes = [e for e in evs if e["ev"] == "crash"]
check("2a 缺退出记录 → 判定上次异常退出", len(crashes) == 1, str(crashes))
check("2b 异常退出带原因说明", "未正常退出" in (crashes[0].get("note") or ""), str(crashes[0]))
rl.session_end()

# ---- 3. 卡死检测 ----
rl.beat()
check("3a 心跳新鲜时不误报", rl.check_hang(now=time.monotonic() + 5) is False)
hung = rl.check_hang(now=time.monotonic() + rl.HANG_THRESHOLD + 1)
check("3b 心跳超时 → 判定卡死", hung is True)
evs = [e for e in rl.events() if e["ev"] == "hang"]
check("3c 卡死事件带阻塞秒数", len(evs) == 1 and evs[0]["blocked_s"] > 0, str(evs))
check("3d 冷却期内不重复记录",
      rl.check_hang(now=time.monotonic() + rl.HANG_THRESHOLD + 5) is False)
check("3e 冷却过后可再次记录",
      rl.check_hang(now=time.monotonic() + rl.HANG_COOLDOWN + rl.HANG_THRESHOLD + 10) is True)

# ---- 4. 汇总与报告文本 ----
s = rl.summary()
check("4a 汇总含启动/退出/异常/卡死计数",
      all(k in s for k in ("starts", "exits", "crashes", "hangs")), str(s))
check("4b 启动计数正确", s["starts"] == 3, str(s))
check("4c 异常退出计数正确", s["crashes"] == 1, str(s))
check("4d 卡死计数正确", s["hangs"] == 2, str(s))

txt = rl.describe()
check("4e 报告首行人话统计", "近 30 天：启动 3 次" in txt, txt.splitlines()[0])
check("4f 报告含⚠标记", "⚠ 异常退出" in txt and "⚠ 界面卡死" in txt)
check("4g 报告不含用户路径", tmp not in txt)

# ---- 5. 事件截断（黑匣子不能无限长）----
rl.MAX_EVENTS = 10
for i in range(30):
    rl._append("start", version=f"t{i}")
with open(rl._record_path(), encoding="utf-8") as f:
    lines = f.readlines()
check("5a 超限自动截断", len(lines) <= rl.MAX_EVENTS, str(len(lines)))
check("5b 保留的是较新记录", "t29" in lines[-1], lines[-1][:60])

# ---- 6. 诊断报告包含运行记录段 ----
for k in ("start", "exit", "crash", "hang"):
    rl._append(k if k != "crash" else "crash", dur=60)
from app.feedback import collect  # noqa: E402
rep = collect()
check("6a 诊断报告含运行记录章节", "---- 运行记录" in rep)
check("6b 报告里有异常退出记录", "异常退出" in rep)

# ---- 7. 记录文件不含内容型字段（只允许结构性数据）----
raw = open(rl._record_path(), encoding="utf-8").read()
allowed = {"ts", "ev", "version", "pid", "dur", "ok", "note", "blocked_s", "since"}
bad = set()
for ln in raw.splitlines():
    try:
        bad |= set(json.loads(ln)) - allowed
    except Exception:
        pass
check("7a 事件字段白名单内", not bad, str(bad))

print(f"共 {PASS} 项，全部通过")
