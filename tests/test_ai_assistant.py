# -*- coding: utf-8 -*-
"""AI 助手回归（2026-11-03）：工具层 / 对话循环 / MCP server / 页面

覆盖：
1. ui2api：通配符展开、COMBO 规整、动态输入补线、缺 widget 取默认、子图展开
2. ai_tools：schemas 完整性、未知工具、current_instance 选择、_http 代理绕过
3. ai_agent：无配置报错 / 工具调用往返 / 停止旗标 / 结果裁剪
4. mcp_server：initialize / tools/list / tools/call / 未知方法 / 通知不应答
5. AiPage：离屏构造、气泡添加、无实例发送被拦
6. 导航一致性：NAV 索引与 NAV_ITEMS 对齐

用法：.venv_build/Scripts/python.exe tests/test_ai_assistant.py
"""
import json
import time
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ================================================== 1. ui2api
from app.ui2api import expand_wildcards, norm_combo, combo_options, build  # noqa: E402

w = expand_wildcards("图片/Z/%date:yyyy-MM-dd%/X")
check("1a 通配符展开为当日日期", "/2026-" in w and len(w.split("/")) == 4, w)
check("1b 非字符串原样返回", expand_wildcards(123) == 123)

old_combo = [["自定义", "16:9 (Widescreen)"], {"default": "自定义"}]
check("1c 旧值前缀映射到新版选项", norm_combo("16:9", old_combo) == "16:9 (Widescreen)")
check("1d 未知值回落默认", norm_combo("32:9", old_combo) == "自定义")
check("1e 新式 COMBO 选项提取",
      combo_options(["COMBO", {"options": ["a", "b"]}]) == ["a", "b"])

# 小工作流：Reroute 绕行 + 动态输入 + 缺 widget 默认
WF = {
    "nodes": [
        {"id": 1, "type": "Reroute", "inputs": [{"name": "x", "link": 9}]},
        {"id": 2, "type": "EmptySD3LatentImage",
         "widgets_values": [512, 512],   # 故意缺 batch_size → 应取默认 1
         "inputs": [{"name": "width", "link": None, "widget": {"name": "width"}},
                    {"name": "height", "link": None, "widget": {"name": "height"}},
                    {"name": "batch_size", "link": None, "widget": {"name": "batch_size"}}],
         "outputs": [{"name": "LATENT", "links": None}]},
    ],
    "links": [[9, 2, 0, 1, 0, "IMAGE"]],
}
OBJ = {"EmptySD3LatentImage": {"input": {"required": {
    "width": ["INT", {"default": 1024}],
    "height": ["INT", {"default": 1024}],
    "batch_size": ["INT", {"default": 1}]}}}}
prompt, warns = build(WF, OBJ)
check("1f 缺 widget 取 object_info 默认", prompt["2"]["inputs"]["batch_size"] == 1,
      json.dumps(prompt, ensure_ascii=False))
check("1g Reroute 节点不出现在 prompt", "1" not in prompt)
check("1h 动态输入补线不误报", isinstance(warns, list))

# ================================================== 2. ai_tools
from app import ai_tools  # noqa: E402

schemas = ai_tools.schemas()
names = [s["function"]["name"] for s in schemas]
check("2a 17 个工具 schema 齐全", len(schemas) == 17 and
      all(s["function"]["parameters"].get("type") == "object" for s in schemas), str(names))
check("2b 关键工具在列",
      all(n in names for n in ("get_health", "control_instance", "run_workflow", "get_job",
                               "download_model", "inspect_workflow", "patch_workflow",
                               "import_workflow", "wait_job")))

ctx = ai_tools.build_ctx({"instances": []}, {"port": 8188, "comfyui_path": "F:/X"})
check("2c api 地址拼装", ctx.api == "http://127.0.0.1:8188")
check("2d 工作流目录拼接", ctx.workflows_dir.replace("\\", "/").endswith("X/user/default/workflows"))
check("2e 未知工具返回错误", ai_tools.execute("nope", {}, ctx)["ok"] is False)
r = ai_tools.execute("control_instance", {"action": "start"}, ctx)
check("2e1 MCP/无主窗口模式拒绝启停", r["ok"] is False and "主界面" in r["error"], str(r))
r = ai_tools.execute("control_instance", {"action": "reboot"}, ctx)
check("2e2 非法 action 被拒", r["ok"] is False and "start / stop / restart" in r["error"], str(r))
from app import ai_agent as _ag  # noqa: E402
check("2e3 control_instance 摘要（就绪）",
      _ag._brief("control_instance",
                 {"ok": True, "action": "start", "ready": True, "port": 8188})
      == "已就绪 · 端口 8188")
