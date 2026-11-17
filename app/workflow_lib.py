# -*- coding: utf-8 -*-
"""本地工作流库：索引 / 槽位识别 / 补丁改写（2026-11-15）

为什么不让模型直接生成工作流？
    实测 2B 级模型必然会编节点名和字段（KSampler 少了 model/positive/negative、
    把 cfg 写成 cfg_scale）。ComfyUI 的图结构是"精确 schema"任务，小模型没有
    这类记忆。所以设计成：**模型只改值，不改结构**。

本模块提供三件事：
    scan()        扫描 user/default/workflows，产出模板清单（含可编辑槽位）
    search()      关键词检索（名称 / 节点类型 / 现有提示词 / 标签）
    apply_edits() 把 {"prompt": "...", "width": 832} 写回原 JSON 的对应槽位

安全底线：apply_edits 前后节点 id、class_type、links 必须完全一致，
否则直接抛错——结构一旦被动过，后面所有诊断都失去意义。
"""
import json
import os
import re

# class_type → {字段名: widgets_values 下标}
# 只收录「参数位置稳定」的常见节点；不确定的一律不碰（宁可少改，不可改坏）
_SLOT_MAP = {
    "CLIPTextEncode": {"text": 0},
    "EmptyLatentImage": {"width": 0, "height": 1, "batch_size": 2},
    "EmptySD3LatentImage": {"width": 0, "height": 1, "batch_size": 2},
    "KSampler": {"seed": 0, "steps": 2, "cfg": 3, "sampler_name": 4,
                 "scheduler": 5, "denoise": 6},
    "LoadImage": {"image": 0},
    "SaveImage": {"filename_prefix": 0},
    "CheckpointLoaderSimple": {"ckpt_name": 0},
    "UNETLoader": {"unet_name": 0},
    "CLIPLoader": {"clip_name": 0},
    "DualCLIPLoader": {"clip_name1": 0, "clip_name2": 1},
    "VAELoader": {"vae_name": 0},
    "LoraLoader": {"lora_name": 0, "strength_model": 1, "strength_clip": 2},
    "LoraLoaderModelOnly": {"lora_name": 0, "strength_model": 1},
    # 把提示词直接吃进自己 widget 的合成节点（2026-11-15）：
    # 官方 MiniMax H3 模板的提示词就在 MiniMaxH3ImageToVideo.widgets_values[0]，
    # 不在任何 CLIPTextEncode 里。只认 CLIPTextEncode 会让这些模板显得"没有可改项"。
    "MiniMaxH3ImageToVideo": {"prompt": 0},
    "MiniMaxH3ReferenceToVideo": {"prompt": 0},
    "MiniMaxH3TextEncode": {"prompt": 0},
    "WanImageToVideo": {"prompt": 0},
}

# 兜底猜提示词时排除的节点（说明文本/基础值/存取类，widgets 里的长字符串都不是提示词）
_NO_PROMPT_TYPES = ("Note", "Primitive", "Reroute", "Save", "Load", "Preview",
                    "ShowText", "String", "Switch", "MathExpression",
                    "ResolutionSelector", "CreateVideo")

# 文本类节点：不写死名单，按类名规则识别（各家 TextEncode 变体太多）
_TEXT_TYPES = ("CLIPTextEncode", "TextEncode", "CLIPTextEncodeSDXL",
               "TextEncodeQwenImage21", "TextEncodeQwenImageEdit",
               "T5TextEncode", "PromptNode")

# 负向提示词的识别特征（标题优先，其次看内容开头）
_NEG_HINTS = ("negative", "neg", "负向", "负面", "反向", "反向提示")

_INT_FIELDS = {"width", "height", "batch_size", "seed", "steps"}
_FLOAT_FIELDS = {"cfg", "denoise", "strength_model", "strength_clip"}

_META_SUFFIX = ".meta.json"

# 检索同义词：用户口语 → 工作流里真实的用词（工作流叫"面部更换"，用户说"换脸"）
_SYNONYMS = {
    "换脸": ["面部", "人脸", "脸部", "face", "替换"],
    "换人": ["面部", "替换", "face"],
    "视频": ["video", "t2v", "i2v"],
    "放大": ["upscale", "超分", "高清"],
    "文生图": ["t2i", "text2img", "生图", "EmptyLatent"],
    "图生图": ["i2i", "img2img", "重绘", "VAEEncode"],
    "修复": ["修复", "修补", "inpaint", "重绘"],
    "三视图": ["三视图", "角色设定", "参考图"],
    "脸": ["面部", "脸部", "face"],
    # 视频类：官方模板用 t2v/i2v/fl2v 这类缩写，用户一律说中文
    "文生视频": ["t2v", "text to video", "文生视频"],
    "图生视频": ["i2v", "image to video", "图生视频"],
    "首尾帧": ["fl2v", "fl2va", "first last frame"],
    "参考图视频": ["ref2v", "ref2va", "reference to video"],
    "数字人": ["avatar", "audio", "口播"],
    "音频": ["audio", "music", "声音"],
    # 本项目的 H3 指的是 MiniMax H3（有内置组件包）；「h3」单独查时
    # 会有别的产品（Pika H3）同名，补 minimax 让 MiniMax 模板排前面
    "h3": ["minimax"],
}


