APP_VERSION = "0.8.41"

# -*- coding: utf-8 -*-
"""配置管理：多实例配置持久化 + 自动探测本机 ComfyUI"""
import copy
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

# ---------------------------------------------------------------- 远程运维字段
# （2026-12-01，0.8.40）远程实例的 SSH / 云钩子 / 隧道配置。
# 设计要点：
#   · 启动/停止命令必须有合理的预填值（PRD A4），用户新加远程实例即可用；
#   · 端口/天数/GB 全部 clamp，脏配置不会把界面或轮询逻辑带偏；
#   · 这里只管「默认值 + 归一」，不做任何 IO，可在离屏环境直接导入。
_SSH_START_CMD_DEFAULT = ("cd ~/ComfyUI && nohup python main.py --listen 0.0.0.0 "
                          "--port 8188 > ~/comfyui.log 2>&1 &")
_SSH_STOP_CMD_DEFAULT = 'pkill -f "main.py --listen" || true'

_INST_DEFAULTS = {
    # ---- SSH 连接 ----
    "ssh_host": "",
    "ssh_port": 22,
    "ssh_user": "",
    "ssh_auth": "password",         # password / key
    "ssh_password": "",             # DPAPI（_SECRET_FIELDS）
    "ssh_key_path": "",
    "ssh_key_passphrase": "",       # DPAPI（_SECRET_FIELDS）
    "ssh_start_cmd": _SSH_START_CMD_DEFAULT,
    "ssh_stop_cmd": _SSH_STOP_CMD_DEFAULT,
    "ssh_host_fingerprint": "",     # 空 = 未记录（首次连接时写入）
    # ---- 云平台开关机（HTTP 钩子）----
    # {url, method, headers, body}；url 空 = 通道未配置。
    # 默认空表，由 UI 侧 `.get()` 兜底，避免老配置里出现半截骨架。
    "cloud_start": {},
    "cloud_stop": {},
    "cloud_start_timeout_s": 300,   # 本版固定 300，不暴露给用户
    # ---- 隧道 ----
    "tunnel_local_port": 8189,
    "tunnel_remote_host": "127.0.0.1",
    "tunnel_remote_port": 8188,
    # ---- 告警与通道记忆 ----
    "insecure_warn_muted": False,
    "last_ops_channel": "ssh",      # ssh / cloud
}

# 整数字段的归一规则：字段 -> (默认值, 下界, 上界)
_CLAMPS = {
    "ssh_port": (22, 1, 65535),
    "tunnel_local_port": (8189, 1024, 65535),
    "tunnel_remote_port": (8188, 1, 65535),
    "cloud_start_timeout_s": (300, 1, 3600),
}

# 枚举字段：字段 -> 允许值（小写比对，非法值回落默认）
_ENUM_FIELDS = {
    "ssh_auth": ("password", "key"),
    "last_ops_channel": ("ssh", "cloud"),
}

# 需要 strip 的字符串字段（命令模板不 strip，避免用户刻意写的行尾空白被吃掉）
_STRIP_FIELDS = ("ssh_host", "ssh_user", "ssh_key_path",
                 "ssh_host_fingerprint", "tunnel_remote_host")


def instance_ops_defaults() -> dict:
    """远程运维字段的默认值（深拷贝：cloud_start/cloud_stop 是 dict）"""
    return copy.deepcopy(_INST_DEFAULTS)


def _probe_roots() -> list:
    """常见 ComfyUI 安装位置（用于自动探测）

    2026-11-21：从写死盘符的静态表改为**按本机实际存在的盘符通配生成**。
    背景：以前写死了 `F:\\Comfy-Desktop\\ComfyUI-Installs\\...`，只有 F 盘
    装了官方 ComfyUI Desktop 的用户能被探测到；官方桌面版默认结构是
    `<盘>:\\Comfy-Desktop\\ComfyUI-Installs\\ComfyUI\\ComfyUI`，装在
    E/D/其它盘的用户直接漏掉，被客户反馈过（向导预填了错误理解的位置）。
    """
    import string
    drives = [c + ":\\" for c in string.ascii_uppercase
              if os.path.isdir(c + ":\\")]
    roots = [os.path.expanduser(r"~\Desktop\ComfyUI")]
    for d in drives:
        roots.append(os.path.join(d, "ComfyUI"))
        # 官方 ComfyUI Desktop 的默认安装结构
        roots.append(os.path.join(
            d, "Comfy-Desktop", "ComfyUI-Installs", "ComfyUI", "ComfyUI"))
        roots.append(os.path.join(d, "AI", "ComfyUI"))
    return roots