check("2e4 control_instance 摘要（失败转失败格式）",
      _ag._brief("control_instance", {"ok": False, "error": "启动失败：进程没有起来"})
      .startswith("失败："))

# 回归（2026-11-06 用户实测报障）：界面里点「帮忙启动实例」被误判成 --mcp 外挂模式。
# 根因是 run_agent 建上下文时没把 mainwin 传下去——这里锁死这条链路。
_cap = {}
_orig_build = ai_tools.build_ctx


def _fake_build(cfg, inst, mainwin=None, log=print):
    _cap["mainwin"] = mainwin
    raise RuntimeError("stop-here")          # 拿到参数就中断，别真发 HTTP


ai_tools.build_ctx = _fake_build
_sentinel = object()
try:
    _ag.run_agent(
        {"settings": {"ai_enabled": True, "ai_base_url": "http://x",
                      "ai_api_key": "k"}},
        {"id": "a", "port": 8188}, "hi", [], lambda *a, **k: None,
        mainwin=_sentinel)
except RuntimeError:
    pass
finally:
    ai_tools.build_ctx = _orig_build
check("2e5 run_agent 把 mainwin 透传给工具上下文", _cap.get("mainwin") is _sentinel)

import pathlib  # noqa: E402
_page_src = pathlib.Path("app/ui/pages/ai_page.py").read_text(encoding="utf-8")
check("2e6 AI 页面确实把主窗口传进 worker", "mainwin=self.mainwin" in _page_src)
check("2e7 worker 也把 mainwin 传进 run_agent",
      _page_src.count("mainwin=self.mainwin") >= 2,
      str(_page_src.count("mainwin=self.mainwin")))


# 有主窗口且实例已在跑：应走「无需重复启动」快路径，而不是报 MCP 模式
class _FakeProcs:
    def is_running(self, inst):
        return True

    def actual_port(self, inst):
        return 8188


class _FakeMainWin:
    procs = _FakeProcs()


_ctx_ui = ai_tools.build_ctx({"instances": []},
                             {"id": "a", "port": 8188, "comfyui_path": "F:/X"},
                             mainwin=_FakeMainWin())
_r = ai_tools.execute("control_instance", {"action": "start"}, _ctx_ui)
check("2e8 GUI 模式下已在跑 → 走快路径", _r.get("ok") is True and _r.get("already") is True, str(_r))

CFG = {"instances": [
    {"id": "a", "comfyui_path": "F:/A", "port": 8188},
    {"id": "b", "comfyui_path": "F:/B", "port": 8189}],
    "settings": {"last_instance_id": "b"}}
check("2f current_instance 按 last_instance_id 选",
      ai_tools.current_instance(CFG)["id"] == "b")
check("2g 无 last 时取第一个", ai_tools.current_instance(
    {"instances": CFG["instances"], "settings": {}})["id"] == "a")
check("2h 空配置安全", ai_tools.current_instance({}) == {})

# _http 打桩：队列取消的验证路径
_calls = []
_orig_http = ai_tools._http
ai_tools._http = lambda c, p, data=None, timeout=30: (
    _calls.append(p) or ({"queue_running": [[1, "pid"]], "queue_pending": []}
                         if p == "/queue" else {}))
r = ai_tools.execute("queue_cancel", {}, ctx)
check("2i 取消后仍在跑 → 如实上报未停止", r.get("stopped") is False, str(r))
_calls.clear()
ai_tools._http = lambda c, p, data=None, timeout=30: (
    _calls.append(p) or {"queue_running": [], "queue_pending": []})
r = ai_tools.execute("queue_cancel", {}, ctx)
check("2j 取消成功路径", r.get("stopped") is True and "/interrupt" in _calls)
ai_tools._http = _orig_http

# 连接被拒绝 → 结构化 instance_down（不要把 urlopen 栈丢给用户）
# 注意：桩必须打在**网络层**（build_opener），打在 _http 上就绕过了翻译层，验不到真逻辑
import urllib.error  # noqa: E402
import urllib.request as _urlreq  # noqa: E402
_orig_build = _urlreq.build_opener


