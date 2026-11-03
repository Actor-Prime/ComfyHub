# -*- coding: utf-8 -*-
"""updater.py - 版本更新（2026-11-04，用户定：A+B 静默安装 + 便携版自替换 + 每次启动检查）

来源：Gitee Releases（匿名 API 可读，实测可用）。
资产命名约定：
  · 安装版：ComfyHub_Setup_<版本>.exe   （静默：/S）
  · 便携版：ComfyHub_Portable_<版本>.zip（**发版时需额外上传**；缺了就提示手动下载）
  · 可选：SHA256SUMS.txt（有则校验，无则只做签名校验）

安全：验证 Authenticode 签名指纹（自签证书）——只保证"包是我们发的"，
     首次安装仍可能被 SmartScreen 拦一次，属预期（已在安装说明里写过）。

便携版自替换：写一个 updater.cmd，等主进程 PID 退出后 robocopy 覆盖（**保留 data 目录**），
再启动新版本并自删。主程序负责分离启动它并立刻退出，避免自己文件被占用。
"""
import hashlib
import json
import os
import subprocess
import ssl
import sys
import tempfile
import time
import urllib.request

from app.config import APP_VERSION, load_config, save_config

OWNER = "yzdydwdd"
REPO = "ComfyHub"
GITEE_API = f"https://gitee.com/api/v5/repos/{OWNER}/{REPO}/releases/latest"
GITHUB_SLUG = "Actor-Prime/ComfyHub"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_SLUG}/releases/latest"
RELEASES_URL = f"https://gitee.com/{OWNER}/{REPO}/releases"
SUMS_ASSET = "SHA256SUMS.txt"

# 源顺序：国内优先 Gitee，GitHub 作备用（可达性差时自动跳过）
SOURCES = (("gitee", "Gitee", GITEE_API, f"https://gitee.com/{OWNER}/{REPO}/releases"),
           ("github", "GitHub", GITHUB_API, f"https://github.com/{GITHUB_SLUG}/releases"))

# 内置我们的自签证书指纹（packaging/certs 里那张）；换真证书时改这里或 settings.update_sign_thumbprint
DEFAULT_THUMBPRINT = "7B50FEE7EF98AB41EA01204E9CDC92B63EB16E76"

SETUP_PREFIX = "comfyhub_setup_"
PORTABLE_PREFIX = "comfyhub_portable_"


# ---------------------------------------------------------------- 版本比较
def ver_key(s: str) -> tuple:
    """'v0.8.10' -> (0, 8, 10)。补丁位是两位数规则，绝不能字符串比较
    （'0.8.10' < '0.8.9' 在字符串下为真，是错的）。"""
    txt = str(s or "").strip().lstrip("vV")
    out = []
    for part in txt.split(".")[:3]:
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return ver_key(latest) > ver_key(current)


# ---------------------------------------------------------------- 环境
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_portable() -> bool:
    """便携模式 = 打包版且程序目录下有 data 子目录（与 app/paths.py 判定一致）"""
    if not is_frozen():
        return False
    return os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data"))


def exe_path() -> str:
    return os.path.abspath(sys.argv[0] if not is_frozen() else sys.executable)


def updates_dir() -> str:
    from app.paths import data_root
    d = os.path.join(data_root(), "updates")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 远端查询
def _ssl():
    ctx = ssl.create_default_context()
    return ctx


def _fetch_one(api: str, page: str, source_id: str, label: str,
               timeout: int = 20) -> dict:
    req = urllib.request.Request(api, headers={
        "User-Agent": "ComfyHub", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    tag = str(data.get("tag_name") or data.get("name") or "")
    assets = {}
    for a in data.get("assets") or []:
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or a.get("download_url") or "")
        if name and url:
            assets[name] = url
    return {"version": tag.lstrip("vV"), "tag": tag,
            "notes": str(data.get("body") or "")[:4000],
            "assets": assets, "page": str(data.get("html_url") or page),
            "published": str(data.get("created_at") or ""),
            "source": source_id, "source_label": label}


def fetch_latest(timeout: int = 20, only: str = "") -> dict:
    """按 SOURCES 顺序取最新 Release；单个源失败自动换下一个。
    返回 {version, tag, notes, assets:{name:url}, page, source, source_label}"""
    errors = []
    for sid, label, api, page in SOURCES:
        if only and sid != only:
            continue
        try:
            return _fetch_one(api, page, sid, label, timeout=timeout)
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}")
    raise OSError("所有下载源都不可用（" + "；".join(errors) + "）")


def parse_sha256sums(text: str) -> dict:
    """解析 `sha256  文件名` 每行一条（兼容 * 前缀与多余空格）"""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("*", " ").split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1]] = parts[0].lower()
    return out


