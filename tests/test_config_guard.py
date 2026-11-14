"""config.load_config 损坏防护回归测试 — 2026-11-15。

事故背景：外部工具写入带 BOM 的 UTF-8 config.json → json.load 失败 →
静默回退默认骨架 → save_config 覆盖，用户配置全丢。
防护：① utf-8-sig 兜底（吃掉 BOM）；② 真坏文件先留底 .bad_<ts> 再回退。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  [%s]" % (detail,)) if detail != "" else ""))
    if not ok:
        FAILS.append(name)


def main():
    from app import config

    real = config.CONFIG_PATH
    good = {"version": 1, "instances": [{"id": "a"}],
            "settings": {"theme": "light", "ai_api_key": "sk-test"}}

    # 1) BOM 文件：utf-8 失败但 utf-8-sig 能救 → 数据完整读回
    with io.open(real + ".tmp_bom", "w", encoding="utf-8-sig") as f:
        json.dump(good, f)
    raw = open(real + ".tmp_bom", "rb").read()
    check("BOM 文件确实带 BOM 头", raw[:3] == b"\xef\xbb\xbf")
    config.CONFIG_PATH = real + ".tmp_bom"
    try:
        cfg = config.load_config()
        check("带 BOM 也能读回完整配置", cfg.get("settings", {}).get("theme") == "light"
              and cfg.get("instances") == [{"id": "a"}], cfg.get("settings", {}).get("theme"))
    finally:
        config.CONFIG_PATH = real
        os.remove(real + ".tmp_bom")

    # 2) 纯坏字节：回退默认 + 坏文件留底 .bad_<ts>
    badp = real + ".tmp_bad"
    with open(badp, "wb") as f:
        f.write(b"\xff\xfe not json at all {{{")
    config.CONFIG_PATH = badp
    try:
        cfg = config.load_config()
        check("坏文件回退默认骨架", cfg.get("instances") == [])
        bads = [x for x in os.listdir(os.path.dirname(badp)) if x.startswith(os.path.basename(badp) + ".bad_")]
        check("坏文件已留底 .bad_<ts>", len(bads) == 1, bads)
    finally:
        config.CONFIG_PATH = real
        for x in os.listdir(os.path.dirname(badp)):
            if x.startswith(os.path.basename(badp)):
                try:
                    os.remove(os.path.join(os.path.dirname(badp), x))
                except OSError:
                    pass

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
