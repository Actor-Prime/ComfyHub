# -*- coding: utf-8 -*-
"""导航常量（独立模块避免 main_window ↔ pages 循环导入）"""


class NAV:
    """导航行索引（与 main_window.NAV_ITEMS 顺序一致，杜绝 goto(6) 式硬编码）"""
    HOME, COMFYUI, PLUGINS, ENV, MODELS, WORKFLOW, CONSOLE, SETTINGS = range(8)
