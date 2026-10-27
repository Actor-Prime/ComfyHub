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


def search(nodes, keyword: str, limit=300, sort="hot") -> list:
    """按标题/作者/描述过滤（关键词空格分词，需全部命中）

    sort: "hot" = 下载量/星数降序（默认）；"name" = 名称升序
    """
    def _hot(n):
        return (int(n.get("_downloads") or 0), int(n.get("_stars") or 0))

    kw = (keyword or "").strip().lower()
    if not kw:
        if sort == "name":
            return sorted(nodes, key=lambda n: (n.get("title") or "").lower())[:limit]
        return sorted(nodes, key=_hot, reverse=True)[:limit]
    parts = kw.split()
    out = []
    for n in nodes:
        hay = " ".join([
            (n.get("title") or ""), (n.get("author") or ""),
            (n.get("description") or ""), (n.get("reference") or ""),
        ]).lower()
        if all(p in hay for p in parts):
            out.append(n)
    if sort == "hot":
        out.sort(key=_hot, reverse=True)
    else:
        out.sort(key=lambda n: (n.get("title") or "").lower())
    return out[:limit]


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


def web_url(node: dict) -> str:
    """git clone 地址 → 浏览器可打开的网页地址（点击插件名跳转用）

    - 去掉尾部 .git
    - git@host:path / ssh://git@ 统一转 https
    - 非 http(s) 或空 → 返回 ""（UI 不可点）
    """
    u = install_url(node).strip()
    if not u:
        return ""
    if u.startswith("git@"):                      # git@github.com:owner/repo.git
        host, _, path = u[4:].partition(":")
        u = f"https://{host}/{path}" if path else u
    elif u.startswith("ssh://git@"):              # ssh://git@github.com/owner/repo.git
        u = "https://" + u[len("ssh://git@"):]
    if not u.startswith(("http://", "https://")):
        return ""
    if u.endswith(".git"):
        u = u[:-4]
    return u

# ============================================================ 热度数据（官方 Registry）
REGISTRY_API = "https://api.comfy.org/nodes"
STATS_CACHE_TTL = 12 * 3600


def stats_cache_path() -> str:
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                     "ComfyHub", "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "registry_stats.json")


def _norm_repo(u: str) -> str:
    """把各种仓库地址归一化成 owner/repo（小写，去协议/.git/尾斜杠）"""
    import re as _re
    u = (u or "").strip().lower().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    m = _re.search(r"github\.com/([^/]+)/([^/]+)", u)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = _re.search(r"gitee\.com/([^/]+)/([^/]+)", u)
    if m:
        return f"gitee:{m.group(1)}/{m.group(2)}"
    return u


def load_registry_stats(force=False, progress=None, log=print) -> dict:
    """拉 ComfyUI 官方 Registry 的全部节点，建 {owner/repo: {downloads, stars, rating}} 索引

    官方 API 不支持排序，只能分页全量拉（约 62 页 / 50~60 秒），结果缓存 12 小时。
    progress: 可选回调 (done_pages, total_pages)
    """
    cp = stats_cache_path()
    if not force and os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                blob = json.load(f)
            if time.time() - float(blob.get("fetched_at") or 0) < STATS_CACHE_TTL:
                stats = blob.get("stats") or {}
                if stats:
                    return stats
        except Exception:
            pass

    def _get(url, timeout=25):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ComfyHub"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    stats = {}
    try:
        first = _get(f"{REGISTRY_API}?limit=100&page=1")
        total_pages = int(first.get("totalPages") or 1)
        pages = [first]
        log(f"[市场] 官方 Registry 共 {first.get('total')} 个节点 / {total_pages} 页")
        for p in range(2, total_pages + 1):
            try:
                pages.append(_get(f"{REGISTRY_API}?limit=100&page={p}"))
            except Exception:
                continue
            if progress:
                progress(p, total_pages)
        for pg in pages:
            for n in (pg.get("nodes") or []):
                key = _norm_repo(n.get("repository") or "")
                if not key:
                    continue
                d = int(n.get("downloads") or 0)
                s = int(n.get("github_stars") or 0)
                # 同名仓库取较大的热度（避免被低值覆盖）
                old = stats.get(key)
                if old is None or d > old.get("downloads", 0):
                    stats[key] = {"downloads": d, "stars": s,
                                  "rating": n.get("rating") or 0,
                                  "name": n.get("name") or ""}
    except Exception as e:
        log(f"[市场] 热度数据拉取失败（不影响使用）：{e}")
        if os.path.isfile(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    return (json.load(f).get("stats") or {})
            except Exception:
                pass
        return {}

    try:
        with open(cp, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "stats": stats}, f, ensure_ascii=False)
    except OSError:
        pass
    log(f"[市场] 热度索引就绪：{len(stats)} 个仓库")
    return stats


def attach_stats(nodes, stats: dict) -> list:
    """给插件列表附加 _downloads / _stars（原地修改，返回同一列表）"""
    if not stats:
        return nodes
    for n in nodes:
        key = _norm_repo(install_url(n))
        s = stats.get(key)
        n["_downloads"] = int(s["downloads"]) if s else 0
        n["_stars"] = int(s["stars"]) if s else 0
    return nodes


def hot_text(node: dict) -> str:
    """热度展示：下载量优先（纯数字，靠列头表达含义），无下载量时退回星数"""
    d = int(node.get("_downloads") or 0)
    s = int(node.get("_stars") or 0)
    if d >= 100_000_000:
        return f"{d / 100_000_000:.1f}亿"
    if d >= 10000:
        return f"{d / 10000:.1f}万"
    if d > 0:
        return f"{d}"
    if s > 0:
        return f"⭐{s}"
    return "—"