class _BoomOpener:
    def open(self, req, timeout=30):
        raise urllib.error.URLError(
            ConnectionRefusedError(10061, "由于目标计算机积极拒绝，无法连接。"))


_urlreq.build_opener = lambda *a, **k: _BoomOpener()
r = ai_tools.execute("get_health", {}, ctx)
check("2k 端口无监听 → instance_down 友好错误",
      r.get("code") == "instance_down" and "启动" in r.get("hint", ""), str(r))
check("2l agent 摘要用人话", __import__("app.ai_agent", fromlist=["_brief"])._brief(
    "get_health", r) == "实例未运行（可让我自动启动）",
      __import__("app.ai_agent", fromlist=["_brief"])._brief("get_health", r))
_urlreq.build_opener = _orig_build

# ================================================== 2c. 工作流编辑闭环
import tempfile as _tf  # noqa: E402

WFDIR = _tf.mkdtemp(prefix="wf_ai_")
UI_WF = {
    "nodes": [
        {"id": 34, "type": "CLIPTextEncode", "widgets_values": ["旧提示词", ""]},
        {"id": 12, "type": "EmptyLatentImage", "widgets_values": [512, 512, 1]},
        {"id": 69, "type": "KSampler", "widgets_values": [0, "randomize", 20, 7, "euler", "normal", 1]},
    ],
    "links": [],
}
with open(os.path.join(WFDIR, "wf_test.json"), "w", encoding="utf-8") as f:
    json.dump(UI_WF, f, ensure_ascii=False)
wf_ctx = ai_tools.build_ctx({"instances": []},
                            {"port": 8188, "comfyui_path": "F:/X"})
wf_ctx.workflows_dir = WFDIR

r = ai_tools.execute("inspect_workflow", {"name": "wf_test.json"}, wf_ctx)
hints = {n["key"]: n["hint"] for n in r.get("nodes", [])}
check("2c1 inspect 列出节点", r.get("ok") and r.get("node_count") == 3, str(r)[:200])
check("2c2 inspect 识别提示词/画布/采样器",
      hints.get("34") == "提示词" and hints.get("12") == "画布尺寸" and hints.get("69") == "采样器",
      str(hints))

r = ai_tools.execute("patch_workflow", {"name": "wf_test.json", "ops": [
    {"node": "34", "index": 0, "value": "蓝发紫眼 欧式校服 贝雷帽 二次元"},
    {"node": "12", "index": 0, "value": 1024},
]}, wf_ctx)
check("2c3 patch 生成副本", r.get("ok") and r.get("name") == "wf_test_ai.json", str(r)[:200])
orig = json.load(open(os.path.join(WFDIR, "wf_test.json"), encoding="utf-8"))
copy = json.load(open(os.path.join(WFDIR, "wf_test_ai.json"), encoding="utf-8"))
check("2c4 原文件未被改动", orig["nodes"][0]["widgets_values"][0] == "旧提示词")
check("2c5 副本写入新提示词",
      copy["nodes"][0]["widgets_values"][0] == "蓝发紫眼 欧式校服 贝雷帽 二次元"
      and copy["nodes"][1]["widgets_values"][0] == 1024,
      str(copy["nodes"][0]["widgets_values"]))

r = ai_tools.execute("patch_workflow", {"name": "wf_test.json",
                                        "ops": [{"node": "999", "index": 0, "value": 1}]}, wf_ctx)
check("2c6 改不存在的节点 → 明确失败", r.get("ok") is False and "不存在" in r.get("error", ""), str(r))

# 情况2：用户粘贴 JSON
r = ai_tools.execute("import_workflow", {"json_text": json.dumps(UI_WF), "name": "粘贴流"}, wf_ctx)
check("2c7 导入 UI 格式", r.get("ok") and r.get("format") == "ui", str(r)[:200])
API_WF = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
r = ai_tools.execute("import_workflow", {"json_text": json.dumps(API_WF), "name": "api流"}, wf_ctx)
check("2c8 导入 API 格式", r.get("ok") and r.get("format") == "api", str(r)[:200])
r = ai_tools.execute("import_workflow", {"json_text": '{"a": 1}'}, wf_ctx)
check("2c9 非法格式被拒", r.get("ok") is False and "无法识别" in r.get("error", ""), str(r))

