# -*- coding: utf-8 -*-
"""GUI 程序专用子进程调用：压制 Windows 控制台弹窗

PyInstaller --windowed 打包后，subprocess.run 默认会为每个外部命令
（git/pip/taskkill…）弹出黑色 CMD 窗口。统一在这里加 CREATE_NO_WINDOW。
"""
import os
import subprocess

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(*args, **kwargs):
    """subprocess.run 的无窗口版本（其余参数原样透传）"""
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(*args, **kwargs)