def fetch_sha256sums(assets: dict, timeout: int = 20) -> dict:
    """有 SHA256SUMS.txt 就取回来解析；没有返回 {}（不阻断更新）"""
    url = ""
    for name, u in (assets or {}).items():
        if name.lower() == SUMS_ASSET.lower():
            url = u
            break
    if not url:
        return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ComfyHub"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl()) as r:
            return parse_sha256sums(r.read().decode("utf-8", "ignore"))
    except Exception:
        return {}


def pick_asset(assets: dict, portable: bool) -> tuple:
    """按运行形态挑资产：返回 (文件名, url) 或 ("", "")"""
    want = PORTABLE_PREFIX if portable else SETUP_PREFIX
    for name, url in sorted(assets.items()):
        low = name.lower()
        if low.startswith(want) and (low.endswith(".zip") if portable else low.endswith(".exe")):
            return name, url
    return "", ""


def check(cfg: dict = None, force: bool = False) -> dict:
    """检查更新。返回 {ok, has_update, ...}；任何网络异常都 ok=False 且不抛。"""
    cfg = cfg if cfg is not None else load_config()
    st = (cfg.get("settings") or {})
    portable = is_portable()
    try:
        info = fetch_latest()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "checked_at": time.time(), "portable": portable}
    latest = info["version"]
    has = is_newer(latest)
    name, url = pick_asset(info["assets"], portable)
    skipped = str(st.get("update_skipped_version") or "")
    if not force and skipped and not is_newer(latest, skipped):
        has = False
    sums = fetch_sha256sums(info.get("assets") or {}) if has and name else {}
    return {"ok": True, "has_update": has, "current": APP_VERSION, "latest": latest,
            "tag": info["tag"], "notes": info["notes"], "page": info["page"],
            "asset_name": name, "asset_url": url, "portable": portable,
            "skipped": skipped, "checked_at": time.time(),
            "source": info.get("source"), "source_label": info.get("source_label"),
            "sha256": sums.get(name, ""),
            "missing_asset": has and not url}


def remember_check(cfg: dict, ts: float = None) -> None:
    cfg.setdefault("settings", {})["update_last_check"] = float(ts or time.time())
    try:
        save_config(cfg)
    except Exception:
        pass


# ---------------------------------------------------------------- 下载与校验
def download(url: str, dest: str, progress=None, cancel=None,
             chunk: int = 262144, timeout: int = 30) -> dict:
    """带断点续传的下载；progress(已下载字节, 总字节) 回调，cancel 为可调用对象"""
    have = os.path.getsize(dest) if os.path.isfile(dest) else 0
    headers = {"User-Agent": "ComfyHub"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=_ssl())
    except urllib.error.HTTPError as e:
        if e.code == 416:                       # 已经下完了
            return {"ok": True, "path": dest, "size": have, "resumed": True}
        return {"ok": False, "error": f"HTTP {e.code}"}
    total = int(resp.headers.get("Content-Length") or 0) + have
    mode = "ab" if have and resp.status == 206 else "wb"
    if mode == "wb":
        have = 0
    done = have
    with open(dest, mode) as f:
        while True:
            if cancel is not None and cancel():
                return {"ok": False, "error": "已取消", "path": dest, "size": done}
            buf = resp.read(chunk)
            if not buf:
                break
            f.write(buf)
            done += len(buf)
            if progress:
                try:
                    progress(done, total)
                except Exception:
                    pass
    return {"ok": True, "path": dest, "size": done, "resumed": bool(have)}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blk)
    return h.hexdigest()


def verify_signature(path: str, expect: str = "") -> dict:
    """校验 Authenticode 指纹（PowerShell 取签名）。失败只警告，不阻断。"""
    want = (expect or DEFAULT_THUMBPRINT).replace(" ", "").upper()
    if not want:
        return {"ok": True, "skipped": True}
    try:
        ps = ("(Get-AuthenticodeSignature -FilePath '%s').SignerCertificate.Thumbprint"
              % path.replace("'", "''"))
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=30)
        got = (out.stdout or "").strip().replace(" ", "").upper()
    except Exception as e:
        return {"ok": True, "warned": True, "detail": f"无法校验签名：{e}"}
    if not got:
        return {"ok": True, "warned": True, "detail": "下载包没有签名（可能是旧版本包）"}
    if got != want:
        return {"ok": False, "error": f"签名指纹不符：{got[:16]}… != {want[:16]}…"}
    return {"ok": True, "thumbprint": got}


# ---------------------------------------------------------------- 应用更新
def _stop_instances(log=print) -> None:
    """更新前停掉所有 ComfyUI 实例，避免安装时文件被占用"""
    try:
        from app import proc_manager
        for fn in ("stop_all", "stop_all_instances"):
            f = getattr(proc_manager, fn, None)
            if callable(f):
                f()
                log("[更新] 已停止运行中的实例")
                return
    except Exception as e:
        log(f"[更新] 停止实例失败（继续）：{e}")