# ---------------------------------------------------------------- 模型清单
# 加载器节点 → 模型目录（ComfyUI 的 models 子目录名）
_LOADER_DIRS = {
    "CheckpointLoaderSimple": "checkpoints",
    "CheckpointLoader": "checkpoints",
    "unCLIPCheckpointLoader": "checkpoints",
    "UNETLoader": "diffusion_models",
    "UnetLoaderGGUF": "diffusion_models",
    "UnetLoaderGGUFAdvanced": "diffusion_models",
    "CLIPLoader": "text_encoders",
    "DualCLIPLoader": "text_encoders",
    "TripleCLIPLoader": "text_encoders",
    "QuadrupleCLIPLoader": "text_encoders",
    "CLIPVisionLoader": "clip_vision",
    "VAELoader": "vae",
    "LoraLoader": "loras",
    "LoraLoaderModelOnly": "loras",
    "ControlNetLoader": "controlnet",
    "UpscaleModelLoader": "upscale_models",
    "StyleModelLoader": "style_models",
    "GLIGENLoader": "gligen",
    "ModelPatchLoader": "model_patches",
    "DiffusersLoader": "diffusers",
}

_MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".sft", ".gguf", ".bin")


def models_of(wf: dict) -> dict:
    """提取工作流引用的模型：{目录: [文件名]}

    比 workflow_scan.extract_models 多两件事（官方模板必需的）：
      1) 穿透 definitions.subgraphs —— 官方模板大量用子图封装加载器
      2) 按 class_type 反查目录（text_encoders / diffusion_models 分得很细，
         只认后缀会全堆进「其他」，缺模型时给不出「放哪个目录」）
    """
    out = {}

    def _add(kind, val):
        val = val.strip()
        if 0 < len(val) < 200 and val.lower().endswith(_MODEL_EXT):
            out.setdefault(kind, set()).add(val)

    for _scope, n in _iter_nodes(wf):
        kind = _LOADER_DIRS.get(n.get("type") or "") or "其他"
        wv = n.get("widgets_values")
        vals = []
        if isinstance(wv, list):
            vals = [v for v in wv if isinstance(v, str)]
        elif isinstance(wv, dict):
            vals = [v for v in wv.values() if isinstance(v, str)]
        for v in vals:
            _add(kind, v)
    # 「其他」里剔除已经归到具体目录的同一个文件——否则缺模型提示会重复列两遍
    known = set()
    for k, vs in out.items():
        if k != "其他":
            known |= {v.lower() for v in vs}
    if "其他" in out:
        rest = {v for v in out["其他"] if v.lower() not in known}
        if rest:
            out["其他"] = rest
        else:
            out.pop("其他")
    return {k: sorted(v) for k, v in sorted(out.items())}


_INSTALLED_CACHE = {}      # (comfyui, models_dir) -> (ts, {小写文件名: {目录}})
_INSTALLED_TTL_S = 60.0


def installed_models(comfyui_path: str, models_dir: str = "") -> dict:
    """实例当前能找到的全部模型文件：{小写文件名: {所在目录}}

    带上 extra_model_paths 挂载的外部库（用户的模型常在 D 盘，不扫就会
    把装好的模型全报成缺）。60 秒缓存——几千个文件 os.walk 一次约 0.1~0.3s。
    """
    import time as _t
    key = (comfyui_path or "", models_dir or "")
    got = _INSTALLED_CACHE.get(key)
    if got and _t.time() - got[0] < _INSTALLED_TTL_S:
        return got[1]
    names = {}
    try:
        from app import model_paths as mp
        roots = [mp.model_root(comfyui_path, models_dir or "")]
        try:
            roots += mp.extra_roots(comfyui_path)
        except Exception:
            pass
    except Exception:
        roots = [os.path.join(comfyui_path or "", "models")]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dns, fns in os.walk(root):
            for fn in fns:
                low = fn.lower()
                if low.endswith(_MODEL_EXT):
                    names.setdefault(low, set()).add(dirpath)
    _INSTALLED_CACHE[key] = (_t.time(), names)
    return names


def missing_models(tpl: dict, installed: dict) -> list:
    """对比已装模型，返回 [{file, dir, ok}]（ok=False 就是缺的）"""
    out = []
    for kind, files in (tpl.get("models") or {}).items():
        for f in files:
            out.append({"file": f, "dir": kind,
                        "ok": f.lower() in installed})
    return out


def model_note(tpl: dict, miss: list) -> str:
    """缺模型提示（对齐 ComfyUI 官方「Missing Models」的行为：文件 + 目标目录）"""
    lack = [m for m in miss if not m["ok"]]
    if not miss:
        return "该模板没有可识别的模型加载节点（可能全是内置节点或 API 节点）"
    if not lack:
        return "所需模型已全部就位（%d 个）" % len(miss)
    lines = []
    for m in lack:
        where = ("models/" + m["dir"]) if m["dir"] != "其他" else "按节点提示选择目录"
        lines.append("· %s → %s" % (m["file"], where))
    return ("缺 %d/%d 个模型，先在「模型库」搜索下载，或把文件放到对应目录后重试：\n"
            % (len(lack), len(miss)) + "\n".join(lines))


