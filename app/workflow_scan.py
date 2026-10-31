# -*- coding: utf-8 -*-
"""工作流 → 插件智能识别
解析 ComfyUI 工作流 JSON（UI 格式 / API 格式），提取节点类型，
反查所属插件包，列出缺失/缺失模型的插件清单。
"""
import ast
import json
import os
import re

from app import core_nodes

# ComfyUI 内置核心节点（无需插件）——按前缀 + 精确名单双重匹配
CORE_EXACT = {
    "KSampler", "KSamplerAdvanced", "CheckpointLoaderSimple", "VAELoader",
    "CLIPTextEncode", "CLIPSetLastLayer", "VAEDecode", "VAEEncode",
    "EmptyLatentImage", "ImageScale", "ImageScaleBy", "SaveImage", "LoadImage",
    "PreviewImage", "LoadImageMask", "ImageInvert", "ImagePadForOutpaint",
    "ConditioningCombine", "ConditioningAverage", "SetLatentNoiseMask",
    "LatentUpscale", "LatentUpscaleBy", "LatentFromBatch", "RepeatLatentBatch",
    "ControlNetLoader", "ControlNetApply", "ControlNetApplyAdvanced",
    "DiffusersLoader", "LoraLoader", "LoraLoaderModelOnly", "UNETLoader",
    "DualCLIPLoader", "TripleCLIPLoader", "CLIPVisionLoader", "VAEDecodeTiled",
    "VAEEncodeTiled", "UpscaleModelLoader", "ImageUpscaleWithModel",
    "CLIPLoader", "CLIPVisionEncode", "StyleModelLoader", "StyleModelApply",
    "unCLIPCheckpointLoader", "GLIGENLoader", "GLIGENTextInput",
    "EmptyLatentImagePresets", "TomePatchModel", "ModelMergeSimple",
    "ModelMergeBlocks", "ModelMergeSubtract", "ModelMergeAdd", "CLIPMergeSimple",
    "SaveAnimatedWEBP", "SaveAnimatedPNG", "ImageBatch", "ImageRepeat",
    "ImageBlend", "ImageBlur", "ImageQuantize", "ImageSharpen",
    "ImageRotate", "ImageFlip", "ImageCrop", "ImageCompositeMasked",
    "MaskToImage", "ImageToMask", "ImageColorToMask", "SolidMask",
    "InvertMask", "CropMask", "MaskComposite", "FeatherMask", "GrowMask",
    "ThresholdMask", "LoadImageOutput", "PerpNeg", "DifferentialDiffusion",
    # 新内核节点 / 前端虚拟节点
    "SetNode", "GetNode", "MarkdownNote", "Note", "Reroute",
    "PrimitiveNode", "PrimitiveInt", "PrimitiveFloat", "PrimitiveBoolean",
    "PrimitiveString", "PrimitiveStringMultiline", "String", "Int", "Float",
    "Boolean", "Combo", "KSamplerSelect", "SamplerCustom", "SamplerCustomAdvanced",
    "BasicScheduler", "BasicGuider", "RandomNoise", "DisableNoise",
    "FluxGuidance", "EmptySD3LatentImage", "EmptyHunyuanLatentVideo",
    "EmptyMochiLatentVideo", "EmptyCosmosLatentVideo", "LTXVConditioning",
    "ModelSamplingFlux", "ModelSamplingSD3", "ModelSamplingAuraFlow",
}
# 内置前缀（核心扩展，随内核分发）
CORE_PREFIXES = (
    "Latent", "CLIP", "Model", "Lora", "VAE", "Canny", "Webcam",
    "FreeU", "SelfAttentionGuidance", "PerturbedAttention",
    "ControlNet", "DiffControlNetLoader", "Morphology", "SplitImage",
    "GELU", "HyperTile", "FaceAnalysis",
)


def is_core_node(class_type: str) -> bool:
    if class_type in CORE_EXACT:
        return True
    return class_type.startswith(CORE_PREFIXES)


