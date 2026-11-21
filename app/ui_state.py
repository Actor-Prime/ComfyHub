# -*- coding: utf-8 -*-
"""界面状态持久化（2026-11-21：从注册表改为数据目录内的 INI）

为什么要改
----------
旧实现用 ``QSettings("LeiMengStudio", "ComfyHub")``，Qt 在 Windows 上把它落到
``HKCU\\Software\\LeiMengStudio\\ComfyHub``。安装器写注册表用的是
``HKCU\\Software\\ComfyHub``（InstallDir/Version），两者不同名 —— 卸载脚本只删了
后者，于是卸载完注册表里还留着一个 ``Software\\LeiMengStudio\\ComfyHub``，
Geek Uninstaller 之类的工具就会报「发现残留痕迹」（用户 2026-11-21 反馈）。

新方案
------
UI 状态写 ``<数据目录>/ui_state.ini``：
  · 普通模式 = ``%APPDATA%\\ComfyHub\\ui_state.ini``
  · 便携模式 = ``<程序目录>/data/ComfyHub/ui_state.ini``
好处：
  1. 卸载删数据目录 = UI 状态一并归零，注册表零残留（安装器只留一个键，卸载已删）；
  2. 便携模式把 data 目录拷走，界面偏好跟着走；
  3. 测试重定向 APPDATA 后状态自然隔离，不会污染真实注册表。

老用户迁移：首次读写时若 INI 不存在，则把旧注册表键里的值搬过来，成功后
顺手删掉旧键（键非空时 winreg.DeleteKey 会失败，绝不误删别的东西）。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings

from .paths import data_root

_LEGACY_ORG = "LeiMengStudio"
_LEGACY_APP = "ComfyHub"
_ready = False


def _ini_path() -> str:
    return os.path.join(data_root(), "ui_state.ini")


def _settings() -> QSettings:
    try:
        os.makedirs(data_root(), exist_ok=True)
    except Exception:
        pass
    return QSettings(_ini_path(), QSettings.IniFormat)


def _purge_legacy_registry() -> None:
    """删掉老版本留在注册表里的键（子键在 winreg 里是键，非空删不掉 = 安全）。"""
    try:
        import winreg
    except Exception:
        return
    for sub in ("Software\\%s\\%s" % (_LEGACY_ORG, _LEGACY_APP),
                "Software\\%s" % _LEGACY_ORG):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass


def _migrate_once() -> None:
    global _ready
    if _ready:
        return
    _ready = True
    try:
        if os.path.exists(_ini_path()):
            return          # 已经在用 INI
        old = QSettings(_LEGACY_ORG, _LEGACY_APP)
        pairs = [(k, old.value(k)) for k in old.allKeys()]
        if pairs:
            new = _settings()
            for k, v in pairs:
                new.setValue(k, v)
            new.sync()
            if not os.path.exists(_ini_path()):
                return      # 落盘失败 → 保留注册表里的值，下次再试
        old.clear()
        old.sync()
    except Exception:
        return
    _purge_legacy_registry()


def get(key: str, default=None, as_bool: bool = False):
    """读一个界面状态；任何异常都退回 default，绝不阻塞启动。"""
    _migrate_once()
    try:
        v = _settings().value(key, default)
    except Exception:
        return default
    if as_bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    return v


def set_value(key: str, val) -> None:
    """写一个界面状态；失败静默（界面偏好丢一次不算事故）。"""
    _migrate_once()
    try:
        s = _settings()
        s.setValue(key, val)
        s.sync()
    except Exception:
        pass
