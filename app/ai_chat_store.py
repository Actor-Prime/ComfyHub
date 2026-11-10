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


def title_of(msgs: list, fallback: str = "新对话") -> str:
    for m in msgs or []:
        if m.get("role") == "user" and str(m.get("content") or "").strip():
            t = " ".join(str(m["content"]).split())
            return t[:24] + ("…" if len(t) > 24 else "")
    return fallback


def save(cid: str, messages: list, title: str = "",
         summary: str = None, upto: int = None, skills=None,
         profile=None) -> None:
    """写一个会话；messages 只保留 role/content。

    summary / upto 为「上下文压缩」产物（2026-11-06）：
    传 None 表示沿用文件里已有的值（普通保存不会把摘要清掉）。
    skills 为本会话勾选的技能 id 列表（2026-11-11）：None 同样沿用旧值。
    profile 为本会话的「工作台设定」（2026-11-11）：主题 / 绑定实例 /
    默认工作流 / 默认模型。None 同样沿用旧值，{} 表示清空。
    """
    try:
        msgs = _clip(messages)
        now = time.time()
        path = _path(cid)
        created = now
        old_summary, old_upto, old_skills, old_profile = "", 0, [], {}
        old_title, old_renamed = "", False
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
            return d
    except Exception:
        pass
    return {"id": cid, "title": "新对话", "messages": [],
            "summary": "", "summary_upto": 0, "skills": [],
            "profile": {}, "updated": 0}


def delete(cid: str) -> None:
    try:
        os.remove(_path(cid))
    except Exception:
        pass


def rename(cid: str, title: str) -> None:
    d = load(cid)
    d["title"] = (title or "").strip()[:40] or title_of(d.get("messages"))
    d["renamed"] = True          # 手动改名标记：后续保存不再用首条消息覆盖
    try:
        with open(_path(cid), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def list_chats() -> list:
    """[{id,title,updated,count,profile}]，按更新时间倒序"""
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
                            "profile": dict(d.get("profile") or {})})
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
