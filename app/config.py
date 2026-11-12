APP_VERSION = "0.8.28"

# -*- coding: utf-8 -*-
"""配置管理：多实例配置持久化 + 自动探测本机 ComfyUI"""
import json
import os
import sys


def app_config_dir() -> str:
    """配置目录：便携模式=程序目录\\data\\ComfyHub；否则 %APPDATA%\\ComfyHub"""
    from app.paths import data_root
    d = data_root()
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_PATH = os.path.join(app_config_dir(), "config.json")

VRAM_MODES = {
    "自动": "",
    "低显存": "--lowvram",
    # 新版 ComfyUI（aki 0.9.2 / 上游）已移除 --medvram，改用 --normalvram
    "中显存": "--normalvram",
    "高显存": "--highvram",
    "禁用显存优化": "--novram",
    "CPU 模式": "--cpu",
}

# 常见 ComfyUI 安装位置（用于自动探测）
_PROBE_ROOTS = [
    r"F:\ComfyUI",
    r"E:\ComfyUI",
    r"D:\ComfyUI",
    r"C:\ComfyUI",
    os.path.expanduser(r"~\Desktop\ComfyUI"),
    r"F:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI",
    r"D:\AI\ComfyUI",
]


def _find_python_for(comfyui_path: str) -> str:
    """在 ComfyUI 目录附近探测配套 Python 解释器"""
    candidates = [
        # ComfyUI 旁边的独立 venv（如 F:\ComfyUI_env）
        r"F:\ComfyUI_env\Scripts\python.exe",
        # 便携包内置环境
        os.path.join(comfyui_path, "python_embeded", "python.exe"),
        os.path.join(comfyui_path, "..", "python_embeded", "python.exe"),
        os.path.join(comfyui_path, ".venv", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "..", ".venv", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "..", "ComfyUI_env", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "venv", "Scripts", "python.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return sys.executable  # 兜底：当前解释器


def default_instance() -> dict:
    """生成默认实例（探测本机安装）"""
    inst = {
        "id": "inst_default",
        "name": "默认实例",
        "comfyui_path": "",
        "python_path": "",
        "port": 8188,
        "vram_mode": "自动",
        "extra_args": "",
        "auto_open_browser": True,
        # 插件方案（启动模式）：name = 展示名，enabled = 该方案启用的插件名列表，args = 附加启动参数
        "plugin_profiles": [{"name": "默认", "enabled": [], "args": ""}],
        "last_profile": "默认",
        "auto_apply_profile": True,        # 启动实例前自动应用 last_profile
        "notify_on_task_done": True,       # 任务完成时弹系统通知
    }
    for root in _PROBE_ROOTS:
        if os.path.isfile(os.path.join(root, "main.py")):
            inst["comfyui_path"] = os.path.normpath(root)
            inst["python_path"] = _find_python_for(root)
            break
    return inst


def load_config() -> dict:
    cfg = {
        "version": 1,
        "instances": [],
        "last_instance_id": "",
    }
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass  # 配置损坏时回退默认，不崩溃
    # 密钥解密（2026-11-06）：盘上是 DPAPI 密文，内存里用明文；
    # 解不开（换了机器/用户）就当没填过，别把密文当 Key 发出去
    try:
        from app import secret_store
        st = cfg.get("settings")
        if isinstance(st, dict) and secret_store.is_encrypted(st.get("ai_api_key") or ""):
            plain = secret_store.unprotect(st["ai_api_key"])
            st["ai_api_key"] = plain or ""
            st["_ai_key_locked"] = plain is None     # 供界面提示"密钥无法解密"
        elif isinstance(st, dict) and str(st.get("ai_api_key") or "").strip():
            cfg["_migrate_key"] = True               # 旧配置明文 → 下次保存时加密
    except Exception:
        pass
    # 用户删光实例后保持为空（显示"（无实例）"占位），不再自动生成
    _sanitize_instances(cfg)
    return cfg


def _sanitize_instances(cfg: dict) -> None:
    """实例列表自愈（2026-11-03）：修重复 id / 完全重复项 / 悬空 last_instance_id。

    背景：测试脚本往真实配置里塞写死 id 的实例后，跑几次就叠出多条同 id 副本
    （用户实测出现过 3 条一模一样的 PPTEST）。同 id 会让「按 id 查找」全部命中
    第一条（选中、删除、切页都会串），属于数据完整性问题，不能只当脏数据看。
    """
    insts = cfg.get("instances")
    if not isinstance(insts, list):
        cfg["instances"] = []
        return
    import uuid as _uuid
    seen_ids, out, changed = set(), [], False
    for inst in insts:
        if not isinstance(inst, dict):
            changed = True
            continue
        iid = str(inst.get("id") or "")
        # 完全重复（id/路径/端口都相同）→ 直接丢弃，保留第一条
        if iid and iid in seen_ids:
            dup = any(o.get("id") == iid
                      and o.get("comfyui_path") == inst.get("comfyui_path")
                      and o.get("port") == inst.get("port") for o in out)
            if dup:
                changed = True
                continue
            # id 相同但内容不同 → 重新分配，别让两条实例互相覆盖
            iid = "inst_" + _uuid.uuid4().hex[:8]
            inst["id"] = iid
            changed = True
        elif not iid:
            iid = "inst_" + _uuid.uuid4().hex[:8]
            inst["id"] = iid
            changed = True
        seen_ids.add(iid)
        out.append(inst)
    if changed:
        cfg["instances"] = out
    # last_instance_id 有两个历史落点（顶层 / settings），统一收敛到顶层
    ids = [i.get("id") for i in cfg.get("instances") or []]
    st = cfg.get("settings") or {}
    legacy = st.pop("last_instance_id", None)
    if legacy and not cfg.get("last_instance_id"):
        cfg["last_instance_id"] = legacy
        changed = True
    last = cfg.get("last_instance_id") or ""
    if last and last not in ids:
        cfg["last_instance_id"] = ""
        changed = True
    if changed:
        try:
            save_config(cfg)      # 自愈结果落盘，下次启动不再重复计算
        except Exception:
            pass


def save_config(cfg: dict) -> None:
    # 密钥加密后再落盘（2026-11-06）：内存里保持明文，写出去的是 DPAPI 密文。
    # 用深拷贝，避免把内存中的明文改成密文导致后续请求拿到密文。
    out = cfg
    try:
        import copy
        from app import secret_store
        out = copy.deepcopy(cfg)
        for k in [k for k in list(out) if str(k).startswith("_")]:
            out.pop(k, None)                        # "_migrate_key" 等运行态标记不写盘
        st = out.get("settings")
        if isinstance(st, dict):
            st.pop("_ai_key_locked", None)          # 运行态标记，不写盘
            key = st.get("ai_api_key")
            if key:
                st["ai_api_key"] = secret_store.protect(str(key))
    except Exception:
        out = cfg
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)  # 原子写，防断电损坏


def is_comfyui_dir(path: str) -> bool:
    """是否为 ComfyUI 实例目录：有 main.py 且具备 ComfyUI 特征文件。
    仅看 main.py 会误判（任何 Python 项目都可能有 main.py，例如本启动器自身）。"""
    import os
    if not os.path.isfile(os.path.join(path, "main.py")):
        return False
    # ComfyUI 三个标志性文件/目录，命中任意一个即认定为 ComfyUI
    return (os.path.isdir(os.path.join(path, "comfy"))
            or os.path.isfile(os.path.join(path, "nodes.py"))
            or os.path.isfile(os.path.join(path, "folder_paths.py")))