def auto_tags(tpl: dict) -> list:
    """从节点构成推导用途标签（不用人工标注也能让检索变准）"""
    ct = " ".join(tpl.get("class_types") or []).lower()
    name = (tpl.get("name") or "").lower()
    roles = {s["role"] for s in tpl.get("slots") or []}
    loads = sum(1 for s in tpl.get("slots") or [] if s["field"] == "image")
    tags = []
    lat = "emptylatent" in ct or "emptysd3" in ct
    if lat and "ksampler" in ct:
        tags.append("文生图" if loads == 0 else "图生图")
    if loads >= 2:
        tags.append("多参考图")
    if "vaeencode" in ct:
        tags.append("重绘")
    if any(k in ct for k in ("wan", "ltx", "hunyuanvideo", "svd",
                             "emptyhunyuanlatentvideo")):
        tags.append("视频")
    if "upscale" in ct:
        tags.append("放大")
    if "gguf" in name or "gguf" in ct:
        tags.append("GGUF")
    for kw, tag in (("三视图", "三视图"), ("面部", "面部修复"),
                    ("人脸", "面部修复"), ("合并", "多图合并")):
        if kw in name:
            tags.append(tag)
    if "denoise" in roles:
        tags.append("可控重绘")
    return tags


def expand_query(query: str) -> str:
    """把口语词扩成同义词串，提高命中率（检索是关键词的，不做这一步会漏）"""
    q = query or ""
    extra = []
    for word, alts in _SYNONYMS.items():
        if word in q:
            extra.extend(alts)
    return q + " " + " ".join(extra) if extra else q


# ---------------------------------------------------------------- 官方模板
# 文件名 slug → 人话标签（官方模板没有描述字段，只能从名字和节点构成推）
_SLUG_WORDS = {
    "t2v": "文生视频", "i2v": "图生视频", "t2i": "文生图", "i2i": "图生图",
    "v2v": "视频转视频", "fl2v": "首尾帧", "fl2va": "首尾帧", "ref2v": "参考图视频",
    "ref2va": "参考图视频", "vid2vid": "视频转视频", "upscale": "放大",
    "inpaint": "修复", "outpaint": "扩图", "edit": "编辑", "audio": "音频",
    "video": "视频", "image": "图像", "3d": "3D", "avatar": "数字人",
    "character": "角色", "face": "面部", "controlnet": "ControlNet",
    "lora": "LoRA", "hunyuan": "Hunyuan", "wan": "Wan", "flux": "Flux",
    "qwen": "Qwen", "ltx": "LTX", "sdxl": "SDXL", "minimax": "MiniMax",
    "h3": "MiniMax H3", "sdxl_refiner": "SDXL Refiner", "turbo": "Turbo",
    "lightning": "Lightning", "gguf": "GGUF", "api": "API 节点",
    "depth": "深度", "canny": "Canny", "pose": "姿态",
}

_SLUG_SPLIT = re.compile(r"[_\-\s\.]+")


def _slug_tags(stem: str) -> list:
    toks = set(_SLUG_SPLIT.split((stem or "").lower()))
    return [v for k, v in _SLUG_WORDS.items() if k in toks]


# 品牌名与常见缩写的固定大小写（自动首字母大写会把 MiniMax 变成 Minimax）
_BRAND_CASE = {
    "minimax": "MiniMax", "hunyuan": "Hunyuan", "qwen": "Qwen", "wan": "Wan",
    "ltx": "LTX", "flux": "Flux", "sdxl": "SDXL", "sd3": "SD3", "gguf": "GGUF",
    "api": "API", "vae": "VAE", "lora": "LoRA", "h3": "H3", "3d": "3D",
    "t2v": "T2V", "i2v": "I2V", "t2i": "T2I", "i2i": "I2I", "v2v": "V2V",
    "fl2v": "FL2V", "fl2va": "FL2VA", "ref2v": "REF2V", "ref2va": "REF2VA",
}


# 介词/连词保持小写（否则 image_to_model 会变成 Image TO Model）
_MINOR_WORDS = {"to", "of", "and", "for", "in", "on", "by", "at", "the", "a", "an"}


def _pretty_title(stem: str) -> str:
    """3d_hunyuan3d_image_to_model → 3D Hunyuan3d Image to Model"""
    words = []
    for w in _SLUG_SPLIT.split(stem or ""):
        if not w:
            continue
        low = w.lower()
        if low in _MINOR_WORDS and words:
            words.append(low)
        elif low in _BRAND_CASE:
            words.append(_BRAND_CASE[low])
        elif w.isupper():
            words.append(w)
        elif len(w) <= 3:
            words.append(w.upper())
        else:
            words.append(w[:1].upper() + w[1:])
    return " ".join(words) or (stem or "")


