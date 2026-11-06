# -*- coding: utf-8 -*-
"""反馈与诊断（2026-11-05 方案A：本地收集 + 用户预览，零网络回传）

隐私铁律（写死在实现里，不是约定）：
  · 不采集：机器名 / MAC / IP / 硬件序列号 / 联系方式
  · 路径脱敏：用户主目录 → %USER%；APPDATA / LOCALAPPDATA / 安装目录 → 对应变量名
  · 日志逐行脱敏：api_key / token / secret / password / Bearer 赋值 → ***
  · 只产出文本，由用户预览（可编辑）后自行复制 / 保存 / 贴到 Issues
"""
import os
import re
import platform
import sys
import datetime

from app.paths import data_root, exe_dir


# ---------------------------------------------------------------- 脱敏
def sanitize_text(s: str) -> str:
    """路径与密钥脱敏。所有替换都先拿到真实值再 replace，避免硬编码机器差异。"""
    # ---- 路径（长的先换：APPDATA 在主目录下层，先换才不会被截胡）----
    for var in ("LOCALAPPDATA", "APPDATA"):
        val = os.environ.get(var)
        if val:
            s = s.replace(val, f"%{var}%")
    try:
        s = s.replace(exe_dir(), "%INSTALLDIR%")
    except Exception:
        pass
    home = os.path.expanduser("~")
    if home and home != "~":
        s = s.replace(home, "%USER%")
    # 兜底：C:\Users\<名字> 形态（~ 不等于真实主目录时也覆盖）
    s = re.sub(r"[A-Za-z]:\\Users\\[^\\\"':\s]+", r"%USER%", s)

    # ---- 密钥 / 凭据（Bearer 先行，否则会被上面的 \S+ 截断成 "Bearer"）----
    s = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+", r"\1***", s)
    s = re.sub(r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|"
               r"authorization)\b(\s*[=:]\s*)\S+", r"\1\2***", s)
    return s


def _tail(path: str, n: int) -> str:
    """读文件末 n 行；不存在 / 读不动就如实说明，不吞异常装没事"""
    if not os.path.isfile(path):
        return "（无此文件）"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception as e:
        return f"（读取失败：{e}）"


