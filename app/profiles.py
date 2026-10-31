# -*- coding: utf-8 -*-
r"""启动模式（profile）：每工作流一个快照式运行模式。

体系（2026-10-31 Starmore 定）：
  内核快照（kernel_store，只读）× 插件快照（profile/custom_nodes）
  × 依赖快照（profile/site_overlay 或 L2 轻 venv）× 模型全局共享

  profile 目录布局：
    <tools_root>/profiles/<name>/
    ├── profile.json     元数据（内核指针/插件清单/依赖档/启动参数/模型引用）
    ├── custom_nodes\    该模式的插件集（与内核和其他模式完全隔离）
    ├── site_overlay\    L1 依赖 overlay（PYTHONPATH 前置压过共享 venv 同名包）
    ├── venv\            L2 兜底轻 venv（--system-site-packages 共享大件）
    └── workflows\       归属该模式的工作流 JSON

  启动拼参（process.build_command 消费合成 inst）：
    · 新主线内核（有 --base-directory）→ 官方基座模式：
      --base-directory <profile_dir> --models-directory <共享模型>，内核目录零触碰
    · 旧核心（aki 类）→ junction 换头模式：
      内核 custom_nodes 指向 profile 的 custom_nodes（唯一改动点，不碰内核代码）
"""
import json
import os
import re
import shutil
import subprocess
import time

PROFILE_FIELDS = {
    "kernel_version": "", "created": "", "deps_mode": "L1",
    "port": 8310, "vram_mode": "自动", "args": "",
    "launch_mode": "auto",          # auto | base_dir | junction
    "models_dir": "",               # 空 = 默认共享模型目录
    "plugins": [],                  # [{name, url, installed, deps_ok}]
    "workflows": [],                # 相对 workflows/ 的文件名
    "notes": "",
}


# ---------------- 路径 ----------------
def profiles_root() -> str:
    from app.bootstrap import tools_root
    d = os.path.join(tools_root(), "profiles")
    os.makedirs(d, exist_ok=True)
    return d


def profile_dir(name: str) -> str:
    return os.path.join(profiles_root(), name)


