# -*- coding: utf-8 -*-
"""双端 Release 配额整理：只保留最新 N 个版本的安装包（铁律，默认 N=10）。

背景（2026-11-22 Starmore 定）：Gitee/GitHub 的 Release 资产有总量配额，几十个
版本的安装包（每个 36~85MB）很快把配额吃光。规则是**版本说明永久保留、安装包
只留最新 10 个**：老版本仍能在 Release 页面看到改了什么的说明与 tag，只是不再
提供 exe 下载（需要时可从本地 dist\\ 找回或重传）。

判据（刻意从严，防误删）：
  · 只删「安装包类」附件 —— .exe / .zip / 名字含 portable；
  · .md / .txt / .png / .jpg 等说明、校验、截图**一律保留**；
  · 最新 N 个版本（按版本号数值排序，不是时间）的附件**一个不动**；
  · Gitee 的 `vX.zip` / `vX.tar.gz` 是系统自动生成的源码包，只出现在 releases
    列表接口里，不会出现在 attach_files 端点 —— 本工具走 attach_files，天然不碰
    它们（也删不掉，不占用户配额）。

用法：
  python tools/prune_release_assets.py                # 试运行，只列计划删除的
  python tools/prune_release_assets.py --apply        # 真正删除（不可逆）
  python tools/prune_release_assets.py --keep 5 --apply
  python tools/prune_release_assets.py --audit        # 只看两端版本数与占用

token 沿用既有发版工具的口径，不落盘：
  Gitee  ← ~/.workbuddy/mcp.json 的 mcpServers.gitee.env.GITEE_ACCESS_TOKEN
  GitHub ← git config --global gh.token（或环境变量 GITHUB_TOKEN）
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

KEEP = 10                                    # 铁律：只保留最新 10 个版本的安装包
GITEE_OWNER, GITEE_REPO = "ActorPrime", "ComfyHub"
GH_OWNER, GH_REPO = "Actor-Prime", "ComfyHub"
GITEE_API = f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}/releases"
GH_API = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/releases"

PKG_HINT = ("portable",)                     # 名字含这些的一律算安装包
KEEP_EXT = (".md", ".txt", ".png", ".jpg", ".jpeg", ".json", ".pdf")


# ---------------------------------------------------------------- 基础
def gitee_token():
    with open(os.path.expanduser("~/.workbuddy/mcp.json"), encoding="utf-8") as f:
        return json.load(f)["mcpServers"]["gitee"]["env"]["GITEE_ACCESS_TOKEN"]


def gh_token():
    try:
        t = subprocess.check_output(["git", "config", "--global", "--get", "gh.token"],
                                    text=True, stderr=subprocess.DEVNULL).strip()
        if t:
            return t
    except Exception:
        pass
    return os.environ.get("GITHUB_TOKEN", "").strip()


def req(url, method="GET", headers=None, data=None):
    r = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                      # 断网/超时：当失败处理，不中断整轮
        return 0, str(e)


def vkey(tag):
    """v0.8.36 → (0, 8, 36)：按数字排序，避免按字符串排成 v0.8.9 > v0.8.36"""
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


def is_pkg(name):
    """是不是安装包类附件（可删）"""
    low = (name or "").lower()
    if low.endswith(KEEP_EXT):
        return False
    if any(h in low for h in PKG_HINT):
        return True
    return low.endswith(".exe") or low.endswith(".zip")


def mb(n):
    return "%.1fMB" % ((n or 0) / 1048576.0)


# ---------------------------------------------------------------- Gitee
def gitee_releases(tok):
    out, page = [], 1
    while True:
        st, batch = req(f"{GITEE_API}?access_token={tok}&per_page=100&page={page}")
        if st != 200 or not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    out.sort(key=lambda r: vkey(r.get("tag_name")), reverse=True)
    return out


def gitee_files(rid, tok):
    st, data = req(f"{GITEE_API}/{rid}/attach_files?access_token={tok}&per_page=100")
    return data if st == 200 and isinstance(data, list) else None


def prune_gitee(keep, apply_):
    tok = gitee_token()
    rels = gitee_releases(tok)
    print("=" * 76)
    print("[Gitee] %d 个 Release，保留最新 %d 个版本的安装包" % (len(rels), keep))
    print("=" * 76)
    n_del = n_fail = n_seen = 0
    freed = 0
    for i, rel in enumerate(rels, 1):
        tag, rid = rel.get("tag_name"), rel.get("id")
        if i <= keep:
            continue
        files = gitee_files(rid, tok)
        if files is None:
            print("  [warn] %-10s 取附件失败，跳过" % tag)
            continue
        targets = [a for a in files if is_pkg(a.get("name", ""))]
        if not targets:
            continue
        print("  %-10s (id=%s) 说明类 %d 个保留，安装包 %d 个："
              % (tag, rid, len(files) - len(targets), len(targets)))
        for a in targets:
            n_seen += 1
            print("      - %s  %s  id=%s" % (a.get("name"), mb(a.get("size")), a.get("id")))
            if not apply_:
                freed += a.get("size") or 0
                continue
            st, _ = req(f"{GITEE_API}/{rid}/attach_files/{a.get('id')}"
                        f"?access_token={tok}", method="DELETE")
            if st in (200, 204):
                n_del += 1
                freed += a.get("size") or 0
                print("        ✔ 已删除")
            else:
                n_fail += 1
                print("        ✘ 失败 %s" % st)
    return {"releases": len(rels), "planned": n_seen, "done": n_del,
            "fail": n_fail, "bytes": freed}


# ---------------------------------------------------------------- GitHub
def gh_releases(tok):
    if not tok:
        print("[GitHub] 无 token（git config --global gh.token 或 GITHUB_TOKEN），跳过")
        return []
    out, page = [], 1
    while True:
        st, batch = req(f"{GH_API}?per_page=100&page={page}",
                        headers={"Authorization": "token " + tok,
                                 "Accept": "application/vnd.github+json"})
        if st != 200 or not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    out.sort(key=lambda r: vkey(r.get("tag_name")), reverse=True)
    return out


def prune_github(keep, apply_):
    tok = gh_token()
    rels = gh_releases(tok)
    if not rels:
        return {"releases": 0, "planned": 0, "done": 0, "fail": 0, "bytes": 0}
    print()
    print("=" * 76)
    print("[GitHub] %d 个 Release，保留最新 %d 个版本的安装包" % (len(rels), keep))
    print("=" * 76)
    n_del = n_fail = n_seen = 0
    freed = 0
    for i, rel in enumerate(rels, 1):
        tag = rel.get("tag_name")
        if i <= keep:
            continue
        assets = rel.get("assets") or []
        targets = [a for a in assets if is_pkg(a.get("name", ""))]
        if not targets:
            continue
        print("  %-10s 说明类 %d 个保留，安装包 %d 个："
              % (tag, len(assets) - len(targets), len(targets)))
        for a in targets:
            n_seen += 1
            print("      - %s  %s  id=%s" % (a.get("name"), mb(a.get("size")), a.get("id")))
            if not apply_:
                freed += a.get("size") or 0
                continue
            st, _ = req(f"{GH_API}/assets/{a.get('id')}", method="DELETE",
                        headers={"Authorization": "token " + tok,
                                 "Accept": "application/vnd.github+json"})
            if st in (200, 204):
                n_del += 1
                freed += a.get("size") or 0
                print("        ✔ 已删除")
            else:
                n_fail += 1
                print("        ✘ 失败 %s" % st)
    return {"releases": len(rels), "planned": n_seen, "done": n_del,
            "fail": n_fail, "bytes": freed}


# ---------------------------------------------------------------- 入口
def main(argv):
    keep = KEEP
    apply_ = "--apply" in argv
    audit = "--audit" in argv
    if "--keep" in argv:
        try:
            keep = max(1, int(argv[argv.index("--keep") + 1]))
        except Exception:
            print("--keep 需要一个数字，例如 --keep 10")
            return 2

    mode = "试运行（只列不删，加 --apply 才真删）" if not apply_ else "执行删除（不可逆）"
    print("模式：%s　保留：最新 %d 个版本" % (mode, keep))
    print()

    dry = not apply_ or audit
    g = prune_gitee(keep, not dry)
    h = prune_github(keep, not dry)

    print()
    print("-" * 76)
    for name, r in (("Gitee ", g), ("GitHub", h)):
        verb = "计划删除" if dry else "已删除"
        num = r["planned"] if dry else r["done"]
        print("%s：%d 个 Release，%s %d 个安装包（%s）%s"
              % (name, r["releases"], verb, num, mb(r["bytes"]),
                 "，失败 %d" % r["fail"] if r["fail"] else ""))
    if dry:
        print("\n以上为试运行。确认无误后：python tools/prune_release_assets.py --apply")
    return 1 if (g["fail"] or h["fail"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