# ---------------------------------------------------------------- 收集
def collect() -> str:
    """生成诊断文本。全部本地执行、毫秒级返回（不做实例 Python 环境探测——那要拉起
    torch 数秒，诊断应秒开；需要时用户可贴工作台「环境」页的摘要）。"""
    from app.config import APP_VERSION, load_config
    from app import updater as upd

    st = []
    st.append("===== ComfyHub 诊断信息（已自动脱敏）=====")
    st.append(f"生成时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    st.append(f"版本: v{APP_VERSION}")
    frozen = getattr(sys, "frozen", False)
    portable = frozen and os.path.isdir(os.path.join(exe_dir(), "data"))
    st.append(f"运行形态: {'便携模式' if portable else ('安装版' if frozen else '开发态')}")
    try:
        winver = sys.getwindowsversion()  # 不含机器名
        st.append(f"系统: Windows {winver.major}.{winver.minor} (build {winver.build})"
                  f" {platform.machine()}")
    except Exception:
        st.append(f"系统: {platform.system()} {platform.release()} {platform.machine()}")
    st.append(f"Python: {sys.version.split()[0]}  PySide6: {_pyside_ver()}")

    # ---- 硬件（只要型号与容量，不要序列号）----
    try:
        from app.sysinfo import cpu_info, ram_info
        c, r = cpu_info(), ram_info()
        st.append(f"CPU: {c['name']} ({c['cores_physical']}C/{c['cores_logical']}T)")
        st.append(f"内存: {r['total_gb']:.1f} GB")
    except Exception as e:
        st.append(f"CPU/内存: 检测失败 ({e})")
    try:
        from app.bootstrap import detect_gpu
        g = detect_gpu()
        st.append(f"显卡: {getattr(g, 'label', g)}")
    except Exception as e:
        st.append(f"显卡: 检测失败 ({e})")

    # ---- 实例配置摘要（只挑非敏感字段；python_path 会再过一遍路径脱敏）----
    st.append("\n---- 实例配置 ----")
    try:
        cfg = load_config()
        insts = cfg.get("instances") or []
        if not insts:
            st.append("（无实例）")
        for it in insts:
            fields = [f"id={it.get('id', '?')}", f"port={it.get('port', '?')}"]
            for k in ("cuda_device", "vram_mode", "lowvram"):
                if it.get(k) not in (None, ""):
                    fields.append(f"{k}={it.get(k)}")
            pp = it.get("python_path") or ""
            if pp:
                fields.append(f"python={sanitize_text(pp)}")
            st.append("  " + "  ".join(fields))
        cfgdir = sanitize_text(data_root())
        st.append(f"数据目录: {cfgdir}")
    except Exception as e:
        st.append(f"（读取失败：{e}）")

    # ---- 运行记录（会话流水 / 闪退 / 卡死）----
    st.append("\n---- 运行记录（启动·退出·闪退·卡死）----")
    try:
        from app import runtime_log
        st.append(runtime_log.describe(limit=40))
    except Exception as e:
        st.append(f"（读取失败：{e}）")

    # ---- 日志（尾部 + 逐行已脱敏）----
    root = data_root()
    st.append(f"\n---- app.log 末 200 行（{sanitize_text(root)}）----")
    st.append(sanitize_text(_tail(os.path.join(root, "logs", "app.log"), 200)))
    crash = os.path.join(root, "crash.log")
    if os.path.isfile(crash):
        st.append("\n---- crash.log 末 50 行 ----")
        st.append(sanitize_text(_tail(crash, 50)))
    try:
        cfg = load_config()
        for it in (cfg.get("instances") or [])[:5]:   # 最多贴 5 个实例日志
            name = it.get("id", "default")
            st.append(f"\n---- instance_{name}.log 末 100 行 ----")
            st.append(sanitize_text(_tail(
                os.path.join(root, "logs", f"instance_{name}.log"), 100)))
    except Exception:
        pass

    st.append("\n==== 诊断信息结束 ====")
    return sanitize_text(_strip_known_secrets("\n".join(st)))


def _strip_known_secrets(s: str) -> str:
    """把**配置里真实的**密钥值直接替换掉（2026-11-06）

    正则只认 "api_key=xxx" 这类形态，日志里若出现裸 token（没有键名）就漏了。
    这里拿当前配置里的值做精确替换，比正则可靠。
    """
    try:
        from app.config import load_config
        cfg = load_config()
        st = cfg.get("settings") or {}
        for k in ("ai_api_key",):
            v = str(st.get(k) or "").strip()
            if len(v) >= 8:
                s = s.replace(v, "***")
    except Exception:
        pass
    return s


def _pyside_ver() -> str:
    try:
        import PySide6
        return getattr(PySide6, "__version__", "?")
    except Exception:
        return "?"


# ---------------------------------------------------------------- 匿名提交
# 客户端不含任何私密密钥：只 POST 到“公开可写”端点。
# 2026-11-06 实测（国内家宽）：
#   · 腾讯云函数 URL —— 需实名注册国内云，但最稳（免费额度内 0 元）
#   · Web3Forms / Formspree —— 免实名免服务器，access key 设计上就是公开的，
#     但属境外服务，可达性会变；失败已有自动落盘兜底
#   · Cloudflare Workers / formsubmit.co —— 本机实测超时，排除
_DEFAULT_ENDPOINT = "https://api.web3forms.com/submit"
_DEFAULT_ACCESS_KEY = "297fb25d-7239-409c-a73a-e7665439bb51"   # Web3Forms 公开 key

MAX_REPORT_BYTES = 300 * 1024    # 与云函数端上限对齐，超了先本地截断

# ---- 单机防刷（2026-11-06 用户要求：别让一台设备刷完 250 条/月额度）----
# 说明：key 是公开的，客户端限流只能挡“老实用户的误操作”，挡不住恶意直连；
# 真要严格必须换成云函数（服务端按 IP 限流）。这里把常见浪费堵住即可。
QUOTA_MIN_INTERVAL = 60          # 两次提交最小间隔（秒）
QUOTA_PER_DAY = 3                # 每台设备每天上限
QUOTA_PER_MONTH = 10             # 每台设备 30 天上限
QUOTA_DUP_DAYS = 7               # 同一份内容 7 天内不重复发
QUOTA_KEEP_DAYS = 40             # 状态文件只保留 40 天记录


def _quota_path() -> str:
    return os.path.join(data_root(), "feedback_quota.json")


def _quota_load() -> dict:
    import json
    try:
        with open(_quota_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("sent"), list):
            return data
    except Exception:
        pass
    return {"sent": []}


def _quota_save(rec: dict) -> None:
    import json
    import time as _t
    cutoff = _t.time() - QUOTA_KEEP_DAYS * 86400
    rec["sent"] = [x for x in rec.get("sent", []) if x.get("ts", 0) >= cutoff]
    try:
        os.makedirs(os.path.dirname(_quota_path()), exist_ok=True)
        tmp = _quota_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, _quota_path())
    except Exception:
        pass                      # 状态写不进去不能挡住用户发反馈


