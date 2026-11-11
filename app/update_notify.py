# -*- coding: utf-8 -*-
"""延迟更新提醒的纯逻辑（2026-11-12 用户需求）。

需求：启动满一小时后若发现新版本，主动提醒一次；用户可以选择
  · 立即更新
  · 延后提醒（时长可选：1 小时 / 4 小时 / 1 天 / 3 天 / 1 周，记住上次选择）
  · 不再提醒此版本（复用 updater 的 update_skipped_version 语义）

这里只放「该不该提醒 / 什么时候再查」的判断与记账，不碰 Qt：
UI 在 app/ui/update_reminder.py，接线在 main_window。

设置键：
  update_delayed_check   启动 1 小时后检查并提醒（默认 True）
  update_snooze_until    延后到何时（epoch 秒；0 或空 = 未延后）
  update_snooze_hours    上次选的延后时长（弹窗下拉的默认值）
  update_last_check      上次联网检查时间（updater.remember_check 维护）
"""
import time

FIRST_DELAY_S = 3600.0          # 启动后多久开始主动提醒（用户指定 1 小时）
RECHECK_GAP_S = 1800.0          # 两次联网检查的最小间隔，防刷接口

# (小时, 显示文案)——文案是动态下拉项，需在 i18n 词表登记
DELAY_CHOICES = [
    (1.0, "1 小时"),
    (4.0, "4 小时"),
    (24.0, "1 天"),
    (72.0, "3 天"),
    (168.0, "1 周"),
]
DEFAULT_DELAY_HOURS = 4.0


def enabled(st: dict) -> bool:
    """开关是否打开（默认开：这是用户主动要的提醒能力）"""
    return bool((st or {}).get("update_delayed_check", True))


def snooze_until(st: dict) -> float:
    try:
        return float((st or {}).get("update_snooze_until") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def should_remind(st: dict, started_at: float, now: float = None) -> bool:
    """现在是否可以主动提醒：开关开 + 已启动满 1 小时 + 不在延后期"""
    now = float(now if now is not None else time.time())
    if not enabled(st):
        return False
    if now - float(started_at or now) < FIRST_DELAY_S:
        return False
    return snooze_until(st) <= now


def can_recheck(st: dict, now: float = None) -> bool:
    """距上次联网检查是否已超过最小间隔（避免每分钟打接口）"""
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
    """下一次可能提醒的时间点（UI 可用来算精确 rearm，调试用）"""
    return max(float(started_at or 0) + FIRST_DELAY_S, snooze_until(st))