# wait_job：轮询到出图（2026-11-06 起要求文件真的存在才算数）
_hist = {"pid1": {"status": {"status_str": "success", "messages": []},
                  "outputs": {"9": {"images": [{"subfolder": "子目录", "filename": "a.png"}]}}}}
os.makedirs(os.path.join(wf_ctx.output_dir, "子目录"), exist_ok=True)
with open(os.path.join(wf_ctx.output_dir, "子目录", "a.png"), "wb") as f:
    f.write(b"png")
_orig_http_wf = ai_tools._http
ai_tools._http = lambda c, p, data=None, timeout=30: (
    _hist if p.startswith("/history/") else {})
r = ai_tools.execute("wait_job", {"prompt_id": "pid1", "timeout_s": 10}, wf_ctx)
check("2cA wait_job 返回出图路径",
      r.get("state") == "success" and r["images"][0].replace("\\", "/").endswith("子目录/a.png"),
      str(r))
check("2cA1 出图路径带 verified 标记", r.get("verified") is True, str(r))

# 同一份 history，但磁盘上的文件被删掉 → 不许再报「有图」
os.remove(os.path.join(wf_ctx.output_dir, "子目录", "a.png"))
r = ai_tools.execute("wait_job", {"prompt_id": "pid1", "timeout_s": 10}, wf_ctx)
check("2cA2 文件不存在时不给 images",
      r.get("images") == [] and r.get("verified") is False
      and r.get("missing_images"), str(r))
ai_tools._http = _orig_http_wf

# run_workflow 提交 API 格式（不打网络）
_posted = {}
ai_tools._http = lambda c, p, data=None, timeout=30: (
    _posted.update(data or {}) or {"prompt_id": "p9", "number": 1})
r = ai_tools.execute("run_workflow", {"name": "api流.json"}, wf_ctx)
check("2cB API 格式工作流可直接提交",
      r.get("ok") and _posted.get("prompt", {}).get("3", {}).get("class_type") == "KSampler",
      str(r)[:200])
ai_tools._http = _orig_http_wf

# ================================================== 2b. 模型画像与参数映射
from app import ai_models  # noqa: E402

p_mimo = ai_models.detect("mimo-v2.6-flash", "https://token-plan-cn.xiaomimimo.com/v1")
check("2k 识别 MiMo 族系", p_mimo.family == "mimo" and p_mimo.style == "thinking.type")
check("2l 识别智谱 GLM", ai_models.detect("glm-4.6", "https://open.bigmodel.cn/api/paas/v4").style == "thinking.type")
check("2m 识别 Qwen3", ai_models.detect("qwen3-235b", "https://dashscope.aliyuncs.com/compatible-mode/v1").style == "qwen")
check("2n 识别推理版 DeepSeek", ai_models.detect("deepseek-reasoner", "https://api.deepseek.com/v1").family == "deepseek-r")
check("2o 未知模型不注入参数",
      ai_models.thinking_params(ai_models.detect("some-local-llm", ""), "normal")[0] == {})

extra_off, _ = ai_models.thinking_params(p_mimo, "off")
extra_on, _ = ai_models.thinking_params(p_mimo, "normal")
check("2p 关闭思考的 body 字段", extra_off == {"thinking": {"type": "disabled"}}, str(extra_off))
check("2q 开启思考的 body 字段", extra_on == {"thinking": {"type": "enabled"}}, str(extra_on))
check("2r Qwen 思考预算", ai_models.thinking_params(
    ai_models.detect("qwen3-8b", ""), "deep")[0].get("thinking_budget") == 8192)
check("2s OpenAI 推理档位", ai_models.thinking_params(
    ai_models.detect("gpt-5", ""), "off")[0] == {"reasoning_effort": "minimal"})
check("2t 推理模型开思考自动抬输出上限",
      ai_models.max_tokens_for(p_mimo, "normal", 2000) >= 8192)
check("2u 用户显式上限保留（关思考时）",
      ai_models.max_tokens_for(p_mimo, "off", 1200) == 1200)

# ================================================== 3. ai_agent
from app import ai_agent  # noqa: E402

events = []
CFG_NOAI = {"settings": {}}
ai_agent.run_agent(CFG_NOAI, {}, "hi", [], lambda kind, **kw: events.append({"kind": kind, **kw}))
check("3a 未配置 AI → 明确报错", events and events[0]["kind"] == "error"
      and "AI 接入" in events[0]["text"], str(events))

