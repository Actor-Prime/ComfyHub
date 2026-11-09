# -*- coding: utf-8 -*-
"""配置自愈回归（2026-11-03）：实例列表去重 / 重分配 id / last_instance_id 收敛

背景：测试脚本曾向真实配置写入固定 id 的实例，反复中断后叠出 3 条完全相同的
PPTEST（同 id / 同路径 / 同端口）。同 id 会让「按 id 查找」全部命中第一条，
选中、删除、切页都会串，是数据完整性问题。

用法：.venv_build/Scripts/python.exe tests/test_config_sanitize.py
"""
import json
import os
import sys
import tempfile

# 必须在导入 app.config 之前重定向 APPDATA，否则测试会写到真实配置
_TMP = tempfile.mkdtemp(prefix="comfyhub_cfg_")
os.environ["APPDATA"] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.config import load_config, save_config, CONFIG_PATH  # noqa: E402

print("配置路径:", CONFIG_PATH)
check("0 测试跑在临时目录", _TMP in CONFIG_PATH, CONFIG_PATH)


def write_cfg(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)


# ---------- 1. 完全重复项去重 ----------
dup = {"id": "inst_pptest", "name": "PPTEST", "comfyui_path": "F:/_pp_ui_test/ComfyUI", "port": 8399}
write_cfg({"version": 1, "instances": [
    dict(dup), dict(dup), dict(dup),
    {"id": "inst_real", "name": "ComfyUI", "comfyui_path": "F:/ComfyUI-aki-v3/ComfyUI", "port": 8188},
], "last_instance_id": ""})
cfg = load_config()
check("1a 三条同 id 副本收敛成一条",
      len(cfg["instances"]) == 2 and sum(1 for i in cfg["instances"]
                                          if i["id"] == "inst_pptest") == 1,
      str([i["id"] for i in cfg["instances"]]))
check("1b 真实实例未受影响",
      any(i["id"] == "inst_real" for i in cfg["instances"]))

# ---------- 2. 同 id 但内容不同 → 重新分配 id ----------
write_cfg({"version": 1, "instances": [
    {"id": "same", "name": "A", "comfyui_path": "F:/A", "port": 8188},
    {"id": "same", "name": "B", "comfyui_path": "F:/B", "port": 8189},
], "last_instance_id": ""})
cfg = load_config()
ids = [i["id"] for i in cfg["instances"]]
check("2a 两条都保留", len(ids) == 2, str(ids))
check("2b id 不再重复", len(set(ids)) == 2, str(ids))
check("2c 第一条保留原 id", ids[0] == "same", str(ids))

# ---------- 3. 缺失 id 自动补 ----------
write_cfg({"version": 1, "instances": [{"name": "NoId", "comfyui_path": "F:/X"}],
           "last_instance_id": ""})
cfg = load_config()
check("3 缺 id 自动补", cfg["instances"][0]["id"].startswith("inst_"), cfg["instances"][0].get("id"))

# ---------- 4. 悬空 last_instance_id 清空 ----------
write_cfg({"version": 1,
           "instances": [{"id": "a", "name": "A", "comfyui_path": "F:/A", "port": 8188}],
           "last_instance_id": "已删除的实例"})
cfg = load_config()
check("4 悬空 last_instance_id 清空", cfg.get("last_instance_id") == "", str(cfg.get("last_instance_id")))

# ---------- 5. 旧落点 settings.last_instance_id 迁移到顶层 ----------
write_cfg({"version": 1,
           "instances": [{"id": "a", "name": "A", "comfyui_path": "F:/A", "port": 8188}],
           "settings": {"last_instance_id": "a"}})
cfg = load_config()
check("5a 迁移到顶层", cfg.get("last_instance_id") == "a", str(cfg.get("last_instance_id")))
check("5b settings 内旧键已移除", "last_instance_id" not in (cfg.get("settings") or {}))

# ---------- 6. 幂等：干净配置不写盘 ----------
clean = {"version": 1,
         "instances": [{"id": "a", "name": "A", "comfyui_path": "F:/A", "port": 8188}],
         "last_instance_id": "a"}
write_cfg(clean)
mtime0 = os.path.getmtime(CONFIG_PATH)
cfg = load_config()
check("6a 干净配置内容不变", cfg["instances"][0]["id"] == "a")
check("6b 干净配置不触发写盘", abs(os.path.getmtime(CONFIG_PATH) - mtime0) < 0.01,
      f"{mtime0} -> {os.path.getmtime(CONFIG_PATH)}")

# ---------- 7. 自愈结果已落盘 ----------
write_cfg({"version": 1, "instances": [dict(dup), dict(dup)], "last_instance_id": ""})
load_config()
raw = json.load(open(CONFIG_PATH, encoding="utf-8"))
check("7 去重结果写回磁盘", len(raw["instances"]) == 1, str(len(raw["instances"])))

print("-" * 56)
fails = [n for n, ok, _ in RESULTS if not ok]
print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(fails)}，失败 {len(fails)}")
sys.exit(1 if fails else 0)
