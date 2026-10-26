# -*- coding: utf-8 -*-
"""plugin_profiles 单元测试：造 4 个假插件，验证冲突检测 + 方案切换 + 回滚"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import plugin_profiles as pp

BASE = r"F:\_pp_test"
CP = os.path.join(BASE, "ComfyUI")
NODES = os.path.join(CP, "custom_nodes")

PLUGINS = {
    "pluginA": {"SharedNode": "A", "UniqueA": "A"},
    "pluginB": {"SharedNode": "B"},            # 与 A 冲突
    "pluginC": {"UniqueC": "C"},               # 无冲突
    "pluginD": {"SharedNode": "D", "UniqueD": "D"},   # 三方冲突
}


def make_fixture():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(NODES, exist_ok=True)
    open(os.path.join(CP, "main.py"), "w").write("print('stub')")
    for name, nodes in PLUGINS.items():
        d = os.path.join(NODES, name)
        os.makedirs(d)
        body = ",\n".join(f'    "{k}": {v}' for k, v in nodes.items())
        open(os.path.join(d, "__init__.py"), "w", encoding="utf-8").write(
            f"NODE_CLASS_MAPPINGS = {{\n{body}\n}}\n")
        # 附带一个无关文件（应被扫描跳过）
        open(os.path.join(d, "readme.md"), "w", encoding="utf-8").write("hi")


res = []


def check(name, ok, detail=""):
    res.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


print("=== 准备测试环境 ===")
make_fixture()
print("  4 个假插件已建：", ", ".join(PLUGINS))

print("\n=== 1. 插件列表 ===")
lst = pp.list_plugins(CP)
check("列出 4 个插件", len(lst) == 4, str([p['name'] for p in lst]))
check("全部默认启用", all(p["enabled"] for p in lst))

print("\n=== 2. 冲突检测 ===")
r = pp.scan_duplicate_nodes(CP, only_enabled=True)
check("节点计数累加=6（SharedNode 被 3 个插件各计 1）", r["keys_total"] == 6, str(r["keys_total"]))
check("检出 1 组冲突（SharedNode）", len(r["conflicts"]) == 1, str(list(r["conflicts"])))
if "SharedNode" in r["conflicts"]:
    check("冲突方含 A/B/D 三方", sorted(r["conflicts"]["SharedNode"]) == ["pluginA", "pluginB", "pluginD"],
          str(r["conflicts"]["SharedNode"]))

print("\n=== 3. 应用方案（只留 A、C）===")
out = pp.apply_profile(CP, ["pluginA", "pluginC"], log=lambda m: print("   ", m))
check("执行成功", out["ok"], str(out["errors"]))
check("停用 2 个", len([c for c in out["changed"] if c[1] == "off"]) == 2, str(out["changed"]))
after = {p["name"]: p["enabled"] for p in pp.list_plugins(CP)}
check("A/C 启用", after.get("pluginA") and after.get("pluginC"), str(after))
check("B/D 已禁用", not after.get("pluginB") and not after.get("pluginD"), str(after))

print("\n=== 4. 冲突检测应忽略已禁用插件 ===")
r2 = pp.scan_duplicate_nodes(CP, only_enabled=True)
check("禁用后无冲突", len(r2["conflicts"]) == 0, str(r2["conflicts"]))
r3 = pp.scan_duplicate_nodes(CP, only_enabled=False)
check("含禁用时仍有冲突", len(r3["conflicts"]) == 1, str(r3["conflicts"]))

print("\n=== 5. 切回全启用 ===")
out2 = pp.apply_profile(CP, list(PLUGINS.keys()), log=lambda m: print("   ", m))
check("执行成功", out2["ok"])
after2 = {p["name"]: p["enabled"] for p in pp.list_plugins(CP)}
check("4 个全启用", all(after2.values()), str(after2))
check("journal 已清理", not os.path.isfile(os.path.join(CP, ".comfyhub_profile_journal.json")))

print("\n=== 6. 幂等（同方案再应用不应有改动）===")
out3 = pp.apply_profile(CP, list(PLUGINS.keys()), log=lambda m: None)
check("无改动", out3["ok"] and not out3["changed"], str(out3["changed"]))

print("\n=== 7. journal 残留自动恢复 ===")
import json
os.rename(os.path.join(NODES, "pluginC"), os.path.join(NODES, "pluginC.disabled"))
j = {"time": "test", "nodes_dir": NODES,
     "plan": [["pluginC", "pluginC.disabled", "pluginC", "off"]]}
json.dump(j, open(os.path.join(CP, ".comfyhub_profile_journal.json"), "w"))
pp.recover_if_needed(CP, log=lambda m: print("   ", m))
check("C 已恢复启用", os.path.isdir(os.path.join(NODES, "pluginC")) and
      not os.path.isdir(os.path.join(NODES, "pluginC.disabled")))
check("journal 已删除", not os.path.isfile(os.path.join(CP, ".comfyhub_profile_journal.json")))

shutil.rmtree(BASE, ignore_errors=True)
passed = sum(1 for _, ok, _ in res if ok)
print(f"\n{'='*50}\n结果：{passed}/{len(res)} 通过")
for n, ok, d in res:
    if not ok:
        print(f"  ❌ {n}  [{d}]")
sys.exit(0 if passed == len(res) else 1)
