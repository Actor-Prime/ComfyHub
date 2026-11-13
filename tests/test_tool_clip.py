# -*- coding: utf-8 -*-
"""工具结果裁剪回归（2026-11-13）

背景：旧 `_clip()` 直接 `json.dumps(...)[:3000]`，实测真实 ComfyUI 日志
尾部 50 行是 4099 字符，从头部切会：
  · 把 JSON 切成半截（模型拿到非法 JSON 只能猜字段）；
  · 丢掉数组尾部的 traceback 收尾和真正的报错行（排查最需要的东西）。

新实现三级降级：日志按行裁剪（保尾部 + 抢回报错行）→ 结构裁剪 → 硬切。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ai_agent import (MAX_TOOL_RESULT, _clip, _clip_lines,  # noqa: E402
                          _shrink)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("  ← " + str(detail) if detail else ""))


def make_log(n_normal=45, err_tail=5):
    """构造一段「报错全在尾部」的日志（真实日志就是这样）"""
    lines = []
    for i in range(n_normal):
        lines.append(f"[2026-11-10 01:1{i % 60}:{i % 60:02d}.100] Prompt executed in "
                     f"{(i * 7) % 59}.{i % 10} seconds  prompt_id=abc-{i}")
    for i in range(err_tail):
        lines.append(f"[2026-11-10 01:16:37.297] Traceback (most recent call last) "
                     f"step {i}")
    lines.append("[2026-11-10 01:16:37.297] TimeoutError")
    return lines


def json_ok(s):
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def run():
    print("== A. 日志裁剪：保尾部 + 报错行 ==")
    log_file = r"F:/ComfyHub_testdata/ComfyUIH3/user/comfyui_8188.log"
    lines = make_log()
    raw = json.dumps({"ok": True, "log_file": log_file, "lines": lines},
                     ensure_ascii=False)
    check("构造数据确实超过上限", len(raw) > MAX_TOOL_RESULT, len(raw))

    old = raw[:MAX_TOOL_RESULT] + "…(截断)"
    check("旧实现会切出非法 JSON（复现 bug）", not json_ok(old))

    out = _clip({"ok": True, "log_file": log_file, "lines": lines})
    check("新实现不超上限", len(out) <= MAX_TOOL_RESULT, len(out))
    check("新实现是合法 JSON", json_ok(out), out[:80])

    d = json.loads(out)
    check("保留了 lines 数组", isinstance(d.get("lines"), list) and d["lines"])
    check("标出省略行数", isinstance(d.get("omitted_lines"), int) and d["omitted_lines"] > 0,
          d.get("omitted_lines"))

    tail_src = [x for x in lines if "Traceback" in x or "TimeoutError" in x]
    kept = d["lines"]
    survived = sum(1 for x in tail_src if x in kept)
    check("尾部报错行全部保住", survived == len(tail_src),
          f"{survived}/{len(tail_src)}")
    check("最后一行是带报错的最新日志", "TimeoutError" in kept[-1], kept[-1])
    check("开头有省略提示", kept[0].startswith("…(省略"), kept[0])

    print("== B. 短结果不动 ==")
    small = {"ok": True, "lines": ["a", "b", "c"]}
    s = _clip(small)
    check("短日志原样保留", json.loads(s)["lines"] == ["a", "b", "c"], s)
    check("短日志不加省略标记", "omitted_lines" not in json.loads(s), s)

    print("== C. 单行超长兜底 ==")
    huge = {"ok": True, "lines": ["x" * 9000]}
    out_h = _clip(huge)
    dh = json.loads(out_h)
    check("单行超长时仍不超上限", len(out_h) <= MAX_TOOL_RESULT, len(out_h))
    check("单行超长时仍有内容（不是空数组）", bool(dh.get("lines")), dh)
    check("单行超长时截断并留省略号", dh["lines"][-1].endswith("…"), dh["lines"][-1][-20:])

    print("== D. 结构裁剪：大 JSON 变合法且瘦身 ==")
    models = {"ok": True, "source": "api",
              "counts": {k: 20 for k in ("checkpoints", "loras", "vae",
                                         "text_encoders", "diffusion_models")},
              "samples": {k: [f"{k}_{i}.safetensors" for i in range(20)]
                          for k in ("checkpoints", "loras", "vae",
                                    "text_encoders", "diffusion_models")}}
    check("大模型清单确实超上限",
          len(json.dumps(models, ensure_ascii=False)) > MAX_TOOL_RESULT, "")
    out_m = _clip(models)
    check("结构裁剪后是合法 JSON", json_ok(out_m), out_m[:80])
    dm = json.loads(out_m)
    check("counts 完整保留（数量信息不丢）",
          dm.get("counts", {}).get("loras") == 20, dm.get("counts"))
    one = dm.get("samples", {}).get("loras") or []
    check("samples 每类被裁到 5 条 + 计数尾巴", len(one) == 6 and "共 20 项" in one[-1],
          one)

    print("== E. _shrink 行为 ==")
    sh = _shrink({"a": [1] * 50, "b": "y" * 500, "c": {"d": list(range(10))}})
    check("长列表裁到 6 项（5 + 计数）", len(sh["a"]) == 6, len(sh["a"]))
    check("长字符串裁到 300+省略号", sh["b"].endswith("…") and len(sh["b"]) == 301,
          len(sh["b"]))
    check("嵌套列表也被裁", len(sh["c"]["d"]) == 6, len(sh["c"]["d"]))

    print("== F. _clip_lines 预算控制 ==")
    got, omitted = _clip_lines(make_log(), 800)
    check("预算 800 时不超预算太多", sum(len(x) + 3 for x in got) <= 800 + 60,
          sum(len(x) + 3 for x in got))
    check("预算 800 时仍保住 TimeoutError", any("TimeoutError" in x for x in got), got[-2:])
    check("omitted 与丢掉的条数一致", omitted == len(make_log()) - (len(got) - 1),
          f"{omitted} vs {len(make_log()) - (len(got) - 1)}")

    print("== G. 枚举类列表不截断（2026-11-14 实测 bug）==")
    # 现场：inspect_workflow 返回 33 个节点，被通用规则裁到 5 个 + "…共 33 项"，
    # 模型定位不到报错节点，只能回「信息不足」再问一轮。
    # 只用模块别名调用：一旦在函数里写 `_shrink = ...`，Python 就会把整个
    # run() 内的 _shrink 视为局部变量，前面 E 段的全局引用直接 UnboundLocalError
    from app import ai_agent as _ag
    from app import ai_tools

    many = {"node_count": 33,
            "nodes": [{"key": f"n{i}", "type": f"T{i}"} for i in range(33)]}
    sh = _ag._shrink(many)
    check("G1 nodes 列表全量保留", len(sh["nodes"]) == 33, len(sh["nodes"]))
    check("G2 nodes 里没有「共 N 项」占位",
          not any(isinstance(x, str) and "共" in x for x in sh["nodes"]),
          str(sh["nodes"][-1]))

    # 普通列表照旧截断（不能因为放开了 nodes 就整体失效）
    other = {"samples": [1, 2, 3, 4, 5, 6, 7, 8]}
    check("G3 普通列表仍截到 5+占位", len(_ag._shrink(other)["samples"]) == 6,
          len(_ag._shrink(other)["samples"]))

    # workflows 也在白名单里
    wl = {"workflows": [f"wf{i}.json" for i in range(20)]}
    check("G4 workflows 不截断", len(_ag._shrink(wl)["workflows"]) == 20,
          len(_ag._shrink(wl)["workflows"]))
    check("G5 白名单内容符合预期",
          _ag.KEEP_LIST_KEYS == frozenset({"nodes", "workflows", "files",
                                       "items", "models"}), str(_ag.KEEP_LIST_KEYS))

    # 真跑一次 inspect_workflow：33 个节点必须全在
    ctx = ai_tools.ToolCtx({}, {"port": 8188, "comfyui_path": "F:/X",
                                "models_dir": ""})
    import os as _os
    # 用本机真实工作流目录取样（没有就跳过，不让测试依赖特定机器）
    REAL_WF_ROOT = "F:/ComfyUI-aki-v3/ComfyUI"
    wf_dir = os.path.join(REAL_WF_ROOT, "user", "default", "workflows")
    ctx.workflows_dir = wf_dir
    if _os.path.isdir(wf_dir):
        names = sorted(f for f in _os.listdir(wf_dir) if f.endswith(".json"))
        if names:
            r = ai_tools.execute("inspect_workflow", {"name": names[0]}, ctx)
            got = json.loads(_clip(r))
            n = r.get("node_count") or 0
            check(f"G6 真实工作流 {n} 个节点全量返回",
                  len(got.get("nodes") or []) == n, len(got.get("nodes") or []))
            check("G7 放宽预算后仍不超 BIG_RESULT",
                  len(_clip(r)) <= _ag.BIG_RESULT, len(_clip(r)))
            check("G8 空字段不占位（title 为空就不写）",
                  all("title" not in x for x in (got.get("nodes") or [])
                      if not x.get("title")),
                  str([x for x in (got.get("nodes") or []) if not x.get("title")][:1]))
        else:
            check("G6 跳过（工作流目录为空）", True)
    else:
        check("G6 跳过（无真实工作流目录）", True)

    print(f"\nPASS {PASS} / FAIL {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
