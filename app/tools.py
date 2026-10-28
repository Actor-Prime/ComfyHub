# -*- coding: utf-8 -*-
"""内核版本管理（git tag 切换）/ 模型下载器 / 环境备份 / 救援模式 —— v0.3 框架"""
import os
import json
import shutil
import time
import hashlib
import subprocess

from app.winsub import run

from app.bootstrap import find_git, download_multi
from app import instance as inst_lib


# ================= 内核版本管理 =================
class KernelManager:
    """基于 git 的内核版本查看与切换（要求实例目录是 git 仓库）"""

    def __init__(self, comfyui_path: str, proxy: str = ""):
        self.path = comfyui_path
        self.git = find_git()
        self.proxy = (proxy or "").strip()
        self.last_fetch_ok = True   # list_versions 的 fetch 成败（UI 提示用）

    @property
    def is_git_repo(self) -> bool:
        return self.git and os.path.isdir(os.path.join(self.path, ".git"))

    def _env(self) -> "dict | None":
        """git 子进程环境：实例配了代理则注入 HTTP(S)_PROXY（fetch/pull 走代理）"""
        if not self.proxy:
            return None
        env = dict(os.environ)
        env["HTTP_PROXY"] = self.proxy
        env["HTTPS_PROXY"] = self.proxy
        return env

    def remote_url(self) -> str:
        """origin 远端地址（失败返回空串）——UI 顶部展示，消除「源不一样」困惑"""
        if not self.is_git_repo:
            return ""
        try:
            r = run([self.git, "-C", self.path, "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=30,
                    encoding="utf-8", errors="replace", env=self._env())
            return (r.stdout or "").strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def current_branch(self) -> str:
        """当前分支名（HEAD 游离时返回短 hash）"""
        if not self.is_git_repo:
            return ""
        try:
            r = run([self.git, "-C", self.path, "branch", "--show-current"],
                    capture_output=True, text=True, timeout=30,
                    encoding="utf-8", errors="replace")
            b = (r.stdout or "").strip()
            if b:
                return b
            r = run([self.git, "-C", self.path, "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=30,
                    encoding="utf-8", errors="replace")
            return (r.stdout or "").strip()
        except Exception:
            return ""

    def fetch_remote(self, timeout=120) -> bool:
        """拉取远端更新（tags + 分支提交）。失败返回 False（不抛，降级用本地缓存）"""
        if not self.is_git_repo:
            return False
        try:
            r = run([self.git, "-C", self.path, "fetch", "--tags", "--quiet"],
                    capture_output=True, timeout=timeout, env=self._env())
            return r.returncode == 0
        except Exception:
            return False

    def current_version(self) -> str:
        from app.instance import read_comfyui_version
        return read_comfyui_version(self.path)

    def list_tags(self, limit=30) -> list:
        """最近 tag 列表（git 拉取）"""
        if not self.is_git_repo:
            return []
        try:
            self.fetch_remote(timeout=300)
            r = run(
                [self.git, "-C", self.path, "tag", "--sort=-creatordate"],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace")
            return [t for t in (r.stdout or "").splitlines() if t][:limit]
        except Exception:
            return []

    def stable_branch(self) -> str:
        """稳定分支名（master 或 main）"""
        for b in ("master", "main"):
            r = run([self.git, "-C", self.path, "rev-parse", "--verify", b],
                    capture_output=True, timeout=30)
            if r.returncode == 0:
                return b
        return "master"

    def update_stable(self) -> tuple:
        """更新至最新稳定版：checkout 稳定分支 + pull（未提交改动先 stash）"""
        if not self.is_git_repo:
            return False, "该实例不是 git 仓库（zip 安装），请重新以 git 方式安装"
        b = self.stable_branch()
        try:
            run([self.git, "-C", self.path, "stash"],
                capture_output=True, timeout=60, env=self._env())
            run([self.git, "-C", self.path, "checkout", b],
                capture_output=True, timeout=120, env=self._env())
            r = run([self.git, "-C", self.path, "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=600,
                    encoding="utf-8", errors="replace", env=self._env())
            ok = r.returncode == 0
            msg = ((r.stdout or "") + (r.stderr or "")).strip()[:400]
            return ok, (msg if ok else f"pull 失败（可稍后重试或手动 git pull）: {msg[:200]}")
        except Exception as e:
            return False, str(e)

    def list_versions(self, limit=50, fetch=True) -> list:
        """版本列表（tag=稳定版优先）：[{version, hash, date, kind}]

        2026-10-26 修复：原来 tag 与提交共用一个 limit 且最后统一 out[:limit]，
        tag 超过 limit 时提交（开发版）会被整段截掉 →「开发版」筛选恒为空。
        现在两类各自取 limit，互不挤占。
        2026-10-29 修复：fetch 前置（原来只读本地 refs，永远看不到远端新提交，
        与绘世等启动器列表对不上的根因）；fetch 失败降级本地缓存（last_fetch_ok=False）。
        """
        if not self.is_git_repo:
            return []
        self.last_fetch_ok = True
        if fetch:
            self.last_fetch_ok = self.fetch_remote(timeout=120)
        tags, devs = [], []
        tag_hashes = set()      # tag 对象哈希 + peel 出的 commit 哈希（开发版去重用）
        try:
            # tag（稳定版）：名字|tag对象短hash|指向commit短hash|日期
            # %(*objectname:short) = annotated tag peel 出的 commit 哈希
            #（lightweight tag 此列为空，去重时回退用 tag 对象哈希）
            r = run([self.git, "-C", self.path, "for-each-ref",
                     "refs/tags", "--sort=-creatordate",
                     "--format=%(refname:short)|%(objectname:short)"
                     "|%(*objectname:short)|%(creatordate:short)"],
                    capture_output=True, text=True, timeout=60,
                    encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines():
                parts = line.split("|")
                if len(parts) == 4:
                    tags.append({"version": parts[0], "hash": parts[2] or parts[1],
                                 "date": parts[3], "kind": "稳定版"})
                    tag_hashes.add(parts[1])
                    if parts[2]:
                        tag_hashes.add(parts[2])
        except Exception:
            pass
        try:
            # 最近提交（开发版）；与 tag 同 hash 的提交视为已被 tag 覆盖，跳过
            r = run([self.git, "-C", self.path, "log", f"-{limit}",
                     "--format=%h|%cd|%s", "--date=short"],
                    capture_output=True, text=True, timeout=60,
                    encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3 and parts[0] not in tag_hashes:
                    devs.append({"version": parts[2][:40] or parts[0],
                                 "hash": parts[0], "date": parts[1],
                                 "kind": "开发版"})
        except Exception:
            pass
        return tags[:limit] + devs[:limit]


    def switch_to(self, tag: str) -> tuple:
        """检出指定版本（未提交改动会 stash 保存）"""
        if not self.is_git_repo:
            return False, "该实例不是 git 仓库（zip 安装），请重新以 git 方式安装"
        try:
            run([self.git, "-C", self.path, "stash"],
                           capture_output=True, timeout=60, env=self._env())
            r = run([self.git, "-C", self.path, "checkout", tag],
                               capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace",
                               env=self._env())
            ok = r.returncode == 0
            return ok, ((r.stdout or "") + (r.stderr or "")).strip()[:400]
        except Exception as e:
            return False, str(e)


# ================= 模型下载器 =================
class ModelDownloader:
    """通用 URL 模型下载（断点续传），v0.3 框架：CivitAI/HF 解析留接口"""

    MODEL_SUBDIRS = ["checkpoints", "loras", "vae", "controlnet",
                     "unet", "clip", "clip_vision", "upscale_models",
                     "embeddings", "diffusion_models", "text_encoders"]

    def __init__(self, comfyui_path: str):
        self.path = comfyui_path

    def resolve_dest(self, subdir: str, filename: str) -> str:
        if subdir not in self.MODEL_SUBDIRS:
            subdir = "checkpoints"
        d = os.path.join(self.path, "models", subdir)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, filename)

    def download(self, url: str, subdir: str, filename: str = "", log=print) -> bool:
        """下载模型到指定类别目录（断点续传）。CivitAI 直链需带 token 参数。
        HuggingFace 链接自动替换为国内镜像 hf-mirror.com（免梯直连）。"""
        # HF 镜像替换：huggingface.co 被墙，hf-mirror.com 国内直连可用
        if "huggingface.co/" in url:
            mirror_url = url.replace("huggingface.co/", "hf-mirror.com/")
            if mirror_url != url:
                log("检测到 HuggingFace 链接，已自动切换国内镜像 hf-mirror.com")
                url = mirror_url
        if not filename:
            filename = url.rstrip("/").split("?")[0].split("/")[-1]
        dest = self.resolve_dest(subdir, filename)
        if os.path.isfile(dest):
            log(f"文件已存在，跳过: {dest}")
            return True
        log(f"开始下载 {filename} → models/{subdir}/")
        return download_multi(url, dest, log=log, max_retry=100, timeout=120)

    # TODO(v0.4): civitai_search(api_token) / hf_search / resolve_missing


# ================= 环境备份 =================
def _inst_dirname(comfyui_path: str) -> str:
    """实例存储目录名：basename + 路径哈希 8 位。
    只用 basename 会让不同盘/不同父目录下的同名 ComfyUI
    （如 F:\\ComfyUI 与 G:\\ComfyUI）共用备份/快照目录、互相污染。"""
    base = os.path.basename(comfyui_path.rstrip("\\/")) or "instance"
    key = os.path.normcase(os.path.normpath(os.path.abspath(comfyui_path)))
    return f"{base}_{hashlib.md5(key.encode('utf-8')).hexdigest()[:8]}"


class EnvBackup:
    """Python 环境（pip freeze）+ 插件清单快照的备份与还原"""

    def __init__(self, comfyui_path: str, python_path: str):
        self.comfyui_path = comfyui_path
        self.python_path = python_path
        from app.paths import data_root
        self.backup_dir = os.path.join(data_root(), "backups",
                                       _inst_dirname(comfyui_path))
        os.makedirs(self.backup_dir, exist_ok=True)

    def create(self, label: str = "", log=print) -> str:
        """创建备份快照，返回备份名"""
        ts = time.strftime("%Y%m%d_%H%M%S")
        name = f"{ts}_{label}" if label else ts
        bdir = os.path.join(self.backup_dir, name)
        os.makedirs(bdir, exist_ok=True)
        # 1) pip freeze
        req = os.path.join(bdir, "requirements_freeze.txt")
        try:
            r = run(
                [self.python_path, "-m", "pip", "freeze"],
                capture_output=True, text=True, timeout=300,
                encoding="utf-8", errors="replace")
            open(req, "w", encoding="utf-8").write(r.stdout or "")
        except Exception as e:
            log(f"pip freeze 失败: {e}")
        # 2) 插件清单（名称规范化：剥离 .disabled，与 Rescue 快照口径一致）
        nodes = inst_lib.list_custom_nodes(self.comfyui_path)
        state = []
        for n, g, en in nodes:
            base = n[:-len(".disabled")] if n.endswith(".disabled") else n
            state.append({"name": base, "git": g, "enabled": en})
        json.dump(state,
                  open(os.path.join(bdir, "custom_nodes.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        log(f"备份完成: {name}")
        return name

    def list_backups(self) -> list:
        if not os.path.isdir(self.backup_dir):
            return []
        return sorted(os.listdir(self.backup_dir), reverse=True)

    def restore(self, name: str, log=print) -> bool:
        """还原：按 freeze 重装依赖（版本对齐）。插件目录还原属破坏性操作，v0.4 再开放。"""
        bdir = os.path.join(self.backup_dir, name)
        req = os.path.join(bdir, "requirements_freeze.txt")
        if not os.path.isfile(req):
            log("备份不存在或缺 freeze 文件")
            return False
        # 镜像优先（官方索引里大包慢）
        from app.bootstrap import PYPI_MIRRORS
        mirror = PYPI_MIRRORS["阿里云"]
        cmd = [self.python_path, "-m", "pip", "install", "-r", req, "-i", mirror,
               "--no-warn-script-location"]
        log("$ pip install -r requirements_freeze.txt（还原环境，耗时较长）")
        r = run(cmd, capture_output=True, text=True, timeout=7200,
                           encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        log("还原" + ("成功" if ok else "失败: " + (r.stderr or "")[-200:]))
        return ok


# ================= 救援模式 =================
class Rescue:
    """插件安装前快照 → 出问题一键回滚（目录级，轻量可靠）"""

    def __init__(self, comfyui_path: str):
        self.comfyui_path = comfyui_path
        self.nodes_dir = os.path.join(comfyui_path, "custom_nodes")
        from app.paths import data_root
        self.snap_dir = os.path.join(data_root(), "rescue",
                                     _inst_dirname(comfyui_path))
        os.makedirs(self.snap_dir, exist_ok=True)

    def snapshot(self, log=print) -> str:
        """记录当前插件状态（名称+启停+git地址），不动文件"""
        ts = time.strftime("%Y%m%d_%H%M%S")
        # ⚠️ 传 comfyui_path 而非 nodes_dir（list_custom_nodes 内部会再拼 custom_nodes）
        nodes = inst_lib.list_custom_nodes(self.comfyui_path)
        # 名称规范化：剥离 .disabled 后缀，启停用 enabled 字段表达，
        # 否则回滚时会把 plugB.disabled 改名成 plugB.disabled.disabled（双重禁用）
        state = []
        for n, _g, en in nodes:
            base = n[:-len(".disabled")] if n.endswith(".disabled") else n
            state.append({"name": base, "enabled": en})
        path = os.path.join(self.snap_dir, f"{ts}.json")
        json.dump(state, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        log(f"救援快照: {ts}（{len(state)} 个插件）")
        return ts

    def list_snapshots(self) -> list:
        return sorted(os.listdir(self.snap_dir), reverse=True) \
            if os.path.isdir(self.snap_dir) else []

    def rollback(self, snap_name: str, log=print) -> tuple:
        """回滚到快照状态：禁用快照里没有的插件 + 恢复启停状态。
        只做目录改名（.disabled），不删除文件——安全第一。
        """
        path = os.path.join(self.snap_dir, snap_name)
        if not os.path.isfile(path):
            return False, "快照不存在"
        state = json.load(open(path, encoding="utf-8"))
        # 防御旧格式快照：加载时也剥离 .disabled 后缀
        want = {}
        for s in state:
            n = s["name"]
            if n.endswith(".disabled"):
                n = n[:-len(".disabled")]
            want[n] = s["enabled"]
        # ⚠️ 传 comfyui_path（内部拼 custom_nodes）；并按基名规范化：
        # cur[基名] = (实际目录名, 当前是否启用)
        now = inst_lib.list_custom_nodes(self.comfyui_path)
        cur = {}
        for n, _g, en in now:
            base = n[:-len(".disabled")] if n.endswith(".disabled") else n
            if base not in cur:  # foo 与 foo.disabled 并存的异常态只取首个
                cur[base] = (n, en)
        actions = []
        # 1) 快照里没有的现插件 → 禁用（救援核心：新装插件导致崩溃时回滚）
        for base, (actual, enabled) in cur.items():
            if base not in want and enabled:
                src = os.path.join(self.nodes_dir, actual)
                dst = src + ".disabled"
                try:
                    os.rename(src, dst)
                    actions.append(f"禁用新增: {base}")
                except OSError as e:
                    actions.append(f"禁用失败 {base}: {e}")
        # 2) 恢复快照里的启停状态（按基名比较，rename 目标用基名拼，
        #    避免出现 xxx.disabled.disabled）
        for base, enabled in want.items():
            hit = cur.get(base)
            if hit is None:
                continue  # 插件已不存在，跳过
            actual, cur_en = hit
            src = os.path.join(self.nodes_dir, actual)
            if enabled and not cur_en:
                dst = os.path.join(self.nodes_dir, base)
                try:
                    os.rename(src, dst)
                    actions.append(f"恢复启用: {base}")
                except OSError:
                    pass
            elif (not enabled) and cur_en:
                dst = os.path.join(self.nodes_dir, base + ".disabled")
                try:
                    os.rename(src, dst)
                    actions.append(f"恢复禁用: {base}")
                except OSError:
                    pass
        msg = "；".join(actions) if actions else "状态一致，无需回滚"
        return True, msg
