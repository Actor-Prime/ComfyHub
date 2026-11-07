# -*- coding: utf-8 -*-
"""保存前改动确认（2026-11-06 用户要求）

场景：用户不小心改了某个配置又忘了改了哪，直接点「保存设置」就把错误值存进去了。
做法：点保存后先弹出一个小窗，逐行列出「字段：旧值 → 新值」，用户确认才落盘，
取消则恢复改动前的值。

纯函数 diff_changes 不依赖 Qt，可单独测试；ChangeDialog 只负责展示。
"""
import copy


def _fmt(v, limit=60) -> str:
    if v is None:
        return "（空）"
    if isinstance(v, bool):
        return "开" if v else "关"
    s = " ".join(str(v).split())
    if len(s) > limit:
        return s[:limit] + "…"
    return s if s else "（空）"


def diff_changes(old: dict, new: dict, labels: dict,
                 secret_keys=(), long_keys=()) -> list:
    """对比两份配置，返回 [(字段名, 旧值显示, 新值显示), ...]，只含有差异的项。

    secret_keys：密钥类字段，绝不显示明文，只提示「已修改 / 已清除」。
    long_keys：长文本字段（如助手设定），只显示字数变化。
    """
    out = []
    for key in new:
        if key not in old and new[key] in ("", None, 0, False):
            continue                      # 新增的空值不算改动
        o, n = old.get(key), new.get(key)
        if o == n:
            continue
        label = labels.get(key, key)
        if key in secret_keys:
            out.append((label, "已设置" if o else "（空）",
                        "已清除" if not n else "已修改"))
        elif key in long_keys:
            out.append((label, f"{len(str(o or ''))} 字", f"{len(str(n or ''))} 字"))
        else:
            out.append((label, _fmt(o), _fmt(n)))
    return out


def confirm_changes(parent, title: str, changes: list, save_label: str = "保存") -> bool:
    """弹出改动清单；返回 True = 用户确认保存。无改动时应跳过调用本函数。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                   QListWidget, QPushButton)

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(430)
    v = QVBoxLayout(dlg)
    v.setSpacing(8)

    tip = QLabel(f"本次将保存 {len(changes)} 处修改：")
    tip.setStyleSheet("font-weight:bold;")
    v.addWidget(tip)

    lst = QListWidget()
    lst.setWordWrap(True)
    for label, o, n in changes:
        lst.addItem(f"{label}：{_fmt(o, 40)}  →  {_fmt(n, 40)}")
    v.addWidget(lst, 1)

    note = QLabel("确认无误再保存；点「取消」不会写入任何内容。")
    note.setWordWrap(True)
    note.setStyleSheet("color:#8a93a3; font-size:11px;")
    v.addWidget(note)

    h = QHBoxLayout()
    h.addStretch(1)
    b_cancel = QPushButton("取消")
    b_cancel.clicked.connect(dlg.reject)
    h.addWidget(b_cancel)
    b_ok = QPushButton(save_label)
    b_ok.setProperty("accent", True)
    b_ok.setDefault(True)
    b_ok.clicked.connect(dlg.accept)
    h.addWidget(b_ok)
    v.addLayout(h)

    for w in (tip, lst, note, b_cancel, b_ok):
        w.setFocusPolicy(Qt.StrongFocus)
    return dlg.exec() == QDialog.Accepted
