# -*- coding: utf-8 -*-
"""GitHub API 同步工具——HTTPS git 被墙时的替代推送通道

背景：国内网络下 github.com 主站 git 端点常不可达（被墙/代理 502），
但 api.github.com 与 uploads.github.com 通常可走代理直连。
本工具用 git database API（blobs/trees/commits/refs）完成与 git push 等价的操作：
  · 分支同步：差异 blob 上传 → 建 tree → 建 commit（parent=远端 HEAD，fast-forward）→ 更新 ref
  · Release：建 tag + Release + 上传本地产物资产

Git blob SHA1 与 GitHub 完全一致 → 远端已有的 blob 直接引用、不重复上传。

Token 获取（二选一，勿写死在命令行历史里）：
  · set GITHUB_TOKEN=ghp_xxx        （临时，推荐）
  · git config --global gh.token ghp_xxx   （持久，本工具会读）

用法：
  # 同步本地分支到 GitHub（默认 master）
  python tools/gh_sync.py push [--branch master] [--dry-run]

  # 同步 + 打 tag + 建 Release + 传资产（一步到位）
  python tools/gh_sync.py release v0.8.1 --assets dist/a.exe dist/b.zip \
      --body docs/RELEASE_v0.8.1.md [--branch master]

依赖：仅标准库（urllib）。token 需 repo 权限（经典 PAT 勾 repo 即可）。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "Actor-Prime/ComfyHub"
API = "https://api.github.com"
UP = "https://uploads.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _token():
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not tok:
        try:
            tok = subprocess.run(
                ["git", "config", "--global", "gh.token"],
                capture_output=True, text=True).stdout.strip()
        except OSError:
            pass
    if not tok:
        sys.exit("❌ 未提供 token：set GITHUB_TOKEN=ghp_xxx 或 "
                 "git config --global gh.token ghp_xxx")
    return tok


TOKEN = None  # main 里初始化


def req(url, obj=None, method=None, raw=None, ctype="application/json"):
    data = json.dumps(obj).encode() if obj is not None else raw
    r = urllib.request.Request(url, data=data,
                               method=method or ("POST" if data else "GET"))
    r.add_header("Authorization", "token " + TOKEN)
    r.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            b = resp.read()
            return resp.status, json.loads(b) if b else None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", ROOT, *args],
                       capture_output=True)
    if r.returncode != 0:
        sys.exit(f"❌ git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def push_branch(branch, dry_run=False):
    """把本地 branch 同步到 GitHub（fast-forward，不覆盖远端独有提交）"""
    # 远端 HEAD
    st, ref = req(f"{API}/repos/{REPO}/git/ref/heads/{branch}")
    if st == 404:
        sys.exit(f"❌ 远端无 {branch} 分支（首推请先在 GitHub 建仓初始化）")
    if st != 200:
        sys.exit(f"❌ 读远端 ref: {st} {json.dumps(ref)[:200]}")
    remote_head = ref["object"]["sha"]

    # 远端完整 tree
    st, rt = req(f"{API}/repos/{REPO}/git/trees/{remote_head}?recursive=1")
    if st != 200 or rt.get("truncated"):
        sys.exit(f"❌ 拉远端 tree 失败或被截断: {st}")
    remote = {i["path"]: i["sha"] for i in rt["tree"] if i["type"] == "blob"}

    # 本地 tree
    out = git("ls-tree", "-r", branch, "--format=%(objectname) %(path)")
    local = [tuple(line.split(" ", 1)) for line in out.splitlines() if line]
    need = [(sha, p) for sha, p in local if remote.get(p) != sha]
    deleted = [p for p in remote if p not in {p2 for _, p2 in local}]
    print(f"本地 {len(local)} / 远端 {len(remote)} 文件；"
          f"变更 {len(need)}、删除 {len(deleted)}")
    if not need and not deleted:
        print("✅ 内容一致，无需推送")
        return remote_head
    if dry_run:
        for _, p in need[:20]:
            print("  ~", p)
        for p in deleted[:20]:
            print("  -", p)
        print("(dry-run 未推送)")
        return remote_head

    # 上传缺失/变更 blob
    blob_sha = {}
    for i, (sha, p) in enumerate(need, 1):
        src = git("cat-file", "blob", sha, binary=True)
        st, r = req(f"{API}/repos/{REPO}/git/blobs",
                    {"content": base64.b64encode(src).decode(),
                     "encoding": "base64"})
        if st != 201:
            sys.exit(f"❌ blob {p}: {st} {json.dumps(r)[:200]}")
        blob_sha[(sha, p)] = r["sha"]
        if i % 20 == 0 or i == len(need):
            print(f"  blob {i}/{len(need)}")

    # 全量 tree（远端已有 blob 直接引用；删除项自然不出现）
    entries = [{"path": p, "mode": "100644", "type": "blob",
                "sha": blob_sha.get((sha, p), sha)} for sha, p in local]
    st, tree = req(f"{API}/repos/{REPO}/git/trees", {"tree": entries})
    if st != 201:
        sys.exit(f"❌ tree: {st} {json.dumps(tree)[:300]}")

    # commit：parent=远端 HEAD → 更新 ref 即 fast-forward
    sha_local = git("rev-parse", branch).strip()
    msg = git("log", "-1", "--format=%B", branch)
    st, c = req(f"{API}/repos/{REPO}/git/commits",
                {"message": msg, "tree": tree["sha"], "parents": [remote_head]})
    if st != 201:
        sys.exit(f"❌ commit: {st} {json.dumps(c)[:300]}")
    st, r = req(f"{API}/repos/{REPO}/git/refs/heads/{branch}",
                {"sha": c["sha"]}, method="PATCH")
    if st != 200:
        sys.exit(f"❌ 更新 ref: {st} {json.dumps(r)[:200]}")
    print(f"✅ {branch} -> {c['sha'][:8]}（本地 {sha_local[:8]} 内容同步）")
    return c["sha"]


def make_release(tag, assets, body_file, branch, target_sha):
    """建 tag + Release + 传资产（已存在则复用）"""
    # tag
    st, r = req(f"{API}/repos/{REPO}/git/refs",
                {"ref": f"refs/tags/{tag}", "sha": target_sha})
    if st == 201:
        print(f"✅ tag {tag} 已建")
    elif st == 422:
        print(f"· tag {tag} 已存在")
    else:
        sys.exit(f"❌ tag: {st} {json.dumps(r)[:200]}")

    # Release
    body = ""
    if body_file and os.path.isfile(body_file):
        body = open(body_file, encoding="utf-8").read()
    st, rel = req(f"{API}/repos/{REPO}/releases",
                  {"tag_name": tag, "name": f"ComfyHub {tag}",
                   "body": body, "target_commitish": target_sha})
    if st == 422:
        _, lst = req(f"{API}/repos/{REPO}/releases?per_page=30")
        rel = next((x for x in lst if x["tag_name"] == tag), None)
        if not rel:
            sys.exit("❌ Release 已存在但取回失败")
        print(f"· Release {tag} 已存在 id={rel['id']}")
    elif st == 201:
        print(f"✅ Release {tag} 创建 id={rel['id']}")
    else:
        sys.exit(f"❌ Release: {st} {json.dumps(rel)[:300]}")

    # 资产（已同名先删后传，保证可重跑）
    exist = {a["name"]: a["id"] for a in rel.get("assets", [])}
    for f in assets:
        path = f if os.path.isabs(f) else os.path.join(ROOT, f)
        if not os.path.isfile(path):
            sys.exit(f"❌ 资产不存在: {path}")
        name = os.path.basename(path)
        if name in exist:
            req(f"{API}/repos/{REPO}/releases/assets/{exist[name]}",
                method="DELETE")
            print(f"· 旧资产 {name} 已删")
        st, r = req(f"{UP}/repos/{REPO}/releases/{rel['id']}/assets?name={name}",
                    raw=open(path, "rb").read(), method="POST",
                    ctype="application/octet-stream")
        if st != 201:
            sys.exit(f"❌ 资产 {name}: {st} {json.dumps(r)[:200]}")
        print(f"✅ 资产 {name} -> id {r['id']}")


def main():
    global TOKEN
    TOKEN = _token()
    ap = argparse.ArgumentParser(description="GitHub API 同步（git 被墙替代通道）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("push", help="同步本地分支到 GitHub")
    p1.add_argument("--branch", default="master")
    p1.add_argument("--dry-run", action="store_true")
    p2 = sub.add_parser("release", help="同步 + tag + Release + 资产")
    p2.add_argument("tag", help="如 v0.8.1")
    p2.add_argument("--assets", nargs="*", default=[])
    p2.add_argument("--body", help="Release 说明 markdown 文件")
    p2.add_argument("--branch", default="master")
    a = ap.parse_args()

    if a.cmd == "push":
        push_branch(a.branch, a.dry_run)
    else:
        sha = push_branch(a.branch)
        make_release(a.tag, a.assets, a.body, a.branch, sha)


if __name__ == "__main__":
    main()