def _site_packages_dirs(comfyui_path: str, python_path: str = "") -> list:
    """列出可能装着 comfyui_workflow_templates* 的 site-packages

    顺序有意为之：实例自己的解释器排在前面。同一个模板包可能同时装在
    实例的 python 和 ComfyHub 的共享便携 python 里（实测这台机器就是两份），
    按包名去重时保留实例那份，版本才对得上实例实际跑的前端。
    """
    roots = []
    if comfyui_path:
        up = os.path.dirname(os.path.abspath(comfyui_path))
        roots += [os.path.join(up, "python"),          # 秋叶/整合包：ComfyUI/../python
                  os.path.join(up, "python_embeded"),  # 官方便携版
                  os.path.join(comfyui_path, "venv"),
                  os.path.join(up, "venv"),
                  os.path.join(up, ".venv")]
    if python_path:
        roots.append(os.path.dirname(os.path.abspath(python_path)))
    out = []
    for r in roots:
        for sub in ("Lib/site-packages", "lib/site-packages"):
            p = os.path.join(r, sub)
            key = os.path.normcase(os.path.realpath(p))
            if os.path.isdir(p) and key not in {os.path.normcase(os.path.realpath(x))
                                                for x in out}:
                out.append(p)
    return out


def official_roots(comfyui_path: str, python_path: str = "") -> list:
    """官方模板来源：[(目录, 类型)]，类型 = official(pip 模板) / blueprint(内核子图)

    按 realpath 去重：秋叶整合包的 <ComfyUI>/../python 与解释器路径推导出的
    site-packages 通常指向同一处，不去重会把 668 个模板扫成 4 份。
    """
    dirs, seen = [], set()

    def _add(p, kind):
        try:
            rp = os.path.normcase(os.path.realpath(p))
        except OSError:
            rp = os.path.normcase(p)
        if rp in seen or not os.path.isdir(p):
            return
        seen.add(rp)
        dirs.append((p, kind))

    if comfyui_path:
        _add(os.path.join(comfyui_path, "blueprints"), "blueprint")
    seen_pkg = set()          # 同一个模板包只认先找到的那份（见 _site_packages_dirs）
    for sp in _site_packages_dirs(comfyui_path, python_path):
        try:
            names = os.listdir(sp)
        except OSError:
            continue
        for nm in names:
            if not nm.startswith("comfyui_workflow_templates") or ".dist-info" in nm:
                continue
            if nm in seen_pkg:
                continue
            seen_pkg.add(nm)
            _add(os.path.join(sp, nm, "templates"), "official")
    return dirs


def find_template(name: str, comfyui_path: str, python_path: str = "",
                  extra_dirs: list = None) -> dict:
    """按文件名定位模板：本地工作流优先，其次官方/blueprint

    返回 {"path", "source"}；找不到返回 {}。
    为什么本地优先：用户把自己的同名工作流放进去就是想覆盖官方那份。
    """
    name = os.path.basename(name or "")
    if not name:
        return {}
    for d in ([os.path.join(comfyui_path, "user", "default", "workflows")]
              + list(extra_dirs or [])):
        p = os.path.join(d, name) if d else ""
        if p and os.path.isfile(p):
            return {"path": p, "source": "local"}
    for d, kind in official_roots(comfyui_path, python_path):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return {"path": p, "source": kind}
    return {}


# ---------------------------------------------------------------- 槽位
def _is_text_type(ctype: str) -> bool:
    return any(t in (ctype or "") for t in _TEXT_TYPES)


def _role_of_text(node: dict) -> str:
    """文本槽位的角色：prompt / negative"""
    title = str(node.get("title") or "").lower()
    if any(h in title for h in _NEG_HINTS):
        return "negative"
    wv = node.get("widgets_values") or []
    head = str(wv[0] if wv else "")[:40].lower()
    if any(h in head for h in ("worst quality", "low quality", "blurry",
                               "模糊", "低质量", "最差", "崩坏")):
        return "negative"
    return "prompt"


def _looks_like_prompt(v) -> bool:
    """widgets 里的字符串是不是提示词（长、不是文件名/路径/URL/纯数字）"""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if len(s) < 24:
        return False
    low = s.lower()
    if low.endswith(_MODEL_EXT) or s.startswith(("http://", "https://")):
        return False
    if s.count("\n") >= 3 and (s.startswith("#") or "|" in s[:80]):
        return False               # Markdown 说明/尺寸表
    return any(c in s for c in (" ", "，", "。", ","))


def _guess_prompt_slot(wf: dict):
    """工作流里没有任何已知文本节点时，从 widgets 里挑最像提示词的那个

    官方模板经常把提示词塞进合成节点自己的 widget（H3 就是），只靠白名单
    会漏。这里做一次保守兜底：只挑「最长的、像自然语言」的那一个，并标低置信。
    """
    best = None
    for scope, n in _iter_nodes(wf):
        ctype = n.get("type") or ""
        if any(t in ctype for t in _NO_PROMPT_TYPES):
            continue
        wv = n.get("widgets_values")
        if not isinstance(wv, list):
            continue
        for i, v in enumerate(wv):
            if _looks_like_prompt(v) and (best is None or len(v) > len(best[1][2])):
                best = (n, (scope, i, v))
    if not best:
        return None
    n, (scope, i, v) = best
    return {"node": n.get("id"), "scope": scope,
            "class_type": n.get("type"), "title": n.get("title") or "",
            "field": "prompt", "index": i, "role": "prompt",
            "value": v, "guessed": True}


def node_slots(node: dict) -> list:
    """单个节点可编辑的槽位 [{"field","index","role","value"}]"""
    ctype = node.get("type") or ""
    wv = node.get("widgets_values")
    if not isinstance(wv, list) or not wv:
        return []
    out = []
    if _is_text_type(ctype):
        out.append({"field": "text", "index": 0, "role": _role_of_text(node),
                    "value": wv[0]})
    for field, idx in (_SLOT_MAP.get(ctype) or {}).items():
        if idx < len(wv):
            out.append({"field": field, "index": idx, "role": field,
                        "value": wv[idx]})
    return out


