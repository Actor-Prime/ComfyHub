# -*- coding: utf-8 -*-
"""远程产物清理（2026-12-01，0.8.40 T07）

**本模块不含任何 UI**（禁止 import PySide6）：统计 / 清理 / 自动策略全在纯逻辑里，
QThread 包装在 workers.py（`CleanupStatsWorker` / `CleanupWorker`），界面在
settings_page「远程产物」分组。

背景（PRD E）：远程实例的图都下到
`%LOCALAPPDATA%\\ComfyHub\\remote\\<实例名>`（见 `api_base.remote_output_dir`），
只进不出，几个月后能吃掉几十 GB，而用户在界面上看不到任何数字。

四条硬约束：
  ① **统计绝不扫在 UI 线程**：几十 GB 的 `os.walk` 会卡死界面（0.8.2x 卡顿事故）；
     这里只读 5 分钟缓存，缓存过期先返回「统计中…」，扫描交给后台线程。
  ② **删的是本地副本**：远端原图不受影响，文案必须说清（PRD 5.5.3）。
  ③ **跳过正在用的文件**：`< 3600s` 内改过的、`.part` 后缀的一律跳过；
     删不掉（被占用）只计数，不抛异常、不弹窗（边界 #17/#18）。
  ④ **自动清理默认关**：用户不开就完全不跑（PRD 5.5.2）。
"""
import os
import threading
import time

from app.log import log_info

CACHE_TTL = 300.0            # 统计缓存 5 分钟（PRD 5.5.1）
AUTO_THROTTLE = 600.0        # 自动清理 10 分钟节流（PRD 5.5.2 / ARCH 验收 ⑥）
SKIP_RECENT = 3600.0         # 自动清理跳过最近 1 小时用过的文件（边界 #18）
TOAST_MIN_BYTES = 500 * 1024 * 1024     # 单次释放 ≥ 500 MB 才弹 toast
DEFAULT_DAYS = 30
DEFAULT_MAX_GB = 10

_cache = {}                  # {abs_dir: (ts, {files, bytes, exists, error})}
_cache_lock = threading.RLock()
_last_auto = 0.0             # time.monotonic() 的时间戳，只做节流
_auto_lock = threading.Lock()

# 自动清理结果的上报钩子（主窗装一个：控制台记一行 + ≥500MB 弹 toast）
# 纯逻辑模块不 import Qt，所以这里只留回调位（ARCH §9 层次约束）
_reporter = None


def set_reporter(fn):
    """注册自动清理结果的上报函数：`fn(result_dict)`；传 None 取消"""
    global _reporter
    _reporter = fn


def _now() -> float:
    return time.time()


def _abs(path: str) -> str:
    return os.path.abspath(str(path or ""))


# ---------------------------------------------------------------- 体积格式化
def human_size(n) -> str:
    """0 → "0 B"；1.2 GB 这类显示（PRD 表格要的是人能读的数）"""
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    if v < 1024:
        return f"{int(v)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        v /= 1024.0
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if v >= 100 else f"{v:.1f} {unit}"
    return f"{v:.1f} TB"


# ---------------------------------------------------------------- 统计
def scan_dir(path: str) -> dict:
    """扫一个目录：返回 `{files, bytes, exists, error, ts}`

    目录不存在 → `exists=False`（界面显示「尚无缓存」而不是 0 B，PRD E1）。
    单个文件读不到大小只跳过该文件，绝不中断整次扫描。
    """
    d = _abs(path)
    out = {"files": 0, "bytes": 0, "exists": os.path.isdir(d), "error": "",
           "ts": _now()}
    if not out["exists"]:
        return out
    try:
        for root, _dirs, names in os.walk(d):
            for name in names:
                fp = os.path.join(root, name)
                try:
                    out["bytes"] += os.path.getsize(fp)
                    out["files"] += 1
                except OSError:
                    continue            # 被占用 / 已删除：只跳过
    except OSError as e:
        out["error"] = f"目录读不出来：{d}（{e}）"
    return out


def stat_of(path: str, *, force: bool = False) -> tuple:
    """取缓存统计：`(stat, cached)`；缓存过期则重扫（调用方决定在哪个线程）"""
    d = _abs(path)
    now = _now()
    with _cache_lock:
        hit = _cache.get(d)
    if hit and not force and now - hit[0] < CACHE_TTL:
        return dict(hit[1]), True
    st = scan_dir(d)
    with _cache_lock:
        _cache[d] = (st["ts"], dict(st))
    return st, False


def invalidate_cache(path: str = "") -> None:
    """清缓存（清空目录后必须调，否则界面还显示旧数字）"""
    with _cache_lock:
        if path:
            _cache.pop(_abs(path), None)
        else:
            _cache.clear()


def cache_dirs() -> list:
    """当前缓存里统计过的目录（测试与诊断用）"""
    with _cache_lock:
        return list(_cache.keys())