def default_models_dir() -> str:
    from app.bootstrap import tools_root
    d = os.path.join(tools_root(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def _slug(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)[:64]


# ---------------- CRUD ----------------
def list_profiles() -> list:
    """扫描全部 profile（读各目录 profile.json），损坏的标记 broken"""
    out = []
    root = profiles_root()
    for name in sorted(os.listdir(root)):
        pdir = os.path.join(root, name)
        if not os.path.isdir(pdir):
            continue
        p = load_profile(name)
        if p is None:
            out.append({"name": name, "broken": True})
        else:
            p["broken"] = False
            p["size_mb"] = _dir_size_mb(pdir)
            out.append(p)
    return out


def load_profile(name: str) -> dict | None:
    f = os.path.join(profile_dir(name), "profile.json")
    try:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        base = dict(PROFILE_FIELDS)
        base.update(data)
        base["name"] = name
        return base
    except Exception:
        return None


def save_profile(p: dict) -> None:
    name = p["name"]
    pdir = profile_dir(name)
    os.makedirs(pdir, exist_ok=True)
    f = os.path.join(pdir, "profile.json")
    data = {k: p.get(k, v) for k, v in PROFILE_FIELDS.items()}
    with open(f + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(f + ".tmp", f)


def create_profile(name: str, kernel_version: str, deps_mode: str = "L1",
                   models_dir: str = "", log=print) -> dict:
    """新建空白启动模式（骨架目录 + profile.json）"""
    from app import kernel_store
    if load_profile(name) is not None or os.path.isdir(profile_dir(name)):
        raise ValueError(f"启动模式「{name}」已存在")
    if not kernel_store.get_kernel(kernel_version):
        raise ValueError(f"内核「{kernel_version}」不存在，请先导入内核快照")
    pdir = profile_dir(name)
    for sub in ("custom_nodes", "workflows", "site_overlay", "output", "input",
                "user", "temp"):
        os.makedirs(os.path.join(pdir, sub), exist_ok=True)
    p = dict(PROFILE_FIELDS)
    p.update({
        "name": name, "kernel_version": kernel_version,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "deps_mode": deps_mode, "models_dir": models_dir,
    })
    save_profile(p)
    log(f"[启动模式] 已创建「{name}」（内核 {kernel_version}，依赖 {deps_mode}）")
    return p


def delete_profile(name: str, log=print) -> bool:
    pdir = profile_dir(name)
    if not os.path.isdir(pdir):
        return False
    root = os.path.normpath(profiles_root())
    if not os.path.normpath(pdir).startswith(root):
        log("[启动模式] 拒绝删除 profiles 目录外的路径")
        return False
    shutil.rmtree(pdir, ignore_errors=True)
    log(f"[启动模式] 已删除「{name}」")
    return True


def duplicate_profile(src: str, dst: str, log=print) -> dict:
    """复制模式（同插件集的工作流共享一个模式时的粒度选择）"""
    if load_profile(src) is None:
        raise ValueError(f"源模式「{src}」不存在")
    if os.path.isdir(profile_dir(dst)):
        raise ValueError(f"目标「{dst}」已存在")
    shutil.copytree(profile_dir(src), profile_dir(dst))
    p = load_profile(dst)
    p["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_profile(p)
    log(f"[启动模式] 已复制「{src}」→「{dst}」")
    return p


def _dir_size_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / 2 ** 20, 1)


# ---------------- 插件安装进模式 ----------------
def _repo_name(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def install_plugin(name: str, url: str, log=print) -> dict:
    """git clone 插件进 profile 的 custom_nodes，并把其 requirements 装进 overlay。
    返回 {ok, plugin, msg}。"""
    from app.instance import git_clone
    p = load_profile(name)
    if p is None:
        raise ValueError(f"模式「{name}」不存在")
    pname = _repo_name(url)
    dest = os.path.join(profile_dir(name), "custom_nodes", pname)
    if os.path.isdir(dest):
        return {"ok": True, "plugin": pname, "msg": "已存在，跳过"}
    ok, msg = git_clone(url, dest)
    if not ok:
        return {"ok": False, "plugin": pname, "msg": str(msg)}
    deps_ok = _install_plugin_deps(name, dest, log=log)
    p["plugins"] = [x for x in p.get("plugins", [])
                    if x.get("name") != pname]
    p["plugins"].append({"name": pname, "url": url,
                         "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "deps_ok": deps_ok})
    save_profile(p)
    return {"ok": True, "plugin": pname, "msg": "安装完成"}


def _install_plugin_deps(name: str, plugin_dir: str, log=print) -> bool:
    """插件 requirements.txt → site_overlay（L1）。L2 模式直接装进模式 venv。"""
    req = os.path.join(plugin_dir, "requirements.txt")
    if not os.path.isfile(req):
        return True
    p = load_profile(name) or {}
    python = _python_for(p, None)
    if p.get("deps_mode") == "L2":
        target_args = []
    else:
        overlay = os.path.join(profile_dir(name), "site_overlay")
        os.makedirs(overlay, exist_ok=True)
        target_args = ["--target", overlay]
    try:
        r = subprocess.run(
            [python, "-m", "pip", "install", "-r", req, "-q", *target_args],
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            log(f"[启动模式] ⚠️ 依赖安装失败（{os.path.basename(plugin_dir)}）："
                f"{(r.stderr or '')[-300:]}")
            return False
        return True
    except Exception as e:
        log(f"[启动模式] ⚠️ 依赖安装异常：{e}")
        return False


def create_l2_venv(name: str, log=print) -> str:
    """L2 兜底：给模式建轻 venv（--system-site-packages 共享 torch 等大件）"""
    p = load_profile(name)
    k = _kernel_or_none(p)
    python = _python_for(p, k)
    venv_dir = os.path.join(profile_dir(name), "venv")
    log(f"[启动模式] 创建 L2 轻 venv（共享系统包）：{venv_dir}")
    r = subprocess.run([python, "-m", "venv", "--system-site-packages", venv_dir],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"venv 创建失败：{(r.stderr or '')[-300:]}")
    p["deps_mode"] = "L2"
    save_profile(p)
    return os.path.join(venv_dir, "Scripts", "python.exe")


# ---------------- junction 换头（旧核心兜底） ----------------
def _is_reparse(path: str) -> bool:
    try:
        from pathlib import Path
        if hasattr(Path, "is_junction"):
            return Path(path).is_junction() or os.path.islink(path)
        import stat as st
        return bool(os.stat(path).st_file_attributes
                    & st.FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def ensure_junction(kernel: dict, profile: dict, log=print) -> None:
    """旧核心模式：把内核 custom_nodes 指向 profile 的 custom_nodes。
    首次会把内核原生 custom_nodes 改名留档（__kernel_default），之后只切链接。"""
    kpath = kernel["path"]
    link = os.path.join(kpath, "custom_nodes")
    target = os.path.join(profile_dir(profile["name"]), "custom_nodes")
    os.makedirs(target, exist_ok=True)
    if os.path.isdir(link) and not _is_reparse(link):
        bak = link + ".__kernel_default"
        if not os.path.isdir(bak):
            os.rename(link, bak)
            log(f"[启动模式] 内核原生 custom_nodes 留档 → {os.path.basename(bak)}")
        else:
            shutil.rmtree(link)
    elif _is_reparse(link):
        os.rmdir(link)          # 只删链接不删目标
    # cmd 输出是 GBK，text=True 会以 UTF-8 解码炸读者线程——拿字节自己解
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or b"").decode("gbk", "replace")
        raise RuntimeError(f"junction 创建失败：{err}")
    log(f"[启动模式] junction 换头：custom_nodes → {target}")


# ---------------- 启动拼参 ----------------
def _kernel_or_none(profile: dict) -> dict | None:
    from app import kernel_store
    if not profile or not profile.get("kernel_version"):
        return None
    return kernel_store.get_kernel(profile["kernel_version"])


def _python_for(profile: dict, kernel: dict | None) -> str:
    if profile and profile.get("deps_mode") == "L2":
        vpy = os.path.join(profile_dir(profile["name"]), "venv",
                           "Scripts", "python.exe")
        if os.path.isfile(vpy):
            return vpy
    if kernel and kernel.get("python_path"):
        return kernel["python_path"]
    return "python"


def prepare_launch(profile: dict, port: int | None = None,
                   log=print) -> dict:
    """把启动模式合成为 ComfyProcess 可用的 inst 字典。
    新主线 → base_directory/models_directory 结构化字段（process.build_command 消费）；
    旧核心 → junction 换头；L1 overlay 非空 → env.PYTHONPATH。"""
    k = _kernel_or_none(profile)
    if k is None or k.get("missing"):
        raise RuntimeError(
            f"内核「{profile.get('kernel_version')}」缺失，请到内核管理重新导入")
    mode = profile.get("launch_mode", "auto")
    base_dir_mode = k.get("base_dir_flag") if mode == "auto" else \
        (mode == "base_dir")
    pdir = profile_dir(profile["name"])
    inst = {
        "id": "profile_" + _slug(profile["name"]),
        "name": f"⚡{profile['name']}",
        "comfyui_path": k["path"],
        "python_path": _python_for(profile, k),
        "port": int(port or profile.get("port") or 8310),
        "vram_mode": profile.get("vram_mode", "自动"),
        "extra_args": (profile.get("args") or "").strip(),
        "auto_open_browser": True,
    }
    if base_dir_mode:
        # 官方基座：custom_nodes/user/output 全部落在 profile 目录，模型共享
        inst["base_directory"] = pdir
        inst["models_directory"] = profile.get("models_dir") or \
            default_models_dir()
    else:
        ensure_junction(k, profile, log=log)
    overlay = os.path.join(pdir, "site_overlay")
    if os.path.isdir(overlay) and os.listdir(overlay):
        inst["env"] = {"PYTHONPATH": overlay}
    return inst
