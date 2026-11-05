# -*- coding: utf-8 -*-
"""运行记录黑匣子（2026-11-06）

解决什么问题：crash.log 只记“已经炸了”的异常；用户说“昨天点着点着就没了”时，
手里没有任何会话级证据。这里补三件事：

  ① 会话流水：每次启动 / 正常退出 / 运行时长
  ② 闪退判定：启动时发现上次的 .running 哨兵还在 → 上次没走正常退出流程
     （强杀、断电、C 层崩溃、任务管理器结束进程都能抓到）
  ③ 卡死判定：主线程心跳超时（watchdog 线程看门）→ 记一条 hang 事件

落盘：<数据根>/logs/runtime.jsonl（每行一个 JSON 事件，只存时间/事件名/数值，
不含任何用户内容）；超过 MAX_EVENTS 行自动截断保留后半。

设计原则：本模块**绝不抛异常、绝不阻塞**——它是排障工具，不能成为新的故障源。
"""
import json
import os
import threading
import time

from app.paths import data_root

MAX_EVENTS = 800            # 超过就截断保留最后 MAX_EVENTS//2 行
HANG_THRESHOLD = 12.0       # 主线程超过这么多秒没心跳 → 记卡死
HANG_COOLDOWN = 60.0        # 同一次卡死别刷屏，冷却期内只记一条

_lock = threading.Lock()
_LAST_BEAT = None           # 最近一次心跳（monotonic）
_LAST_HANG = 0.0            # 上次记 hang 的时间
_SESSION_START = None
_WATCHDOG = None


