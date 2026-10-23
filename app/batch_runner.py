# -*- coding: utf-8 -*-
"""批量任务执行器：把同一工作流跑 N 次，每 K 次重启 ComfyUI 释放显存碎片

背景：文生视频/大图连跑容易 OOM（显存碎片累积），业内做法是"跑几轮重启一次"。
本模块提供可脚本化的批量执行：提交 /prompt → 轮询 /history → 计数 → 到点重启。

工作流要求：ComfyUI 里 **Export (API)** 导出的 JSON（节点直连格式），
不是前端保存的界面工作流（含 nodes/links）。
"""
import json
import os
import time
import urllib.error
import urllib.request

CLIENT_ID = "comfyhub-batch"


# ---------------------------------------------------------------- HTTP
def _post_json(url, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _get_json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def load_api_prompt(path: str) -> dict:
    """读取 API 格式工作流；兼容被包在 {"prompt": {...}} 里的情况"""
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("工作流 JSON 结构异常")
    if "nodes" in d and "links" in d:
        raise ValueError(
            "这是 ComfyUI 界面工作流（含 nodes/links），无法直接提交。\n"
            "请在 ComfyUI 里用「Workflow → Export (API)」导出后再试。")
    if "prompt" in d and isinstance(d["prompt"], dict):
        d = d["prompt"]
    # 基本校验：每个节点应有 class_type
    bad = [k for k, v in list(d.items())[:50]
           if not (isinstance(v, dict) and v.get("class_type"))]
    if bad and len(bad) == len(list(d.items())[:50]):
        raise ValueError("看起来不是 API 格式工作流（节点缺少 class_type）")
    return d


# ---------------------------------------------------------------- 执行
def wait_ready(port: int, timeout=180, log=print) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            _get_json(f"http://127.0.0.1:{port}/system_stats", timeout=3)
            return True
        except Exception:
            time.sleep(1.5)
    return False


def submit(port: int, prompt: dict) -> str:
    """提交一次任务，返回 prompt_id"""
    r = _post_json(f"http://127.0.0.1:{port}/prompt",
                   {"prompt": prompt, "client_id": CLIENT_ID})
    pid = r.get("prompt_id")
    if not pid:
        raise RuntimeError(f"提交失败：{str(r)[:200]}")
    return pid


def wait_done(port: int, prompt_id: str, timeout=1800, poll=2.0, log=print) -> dict:
    """等某个 prompt 执行完，返回 history 条目（含 status/outputs）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = _get_json(f"http://127.0.0.1:{port}/history/{prompt_id}")
        except Exception:
            h = None
        if isinstance(h, dict) and prompt_id in h:
            return h[prompt_id]
        # 队列里已消失但历史里也没有 → 可能被中断
        try:
            q = _get_json(f"http://127.0.0.1:{port}/queue")
            ids = [x[1] for x in (q.get("queue_running") or []) + (q.get("queue_pending") or [])
                   if isinstance(x, list) and len(x) > 1]
            if prompt_id not in ids and time.time() - t0 > 3:
                h2 = _get_json(f"http://127.0.0.1:{port}/history/{prompt_id}")
                if isinstance(h2, dict) and prompt_id in h2:
                    return h2[prompt_id]
        except Exception:
            pass
        time.sleep(poll)
    raise TimeoutError(f"任务 {prompt_id} 超时（{timeout}s）")


def summarize(entry: dict) -> tuple:
    """(成功?, 摘要)"""
    status = entry.get("status") or {}
    st = status.get("status_str") or "success"
    imgs = 0
    for out in (entry.get("outputs") or {}).values():
        if isinstance(out, dict):
            imgs += len(out.get("images") or [])
    if st == "error":
        msg = "执行出错"
        for m in (status.get("messages") or []):
            if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
                msg = ((m[1] or {}).get("exception_message") or msg)[:120]
                break
        return False, msg
    return True, f"生成 {imgs} 张图" if imgs else "完成"


def run_batch(port: int, prompt: dict, times: int,
              restart_every: int = 0, on_restart=None,
              log=print) -> dict:
    """批量执行主循环。

    restart_every > 0 时，每完成这么多次就调用 on_restart()（由调用方负责重启实例）。
    返回 {ok, done, failed, errors}
    """
    done = failed = 0
    errors = []
    for i in range(1, times + 1):
        try:
            log(f"[批量] 第 {i}/{times} 次…")
            pid = submit(port, prompt)
            entry = wait_done(port, pid, log=log)
            ok, summary = summarize(entry)
            if ok:
                done += 1
                log(f"[批量]   ✅ {summary}")
            else:
                failed += 1
                errors.append(summary)
                log(f"[批量]   ❌ {summary}")
        except Exception as e:
            failed += 1
            errors.append(str(e))
            log(f"[批量]   ❌ {e}")
        # 到点重启
        if restart_every and i % restart_every == 0 and i < times:
            log(f"[批量] 已完成 {i} 次，重启 ComfyUI 释放显存…")
            try:
                if on_restart:
                    on_restart()
                else:
                    log("[批量]   （未提供重启回调，跳过）")
            except Exception as e:
                log(f"[批量]   重启失败: {e}")
    return {"ok": failed == 0, "done": done, "failed": failed, "errors": errors}
