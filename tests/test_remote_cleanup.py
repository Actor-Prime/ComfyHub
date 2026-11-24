# -*- coding: utf-8 -*-
"""0.8.40 T07：远程产物清理（统计 / 缓存 / 手动清空 / 自动策略）

覆盖（ARCH §7 T07 验收 ①-⑧）：
  ① 目录不存在 → exists=False（界面显示「尚无缓存」，不是 0 B、不报错）
  ② 200 个文件 + 1 个只读文件 → clean_dir 的 deleted/skipped 正确，只读那个 skipped
  ③ 缓存：连续两次 stats_rows 只第一次扫盘（打点计数），force=True 强制重扫
  ④ 缓存命中时 500ms 内返回（不等扫描，UI 不卡）
  ⑤ 自动清理默认关；开启后 days 删 31 天前、保留 1 小时内；size 删到上限内且从最旧开始
  ⑥ 10 分钟内两次 schedule_auto 只触发一次
  ⑦ 确认文案逐字含「删的是 ComfyHub 的本地副本，远端原图不受影响」（源码级断言）
  ⑧ 目录不可写 → error="目录不可写：<path>"，自动清理跳过、不抛异常
  ⑨ 孤儿目录显示「（已删除的实例）<目录名>」
  ⑩ human_size 输出人读格式；层次约束（不 import PySide6）

用法：.venv_build/Scripts/python.exe -u tests/test_remote_cleanup.py
"""
import ast
import os
import shutil
import stat
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api_base                      # noqa: E402
from app import remote_cleanup as rc          # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, bool(ok), d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


_TMP = tempfile.mkdtemp(prefix="cfhub_rc_")
CFG = {
    "settings": {"remote_output_dir": _TMP},
    "instances": [
        {"id": "i1", "name": "云端 4090", "kind": "remote"},
        {"id": "i2", "name": "家里那台", "kind": "remote"},
        {"id": "i3", "name": "本机实例", "kind": "local"},
    ],
}
DIR1 = api_base.remote_output_dir(CFG["instances"][0], CFG)
DIR2 = api_base.remote_output_dir(CFG["instances"][1], CFG)


def _write(path, size=16, age_s=0.0):
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if age_s:
        t = time.time() - age_s
        os.utime(path, (t, t))
    return path


_seq = [0]


def _mkfiles(d, n, size=16, age_s=0.0):
    """造 n 个文件。文件名带全局递增序号 —— 各段之间不能互相覆盖（否则「旧文件
    被新文件顶上」会让 mtime 断言失效）"""
    os.makedirs(d, exist_ok=True)
    out = []
    for _ in range(n):
        _seq[0] += 1
        out.append(_write(os.path.join(d, f"img_{_seq[0]:04d}.png"), size, age_s))
    return out


print("=== 1. 目录不存在 → exists=False（尚无缓存）===")
rc.invalidate_cache()
_rows = rc.stats_rows(CFG["instances"], CFG)["rows"]
_remote_rows = [r for r in _rows if not r["orphan"]]
check("只统计 kind=remote 的实例", len(_remote_rows) == 2, str(len(_remote_rows)))
check("目录不存在 → exists=False 且 files/bytes 都是 0",
      all((not r["exists"]) and r["files"] == 0 and r["bytes"] == 0
          for r in _remote_rows), str([(r["name"], r["exists"]) for r in _remote_rows]))
check("目录不存在不报 error", all(not r["error"] for r in _remote_rows))
check("合计为 0", rc.stats_rows(CFG["instances"], CFG)["total"] == {"files": 0, "bytes": 0})

print("=== 2. 200 个文件 + 1 个只读文件 → deleted/skipped ===")
_mkfiles(DIR1, 200)
_ro = _write(os.path.join(DIR1, "locked.png"))
os.chmod(_ro, stat.S_IREAD)
_r = rc.clean_dir(DIR1)
check("删掉 200 个普通文件", _r["deleted"] == 200, str(_r))
check("只读文件计入 skipped（不抛异常）", _r["skipped"] == 1, str(_r))
check("只读文件确实还在", os.path.isfile(_ro))
check("freed 等于删除文件的总字节", _r["freed"] == 200 * 16, str(_r["freed"]))
os.chmod(_ro, stat.S_IWRITE)
check("解锁后再清一次能删掉", rc.clean_dir(DIR1)["deleted"] == 1)
check("清完目录里没有残留文件",
      not [f for f in os.listdir(DIR1)] if os.path.isdir(DIR1) else True)

print("=== 3. 5 分钟缓存 + force 重扫 ===")
_mkfiles(DIR1, 5)
rc.invalidate_cache()
_calls = []
_orig_scan = rc.scan_dir


