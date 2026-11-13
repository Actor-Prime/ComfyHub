# -*- coding: utf-8 -*-
"""AI 对话存储（2026-11-06：多会话 + 重启不丢上下文）

存哪：<数据根>/ai_chats/<id>.json
存什么：{id, title, created, updated, messages:[{role, content}]}
不存什么：附件原始图片（base64 动辄几 MB）——只保留文字，界面上会提示附件不入库。

容量：最多 MAX_CHATS 个会话、每会话 MAX_MSGS 条；超出按时间淘汰最旧的。
查询/写入都做成"失败不抛"——聊天记录丢了不该让界面崩。
"""
import json
import os
import random
import time

from app.paths import data_root

MAX_CHATS = 60
MAX_MSGS = 300
MAX_CHARS = 20000


def _dir() -> str:
    # COMFYHUB_CHAT_DIR 仅供测试隔离（指向临时目录，别写进用户的真实存档）
    d = os.environ.get("COMFYHUB_CHAT_DIR") or os.path.join(data_root(), "ai_chats")
    os.makedirs(d, exist_ok=True)
    return d


def _path(cid: str) -> str:
    return os.path.join(_dir(), f"{cid}.json")


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + f"{random.randint(0x1000, 0xffff):x}"


def _clip(msgs: list) -> list:
    out = []
    for m in (msgs or [])[-MAX_MSGS:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        out.append({"role": role, "content": str(m.get("content") or "")[:MAX_CHARS]})
    return out


MAX_ARTIFACTS = 120


def _clip_artifacts(items) -> list:
    """规整产物清单：只留 kind/path/label/ts，路径去重，最多 MAX_ARTIFACTS 条"""
    out, seen = [], set()
    for a in (items or []):
        if not isinstance(a, dict):
            continue
        p = str(a.get("path") or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append({"kind": str(a.get("kind") or "file"),
                    "path": p,
                    "label": str(a.get("label") or os.path.basename(p))[:80],
                    "ts": float(a.get("ts") or 0)})
    return out[-MAX_ARTIFACTS:]


def title_of(msgs: list, fallback: str = "新对话") -> str:
    for m in msgs or []:
        if m.get("role") == "user" and str(m.get("content") or "").strip():
            t = " ".join(str(m["content"]).split())
            return t[:24] + ("…" if len(t) > 24 else "")
    return fallback


def save(cid: str, messages: list, title: str = "",
         summary: str = None, upto: int = None, skills=None,
         profile=None, artifacts=None, summary_inst=None) -> None:
    """写一个会话；messages 只保留 role/content。

    summary / upto 为「上下文压缩」产物（2026-11-06）：
    传 None 表示沿用文件里已有的值（普通保存不会把摘要清掉）。
    skills 为本会话勾选的技能 id 列表（2026-11-11）：None 同样沿用旧值。
    profile 为本会话的「工作台设定」（2026-11-11）：主题 / 绑定实例 /
    默认工作流 / 默认模型。None 同样沿用旧值，{} 表示清空。
    artifacts 为本会话的「产物」清单（2026-11-12 右侧概要栏）：
    [{kind:"image"|"file", path, label, ts}]；None 沿用旧值，[] 清空。
    summary_inst 为摘要对应的实例 id（2026-11-14）：换实例后旧摘要里的
    端口/路径全错，读取时比对不一致就直接丢弃。None 沿用旧值。
    """
    try:
        msgs = _clip(messages)
        now = time.time()
        path = _path(cid)
        created = now
        old_summary, old_upto, old_skills, old_profile = "", 0, [], {}
        old_title, old_renamed = "", False
        old_artifacts = []
        old_sinst = ""
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    prev = json.load(f)
                created = prev.get("created") or now
                old_summary = prev.get("summary") or ""
                old_upto = int(prev.get("summary_upto") or 0)
                old_skills = list(prev.get("skills") or [])
                old_profile = dict(prev.get("profile") or {})
                old_title = prev.get("title") or ""
                old_renamed = bool(prev.get("renamed"))
                old_artifacts = list(prev.get("artifacts") or [])
                old_sinst = str(prev.get("summary_inst") or "")
            except Exception:
                pass
        # 用户手动改过的名字优先（2026-11-11）：多会话并排时，
        # 每次发消息都把「H3 视频专用」变回首条消息的前 24 字，等于白改名。
        final_title = ((old_title if old_renamed else "")
                       or title or title_of(msgs))
        data = {"id": cid, "title": final_title, "created": created,
                "updated": now, "messages": msgs,
                "summary": old_summary if summary is None else str(summary),
                "summary_upto": old_upto if upto is None else max(0, int(upto)),
                "skills": old_skills if skills is None else list(skills),
                "profile": old_profile if profile is None else dict(profile or {}),
                "artifacts": (old_artifacts if artifacts is None
                              else _clip_artifacts(artifacts)),
                "summary_inst": (old_sinst if summary_inst is None
                                 else str(summary_inst or "")),
                "renamed": old_renamed}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        _enforce_limit()
    except Exception:
        pass


def load(cid: str) -> dict:
    try:
        with open(_path(cid), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("id", cid)
            d.setdefault("messages", [])
            d.setdefault("summary", "")
            d.setdefault("summary_upto", 0)
            d.setdefault("skills", [])
            d.setdefault("profile", {})
            d.setdefault("artifacts", [])
            d.setdefault("summary_inst", "")
            return d
    except Exception:
        pass
    return {"id": cid, "title": "新对话", "messages": [],
            "summary": "", "summary_upto": 0, "skills": [],
            "profile": {}, "artifacts": [], "summary_inst": "",
            "updated": 0}


def delete(cid: str) -> None:
    try:
        os.remove(_path(cid))
    except Exception:
        pass
    try:                                   # 压缩原文副本一起清掉
        import shutil
        shutil.rmtree(parts_dir(cid), ignore_errors=True)
    except Exception:
        pass


def parts_dir(cid: str) -> str:
    """被压缩原文的落盘目录（2026-11-14「压缩后可回查」）"""
    return os.path.join(_dir(), f"{cid}.parts")


def save_part(cid: str, upto: int, chunk: list) -> str:
    """压缩时把原文存下来：摘要会丢细节，模型还能用 recall_history 查回来

    存的是**压缩前**的原文（每条截 4000 字），文件名用压缩覆盖到的条数，
    便于按时间顺序检索。
    """
    try:
        if not cid or not chunk:
            return ""
        d = parts_dir(cid)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{int(upto):06d}.json")
        rows = [{"role": m.get("role"),
                 "content": str(m.get("content") or "")[:4000]}
                for m in chunk]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        return path
    except Exception:
        return ""


def search_parts(cid: str, keyword: str, limit: int = 6) -> list:
    """在已压缩掉的早期原文里按关键词回查，返回 [{batch,idx,role,text}]"""
    kw = (keyword or "").strip().lower()
    root = parts_dir(cid)
    if not kw or not os.path.isdir(root):
        return []
    hits = []
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(root, fn), encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            continue
        for i, m in enumerate(rows or []):
            text = str(m.get("content") or "")
            if kw not in text.lower():
                continue
            hits.append({"batch": fn[:-5], "idx": i,
                         "role": m.get("role") or "?",
                         "text": " ".join(text.split())[:400]})
            if len(hits) >= limit:
                return hits
    return hits


def rename(cid: str, title: str) -> None:
    d = load(cid)
    d["title"] = (title or "").strip()[:40] or title_of(d.get("messages"))
    d["renamed"] = True          # 手动改名标记：后续保存不再用首条消息覆盖
    try:
        with open(_path(cid), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def _first_user_text(msgs: list, limit: int = 200) -> str:
    """首条用户消息（会话检索用：标题只截 24 字，搜后面的内容得靠它）"""
    for m in msgs or []:
        if m.get("role") == "user" and str(m.get("content") or "").strip():
            return " ".join(str(m["content"]).split())[:limit]
    return ""


def list_chats() -> list:
    """[{id,title,updated,count,profile,first}]，按更新时间倒序"""
    out = []
    try:
        for fn in os.listdir(_dir()):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_dir(), fn), encoding="utf-8") as f:
                    d = json.load(f)
                out.append({"id": d.get("id") or fn[:-5],
                            "title": d.get("title") or "新对话",
                            "updated": float(d.get("updated") or 0),
                            "count": len(d.get("messages") or []),
                            "profile": dict(d.get("profile") or {}),
                            "first": _first_user_text(d.get("messages") or [])})
            except Exception:
                continue
    except Exception:
        return []
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


def latest_id() -> str:
    ls = list_chats()
    return ls[0]["id"] if ls else ""


def _enforce_limit() -> None:
    """会话太多时删最旧的（文件的 mtime 也一起看，避免 updated 缺失）"""
    try:
        chats = list_chats()
        for c in chats[MAX_CHATS:]:
            delete(c["id"])
    except Exception:
        pass