def _find_python_for(comfyui_path: str) -> str:
    """在 ComfyUI 目录附近探测配套 Python 解释器"""
    candidates = [
        # ComfyUI 旁边的独立 venv（与启动器同级，任意盘）
        os.path.join(comfyui_path, "..", "ComfyUI_env", "Scripts", "python.exe"),
        # 便携包内置环境
        os.path.join(comfyui_path, "python_embeded", "python.exe"),
        os.path.join(comfyui_path, "..", "python_embeded", "python.exe"),
        os.path.join(comfyui_path, ".venv", "Scripts", "python.exe"),
        os.path.join(comfyui_path, "..", ".venv", "Scripts", "python.exe"),
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
        # 连接方式（2026-11-23，0.8.39）：local=本机进程；remote=远程/云镜像
        # 老配置里没有这个字段，一律按 local 处理（见 _sanitize_instances）
        "kind": "local",
        "comfyui_path": "",
        "python_path": "",
        "port": 8188,
        # ---- 远程连接字段（kind=remote 时生效）----
        "base_url": "",                 # 例：http://192.168.1.20:8188 / https://xxx.platform.com
        "auth_type": "none",            # none / bearer / basic / header
        "auth_value": "",               # bearer token 或自定义头的值（DPAPI 加密落盘）
        "auth_header_name": "X-API-KEY",  # auth_type=header 时的头名
        "auth_user": "",
        "auth_pass": "",                # DPAPI 加密落盘
        "tls_verify": True,             # 自签证书时关掉
        "vram_mode": "自动",
        "extra_args": "",
        "auto_open_browser": True,
        # 插件方案（启动模式）：name = 展示名，enabled = 该方案启用的插件名列表，args = 附加启动参数
        "plugin_profiles": [{"name": "默认", "enabled": [], "args": ""}],
        "last_profile": "默认",
        "auto_apply_profile": True,        # 启动实例前自动应用 last_profile
        "notify_on_task_done": True,       # 任务完成时弹系统通知
    }
    # 远程运维字段（2026-12-01，0.8.40）：新实例直接预填启动/停止命令与隧道默认值，
    # 用户切到「远程实例」就能点「远程启动」（PRD A4：清空才拦，预填不当默认值看）
    inst.update(instance_ops_defaults())
    for root in _probe_roots():
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
            # 配置损坏时回退默认，不崩溃；但**必须先把坏文件留底**再回退，
            # 否则下次 save_config 会用默认骨架把用户的真配置无声覆盖掉
            # （2026-11-15 事故：外部工具写了带 BOM 的 UTF-8，json.load 失败，
            #  回退骨架被写回，用户设置全丢；utf-8-sig 可以吃掉 BOM，一并兼容）
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
            except Exception:
                try:
                    import time as _t
                    os.replace(CONFIG_PATH, CONFIG_PATH + ".bad_%d" % int(_t.time()))
                except OSError:
                    pass
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
    _unprotect_instances(cfg)
    st_changed = _sanitize_settings(cfg)
    _sanitize_instances(cfg, extra_changed=st_changed)
    return cfg


# 全局设置默认值（2026-12-01，0.8.40 远程产物清理）
# remote_output_dir 是 0.8.39 既有字段，不在此表内（不补、不改默认）。
_SETTINGS_DEFAULTS = {
    "remote_cleanup_enabled": False,
    "remote_cleanup_policy": "days",      # days / size
    "remote_cleanup_days": 30,
    "remote_cleanup_max_gb": 10,
}
_SETTINGS_CLAMPS = {"remote_cleanup_days": (30, 1, 365),
                    "remote_cleanup_max_gb": (10, 1, 500)}


def _sanitize_settings(cfg: dict) -> bool:
    """settings 自愈：补远程产物清理字段 + 归一；返回是否改变"""
    st = cfg.get("settings")
    if not isinstance(st, dict):
        st = {}
        cfg["settings"] = st
        changed = True
    else:
        changed = False
    for k, v in _SETTINGS_DEFAULTS.items():
        if k not in st:
            st[k] = v
            changed = True
    for k, (dflt, lo, hi) in _SETTINGS_CLAMPS.items():
        v = _clamp(st.get(k), dflt, lo, hi)
        if st.get(k) != v:
            st[k] = v
            changed = True
    policy = str(st.get("remote_cleanup_policy") or "").strip().lower()
    if policy not in ("days", "size"):
        policy = _SETTINGS_DEFAULTS["remote_cleanup_policy"]
    if st.get("remote_cleanup_policy") != policy:
        st["remote_cleanup_policy"] = policy
        changed = True
    if not isinstance(st.get("remote_cleanup_enabled"), bool):
        st["remote_cleanup_enabled"] = _as_bool(st.get("remote_cleanup_enabled"))
        changed = True
    return changed


# 远程实例的敏感字段（2026-11-23，0.8.39）：和 ai_api_key 同一套 DPAPI 处理，
# 落盘是密文、内存是明文。解不开（换机器/换用户）就当没填过，绝不发密文出去。
# 0.8.40 追加 SSH 密码与私钥口令（PRD X1）。
_SECRET_FIELDS = ("auth_value", "auth_pass", "ssh_password", "ssh_key_passphrase")


