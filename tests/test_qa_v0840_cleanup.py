# -*- coding: utf-8 -*-
"""QA 独立验证 V5：远程产物清理策略边界（真在临时目录里造数据）

工程师的 test_remote_cleanup 验的是「200 个假文件 + 1 个只读文件」；
本文件专攻**边界与误删风险**——这类功能的缺陷不是「没删」，而是「删错了」：

  V5-1 days 策略：只删超过 N 天的；60 分钟内的、`.part` 一律不动
  V5-2 size 策略：从最旧的删到总量达标，且同样跳过 60 分钟内与 `.part`
  V5-3 只读文件 → 计入 skipped，不崩、不影响其它文件
  V5-4 目录不可写 / 读不出来 → `error` 字段，不抛异常
  V5-5 目录不存在 → `exists=False`（界面显示「尚无缓存」而不是 0 B）
  V5-6 自动清理默认关；10 分钟节流
  V5-7 ★清空后会 rmdir 空目录：会不会误删用户还有东西的目录？
       （断言必须「只删空目录」，非空目录一个都不许动）
  V5-8 统计缓存：5 分钟内只扫一次，force=True 强制重扫

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_cleanup.py
"""
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="comfyhub_qa_clean_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP

from app import remote_cleanup as rc            # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


DAY = 86400.0
NOW = time.time()


def mk(path, size=100, age_s=0.0, name="f.bin", content=None):
    """造一个文件：size 字节 / mtime = now - age_s"""
    os.makedirs(path, exist_ok=True)
    fp = os.path.join(path, name)
    with open(fp, "wb") as f:
        f.write(content if content is not None else (b"x" * int(size)))
    if age_s:
        t = NOW - float(age_s)
        os.utime(fp, (t, t))
    return fp


def age_of(fp):
    return NOW - os.path.getmtime(fp)


