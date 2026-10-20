# -*- coding: utf-8 -*-
"""统一日志：UI 层防御性 except 的最小可见性（写 %APPDATA%/ComfyHub/logs/app.log）"""
import logging
import os

_LOG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "ComfyHub", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_logger = logging.getLogger("comfyhub")
if not _logger.handlers:
    _h = logging.FileHandler(os.path.join(_LOG_DIR, "app.log"), encoding="utf-8")
    _h.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.DEBUG)


def log_exc(context: str):
    """在防御性 except Exception 处调用：记录上下文 + 异常堆栈"""
    import traceback
    _logger.debug("swallowed exception at %s\n%s", context,
                  traceback.format_exc())