# ---------------------------------------------------------------- 字段归一
def _as_int(value, default: int) -> int:
    """能转 int 就转，转不了用默认值（脏配置不抛异常）"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)


def _clamp(value, default: int, lo: int, hi: int) -> int:
    return max(int(lo), min(int(hi), _as_int(value, default)))


def _as_bool(value) -> bool:
    """宽松布尔：`bool("false")` 是 True，手改配置里的 "false" 不能变成「已忽略告警」"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _normalize_inst_ops(inst: dict) -> bool:
    """补齐 + 归一远程运维字段；返回是否发生改变（供自愈落盘判断）"""
    changed = False
    for k, v in _INST_DEFAULTS.items():
        if k not in inst:
            inst[k] = copy.deepcopy(v)          # dict 必须深拷贝，禁止共享默认对象
            changed = True
    for k, (dflt, lo, hi) in _CLAMPS.items():
        v = _clamp(inst.get(k), dflt, lo, hi)
        if inst.get(k) != v:
            inst[k] = v
            changed = True
    for k, allowed in _ENUM_FIELDS.items():
        cur = str(inst.get(k) or "").strip().lower()
        if cur not in allowed:
            cur = _INST_DEFAULTS[k]
        if inst.get(k) != cur:
            inst[k] = cur
            changed = True
    for k in _STRIP_FIELDS:
        if isinstance(inst.get(k), str):
            v = inst[k].strip()
            if v != inst[k]:
                inst[k] = v
                changed = True
    for k in ("cloud_start", "cloud_stop"):
        if not isinstance(inst.get(k), dict):
            inst[k] = {}
            changed = True
    if not isinstance(inst.get("insecure_warn_muted"), bool):
        inst["insecure_warn_muted"] = _as_bool(inst.get("insecure_warn_muted"))
        changed = True
    return changed


def _unprotect_instances(cfg: dict) -> None:
    try:
        from app import secret_store
    except Exception:
        return
    for inst in cfg.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        for f in _SECRET_FIELDS:
            v = inst.get(f)
            if isinstance(v, str) and secret_store.is_encrypted(v):
                plain = secret_store.unprotect(v)
                inst[f] = plain or ""
                if plain is None:
                    inst["_auth_locked"] = True     # 供界面提示「密钥无法解密」


def _sanitize_instances(cfg: dict, extra_changed: bool = False) -> None:
    """实例列表自愈（2026-11-03）：修重复 id / 完全重复项 / 悬空 last_instance_id。

    背景：测试脚本往真实配置里塞写死 id 的实例后，跑几次就叠出多条同 id 副本
    （用户实测出现过 3 条一模一样的 PPTEST）。同 id 会让「按 id 查找」全部命中
    第一条（选中、删除、切页都会串），属于数据完整性问题，不能只当脏数据看。

    extra_changed（2026-12-01，0.8.40）：settings 侧也做了自愈时由调用方传入，
    使整次 load_config 只在末尾落盘一次（PRD §6「老配置自愈」）。
    """
    insts = cfg.get("instances")
    if not isinstance(insts, list):
        cfg["instances"] = []
        return
    import uuid as _uuid
    seen_ids, out, changed = set(), [], bool(extra_changed)
    for inst in insts:
        if not isinstance(inst, dict):
            changed = True
            continue
        iid = str(inst.get("id") or "")
        # 连接方式归一（2026-11-23，0.8.39）：老配置没有 kind 字段 → local。
        # 远程实例没有本地目录，跳过「目录重复」判定，改按 base_url + 认证比对。
        if inst.get("kind") not in ("local", "remote"):
            inst["kind"] = "local"
            changed = True
        # 关键字段补齐（2026-11-23，0.8.39）：远程实例没有本地目录，但插件页/
        # 模型页/工作流页都在用 inst["comfyui_path"] 强取。补成空串让它们走
        # 「空目录」分支，而不是 KeyError 崩掉整个界面。
        for k, dflt in (("comfyui_path", ""), ("python_path", ""), ("port", 8188)):
            if k not in inst:
                inst[k] = dflt
                changed = True
        # 远程运维字段补齐与归一（0.8.40）
        if _normalize_inst_ops(inst):
            changed = True
        # 完全重复（id/路径/端口都相同）→ 直接丢弃，保留第一条
        if iid and iid in seen_ids:
            if inst.get("kind") == "remote":
                dup = any(o.get("id") == iid
                          and o.get("kind") == "remote"
                          and o.get("base_url") == inst.get("base_url") for o in out)
            else:
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
        # 远程实例的 token / 密码同等待遇（2026-11-23，0.8.39）
        for inst in out.get("instances") or []:
            if not isinstance(inst, dict):
                continue
            inst.pop("_auth_locked", None)
            for f in _SECRET_FIELDS:
                v = inst.get(f)
                if v and not secret_store.is_encrypted(str(v)):
                    inst[f] = secret_store.protect(str(v))
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
