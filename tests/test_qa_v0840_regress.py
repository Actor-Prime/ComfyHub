# -*- coding: utf-8 -*-
"""QA 独立验证 V8：全量回归 + 编译检查 + 测试隔离性实测

三件事：
  ① 跑 LEAD 指定的 15 个回归用例（逐个起子进程，各自独立环境，互不污染）
  ② `python -m compileall -q app`
  ③ ★测试隔离性：跑完 `test_remote_ui.py` 之后，**真实的**
     `%LOCALAPPDATA%\\ComfyHub\\remote` 是否仍为空
     （工程师说曾漏掉 `os.environ["LOCALAPPDATA"]` 重定向，补过一次；这里实测复核）

另外附一份「已知基线失败」的复核结果，不算新缺陷，但要能对上账。

用法：.venv_build/Scripts/python.exe -u tests/test_qa_v0840_regress.py
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv_build", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

SUITE = [
    ("test_api_base", 120),
    ("test_remote_config_v084", 180),
    ("test_remote_ssh", 180),
    ("test_remote_ops", 180),
    ("test_tunnel", 240),
    ("test_cloud_hooks", 180),
    ("test_remote_cleanup", 180),
    ("test_remote_ui", 240),
    ("test_ai_conn", 180),
    ("test_task_notify", 180),
    ("test_batch_runner", 180),
    ("test_secret_store", 120),
    ("test_plugin_deps", 180),
    ("test_close_behavior", 180),
    ("test_styled_bg", 120),
    # 2026-11-25 并入：入口/全链路 + 下拉翻译不许写坏配置（两条都是本次修复的新风险面）
    ("test_qa_v0840_entry", 180),
    ("test_qa_v0840_i18n_save", 180),
    # 2026-11-25 并回：语言切换唯一回归 + 静态词表检查 + 单实例守卫
    ("test_lang_switch", 180),
    ("test_settings_first_save", 180),
    ("test_theme_i18n", 180),
    ("test_single_instance", 150),
]

# 已知基线失败（复核用；失败属预期，不算本版新缺陷）
BASELINE_FAIL = [
    ("test_instance_mount_ui", "FakeProcs 缺 is_own"),
    ("test_save_diff", "FakeProcs 缺 is_own"),
    # 2026-11-25 QA：以下两条已修好，从基线失败清单移出，改为正式回归用例
    #   test_lang_switch       —— 补 confirm_changes 打桩 + APPDATA 隔离，现 10/10
    #   test_settings_first_save —— D-09 收尾修复，现 10/10 rc=0
    ("test_ui_state", "发布者父键 1 项"),
    ("test_open_web", "需真实实例"),
]

res = []


def check(name, ok, detail=""):
    res.append((name, bool(ok), str(detail)))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))


def run_one(name: str, timeout: int) -> tuple:
    """跑一个用例；返回 (returncode, 末行摘要, 全输出)"""
    path = os.path.join(ROOT, "tests", name + ".py")
    if not os.path.isfile(path):
        return (-404, "文件不存在", "")
    try:
        p = subprocess.run([PY, "-u", path], cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True, errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        tail = [ln for ln in out.strip().splitlines() if ln.strip()]
        summary = tail[-1] if tail else ""
        return (p.returncode, summary[:110], out)
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "replace")
               if isinstance(e.stdout, (bytes, bytearray)) else str(e.stdout or ""))
        return (-1, "超时（%ss）" % timeout, out)


print("== V8-1 指定回归用例（%d 个）==" % len(SUITE))
for name, tmo in SUITE:
    rc, summary, _out = run_one(name, tmo)
    check(f"V8-1 {name} rc=0", rc == 0, f"rc={rc} {summary}")

print("== V8-2 python -m compileall -q app ==")
p = subprocess.run([PY, "-m", "compileall", "-q", "app"], cwd=ROOT,
                   capture_output=True, text=True)
_cc_out = ((p.stdout or "") + (p.stderr or ""))[:160]
check("V8-2a compileall 返回 0", p.returncode == 0, _cc_out)
check("V8-2b compileall 无输出（无语法错/无警告）",
      not (p.stdout or "").strip() and not (p.stderr or "").strip(),
      (p.stdout or "") + (p.stderr or ""))

print("== V8-3 ★测试隔离性：真实 %LOCALAPPDATA%\\ComfyHub\\remote 不许被写 ==")
_real = os.path.join(os.environ.get("LOCALAPPDATA")
                     or os.path.expanduser("~"), "ComfyHub", "remote")


def _snapshot(dirpath: str) -> set:
    out = set()
    if not os.path.isdir(dirpath):
        return out
    for root, _d, names in os.walk(dirpath):
        for n in names:
            out.add(os.path.relpath(os.path.join(root, n), dirpath))
    return out


_before = _snapshot(_real)
# 只跑工程师点名补过环境重定向的那一个用例
rc_ui, summary_ui, _ = run_one("test_remote_ui", 240)
_after = _snapshot(_real)
_new = _after - _before
check("V8-3a test_remote_ui 本身跑通", rc_ui == 0, f"rc={rc_ui} {summary_ui}")
check("V8-3b 跑完后真实 remote 目录**没有新增任何文件**", not _new,
      sorted(_new))
print(f"    实测路径：{_real}（跑前 {len(_before)} 个文件，跑后 {len(_after)} 个）")
check("V8-3c 该目录要么不存在、要么为空（当前 %s 个文件）" % len(_after),
      len(_after) == 0, sorted(_after)[:5])

print("== V8-4 已知基线失败复核（不算新缺陷，只对账）==")
for name, why in BASELINE_FAIL:
    tmo = 45 if name == "test_lang_switch" else 150
    rc, summary, _o = run_one(name, tmo)
    print(f"  · {name:24s} rc={rc:<4} 预期失败原因：{why}")
    print(f"      实测：{summary}")
    # 只记录不判失败：这一批是既有问题，不属于本版责任范围
    if rc == 0:
        print(f"      ⚠ 该项现在能跑通了（不在基建失败清单里了）——请 LEAD 更新基线")
    res.append((f"V8-4 {name} 基线复核（rc={rc}）", True, summary))

print()
bad = [n for n, ok, _ in res if not ok]
print(f"V8 结果：{len(res) - len(bad)}/{len(res)} 通过" +
      ("" if not bad else "  失败：" + "、".join(bad)))
sys.exit(1 if bad else 0)
