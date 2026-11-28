# -*- coding: utf-8 -*-
"""保存前改动确认（2026-11-06 用户要求）

场景：用户不小心改了某个配置又忘了改了哪，直接点「保存设置」就把错误值存进去了。
做法：点保存后先弹出一个小窗，逐行列出「字段：旧值 → 新值」，用户确认才落盘，
取消则恢复改动前的值。

纯函数 diff_changes / _restart_required_keys 不依赖 Qt，可单独测试；
ChangeDialog 只负责展示。
"""
import copy


def _fmt(v, limit=60, empty="（空）") -> str:
    if v is None:
        return empty
    if isinstance(v, bool):
        return "开" if v else "关"
    s = " ".join(str(v).split())
    if len(s) > limit:
        return s[:limit] + "…"
    return s if s else empty


def diff_changes(old: dict, new: dict, labels: dict,
                 secret_keys=(), long_keys=(), defaults=None) -> list:
    """对比两份配置，返回 [(字段名, 旧值显示, 新值显示), ...]，只含有差异的项。

    secret_keys：密钥类字段，绝不显示明文，只提示「已修改 / 已清除」。
    long_keys：长文本字段（如助手设定），只显示字数变化。
    defaults：{字段名: 空值时的显示文案}——有些字段留空 = 用默认值（如模型目录
    留空 = 跟随 <根目录>/models），显示「默认」比显示「（空）」清楚（2026-11-08）。
    """
    defaults = defaults or {}
    out = []
    for key in new:
        if key not in old and new[key] in ("", None, 0, False):
            continue                      # 新增的空值不算改动
        o, n = old.get(key), new.get(key)
        if o == n:
            continue
        if o in ("", None) and n in ("", None):
            continue                      # None 与 "" 互相顶替不算改动（2026-11-07）
        label = labels.get(key, key)
        empty_txt = defaults.get(key, "（空）")
        if key in secret_keys:
            out.append((label, "已设置" if o else "（空）",
                        "已清除" if not n else "已修改"))
        elif key in long_keys:
            out.append((label, f"{len(str(o or ''))} 字", f"{len(str(n or ''))} 字"))
        else:
            out.append((label, _fmt(o, empty=empty_txt), _fmt(n, empty=empty_txt)))
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


# ==================== 启动时才生效的字段（保存后需重启实例） ====================
# 判定依据全部来自 app/process.py：下列字段只在 ComfyProcess.start() 构造
# 启动命令 / 进程环境时被读取一次，ComfyUI 进程运行中不会再读 —— 保存后
# 正在跑的实例不会自动应用，必须停止再启动（build_command 由 start() 在
# process.py:638 调用）：
#   · lan_access / port        → build_command L386-390（--listen/--port，
#                                ComfyUI 只在启动时绑定监听地址）
#   · python_path              → build_command L359（启动用的解释器路径）
#   · vram_mode                → build_command L362-365（VRAM_MODES 标志位）
#   · engine（backend/index）  → build_command L366-384（--cpu/--directml/
#                                --cuda-device）
#   · cross_attn               → build_command L391-405（注意力后端参数）
#   · preview_method           → build_command L406-411（--preview-method）
#   · precision                → build_command L412-419（--force-fp16/fp32）
#   · fast_mode                → build_command L420-421（--fast）
#   · no_smart_memory          → build_command L422-423（--disable-smart-memory）
#   · cpu_vae                  → build_command L425-426（--cpu-vae）
#   · deterministic            → build_command L427-428（--deterministic）
#   · channels_last            → build_command L429-430（--force-channels-last）
#   · multi_user               → build_command L431-432（--multi-user）
#   · cuda_malloc              → build_command L433-438（--cuda-malloc 等）
#   · auto_open_browser        → build_command L441-442（--disable-auto-launch）
#                                + start() L636（就绪后是否弹浏览器）
#   · models_dir               → build_command L454-461（--models-directory）
#   · mount_enabled / mount_dir→ build_command L462-467（--extra-model-paths-config；
#                                yaml 只在启动时传给 ComfyUI）
#   · extra_args               → build_command L474-477（原样拼进命令行）
#   · hf_offline               → start() L652-655（QProcess 环境变量，启动时
#                                注入 HF_HUB_OFFLINE）
# 反例（不在此列）：
#   · name —— 只是 ComfyHub 侧的显示名，不进启动命令；
#   · kind / base_url / auth_* / tls_verify / ssh_* / tunnel_* / cloud_* /
#     insecure_warn_muted / last_ops_channel —— ComfyHub 侧的连接与远程运维
#     配置，跟 ComfyUI 进程怎么启动无关。
_RESTART_AT_START_KEYS = frozenset({
    "lan_access", "port", "python_path", "vram_mode", "engine",
    "cross_attn", "preview_method", "precision", "fast_mode",
    "no_smart_memory", "cpu_vae", "deterministic", "channels_last",
    "cuda_malloc", "multi_user", "auto_open_browser", "models_dir",
    "mount_enabled", "mount_dir", "extra_args", "hf_offline",
})


def _restart_required_keys(old: dict, new: dict) -> list:
    """返回本次改动里「启动时才生效」的字段 key（顺序跟随 new 的字段序）。

    纯函数、无 Qt 依赖，可直接单测：
      改 lan_access → ["lan_access"]；只改 name → []；只改 base_url → []。

    改动判定与 diff_changes 完全同口径（只看 new 里的键；新增空值不算改动；
    None 与 "" 互相顶替不算改动），保证与保存确认框里用户看到的改动清单一致，
    不会多报用户没动过的字段。
    """
    out = []
    for key in new:
        if key not in _RESTART_AT_START_KEYS:
            continue
        if key not in old and new[key] in ("", None, 0, False):
            continue                      # 新增的空值不算改动（同 diff_changes）
        o, n = old.get(key), new.get(key)
        if o == n:
            continue
        if o in ("", None) and n in ("", None):
            continue                      # None 与 "" 互相顶替不算改动
        out.append(key)
    return out