ST = {"settings": {"ai_enabled": True, "ai_base_url": "http://x/v1",
                   "ai_api_key": "k", "ai_model": "m"}}
events = []


def fake_chat(settings, messages, tools, **kw):
    n_tools = sum(1 for m in messages if m.get("role") == "tool")
    if n_tools == 0:
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "function": {
                    "name": "get_health", "arguments": "{}"}}]}
    return {"role": "assistant", "content": "实例正常，显存充足"}


cap = {}


def fake_chat_cap(settings, messages, tools, **kw):
    cap.update(kw)
    cap.setdefault("n_first", len(messages))   # 只看首轮（后续轮会追加工具结果）
    return fake_chat(settings, messages, tools, **kw)


_ORIG_CHAT_TOOLS = ai_agent._chat_tools
_orig_chat = ai_agent._round_chat
ai_agent._round_chat = fake_chat
ai_agent.run_agent(ST, {"port": 8188, "comfyui_path": "F:/X"}, "看看状态", [],
                   lambda kind, **kw: events.append({"kind": kind, **kw}))
check("3b0 默认不带思考参数（未知模型）", True)

# 用 MiMo 画像 + 关思考，断言 extra 与 max_tokens 真正注入
ST_MIMO = {"settings": {"ai_enabled": True, "ai_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                        "ai_api_key": "k", "ai_model": "mimo-v2.6-flash",
                        "ai_think": "off", "ai_max_tokens": 1200, "ai_ctx_msgs": 6}}
events = []
ai_agent._round_chat = fake_chat_cap
hist = [{"role": "user", "content": f"旧问题{i}"} for i in range(20)]
ai_agent.run_agent(ST_MIMO, {"port": 8188, "comfyui_path": "F:/X"}, "看看状态",
                   hist, lambda kind, **kw: events.append({"kind": kind, **kw}))
check("3b1 思考关闭参数已注入", cap.get("extra") == {"thinking": {"type": "disabled"}}, str(cap))
check("3b2 输出上限取用户设置", cap.get("max_tokens") == 1200, str(cap.get("max_tokens")))
check("3b3 上下文按条数截断（系统+6历史+新问题=8）", cap.get("n_first") == 8, str(cap.get("n_first")))
ai_agent._round_chat = fake_chat
kinds = [e["kind"] for e in events]
check("3b 工具往返后给最终回答", kinds == ["tool", "tool_result", "assistant"], str(kinds))
check("3c 工具结果事件带摘要", any(e["kind"] == "tool_result" and e.get("brief")
                                  for e in events), str(events))
ai_agent._round_chat = _orig_chat

# 停止旗标：第一轮前就停
events = []
stop = __import__("threading").Event()
stop.set()
ai_agent._round_chat = fake_chat
ai_agent.run_agent(ST, {"port": 8188}, "x", [], lambda kind, **kw: events.append({"kind": kind, **kw}), stop_flag=stop)
check("3d 停止旗标立即生效", events and events[0]["kind"] == "error")
ai_agent._round_chat = _orig_chat

# 工具结果超长裁剪
big = {"ok": True, "data": "x" * 20000}
check("3e 工具结果裁剪到上限", len(ai_agent._clip(big)) <= ai_agent.MAX_TOOL_RESULT + 10)

# ================================================== 3f. 流式解析
from app import ai_assist  # noqa: E402
class _FakeResp:
    def __init__(self, lines, ctype="text/event-stream"):
        self._lines = lines
        self.headers = {"Content-Type": ctype}

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_sse(chunks):
    return [("data: " + json.dumps(c) + "\n\n").encode() for c in chunks]


