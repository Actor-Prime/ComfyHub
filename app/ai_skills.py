# -*- coding: utf-8 -*-
"""ai_skills.py - AI 助手会话级技能（2026-11-11）

用户诉求：参照 WorkBuddy 的三层结构——
  · ComfyUI 工具层（ai_tools）恒开，不用配；
  · agent 人格（ai_persona）恒开，不用配；
  · **skill 按会话勾选**，不同会话可挂不同技能包。

skill = 一个 markdown 文件，放进技能目录即生效：
  · 出厂内置：<安装目录>/assets/skills/*.md（随包只读）
  · 用户自定义：<数据根>/ai_skills/*.md（同名覆盖出厂）
文件格式（无 frontmatter，纯 markdown 约定）：
  · 首个「# 标题」= 技能名
  · 紧随其后的第一段普通文本 = 一句话描述（列表里显示）
  · 全文 = 注入 system 提示的正文

注入方式：会话里勾选的技能，正文按「【技能：名字】…」拼进 system 提示，
每轮生效。选几个技能由用户自己控制 token 开销。
"""
import os

from app.paths import data_root

SKILL_HEADER = "【技能】"
PROFILE_HEADER = "【本会话工作台】"

PROFILE_KEYS = ("topic", "inst_id", "workflow", "model")


def norm_profile(p) -> dict:
    """规整会话设定：只留四个已知字段，全部转成去空白的字符串"""
    p = p if isinstance(p, dict) else {}
    out = {}
    for k in PROFILE_KEYS:
        v = str(p.get(k) or "").strip()
        if v:
            out[k] = v
    return out


def is_empty(p) -> bool:
    return not norm_profile(p)


def render_profile(profile, inst_name: str = "") -> str:
    """把会话设定渲染成 system 提示段落（空配置返回空串）。

    为什么要把「这条会话是干什么的」写进提示（2026-11-11）：
    用户会同时开多条会话——A 做 H3 视频、B 做 Qwen 生图、C 做 minimax 音乐。
    模型本身不记得别的会话，但同一个人格模板容易把上一轮的套路带过来
    （视频会话里去跑生图工作流）。这段把用途、工作流、模型钉死在本会话，
    并明确禁止沿用别的会话的配置。
    """
    p = norm_profile(profile)
    if not p:
        return ""
    lines = [PROFILE_HEADER]
    topic = p.get("topic")
    if topic:
        lines.append(f"用途：{topic}。本会话只做这件事；用户问明显无关的内容时，"
                     f"先一句话确认再动手，不要自作主张换方向。")
    if inst_name:
        lines.append(f"实例：{inst_name}（本会话的工具调用都作用在这个实例上）。")
    wf = p.get("workflow")
    if wf:
        lines.append(
            f"默认工作流：{wf} —— 用户说「生成 / 跑一下 / 出个片」而没指明工作流时，"
            f"直接用它（工具里省略 name 参数即默认走它）；"
            f"不要改用别的会话的工作流，也不要为了换个效果就另建新工作流，"
            f"除非用户明确要求。")
    model = p.get("model")
    if model:
        lines.append(f"默认模型：{model}（本会话的生成都用它；工作流里已有的模型节点别乱改）。")
    lines.append("注意：其它会话可能是完全不同的用途（视频 / 生图 / 音乐），"
                 "各有各的工作流和模型——**不要沿用别的会话的选择和参数**；"
                 "本会话里只讨论与上述用途相关的事。")
    return "\n".join(lines)


def _builtin_dir() -> str:
    # PyInstaller onedir：assets 与 exe 同级；源码运行：项目根/assets
    if getattr(__import__("sys"), "frozen", False):
        base = os.path.dirname(os.path.abspath(__import__("sys").executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "skills")


def user_dir() -> str:
    """用户自定义技能目录（不存在不创建——用户放文件时自然会有）。
    COMFYHUB_SKILL_DIR 仅供测试隔离（别写进用户的真实目录）。"""
    return (os.environ.get("COMFYHUB_SKILL_DIR")
            or os.path.join(data_root(), "ai_skills"))


def _parse(path: str) -> dict:
    """解析一个 skill markdown：标题=名，首段=描述，全文=正文"""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return {}
    name, desc = os.path.splitext(os.path.basename(path))[0], ""
    lines = text.splitlines()
    body_start = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("# ") and name == os.path.splitext(os.path.basename(path))[0]:
            name = s[2:].strip() or name
            body_start = i + 1
            continue
        if s.startswith("#"):
            body_start = i + 1
            continue
        # 第一段普通文本 = 描述（到空行/标题为止，最长 120 字）
        para = []
        for ln2 in lines[i:]:
            s2 = ln2.strip()
            if not s2 or s2.startswith("#"):
                break
            para.append(s2)
        desc = " ".join(para)[:120]
        break
    return {"id": os.path.splitext(os.path.basename(path))[0],
            "name": name, "desc": desc, "body": text.strip(),
            "source": path}


def list_skills() -> list:
    """全部技能（用户目录同名覆盖出厂），按名称排序"""
    out = {}
    for d in (_builtin_dir(), user_dir()):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".md") or fn.startswith("_"):
                continue
            sk = _parse(os.path.join(d, fn))
            if sk and sk.get("body"):
                out[sk["id"]] = sk          # 用户目录后扫 → 同名覆盖出厂
    return sorted(out.values(), key=lambda s: s["name"].lower())


def get_skill(sid: str) -> dict:
    for s in list_skills():
        if s["id"] == sid:
            return s
    return {}


def render(skill_ids: list) -> str:
    """把勾选的技能渲染成 system 提示段落（空/全失效返回空串）"""
    if not skill_ids:
        return ""
    have = {s["id"]: s for s in list_skills()}
    parts = []
    for sid in (skill_ids or []):
        s = have.get(sid)
        if s:
            parts.append(f"【技能：{s['name']}】\n{s['body']}")
    return "\n\n".join(parts)