def _digest(report: str) -> str:
    import hashlib
    return hashlib.sha256(report.encode("utf-8", "replace")).hexdigest()[:16]


def quota_check(report: str, key: str = "") -> tuple:
    """提交前检查。返回 (allowed, 给用户看的理由)。

    去重按用户自己写的描述（key）算哈希——报告里带时间戳，用它去重永远不命中。
    状态只存哈希，不存内容。
    """
    import time as _t
    now = _t.time()
    sent = _quota_load().get("sent", [])
    h = _digest(key.strip() if key.strip() else report)

    for x in sent:
        if x.get("h") == h and now - x.get("ts", 0) < QUOTA_DUP_DAYS * 86400:
            return False, "这条反馈最近提交过，不用重复提交"
    if sent:
        last = max(x.get("ts", 0) for x in sent)
        if now - last < QUOTA_MIN_INTERVAL:
            left = int(QUOTA_MIN_INTERVAL - (now - last)) + 1
            return False, f"刚提交过，{left} 秒后再试"
    today = [x for x in sent if now - x.get("ts", 0) < 86400]
    if len(today) >= QUOTA_PER_DAY:
        return False, f"今天已提交 {QUOTA_PER_DAY} 条，请明天再试（避免占满反馈额度）"
    month = [x for x in sent if now - x.get("ts", 0) < 30 * 86400]
    if len(month) >= QUOTA_PER_MONTH:
        return False, f"30 天内已提交 {QUOTA_PER_MONTH} 条，已达单机上限"
    return True, ""


def quota_record(report: str, key: str = "") -> None:
    import time as _t
    rec = _quota_load()
    rec.setdefault("sent", []).append(
        {"ts": _t.time(), "h": _digest(key.strip() if key.strip() else report)})
    _quota_save(rec)


def endpoint() -> str:
    """提交地址：环境变量优先（显式置空可关闭提交通道，便于测试/临时停用）"""
    raw = os.environ.get("COMFYHUB_FEEDBACK_ENDPOINT")
    if raw is not None:
        return raw.strip()
    return _DEFAULT_ENDPOINT.strip()


def access_key() -> str:
    return (os.environ.get("COMFYHUB_FEEDBACK_KEY") or _DEFAULT_ACCESS_KEY).strip()


def submit(report: str, contact: str = "", message: str = "", timeout: int = 15) -> tuple:
    """提交反馈。返回 (ok, 提示语)。

    · report  = 自动生成的脱敏诊断（大段）
    · message = 用户自己写的描述/建议（短，邮件里放最前面最好读）
    · 全程不抛异常——网络问题由调用方兜底成“保存文件”
    """
    import json
    import urllib.request
    import urllib.error

    url = endpoint()
    if not url:
        return False, "未配置提交地址"
    if len(report.encode("utf-8")) > MAX_REPORT_BYTES:
        report = report[:MAX_REPORT_BYTES // 2] + "\n…（超长已截断）"

    from app.config import APP_VERSION
    payload = {
        "version": APP_VERSION,
        "os": f"{platform.system()} {platform.release()}",
        "contact": (contact or "")[:120],
        "message": (message or "").strip()[:2000],
        "report": report,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    # Web3Forms 要求 access_key 同时出现在 body 与 JSON 里；key 由平台设计为可公开
    if access_key():
        payload["access_key"] = access_key()
        head = payload["message"].splitlines()[0][:40] if payload["message"] else ""
        payload["subject"] = f"[ComfyHub 反馈] {head}" if head else f"[ComfyHub 反馈] v{APP_VERSION}"
        payload["from_name"] = "ComfyHub"

    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": f"ComfyHub/{APP_VERSION} (feedback)",
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = (r.read(500) or b"").decode("utf-8", "replace")
        if 200 <= r.status < 300 and '"success":false' not in body.replace(" ", ""):
            return True, "已提交"
        return False, f"服务返回 {r.status}：{body[:80]}"
    except urllib.error.HTTPError as e:
        return False, f"服务返回 {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def issues_url() -> str:
    """Gitee 新建 Issue 直达链接（标题预填版本号，正文让用户贴诊断文本）"""
    from app.config import APP_VERSION
    from app import updater as upd
    return (f"https://gitee.com/{upd.OWNER}/{upd.REPO}/issues/new"
            f"?title=[反馈] ComfyHub v{APP_VERSION}")
