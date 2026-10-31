# -*- coding: utf-8 -*-
"""AI 接入回归（2026-11-01）：测试接口 / 拉模型清单 / 绑定模型

覆盖：
1. pick_chat_model：跳过 asr/tts 等非对话模型；用户指定优先；空清单兜底
2. list_models / test_connection：成功、拉清单失败、对话失败三条分支（打桩 _request）
3. 设置页：有「测试接口」按钮、模型名是可编辑下拉、测试回填并与保存联动

用法：.venv_build/Scripts/python.exe tests/test_ai_conn.py
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication([])
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)

from app import ai_assist  # noqa: E402

# -------------------------------------------------- 1. 对话模型优选
models = ["mimo-v2.5-asr", "mimo-v2.5-tts", "mimo-v2.5-tts-voiceclone",
          "mimo-v2.5-tts-voicedesign", "mimo-v2.6-flash", "mimo-v2.6-pro"]
check("1a 自动跳过语音模型", ai_assist.pick_chat_model(models) == "mimo-v2.6-flash",
      ai_assist.pick_chat_model(models))
check("1b 用户指定优先", ai_assist.pick_chat_model(models, "mimo-v2.6-pro") == "mimo-v2.6-pro")
check("1c 指定的不在清单里 → 回落优选",
      ai_assist.pick_chat_model(models, "not-exist") == "mimo-v2.6-flash")
check("1d 空清单返回空", ai_assist.pick_chat_model([]) == "")
check("1e 全是语音模型时兜底取第一个",
      ai_assist.pick_chat_model(["x-asr", "y-tts"]) == "x-asr")

# -------------------------------------------------- 2. 接口调用（打桩 _request）
_orig = ai_assist._request


def fake_ok(url, api_key, data=None, timeout=20):
    if url.endswith("/models"):
        return {"object": "list", "data": [{"id": i} for i in models]}
    body = json.loads(data.decode("utf-8"))
    assert body["model"] and body["messages"], body
    return {"choices": [{"message": {"content": "你好"}}]}


ai_assist._request = fake_ok
check("2a 列表按字母序返回",
      ai_assist.list_models("https://x.test/v1", "k") == sorted(models))
r = ai_assist.test_connection("https://x.test", "k", "")
check("2b 未指定模型 → 选对话模型并对话成功",
      r["ok"] and r["model"] == "mimo-v2.6-flash" and r["reply"] == "你好", str(r))
check("2c 返回模型清单供下拉", r["models"] == sorted(models))
check("2d 地址自动补 /v1", ai_assist._v1_base("https://x.test") == "https://x.test/v1")
check("2e 已带 /v1 不重复拼", ai_assist._v1_base("https://x.test/v1/") == "https://x.test/v1")


def fake_list_fail(url, api_key, data=None, timeout=20):
    raise OSError("connection refused")


ai_assist._request = fake_list_fail
r2 = ai_assist.test_connection("https://x.test", "k", "")
check("2f 拉列表失败 → 明确报错", not r2["ok"] and "拉取模型列表失败" in r2["msg"], r2["msg"])
check("2g 失败时 ok=False 且模型为空", r2["model"] == "" and r2["models"] == [])


def fake_chat_fail(url, api_key, data=None, timeout=20):
    if url.endswith("/models"):
        return {"data": [{"id": "m"} for m in models]}
    raise OSError("timeout")


ai_assist._request = fake_chat_fail
r3 = ai_assist.test_connection("https://x.test", "k", "")
check("2h 对话失败 → 报对话请求失败", not r3["ok"] and "对话请求失败" in r3["msg"], r3["msg"])
ai_assist._request = _orig

# -------------------------------------------------- 3. 设置页
from app.ui.pages import settings_page as sp_mod  # noqa: E402


class FakeMain:
    def current_inst(self):
        return None


sp_mod.save_config = lambda cfg: None
cfg = {"settings": {"ai_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                    "ai_api_key": "tp-test", "ai_model": "mimo-v2.6-flash"}}
page = sp_mod.SettingsPage(cfg, FakeMain())

check("3a 有「测试接口」按钮", page.b_ai_test.text() == "测试接口", page.b_ai_test.text())
check("3b 模型名是可编辑下拉", page.cb_ai_model.isEditable())
check("3c 下拉预置已保存模型", page.cb_ai_model.currentText() == "mimo-v2.6-flash",
      page.cb_ai_model.currentText())
check("3d 初始无状态文字", page.lb_ai_state.text() == "")

# 模拟后台线程回调：应把模型灌进下拉并选中对话模型
page._on_ai_tested({"ok": True, "models": sorted(models),
                    "model": "mimo-v2.6-flash", "latency_ms": 1500,
                    "reply": "你好", "msg": "连接正常：6 个模型，用时 1.5s"})
items = [page.cb_ai_model.itemText(i) for i in range(page.cb_ai_model.count())]
check("3e 回填后下拉有 6 个模型", len(items) == 6, str(items))
check("3f 自动选中对话模型", page.cb_ai_model.currentText() == "mimo-v2.6-flash",
      page.cb_ai_model.currentText())
check("3g 状态条显示连接正常", "连接正常" in page.lb_ai_state.text(), page.lb_ai_state.text())

page._on_ai_tested({"ok": False, "models": [], "model": "", "latency_ms": 0,
                    "reply": "", "msg": "对话请求失败：OSError: timeout"})
check("3h 失败状态条显示原因", "失败" in page.lb_ai_state.text(), page.lb_ai_state.text())

# 选另一个模型后保存 → 绑定写入设置
page.cb_ai_model.setCurrentText("mimo-v2.6-pro")
page._save()
check("3i 保存后 ai_model 跟随下拉",
      cfg["settings"].get("ai_model") == "mimo-v2.6-pro", str(cfg["settings"].get("ai_model")))

# 缺地址/Key 时点测试：不崩、给出提示
page.ed_ai_key.setText("")
page._test_ai()
check("3j 缺 Key 时测试被拦下（不起线程）",
      not (getattr(page, "_ai_worker", None) and page._ai_worker.isRunning()))

# -------------------------------------------------- 汇总
fails = [r for r in RESULTS if not r[1]]
print("=" * 50)
print(f"AI 接入回归：{len(RESULTS) - len(fails)}/{len(RESULTS)} 通过")
if fails:
    for n, _, d in fails:
        print(f"  FAIL {n}  [{d}]")
sys.exit(1 if fails else 0)