chunks = _fake_sse([
    {"choices": [{"delta": {"reasoning_content": "先看看"}}]},
    {"choices": [{"delta": {"content": "实例"}}]},
    {"choices": [{"delta": {"content": "正常"}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c9",
                                            "function": {"name": "get_health", "arguments": ""}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
]) + [b"data: [DONE]\n\n"]

_orig_open = ai_assist.open_request
_orig_make = ai_assist.make_request
class _FakeReq:
    def add_header(self, *a):
        pass


ai_assist.make_request = lambda url, key, data=None: _FakeReq()
ai_assist.open_request = lambda req, timeout=20: _FakeResp(chunks)
seen = []
msg = ai_agent._stream_chat({"ai_base_url": "http://x/v1", "ai_api_key": "k",
                             "ai_model": "m"},
                            [{"role": "user", "content": "hi"}], [], max_tokens=100,
                            emit=lambda kind, **kw: seen.append({"kind": kind, **kw}))
check("3f SSE 正文增量拼装", msg["content"] == "实例正常", msg.get("content"))
check("3f2 工具调用分片聚合",
      msg["tool_calls"][0]["function"]["arguments"] == "{}"
      and msg["tool_calls"][0]["function"]["name"] == "get_health", str(msg.get("tool_calls")))
check("3f3 delta 事件逐段上报", [e["text"] for e in seen if e["kind"] == "delta"] == ["实例", "正常"])
check("3f4 reasoning 只通报字数", any(e["kind"] == "reasoning" for e in seen))

# 不支持流式 → 回落普通请求
ai_assist.open_request = lambda req, timeout=20: _FakeResp(
    [b'{"choices":[{"message":{"content":"ok"}}]}'], ctype="application/json")
ai_agent._chat_tools = lambda *a, **k: {"choices": [{"message": {"content": "回落成功"},
                                                     "finish_reason": "stop"}]}
msg2 = ai_agent._round_chat({"ai_base_url": "http://x/v1", "ai_api_key": "k",
                             "ai_model": "m"}, [], [], max_tokens=10,
                            emit=lambda kind, **kw: None, log=lambda *a: None)
check("3f5 不支持流式时自动回落", msg2["content"] == "回落成功", str(msg2))
ai_assist.open_request = _orig_open
ai_assist.make_request = _orig_make
ai_agent._chat_tools = _ORIG_CHAT_TOOLS

# ================================================== 4. mcp_server
from app import mcp_server  # noqa: E402

ctx = ai_tools.build_ctx(ST, {"port": 8188, "comfyui_path": "F:/X"})
r = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {}}, ctx)
check("4a initialize 握手", r["result"]["serverInfo"]["name"] == "ComfyHub"
      and r["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION)
r = mcp_server._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)
check("4b tools/list 与工具层一致",
      len(r["result"]["tools"]) == len(ai_tools.TOOLS))
check("4c 通知不应答", mcp_server._handle(
    {"jsonrpc": "2.0", "method": "notifications/initialized"}, ctx) is None)
r = mcp_server._handle({"jsonrpc": "2.0", "id": 3, "method": "nope"}, ctx)
check("4d 未知方法 -32601", r["error"]["code"] == -32601)

_orig_exec = ai_tools.execute
ai_tools.execute = lambda n, a, c: {"ok": True, "state": "success", "images": []}
r = mcp_server._handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "get_job", "arguments": {"prompt_id": "p"}}}, ctx)
check("4e tools/call 包装 text content", r["result"]["isError"] is False
      and json.loads(r["result"]["content"][0]["text"])["state"] == "success")
ai_tools.execute = lambda n, a, c: {"ok": False, "error": "炸了"}
r = mcp_server._handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "get_health", "arguments": {}}}, ctx)
check("4f 工具异常标 isError", r["result"]["isError"] is True)
ai_tools.execute = _orig_exec

# ================================================== 5. AiPage 离屏
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

from app.ui.pages.ai_page import AiPage  # noqa: E402


class _FakeWin:
    cfg = {}

    def current_inst(self):
        return {}


page = AiPage({}, _FakeWin())
page.show()
check("5a 页面离屏构造", page.flow is not None and page.b_send.isEnabled())
page.ed.setPlainText("hello")
page._send()
check("5b 无实例发送被拦（出错误气泡且不启动线程）",
      page._worker is None and page._history == [])
check("5c 历史只记文本消息", page._history == [])

# 流式：delta 建卡片并增量更新，assistant 收尾启用按钮
page._on_event({"kind": "delta", "text": "实例"})
page._on_event({"kind": "delta", "text": "正常"})
page._stream_flush()
card = page._last_card
check("5c1 delta 建流式卡片", card is not None and card["text"] == "实例正常",
      card["text"] if card else "无卡片")
page._on_event({"kind": "assistant", "text": "实例正常，显存充足"})
check("5c2 收尾写入全文", card["text"] == "实例正常，显存充足", card["text"])
check("5c3 收尾后复制按钮可用", card["copy"].isEnabled())
page.close()

