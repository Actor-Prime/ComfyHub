# -*- coding: utf-8 -*-
"""数据根目录统一入口（便携模式开关）

规则（2026-10-28 用户需求：程序+数据一个文件夹，删文件夹即卸载干净）：
  · 便携模式：**打包后**的程序目录（ComfyHub.exe 所在目录）下存在 data 子目录
    → 所有数据落到 <程序目录>\\data\\ComfyHub（config/tools/备份/快照/缓存/日志）
  · 普通模式（默认）：%APPDATA%\\ComfyHub —— 多用户互不干扰，不受安装路径影响
开发态（venv 直跑）恒走普通模式，测试重定向 APPDATA 的约定不受影响。

启用方式：在 ComfyHub.exe 旁边建一个空的 data 文件夹（或用带 data 的便携发行包）。
"""
import os
import sys


def exe_dir() -> str:
    """程序目录：onedir 打包后即 ComfyHub.exe 所在目录"""
    return os.path.dirname(os.path.abspath(sys.executable))


def data_root() -> str:
    """数据根目录：便携模式 = <程序目录>/data/ComfyHub；否则 %APPDATA%/ComfyHub"""
    if getattr(sys, "frozen", False):   # 仅打包版认便携开关，开发态不误触
        portable = os.path.join(exe_dir(), "data")
        if os.path.isdir(portable):
            return os.path.join(portable, "ComfyHub")
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ComfyHub")
