# -*- coding: utf-8 -*-
"""插件市场：从 ComfyUI-Manager 官方清单拉取 6000+ 插件目录

数据源（多源轮换，任一可用即可）：
  1. jsDelivr CDN（国内通常可直连）
  2. jsDelivr fastly / gcore 备用节点
  3. ghproxy.net / gh-proxy.com 反代 raw.githubusercontent.com

缓存：%APPDATA%\\ComfyHub\\cache\\plugin_catalog.json（默认 12 小时有效）
清单字段：author / title / reference / files[] / install_type / description
"""
import json
import os
import ssl
import time
import urllib.request

SOURCES = [
    ("jsDelivr", "https://cdn.jsdelivr.net/gh/ltdrdata/ComfyUI-Manager@main/custom-node-list.json"),
    ("jsDelivr-fastly", "https://fastly.jsdelivr.net/gh/ltdrdata/ComfyUI-Manager@main/custom-node-list.json"),
    ("jsDelivr-gcore", "https://gcore.jsdelivr.net/gh/ltdrdata/ComfyUI-Manager@main/custom-node-list.json"),
    ("ghproxy", "https://ghproxy.net/https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/custom-node-list.json"),
    ("gh-proxy", "https://gh-proxy.com/https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/custom-node-list.json"),
]

CACHE_TTL = 12 * 3600        # 12 小时


def cache_path() -> str:
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                     "ComfyHub", "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "plugin_catalog.json")


def _ssl_ctx():
    ctx = ssl.create_default_context()
    # 部分镜像证书链不全，这里放宽（仅拉公开清单，无敏感数据）
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_one(url: str, timeout=25) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ComfyHub/0.7"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        raw = r.read()
    j = json.loads(raw.decode("utf-8", "ignore"))
    nodes = j.get("custom_nodes")
    if not isinstance(nodes, list):
        raise ValueError("清单结构异常")
    return nodes


def load_catalog(force=False, log=print) -> tuple:
    """返回 (nodes, meta)。优先读缓存；缓存过期或缺省时按源顺序拉取。

    nodes: [{author,title,reference,files,install_type,description}, ...]
    meta:  {source, fetched_at, count, cached}
    """
    cp = cache_path()
    if not force and os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                blob = json.load(f)
            age = time.time() - float(blob.get("fetched_at") or 0)
            nodes = blob.get("nodes") or []
            if nodes and age < CACHE_TTL:
                return nodes, {"source": blob.get("source", "cache"),
                               "fetched_at": blob.get("fetched_at"),
                               "count": len(nodes), "cached": True}
        except Exception:
            pass

    last_err = None
    for name, url in SOURCES:
        for attempt in (1, 2):
            try:
                log(f"[市场] 从 {name} 拉取插件清单…")
                nodes = _fetch_one(url)
                # 只保留有安装地址的
                nodes = [n for n in nodes
                         if (n.get("reference") or (n.get("files") or [None])[0])]
                try:
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump({"fetched_at": time.time(), "source": name,
                                   "nodes": nodes}, f, ensure_ascii=False)
                except OSError:
                    pass
                log(f"[市场] ✅ {name}：{len(nodes)} 个插件")
                return nodes, {"source": name, "fetched_at": time.time(),
                               "count": len(nodes), "cached": False}
            except Exception as e:
                last_err = f"{name}: {type(e).__name__} {str(e)[:60]}"
                log(f"[市场] {name} 失败（{attempt}/2）：{last_err}")
                time.sleep(0.6)
    # 全失败 → 退回过期缓存（有总比没有好）
    if os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                blob = json.load(f)
            nodes = blob.get("nodes") or []
            if nodes:
                log(f"[市场] ⚠️ 全部源失败，使用 {len(nodes)} 条过期缓存")
                return nodes, {"source": "stale-cache", "count": len(nodes),
                               "fetched_at": blob.get("fetched_at"), "cached": True}
        except Exception:
            pass
    raise RuntimeError(f"插件清单获取失败：{last_err}")


def search(nodes, keyword: str, limit=300) -> list:
    """按标题/作者/描述过滤（关键词空格分词，需全部命中）"""
    kw = (keyword or "").strip().lower()
    if not kw:
        # 无关键词：按标题排序取前 limit
        return sorted(nodes, key=lambda n: (n.get("title") or "").lower())[:limit]
    parts = kw.split()
    out = []
    for n in nodes:
        hay = " ".join([
            (n.get("title") or ""), (n.get("author") or ""),
            (n.get("description") or ""), (n.get("reference") or ""),
        ]).lower()
        if all(p in hay for p in parts):
            out.append(n)
            if len(out) >= limit:
                break
    return out


def install_url(node: dict) -> str:
    """取出可 git clone 的地址（reference 优先，其次 files[0]）"""
    ref = (node.get("reference") or "").strip()
    if ref:
        return ref
    files = node.get("files") or []
    return (files[0] or "").strip() if files else ""


def is_git_installable(node: dict) -> bool:
    u = install_url(node)
    return bool(u) and ("github.com" in u or "gitee.com" in u or "gitlab.com" in u)


def display_name(node: dict) -> str:
    return (node.get("title") or node.get("reference") or "（未命名）").strip()