# ---------------------------------------------------------------- 扫描
def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _models_of(wf: dict) -> dict:
    try:
        from app.workflow_scan import extract_models
        return extract_models(wf)
    except Exception:
        return {}


def _class_types(wf: dict) -> list:
    try:
        from app.workflow_scan import extract_class_types
        return extract_class_types(wf)
    except Exception:
        return sorted({n.get("type") for n in wf.get("nodes") or []
                       if n.get("type")})


def _meta_of(path: str) -> dict:
    """模板 sidecar：<workflow>.meta.json（可选，手写标签）"""
    p = path[:-5] + _META_SUFFIX if path.lower().endswith(".json") else ""
    if p and os.path.isfile(p):
        try:
            return _load(p)
        except Exception:
            return {}
    return {}


def _iter_nodes(wf: dict):
    """遍历全部节点，产出 (scope, node)。scope="" 是主图，否则是子图 id。

    2026-11-15：用户的工作流大量用「子图」封装（1009/1013/1015 都是），
    参数其实躺在 definitions.subgraphs[*].nodes 里。不进子图扫，
    这些模板会变成"一个可改项都没有"。
    """
    for n in wf.get("nodes") or []:
        yield "", n
    for sg in ((wf.get("definitions") or {}).get("subgraphs") or []):
        sid = str(sg.get("id") or "")
        for n in sg.get("nodes") or []:
            yield sid, n


def describe(path: str, source: str = "local", kind: str = "") -> dict:
    """把一份工作流描述成模板（失败返回 None）

    source: local=用户自己的工作流 / official=pip 里的官方模板 / blueprint=内核子图
    """
    try:
        wf = _load(path)
    except Exception:
        return None
    if not isinstance(wf, dict):
        return None              # 少数官方模板是数组/索引文件，不是工作流
    nodes = wf.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None              # 纯 API 格式没有 nodes，先不收
    slots = []
    for scope, n in _iter_nodes(wf):
        for s in node_slots(n):
            slots.append({"node": n.get("id"), "scope": scope,
                          "class_type": n.get("type"),
                          "title": n.get("title") or "", **s})
    # 一个文本节点都没认出来时兜底挑一个（官方合成节点常把提示词塞自己 widget）
    if not any(s["role"] == "prompt" for s in slots):
        g = _guess_prompt_slot(wf)
        if g:
            slots.append(g)
    meta = _meta_of(path)
    prompts = [str(s["value"])[:300] for s in slots if s["role"] == "prompt"]
    stem = os.path.splitext(os.path.basename(path))[0]
    tpl = {
        "name": os.path.basename(path),
        "path": path,
        "source": source,
        "kind": kind,
        "title": meta.get("title") or stem,
        "tags": list(meta.get("tags") or []),
        "note": meta.get("note") or "",
        "class_types": _class_types(wf),
        "models": models_of(wf),
        "slots": slots,
        "prompt_text": prompts,
        "node_count": len(nodes),
        "mtime": os.path.getmtime(path),
    }
    if source != "local":
        # 官方模板文件名是英文 slug，用人话标题 + 文件名双份喂检索
        tpl["title"] = _pretty_title(stem)
        tpl["tags"] = list(dict.fromkeys(tpl["tags"] + _slug_tags(stem)))
    # 人工标签 + 自动推导标签（工作流名字多是 1010/1011 这种编号，
    # 不靠节点构成推用途，检索基本只能瞎猜）
    tpl["tags"] = list(dict.fromkeys(tpl["tags"] + auto_tags(tpl)))
    return tpl


_SCAN_CACHE = {}          # (目录签名) -> (ts, [tpl])
_SCAN_TTL_S = 60.0


def _dir_sig(dirs: list) -> tuple:
    sig = []
    for d, _kind in dirs:
        try:
            sig.append((d, os.stat(d).st_mtime_ns))
        except OSError:
            sig.append((d, 0))
    return tuple(sig)


def scan(comfyui_path: str, extra_dirs: list = None,
         include_official: bool = False, python_path: str = "",
         use_cache: bool = True) -> list:
    """扫描工作流目录，返回模板描述列表（本地优先，组内按修改时间倒序）

    include_official=True 时把官方模板一并收进来（pip 的
    comfyui_workflow_templates_*/templates 约 668 个 + ComfyUI/blueprints 约 117 个）。
    实测全量 describe 784 个约 0.5s，加 60 秒缓存后检索不卡。
    """
    import time as _t
    dirs = []
    if comfyui_path:
        dirs.append((os.path.join(comfyui_path, "user", "default", "workflows"),
                     "local"))
    dirs += [(d, "local") for d in (extra_dirs or []) if d]
    if include_official:
        dirs += official_roots(comfyui_path, python_path)
    seen, uniq = set(), []
    for d, kind in dirs:
        if d and d not in seen:
            seen.add(d)
            uniq.append((d, kind))

    sig = _dir_sig(uniq)
    key = (sig, bool(include_official))
    got = _SCAN_CACHE.get(key)
    if use_cache and got and _t.time() - got[0] < _SCAN_TTL_S:
        return list(got[1])

    out = []
    for d, kind in uniq:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".json") or fn.endswith(_META_SUFFIX):
                continue
            t = describe(os.path.join(d, fn), source=kind)
            if t:
                out.append(t)
    out.sort(key=lambda t: (t.get("source") != "local", -t["mtime"]))
    _SCAN_CACHE[key] = (_t.time(), out)
    return list(out)


