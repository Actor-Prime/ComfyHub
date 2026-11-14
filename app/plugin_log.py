# -*- coding: utf-8 -*-
"""从 ComfyUI 启动日志里找「插件加载失败，缺哪个模块」（2026-11-14）

为什么要有：插件缺依赖时 ComfyUI 只在启动日志里打几行，用户在界面里
完全看不到 —— 症状表现为「节点不见了」「一提交工作流就 HTTP 400
Exception when validating node: '78:70'」，没人会把这两件事联系起来。
2026-11-14 的现场就是这样：XB_ToolBox 缺 cv2 → 子图里 XB 节点没注册 →
整个 prompt 校验失败，排查耗时很久。

真实日志四种形态（抓自 aki 整合包，带时间戳前缀）：
    Cannot import F:\\...\\custom_nodes\\ComfyUI-GGUF module for custom nodes:
        No module named 'gguf'
    Error loading F:\\...\\custom_nodes\\ComfyUI-RMBG\\py\\AILab_Segment.py:
        No module named 'segment_anything'
       0.6 seconds (IMPORT FAILED): F:\\...\\custom_nodes\\ComfyUI-LTXVideo
    🚨 [XB-BOX] FATAL ERROR: XB_ToolBox Loading Failed!
    ❌ Error Detail: No module named 'cv2'          ← 插件自己 catch 后打印，
                                                       IMPORT FAILED 里看不到它
必须排除的噪声（插件做可选后端探测时会打）：
    Found comfy_kitchen backend ascend: {... 'No module named torch_npu' ...}
    No OpenGL_accelerate module loaded: No module named 'OpenGL_accelerate'

所以判定规则是「按事件归因」而不是「见 No module named 就当缺包」：
只有落在失败行（Cannot import / Error loading / IMPORT FAILED / FATAL ERROR）
上、或能双向就近归属到某个 custom_nodes 目录的模块缺失才算数。
"""
import re

_CANNOT = re.compile(r"Cannot import\s+(.+?)\s+module for custom nodes", re.I)
_IMPORT_FAILED = re.compile(r"(?:\(IMPORT FAILED\)|IMPORT FAILED:)", re.I)
_NO_MODULE = re.compile(r"No module named\s+['\"]?([A-Za-z0-9_.\-]+)['\"]?")
_CUSTOM_NODES = re.compile(r"custom_nodes[\\/]+([^\\/\s\"']+)", re.I)
# 失败事件关键词（有它才说明这一行/这一块真的出事了）
_FAIL_WORD = re.compile(r"Cannot import|Error loading|IMPORT FAILED|"
                        r"FATAL ERROR|Loading Failed|加载失败|ImportError", re.I)
# 明确的可选组件探测（看到就整行跳过）
_NOISE = re.compile(r"comfy_kitchen backend|module loaded:|"
                    r"is not installed:|optional|will be skipped|skipping|"
                    r"falling back|using fallback", re.I)

_NEAR_BACK = 30      # 往回最多找多少行（一般 Failure 在上、traceback 在下）
_NEAR_FWD = 8        # 往后最多找多少行
# 「Import times」汇总段的行（`0.0 seconds: <path>`）：相邻但有插件名，
# 往里找会把 traceback 错绑到相邻插件上，所以向后查找要跳过它们
_SECONDS = re.compile(r"\bseconds?\b\s*[:(]", re.I)


def plugin_of(text: str) -> str:
    """从一段日志里的路径取出插件目录名（取不到返回空串）"""
    m = _CUSTOM_NODES.search(text or "")
    return m.group(1).strip().strip("\"'") if m else ""


def _nearby_plugin(idx: int, at: dict, skip: set) -> str:
    """就近找插件归属：先往后（traceback 通常紧跟在后），再往前"""
    for d in range(1, _NEAR_FWD + 1):
        j = idx + d
        if j in at and j not in skip:
            return at[j]
    for d in range(1, _NEAR_BACK + 1):
        j = idx - d
        if j in at and j not in skip:
            return at[j]
    return ""


def analyze(lines) -> dict:
    """扫描日志行 → 插件加载失败汇总

    返回：
      {"count": 插件数, "plugins": [{"name", "mods": [...]}],
       "modules": [去重模块名], "lines": [原始行，最多 8 条]}
    count=0 表示没有失败，调用方直接跳过即可。
    """
    rows = [(i, (t or "").rstrip("\r\n")) for i, t in enumerate(lines or [])]
    # 每一行里出现的 custom_nodes 目录名（一行可能只有路径，没别的信息）
    at, summary = {}, set()
    for i, line in rows:
        name = plugin_of(line)
        if not name:
            continue
        at[i] = name
        if _SECONDS.search(line):
            summary.add(i)

    plugins = {}          # name -> set(mods)；名字为空串表示「归属不明」
    keeps = []
    for i, line in rows:
        if not line or _NOISE.search(line):
            continue
        failed = bool(_FAIL_WORD.search(line))
        mod = _NO_MODULE.search(line)
        if not failed and not mod:
            continue
        name = at.get(i) or _nearby_plugin(i, at, summary)
        if mod and (failed or name):
            # 能归属到某个插件 + 有模块名 → 记账。XB_ToolBox 这种插件自己
            # catch 异常再打印「❌ Error Detail: No module named 'cv2'」的，
            # import 阶段算「成功」，IMPORT FAILED 列表里根本没有它 ——
            # 只能靠就近归属把它捞出来（2026-11-14 现场的真凶就是它）。
            plugins.setdefault(name, set()).add(mod.group(1))
        elif failed and (name or _IMPORT_FAILED.search(line)):
            plugins.setdefault(name, set())      # 只知道失败了，不知道缺什么
        else:
            # 既不是失败行、也没归属：多半是可选依赖探测残留，丢弃
            continue
        if len(keeps) < 8:
            keeps.append(line.strip()[:300])

    mods_all = sorted({m for v in plugins.values() for m in v})
    out_plugins = [{"name": k, "mods": sorted(v)}
                   for k, v in sorted(plugins.items(), key=lambda kv: kv[0].lower())]
    return {
        "count": len(out_plugins),
        "plugins": out_plugins,
        "modules": mods_all,
        "lines": keeps,
    }


def has_deps_hint(hit: dict) -> bool:
    """是否明确指向「缺依赖」（有模块名）——只有这种才推荐一键补装

    单纯 IMPORT FAILED 却解析不出模块名，也可能是插件代码跟内核不兼容，
    这时推「补装」会误导用户，所以分开处理。
    """
    return bool((hit or {}).get("modules"))


def signature(hit: dict) -> str:
    """去重指纹：同一实例同一批失败只提示一次"""
    return "|".join(sorted(p["name"] for p in (hit or {}).get("plugins") or [])) \
        + "#" + ",".join((hit or {}).get("modules") or [])


def summarize(hit: dict, max_plugins: int = 3, max_mods: int = 3) -> str:
    """一句话文案，例如：
    「6 个插件加载失败（ComfyUI-GGUF、ComfyUI-Impact-Pack…），缺 cv2、gguf」
    """
    hit = hit or {}
    plugins = [p["name"] for p in hit.get("plugins") or [] if p["name"]]
    mods = hit.get("modules") or []
    if not plugins and not mods:
        return ""
    head = ("%d 个插件加载失败（%s%s）"
            % (len(plugins), "、".join(plugins[:max_plugins]),
               "…" if len(plugins) > max_plugins else "")
            if plugins else "有插件加载失败")
    if mods:
        head += "，缺 %s%s" % ("、".join(mods[:max_mods]),
                              "…" if len(mods) > max_mods else "")
    return head
