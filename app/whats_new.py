# -*- coding: utf-8 -*-
"""版本更新内容弹窗（2026-11-15 用户需求）。

用户反馈：从低版本更新到新版本后，第一次打开应该告诉用户「更新了什么」，
内容就是 git 上的版本更新简介。

数据源（两级）：
1. 随包 docs/RELEASE_v*.md —— 发版流程里它就是 Gitee Release 简介的源头
   （tools/gitee_release_mgr.py mkrelease 拿它当 body），NSIS/PyInstaller
   都带上它，**离线可用**，且跨多版本更新能把每一版的说明按序聚合；
2. 在线兜底 —— 本地没带说明文件时走 updater.fetch_latest() 拉
   Gitee/GitHub latest release 的 body（latest 必然 == 当前版本）。

触发：config.settings.last_version 记录上次运行版本；启动后发现
old < 当前版本 → 弹窗；同版本 / 降级 / 首次安装（无记录）不弹。
"""

import os
import re
import sys

from app.config import APP_VERSION

# RELEASE_v0.8.29.md → "0.8.29"
_VER_RE = re.compile(r"RELEASE_v([0-9][0-9.]*)\.md$", re.IGNORECASE)


def release_docs_dirs():
    """随包 docs 目录候选（按优先级）：exe 旁 → PyInstaller 解包目录 → 源码树。"""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "docs"))
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            dirs.append(os.path.join(meipass, "docs"))
    else:
        dirs.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs"))
    return [d for d in dirs if os.path.isdir(d)]


def collect_notes(from_version: str, current: str = APP_VERSION):
    """收集 (from_version, current] 区间内的版本说明，按版本**升序**。

    返回 [(version, markdown_text), ...]；多个 docs 目录同名版本取先找到的。
    """
    from app.updater import ver_key
    lo, hi = ver_key(from_version), ver_key(current)
    out = {}
    for d in release_docs_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            m = _VER_RE.search(name)
            if not m:
                continue
            v = m.group(1).rstrip(".")
            k = ver_key(v)
            if not (lo < k <= hi) or v in out:
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                continue
            if text:
                out[v] = text
    return sorted(out.items(), key=lambda kv: ver_key(kv[0]))


def online_notes(current: str = APP_VERSION, timeout: int = 8) -> str:
    """在线兜底：拉 latest release 简介；latest 不是当前版本时不用（说明没对上）。"""
    try:
        from app import updater
        info = updater.fetch_latest(timeout=timeout)
        if str(info.get("version") or "") == current:
            return str(info.get("notes") or "").strip()
    except Exception:
        pass
    return ""


def merge_text(notes):
    """多版说明聚合：每版保留自己的 md 标题，版本之间用分隔线。

    **最新的版本排在最上面**（2026-11-19 用户要求）。数据层 collect_notes 仍按升序
    返回（区间语义清晰、测试按升序断言），只在这里翻转展示顺序 —— 用户升级后先想看
    的是「这次更了什么」，不是三个版本前的说明。
    """
    parts = []
    for v, text in reversed(list(notes)):
        t = text.strip()
        # 文件首行一般已是 "# ComfyHub v0.8.29"，没有就补一个版本小节
        if not t.lstrip("#").lstrip().lower().startswith("comfyhub"):
            t = "# ComfyHub v%s\n\n%s" % (v, t)
        parts.append(t)
    return "\n\n---\n\n".join(parts)


def maybe_show(parent=None) -> bool:
    """启动钩子：低版本 → 当前版本的首启弹窗。弹了返回 True。

    幂等：无论弹不弹，最终都把 last_version 写成当前版本。

    ⚠️ 2026-11-19 修顺序缺陷：原来是「**先**写 last_version，**再**收集说明」。
    一旦说明收集失败（本地 docs 缺失 + 在线兜底超时/版本没对上），版本号已经
    被推进到新版本，这次和以后都再也不会弹——用户永远看不到更新内容。
    现在改成：收集到内容 → 写 + 弹；收集不到 → 仍然写（防每次启动重复卡在
    在线请求上），但记一条日志，便于远程排查。
    """
    from app.config import load_config, save_config
    from app.updater import ver_key

    cfg = load_config()
    st = cfg.setdefault("settings", {})
    old = str(st.get("last_version", "") or "")

    def _commit():
        st["last_version"] = APP_VERSION
        try:
            save_config(cfg)
        except Exception:
            pass

    # 首次安装（无记录）/ 同版本 / 降级：不弹，直接刷新记录
    if not old or ver_key(old) >= ver_key(APP_VERSION):
        _commit()
        return False

    # 升级：先把说明取到手，再决定是否推进版本号
    notes = collect_notes(old)
    if not notes:
        t = online_notes()
        if t:
            notes = [(APP_VERSION, t)]
    _commit()
    if not notes:
        print("[whats_new] %s → %s 有升级但没取到说明（本地 docs 与在线兜底均为空）"
              % (old, APP_VERSION))
        return False
    _show_dialog(parent, notes)
    return True


def _show_dialog(parent, notes):
    """展示弹窗：md 渲染 + 只读，一个「知道了」收尾。

    ⚠️ 2026-11-19 修：这里原来构造完 QDialog 就返回，**从未调用 exec()**，
    所以更新后弹窗一次都没出现过（用户反馈「更新成功但没跳更新内容」）。
    模态窗必须 exec() 才会进入事件循环显示；只 new 出来只会随函数返回被回收。
    """
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QPushButton,
                                   QTextBrowser, QVBoxLayout)
    dlg = QDialog(parent)
    dlg.setWindowTitle("ComfyHub 更新内容")
    dlg.setModal(True)
    dlg.setMinimumSize(560, 460)
    view = QTextBrowser(dlg)
    view.setOpenExternalLinks(True)
    view.setMarkdown(merge_text(notes))
    view.setReadOnly(True)
    b_ok = QPushButton("知道了")
    b_ok.setProperty("accent", True)
    b_ok.clicked.connect(dlg.accept)
    b_ok.setDefault(True)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(b_ok)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(14, 14, 14, 12)
    lay.addWidget(view, 1)
    lay.addLayout(row)
    # 置顶到主窗口之上：更新后首启时其他窗口（浏览器/ComfyUI 控制台）可能抢焦点
    dlg.raise_()
    dlg.activateWindow()
    dlg.exec()


def show_whats_new(parent=None, versions=None) -> bool:
    """手动触发（测试/调试）：不看 last_version，直接收集最近 N 版弹窗。"""
    cur = versions or APP_VERSION
    notes = collect_notes("0", cur)
    if not notes:
        return False
    _show_dialog(parent, notes[-1:])
    return True