# ---------------------------------------------------------------- 检索
def _tokens(text: str) -> set:
    """中文按 2-gram + 英文按词切；用于粗糙但够用的相关性打分"""
    text = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fa5]+", text)
    grams = set()
    for run in cjk:
        for i in range(len(run) - 1):
            grams.add(run[i:i + 2])
        if len(run) == 1:
            grams.add(run)
    return words | grams


def _phrases(q: str) -> list:
    """整词片段：中文连续串 + 短英文词（h3 / t2v 这类被 2-gram 稀释的关键词）"""
    out = re.findall(r"[\u4e00-\u9fa5]{2,}", q or "")
    out += [w for w in re.findall(r"[a-z0-9_]{2,8}", (q or "").lower())]
    return out


def score(tpl: dict, query: str) -> float:
    """相关性打分。

    权重设计有教训（2026-11-15）：「h3 文生视频」最初把 WAN 的文生视频模板
    排到了 MiniMax H3 前面——因为「文生视频」三个 2-gram 各命中一次，把
    只有 1 个 h3 词元的正确模板压了下去。所以：
      · 模板名/标题里的词（产品名、模型名）权重最高
      · 用途标签次之，节点类型再次，正文最低
      · 再加「整词命中」奖励，让「文生视频」按一个词算而不是三个碎片
    """
    q = _tokens(query)
    if not q:
        return 0.0
    ident = ((tpl.get("name") or "") + " " + (tpl.get("title") or "")).lower()
    tag_txt = " ".join(tpl.get("tags") or []).lower()
    node_txt = " ".join(tpl.get("class_types") or []).lower()
    body = (" ".join(tpl.get("prompt_text") or []) + " "
            + (tpl.get("note") or "")).lower()
    s = (3.0 * len(q & _tokens(ident)) + 1.5 * len(q & _tokens(tag_txt))
         + 1.5 * len(q & _tokens(node_txt)) + 1.0 * len(q & _tokens(body)))
    for ph in _phrases(query):
        if ph in ident:
            s += 4.0
        elif ph in tag_txt:
            s += 3.0
    if (tpl.get("name") or "").startswith("api_"):
        s -= 3.0          # 需要云端 Key 的 API 模板，本地跑不了，别抢头名
    return s


def search(templates: list, query: str, limit: int = 5) -> list:
    """关键词检索。API 模板（需要云端 Key）永远排在本地可跑的之后。"""
    q = expand_query(query)
    ranked = [(score(t, q), t) for t in templates]
    ranked = [(s, t) for s, t in ranked if s > 0]
    ranked.sort(key=lambda x: ((x[1].get("name") or "").startswith("api_"), -x[0]))
    return [t for _s, t in ranked[:limit]]


def brief(tpl: dict, with_slots: bool = False) -> dict:
    """给模型看的精简信息（别把整份 JSON 塞进 prompt）"""
    d = {"name": tpl["name"], "title": tpl["title"],
         "tags": tpl["tags"], "nodes": tpl["node_count"],
         "class_types": tpl["class_types"][:12]}
    if with_slots:
        d["slots"] = [{"node": s["node"], "role": s["role"],
                       "value": (str(s["value"])[:60])} for s in tpl["slots"]]
    return d


# ---------------------------------------------------------------- 改写
def structure_sig(wf: dict) -> tuple:
    """结构指纹：节点 id+类名 + 连线。改写前后必须相等。

    连线有两种存的形态：主图是数组 [id,origin,slot,target,slot,type]，
    子图在新版前端里是对象 {id,origin_id,target_id,...}——两种都要归一化，
    否则子图工作流会被误判成"结构变了"。
    """
    nodes = tuple(sorted((str(n.get("id")), str(n.get("type")))
                         for n in wf.get("nodes") or []))
    raw = []
    for l in wf.get("links") or []:
        if isinstance(l, list):
            raw.append(tuple(l))
        elif isinstance(l, dict):
            raw.append((l.get("id"), l.get("origin_id"), l.get("origin_slot"),
                        l.get("target_id"), l.get("target_slot")))
    links = tuple(sorted(raw, key=lambda t: str(t[0])))
    subs = tuple(sorted(str(sg.get("id")) for sg in
                        ((wf.get("definitions") or {}).get("subgraphs") or [])))
    return (nodes, links, subs)


def _coerce(field: str, value):
    if field in _INT_FIELDS:
        return int(value)
    if field in _FLOAT_FIELDS:
        return float(value)
    return value