def names(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# =====================================================================
print("== V5-1 days 策略：只删超过 N 天的，跳 60 分钟内与 .part ==")
d1 = os.path.join(_TMP, "case_days")
mk(d1, 100, 40 * DAY, "old40.bin")
mk(d1, 100, 35 * DAY, "old35.bin")
mk(d1, 100, 31 * DAY, "old31.bin")
mk(d1, 100, 20 * DAY, "keep20.bin")
mk(d1, 100, 600, "fresh10m.bin")            # 10 分钟前
mk(d1, 100, 40 * DAY, "half.part")          # 40 天前的半成品
cfg_days = {"settings": {"remote_cleanup_enabled": True,
                         "remote_cleanup_policy": "days",
                         "remote_cleanup_days": 30,
                         "remote_cleanup_max_gb": 10,
                         "remote_output_dir": _TMP},
            "instances": [{"id": "i1", "name": "case_days", "kind": "remote"}]}
r1 = rc.auto_clean(cfg_days, now=NOW)
left1 = names(d1)
check("V5-1a 删了 3 个（40d/35d/31d）", r1["deleted"] == 3, r1)
check("V5-1b 20 天的留着（未超 30 天）", "keep20.bin" in left1, left1)
check("V5-1c 10 分钟前的留着（60 分钟内保护）", "fresh10m.bin" in left1, left1)
check("V5-1d 40 天前的 .part 留着（半成品不删）", "half.part" in left1, left1)
check("V5-1e freed 等于删掉的字节数", r1["freed"] == 300, r1["freed"])

# =====================================================================
print("== V5-2 size 策略：从最旧删到总量达标 ==")
_ROOT2 = os.path.join(_TMP, "root_size")
d2 = os.path.join(_ROOT2, "case_size")
# 4 个 100 字节；上限设成 150 → 必须删到 <= 150，最旧的先删
mk(d2, 100, 40 * DAY, "a_oldest.bin")
mk(d2, 100, 30 * DAY, "b_older.bin")
mk(d2, 100, 20 * DAY, "c_newer.bin")
mk(d2, 100, 10 * DAY, "d_newest.bin")
mk(d2, 100, 5 * DAY, "e_part.part")         # 不算进候选
mk(d2, 100, 600, "f_fresh.bin")             # 60 分钟内，不算候选
_orig_limit = rc._size_limit
rc._size_limit = lambda st: 150             # 注入上限（避免真造 10 GB）
cfg_size = {"settings": {"remote_cleanup_enabled": True,
                         "remote_cleanup_policy": "size",
                         "remote_cleanup_days": 30,
                         "remote_cleanup_max_gb": 1,
                         "remote_output_dir": _ROOT2},
            "instances": [{"id": "i2", "name": "case_size", "kind": "remote"}]}
r2 = rc.auto_clean(cfg_size, now=NOW)
left2 = names(d2)
# 总量 600（4 个候选各 100），上限 150 → 需删到 100：最旧的 5 个？按最旧删：
# 候选只有 a/b/c/d 四个（e 是 .part、f 太新），总量按 _rows_of 的 bytes 算的是全部文件
# = 600 → 删到 <=150 需要删 5 个，但只有 4 个候选 → 删 4 个（100 剩不下）
check("V5-2a 从最旧的开始删（a_oldest 必删）", "a_oldest.bin" not in left2, left2)
check("V5-2b b_older 也删了（还没达标）", "b_older.bin" not in left2, left2)
check("V5-2c .part 不参与删除", "e_part.part" in left2, left2)
check("V5-2d 60 分钟内的不参与删除", "f_fresh.bin" in left2, left2)
check("V5-2e 删完后剩余总量 <= 上限（在候选耗尽前就停了）",
      r2["freed"] >= 400, f"freed={r2['freed']} left={left2}")

# 再验一次「阈值刚好够」的精确边界：总量 400，上限 250 → 只该删最旧 1 个
_ROOT2B = os.path.join(_TMP, "root_size2")
d2b = os.path.join(_ROOT2B, "case_size2")
mk(d2b, 100, 40 * DAY, "a1.bin")
mk(d2b, 100, 30 * DAY, "a2.bin")
mk(d2b, 100, 20 * DAY, "a3.bin")
mk(d2b, 100, 10 * DAY, "a4.bin")
rc._size_limit = lambda st: 399             # 400 > 399 → 只需删 1 个
cfg_size2 = {"settings": dict(cfg_size["settings"],
                               remote_output_dir=_ROOT2B),
             "instances": [{"id": "i3", "name": "case_size2",
                            "kind": "remote"}]}
r2b = rc.auto_clean(cfg_size2, now=NOW)
left2b = names(d2b)
check("V5-2f 精确边界：只删最旧的 1 个就达标", r2b["deleted"] == 1,
      f"{r2b} left={left2b}")
check("V5-2g 删的是最旧的那个", "a1.bin" not in left2b and "a4.bin" in left2b,
      left2b)
rc._size_limit = _orig_limit

# =====================================================================
print("== V5-3 只读文件 → skipped，不崩、不影响其它文件 ==")
d3 = os.path.join(_TMP, "case_readonly")
fo = mk(d3, 100, 40 * DAY, "readonly.bin")
mk(d3, 100, 40 * DAY, "normal1.bin")
mk(d3, 100, 40 * DAY, "normal2.bin")
os.chmod(fo, 0o444)                          # 只读
r3 = rc.clean_dir(d3, min_age_s=0, now=NOW)
check("V5-3a 两个正常文件被删", r3["deleted"] == 2, r3)
check("V5-3b 只读文件计入 skipped", r3["skipped"] == 1, r3)
check("V5-3c 不抛异常、error 为空", r3["error"] == "", r3["error"])
check("V5-3d 只读文件本身还在", os.path.isfile(fo), names(d3))
os.chmod(fo, 0o644)
try:
    os.remove(fo)
except OSError:
    pass

# =====================================================================
print("== V5-4 目录不可写 / 读不出来 → error 字段，不抛 ==")
d4 = os.path.join(_TMP, "case_denied")
mk(d4, 100, 40 * DAY, "x.bin")
_orig_access = rc.os.access
rc.os.access = lambda p, m: False if os.path.abspath(p) == os.path.abspath(d4) else \
    _orig_access(p, m)
_err = None
try:
    r4 = rc.clean_dir(d4, now=NOW)
except Exception as e:                       # noqa: BLE001
    _err = e
    r4 = {}
rc.os.access = _orig_access
check("V5-4a 不抛异常", _err is None, _err)
check("V5-4b error 字段说明「目录不可写」",
      str(r4.get("error", "")).startswith("目录不可写"), r4.get("error"))
check("V5-4c 一个文件都没删（不可写就不动手）", r4.get("deleted") == 0, r4)
check("V5-4d 文件还在", os.path.isfile(os.path.join(d4, "x.bin")))

# 读不出来（os.walk 抛）→ scan_dir 要写 error 而不是抛
_orig_walk = rc.os.walk


def _bad_walk(*a, **k):
    raise OSError(13, "Permission denied")


rc.os.walk = _bad_walk
_err2 = None
try:
    st4 = rc.scan_dir(d4)
except Exception as e:                       # noqa: BLE001
    _err2 = e
    st4 = {}
rc.os.walk = _orig_walk
check("V5-4e scan_dir 遇到 OSError 不抛", _err2 is None, _err2)
check("V5-4f scan_dir 的 error 字段有内容", bool(st4.get("error")),
      st4.get("error"))

# 自动清理遇到 error 的目录要跳过（不弹窗、不反复重试）
rc.invalidate_cache()
r4b = rc.auto_clean({"settings": {"remote_cleanup_enabled": True,
                                 "remote_cleanup_policy": "days",
                                 "remote_cleanup_days": 1,
                                 "remote_output_dir": _TMP},
                     "instances": [{"id": "i4", "name": "case_denied",
                                    "kind": "remote"}]}, now=NOW)
check("V5-4g 自动清理不因为某目录异常而中断",
      isinstance(r4b, dict) and "deleted" in r4b, r4b)

# =====================================================================
print("== V5-5 目录不存在 → exists=False（「尚无缓存」而不是 0 B）==")
ghost = os.path.join(_TMP, "case_missing")
shutil.rmtree(ghost, ignore_errors=True)
st5 = rc.scan_dir(ghost)
check("V5-5a exists=False", st5["exists"] is False, st5)
check("V5-5b files=0 / bytes=0（界面据此显示「尚无缓存」）",
      st5["files"] == 0 and st5["bytes"] == 0, st5)
check("V5-5c error 为空（这不是错误，只是没缓存）", st5["error"] == "", st5["error"])
r5 = rc.stats_rows([{"id": "i5", "name": "case_missing", "kind": "remote"}],
                   {"settings": {"remote_output_dir": _TMP}})
row5 = [x for x in r5["rows"] if x["iid"] == "i5"][0]
check("V5-5d stats_rows 里该行 exists=False（不是 0 B 一行）",
      row5["exists"] is False, row5)

# =====================================================================
print("== V5-6 默认关 / 10 分钟节流 ==")
r6 = rc.auto_clean({"settings": {"remote_output_dir": _TMP},
                    "instances": []}, now=NOW)
check("V5-6a 默认（没开）不跑：ran=False", r6["ran"] is False, r6)
check("V5-6b 值从 config 默认来：remote_cleanup_enabled=False",
      __import__("app.config", fromlist=["x"])._SETTINGS_DEFAULTS[
          "remote_cleanup_enabled"] is False)
rc.reset_throttle()
_ok1 = rc.schedule_auto(cfg={"settings": {"remote_cleanup_enabled": False},
                            "instances": []})
_ok2 = rc.schedule_auto(cfg={"settings": {"remote_cleanup_enabled": False},
                            "instances": []})
check("V5-6c 第一次 schedule_auto 起线程（True）", _ok1 is True, _ok1)
check("V5-6d 10 分钟内第二次被节流（False）", _ok2 is False, _ok2)
rc.reset_throttle()
_ok3 = rc.schedule_auto(cfg={"settings": {"remote_cleanup_enabled": False},
                            "instances": []})
check("V5-6e reset_throttle 后可再跑一次", _ok3 is True, _ok3)
time.sleep(0.3)

# =====================================================================
print("== V5-7 ★清空后 rmdir 空目录：只许删「真的空」的目录 ==")
# 7a：目标目录里只剩「保护中的文件」→ 不是空目录 → 不许删目录
d7a = os.path.join(_TMP, "case_keepdir")
mk(d7a, 100, 600, "fresh.bin")               # 60 分钟内
mk(d7a, 100, 40 * DAY, "half.part")          # .part
r7a = rc.clean_dir(d7a, min_age_s=3600, now=NOW)
check("V5-7a 目录里只剩受保护文件时，目录**不被删**",
      os.path.isdir(d7a), f"exists={os.path.isdir(d7a)} res={r7a}")
check("V5-7b 受保护文件都还在",
      sorted(names(d7a)) == ["fresh.bin", "half.part"], names(d7a))

# 7c：目录里有子目录且子目录里有文件 → 不许删
d7c = os.path.join(_TMP, "case_nested")
mk(os.path.join(d7c, "sub"), 100, 600, "deep.bin")
mk(d7c, 100, 40 * DAY, "top.bin")
r7c = rc.clean_dir(d7c, min_age_s=3600, now=NOW)
check("V5-7c 有内容的子目录不被 rmdir", os.path.isdir(os.path.join(d7c, "sub")),
      names(d7c))
check("V5-7d 子目录里的文件仍在", os.path.isfile(
    os.path.join(d7c, "sub", "deep.bin")))
check("V5-7e 顶层的旧文件照删", "top.bin" not in names(d7c), names(d7c))

# 7e：目录里有非缓存类文件（例如用户自己放了个 README，但 mtime 很新）→ 不许删
d7e = os.path.join(_TMP, "case_userfile")
mk(d7e, 100, 0, "README.txt")                # 刚写的
r7e = rc.clean_dir(d7e, min_age_s=3600, now=NOW)
check("V5-7f 用户刚放进去的文件不会被当缓存删掉",
      os.path.isfile(os.path.join(d7e, "README.txt")), names(d7e))
check("V5-7g 因此该目录也保留", os.path.isdir(d7e))

# 7h：目录**真的空了**（全部删掉）→ 允许 rmdir（否则「已删除的实例」会一直挂着）
d7h = os.path.join(_TMP, "case_becomes_empty")
mk(d7h, 100, 40 * DAY, "only.bin")
r7h = rc.clean_dir(d7h, min_age_s=0, now=NOW)
check("V5-7h 清空后目录本身被收掉（设计如此，避免残留空壳）",
      not os.path.isdir(d7h), f"exists={os.path.isdir(d7h)} res={r7h}")
check("V5-7i 收掉的是**空**目录 —— 没有数据因此丢失（文件是先删完的）",
      r7h["deleted"] == 1 and r7h["freed"] == 100, r7h)

# 7j：清理根目录（用户在设置里把 remote_output_dir 指到别处）——root 本身不该被删
root_probe = os.path.join(_TMP, "case_root_probe")
mk(os.path.join(root_probe, "instA"), 100, 40 * DAY, "a.bin")
mk(os.path.join(root_probe, "instB"), 100, 40 * DAY, "b.bin")
cfg_probe = {"settings": {"remote_cleanup_enabled": True,
                          "remote_cleanup_policy": "days",
                          "remote_cleanup_days": 30,
                          "remote_output_dir": root_probe},
             "instances": [{"id": "iA", "name": "instA", "kind": "remote"},
                           {"id": "iB", "name": "instB", "kind": "remote"}]}
rc.invalidate_cache()
rc.clean_all(cfg_probe["instances"], cfg_probe)
check("V5-7j clean_all 后清理**根目录**本身仍存在（只收实例子目录）",
      os.path.isdir(root_probe), f"exists={os.path.isdir(root_probe)}")
check("V5-7k 两个实例子目录都被收掉（都空了）",
      not os.path.isdir(os.path.join(root_probe, "instA"))
      and not os.path.isdir(os.path.join(root_probe, "instB")),
      names(root_probe))

# =====================================================================
print("== V5-8 统计缓存：5 分钟内只扫一次，force 强制重扫 ==")
d8 = os.path.join(_TMP, "case_cache")
mk(d8, 1000, 0, "a.bin")
rc.invalidate_cache()
_calls = {"n": 0}
_orig_scan = rc.scan_dir


_d8 = os.path.abspath(d8)


def _counting_scan(p):
    if os.path.abspath(p) == _d8:      # 只数目标目录，别的目录不该影响本断言
        _calls["n"] += 1
    return _orig_scan(p)


rc.scan_dir = _counting_scan
cfg8 = {"settings": {"remote_output_dir": _TMP},
        "instances": [{"id": "i8", "name": "case_cache", "kind": "remote"}]}
rc.stats_rows(cfg8["instances"], cfg8)
_after_first = _calls["n"]
rc.stats_rows(cfg8["instances"], cfg8)
rc.stats_rows(cfg8["instances"], cfg8)
check("V5-8a 5 分钟内连续三次统计只扫盘一次（走缓存）",
      _calls["n"] == _after_first and _after_first == 1,
      f"first={_after_first} total={_calls['n']}")
rc.stats_rows(cfg8["instances"], cfg8, force=True)
check("V5-8b force=True 强制重扫", _calls["n"] == _after_first + 1, _calls["n"])
rc.scan_dir = _orig_scan

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V5 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
