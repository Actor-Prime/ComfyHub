# -*- coding: utf-8 -*-
"""mcp_server.py - ComfyHub 外挂 MCP 模式（2026-11-03，方案 B）

`ComfyHub.exe mcp`（或 `python main.py mcp`）进入 stdio MCP server：
把 AI 助手工具层（app/ai_tools.py，与 GUI 页共用）以 MCP 协议暴露给
外部 AI 客户端（WorkBuddy / Claude Desktop 等）。不依赖 node、不引第三方包，
手写 MCP stdio 传输（JSON-RPC 2.0，每行一条消息）。

协议实现要点：
  - initialize → 回 protocolVersion/capabilities/serverInfo
  - tools/list → ai_tools.schemas() 的 inputSchema 形态
  - tools/call → 执行并包成 {content:[{type:"text"}]}；工具异常标 isError
  - 通知类消息（无 id）一律不应答
日志只写 stderr，stdout 严禁混入非协议内容。
"""
import json
import sys

from app.config import APP_VERSION
from app import ai_tools

PROTOCOL_VERSION = "2024-11-05"


def _tool_defs() -> list:
    return [{"name": t["name"], "description": t["desc"],
             "inputSchema": t["schema"]} for t in ai_tools.TOOLS]


def _handle(req: dict, ctx) -> dict | None:
    method = req.get("method") or ""
    rid = req.get("id")
    is_notify = "id" not in req

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ComfyHub", "version": APP_VERSION},
            "instructions": "ComfyHub 内置 ComfyUI 管理工具：体检/日志/队列/模型/工作流执行。"
                            "当前实例端口见 get_health。",
        }}
    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _tool_defs()}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        result = ai_tools.execute(name, args, ctx)
        ok = result.get("ok", True)
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False)}],
            "isError": not ok,
        }}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error":
            {"code": -32601, "message": f"未知方法：{method}"}}


def main() -> int:
    cfg = None
    try:
        from app.config import load_config
        cfg = load_config()
    except Exception as e:
        print(f"配置读取失败: {e}", file=sys.stderr)
        return 1
    inst = ai_tools.current_instance(cfg)
    if not inst or not inst.get("comfyui_path"):
        print("没有可用实例：先在 ComfyHub 里添加实例", file=sys.stderr)
        return 1
    ctx = ai_tools.build_ctx(cfg, inst, mainwin=None,
                             log=lambda s: print(f"[comfyhub-mcp] {s}", file=sys.stderr))
    print(f"[comfyhub-mcp] 实例 {inst.get('name')} 端口 {inst.get('port')}，"
          f"{len(ai_tools.TOOLS)} 个工具就绪（stdin/stdout JSON-RPC）", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = _handle(req, ctx)
        except Exception as e:
            if "id" in req:
                resp = {"jsonrpc": "2.0", "id": req["id"], "error":
                        {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
            else:
                resp = None
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0
