# -*- coding: utf-8 -*-
"""「启动时才生效」字段判定的纯函数单测（无 Qt 依赖）

锁定保存后「需重启提示」的判定核心 `_restart_required_keys`（changes_dialog）：
  1. 改 lan_access       → 提示出现（返回含 lan_access）
  2. 只改 name           → 提示不出现（name 只是显示名，不进启动命令）
  3. 只改 base_url       → 提示不出现（远程连接字段是 ComfyHub 侧配置）
  4. 与 diff_changes 同口径：None ↔ "" 互顶、新增空值都不算改动
  5. 多个启动字段一起改 → 全部列出，顺序跟随 new 的字段序

用法：python tests/test_restart_required_keys.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ui.changes_dialog import _restart_required_keys  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name
          + (f"  [{detail}]" if detail and not cond else ""))


BASE = {
    "name": "我的实例", "lan_access": False, "port": 8188,
    "kind": "remote", "base_url": "http://1.2.3.4:8188",
    "extra_args": "", "vram_mode": "自动",
}


def changed(**kw):
    new = dict(BASE)
    new.update(kw)
    return _restart_required_keys(BASE, new)


# ---- 1. 用户报障场景：开了局域网访问 → 提示必须出现 ----
r = changed(lan_access=True)
check("改 lan_access → 提示出现", r == ["lan_access"], r)

# ---- 2. 只改显示名 → 不打扰用户 ----
r = changed(name="改名了")
check("只改 name → 提示不出现", r == [], r)

# ---- 3. 只改远程连接地址 → 不算重启类改动 ----
r = changed(base_url="http://5.6.7.8:8188")
check("只改 base_url → 提示不出现", r == [], r)

# ---- 4. 同 diff_changes 口径：空值互顶 / 新增空值不算改动 ----
r = _restart_required_keys({"extra_args": None}, {"extra_args": ""})
check("None ↔ \"\" 互顶不算改动", r == [], r)
r = _restart_required_keys({}, {"port": 0})
check("新增的空值（0）不算改动", r == [], r)

# ---- 5. 多个启动字段一起改 → 全部列出（跟随 new 字段序）----
r = changed(name="x", lan_access=True, port=9000)
check("多个启动字段一起改 → 全部列出", r == ["lan_access", "port"], r)

# ---- 附：引擎下拉（engine 是元组值，落盘拆成 backend/index）----
r = _restart_required_keys(
    {"engine": ("cuda", 0)}, {"engine": ("cpu", None)})
check("改 engine → 提示出现", r == ["engine"], r)

# ---- 附：挂载开关与远端字段的分界 ----
r = changed(mount_enabled=True)
check("改 mount_enabled → 提示出现", r == ["mount_enabled"], r)
r = changed(kind="local")
check("只改 kind → 提示不出现", r == [], r)

bad = [n for n, ok, _ in RESULTS if not ok]
print()
print("restart_required_keys：%d/%d 通过" % (len(RESULTS) - len(bad), len(RESULTS)))
if bad:
    print("FAILED:")
    for n in bad:
        print("  - " + n)
sys.exit(1 if bad else 0)
