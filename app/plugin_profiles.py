# -*- coding: utf-8 -*-
"""插件方案（启动模式）+ 重复节点检查

设计要点：
  · 方案 = 一组"启用插件名"的集合（+ 可选启动参数）
  · 切换方案 = 只移动差异项（custom_nodes/xxx <-> custom_nodes/xxx.disabled）
  · 每次批量操作先写 journal，异常可回滚 —— 绝不让用户插件凭空消失
  · 重复节点检查用**静态解析**（读 .py 文本里的 NODE_CLASS_MAPPINGS 键名），
    不 import 插件代码，避免执行第三方代码与副作用
"""
import json
import os
import re
import time

JOURNAL_NAME = ".comfyhub_profile_journal.json"


# ---------------------------------------------------------------- 基础查询
def _git_remote_url(repo_dir: str) -> str:
    """读 .git/config 里 [remote "origin"] 的 url（纯文本解析，不调 git 命令）"""
    cfg = os.path.join(repo_dir, ".git", "config")
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as f:
            in_origin = False
            for line in f:
                s = line.strip()
                if s.startswith("["):
                    in_origin = s in ('[remote "origin"]', "[remote \"origin\" ]")
                    continue
                if in_origin and s.startswith("url"):
                    _, _, v = s.partition("=")
                    return v.strip()
    except OSError:
        pass
    return ""


def list_plugins(comfyui_path: str) -> list:
    """[{name, enabled, git, dir, url}] —— name 为去掉 .disabled 的插件名"""
    nodes_dir = os.path.join(comfyui_path, "custom_nodes")
    out = []
    if not os.path.isdir(nodes_dir):
        return out
    for entry in sorted(os.listdir(nodes_dir)):
        p = os.path.join(nodes_dir, entry)
        if not os.path.isdir(p) or entry.startswith("__") or entry.startswith("."):
            continue
        disabled = entry.endswith(".disabled")
        name = entry[:-len(".disabled")] if disabled else entry
        is_git = os.path.isdir(os.path.join(p, ".git"))
        out.append({
            "name": name,
            "enabled": not disabled,
            "git": is_git,
            "dir": p,
            "url": _git_remote_url(p) if is_git else "",
        })
    return out


def capture_current(comfyui_path: str) -> list:
    """把当前"已启用"的插件名收集成一份方案"""
    return [p["name"] for p in list_plugins(comfyui_path) if p["enabled"]]


# ---------------------------------------------------------------- 应用方案
def apply_profile(comfyui_path: str, enabled_names, log=print) -> dict:
    """按方案批量启用/禁用。只动差异项。

    返回 {ok, changed: [(name, "on"|"off")], errors: [str], rolled_back: bool}
    """
    nodes_dir = os.path.join(comfyui_path, "custom_nodes")
    if not os.path.isdir(nodes_dir):
        return {"ok": False, "changed": [], "errors": ["custom_nodes 目录不存在"], "rolled_back": False}

    want_on = {n for n in (enabled_names or [])}
    current = list_plugins(comfyui_path)

    # 计算差异
    plan = []          # [(src_dir_name, dst_dir_name, plugin_name, action)]
    for p in current:
        on_now = p["enabled"]
        should_on = p["name"] in want_on
        if on_now == should_on:
            continue
        if should_on:
            plan.append((p["name"] + ".disabled", p["name"], p["name"], "on"))
        else:
            plan.append((p["name"], p["name"] + ".disabled", p["name"], "off"))

    if not plan:
        log("方案与当前状态一致，无需改动")
        return {"ok": True, "changed": [], "errors": [], "rolled_back": False}

    # 先写 journal（记录预期动作），成功后再删
    journal = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nodes_dir": nodes_dir,
        "plan": plan,
    }
    jpath = os.path.join(comfyui_path, JOURNAL_NAME)
    try:
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(journal, f, ensure_ascii=False, indent=1)
    except OSError as e:
        return {"ok": False, "changed": [], "errors": [f"无法写 journal: {e}"], "rolled_back": False}

    done = []
    errors = []
    for src, dst, name, action in plan:
        s = os.path.join(nodes_dir, src)
        d = os.path.join(nodes_dir, dst)
        try:
            if not os.path.isdir(s):
                errors.append(f"{name}: 源不存在（可能被手动改动）")
                continue
            if os.path.exists(d):
                errors.append(f"{name}: 目标已存在，跳过以免覆盖")
                continue
            os.rename(s, d)
            done.append((src, dst))
            log(("启用 " if action == "on" else "停用 ") + name)
        except OSError as e:
            errors.append(f"{name}: {e}")
            break

    rolled_back = False
    if errors:
        # 回滚已完成的部分
        for src, dst in reversed(done):
            try:
                os.rename(os.path.join(nodes_dir, dst), os.path.join(nodes_dir, src))
            except OSError:
                pass
        rolled_back = True
        log(f"❌ 出错已回滚（{len(done)} 项）")
    else:
        try:
            os.remove(jpath)
        except OSError:
            pass

    changed = [(n, a) for (s, d, n, a) in plan if (s, d) in done]
    return {"ok": not errors, "changed": changed, "errors": errors, "rolled_back": rolled_back}