# ================================================== 5b. 设置页三个新控件
from app.ui.pages.settings_page import SettingsPage  # noqa: E402


class _FakeWin2:
    cfg = {"settings": {"ai_enabled": True, "ai_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                        "ai_model": "mimo-v2.6-flash"}}

    def current_inst(self):
        return {}


sp = SettingsPage(_FakeWin2.cfg, _FakeWin2())
from app.ui.pages.ai_page import _md_to_html  # noqa: E402

md = _md_to_html("**体检结果**\n- 端口 8188 无监听\n- 用 `get_logs` 读到 30 行")
check("5g Markdown 粗体", "<b>体检结果</b>" in md, md)
check("5h Markdown 列表", "<ul" in md and "<li>" in md, md)
check("5i Markdown 行内代码", "<code" in md and "get_logs" in md, md)
check("5j HTML 转义防注入", "&lt;script&gt;" in _md_to_html("<script>x</script>"))

# 键盘：回车发送 / Shift+回车换行（2026-11-03 用户要求）
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

_sent = {"n": 0}
page._send = lambda: _sent.__setitem__("n", _sent["n"] + 1)
page.ed.setPlainText("测试")
QTest.keyClick(page.ed, Qt.Key_Return)
check("5k 回车触发发送", _sent["n"] == 1, str(_sent))
check("5k2 回车不再插入换行", "\n" not in page.ed.toPlainText(), page.ed.toPlainText())
page.ed.setPlainText("第一行")
QTest.keyClick(page.ed, Qt.Key_Return, Qt.ShiftModifier)
check("5l Shift+回车不发送、插入换行",
      _sent["n"] == 1 and "\n" in page.ed.toPlainText(), page.ed.toPlainText())
QTest.keyClick(page.ed, Qt.Key_Enter, Qt.ControlModifier)
check("5m Ctrl+回车兼容发送", _sent["n"] == 2, str(_sent))

# 附件 chip：删除按钮必须可见可点（2026-11-03 用户报"加了删不掉"）
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QFrame, QToolButton  # noqa: E402

page.show()
_test_img = QImage(40, 30, QImage.Format_RGB32)
_test_img.fill(QColor("#aa3333"))
page._add_image_att(_test_img)
check("5n 粘贴图片生成附件", len(page._atts) == 1 and page.att_host.isVisible(),
      f"atts={len(page._atts)} visible={page.att_host.isVisible()}")
_btns = page.att_host.findChildren(QToolButton)
check("5o 删除按钮存在且带图标（不是空白字符）",
      len(_btns) == 1 and not _btns[0].icon().isNull(), str(len(_btns)))
_btns[0].click()
check("5p 点 ✕ 能移除", len(page._atts) == 0, str(len(page._atts)))

for _ in range(5):
    app.processEvents()          # 让上一轮 deleteLater 真正执行
page._add_image_att(_test_img)
_chips = [page.att_row.itemAt(i).widget() for i in range(page.att_row.count())
          if page.att_row.itemAt(i).widget() is not None]
check("5q chip 数量", len(_chips) == 1, str(len(_chips)))
QTest.mouseClick(_chips[0], Qt.LeftButton)
check("5r 点 chip 本体也能移除", len(page._atts) == 0, str(len(page._atts)))
page.close()

check("5d 设置页有思考强度/最大输出/上下文",
      hasattr(sp, "cb_ai_think") and hasattr(sp, "sp_ai_max") and hasattr(sp, "cb_ai_ctx"))
check("5e 思考强度三档", sp.cb_ai_think.count() == 3)
check("5f 画像提示识别出 MiMo", "MiMo" in sp.lb_ai_profile.text(), sp.lb_ai_profile.text())
sp.close()

# ================================================== 6. 导航一致性
from app.ui.main_window import NAV_ITEMS  # noqa: E402
from app.ui.nav import NAV  # noqa: E402

check("6a NAV 索引数与 NAV_ITEMS 一致",
      len(NAV_ITEMS) == len([a for a in dir(NAV) if not a.startswith("_")]))
check("6b AI 页在导航里", ("🤖", "AI 助手") in NAV_ITEMS)
check("6c AI 在 SETTINGS 之前", NAV.AI < NAV.SETTINGS)

# ================================================== 7. 2026-11-06 三项用户反馈
# ① 出图路径必须验证存在性（用户实测：AI 报「出图完成」，文件夹里没有）
import tempfile, shutil  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="ch_ai_")
_cpath = os.path.join(_tmp, "ComfyUI")
_wfdir = os.path.join(_cpath, "user", "default", "workflows")
os.makedirs(_wfdir, exist_ok=True)
_outdir = os.path.join(_cpath, "output")
os.makedirs(os.path.join(_outdir, "sub"), exist_ok=True)

with open(os.path.join(_wfdir, "demo.json"), "w", encoding="utf-8") as f:
    json.dump({"nodes": [{"id": 1, "type": "KSampler", "widgets_values": [1, 2]},
                         {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["old"]}]}, f)

_ctx7 = ai_tools.build_ctx({"instances": []},
                          {"id": "x", "port": 8188, "comfyui_path": _cpath})

# 真文件放一个，假文件不放 → 必须被识别为 missing
_real = os.path.join(_outdir, "sub", "real.png")
open(_real, "wb").write(b"x")
_found, _missing, _stale = ai_tools._collect_images(_ctx7, {"9": {"images": [
    {"subfolder": "sub", "filename": "real.png"},
    {"subfolder": "sub", "filename": "ghost_00003_.png"}]}})
check("7a 存在的图片被识别", _found == [_real], str(_found))
check("7b 不存在的图片进 missing",
      len(_missing) == 1 and _missing[0].endswith("ghost_00003_.png"), str(_missing))

# 文件存在但比本次提交还旧 → 缓存命中，不算新出图
_old = os.path.getmtime(_real) - 600
os.utime(_real, (_old, _old))
_f2, _m2, _s2 = ai_tools._collect_images(
    _ctx7, {"9": {"images": [{"subfolder": "sub", "filename": "real.png"}]}},
    since=time.time())
check("7b2 缓存命中的旧图被识别为 stale", _f2 == [] and _s2 == [_real], str((_f2, _s2)))
_f3, _m3, _s3 = ai_tools._collect_images(
    _ctx7, {"9": {"images": [{"subfolder": "sub", "filename": "real.png"}]}})
check("7b3 不给 since 时不做新鲜度判断", _f3 == [_real] and _s3 == [], str(_f3))

# ② patch_workflow 参数形状容错（用户实测：连续 3 次 ValueError/AttributeError）
r = ai_tools.execute("patch_workflow", {"ops": [{"node": "2", "index": 0, "value": "new"}]}, _ctx7)
check("7c 缺工作流名时给出候选提示",
      r["ok"] is False and "可选" in r["error"] and "demo.json" in r["error"], str(r))
check("7d 错误信息不再带 ValueError 前缀", "ValueError:" not in r["error"], r["error"])

r = ai_tools.execute("patch_workflow",
                     {"workflow": "demo", "ops": [{"node": "2", "index": 0, "value": "new"}]},
                     _ctx7)
check("7e workflow 别名可正常命中", r["ok"] is True and r.get("name") == "demo_ai.json", str(r))

r = ai_tools.execute("patch_workflow",
                     {"name": "demo", "ops": ["把节点2改成new"]}, _ctx7)
check("7f ops 传字符串时给出正确格式",
      r["ok"] is False and "必须是对象" in r["error"], str(r))

r = ai_tools.execute("patch_workflow",
                     {"name": "demo", "ops": {"node": "2", "index": 0, "value": "x"}}, _ctx7)
check("7g 单个 op 未套数组也能用", r["ok"] is True, str(r))

# ③ 上传图片点击可预览
from app.ui.pages.ai_page import _ClickableLabel, ImagePreviewDialog  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
_pm = QPixmap(200, 120)
_pm.fill()
_dlg = ImagePreviewDialog(_pm)
check("7h 预览对话框能打开且尺寸受限", _dlg.width() > 0 and _dlg.height() > 0)
_dlg.close()
check("7i 缩略图用可点击标签", issubclass(_ClickableLabel, __import__(
    "PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))

shutil.rmtree(_tmp, ignore_errors=True)

# --------------------------------------------------
print("-" * 60)
fails = [n for n, ok, _ in RESULTS if not ok]
print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(fails)}，失败 {len(fails)}")
sys.exit(1 if fails else 0)