def _logs_dir() -> str:
    d = os.path.join(data_root(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def _record_path() -> str:
    return os.path.join(_logs_dir(), "runtime.jsonl")


def _sentinel_path() -> str:
    return os.path.join(_logs_dir(), ".running")


def _append(ev: str, **kw) -> None:
    """追加一条事件。写失败一律静默（磁盘满/权限问题不该拖垮主程序）。"""
    try:
        line = json.dumps({"ts": time.time(), "ev": ev, **kw},
                          ensure_ascii=False)
        with _lock:
            p = _record_path()
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _trim_if_needed(p)
    except Exception:
        pass


def _trim_if_needed(path: str) -> None:
    """行数超限就保留后半：黑匣子只关心近期，别让文件无限长。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if len(lines) <= MAX_EVENTS:
            return
        keep = lines[len(lines) // 2:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except Exception:
        pass


def events(limit: int = 40) -> list:
    """读最近 limit 条事件（新的在最后）"""
    try:
        with open(_record_path(), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []


# ------------------------------------------------------------------ 会话
def session_start(version: str = "") -> None:
    """启动时调用（单实例守卫之后）。发现上回没正常退出 → 记一条 crash。"""
    global _SESSION_START
    _SESSION_START = time.time()
    try:
        if os.path.isfile(_sentinel_path()):
            prev = {}
            try:
                with open(_sentinel_path(), encoding="utf-8") as f:
                    prev = json.load(f)
            except Exception:
                pass
            # 区分“真崩溃”和“被强杀/升级安装杀掉”：crash.log 在哨兵之后有新内容
            # 才算有错误证据，否则只报“未正常退出”，避免把强杀误报成闪退。
            note = "上次未正常退出（可能被强制结束或升级安装）"
            try:
                cl = os.path.join(_logs_dir(), "crash.log")
                if (os.path.isfile(cl) and prev.get("ts")
                        and os.path.getmtime(cl) > float(prev["ts"])):
                    note = "上次疑似闪退（crash.log 有新的错误记录）"
            except Exception:
                pass
            _append("crash", note=note, since=prev.get("ts"), pid=prev.get("pid"))
            try:
                os.remove(_sentinel_path())
            except Exception:
                pass
        with open(_sentinel_path(), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "pid": os.getpid(),
                       "version": version}, f)
    except Exception:
        pass
    _append("start", version=version, pid=os.getpid())
    beat()


def session_end() -> None:
    """正常退出时调用（app.exec 返回后 / 主窗 closeEvent 收尾处）"""
    try:
        dur = int(time.time() - _SESSION_START) if _SESSION_START else 0
        _append("exit", ok=True, dur=dur)
    except Exception:
        pass
    try:
        os.remove(_sentinel_path())
    except Exception:
        pass


def mark_crash(note: str = "") -> None:
    """主循环捕获到致命异常时补一条（session_end 不会跑到）"""
    _append("crash", note=note or "主循环致命异常")


# ------------------------------------------------------------------ 心跳
def beat() -> None:
    """主线程心跳：UI 定时器每隔几秒调一次（Qt 定时器跑不动 = 界面卡了）"""
    global _LAST_BEAT
    _LAST_BEAT = time.monotonic()


def check_hang(now: float = None, threshold: float = HANG_THRESHOLD) -> bool:
    """返回 True 表示判定为卡死并已记录。抽出来便于测试。"""
    global _LAST_BEAT, _LAST_HANG
    if _LAST_BEAT is None:
        return False
    now = time.monotonic() if now is None else now
    blocked = now - _LAST_BEAT
    if blocked < threshold:
        return False
    if now - _LAST_HANG < HANG_COOLDOWN:
        _LAST_BEAT = now          # 同一次卡死只记一条，但心跳要跟上
        return False
    _LAST_HANG = now
    _LAST_BEAT = now
    _append("hang", blocked_s=round(blocked, 1))
    return True


def start_watchdog(interval: float = 5.0) -> None:
    """看门狗线程：主线程的心跳停了就记 hang。daemon，不挡退出。"""
    global _WATCHDOG
    if _WATCHDOG is not None and _WATCHDOG.is_alive():
        return

    def loop():
        while True:
            time.sleep(interval)
            try:
                check_hang()
            except Exception:
                pass

    _WATCHDOG = threading.Thread(target=loop, name="ComfyHub-Watchdog",
                                 daemon=True)
    _WATCHDOG.start()


# ------------------------------------------------------------------ 汇总
def summary(days: int = 30) -> dict:
    """近 days 天的统计，给诊断报告用"""
    cutoff = time.time() - days * 86400
    starts = exits = crashes = hangs = 0
    durs = []
    for e in events(limit=MAX_EVENTS):
        if e.get("ts", 0) < cutoff:
            continue
        ev = e.get("ev")
        if ev == "start":
            starts += 1
        elif ev == "exit":
            exits += 1
            durs.append(int(e.get("dur") or 0))
        elif ev == "crash":
            crashes += 1
        elif ev == "hang":
            hangs += 1
    return {
        "days": days,
        "starts": starts,
        "exits": exits,
        "crashes": crashes,
        "hangs": hangs,
        "avg_minutes": round(sum(durs) / len(durs) / 60, 1) if durs else 0,
    }


def describe(limit: int = 40) -> str:
    """人话版运行记录，直接进诊断报告"""
    s = summary()
    lines = [
        f"近 {s['days']} 天：启动 {s['starts']} 次 / 正常退出 {s['exits']} 次 / "
        f"异常退出 {s['crashes']} 次 / 卡死 {s['hangs']} 次"
        + (f" / 平均运行 {s['avg_minutes']} 分钟" if s["avg_minutes"] else ""),
        "",
    ]
    label = {"start": "启动", "exit": "正常退出", "crash": "⚠ 异常退出",
             "hang": "⚠ 界面卡死"}
    for e in events(limit):
        ts = time.strftime("%Y-%m-%d %H:%M:%S",
                           time.localtime(e.get("ts", 0)))
        name = label.get(e.get("ev"), str(e.get("ev")))
        extra = []
        if e.get("version"):
            extra.append(f"v{e['version']}")
        if e.get("dur"):
            extra.append(f"运行 {int(e['dur']) // 60} 分钟")
        if e.get("blocked_s"):
            extra.append(f"阻塞 {e['blocked_s']}s")
        if e.get("note"):
            extra.append(str(e["note"]))
        lines.append(f"{ts}  {name}" + (f"（{'，'.join(extra)}）" if extra else ""))
    if len(lines) <= 3:
        lines.append("（暂无运行记录）")
    return "\n".join(lines)
