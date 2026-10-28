# -*- coding: utf-8 -*-
"""UI 操作审计日志：把用户/测试在界面上的真实操作记录到文件，便于复盘问题。

写到哪里：<数据根>/logs/ui_YYYYMMDD.log
格式：HH:MM:SS.mmm  ACTION  key=value ...
"""
import datetime
import os

_LOG_DIR = None
_LOG_PATH = None


def _ensure():
    global _LOG_DIR, _LOG_PATH
    if _LOG_PATH:
        return
    from app.paths import data_root
    base = os.path.join(data_root(), "logs")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = os.environ.get("TEMP", ".")
    _LOG_DIR = base
    _LOG_PATH = os.path.join(base, f"ui_{datetime.date.today():%Y%m%d}.log")


def log_ui(action: str, **fields):
    """追加一条 UI 操作记录（永不抛异常，绝不因日志失败影响主流程）"""
    try:
        _ensure()
        parts = " ".join(
            f"{k}={v}" for k, v in fields.items()
            if v is not None and str(v) != ""
        )
        line = f"{datetime.datetime.now():%H:%M:%S.%f}"[:-3] + f"  {action:<12} {parts}"
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_path() -> str:
    _ensure()
    return _LOG_PATH or ""
