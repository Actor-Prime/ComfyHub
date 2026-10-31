# -*- coding: utf-8 -*-
"""启动模式体检：headless 起内核 → 查 /object_info → 核对节点类。

语义（2026-10-31 启动模式体系）：
  · probe_classes(inst, ...)：用合成 inst 短暂拉起内核，取已注册节点类全集，
    返回 set；失败返回 None（附错误）。
  · check_workflow(inst, workflow_path, ...)：工作流所需节点类是否全部可加载，
    缺失的列清单（供「装插件/换内核」修复决策）。
  · 只读体检不装不改；端口用非常用段（8317+）避免撞运行中的实例。
"""
import json
import os
import socket
import subprocess
import time
import urllib.request

PROBE_PORT = 8317


def _port_free(p: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", p)) != 0


def _pick_port(start: int = PROBE_PORT) -> int:
    for p in range(start, start + 20):
        if _port_free(p):
            return p
    raise RuntimeError(f"{start}~{start+19} 端口全被占用，无法体检")


def probe_classes(inst: dict, timeout: float = 120, log=print) -> set | None:
    """headless 拉起内核，返回 /object_info 的节点类全集（set），失败 None。
    inst 由 profiles.prepare_launch 合成；体检后进程必杀。"""
    from app.process import build_command
    port = _pick_port()
    probe = dict(inst)
    probe["port"] = port
    probe["auto_open_browser"] = False          # --disable-auto-launch
    cmd = build_command(probe, port=port)
    env = dict(os.environ)
    for k, v in (inst.get("env") or {}).items():
        env[k] = v
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    log(f"[体检] headless 启动 {os.path.basename(inst['comfyui_path'])} "
        f"探针端口 {port}…")
    proc = subprocess.Popen(cmd, cwd=inst["comfyui_path"], env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                log("[体检] ❌ 内核启动即退出（进程已死）")
                return None
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/object_info",
                        timeout=5) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
                classes = set(data.keys())
                log(f"[体检] ✅ 内核就绪，已注册节点类 {len(classes)} 个")
                return classes
            except Exception:
                time.sleep(2)
        log(f"[体检] ❌ {timeout}s 内未就绪（超时）")
        return None
    finally:
        _kill(proc)


def _kill(proc) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def check_workflow(inst: dict, workflow_path: str,
                   timeout: float = 120, log=print) -> dict:
    """核对工作流所需节点类在该启动模式下是否全部可加载。
    返回 {ok, missing:[类名], total, loaded}。"""
    from app.workflow_scan import extract_class_types, load_workflow
    wf = load_workflow(workflow_path)
    ctypes = extract_class_types(wf)
    classes = probe_classes(inst, timeout=timeout, log=log)
    if classes is None:
        return {"ok": False, "missing": [], "total": len(ctypes),
                "loaded": 0, "error": "内核未能启动"}
    missing = sorted(t for t in ctypes if t not in classes)
    return {"ok": not missing, "missing": missing,
            "total": len(ctypes), "loaded": len(ctypes) - len(missing)}
