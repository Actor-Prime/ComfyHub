# -*- coding: utf-8 -*-
"""敏感字段加密存储（2026-11-06）

问题：`ai_api_key` 原本明文写在 `%APPDATA%\\ComfyHub\\config.json`，
文件被拷走（或整目录打包分享）就等于把 Key 送出去。

做法：用 Windows DPAPI（CryptProtectData）加密，密文只能被**同一个 Windows 用户
在同一台机器**上解密——文件被拷到别处也解不开。不依赖 pywin32，纯 ctypes。

存储形态：`enc:v1:<base64>`。解不开（换机器/换用户/系统异常）返回 None，
调用方按"没填过"处理，绝不因为解密失败而崩。非 Windows 环境原样返回明文。
"""
import base64
import ctypes
import sys
from ctypes import wintypes

PREFIX = "enc:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _free(blob):
    try:
        ctypes.windll.kernel32.LocalFree(blob.pbData)
    except Exception:
        pass


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def protect(text: str) -> str:
    """明文 → enc:v1:...；失败则原样返回（宁可可用，不可丢配置）"""
    text = text or ""
    if not text or is_encrypted(text) or sys.platform != "win32":
        return text
    try:
        src, _keep = _to_blob(text.encode("utf-8"))
        out = _Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(src), "ComfyHub", None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return text
        try:
            raw = ctypes.string_at(out.pbData, out.cbData)
        finally:
            _free(out)
        return PREFIX + base64.b64encode(raw).decode("ascii")
    except Exception:
        return text


def unprotect(value: str):
    """enc:v1:... → 明文；不是密文则原样返回；解密失败返回 None"""
    value = value or ""
    if not value:
        return ""
    if not is_encrypted(value):
        return value
    if sys.platform != "win32":
        return None
    try:
        raw = base64.b64decode(value[len(PREFIX):])
        src, _keep = _to_blob(raw)
        out = _Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return None
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8", "ignore")
        finally:
            _free(out)
    except Exception:
        return None