def ensure_scan_async(paths, done_cb=None) -> threading.Thread:
    """后台扫一批目录（缓存新鲜的跳过），扫完调 `done_cb()`（在线程里！）"""
    todo = []
    for p in paths or []:
        d = _abs(p)
        with _cache_lock:
            hit = _cache.get(d)
        if not hit or _now() - hit[0] >= CACHE_TTL:
            todo.append(d)

    def _run():
        for d in todo:
            try:
                st = scan_dir(d)
                with _cache_lock:
                    _cache[d] = (st["ts"], dict(st))
            except Exception:           # noqa: BLE001 - 后台线程不能死
                continue
        if done_cb is not None:
            try:
                done_cb()
            except Exception:           # noqa: BLE001
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _rows_of(insts, cfg, *, force: bool):
    """实例列表 + 孤儿目录 → 行数据（每行带 dir / files / bytes / exists / error）"""
    from app import api_base
    rows = []
    seen = set()
    for inst in insts or []:
        if str((inst or {}).get("kind") or "local") != "remote":
            continue
        d = _abs(api_base.remote_output_dir(inst, cfg))
        seen.add(os.path.normcase(d))
        st, _cached = stat_of(d, force=force)
        rows.append({
            "iid": str(inst.get("id") or ""),
            "name": str(inst.get("name") or inst.get("id") or "远端"),
            "dir": d,
            "files": int(st["files"]),
            "bytes": int(st["bytes"]),
            "exists": bool(st["exists"]),
            "error": str(st["error"] or ""),
            "orphan": False,
        })
    # 实例已删除但目录还在 → 「（已删除的实例）<目录名>」（边界 #19）
    root = _abs(api_base.remote_output_dir({}, cfg))
    root = os.path.dirname(root)
    if os.path.isdir(root):
        try:
            for name in sorted(os.listdir(root)):
                d = _abs(os.path.join(root, name))
                if not os.path.isdir(d) or os.path.normcase(d) in seen:
                    continue
                st, _cached = stat_of(d, force=force)
                rows.append({
                    "iid": "", "name": f"（已删除的实例）{name}", "dir": d,
                    "files": int(st["files"]), "bytes": int(st["bytes"]),
                    "exists": bool(st["exists"]),
                    "error": str(st["error"] or ""), "orphan": True,
                })
        except OSError:
            pass
    return rows


def stats_rows(insts, cfg=None, *, force: bool = False) -> dict:
    """统计行 + 合计（PRD 5.5.1 四列表格）

    返回 `{"rows": [...], "total": {"files", "bytes"}, "root": str}`
    · 只读缓存（5 分钟）；`force=True` 强制重扫 —— 一定要在后台线程调
    · 目录不存在 → 该行 `exists=False`（界面显示「尚无缓存」）
    """
    from app import api_base
    rows = _rows_of(insts, cfg, force=force)
    total = {"files": sum(r["files"] for r in rows),
             "bytes": sum(r["bytes"] for r in rows)}
    return {"rows": rows, "total": total,
            "root": os.path.dirname(_abs(api_base.remote_output_dir({}, cfg)))}


# ---------------------------------------------------------------- 清理
def _iters_files(path: str):
    """列出目录下所有文件的 (路径, 大小, mtime)；读不到就跳过"""
    out = []
    for root, _dirs, names in os.walk(path):
        for name in names:
            fp = os.path.join(root, name)
            try:
                stt = os.stat(fp)
            except OSError:
                continue
            out.append((fp, int(stt.st_size), float(stt.st_mtime)))
    return out


def clean_dir(path: str, min_age_s: float = 0.0, *, now=None) -> dict:
    """清一个目录：返回 `{deleted, skipped, freed, error, dir}`

    · `min_age_s`：mtime 比这更新的文件跳过（自动清理传 3600；手动清空传 0）
    · `.part` 后缀一律跳过（正在下载的半成品，删了会留下坏文件）
    · 删不掉（被占用/只读）只计入 `skipped`，不抛异常（PRD 结果文案要报数量）
    """
    d = _abs(path)
    now = _now() if now is None else float(now)
    res = {"deleted": 0, "skipped": 0, "freed": 0, "error": "", "dir": d}
    if not os.path.isdir(d):
        res["error"] = f"目录不存在：{d}"
        return res
    if not os.access(d, os.W_OK):
        res["error"] = f"目录不可写：{d}"
        return res
    for fp, size, mtime in _iters_files(d):
        if fp.lower().endswith(".part"):
            res["skipped"] += 1
            continue
        if min_age_s and (now - mtime) < float(min_age_s):
            res["skipped"] += 1
            continue
        try:
            os.remove(fp)
        except OSError:
            res["skipped"] += 1
            continue
        res["deleted"] += 1
        res["freed"] += size
    # 顺手收掉空目录（失败无所谓，留着也不占空间）
    for root, dirs, _f in os.walk(d, topdown=False):
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    # 目录自己空了也收掉：下次下载会自动重建；留着会让「已删除的实例」一直列在那里
    try:
        os.rmdir(d)
    except OSError:
        pass
    invalidate_cache(d)
    return res


