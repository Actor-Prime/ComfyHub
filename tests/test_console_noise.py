# -*- coding: utf-8 -*-
"""控制台降噪回归：[safe-delete] 等外部环境噪声不得染成错误红字

用法: .venv_build/Scripts/python.exe tests/test_console_noise.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ui.pages.console_page import classify_color

# ---- 环境噪声 → 暗灰降噪（不得命中错误红 #e8635a）----
noise_lines = [
    '[safe-delete] {"action": "rmtree", "result": "SAFE_DELETE_FAILED_CLOSED"}',
    '[safe-delete] {"result": "SAFE_DELETE_BULK_CONFIRM_REQUIRED", "count": 555}',
    '[safe-delete] {"result": "SAFE_DELETE_BULK_CONFIRM_REQUIRED", "targets": ["ckpts"]}',
    'SAFE_DELETE_FAILED_CLOSED: kept file on failure',
    '[safe-delete] blocked shutil.rmtree with Exception inside',  # 含错误关键词仍须降噪
]
for ln in noise_lines:
    c = classify_color(ln)
    assert c == "#6b7480", f"噪声行应为暗灰 #6b7480，实际 {c}: {ln}"

# ---- 既有颜色规范不受影响 ----
assert classify_color("[ERROR] boom") == "#e8635a"
assert classify_color("Traceback (most recent call last):") == "#e8635a"
assert classify_color("Exception: cannot start") == "#e8635a"
assert classify_color("[WARNING] deprecated api") == "#e8a33d"
assert classify_color("[INFO] hello") == "#7fa8d9"
assert classify_color("Prompt executed in 1.2s") == "#4cd47c"
assert classify_color("[ComfyHub] 进程退出，code=1") == "#e8871e"
assert classify_color("[计划] 定时任务开始") == "#e8871e"
assert classify_color("random ordinary output") == "#b8c2ce"

print("test_console_noise: OK")