def _spy(p):
    _calls.append(p)
    return _orig_scan(p)


rc.scan_dir = _spy
try:
    r1 = rc.stats_rows(CFG["instances"], CFG)
    n1 = len(_calls)
    check("首次统计会扫盘", n1 >= 1, str(n1))
    check("首次数值正确", r1["rows"][0]["files"] == 5, str(r1["rows"][0]))
    r2 = rc.stats_rows(CFG["instances"], CFG)
    check("第二次走缓存不再扫盘", len(_calls) == n1, f"{n1} → {len(_calls)}")
    check("缓存命中数值相同", r2["rows"][0]["files"] == 5)
    _mkfiles(DIR1, 2)
    r3 = rc.stats_rows(CFG["instances"], CFG)
    check("缓存期内看不到新文件（符合 5 分钟语义）", r3["rows"][0]["files"] == 5,
          str(r3["rows"][0]["files"]))
    t0 = time.monotonic()
    rc.stats_rows(CFG["instances"], CFG)
    dt = time.monotonic() - t0
    check("④ 缓存命中 500ms 内返回（不等扫描）", dt < 0.5, f"{dt*1000:.1f}ms")
    _before = len(_calls)
    r4 = rc.stats_rows(CFG["instances"], CFG, force=True)
    check("force=True 强制重扫看到新文件", r4["rows"][0]["files"] == 7,
          str(r4["rows"][0]["files"]))
    check("force 会重扫每个远程目录", len(_calls) == _before + 2, str(len(_calls)))
finally:
    rc.scan_dir = _orig_scan

print("=== 4. 自动清理：默认关 ===")
rc.clean_dir(DIR1)
_mkfiles(DIR1, 3, age_s=40 * 86400)
_a = rc.auto_clean(CFG)
check("默认关 → ran=False 且一个文件都不删", _a["ran"] is False and _a["deleted"] == 0,
      str(_a))
check("文件都还在", len(os.listdir(DIR1)) == 3)

print("=== 5. 自动清理 days 策略：删 31 天前、保留 1 小时内 ===")
rc.clean_dir(DIR1)                      # 把第 4 段留下的 3 个 40 天旧文件夹清掉，保证本段干净
CFG["settings"].update({"remote_cleanup_enabled": True,
                        "remote_cleanup_policy": "days",
                        "remote_cleanup_days": 30})
_old = _mkfiles(DIR1, 2, age_s=31 * 86400)
_fresh = _mkfiles(DIR1, 1, age_s=600)
_part = _write(os.path.join(DIR1, "downloading.png.part"), age_s=90 * 86400)
_a = rc.auto_clean(CFG)
check("day 策略删掉 31 天前的 2 个", _a["deleted"] == 2, str(_a))
check("删的都是旧的", all(not os.path.isfile(p) for p in _old))
check("最近 1 小时内的保留", all(os.path.isfile(p) for p in _fresh))
check(".part 一律跳过", os.path.isfile(_part))

print("=== 6. 自动清理 size 策略：从最旧开始删到不超上限 ===")
for f in os.listdir(DIR1):
    try:
        os.remove(os.path.join(DIR1, f))
    except OSError:
        pass
_f1 = _write(os.path.join(DIR1, "a_oldest.png"), 100, age_s=400 * 86400)
_f2 = _write(os.path.join(DIR1, "b_mid.png"), 100, age_s=300 * 86400)
_f3 = _write(os.path.join(DIR1, "c_newest.png"), 100, age_s=200 * 86400)
CFG["settings"].update({"remote_cleanup_policy": "size",
                        "remote_cleanup_max_gb": 10})
_orig_limit = rc._size_limit
rc._size_limit = lambda st: 250          # 造一个小上限：3×100B → 删到 ≤250B
try:
    _a = rc.auto_clean(CFG)
finally:
    rc._size_limit = _orig_limit
check("size 策略删 1 个就够", _a["deleted"] == 1, str(_a))
check("删的是最旧的那个", not os.path.isfile(_f1) and os.path.isfile(_f2)
      and os.path.isfile(_f3))

print("=== 7. schedule_auto 10 分钟节流 ===")
rc.reset_throttle()
_hits = []
_orig_auto = rc.auto_clean
_done = threading.Event()


def _fake_auto(cfg=None, *, now=None):
    _hits.append(1)
    _done.set()
    return {"ran": True, "deleted": 1, "freed": 1024, "skipped": 0,
            "policy": "days", "errors": []}


rc.auto_clean = _fake_auto
try:
    first = rc.schedule_auto(CFG)
    _done.wait(5.0)
    second = rc.schedule_auto(CFG)
