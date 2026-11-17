# -*- coding: utf-8 -*-
"""联网搜索（2026-11-15）

本地小模型（2B 级）的知识截止得早，新模型/新节点一律不知道，硬编就是瞎
编节点名。给它一个 web_search，让它至少能查到「MiniMax H3 需要多大显存」
这种事实。

搜索源按顺序尝试：
    1) Tavily   —— 有 Key 时首选（结构化 JSON，最稳）；Key 在设置里填
    2) Bing     —— 免 Key，抓 cn.bing.com 的结果页（实测国内直连可用）
    3) DuckDuckGo —— 兜底，html.duckduckgo.com（部分网络下不通）

只做「拿标题 + 链接 + 摘要」这一件事，不做正文抓取——正文动辄几百 KB，
塞进 2B 模型的上下文只会把真正的问题挤出去。需要细节让模型给用户看链接。
"""
import gzip
import html as _html
import json
import re
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip(s: str) -> str:
    """HTML 片段 → 纯文本（去标签、反转义、压空白）"""
    s = _TAG_RE.sub(" ", s or "")
    s = _html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def _get(url: str, timeout: int = 15, data: bytes = None,
         headers: dict = None) -> str:
    h = {"User-Agent": _UA,
         "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
         "Accept": "text/html,application/xhtml+xml,application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw.decode("utf-8", "ignore")


# ---------------------------------------------------------------- Tavily
def _tavily(query: str, limit: int, key: str, timeout: int) -> list:
    body = json.dumps({"api_key": key, "query": query,
                       "max_results": limit, "search_depth": "basic",
                       "include_answer": False}).encode("utf-8")
    txt = _get("https://api.tavily.com/search", timeout=timeout, data=body,
               headers={"Content-Type": "application/json"})
    data = json.loads(txt)
    out = []
    for r in (data.get("results") or [])[:limit]:
        out.append({"title": _strip(r.get("title") or ""),
                    "url": r.get("url") or "",
                    "snippet": _strip(r.get("content") or "")[:400]})
    return out


# ---------------------------------------------------------------- Bing
_BING_BLOCK = re.compile(r'<li class="b_algo".*?(?=<li class="b_algo"|</ol>)', re.S)
_BING_H2 = re.compile(r"<h2[^>]*>.*?<a[^>]+href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a>",
                      re.S)
_BING_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def _bing(query: str, limit: int, timeout: int) -> list:
    url = ("https://cn.bing.com/search?"
           + urllib.parse.urlencode({"q": query, "count": max(10, limit),
                                     "setlang": "zh-Hans"}))
    html = _get(url, timeout=timeout)
    out = []
    for blk in _BING_BLOCK.findall(html):
        m = _BING_H2.search(blk)
        if not m:
            continue
        href, title = m.group(1), _strip(m.group(2))
        if not href or "bing.com" in href or not title:
            continue
        snip = ""
        for p in _BING_P.findall(blk):
            t = _strip(p)
            if len(t) > 20:
                snip = t
                break
        out.append({"title": title[:160], "url": href, "snippet": snip[:400]})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- DuckDuckGo
_DDG_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _ddg(query: str, limit: int, timeout: int) -> list:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    html = _get(url, timeout=timeout,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    out = []
    for href, title, snip in _DDG_RE.findall(html)[:limit]:
        out.append({"title": _strip(title)[:160], "url": href,
                    "snippet": _strip(snip)[:400]})
    return out


# ---------------------------------------------------------------- 对外
def search(query: str, limit: int = 6, timeout: int = 15,
           tavily_key: str = "", engine: str = "") -> dict:
    """联网搜索。返回 {ok, engine, query, results[], error}

    引擎顺序可按 engine 参数锁定（"bing" / "tavily" / "ddg"），主要给测试用。
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query 不能为空", "results": []}
    limit = max(1, min(10, int(limit or 6)))
    order = [engine] if engine else (
        (["tavily"] if tavily_key else []) + ["bing", "ddg"])
    errors = []
    for eng in order:
        try:
            if eng == "tavily":
                if not tavily_key:
                    continue
                res = _tavily(q, limit, tavily_key, timeout)
            elif eng == "bing":
                res = _bing(q, limit, timeout)
            elif eng == "ddg":
                res = _ddg(q, limit, timeout)
            else:
                continue
            if res:
                return {"ok": True, "engine": eng, "query": q, "results": res}
            errors.append("%s: 无结果" % eng)
        except Exception as e:
            errors.append("%s: %s" % (eng, type(e).__name__))
    return {"ok": False, "query": q, "results": [],
            "error": "所有搜索源都失败（" + "；".join(errors) + "）"
                     "——网络受限时可让用户直接把文档/链接贴进来"}
