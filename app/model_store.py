# -*- coding: utf-8 -*-
"""模型下载器后端：hf-mirror.com（HuggingFace 国内全量镜像，免登录）

功能：
  · search_models(keyword, limit, cursor)   搜索模型仓库（cursor 分页，返回 (rows, next_cursor)）
  · list_weight_files(repo_id)      列出仓库内的模型权重文件
  · download_file(...)              断点续传下载到实例 models 目录
                                    （repo_id 传完整 http(s) 直链时按 URL 直接下载）
  · derive_mirror_queries(...)      模型名/文件名 → HF 镜像多策略检索词
  · guess_category(filename)        按文件名预判 ComfyUI 模型分类

数据来源：hf-mirror.com 公开 API（与 HuggingFace API 同构）
  · 搜索:  /api/models?search=&limit=
  · 文件:  /api/models/{repo_id}/tree/main
  · 下载:  /{repo_id}/resolve/main/{file_path}
本模块只做索引与传输，模型版权归用户自行确认（见 docs/DISCLAIMER.txt）。
"""
import json
import os
import re
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
    return _get2(url, timeout)[0]


def _get2(url, timeout=30):
    """GET + 解析 JSON，同时返回响应头（Link header 里的分页 cursor 要用）"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ComfyHub"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "ignore")), dict(r.headers)


class SearchResult(list):
    """list 子类：兼容纯 list 用法（迭代/len/解包照旧），附带 next_cursor 分页游标。

    2026-10-31：搜索从「一次 20/30 条」升级为 cursor 分页（加载更多/滚动加载）。
    测试 stub 可继续返回纯 list——调用方 getattr(rows, "next_cursor", None)
    取不到游标即视为「没有下一页」，向后兼容。
    """

    def __init__(self, rows=(), next_cursor=None):
        super().__init__(rows)
        self.next_cursor = next_cursor


# -------------------------------------------------- 搜索
def search_models(keyword: str, limit=30, cursor: str = None) -> "SearchResult":
    """搜索模型仓库，返回 SearchResult([{id, name, downloads, likes, pipeline_tag}], next_cursor)。

    分页（2026-10-31）：hf-mirror 与 HuggingFace API 同构，用 Link header 的
    rel="next" 里 cursor= 传下一页游标（实测 page 参数不存在、cursor 可用）。
    cursor=None 取第一页；next_cursor=None 表示没有下一页。
    """
    kw = urllib.parse.quote(keyword.strip())
    url = f"{base_url()}/api/models?search={kw}&limit={limit}&sort=downloads&direction=-1"
    if cursor:
        url += f"&cursor={urllib.parse.quote(cursor)}"
    raw, headers = _get2(url)
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
    return SearchResult(out, _next_cursor_from_link(headers.get("Link") or ""))


_LINK_CURSOR_RE = re.compile(r"[?&]cursor=([^>&]+)[^>]*>;\s*rel=\"next\"")


def _next_cursor_from_link(link: str):
    """Link header → 下一页 cursor（URL 解码后返回；无 next 返回 None）"""
    m = _LINK_CURSOR_RE.search(link)
    return urllib.parse.unquote(m.group(1)) if m else None


# -------------------------------------------------- 文件树
WEIGHT_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")


def list_weight_files(repo_id: str) -> list:
    """列出仓库内的模型权重文件 [{path, size}]（跳过隐藏/非权重）。

    2026-10-31 修：加 ?recursive=true 递归子目录——diffusers 分目录仓库
    （unet/text_encoder/…）此前只扫根目录得到 0 文件，下载入口成了死胡同。
    """
    url = f"{base_url()}/api/models/{repo_id}/tree/main?recursive=true"
    tree = _get(url)
    out = []
    for f in tree:
        path = f.get("path") or ""
        if not path.lower().endswith(WEIGHT_EXT):
            continue
        if f.get("type") not in (None, "file"):
            continue
        if path.startswith(".") or "/." in path:
            continue
        out.append({"path": path, "size": int(f.get("size") or 0),
                    "category": guess_category(path)})
    out.sort(key=lambda x: -x["size"])
    return out


# -------------------------------------------------- 中文关键词 → 英文习惯词
# HuggingFace/CivitAI 仓库命名几乎全为英文，中文直搜 0 结果。
# 词级映射把常见中文习惯词换成英文检索词；未收录的中文原样保留（UI 提示人工换词）。
_CN_HINTS = {
    "写实": "realistic", "真实感": "photorealistic", "真人": "photorealistic",
    "二次元": "anime", "动漫": "anime", "日漫": "anime", "卡通": "cartoon",
    "国风": "chinese style", "古风": "chinese traditional", "水墨": "ink painting",
    "油画": "oil painting", "水彩": "watercolor", "像素": "pixel",
    "风景": "landscape", "场景": "background", "人像": "portrait",
    "线稿": "lineart", "上色": "colorize", "修复": "restore", "高清": "hd",
    "放大": "upscale", "超分": "upscale", "美少女": "girl", "少女": "girl",
    "男孩": "boy", "机甲": "mecha", "科幻": "sci-fi", "魔法": "magic",
    "盲盒": "blind box", "q版": "chibi", "3d": "3d", "游戏": "game",
}


def translate_keyword(kw: str) -> tuple:
    """中文关键词 → 英文习惯词（词级替换）。

    返回 (en_kw, notes)：notes 为 ["写实→realistic", ...] 供 UI 提示；
    未收录的中文原样保留。长词优先替换，避免子串截胡。"""
    notes = []
    out = kw
    for cn, en in sorted(_CN_HINTS.items(), key=lambda x: -len(x[0])):
        if cn in out:
            out = out.replace(cn, en + " ")
            notes.append(f"{cn}→{en}")
    return " ".join(out.split()), notes


# -------------------------------------------------- 同步仓库检索词推导
# 背景（2026-10-31）：CivitAI 文件名（controlnet11Models_tileE.safetensors）
# 直接拿去搜 HF 镜像命中 0——社区同步仓库名与 CivitAI 文件名不同。
# 实测有效路径：拆出实义词组再搜（"controlnet tile" 命中 8 个仓库）。
_STOPWORDS = {"models", "model", "safetensors", "ckpt", "pt", "pth", "bin",
              "the", "for", "with", "v1", "v2", "v3", "fp16", "fp32", "bf16",
              "full", "pruned", "ema", "vae", "sft"}


def derive_mirror_queries(model_name: str, file_names=None, limit=3) -> list:
    """模型名/文件名 → HF 镜像检索词列表（多策略，去重保序）。

    实测（2026-10-31）：CivitAI 文件名直搜 0 命中，拆实义词组后
    "controlnet tile" 命中 8 个社区同步仓库。词组策略以**文件名**为准
    （模型名 "ControlNet" 拆词会碎成 control+net，词组失配），故：
      ① 模型名原样（同名仓库直接命中时最快）
      ② 文件名实义词组（≤3 词，去停用词/数字/扩展名）——主力策略
      ③ 文件首实义词单兜底
    """
    out = []

    def _push(q):
        q = " ".join(q.split())
        if q and q.lower() not in [x.lower() for x in out]:
            out.append(q)

    def _tokens(text):
        # 去扩展名 → camelCase/分隔符/数字边界拆词 → 去停用词/数字/单字母
        text = re.sub(r"\.[a-z0-9]{2,5}$", "", text.strip(), flags=re.I)
        parts = re.split(r"[_\-\.\s]+|(?<=[a-z0-9])(?=[A-Z])", text)
        toks = []
        for p in parts:
            for t in re.findall(r"[A-Za-z]+|\d+", p):
                tl = t.lower()
                if tl in _STOPWORDS or len(tl) < 2 or tl.isdigit():
                    continue
                toks.append(tl)
        return toks

    if model_name:
        _push(model_name)
    ftoks = []
    for fn in file_names or []:
        ftoks.extend(_tokens(fn or ""))
    seen, uniq = set(), []
    for t in ftoks:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    if len(uniq) >= 2:
        _push(" ".join(uniq[:3]))          # 文件实义词组（如 "controlnet tile"）
    if uniq:
        _push(uniq[0])                     # 单实义词兜底
    return out[:limit]


# -------------------------------------------------- CivitAI（civitai.red 国内镜像）
# 元数据/搜索可用；直链下载 307→b2.civitai.com 被墙，文件走 HF 镜像同步仓库兜底
# 2026-10-31 实测：分页用 cursor=N（page 参数报 400，勿用）；API 文件对象带
# downloadUrl（civitai.com/api/download/...），供「复制直链」手动下载
CIVITAI = "https://civitai.red"


def search_civitai(keyword: str, limit=20, cursor: str = None) -> "SearchResult":
    """CivitAI 镜像搜索。返回 SearchResult([{id, name, type, downloads, likes}], next_cursor)

    分页：cursor 参数取 metadata.nextCursor（page 参数在 civitai.red 报 400）。
    """
    kw = urllib.parse.quote(keyword.strip())
    url = (f"{CIVITAI}/api/v1/models?query={kw}&limit={limit}"
           f"&sort=Most%20Downloaded")
    if cursor:
        url += f"&cursor={urllib.parse.quote(str(cursor))}"
    raw = _get(url)
    items = (raw.get("items") or []) if isinstance(raw, dict) else (raw or [])
    out = []
    for m in items:
        st = m.get("stats") or {}
        out.append({
            "id": str(m.get("id") or ""),
            "name": m.get("name") or "",
            "type": m.get("type") or "",
            "downloads": int(st.get("downloadCount") or 0),
            "likes": int(st.get("favoriteCount") or 0),
        })
    meta = (raw.get("metadata") or {}) if isinstance(raw, dict) else {}
    nxt = meta.get("nextCursor")
    return SearchResult(out, str(nxt) if nxt not in (None, "") else None)


def civitai_files(model_id: str) -> tuple:
    """CivitAI 模型最新版本的文件列表（元数据 + downloadUrl 直链）。
    返回 (files: [{name, size_mb, type, downloadUrl}], 模型名)"""
    data = _get(f"{CIVITAI}/api/v1/models/{model_id}")
    vers = data.get("modelVersions") or []
    if not vers:
        return [], data.get("name") or ""
    files = []
    for f in (vers[0].get("files") or []):
        files.append({
            "name": f.get("name") or "",
            "size_mb": (f.get("sizeKB") or 0) / 1024,
            "type": f.get("type") or "",
            "downloadUrl": f.get("downloadUrl") or "",
        })
    return files, data.get("name") or ""


def download_url(repo_id: str, file_path: str) -> str:
    return f"{base_url()}/{repo_id}/resolve/main/{file_path}"


# -------------------------------------------------- 下载（断点续传）
def download_file(repo_id: str, file_path: str, dest_dir: str,
                  progress_cb=None, cancel_flag=None) -> str:
    """下载单个权重文件到 dest_dir（断点续传）。返回目标文件完整路径。

    repo_id：HF 仓库 id（如 "a/b"）或完整 http(s) 直链（CivitAI downloadUrl 等）。
      传直链时按 URL 原样下载，file_path 仅用于取文件名。
    progress_cb(done_bytes, total_bytes)   每 512KB 回调一次
    cancel_flag: 可调用对象，返回 True 时中止（保留 .part 下次续传）
    """
    if repo_id.startswith(("http://", "https://")):
        url = repo_id                     # 直链直传（2026-10-31：CivitAI 文件行下载）
    else:
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
    # 2026-10-31 修：续传必须校验 206——服务器无视 Range 返 200 时整文件会在
    # .part 后面追加一遍（文件损坏翻倍）。收到 200 就截断重来。
    if done and resp.status != 206:
        done = 0
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
    # 2026-10-31 修：截断必须显式失败——连接中断时 done < total，此前照样
    # os.replace，残缺模型伪装完成（用户加载出图直接崩）。保留 .part 供续传。
    if total and done < total:
        raise IOError(f"下载中断：{done}/{total} 字节，已保留 .part 可续传")
    os.replace(part, dest)      # 原子改名
    return dest