def recover_if_needed(comfyui_path: str, log=print) -> bool:
    """启动时检查有无未完成的切换（journal 残留）→ 回滚，避免插件处于半移动状态"""
    jpath = os.path.join(comfyui_path, JOURNAL_NAME)
    if not os.path.isfile(jpath):
        return False
    try:
        j = json.load(open(jpath, encoding="utf-8"))
        nodes_dir = j.get("nodes_dir") or os.path.join(comfyui_path, "custom_nodes")
        log(f"⚠️ 发现未完成的插件方案切换（{j.get('time')}），正在恢复…")
        for src, dst, name, action in reversed(j.get("plan", [])):
            s, d = os.path.join(nodes_dir, src), os.path.join(nodes_dir, dst)
            if os.path.isdir(d) and not os.path.isdir(s):
                os.rename(d, s)
        os.remove(jpath)
        log("✅ 已恢复切换前的状态")
        return True
    except Exception as e:
        log(f"恢复失败: {e}")
        return False


# ---------------------------------------------------------------- 重复节点检查
_NCM_RE = re.compile(
    r"NODE_CLASS_MAPPINGS\s*(?::\s*[^=]+)?=\s*\{(?P<body>.*?)\}",
    re.S | re.I,
)
_KEY_RE = re.compile(r"""["']([A-Za-z0-9_ .\-]{2,80})["']\s*:""")

_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".github", "web", "js"}


def _scan_plugin_node_keys(plugin_dir: str, max_files: int = 60) -> set:
    """静态解析插件目录里的 Python 文件，抓 NODE_CLASS_MAPPINGS 的键名（节点类型）"""
    keys = set()
    scanned = 0
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            if scanned >= max_files:
                return keys
            scanned += 1
            p = os.path.join(root, fn)
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if "NODE_CLASS_MAPPINGS" not in txt:
                continue
            for m in _NCM_RE.finditer(txt):
                body = m.group("body")
                if len(body) > 200_000:
                    continue
                for km in _KEY_RE.finditer(body):
                    k = km.group(1).strip()
                    if k and not k.startswith("__"):
                        keys.add(k)
    return keys


def scan_duplicate_nodes(comfyui_path: str, only_enabled: bool = True, log=None) -> dict:
    """扫描重复节点。

    返回 {conflicts: {节点名: [插件1, 插件2]}, scanned: 插件数, keys_total: 节点数}
    only_enabled=True 只查已启用插件（禁用中的不参与加载，不构成冲突）
    """
    plugins = list_plugins(comfyui_path)
    if only_enabled:
        plugins = [p for p in plugins if p["enabled"]]

    owner = {}       # 节点名 -> [插件名]
    keys_total = 0
    for p in plugins:
        ks = _scan_plugin_node_keys(p["dir"])
        keys_total += len(ks)
        for k in ks:
            owner.setdefault(k, []).append(p["name"])
        if log:
            log(f"  扫描 {p['name']}: {len(ks)} 个节点")

    conflicts = {k: v for k, v in owner.items() if len(v) > 1}
    return {"conflicts": conflicts, "scanned": len(plugins), "keys_total": keys_total}
