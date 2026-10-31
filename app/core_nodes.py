# -*- coding: utf-8 -*-
"""内核核心节点识别（2026-10-31 扫描器增强）

背景（真实案例）：Qwen-Image-2.1 工作流扫描出三个「未识别」节点：
  · ComfySwitchNode          —— 核心节点（comfy_extras/nodes_logic.py），本机内核
    里就有，却被当插件让用户去 Manager 搜索（误报）；
  · TextEncodeQwenImage21 / QwenImage21Cache —— 也是核心节点，但要 ComfyUI
    ≥ 0.37.0（2026-09 中旬进核心），旧内核上没有；正确动作是「升级内核」，
    去 Manager 搜插件是死路（根本不存在该插件）。

两层修法：
  1) scan_kernel_core()  静态反查实例自身内核源码 comfy/ + comfy_extras/ 的
     节点注册（node_id="..." 新式 / NODE_CLASS_MAPPINGS 旧式），在本机内核里的
     = 核心节点，直接判「内核自带」；
  2) CORE_MIN_VERSION    知识表兜底「本机内核没有、但新内核已是核心」的节点，
     判「需升级内核到 ≥ X」。
零执行（不 import 内核代码）、带缓存，可在 UI 线程外任意调用。
"""
import os
import re

# node_id="X"（io.ComfyNode 新式注册：kwarg 形态与 dict 键形态都要认）
_NODE_ID_RE = re.compile(r"""["']?node_id["']?\s*[:=]\s*["']([^"']+)["']""")
# NODE_CLASS_MAPPINGS = {"X": ...}（旧式注册，抓 dict 的字符串键）
_NCM_RE = re.compile(r"NODE_CLASS_MAPPINGS\s*[:=]\s*\{(.{0,30000}?)\}", re.S)
_KEY_RE = re.compile(r"""["']([^"']+)["']\s*:""")
_VER_RE = re.compile(r"""(?:__version__|version)\s*=\s*["']([0-9][0-9A-Za-z.\-]*)["']""")

# 内核扫描缓存：{规范化路径: ({类名: 模块}, 版本字符串)} —— 同一进程内免重复解析
_CACHE = {}


def parse_ver(v: str) -> tuple:
    """'0.37.0' → (0, 37, 0)，非数字段按 0，便于元组比较"""
    out = []
    for part in str(v).split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num or 0))
    return tuple((out + [0, 0, 0])[:3])


def kernel_version(comfyui_path: str) -> str:
    """读内核版本号：pyproject.toml → comfyui_version.py → 空串"""
    info = _scan(comfyui_path)
    return info[1]


def scan_kernel_core(comfyui_path: str) -> dict:
    """{节点类名: 所在模块} —— 本机内核注册的全部核心节点"""
    return dict(_scan(comfyui_path)[0])


def known_core_min_version(node: str) -> str:
    """知识表：新内核核心节点 → 最低内核版本；不认识返回 ''"""
    return CORE_MIN_VERSION.get(node, "")


def classify_node(node: str, comfyui_path: str) -> tuple:
    """节点三分类。返回 (kind, detail)：
       ("local_core",  模块名)   本机内核自带，无需任何安装
       ("needs_upgrade", "0.37.0")  核心节点但内核版本不够 → 升级内核
       ("unknown", "")           核心表里也没有 → 才轮到找插件/AI
    """
    core, ver = _scan(comfyui_path)
    if node in core:
        return ("local_core", core[node])
    minv = known_core_min_version(node)
    if minv:
        if not ver or parse_ver(ver) < parse_ver(minv):
            return ("needs_upgrade", minv)
        # 版本已达标却没扫到——大概率内核文件布局变化，仍按核心节点提示
        return ("local_core", "(内核自带)")
    return ("unknown", "")


# ---------------- 内核源码静态扫描 ----------------
_SCAN_DIRS = ("comfy_extras",)          # 扫描的包目录（一层 *.py）
_SCAN_FILES = (os.path.join("comfy", "nodes.py"),)  # 核心注册入口


def _scan(comfyui_path: str) -> tuple:
    key = os.path.normcase(os.path.normpath(comfyui_path or ""))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    found, ver = {}, ""
    if comfyui_path and os.path.isdir(comfyui_path):
        files = []
        for d in _SCAN_DIRS:
            p = os.path.join(comfyui_path, d)
            if os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    if f.endswith(".py"):
                        files.append(os.path.join(p, f))
        for f in _SCAN_FILES:
            p = os.path.join(comfyui_path, f)
            if os.path.isfile(p):
                files.append(p)
        for f in files:
            mod = os.path.relpath(f, comfyui_path).replace("\\", "/")
            for cls in _parse_node_names(f):
                found.setdefault(cls, mod)
        ver = _read_version(comfyui_path)
    _CACHE[key] = (found, ver)
    return found, ver


def _parse_node_names(pyfile: str) -> set:
    names = set()
    try:
        src = open(pyfile, encoding="utf-8", errors="replace").read()
    except OSError:
        return names
    names.update(_NODE_ID_RE.findall(src))
    for m in _NCM_RE.finditer(src):
        names.update(_KEY_RE.findall(m.group(1)))
    return names


def _read_version(comfyui_path: str) -> str:
    for rel in ("pyproject.toml", "comfyui_version.py",
                os.path.join("comfy", "__init__.py")):
        p = os.path.join(comfyui_path, rel)
        if not os.path.isfile(p):
            continue
        try:
            src = open(p, encoding="utf-8", errors="replace").read(4000)
        except OSError:
            continue
        m = _VER_RE.search(src)
        if m:
            return m.group(1)
    return ""


# ---------------- 新内核核心节点知识表 ----------------
# 本机内核没有、但新内核已是核心的节点 → 最低内核版本。
# 来源：ComfyUI 发布说明/官方模板（2026-09 中旬 0.37.0 起内置 Qwen-Image-2.1
# 支持，TextEncodeQwenImage21 与 QwenImage21Cache 随核心分发，无第三方插件）。
# 收录原则：只登记多方可考的条目，宁缺毋滥——表里没有不等于不是核心，
# 但乱写会误导用户升级，比「未识别」更糟。
CORE_MIN_VERSION = {
    "TextEncodeQwenImage21": "0.37.0",
    "QwenImage21Cache": "0.37.0",
}
