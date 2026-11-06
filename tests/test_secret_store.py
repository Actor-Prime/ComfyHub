# -*- coding: utf-8 -*-
"""密钥加密存储回归（2026-11-06）

背景：ai_api_key 原本明文写 config.json，文件被拷走等于把 Key 送人。
现在落盘走 Windows DPAPI，内存里仍是明文。
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

PASS = 0


def check(name, ok, detail=""):
    global PASS
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        sys.exit(1)
    PASS += 1


from app import secret_store as ss  # noqa: E402

# ---- 1. DPAPI 往返 ----
KEY = "tp-csu6abcdefghijklmnopqrstuvwxyz0123456789ABCDE"
enc = ss.protect(KEY)
check("1a 加密后不是明文", enc != KEY and enc.startswith(ss.PREFIX), enc[:24])
check("1b 能解回原值", ss.unprotect(enc) == KEY, str(ss.unprotect(enc))[:20])
check("1c 重复加密幂等", ss.protect(enc) == enc)
check("1d 明文传入原样返回（兼容旧配置）", ss.unprotect(KEY) == KEY)
check("1e 空值安全", ss.protect("") == "" and ss.unprotect("") == "")
check("1f 密文被破坏时返回 None（不崩、不当明文用）",
      ss.unprotect(ss.PREFIX + "not-a-valid-blob") is None)
check("1g is_encrypted 判定", ss.is_encrypted(enc) and not ss.is_encrypted(KEY))

# ---- 2. config 落盘加密 / 读取解密 ----
from app import config as cfgmod  # noqa: E402

tmp = tempfile.mkdtemp(prefix="ch_cfg_")
orig_path = cfgmod.CONFIG_PATH
cfgmod.CONFIG_PATH = os.path.join(tmp, "config.json")
try:
    cfg = {"version": 1, "instances": [], "last_instance_id": "",
           "settings": {"ai_api_key": KEY, "ai_model": "m", "_ai_key_locked": False}}
    cfgmod.save_config(cfg)

    raw = open(cfgmod.CONFIG_PATH, encoding="utf-8").read()
    check("2a 盘上没有明文 Key", KEY not in raw, "明文泄漏")
    check("2b 盘上是密文", ss.PREFIX in raw)
    check("2c 运行态标记不写盘", "_ai_key_locked" not in raw)
    check("2d 保存不改动内存里的明文（不会被替换成密文）",
          cfg["settings"]["ai_api_key"] == KEY)

    back = cfgmod.load_config()
    check("2e 读取后内存是明文", back["settings"]["ai_api_key"] == KEY,
          str(back["settings"].get("ai_api_key"))[:20])
    check("2f 其它设置照常", back["settings"]["ai_model"] == "m")

    # 换机器/换用户 → 解不开：置空并打标记，不把密文当 Key
    bad = json.loads(raw)
    bad["settings"]["ai_api_key"] = ss.PREFIX + "AAAA" + "broken"
    with open(cfgmod.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(bad, f)
    b2 = cfgmod.load_config()
    check("2g 解不开时置空并打标记",
          b2["settings"]["ai_api_key"] == "" and b2["settings"].get("_ai_key_locked") is True,
          str(b2["settings"]))

    # 旧配置（明文）→ 打迁移标记 → 保存后变密文
    with open(cfgmod.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "instances": [],
                   "settings": {"ai_api_key": KEY}}, f)
    b3 = cfgmod.load_config()
    check("2h 明文旧配置打上迁移标记", b3.get("_migrate_key") is True, str(b3.keys()))
    check("2i 明文照常可用（不中断）", b3["settings"]["ai_api_key"] == KEY)
    b3.pop("_migrate_key", None)
    cfgmod.save_config(b3)
    raw2 = open(cfgmod.CONFIG_PATH, encoding="utf-8").read()
    check("2j 迁移后盘上不再有明文", KEY not in raw2 and ss.PREFIX in raw2)
    check("2k 迁移后仍能读回", cfgmod.load_config()["settings"]["ai_api_key"] == KEY)
finally:
    cfgmod.CONFIG_PATH = orig_path

# ---- 3. 诊断包对"裸 token"也做精确替换 ----
os.environ["APPDATA"] = tmp          # feedback 取配置也走这条路径不必要，直接测函数
from app import feedback as fb  # noqa: E402

_orig_load = fb.__dict__.get("load_config")
import app.config as _c  # noqa: E402
_orig = _c.load_config
_c.load_config = lambda: {"settings": {"ai_api_key": KEY}, "instances": []}
try:
    out = fb._strip_known_secrets(f"日志里出现了 {KEY} 这样一段裸 token")
    check("3a 裸 token 被替换", KEY not in out and "***" in out, out[:60])
finally:
    _c.load_config = _orig

print(f"共 {PASS} 项，全部通过")
