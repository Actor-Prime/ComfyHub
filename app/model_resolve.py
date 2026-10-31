# -*- coding: utf-8 -*-
"""模型文件 → 下载直链解析（2026-10-31 扫描器增强）

背景：工作流扫描出的模型文件此前一律标「需手动下载（尚无搜索源）」，
其实官方 repack 仓库（Comfy-Org 等）早就有直链——Qwen-Image-2.1 三件套
（int8 主干/文本编码器/VAE）就是典型案例。

三级解析（前两级离线/弱在线，快）：
  1) KNOWN_MODEL_FILES   精确文件名 → (repo, path) 已知表（官方 repack 布局）
  2) repack 仓库文件树扫描：列出仓库权重文件（带 24h 缓存）精确匹配文件名
  3) 都没有 → None（UI 层给搜索链接 + 可选 AI 接入）
命中后同时给出 hf-mirror 与 ModelScope（国内直连）两个下载源。
"""
import os
import time

from app import model_store

# 文件名 → (仓库 id, 仓库内路径)。来源：Comfy-Org 官方 repack 仓库布局
# （hf-mirror / ModelScope 同步），文件名与目录均照抄 ComfyUI 官方模板。
KNOWN_MODEL_FILES = {
    # —— Qwen-Image-2.1 三件套（INT8 模板默认） ——
    "qwen_image_2.1_int8_convrot.safetensors":
        ("Comfy-Org/Qwen-Image-2.1", "diffusion_models/qwen_image_2.1_int8_convrot.safetensors"),
    "qwen_image_2.1_bf16.safetensors":
        ("Comfy-Org/Qwen-Image-2.1", "diffusion_models/qwen_image_2.1_bf16.safetensors"),
    "qwen3vl_8b_int8_convrot.safetensors":
        ("Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_int8_convrot.safetensors"),
    "qwen3vl_8b_bf16.safetensors":
        ("Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_bf16.safetensors"),
    "qwen3vl_8b_w4a8.safetensors":
        ("Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_w4a8.safetensors"),
    "qwen_image_2.1_vae_bf16.safetensors":
        ("Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors"),
}

# repack 仓库扫描清单（命中已知表之外的文件名时，逐仓查文件树）
REPACK_REPOS = [
    "Comfy-Org/Qwen-Image-2.1",
]

# 仓库文件树缓存：{repo: (ts, [{path,size,category}])}，24h TTL
_TREE_CACHE = {}
_TREE_TTL = 24 * 3600

MODELScope = "https://modelscope.cn"


def _ms_url(repo_id: str, file_path: str) -> str:
    return f"{MODELScope}/models/{repo_id}/resolve/master/{file_path}"


def resolve_file(filename: str, online: bool = True, log=print) -> dict:
    """解析单个模型文件名 → 下载源。命中返回：
    {repo, path, subdir, hf_url, ms_url, size, source}
    未命中返回 None。online=False 时只查已知表（测试/快速路径）。
    """
    fname = os.path.basename(filename.replace("\\", "/").strip())
    if not fname:
        return None
    hit = KNOWN_MODEL_FILES.get(fname)
    source = "known"
    if not hit and online:
        hit = _scan_repack_trees(fname, log=log)
        source = "repack_tree"
    if not hit:
        return None
    repo, path = hit
    subdir = path.split("/")[0] if "/" in path else "checkpoints"
    return {
        "repo": repo,
        "path": path,
        "subdir": subdir,
        "hf_url": model_store.download_url(repo, path),
        "ms_url": _ms_url(repo, path),
        "size": _tree_size(repo, path),
        "source": source,
    }


def _scan_repack_trees(fname: str, log=print) -> tuple:
    """逐个 repack 仓库查文件树，精确匹配文件名 → (repo, path)"""
    for repo in REPACK_REPOS:
        try:
            tree = _repo_tree(repo, log=log)
        except Exception as e:
            log(f"[模型解析] 仓库 {repo} 列表失败: {e}")
            continue
        for f in tree:
            if os.path.basename(f["path"]) == fname:
                return (repo, f["path"])
    return None


def _repo_tree(repo: str, log=print) -> list:
    now = time.time()
    hit = _TREE_CACHE.get(repo)
    if hit and now - hit[0] < _TREE_TTL:
        return hit[1]
    tree = model_store.list_weight_files(repo)
    _TREE_CACHE[repo] = (now, tree)
    return tree


def _tree_size(repo: str, path: str) -> int:
    hit = _TREE_CACHE.get(repo)
    if hit:
        for f in hit[1]:
            if f["path"] == path:
                return int(f.get("size") or 0)
    return 0


# ---------------- 下载（带「有进展就重置」重试） ----------------
# hf-mirror 大文件每 256-500MB 掐一次 TLS（record layer failure），
# 固定重试次数会提前耗尽——必须按「下载字节比上次失败点有增长就重置计数」。
def download_with_retry(repo_id: str, file_path: str, dest_dir: str,
                        progress_cb=None, cancel_flag=None,
                        max_tries: int = 60, log=print) -> str:
    tries, prev_fail_size = 0, -1
    part = os.path.join(dest_dir, os.path.basename(file_path) + ".part")

    def _part_size():
        return os.path.getsize(part) if os.path.isfile(part) else 0

    while True:
        try:
            return model_store.download_file(
                repo_id, file_path, dest_dir,
                progress_cb=progress_cb, cancel_flag=cancel_flag)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            cur = _part_size()
            if cur > prev_fail_size:
                prev_fail_size = cur
                tries = 0               # 有进展就重置重试计数
            tries += 1
            log(f"[下载] 中断重试 {tries}: {e}（已收 {cur / 1e6:.0f}MB）")
            if cancel_flag is not None and cancel_flag():
                raise KeyboardInterrupt
            if tries >= max_tries:
                raise
