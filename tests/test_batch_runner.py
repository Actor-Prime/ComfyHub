# -*- coding: utf-8 -*-
"""批量任务：工作流格式校验 + 摘要解析（不真跑出图）"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import batch_runner as br

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def wf(obj):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(obj, f, ensure_ascii=False)
    f.close()
    return f.name


print("=== 1. API 格式工作流可读 ===")
api = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}},
       "4": {"class_type": "CheckpointLoaderSimple", "inputs": {}}}
p1 = wf(api)
try:
    d = br.load_api_prompt(p1)
    check("解析成功", len(d) == 2, str(list(d)))
except Exception as e:
    check("解析成功", False, str(e))
os.unlink(p1)

print("\n=== 2. 包裹格式 {prompt: {...}} 也能读 ===")
p2 = wf({"prompt": api, "client_id": "x"})
try:
    d = br.load_api_prompt(p2)
    check("解包成功", len(d) == 2)
except Exception as e:
    check("解包成功", False, str(e))
os.unlink(p2)

print("\n=== 3. 前端界面工作流应友好拒绝 ===")
p3 = wf({"nodes": [], "links": [], "version": 0.4})
try:
    br.load_api_prompt(p3)
    check("应拒绝前端格式", False, "没有报错")
except ValueError as e:
    check("拒绝并给出指引", ("Export (API)" in str(e)), str(e)[:60])
os.unlink(p3)

print("\n=== 4. 摘要解析 ===")
ok, s = br.summarize({"status": {"status_str": "success"},
                      "outputs": {"9": {"images": [{"filename": "a.png"}]}}})
check("成功+1张图", ok and "1 张" in s, s)

ok2, s2 = br.summarize({"status": {"status_str": "error",
                                   "messages": [["execution_error",
                                                 {"exception_message": "OOM"}]]}})
check("错误摘要透出原因", (not ok2) and "OOM" in s2, s2)

print("\n=== 5. 无 class_type 的假 API 格式应拒绝 ===")
p4 = wf({"a": {"inputs": {}}, "b": {"inputs": {}}})
try:
    br.load_api_prompt(p4)
    check("应拒绝", False, "没有报错")
except ValueError as e:
    check("拒绝无 class_type 的工作流", "class_type" in str(e), str(e)[:50])
os.unlink(p4)

passed = sum(1 for _, o, _ in res if o)
print("\n" + "=" * 46)
print(f"结果：{passed}/{len(res)} 通过")
for n, o, d in res:
    if not o:
        print(f"  ❌ {n}  [{d}]")
sys.exit(0 if passed == len(res) else 1)