def _propagate(wf: dict, pairs: list, changes: list = None) -> int:
    """把改过的值同步到子图的两侧

    新版前端的子图实例会在自己的 widgets_values 里另存一份「暴露出来的」参数。
    只改子图内部节点的话，用户打开工作流在画布上看到的还是旧值；而实例上的
    值又优先于内部节点——两边必须一致，否则「改完看不出变化」。

    两条路径：
      · pairs   —— 值完全相等的 widget 一起替换（同一提示词被复制多处的场景）
      · changes —— (子图 id, 下标, 新值)：改动落在子图内部时，按下标同步到
                   主图里该子图的实例节点。实测 H3 模板里实例与内部节点的提示词
                   文本并不相同（各自一份），只靠值匹配会漏。
    """
    hit = 0
    for _scope, n in _iter_nodes(wf):
        wv = n.get("widgets_values")
        if not isinstance(wv, list):
            continue
        for i, v in enumerate(wv):
            for old, new in pairs:
                if v == old:
                    wv[i] = new
                    hit += 1
    sub_ids = set()
    for sg in ((wf.get("definitions") or {}).get("subgraphs") or []):
        sub_ids.add(str(sg.get("id") or ""))
    for scope, idx, val in (changes or []):
        if not scope:
            continue
        for _sc, n in _iter_nodes(wf):
            if str(n.get("type") or "") != scope:
                continue
            wv = n.get("widgets_values")
            if isinstance(wv, list) and idx < len(wv) and isinstance(wv[idx], str):
                wv[idx] = val
                hit += 1
    return hit


def apply_edits(wf: dict, edits: dict) -> tuple:
    """把 edits 写回工作流副本。

    edits 支持三种键：
        "prompt" / "negative"          → 按角色找文本节点
        "width"/"height"/"seed"/...     → 按字段名找
        "node:<id>:<field>"            → 精确定位（模型选错时兜底）
    返回 (新 wf, 已应用列表, 警告列表)；结构被改动会抛 ValueError。
    """
    if not isinstance(edits, dict):
        raise ValueError("edits 必须是对象")
    new = json.loads(json.dumps(wf))          # 深拷贝，绝不原地改用户文件
    before = structure_sig(new)
    pairs = list(_iter_nodes(new))            # 含子图内部节点
    applied, warns = [], []
    used = set()
    prop = []                                 # [(旧值, 新值)]，用于同步子图实例
    changes = []                              # [(子图 id, 下标, 新值)]

    def find_by_role(role):
        # 槽位带上 scope（子图 id），后面同步子图实例要用
        return [(n, dict(s, scope=sc)) for sc, n in pairs
                for s in node_slots(n) if s["role"] == role]

    for key, raw in edits.items():
        key = str(key).strip()
        targets = []
        if key.startswith("node:"):
            parts = key.split(":")                 # node:20:width
            if len(parts) == 3:
                nid, field = parts[1], parts[2]
                for _sc, n in pairs:
                    if str(n.get("id")) == nid:
                        for s in node_slots(n):
                            if s["field"] == field:
                                targets.append((n, s))
        elif key in ("prompt", "negative"):
            targets = find_by_role(key)
            if not targets and key == "prompt":
                g = _guess_prompt_slot(new)      # 白名单没覆盖到时的兜底
                if g:
                    targets = [(n, g) for _sc, n in pairs
                               if n.get("id") == g["node"]]
            # 多标签场景（正向有多个文本节点）只改第一个，其余原样
            targets = targets[:1]
        else:
            for _sc, n in pairs:
                for s in node_slots(n):
                    if s["field"] == key and s["role"] not in used:
                        targets.append((n, s))
        if not targets:
            warns.append("模板里没有「%s」这个可编辑项，已跳过" % key)
            continue
        n, s = targets[0]
        wv = n.get("widgets_values")
        try:
            val = _coerce(s["field"], raw)
        except (TypeError, ValueError):
            warns.append("「%s」的值 %r 类型不对，已跳过" % (key, raw))
            continue
        if s["index"] >= len(wv):
            warns.append("「%s」槽位越界，已跳过" % key)
            continue
        old = wv[s["index"]]
        wv[s["index"]] = val
        used.add(s["role"])
        applied.append({"key": key, "node": n.get("id"),
                        "field": s["field"], "value": val})
        if isinstance(old, str) and isinstance(val, str) and old != val:
            prop.append((old, val))
            changes.append((s.get("scope") or "", s["index"], val))

    if prop:
        _propagate(new, prop, changes)

    after = structure_sig(new)
    if before != after:
        raise ValueError("改写破坏了工作流结构（节点/连线发生变化），已中止")
    return new, applied, warns


# ---------------------------------------------------------------- 模型规划
PLAN_SYSTEM = (
    "你是 ComfyUI 工作流助手。用户会用口语描述想要的效果，你要从候选模板里选一个，"
    "并给出需要修改的参数。\n"
    "规则：\n"
    "1) 只能从候选模板里选，template 填模板的 name（含 .json）；\n"
    "2) 只能改参数值，不能增删节点、不能改连线——你不需要、也不应该输出工作流结构；\n"
    "3) edits 的键用槽位里的 role（如 prompt / negative / width / height / seed / steps）；\n"
    "4) 用户没提到的参数不要写；\n"
    "5) 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。\n"
    '示例：{"template": "xxx.json", "edits": {"prompt": "银发少女，雨夜街头", '
    '"width": 832, "height": 1216}}'
)


