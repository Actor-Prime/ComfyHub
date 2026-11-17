# -*- coding: utf-8 -*-
"""ui2api.py - LiteGraph UI 工作流 JSON -> ComfyUI /prompt API 格式（含子图展开）

2026-11-03 于真实实例上逐类验证过（Z-Image 子图工作流 + Qwen 平铺工作流均实际出图）。
覆盖前端 graphToPrompt 会做、而 API 直提必须自己处理的四类事：
  1. 新式 COMBO（spec[0]=="COMBO"，选项在 spec[1]['options']）
  2. 动态输入（object_info 未声明、由节点运行时生成的端口，如 ImageConcatMulti 的 image_3+）
  3. 工作流存档缺 widget（节点类后加的参数）→ 取 object_info 默认值
  4. filename 通配符（%date:yyyy-MM-dd% 等）——前端提交前展开，API 直提必须自己做，
     否则含冒号的目录名在 Windows 上直接 NotADirectoryError
"""
import json
import re
from datetime import datetime

WIDGET_TYPES = {'INT', 'FLOAT', 'STRING', 'BOOLEAN'}
# COMBO=标准下拉；COMFY_DYNAMICCOMBO_V3=新式带动态子项的下拉（SaveVideo.format
# 等 220 处，2026-11-18 实测不识别就漏传 format → 后端 missing argument）。
# 注意 COMFY_MATCHTYPE_V3 / COMFY_AUTOGROW_V3 是连接口，不是 widget，别加进来。
WIDGET_TYPE_NAMES = WIDGET_TYPES | {'COMBO', 'COMFY_DYNAMICCOMBO_V3'}
SKIPPED_TYPES = {'Note', 'MarkdownNote', 'XB_CanvasLabel'}

_MISSING = object()


class _Lit:
    """子图边界输入未连线时、从实例 widget 兜出来的字面量值（不是连线对）"""
    __slots__ = ('v',)

    def __init__(self, v):
        self.v = v


def sg_widget_value(sg, inst, iname):
    """子图实例上某边界输入对应的 widget 值；找不到返回 _MISSING。

    2026-11-18 H3 视频工作流实测：子图 15 个输入里 6 个（prompt/vae_name/
    unet_name 等）是纯 widget，根本不在实例 inputs 列表里——旧版直接抛
    「子图输入未连线」，run_workflow 必失败。取值顺序：
    1) widgets_values 数组：按「sg 里 widget 型输入」的序号定位（ComfyHub
       与前端 serialiseWidgetValues 同序）；
    2) widgets_values_named（键名→值，键序同上）——数组缺位时兜底。
    """
    wv = inst.get('widgets_values')
    if isinstance(wv, list):
        idx = 0
        for i in sg.get('inputs') or []:
            t = i.get('type')
            is_w = isinstance(t, list) or (isinstance(t, str)
                                           and t in WIDGET_TYPE_NAMES)
            if not is_w:
                continue
            if i.get('name') == iname:
                if idx < len(wv):
                    return wv[idx]
                break
            idx += 1
    named = inst.get('widgets_values_named')
    if isinstance(named, dict) and iname in named:
        return named[iname]
    return _MISSING


def fetch_obj_info(api_base: str, timeout: int = 60) -> dict:
    import urllib.request
    with urllib.request.urlopen(api_base.rstrip('/') + '/object_info', timeout=timeout) as r:
        return json.loads(r.read())


def expand_wildcards(val):
    """前端同款 filename 通配符展开（%date:yyyy-MM-dd% 等）"""
    if not isinstance(val, str) or '%' not in val:
        return val
    now = datetime.now()

    def sub_fmt(m):
        f = m.group(2)
        for k, v in (('yyyy', '%Y'), ('yy', '%y'), ('MM', '%m'), ('dd', '%d'),
                     ('HH', '%H'), ('mm', '%M'), ('ss', '%S')):
            f = f.replace(k, v)
        return now.strftime(f)

    val = re.sub(r'%(date|time|datetime):([^%]*)%', sub_fmt, val)
    for k, v in (('year', now.strftime('%Y')), ('month', now.strftime('%m')),
                 ('day', now.strftime('%d')), ('hour', now.strftime('%H')),
                 ('minute', now.strftime('%M')), ('second', now.strftime('%S')),
                 ('date', now.strftime('%Y-%m-%d')), ('time', now.strftime('%H-%M-%S'))):
        val = val.replace('%' + k + '%', v)
    return val


