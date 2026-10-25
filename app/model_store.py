# -*- coding: utf-8 -*-
"""模型下载器后端：hf-mirror.com（HuggingFace 国内全量镜像，免登录）

功能：
  · search_models(keyword, limit)   搜索模型仓库
  · list_weight_files(repo_id)      列出仓库内的模型权重文件
  · download_file(...)              断点续传下载到实例 models 目录
  · guess_category(filename)        按文件名预判 ComfyUI 模型分类

数据来源：hf-mirror.com 公开 API（与 HuggingFace API 同构）
  · 搜索:  /api/models?search=&limit=
  · 文件:  /api/models/{repo_id}/tree/main
  · 下载:  /{repo_id}/resolve/main/{file_path}
本模块只做索引与传输，模型版权归用户自行确认（见 docs/DISCLAIMER.txt）。
"""
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

MIRROR = "https://hf-mirror.com"
HF_OFFICIAL = "https://huggingface.co"

# 数据源："mirror"（国内直连）| "hf"（HuggingFace 官方，需代理）
_source = "mirror"


def set_source(mode: str):
    global _source
    _source = "hf" if mode == "hf" else "mirror"


def base_url() -> str:
    return HF_OFFICIAL if _source == "hf" else MIRROR

# ComfyUI models 目录分类 + 文件名关键词预判
CATEGORIES = ["checkpoints", "loras", "vae", "upscale_models",
              "controlnet", "clip_vision", "embeddings", "unet"]

_GUESS_RULES = [
    ("vae", ["vae"]),
    ("loras", ["lora", "locon", "lokr", "lycoris"]),
    ("upscale_models", ["upscaler", "esrgan", "swinir", "ultrasharp", "4x-", "2x-"]),
    ("controlnet", ["controlnet", "t2i-adapter", "t2i"]),
    ("clip_vision", ["clip_vision", "clipvision"]),
    ("unet", ["unet", "flux1-", "diffusion_pytorch_model"]),
    ("checkpoints", ["checkpoint", "xl_base", "sd_xl", "sd15", "sd_1.5", "base-1.0",
                     "inpainting", "turbo", "realistic", "anime"]),
]


def guess_category(filename: str) -> str:
    low = filename.lower()
    for cat, kws in _GUESS_RULES:
        if any(k in low for k in kws):
            return cat
    return "checkpoints"


def _get(url, timeout=30):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ComfyHub"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# -------------------------------------------------- 搜索
def search_models(keyword: str, limit=30) -> list:
    """搜索模型仓库，返回 [{id, name, downloads, likes, pipeline_tag}]"""
    kw = urllib.parse.quote(keyword.strip())
    url = f"{base_url()}/api/models?search={kw}&limit={limit}&sort=downloads&direction=-1"
    raw = _get(url)
    out = []
    for m in raw:
        mid = m.get("id") or m.get("modelId") or ""
        out.append({
            "id": mid,
            "name": mid.split("/")[-1] if "/" in mid else mid,
            "author": mid.split("/")[0] if "/" in mid else "",
            "downloads": int(m.get("downloads") or 0),
            "likes": int(m.get("likes") or 0),
            "pipeline": m.get("pipeline_tag") or "",
        })
    return out


# -------------------------------------------------- 文件树
WEIGHT_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")


def list_weight_files(repo_id: str) -> list:
    """列出仓库内的模型权重文件 [{path, size}]（跳过隐藏/非权重）"""
    url = f"{base_url()}/api/models/{repo_id}/tree/main"
    tree = _get(url)
    out = []
    for f in tree:
        path = f.get("path") or ""
        if not path.lower().endswith(WEIGHT_EXT):
            continue
        if path.startswith(".") or "/." in path:
            continue
        out.append({"path": path, "size": int(f.get("size") or 0),
                    "category": guess_category(path)})
    out.sort(key=lambda x: -x["size"])
    return out


def download_url(repo_id: str, file_path: str) -> str:
    return f"{base_url()}/{repo_id}/resolve/main/{file_path}"


# -------------------------------------------------- 下载（断点续传）
def download_file(repo_id: str, file_path: str, dest_dir: str,
                  progress_cb=None, cancel_flag=None) -> str:
    """下载单个权重文件到 dest_dir（断点续传）。返回目标文件完整路径。

    progress_cb(done_bytes, total_bytes)   每 512KB 回调一次
    cancel_flag: 可调用对象，返回 True 时中止（保留 .part 下次续传）
    """
    url = download_url(repo_id, file_path)
    os.makedirs(dest_dir, exist_ok=True)
    fname = os.path.basename(file_path.replace("\\", "/"))
    dest = os.path.join(dest_dir, fname)
    part = dest + ".part"

    tmp_headers = {"User-Agent": "Mozilla/5.0 ComfyHub"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 已有 .part → 续传
    done = os.path.getsize(part) if os.path.isfile(part) else 0
    req = urllib.request.Request(url, headers=dict(tmp_headers, **({"Range": f"bytes={done}-"} if done else {})))
    resp = urllib.request.urlopen(req, timeout=60, context=ctx)
    total = done + int(resp.headers.get("Content-Length") or 0)
    mode = "ab" if done else "wb"

    last_cb = 0
    with open(part, mode) as f:
        while True:
            if cancel_flag is not None and cancel_flag():
                f.close()
                raise KeyboardInterrupt
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb and done - last_cb >= 512 * 1024:
                progress_cb(done, total)
                last_cb = done
    if progress_cb:
        progress_cb(done, total)
    os.replace(part, dest)      # 原子改名
    return dest
