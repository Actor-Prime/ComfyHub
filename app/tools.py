# -*- coding: utf-8 -*-
"""内核版本管理（git tag 切换）/ 模型下载器 / 环境备份 / 救援模式 —— v0.3 框架"""
import os
import json
import shutil
import time
import subprocess

from app.winsub import run

from app.bootstrap import find_git, download_multi
from app import instance as inst_lib


# ================= 内核版本管理 =================
class KernelManager:
    """基于 git 的内核版本查看与切换（要求实例目录是 git 仓库）"""

    def __init__(self, comfyui_path: str):
        self.path = comfyui_path
        self.git = find_git()

    @property
    def is_git_repo(self) -> bool:
        return self.git and os.path.isdir(os.path.join(self.path, ".git"))

    def current_version(self) -> str:
        from app.instance import read_comfyui_version
        return read_comfyui_version(self.path)

    def list_tags(self, limit=30) -> list:
        """最近 tag 列表（git 拉取）"""
        if not self.is_git_repo:
            return []
        try:
            run([self.git, "-C", self.path, "fetch", "--tags", "--quiet"],
                           capture_output=True, timeout=300)
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
                capture_output=True, timeout=60)
            run([self.git, "-C", self.path, "checkout", b],
                capture_output=True, timeout=120)
            r = run([self.git, "-C", self.path, "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=600,
                    encoding="utf-8", errors="replace")
            ok = r.returncode == 0
            msg = ((r.stdout or "") + (r.stderr or "")).strip()[:400]
            return ok, (msg if ok else f"pull 失败（可稍后重试或手动 git pull）: {msg[:200]}")
        except Exception as e:
            return False, str(e)

    def list_versions(self, limit=50) -> list:
        """版本列表（tag=稳定版优先）：[{version, hash, date, kind}]"""
        if not self.is_git_repo:
            return []
        out = []
        try:
            # tag（稳定版）：名字|短hash|日期
            r = run([self.git, "-C", self.path, "for-each-ref",
                     "refs/tags", "--sort=-creatordate",
                     "--format=%(refname:short)|%(objectname:short)|%(creatordate:short)"],
                    capture_output=True, text=True, timeout=60,
                    encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines():
                parts = line.split("|")
                if len(parts) == 3:
                    out.append({"version": parts[0], "hash": parts[1],
                                "date": parts[2], "kind": "稳定版"})
        except Exception:
            pass
        try:
            # 最近提交（开发版）
            r = run([self.git, "-C", self.path, "log", f"-{limit}",
                     "--format=%h|%cd|%s", "--date=short"],
                    capture_output=True, text=True, timeout=60,
                    encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    tag_names = {v["hash"] for v in out}
                    if parts[0] not in tag_names:
                        out.append({"version": parts[2][:40] or parts[0],
                                    "hash": parts[0], "date": parts[1],
                                    "kind": "开发版"})
        except Exception:
            pass
        return out[:limit]


    def switch_to(self, tag: str) -> tuple:
        """检出指定版本（未提交改动会 stash 保存）"""
        if not self.is_git_repo:
            return False, "该实例不是 git 仓库（zip 安装），请重新以 git 方式安装"
        try:
            run([self.git, "-C", self.path, "stash"],
                           capture_output=True, timeout=60)
            r = run([self.git, "-C", self.path, "checkout", tag],
                               capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace")
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
class EnvBackup:
    """Python 环境（pip freeze）+ 插件清单快照的备份与还原"""

    def __init__(self, comfyui_path: str, python_path: str):
        self.comfyui_path = comfyui_path
        self.python_path = python_path
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        self.backup_dir = os.path.join(base, "ComfyHub", "backups",
                                       os.path.basename(comfyui_path.rstrip("\\/")))
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
        # 2) 插件清单
        nodes = inst_lib.list_custom_nodes(self.comfyui_path)
        json.dump([{"name": n, "git": g, "enabled": en} for n, g, en in nodes],
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
        self.nodes_dir = os.path.join(comfyui_path, "custom_nodes")
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        self.snap_dir = os.path.join(base, "ComfyHub", "rescue",
                                     os.path.basename(comfyui_path.rstrip("\\/")))
        os.makedirs(self.snap_dir, exist_ok=True)

    def snapshot(self, log=print) -> str:
        """记录当前插件状态（名称+启停+git地址），不动文件"""
        ts = time.strftime("%Y%m%d_%H%M%S")
        nodes = inst_lib.list_custom_nodes(self.nodes_dir)
        state = [{"name": n, "enabled": en} for n, _g, en in nodes]
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
        want = {s["name"]: s["enabled"] for s in state}
        now = inst_lib.list_custom_nodes(self.nodes_dir)
        actions = []
        # 1) 快照里没有的现插件 → 禁用（救援核心：新装插件导致崩溃时回滚）
        for name, _g, enabled in now:
            if name not in want and enabled:
                src = os.path.join(self.nodes_dir, name)
                dst = src + ".disabled"
                try:
                    os.rename(src, dst)
                    actions.append(f"禁用新增: {name}")
                except OSError as e:
                    actions.append(f"禁用失败 {name}: {e}")
        # 2) 恢复快照里的启停状态
        for name, enabled in want.items():
            src = os.path.join(self.nodes_dir, name)
            dis = src + ".disabled"
            if enabled and os.path.isdir(dis):
                try:
                    os.rename(dis, src)
                    actions.append(f"恢复启用: {name}")
                except OSError:
                    pass
            elif (not enabled) and os.path.isdir(src):
                try:
                    os.rename(src, dis)
                    actions.append(f"恢复禁用: {name}")
                except OSError:
                    pass
        msg = "；".join(actions) if actions else "状态一致，无需回滚"
        return True, msg