# ---------------- 工作流解析 ----------------
def load_workflow(path_or_json: str) -> dict:
    """读工作流文件，返回原始 dict。兼容 UI 导出格式与 API 格式"""
    if os.path.isfile(path_or_json):
        with open(path_or_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(path_or_json)
    if not isinstance(data, dict):
        raise ValueError("不是有效的工作流 JSON")
    return data


def extract_class_types(wf: dict) -> list:
    """从工作流提取全部节点类型（自动识别 UI/API 两种格式）"""
    types = []
    if "nodes" in wf and isinstance(wf["nodes"], list):
        # UI 导出格式: nodes[].type
        for n in wf["nodes"]:
            t = n.get("type")
            if t:
                types.append(t)
    # API 格式: 顶层 {node_id: {class_type: ...}}；UI 格式也带个头顶层字段兜底扫一遍
    for k, v in wf.items():
        if k == "nodes":
            continue
        if isinstance(v, dict) and "class_type" in v:
            types.append(v["class_type"])
    # 去重保序
    seen, out = set(), []
    for t in types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_models(wf: dict) -> dict:
    """提取工作流引用的模型文件名（ckpt/lora/vae 等主要类型）"""
    models = {}
    UI_WIDGET_KEYS = ("ckpt_name", "lora_name", "vae_name", "unet_name",
                      "control_net_name", "clip_name", "style_model_name",
                      "upscale_model_name", "model_name")
    def _add(kind, val):
        if isinstance(val, str) and val.lower().endswith((".safetensors", ".ckpt", ".pt", ".pth", ".sft", ".gguf")):
            models.setdefault(kind, set()).add(val)
    if "nodes" in wf and isinstance(wf["nodes"], list):
        for n in wf["nodes"]:
            t = n.get("type", "")
            widgets = n.get("widgets_values") or []
            if t in ("CheckpointLoaderSimple", "CheckpointLoader"):
                for w in widgets:
                    if isinstance(w, str) and "/" in w or w.endswith((".safetensors", ".ckpt")):
                        _add("checkpoints", w)
            elif t in ("LoraLoader", "LoraLoaderModelOnly"):
                for w in widgets:
                    _add("loras", w)
            elif t == "VAELoader":
                for w in widgets:
                    _add("vae", w)
            elif t in ("ControlNetLoader",):
                for w in widgets:
                    _add("controlnet", w)
            elif t in ("UNETLoader",):
                for w in widgets:
                    _add("unet", w)
            else:
                for w in widgets:
                    if isinstance(w, str) and w.endswith((".safetensors", ".ckpt", ".gguf")):
                        _add("其他", w)
    # API 格式: inputs 里的键名
    for _k, v in wf.items():
        if isinstance(v, dict) and "inputs" in v and isinstance(v["inputs"], dict):
            ct = v.get("class_type", "")
            for key, val in v["inputs"].items():
                if key in UI_WIDGET_KEYS:
                    kind = {"ckpt_name": "checkpoints", "lora_name": "loras",
                            "vae_name": "vae", "unet_name": "unet",
                            "control_net_name": "controlnet"}.get(key, "其他")
                    _add(kind, val)
    return {k: sorted(v) for k, v in models.items()}


# ---------------- 插件反查 ----------------
def build_node_plugin_map(comfyui_path: str, log=print) -> dict:
    """扫描 custom_nodes 下所有插件的 NODE_CLASS_MAPPINGS，
    返回 {节点类名: 插件目录名}。静态解析，无需导入插件（避免副作用）。
    性能：只扫 __init__.py 和 nodes.py，大仓库快。
    """
    mapping = {}
    nodes_dir = os.path.join(comfyui_path, "custom_nodes")
    if not os.path.isdir(nodes_dir):
        return mapping
    for name in sorted(os.listdir(nodes_dir)):
        p = os.path.join(nodes_dir, name)
        if not os.path.isdir(p) or name.startswith("__") or name.endswith(".disabled"):
            continue
        found = _scan_plugin_nodes(p)
        for cls in found:
            mapping.setdefault(cls, name)
    return mapping


def _scan_plugin_nodes(plugin_dir: str) -> set:
    """从插件源码静态提取 NODE_CLASS_MAPPINGS 的 key"""
    classes = set()
    for fname in ("__init__.py", "nodes.py", "node.py"):
        f = os.path.join(plugin_dir, fname)
        if os.path.isfile(f):
            classes |= _parse_mappings_keys(f)
    # 部分插件映射分散在子模块（如 nodes_xxx.py），限制深度补扫
    try:
        for entry in os.listdir(plugin_dir):
            if entry.startswith("nodes") and entry.endswith(".py"):
                classes |= _parse_mappings_keys(os.path.join(plugin_dir, entry))
    except OSError:
        pass
    return classes


def _parse_mappings_keys(pyfile: str) -> set:
    keys = set()
    try:
        tree = ast.parse(open(pyfile, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return keys
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "NODE_CLASS_MAPPINGS" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    try:
                        keys.add(ast.literal_eval(k))
                    except Exception:
                        pass
    # 兜底：正则扫（部分插件动态生成 dict）
    if not keys:
        try:
            src = open(pyfile, encoding="utf-8", errors="replace").read()
            m = re.search(r'NODE_CLASS_MAPPINGS\s*=\s*\{(.{0,20000}?)\}',
                          src, re.S)
            if m:
                keys = set(re.findall(r'"([^"]+)"\s*:', m.group(1)))
        except Exception:
            pass
    return keys


def analyze_workflow(comfyui_path: str, workflow_path: str, log=print) -> dict:
    """完整分析：工作流 → 节点 → 插件需求
    返回 {
        class_types: [...],            # 全部节点类型
        core: [...],                   # 内核自带
        required: {插件名: [节点...]},  # 已安装插件要用的
        missing: {插件名: [节点...]},   # 未安装（无法定位来源，给出节点名与搜索建议）
        models: {类别: [文件名...]},
    }
    """
    wf = load_workflow(workflow_path)
    ctypes = extract_class_types(wf)
    models = extract_models(wf)
    node_map = build_node_plugin_map(comfyui_path, log=log)
    # 2026-10-31：静态反查实例自身内核（comfy/ + comfy_extras/ 节点注册），
    # 核心节点不再误报「未识别」（如 ComfySwitchNode 就在 nodes_logic.py）
    kernel_core = core_nodes.scan_kernel_core(comfyui_path)

    core, required, unknown = [], {}, []
    for t in ctypes:
        if is_core_node(t) or t in kernel_core:
            core.append(t)
        elif t in node_map:
            required.setdefault(node_map[t], []).append(t)
        else:
            unknown.append(t)
    # unknown = 需要用户搜索安装的（可能是未装的插件，也可能要升级内核）
    missing = {"__unknown__": unknown} if unknown else {}
    return {
        "class_types": ctypes,
        "core": core,
        "required": required,
        "missing": missing,
        "models": models,
        "kernel_ver": core_nodes.kernel_version(comfyui_path),
        "comfyui_path": comfyui_path,
    }
