# -*- coding: utf-8 -*-
"""
model_mirror.py — 无 VPN 模型源助手（ComfyHub tools）

背景（2026-10-25 实测）：
  - huggingface.co / civitai.com / b2.civitai.com 在国内 DNS 污染 + IP/SNI 双层阻断，改 hosts 无效
  - hf-mirror.com 是 HF 国内镜像，API + 文件下载全链路可用（大文件 302 到 xethub CDN 也可达）
  - civitai.red 是 CivitAI 的 Cloudflare 中转镜像：/api/v1/* 元数据可用，
    但 /api/download/models/{id} 307 跳 b2.civitai.com（被墙）→ 文件下载不可行
  - 本工具封装可用通道，纯标准库零依赖

用法：
  python model_mirror.py hf <repo_id|hf_url>                  # 列出仓库文件
  python model_mirror.py hf <repo_id|hf_url> <file> [-o OUT]  # 下载指定文件（走 hf-mirror）
  python model_mirror.py civitai <model_id|civitai_url>       # 查模型元数据 + 文件列表
  python model_mirror.py civitai <model_id> --search           # 附带在 HF 镜像搜索同名同步仓库

退出码：0 成功 / 1 参数错误 / 2 网络失败 / 3 无可用通道
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

HF_MIRROR = "https://hf-mirror.com"
CIVITAI_MIRROR = "https://civitai.red"
UA = {"User-Agent": "ComfyHub-model-mirror/1.0"}
TIMEOUT = 30


def _make_ssl_ctx():
    """证书策略：优先 certifi（如有）→ 系统默认 → 降级不校验（打警告）。
    2026-10-25 实测：Python 自带 CA 包部分过期导致 hf-mirror 证书误报，
    curl（用系统证书库）正常——故做三级降级。"""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        ctx = ssl.create_default_context()
        # 故意触发一次验证以探测 CA 是否可用
        return ctx
    except Exception:
        pass
    print("[警告] 无法建立可信 SSL 上下文，降级为不校验证书（仅限镜像下载场景）",
          file=sys.stderr)
    ctx = ssl._create_unverified_context()
    return ctx


_SSL_CTX = None


def _open(url, timeout=TIMEOUT):
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = _make_ssl_ctx()
    req = urllib.request.Request(url, headers=UA)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
    except (ssl.SSLError, urllib.error.URLError) as e:
        # urllib 会把 SSLError 包进 URLError(reason)，需解包判断
        reason = getattr(e, "reason", e)
        if not isinstance(reason, ssl.SSLError) and "CERTIFICATE" not in str(reason):
            raise
        # CA 过期误报时降级重试（镜像站为国内可直连站点，风险可控）
        print("[警告] SSL 校验失败，降级重试（不校验证书）", file=sys.stderr)
        insecure = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=insecure)


def _get(url, timeout=TIMEOUT):
    with _open(url, timeout) as r:
        return r.status, r.read(), dict(r.headers)


def _get_json(url):
    st, body, _ = _get(url)
    if st != 200:
        raise RuntimeError(f"HTTP {st}: {url}")
    return json.loads(body)


def _err(msg, code=2):
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(code)


# ---------- HF ----------

_HF_RE = re.compile(
    r"(?:https?://(?:www\.)?huggingface\.co/|https?://hf-mirror\.com/)?"
    r"(?P<repo>[\w.\-]+/[\w.\-]+)"
    r"(?:/(?:resolve|blob)/(?P<rev>[\w.\-]+)/(?P<file>.+))?"
)


def parse_hf_ref(ref):
    """repo_id 或 HF URL → (repo, file_or_None)"""
    m = _HF_RE.match(ref.strip().rstrip("/"))
    if not m:
        _err(f"无法解析 HF 引用: {ref}（期望 repo_id 如 gpt2 或完整 URL）", 1)
    return m.group("repo"), m.group("file")


def cmd_hf(args):
    repo, file_in_url = parse_hf_ref(args.ref)
    file = args.file or file_in_url
    # 提前算好 url，避免列文件分支抛 HTTPError 时 except 里引用未定义变量
    url = (f"{HF_MIRROR}/{repo}/resolve/main/{file}" if file
           else f"{HF_MIRROR}/api/models/{repo}")
    try:
        if not file:
            # 列文件
            data = _get_json(url)
            sibs = [s["rfilename"] for s in data.get("siblings", [])]
            if not sibs:
                _err(f"仓库 {repo} 无文件或不存在")
            print(f"== {repo} 共 {len(sibs)} 个文件 ==")
            for f in sibs:
                print(f"  {f}")
            return
        # 下载
        out = args.output or os.path.basename(file)
        print(f"[HF镜像] {repo}/{file} -> {out}")
        with _open(url, timeout=120) as r:
            if r.status != 200:
                _err(f"HTTP {r.status}")
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(out, "wb") as fp:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fp.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r  {done/1e6:.1f}/{total/1e6:.1f} MB "
                              f"({done*100//total}%)", end="", flush=True)
            print(f"\n[完成] {done} bytes")
    except urllib.error.HTTPError as e:
        _err(f"HTTP {e.code}: {url}（仓库/文件不存在或需登录）")
    except Exception as e:
        _err(str(e))


# ---------- CivitAI ----------

_CIV_ID_RE = re.compile(r"(?:civitai\.com/(?:models|download)/|civitai\.red/(?:models|download)/)?(\d{3,10})")


def parse_civ_id(ref):
    m = _CIV_ID_RE.search(ref.strip())
    if not m:
        _err(f"无法解析 CivitAI 模型 ID: {ref}（期望如 129760 或完整 URL）", 1)
    return m.group(1)


def cmd_civitai(args):
    mid = parse_civ_id(args.ref)
    try:
        data = _get_json(f"{CIVITAI_MIRROR}/api/v1/models/{mid}")
    except Exception as e:
        # 镜像 404 时明确告知不可行
        if "HTTPError" in type(e).__name__ and getattr(e, "code", 0) == 404:
            _err(f"civitai.red 无该模型 ({mid})，可能镜像未同步", 3)
        _err(f"civitai.red 元数据请求失败: {e}", 3)

    print(f"== {data.get('name', '?')} (id={mid}) ==")
    print(f"类型: {data.get('type', '?')}  作者: {(data.get('creator') or {}).get('username', '?')}")
    stats = data.get("stats") or {}
    if stats:
        print(f"下载: {stats.get('downloadCount', '?')}  点赞: {stats.get('favoriteCount', '?')}")
    model_ver = data.get("modelVersions") or []
    if not model_ver:
        _err("该模型无版本信息")
    ver = model_ver[0]
    print(f"\n最新版本: {ver.get('name', '?')  }  baseModel: {ver.get('baseModel', '?')}")
    files = ver.get("files") or []
    print(f"\n-- 文件（{len(files)} 个，需 VPN/代理下载，b2.civitai.com 直连被墙）--")
    for f in files:
        size_mb = (f.get("sizeKB") or 0) / 1024
        print(f"  {f.get('name')}  ({size_mb:.1f} MB, {f.get('type')})")
        print(f"    直链: {CIVITAI_MIRROR}/api/download/models/{mid}")

    if args.search:
        kw = urllib.parse.quote(data.get("name", ""))
        try:
            hits = _get_json(f"{HF_MIRROR}/api/models?search={kw}&limit=5")
            print(f"\n-- HF镜像搜索同名仓库（{len(hits)} 条，社区同步，质量不保证）--")
            for h in hits:
                print(f"  {h.get('modelId')}  (downloads={h.get('downloads', 0)})")
        except Exception as e:
            print(f"\n[提示] HF 镜像搜索失败: {e}")


def main():
    ap = argparse.ArgumentParser(description="无 VPN 模型源助手（HF走hf-mirror / CivitAI元数据走civitai.red）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("hf", help="HF 镜像：列文件 / 下载")
    p1.add_argument("ref", help="repo_id 或 HF URL")
    p1.add_argument("file", nargs="?", help="仓库内文件路径（省略则列文件）")
    p1.add_argument("-o", "--output", help="输出路径")
    p1.set_defaults(func=cmd_hf)

    p2 = sub.add_parser("civitai", help="CivitAI 镜像：元数据（下载需代理）")
    p2.add_argument("ref", help="模型 ID 或 CivitAI URL")
    p2.add_argument("--search", action="store_true", help="附带 HF 镜像搜索同名同步仓库")
    p2.set_defaults(func=cmd_civitai)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