def clean_all(insts, cfg=None) -> dict:
    """一次清掉所有实例缓存（含孤儿目录）：返回汇总"""
    out = {"deleted": 0, "skipped": 0, "freed": 0, "errors": [], "dirs": 0}
    for row in _rows_of(insts, cfg, force=False):
        if not row["exists"]:
            continue
        r = clean_dir(row["dir"])
        out["dirs"] += 1
        out["deleted"] += r["deleted"]
        out["skipped"] += r["skipped"]
        out["freed"] += r["freed"]
        if r["error"]:
            out["errors"].append(r["error"])
    return out


# ---------------------------------------------------------------- 自动清理
def _settings(cfg) -> dict:
    st = (cfg or {}).get("settings") or {}
    return st if isinstance(st, dict) else {}


def _size_limit(st) -> int:
    """总量上限（字节）：`remote_cleanup_max_gb` clamp 1–500 后换算
    （抽出来是为了能单测「删到不超过」的排序逻辑，不必真造 10 GB 文件）"""
    gb = _as_int(st.get("remote_cleanup_max_gb"), DEFAULT_MAX_GB, 1, 500)
    return gb * 1024 * 1024 * 1024


def _as_int(v, dflt, lo, hi):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        n = int(dflt)
    return max(int(lo), min(int(hi), n))


def auto_clean(cfg=None, *, now=None) -> dict:
    """按策略自动清理（默认关）：返回 `{ran, deleted, freed, skipped, policy}`

    · `days`：删掉 mtime 早于「现在 − 天数」的文件
    · `size`：总量超过上限时**从最旧的开始删**，删到不超过为止
    · 两种策略都跳过最近 1 小时内用过的文件与 `.part`（边界 #18）
    """
    st = _settings(cfg)
    out = {"ran": False, "deleted": 0, "freed": 0, "skipped": 0,
           "policy": str(st.get("remote_cleanup_policy") or "days"),
           "errors": []}
    if not st.get("remote_cleanup_enabled"):
        return out
    out["ran"] = True
    now = _now() if now is None else float(now)
    rows = _rows_of((cfg or {}).get("instances") or [], cfg, force=True)
    days = _as_int(st.get("remote_cleanup_days"), DEFAULT_DAYS, 1, 365)
    limit = _size_limit(st)
    total = sum(r["bytes"] for r in rows)
    # 候选文件：跳过最近的与 .part，按 mtime 从旧到新
    cands = []
    for row in rows:
        if not row["exists"] or row["error"]:
            continue
        for fp, size, mtime in _iters_files(row["dir"]):
            if fp.lower().endswith(".part"):
                continue
            if (now - mtime) < SKIP_RECENT:
                continue
            cands.append((mtime, fp, size, row["dir"]))
    cands.sort(key=lambda x: x[0])
    victims = []
    if out["policy"] == "size":
        for _mtime, fp, size, d in cands:
            if total <= limit:
                break
            victims.append((fp, size, d))
            total -= size
    else:
        cutoff = now - days * 86400.0
        for mtime, fp, size, d in cands:
            if mtime > cutoff:
                break
            victims.append((fp, size, d))
    touched = set()
    for fp, size, d in victims:
        try:
            os.remove(fp)
        except OSError:
            out["skipped"] += 1
            continue
        out["deleted"] += 1
        out["freed"] += size
        touched.add(d)
    for d in touched:
        invalidate_cache(d)
    if out["deleted"]:
        log_info("自动清理：删除 %d 个旧产物，释放 %s"
                 % (out["deleted"], human_size(out["freed"])))
    return out


def schedule_auto(cfg=None, *, on_done=None) -> bool:
    """触发一次自动清理（10 分钟节流，PRD 5.5.2 / ARCH 验收 ⑥）

    在 `api_base.fetch_view()` 成功后调用 —— 那是「用户真的在用远程产物」的时刻。
    **必须异步**：fetch_view 有可能跑在 UI 线程，同步扫盘会把界面卡住。
    返回 True 表示这次真的起了线程。
    """
    global _last_auto
    now = time.monotonic()
    with _auto_lock:
        if _last_auto and (now - _last_auto) < AUTO_THROTTLE:
            return False
        _last_auto = now

    def _run():
        try:
            if cfg is None:
                from app.config import load_config
                cfg_now = load_config()
            else:
                cfg_now = cfg
            res = auto_clean(cfg_now)
        except Exception as e:          # noqa: BLE001 - 自动清理失败只记日志
            log_info("自动清理失败：%s" % (e,))
            return
        cb = on_done or _reporter
        if cb is not None and res.get("deleted"):
            try:
                cb(res)
            except Exception:           # noqa: BLE001
                pass

    threading.Thread(target=_run, daemon=True).start()
    return True


def reset_throttle() -> None:
    """把 10 分钟节流清零（测试与「手动重新统计」后用）"""
    global _last_auto
    with _auto_lock:
        _last_auto = 0.0