def _parse_json_reply(text: str) -> dict:
    """模型偶尔会套 ```json 或前后加话，尽量抠出第一个 JSON 对象"""
    s = (text or "").strip()
    if "```" in s:
        parts = s.split("```")
        for p in parts:
            p = p.strip()
            if p.lower().startswith("json"):
                p = p[4:]
            if p.startswith("{"):
                s = p
                break
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


def plan(templates: list, user_text: str, chat, top: int = 3) -> dict:
    """让模型选模板 + 填参数，返回改写好的工作流（不落盘）。

    chat(system, user) -> str：由调用方注入（本地 llama-server / 云端 API 均可）。
    返回 {"ok", "template", "path", "edits", "applied", "warns", "wf", "error"}
    """
    cands = search(templates, user_text, limit=top) or templates[:top]
    if not cands:
        return {"ok": False, "error": "本地工作流库里没有可用模板"}
    lines = []
    for i, t in enumerate(cands, 1):
        b = brief(t, with_slots=True)
        slot_txt = "、".join(
            "%s=%s" % (s["role"], str(s["value"])[:28]) for s in b.get("slots", []))
        lines.append("[%d] %s\n    用途: %s\n    可改: %s"
                     % (i, b["name"], "/".join(b["tags"]) or "未标注", slot_txt))
    user = ("候选模板：\n" + "\n".join(lines)
            + "\n\n用户需求：" + (user_text or "").strip())
    raw = chat(PLAN_SYSTEM, user)
    try:
        data = _parse_json_reply(raw)
    except Exception as e:
        return {"ok": False, "error": "模型输出不是合法 JSON：%s" % e, "raw": raw}
    name = str(data.get("template") or "").strip()
    tpl = next((t for t in cands if t["name"] == name), None)
    if tpl is None:      # 名字对不上（模型可能漏了 .json）：按标题再兜一次
        tpl = next((t for t in cands
                    if os.path.splitext(t["name"])[0] == os.path.splitext(name)[0]),
                   None)
    if tpl is None:
        return {"ok": False, "error": "模型选的模板不在候选里：%s" % name,
                "raw": raw}
    wf = _load(tpl["path"])
    try:
        new, applied, warns = apply_edits(wf, data.get("edits") or {})
    except ValueError as e:
        return {"ok": False, "error": str(e), "raw": raw}
    return {"ok": True, "template": tpl["name"], "path": tpl["path"],
            "edits": data.get("edits") or {}, "applied": applied,
            "warns": warns, "wf": new, "raw": raw}


# ---------------------------------------------------------------- 静态校验
# 常见核心节点的必需输入（UI 格式里由连线提供，widgets 不算）
_REQUIRED_INPUTS = {
    "KSampler": ("model", "positive", "negative", "latent_image"),
    "KSamplerAdvanced": ("model", "positive", "negative", "latent_image"),
    "CLIPTextEncode": ("clip",),
    "VAEDecode": ("samples", "vae"),
    "VAEEncode": ("pixels", "vae"),
    "SaveImage": ("images",),
    "EmptyLatentImage": (),
    "CheckpointLoaderSimple": (),
}


def validate(wf: dict) -> list:
    """静态自检（不需要运行 ComfyUI）：连线是否指向存在的节点、核心输入是否齐。

    只查「一定能判定」的问题；widget 取值合法性要 /object_info，交给运行期。
    """
    problems = []
    for scope, node in _iter_nodes(wf):
        where = ("子图 %s / " % scope[:8]) if scope else ""
        nid = node.get("id")
        ctype = node.get("type") or ""
        # 1) 输入端连线必须指向存在的 link
        #    子图内部不查这条：子图的入边来自「子图输入节点」，link 可能只存在于
        #    父级或根本不在两份 links 里（2026-11-15 实测 1009 的子图 45/140 号
        #    连线两处都查不到）。宁可漏报，也不要把正确工作流报成一堆红色错误。
        links = node.get("inputs") or []
        if not scope:
            for inp in links:
                if not isinstance(inp, dict):
                    continue
                lid = inp.get("link")
                if lid is None:
                    continue
                if not _link_exists(wf, lid):
                    problems.append("%s节点 %s(%s) 的输入 %s 指向不存在的连线 %s"
                                    % (where, nid, ctype, inp.get("name"), lid))
        # 2) 核心节点必需输入（只有连线才算，悬空就是缺）
        need = _REQUIRED_INPUTS.get(ctype)
        if need:
            have = {i.get("name") for i in links
                    if isinstance(i, dict) and i.get("link") is not None}
            miss = [n for n in need if n not in have]
            if miss:
                problems.append("%s节点 %s(%s) 缺少必需输入：%s"
                                % (where, nid, ctype, "/".join(miss)))
    return problems


def _link_ids(wf: dict) -> set:
    """收集全部连线 id：主图是数组形式，子图在新版前端里是对象形式"""
    ids = set()
    groups = [wf.get("links") or []]
    for sg in ((wf.get("definitions") or {}).get("subgraphs") or []):
        groups.append(sg.get("links") or [])
    for links in groups:
        for l in links:
            if isinstance(l, list) and l:
                ids.add(l[0])
            elif isinstance(l, dict) and l.get("id") is not None:
                ids.add(l["id"])
    return ids


def _link_exists(wf: dict, lid) -> bool:
    return lid in _link_ids(wf)
