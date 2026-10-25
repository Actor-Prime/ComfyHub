# -*- coding: utf-8 -*-
"""Gitee Release 资产维护工具。

用途：
  1) 列出某 release 的全部附件（含 ID）
  2) 删除多余/错放的附件
  3) 新建 release 并上传本地产物

背景：Gitee 官方 MCP 只有 create/list release，没有附件管理；
     附件端点是非公开文档的 attach_files（上传=POST 多段表单，删除=DELETE）。

token 从 ~/.workbuddy/mcp.json 的 gitee 环境变量读取，不落盘不硬编码。

用法：
  python tools/gitee_release_mgr.py list <release_id>
  python tools/gitee_release_mgr.py delete <release_id> <attach_id> [<attach_id> ...]
  python tools/gitee_release_mgr.py mkrelease <tag> <title> <body_file> [target_commitish]
  python tools/gitee_release_mgr.py upload <release_id> <file> [<file> ...]
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from mimetypes import guess_type

OWNER = "yzdydwdd"
REPO = "ComfyHub"
API = f"https://gitee.com/api/v5/repos/{OWNER}/{REPO}/releases"


def _token():
    path = os.path.expanduser("~/.workbuddy/mcp.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["mcpServers"]["gitee"]["env"]["GITEE_ACCESS_TOKEN"]


def _req(url, data=None, method=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return r.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _multipart(fields, files):
    """构建 multipart/form-data body。files: {name: (filename, bytes)}"""
    boundary = "----ch" + str(int(time.time() * 1000))
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; "
                  f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for name, (fname, content) in files.items():
        ctype = guess_type(fname)[0] or "application/octet-stream"
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; "
                  f"name=\"{name}\"; filename=\"{fname}\"\r\n"
                  f"Content-Type: {ctype}\r\n\r\n".encode())
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def cmd_list(rid):
    st, data = _req(f"{API}/{rid}/attach_files?access_token={_token()}&per_page=100")
    if st != 200:
        print("FAIL", st, data)
        return 1
    print("total:", len(data))
    for a in data:
        print(a["id"], a["name"], a["size"])
    return 0


def cmd_delete(rid, ids):
    tok = _token()
    ok = fail = 0
    for aid in ids:
        st, data = _req(f"{API}/{rid}/attach_files/{aid}?access_token={tok}",
                        method="DELETE")
        if st in (200, 204):
            ok += 1
            print("deleted", aid)
        else:
            fail += 1
            print("FAIL", aid, st, str(data)[:200])
    print(f"done: ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


def cmd_mkrelease(tag, title, body_file, target="master"):
    with open(body_file, encoding="utf-8") as f:
        body = f.read()
    payload = json.dumps({
        "access_token": _token(),
        "tag_name": tag,
        "name": title,
        "body": body,
        "target_commitish": target,
        "prerelease": False,
    }).encode("utf-8")
    st, data = _req(API, data=payload, method="POST",
                    headers={"Content-Type": "application/json"})
    if st != 201:
        print("FAIL", st, str(data)[:300])
        return 1
    print("release id:", data["id"], "tag:", data["tag_name"])
    return 0


def cmd_upload(rid, files):
    tok = _token()
    for path in files:
        with open(path, "rb") as f:
            content = f.read()
        fname = os.path.basename(path)
        body, ctype = _multipart({}, {"file": (fname, content)})
        st, data = _req(f"{API}/{rid}/attach_files?access_token={tok}",
                        data=body, method="POST",
                        headers={"Content-Type": ctype})
        if st in (200, 201):
            print("uploaded", fname, "->", data.get("id"))
        else:
            print("FAIL", fname, st, str(data)[:300])
            return 1
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "list":
        return cmd_list(args[0])
    if cmd == "delete":
        return cmd_delete(args[0], args[1:])
    if cmd == "mkrelease":
        return cmd_mkrelease(*args)
    if cmd == "upload":
        return cmd_upload(args[0], args[1:])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
