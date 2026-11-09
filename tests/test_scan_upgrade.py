# -*- coding: utf-8 -*-
"""扫描器增强回归（2026-10-31）：核心节点反查 / 内核版本提示 / 模型直链 / AI 接入

背景案例：Qwen-Image-2.1 工作流扫描出 ComfySwitchNode「未识别」（实为核心节点，
本机内核就有）、TextEncodeQwenImage21/QwenImage21Cache「请到 Manager 找插件」
（实为 0.37+ 核心节点，正确动作=升级内核）、三个模型「尚无搜索源」
（实为 Comfy-Org/Qwen-Image-2.1 直链）。

用法：.venv_build/Scripts/python.exe tests/test_scan_upgrade.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app import core_nodes, workflow_scan, installer, model_resolve, ai_assist

# ---------------- 伪造内核 ----------------
TMP = tempfile.mkdtemp(prefix="scan_upg_")
KERNEL = os.path.join(TMP, "ComfyUI")
os.makedirs(os.path.join(KERNEL, "comfy_extras"))
os.makedirs(os.path.join(KERNEL, "comfy"))

# 新式 node_id 注册（如 nodes_logic.py 的 ComfySwitchNode）
with open(os.path.join(KERNEL, "comfy_extras", "nodes_logic.py"), "w", encoding="utf-8") as f:
    f.write('''
class SwitchNode:
    DEFINITION = {"node_id": "ComfySwitchNode"}
''')
# 旧式 NODE_CLASS_MAPPINGS 注册
with open(os.path.join(KERNEL, "comfy_extras", "nodes_qwen.py"), "w", encoding="utf-8") as f:
    f.write('''
NODE_CLASS_MAPPINGS = {
    "TextEncodeQwenImageEdit": TextEncodeQwenImageEdit,
    "EmptyQwenImageLayeredLatentImage": X,
}
''')
with open(os.path.join(KERNEL, "comfy", "nodes.py"), "w", encoding="utf-8") as f:
    f.write('NODE_CLASS_MAPPINGS = {"KSampler": KSampler, "VAEDecode": V}\n')
with open(os.path.join(KERNEL, "pyproject.toml"), "w", encoding="utf-8") as f:
    f.write('[project]\nname = "comfyui"\nversion = "0.9.2"\n')

# ---------------- 1. core_nodes ----------------
core = core_nodes.scan_kernel_core(KERNEL)
check("1.1 内核扫描发现新式 node_id 节点", core.get("ComfySwitchNode") == "comfy_extras/nodes_logic.py",
      str(core.get("ComfySwitchNode")))
check("1.2 内核扫描发现旧式 NODE_CLASS_MAPPINGS 节点",
      "TextEncodeQwenImageEdit" in core and "KSampler" in core)
check("1.3 未知名不误报", "QwenImage21Fake" not in core)
check("1.4 版本解析 pyproject", core_nodes.kernel_version(KERNEL) == "0.9.2",
      core_nodes.kernel_version(KERNEL))

k2 = os.path.join(TMP, "ComfyUI2")
os.makedirs(os.path.join(k2, "comfy"))
with open(os.path.join(k2, "comfyui_version.py"), "w", encoding="utf-8") as f:
    f.write('__version__ = "0.43.0"\n')
check("1.5 版本解析 comfyui_version.py", core_nodes.kernel_version(k2) == "0.43.0")
check("1.6 版本比较 0.9.2 < 0.37.0", core_nodes.parse_ver("0.9.2") < core_nodes.parse_ver("0.37.0"))
check("1.7 版本比较 0.43.0 >= 0.37.0", core_nodes.parse_ver("0.43.0") >= core_nodes.parse_ver("0.37.0"))

k, d = core_nodes.classify_node("ComfySwitchNode", KERNEL)
check("2.1 classify：内核自带节点 → local_core", k == "local_core", f"{k},{d}")
k, d = core_nodes.classify_node("TextEncodeQwenImage21", KERNEL)
check("2.2 classify：新内核核心旧内核没有 → needs_upgrade 0.37.0",
      k == "needs_upgrade" and d == "0.37.0", f"{k},{d}")
k, d = core_nodes.classify_node("QwenImage21Cache", KERNEL)
check("2.3 classify：QwenImage21Cache → needs_upgrade", k == "needs_upgrade", k)
k, d = core_nodes.classify_node("SomeRandomNode", KERNEL)
check("2.4 classify：真未知 → unknown", k == "unknown", k)

# ---------------- 2. workflow_scan 集成 ----------------
wf = {
    "nodes": [
        {"type": "KSampler"}, {"type": "ComfySwitchNode"},
        {"type": "TextEncodeQwenImage21"}, {"type": "SomeRandomNode"},
        {"type": "UNETLoader"},
    ]
}
wf_path = os.path.join(TMP, "wf.json")
json.dump(wf, open(wf_path, "w", encoding="utf-8"))
res = workflow_scan.analyze_workflow(KERNEL, wf_path)
check("3.1 ComfySwitchNode 判核心（不再误报未识别）", "ComfySwitchNode" in res["core"],
      str(res["missing"]))
check("3.2 TextEncodeQwenImage21 落入 unknown 供升级提示", "TextEncodeQwenImage21" in res["missing"].get("__unknown__", []))
check("3.3 kernel_ver 随结果携带", res.get("kernel_ver") == "0.9.2", str(res.get("kernel_ver")))

# ---------------- 3. installer.plan_from_scan ----------------
plan = installer.plan_from_scan(res, resolve_online=False)
up_nodes = {u["node"] for u in plan["upgrades"]}
check("4.1 TextEncodeQwenImage21 → upgrades(0.37.0)",
      "TextEncodeQwenImage21" in up_nodes and
      any(u["min_version"] == "0.37.0" for u in plan["upgrades"]), str(plan["upgrades"]))
check("4.2 真未知 → unresolvable", "SomeRandomNode" in plan["unresolvable"],
      str(plan["unresolvable"]))

# 内置映射命中
wf2 = {"nodes": [{"type": "UnetLoaderGGUF"}]}
wf2_path = os.path.join(TMP, "wf2.json")
json.dump(wf2, open(wf2_path, "w", encoding="utf-8"))
res2 = workflow_scan.analyze_workflow(KERNEL, wf2_path)
plan2 = installer.plan_from_scan(res2, resolve_online=False)
check("4.3 内置插件映射命中 → git_clone 步骤",
      any(s["type"] == "git_clone" and "ComfyUI-GGUF" in s["content"]
          for s in plan2["steps"]), str(plan2["steps"]))

# plugin_store 在线映射（打桩）
import app.plugin_store as plugin_store
_stub_sugg = [{"class": "SomeRandomNode",
               "urls": ["https://github.com/x/ComfyUI-Random"], "desc": "", "catalog_node": None}]
_orig_sugg = plugin_store.suggest_for_classes


def _fake_sugg(classes, log=print):
    out = []
    for c in classes:
        d = dict(_stub_sugg[0])
        d["class"] = c
        out.append(d)
    return out


plugin_store.suggest_for_classes = _fake_sugg
plan3 = installer.plan_from_scan(res, resolve_online=True)
check("5.1 Manager 映射命中 → git_clone 步骤",
      any(s["type"] == "git_clone" and "ComfyUI-Random" in s["content"]
          and not s.get("ai") for s in plan3["steps"]), str(plan3["steps"]))
plugin_store.suggest_for_classes = lambda classes, log=print: []
plan4 = installer.plan_from_scan(res, resolve_online=True)
check("5.2 映射落空 → 仍 unresolvable", "SomeRandomNode" in plan4["unresolvable"])

# AI 接入（打桩）
import app.ai_assist as ai_assist_mod
_orig_chat = ai_assist_mod._chat
ai_settings = {"ai_enabled": True, "ai_base_url": "https://api.deepseek.com/v1",
               "ai_api_key": "sk-test", "ai_model": "deepseek-chat"}
ai_assist_mod._chat = lambda sys_, usr, st, timeout=40: \
    "https://github.com/someone/ComfyUI-AIGuess"
plan5 = installer.plan_from_scan(res, resolve_online=True, ai_settings=ai_settings)
ai_steps = [s for s in plan5["steps"] if s.get("ai")]
check("6.1 AI 接入命中 → 步骤带 ai 标记（待验证）",
      len(ai_steps) == 1 and "待验证" in ai_steps[0]["reason"]
      and "ComfyUI-AIGuess" in ai_steps[0]["content"], str(plan5["steps"]))
check("6.2 AI 命中后不再落 unresolvable", "SomeRandomNode" not in plan5["unresolvable"])
ai_assist_mod._chat = lambda sys_, usr, st, timeout=40: "UNKNOWN"
plan6 = installer.plan_from_scan(res, resolve_online=True, ai_settings=ai_settings)
check("6.3 AI 说 UNKNOWN → 落回 unresolvable", "SomeRandomNode" in plan6["unresolvable"])
ai_assist_mod._chat = _orig_chat

# ---------------- 4. model_resolve ----------------
hit = model_resolve.resolve_file("qwen_image_2.1_int8_convrot.safetensors", online=False)
check("7.1 已知表命中 Qwen 主干", hit and hit["repo"] == "Comfy-Org/Qwen-Image-2.1"
      and hit["path"].startswith("diffusion_models/"), str(hit))
hit = model_resolve.resolve_file("qwen3vl_8b_int8_convrot.safetensors", online=False)
check("7.2 已知表命中文本编码器（text_encoders 目录）",
      hit and hit["subdir"] == "text_encoders", str(hit))
hit = model_resolve.resolve_file("qwen_image_2.1_vae_bf16.safetensors", online=False)
check("7.3 已知表命中 VAE", hit and hit["subdir"] == "vae", str(hit))
check("7.4 双源直链齐全（hf_url+ms_url）",
      hit and "hf-mirror.com" in hit["hf_url"] and "modelscope.cn" in hit["ms_url"], str(hit))
check("7.5 未知文件离线不命中", model_resolve.resolve_file("totally_unknown.safetensors", online=False) is None)

# repack 树扫描（打桩缓存，不走网络）——用一个不在已知表里的文件名
model_resolve._TREE_CACHE["Comfy-Org/Qwen-Image-2.1"] = (
    10 ** 12, [{"path": "text_encoders/qwen3vl_8b_fp8_e4m3fn.safetensors", "size": 5_100_000_000},
               {"path": "vae/qwen_image_2.1_vae_bf16.safetensors", "size": 680_000_000}])
hit = model_resolve.resolve_file("qwen3vl_8b_fp8_e4m3fn.safetensors", online=True)
check("8.1 repack 树扫描命中（source=repack_tree）",
      hit and hit["source"] == "repack_tree" and hit["path"].startswith("text_encoders/"), str(hit))

# plan_from_scan 模型行
wf3 = {"nodes": [{"type": "UNETLoader", "widgets_values": ["qwen_image_2.1_int8_convrot.safetensors"]},
                 {"type": "VAELoader", "widgets_values": ["mystery_vae.safetensors"]}]}
wf3_path = os.path.join(TMP, "wf3.json")
json.dump(wf3, open(wf3_path, "w", encoding="utf-8"))
res3 = workflow_scan.analyze_workflow(KERNEL, wf3_path)
plan7 = installer.plan_from_scan(res3, resolve_online=False)
by_file = {m["file"]: m for m in plan7["models"]}
check("9.1 模型有直链 → downloadable + 状态含仓库",
      by_file["qwen_image_2.1_int8_convrot.safetensors"]["downloadable"]
      and "Comfy-Org" in by_file["qwen_image_2.1_int8_convrot.safetensors"]["status"],
      str(plan7["models"]))
check("9.2 无直链模型 → 需手动下载 + source=None",
      not by_file["mystery_vae.safetensors"]["downloadable"]
      and by_file["mystery_vae.safetensors"]["source"] is None)

# AI 模型推断（打桩）
ai_assist_mod._chat = lambda sys_, usr, st, timeout=40: \
    "someone/Some-Repo\nmodels/vae/mystery_vae.safetensors"
plan8 = installer.plan_from_scan(res3, resolve_online=False, ai_settings=ai_settings)
m2 = {m["file"]: m for m in plan8["models"]}["mystery_vae.safetensors"]
check("10.1 AI 模型推断 → downloadable + 待验证状态",
      m2["downloadable"] and "待验证" in m2["status"]
      and m2["source"]["repo"] == "someone/Some-Repo", str(m2))
ai_assist_mod._chat = _orig_chat

# ---------------- 5. ai_assist 校验 ----------------
check("11.1 enabled 三件套齐全才开", ai_assist.enabled(ai_settings) is True)
check("11.2 缺 key 不开", ai_assist.enabled({"ai_enabled": True, "ai_base_url": "x"}) is False)
check("11.3 未启用不开", ai_assist.enabled({}) is False)
check("11.4 白名单域名收", ai_assist._valid_url("https://github.com/a/b") != "")
check("11.5 幻觉域名拒", ai_assist._valid_url("https://comfyui-magic-download.net/x") == "")
check("11.6 从文本提首个可信 URL",
      ai_assist._first_url("see https://fake.io/x then https://huggingface.co/a/b !")
      == "https://huggingface.co/a/b")

# 收尾
plugin_store.suggest_for_classes = _orig_sugg
shutil.rmtree(TMP, ignore_errors=True)

n_fail = sum(1 for _n, ok, _d in RESULTS if not ok)
print(f"\n===== {len(RESULTS) - n_fail}/{len(RESULTS)} 通过 =====")
sys.exit(1 if n_fail else 0)
