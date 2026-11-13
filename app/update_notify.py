# -*- coding: utf-8 -*-
"""延迟更新提醒的纯逻辑（2026-11-12 建，2026-11-13 改节奏）

需求演变：
  · 2026-11-12：启动满一小时后若发现新版本，主动提醒一次；
  · 2026-11-13（用户要求）：改成**启动后每一小时检查一次**——挂着不关也能
    及时知道新版本，而不是只在启动那一小时里查一次就再也不管。

用户可选项：立即更新 / 延后提醒（1 小时 / 4 小时 / 1 天 / 3 天 / 1 周，记住上次）/
不再提醒此版本（复用 updater 的 update_skipped_version 语义）。

节奏设计（2026-11-13 定稿，用户逐条确认）：
  · **启动检查**：启动 6 秒后查一次，发现新版本只出顶部横幅，不弹窗（不打扰）；
  · **周期检查**：启动满 1 小时起，每隔 PERIOD_S（1 小时）查一次，用
    update_next_check 记账，跨重启也保持；**检测到新版本就弹一次弹窗**；
  · **开关主从**：update_auto_check 是总闸，关掉后启动与每小时都停；
    update_delayed_check 只管每小时那条，总闸关掉时自动失效；
  · **延后**：只记时间点，不把这个版本记进「已提醒」；到期由 main_window 的
    单次 QTimer 立刻补弹（不等下一次每小时检查）；
  · **不再提醒**：只在本次运行内静音该版本（内存集合），**不写 config** ——
    下次启动照常提示。

⚠️ `update_skipped_version` 只服务于顶部横幅的「跳过此版本」（持久跳过，
设置页有清除按钮），与弹窗的「不再提醒」是两回事，别混用。

这里只放「该不该检查 / 该不该提醒」的判断与记账，不碰 Qt：
UI 在 app/ui/update_reminder.py，接线在 main_window。

设置键：
  update_delayed_check   每小时检查并提醒（默认 True）
  update_next_check      下一次周期检查时间（epoch 秒）
  update_snooze_until    延后到何时（epoch 秒；0 或空 = 未延后）
  update_snooze_hours    上次选的延后时长（弹窗下拉的默认值）
  update_last_check      上次联网检查时间（updater.remember_check 维护）
"""
import time

FIRST_DELAY_S = 3600.0          # 启动后多久开始第一次检查（用户指定 1 小时）
PERIOD_S = 3600.0               # 之后每隔多久检查一次（2026-11-13：每小时）
RECHECK_GAP_S = 1800.0          # 两次联网检查的硬下限，防刷接口（手动/异常路径兜底）

# (小时, 显示文案)——文案是动态下拉项，需在 i18n 词表登记
DELAY_CHOICES = [
    (1.0, "1 小时"),
    (4.0, "4 小时"),
    (24.0, "1 天"),
    (72.0, "3 天"),
    (168.0, "1 周"),
]
DEFAULT_DELAY_HOURS = 4.0


def master_enabled(st: dict) -> bool:
    """总开关（update_auto_check）：自动检查更新的总闸

    2026-11-13 用户要求改主从——取消勾选「启动时自动检查」后，
    「每小时检查一次更新」也一并停掉。否则用户明明取消了勾选，
    后台还有一条在偷偷跑，行为与直觉不符。
    """
    return bool((st or {}).get("update_auto_check", True))


def enabled(st: dict) -> bool:
    """每小时检查（含弹窗）是否启用：总开关 + 自己的勾选，缺一不可"""
    st = st or {}
    return master_enabled(st) and bool(st.get("update_delayed_check", True))


def snooze_until(st: dict) -> float:
    try:
        return float((st or {}).get("update_snooze_until") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def should_remind(st: dict, started_at: float, now: float = None) -> bool:
    """现在是否可以**弹窗提醒**：开关开 + 已启动满 1 小时 + 不在延后期"""
    now = float(now if now is not None else time.time())
    if not enabled(st):
        return False
    if now - float(started_at or now) < FIRST_DELAY_S:
        return False
    return snooze_until(st) <= now


def next_check_at(st: dict, started_at: float) -> float:
    """下一次周期检查的时间点：启动满 1 小时，与上次记的周期点取较晚者。

    跨重启也保持节奏：上次记的 next 若还没到，重启后不会提前查；
    若已经过期，则等本次启动满 1 小时再查（不因重启而扎堆请求）。
    """
    base = float(started_at or 0.0) + FIRST_DELAY_S
    try:
        nxt = float((st or {}).get("update_next_check") or 0.0)
    except (TypeError, ValueError):
        nxt = 0.0
    return max(base, nxt)


def should_check(st: dict, started_at: float, now: float = None) -> bool:
    """现在该不该跑一次「每小时检查」。

    注意：**不看延后期**——延后只压弹窗，静默检查照跑（不然延后 4 小时里
    出了新版本，用户要等延期结束才知道）。
    """
    now = float(now if now is not None else time.time())
    if not enabled(st):
        return False
    return now >= next_check_at(st, started_at)


def mark_checked(st: dict, now: float = None) -> float:
    """记一轮检查：把下次周期点推到 now + PERIOD_S，返回该时间（调用方负责落盘）"""
    now = float(now if now is not None else time.time())
    st["update_next_check"] = now + PERIOD_S
    return st["update_next_check"]


def can_recheck(st: dict, now: float = None) -> bool:
    """距上次联网检查是否已超过硬下限（异常/手动路径兜底，防刷接口）"""
    now = float(now if now is not None else time.time())
    try:
        last = float((st or {}).get("update_last_check") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    return (now - last) >= RECHECK_GAP_S


def snooze(st: dict, hours: float, now: float = None) -> float:
    """记延后：写入 until 与所选时长，返回 until"""
    now = float(now if now is not None else time.time())
    try:
        h = float(hours)
    except (TypeError, ValueError):
        h = DEFAULT_DELAY_HOURS
    h = max(0.25, min(h, 24.0 * 30))
    st["update_snooze_until"] = now + h * 3600.0
    st["update_snooze_hours"] = h
    return st["update_snooze_until"]


def clear_snooze(st: dict) -> None:
    st["update_snooze_until"] = 0.0


def last_delay_hours(st: dict) -> float:
    """上次选的时长（弹窗默认选中它）"""
    try:
        h = float((st or {}).get("update_snooze_hours") or DEFAULT_DELAY_HOURS)
    except (TypeError, ValueError):
        h = DEFAULT_DELAY_HOURS
    return h if h > 0 else DEFAULT_DELAY_HOURS


def due_at(st: dict, started_at: float) -> float:
    """下一次「可能弹提醒」的时间点（周期点与延后期取较晚；调试用）"""
    return max(next_check_at(st, started_at), snooze_until(st))