def combo_options(spec):
    """COMBO 选项列表：旧式 spec[0]=选项 list；新式 spec[0]=='COMBO' 且 spec[1]['options']"""
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == 'COMBO' and isinstance(spec[1], dict):
        return spec[1].get('options')
    return None


def norm_combo(val, spec):
    """COMBO 值规整：不在选项里则按 '值 (' 前缀匹配（旧版选项名→新版全称），再不行取默认"""
    opts = combo_options(spec)
    if not opts or val in opts:
        return val
    if isinstance(val, str):
        for opt in opts:
            if isinstance(opt, str) and opt.startswith(val + ' ('):
                return opt
    if isinstance(spec[1], dict) and 'default' in spec[1]:
        return spec[1]['default']
    return val


def build(workflow: dict, obj_info: dict):
    """workflow: UI JSON dict；obj_info: /object_info 全量。
    返回 (prompt_dict, warnings_list)。"""
    main_nodes = {n['id']: n for n in workflow.get('nodes', [])}
    main_links = {l[0]: l for l in workflow.get('links', [])}
    sgs = {sg['id']: sg for sg in (workflow.get('definitions', {}) or {}).get('subgraphs', []) or []}

    prompt = {}
    warnings = []

    def _val(r):
        """resolve_* 返回连线对或 _Lit 字面量 → 统一成可直接写进 inputs 的值"""
        return r.v if isinstance(r, _Lit) else list(r)

    _OMIT = object()

    def _optional(ctype, k):
        return k in ((obj_info.get(ctype) or {}).get('input', {}).get('optional') or {})

    def _conn(link, ctype, k, inst_id):
        """解析连线；子图边界没接线且该输入可选 → _OMIT（前端同款：不传该输入，
        2026-11-18 H3 实测 first_frame/last_frame 是 optional 且未接线）"""
        try:
            if inst_id is None:
                l = main_links[link]
                return _val(resolve_main(l[1], l[2]))
            il = sg_links[link]
            return _val(resolve_sub(inst_id, il['origin_id'], il['origin_slot']))
        except RuntimeError as e:
            if '未连线' in str(e) and _optional(ctype, k):
                return _OMIT
            raise

    def widget_names(ctype):
        info = obj_info.get(ctype)
        if not info:
            return None
        names = []
        for sec in ('required', 'optional'):
            for k, spec in (info.get('input', {}).get(sec) or {}).items():
                if not isinstance(spec, list) or not spec:
                    continue
                t = spec[0]
                if isinstance(t, list):          # 旧式 COMBO
                    names.append(k)
                elif isinstance(t, str) and t in WIDGET_TYPE_NAMES:
                    names.append(k)
                    extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                    if k == 'seed' or extra.get('control_after_generate'):
                        names.append(k + '@ctrl')
        return names

    def resolve_main(orig, slot, depth=0):
        if depth > 50:
            raise RuntimeError('Reroute 循环过深')
        n = main_nodes.get(orig)
        if n is None:
            raise RuntimeError(f'主图节点 {orig} 不存在')
        t = n['type']
        if t == 'Reroute':
            lid = (n.get('inputs') or [{}])[0].get('link')
            l = main_links[lid]
            return resolve_main(l[1], l[2], depth + 1)
        if t in sgs:   # 子图实例：outputs[slot] → 边界输出 linkIds → 内部源
            out = (n.get('outputs') or [])[slot]
            sg = sgs[t]
            for so in sg['outputs']:
                if so['name'] == out['name']:
                    il = sg_links[so['linkIds'][0]]
                    return resolve_sub(orig, il['origin_id'], il['origin_slot'], depth + 1)
            raise RuntimeError(f'子图输出 {out.get("name")} 未找到')

        return str(orig), slot

    def resolve_sub(inst_id, orig, slot, depth=0):
        if depth > 50:
            raise RuntimeError('Reroute 循环过深')
        sg = sgs[main_nodes[inst_id]['type']]
        if orig == -10:   # 边界输入哨兵：slot=边界输入序号
            inst = main_nodes[inst_id]
            iname = sg['inputs'][slot]['name']
            for inp in inst.get('inputs') or []:
                if inp['name'] == iname and inp.get('link') is not None:
                    l = main_links[inp['link']]
                    return resolve_main(l[1], l[2], depth + 1)
            # 未连线 → 该输入的值挂在实例 widget 上（2026-11-18 H3 实测兜底）
            val = sg_widget_value(sg, inst, iname)
            if val is not _MISSING:
                return _Lit(val)
            raise RuntimeError(f'子图输入 {iname} 未连线，实例上也没有对应 widget 值')
        n = sub_nodes[inst_id][orig]
        if n['type'] == 'Reroute':
            lid = (n.get('inputs') or [{}])[0].get('link')
            il = sg_links[lid]
            return resolve_sub(inst_id, il['origin_id'], il['origin_slot'], depth + 1)
        return f'{inst_id}:{orig}', slot

    def emit(inst_id, node):
        t = node['type']
        if t == 'Reroute' or t in sgs or t in SKIPPED_TYPES:
            if t in SKIPPED_TYPES:
                warnings.append(f'{t}#{node["id"]} 纯装饰节点已跳过')
            return
        names = widget_names(t)
        if names is None:
            warnings.append(f'{t}#{node["id"]} 未在实例注册（缺插件？）已跳过')
            return
        inputs = {}
        wvals = list(node.get('widgets_values') or [])
        wi = 0
        for sec in ('required', 'optional'):
            info = obj_info[t]['input'].get(sec) or {}
            for k, spec in info.items():
                if not isinstance(spec, list) or not spec:
                    continue
                sp0 = spec[0]
                is_widget = isinstance(sp0, list) or (
                    isinstance(sp0, str) and sp0 in WIDGET_TYPE_NAMES)
                if is_widget:
                    # widget 可能被转成输入端口并连线——连接优先取值，但位置照常消耗
                    link = None
                    for inp in node.get('inputs') or []:
                        if inp.get('name') == k and inp.get('link') is not None:
                            link = inp['link']
                            break
                    if link is not None:
                        v = _conn(link, t, k, inst_id)
                        if v is not _OMIT:
                            inputs[k] = v
                    elif wi < len(wvals):
                        inputs[k] = norm_combo(wvals[wi], spec)
                    elif isinstance(spec[1], dict) and 'default' in spec[1]:
                        inputs[k] = spec[1]['default']   # 存档缺该 widget → 默认值
                    if wi < len(wvals):
                        wi += 1
                        if k == 'seed' or (len(spec) > 1 and isinstance(spec[1], dict)
                                           and spec[1].get('control_after_generate')):
                            if wi < len(wvals) and isinstance(wvals[wi], str):
                                wi += 1   # 跳过 control_after_generate
                    continue
                # 连接型：从 node.inputs 找同名 link
                link = None
                for inp in node.get('inputs') or []:
                    if inp.get('name') == k and inp.get('link') is not None:
                        link = inp['link']
                        break
                if link is not None:
                    v = _conn(link, t, k, inst_id)
                    if v is not _OMIT:
                        inputs[k] = v
        # 动态输入（object_info 未声明，如 ImageConcatMulti 的 image_3+）：补其连线
        for inp in node.get('inputs') or []:
            nm = inp.get('name')
            link = inp.get('link')
            if link is None or nm in inputs:
                continue
            v = _conn(link, t, nm, inst_id)
            if v is not _OMIT:
                inputs[nm] = v
        if wi < len(wvals):
            warnings.append(f'{t}#{node["id"]} 有未消耗的 widget 值 {wvals[wi:]}（多为历史遗留，无害）')
        pid = f'{inst_id}:{node["id"]}' if inst_id is not None else str(node['id'])
        prompt[pid] = {'class_type': t, 'inputs': inputs}

    # 子图 links 索引；sub_nodes 按实例 id 索引（须在主图 emit 前建好）
    sub_nodes = {}
    sg_links = {}
    for sg in sgs.values():
        for l in sg.get('links', []) or []:
            sg_links[l['id']] = l
    for inst_id, n in main_nodes.items():
        if n['type'] in sgs:
            sub_nodes[inst_id] = {sn['id']: sn for sn in sgs[n['type']].get('nodes', []) or []}

    for n in main_nodes.values():
        emit(None, n)
    for inst_id in sub_nodes:
        for sn in sub_nodes[inst_id].values():
            emit(inst_id, sn)

    for e in prompt.values():   # 通配符展开
        for k, v in e['inputs'].items():
            if isinstance(v, str):
                e['inputs'][k] = expand_wildcards(v)
    return prompt, warnings
