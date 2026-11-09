# -*- coding: utf-8 -*-
"""list_weight_files 递归扫描回归（2026-10-31 修：diffusers 分目录仓库 0 文件死胡同）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import model_store as ms

TREE = [
    {"type": "directory", "path": "unet"},
    {"type": "directory", "path": "text_encoder"},
    {"type": "file", "path": "README.md", "size": 100},
    {"type": "file", "path": "unet/diffusion_pytorch_model.safetensors", "size": 3000},
    {"type": "file", "path": "text_encoder/model.fp16.safetensors", "size": 500},
    {"type": "file", "path": "DreamShaper_8_pruned.safetensors", "size": 2100},
    {"type": "file", "path": ".hidden/model.safetensors", "size": 1},   # 隐藏目录跳过
    {"type": "file", "path": "notes.txt", "size": 2},                    # 非权重跳过
]

_orig = ms._get
ms._get = lambda url, timeout=30: TREE
try:
    files = ms.list_weight_files("x/y")
finally:
    ms._get = _orig

paths = {f["path"] for f in files}
assert paths == {"unet/diffusion_pytorch_model.safetensors",
                 "text_encoder/model.fp16.safetensors",
                 "DreamShaper_8_pruned.safetensors"}, f"筛选结果不对: {paths}"

# 按大小降序
assert [f["size"] for f in files] == sorted((f["size"] for f in files), reverse=True)

# 分类按全路径猜：unet/… 必须落 unet 类，根目录单文件落 checkpoints
by_path = {f["path"]: f for f in files}
assert by_path["unet/diffusion_pytorch_model.safetensors"]["category"] == "unet", \
    by_path["unet/diffusion_pytorch_model.safetensors"]["category"]
assert by_path["DreamShaper_8_pruned.safetensors"]["category"] == "checkpoints"

# 根目录递归请求必须带 recursive=true（防止回退成只扫根）
seen = {}
def _spy(url, timeout=30):
    seen["url"] = url
    return []
ms._get = _spy
try:
    ms.list_weight_files("x/y")
finally:
    ms._get = _orig
assert "recursive=true" in seen["url"], f"URL 未递归: {seen['url']}"

print("test_model_tree: OK")