finally:
    rc.auto_clean = _orig_auto
check("第一次真的起了线程", first is True)
check("10 分钟内第二次被节流", second is False)
check("只跑了一次", len(_hits) == 1, str(len(_hits)))
rc.reset_throttle()

print("=== 8. 目录不可写 → error，不抛异常 ===")
_orig_access = os.access
try:
    os.access = lambda p, m: False if str(p) == os.path.abspath(DIR1) else _orig_access(p, m)
    _r = rc.clean_dir(DIR1)
finally:
    os.access = _orig_access
check("error 逐字为「目录不可写：<path>」",
      _r["error"] == f"目录不可写：{os.path.abspath(DIR1)}", _r["error"])
check("不可写时不删任何东西", _r["deleted"] == 0)
check("目录不存在时 error 是「目录不存在」",
      rc.clean_dir(os.path.join(_TMP, "nope"))["error"].startswith("目录不存在"))

print("=== 9. 孤儿目录 → 「（已删除的实例）<目录名>」===")
_orphan = os.path.join(_TMP, "old-box")
_mkfiles(_orphan, 3)
rc.invalidate_cache()
_rows = rc.stats_rows(CFG["instances"], CFG)["rows"]
_or = [r for r in _rows if r["orphan"]]
check("孤儿目录被列出来", len(_or) == 1, str([r["name"] for r in _rows]))
check("名称是「（已删除的实例）old-box」",
      _or and _or[0]["name"] == "（已删除的实例）old-box",
      _or[0]["name"] if _or else "")
check("孤儿目录也有文件数", _or and _or[0]["files"] == 3,
      str(_or[0]["files"]) if _or else "")
_all = rc.clean_all(CFG["instances"], CFG)
check("全部清空把孤儿目录一起清", _all["deleted"] >= 3, str(_all))
check("清完后孤儿目录不再列出",
      not [r for r in rc.stats_rows(CFG["instances"], CFG,
                                    force=True)["rows"] if r["orphan"]])

print("=== 10. human_size / 文案 / 层次约束 ===")
check("0 → 0 B", rc.human_size(0) == "0 B", rc.human_size(0))
check("512 → 512 B", rc.human_size(512) == "512 B", rc.human_size(512))
check("1536 → 1.5 KB", rc.human_size(1536) == "1.5 KB", rc.human_size(1536))
check("1.2 GB", rc.human_size(int(1.2 * 1024 ** 3)).startswith("1.2 GB"),
      rc.human_size(int(1.2 * 1024 ** 3)))
_src = open(os.path.join(ROOT, "app", "ui", "pages", "settings_page.py"),
            encoding="utf-8").read()
check("⑦ 单个清空文案逐字含「删的是 ComfyHub 的本地副本，远端原图不受影响」",
      "删的是 ComfyHub 的本地副本，远端原图不受影响" in _src)
check("⑦ 结果文案逐字含「个文件正在使用，没删」",
      "个文件正在使用，没删" in _src)
check("⑦ 自动清理结果文案逐字「自动清理：删除 … 个旧产物，释放」",
      "自动清理：删除" in open(os.path.join(ROOT, "app", "ui", "main_window.py"),
                                encoding="utf-8").read())
_tree = ast.parse(open(os.path.join(ROOT, "app", "remote_cleanup.py"),
                       encoding="utf-8").read())
_mods = []
for _n in ast.walk(_tree):
    if isinstance(_n, ast.Import):
        _mods += [a.name for a in _n.names]
    elif isinstance(_n, ast.ImportFrom) and _n.module:
        _mods.append(_n.module)
check("不 import PySide6（纯逻辑模块）",
      not any(m.startswith("PySide6") for m in _mods), str(_mods))
check("CACHE_TTL = 300s / AUTO_THROTTLE = 600s / SKIP_RECENT = 3600s",
      rc.CACHE_TTL == 300 and rc.AUTO_THROTTLE == 600 and rc.SKIP_RECENT == 3600,
      f"{rc.CACHE_TTL}/{rc.AUTO_THROTTLE}/{rc.SKIP_RECENT}")
check("单次释放 ≥500MB 才弹 toast", rc.TOAST_MIN_BYTES == 500 * 1024 * 1024)

shutil.rmtree(_TMP, ignore_errors=True)
print()
_bad = [r for r in res if not r[1]]
print(f"通过 {len(res) - len(_bad)}/{len(res)}")
if _bad:
    print("失败：")
    for n, _ok, d in _bad:
        print(f"  - {n}  [{d}]")
    sys.exit(1)
print("OK 全部通过")