def apply_installer(setup_path: str, log=print) -> dict:
    """静默安装：先派一个等待脚本，等本进程真正退出后再跑安装器。

    为什么不直接启动安装器（2026-11-04 实测事故）：本进程还在跑 → ComfyHub.exe 被占用
    → 安装器覆盖失败 → 装成"新 _internal + 旧 exe"的半破状态，版本号还是旧的。
    等自己退出后再装，文件必然全部空闲。
    """
    if not os.path.isfile(setup_path):
        return {"ok": False, "error": f"安装包不存在：{setup_path}"}
    sig = verify_signature(setup_path)
    if not sig.get("ok"):
        return {"ok": False, "error": sig.get("error") or "签名校验失败"}
    _stop_instances(log)
    work = tempfile.mkdtemp(prefix="comfyhub_inst_", dir=updates_dir())
    cmd_path = os.path.join(work, "run_installer.cmd")
    log_path = os.path.join(updates_dir(), "update.log")
    script = f"""@echo off
chcp 65001 >nul
set "PID={os.getpid()}"
set "SETUP={setup_path}"
set "LOG={log_path}"
echo [%date% %time%] 等待主进程退出 >> "%LOG%"
:waitloop
tasklist /FI "PID eq %PID%" 2>nul | findstr /C:"%PID%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)
echo [%date% %time%] 启动静默安装 >> "%LOG%"
start "" "%SETUP%" /S
timeout /t 3 /nobreak >nul
del /f /q "%~f0" >nul 2>&1
"""
    try:
        with open(cmd_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(script)
        subprocess.Popen(["cmd", "/c", "start", "", "/min", cmd_path], shell=False)
    except Exception as e:
        return {"ok": False, "error": f"启动更新脚本失败：{e}"}
    return {"ok": True, "launched": True, "script": cmd_path,
            "warning": sig.get("detail")}


def make_portable_updater(zip_path: str, log=print) -> dict:
    """生成 updater.cmd：等主进程退出 → 解压覆盖（保留 data）→ 启动 → 自删"""
    if not os.path.isfile(zip_path):
        return {"ok": False, "error": f"便携包不存在：{zip_path}"}
    target = os.path.dirname(exe_path())
    exe_name = os.path.basename(exe_path())
    work = tempfile.mkdtemp(prefix="comfyhub_upd_", dir=updates_dir())
    cmd_path = os.path.join(work, "updater.cmd")
    log_path = os.path.join(updates_dir(), "update.log")
    script = f"""@echo off
chcp 65001 >nul
setlocal
set "PID={os.getpid()}"
set "TARGET={target}"
set "ZIP={zip_path}"
set "WORK={work}"
set "LOG={log_path}"
echo [%date% %time%] 等待主进程退出 >> "%LOG%"
:waitloop
tasklist /FI "PID eq %PID%" 2>nul | findstr /C:"%PID%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)
echo [%date% %time%] 解压新版本 >> "%LOG%"
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%WORK%\\new' -Force" >> "%LOG%" 2>&1
echo [%date% %time%] 覆盖程序目录（保留 data） >> "%LOG%"
robocopy "%WORK%\\new" "%TARGET%" /MIR /XD "%TARGET%\\data" /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
if exist "%TARGET%\\{exe_name}" (
    echo [%date% %time%] 启动新版本 >> "%LOG%"
    start "" "%TARGET%\\{exe_name}"
) else (
    echo [%date% %time%] 启动失败：找不到 {exe_name} >> "%LOG%"
)
timeout /t 2 /nobreak >nul
del /f /q "%~f0" >nul 2>&1
"""
    try:
        with open(cmd_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(script)
        subprocess.Popen(["cmd", "/c", "start", "", "/min", cmd_path], shell=False)
    except Exception as e:
        return {"ok": False, "error": f"启动更新脚本失败：{e}"}
    return {"ok": True, "script": cmd_path, "log": log_path}


def apply_update(asset_name: str, asset_url: str, progress=None, cancel=None,
                 log=print, expect_sha: str = "") -> dict:
    """下载 → 哈希校验 → 签名校验 → 按形态应用。调用方负责随后退出程序。"""
    dest = os.path.join(updates_dir(), asset_name)
    dl = download(asset_url, dest, progress=progress, cancel=cancel)
    if not dl.get("ok"):
        return dl
    if expect_sha:
        got = sha256_file(dest)
        if got.lower() != expect_sha.lower():
            try:
                os.remove(dest)          # 删掉坏包，下次重下
            except OSError:
                pass
            return {"ok": False, "path": dest,
                    "error": f"哈希校验失败（下载可能损坏）：{got[:12]}… ≠ {expect_sha[:12]}…，请重试"}
    sig = verify_signature(dest)
    if not sig.get("ok"):
        return {"ok": False, "error": sig.get("error") or "签名校验失败", "path": dest}
    if asset_name.lower().endswith(".zip"):
        r = make_portable_updater(dest, log=log)
        r["mode"] = "portable"
        return r
    r = apply_installer(dest, log=log)
    r["mode"] = "installer"
    r["path"] = dest
    return r


def human_size(n) -> str:
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
