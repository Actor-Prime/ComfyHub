# -*- coding: utf-8 -*-
"""ComfyHub 命令行模式（无 GUI）

用法：
    ComfyHub.exe                        启动图形界面
    ComfyHub.exe list                   列出所有实例
    ComfyHub.exe start [--id N|--name 名字] [--wait]   启动实例（--wait 等就绪）
    ComfyHub.exe stop  [--id N|--name 名字] [--force]  停止实例
    ComfyHub.exe status [--json]        查看状态
    ComfyHub.exe info   [--json]        查看当前生效配置
    ComfyHub.exe logs   [-n 100] [--id N]              查看实例日志尾部

设计原则：
  · 只依赖标准库 + 项目内非 Qt 模块（拼命令用 app.process.build_command）
  · 退出码稳定：0=成功 / 1=一般错误 / 2=参数错误 / 3=实例未运行 / 4=超时
  · 所有子命令支持 --json 输出单行 JSON，便于脚本/自动化消费
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

EXIT_OK, EXIT_ERR, EXIT_ARG, EXIT_NOTRUN, EXIT_TIMEOUT = 0, 1, 2, 3, 4


# ---------------------------------------------------------------- 基础设施
def _cfg():
    from app.config import load_config
    return load_config()


def _instances():
    return _cfg().get("instances") or []


def _pick(args):
    """按 --id / --name 选实例；都不给则取 last_instance_id 或第一个"""
    insts = _instances()
    if not insts:
        return None
    if getattr(args, "id", None) is not None:
        i = args.id
        return insts[i] if 0 <= i < len(insts) else None
    if getattr(args, "name", None):
        for it in insts:
            if it.get("name") == args.name:
                return it
        return None
    lid = _cfg().get("last_instance_id") or ""
    for it in insts:
        if it.get("id") == lid:
            return it
    return insts[0]


def _log_dir():
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyHub", "logs")
    os.makedirs(d, exist_ok=True)
    return d


def _inst_log_path(inst) -> str:
    return os.path.join(_log_dir(), f"instance_{inst.get('id', 'default')}.log")


def _port_alive(port: int, timeout=1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _system_ready(port: int, timeout=2.0) -> bool:
    """ComfyUI 真正可用的判据：/system_stats 有响应"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _pids_by_port(port: int):
    """找监听该端口的 PID（netstat + tasklist，无需 psutil）"""
    pids = []
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "TCP" and parts[1].endswith(f":{port}"):
                if parts[3] == "LISTENING":
                    pids.append(parts[4])
    except Exception:
        pass
    return sorted(set(pids))


# ---------------------------------------------------------------- 子命令
def cmd_list(args):
    insts = _instances()
    if args.json:
        out = [{"index": i, "id": x.get("id"), "name": x.get("name"),
                "path": x.get("comfyui_path"), "port": x.get("port"),
                "running": _port_alive(int(x.get("port") or 8188))}
               for i, x in enumerate(insts)]
        print(json.dumps(out, ensure_ascii=False))
        return EXIT_OK
    if not insts:
        print("（没有已配置的实例）")
        return EXIT_OK
    print(f"{'#':<3}{'名称':<18}{'端口':<7}{'状态':<8}路径")
    for i, x in enumerate(insts):
        run = "运行中" if _port_alive(int(x.get("port") or 8188)) else "-"
        print(f"{i:<3}{x.get('name', '?'):<18}{x.get('port', ''):<7}{run:<8}{x.get('comfyui_path', '')}")
    return EXIT_OK


def cmd_status(args):
    inst = _pick(args)
    if inst is None:
        return _fail(args, "未找到实例", EXIT_ARG)
    port = int(inst.get("port") or 8188)
    alive = _port_alive(port)
    ready = _system_ready(port) if alive else False
    st = {"name": inst.get("name"), "id": inst.get("id"), "port": port,
          "running": alive, "ready": ready, "pids": _pids_by_port(port) if alive else []}
    if args.json:
        print(json.dumps(st, ensure_ascii=False))
    else:
        print(f"实例: {st['name']}  端口: {port}")
        print(f"状态: {'运行中（API 就绪）' if ready else ('端口占用但 API 未就绪' if alive else '未运行')}")
        if st["pids"]:
            print(f"PID: {', '.join(st['pids'])}")
    return EXIT_OK if alive else EXIT_NOTRUN


def cmd_info(args):
    inst = _pick(args)
    if inst is None:
        return _fail(args, "未找到实例", EXIT_ARG)
    from app.process import build_command
    try:
        cmd = build_command(inst)
    except Exception as e:
        cmd = [f"<构造命令失败: {e}>"]
    data = {"instance": {k: inst.get(k) for k in
                         ("id", "name", "comfyui_path", "python_path", "port",
                          "vram_mode", "extra_args", "last_profile")},
            "command": cmd}
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(f"实例: {inst.get('name')} ({inst.get('id')})")
        print(f"目录: {inst.get('comfyui_path')}")
        print(f"解释器: {inst.get('python_path')}")
        print(f"插件方案: {inst.get('last_profile') or '(无)'}")
        print("启动命令:")
        print("  " + " ".join(cmd))
    return EXIT_OK


def cmd_start(args):
    from app.process import build_command
    inst = _pick(args)
    if inst is None:
        return _fail(args, "未找到实例", EXIT_ARG)
    port = int(inst.get("port") or 8188)
    if _port_alive(port):
        print(f"端口 {port} 已被占用（可能已在运行）", file=sys.stderr)
        return EXIT_NOTRUN
    cmd = build_command(inst)
    logpath = _inst_log_path(inst)
    logf = open(logpath, "a", encoding="utf-8", buffering=1)
    logf.write(f"\n===== CLI start @ {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    logf.flush()
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = os.environ.copy()
    proxy = (inst.get("proxy") or "").strip()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        print(f"使用代理: {proxy}")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        p = subprocess.Popen(cmd, cwd=inst.get("comfyui_path") or None,
                             stdout=logf, stderr=subprocess.STDOUT,
                             env=env,
                             creationflags=flags, close_fds=True)
    except Exception as e:
        print(f"启动失败: {e}", file=sys.stderr)
        return EXIT_ERR
    print(f"已启动 {inst.get('name')}（PID {p.pid}），日志: {logpath}")
    if not args.wait:
        return EXIT_OK
    print("等待 API 就绪…")
    t0 = time.time()
    limit = args.timeout
    while time.time() - t0 < limit:
        if _system_ready(port):
            print(f"✅ 就绪（{time.time() - t0:.0f}s）：http://127.0.0.1:{port}/")
            return EXIT_OK
        time.sleep(1.5)
    print(f"⏱ 超时（{limit}s）未就绪，请查看日志: {logpath}", file=sys.stderr)
    return EXIT_TIMEOUT


def cmd_stop(args):
    inst = _pick(args)
    if inst is None:
        return _fail(args, "未找到实例", EXIT_ARG)
    port = int(inst.get("port") or 8188)
    pids = _pids_by_port(port)
    if not pids:
        if args.json:
            print(json.dumps({"stopped": False, "reason": "not running"}, ensure_ascii=False))
        else:
            print("实例未在运行")
        return EXIT_NOTRUN
    for pid in pids:
        try:
            # 不捕获输出：taskkill 的中文输出在某些代码页下会触发解码线程异常
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            print(f"结束 PID {pid} 失败: {e}", file=sys.stderr)
    if args.json:
        print(json.dumps({"stopped": True, "pids": pids}, ensure_ascii=False))
    else:
        print(f"已停止（PID {', '.join(pids)}）")
    return EXIT_OK


def cmd_logs(args):
    inst = _pick(args)
    if inst is None:
        return _fail(args, "未找到实例", EXIT_ARG)
    path = _inst_log_path(inst)
    if not os.path.isfile(path):
        print(f"（尚无日志文件：{path}）", file=sys.stderr)
        return EXIT_ERR
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    for line in lines[-max(1, args.n):]:
        sys.stdout.write(line)
    return EXIT_OK


def cmd_batch(args):
    """批量跑同一工作流，每 --restart-every 次重启实例释放显存"""
    from app import batch_runner as br
    inst = _pick(args)
    if inst is None:
        return _fail(args, "未找到实例", EXIT_ARG)
    port = int(inst.get("port") or 8188)
    try:
        prompt = br.load_api_prompt(args.workflow)
    except Exception as e:
        return _fail(args, f"工作流读取失败: {e}", EXIT_ARG)
    print(f"工作流节点数: {len(prompt)}｜计划执行 {args.times} 次"
          + (f"，每 {args.restart_every} 次重启实例" if args.restart_every else ""))
    if not _system_ready(port):
        if not args.autostart:
            return _fail(args, f"端口 {port} 上的 ComfyUI 未就绪（可加 --autostart 自动启动）", EXIT_NOTRUN)
        print("实例未运行，正在启动…")
        rc = cmd_start(argparse.Namespace(id=getattr(args, "id", None),
                                          name=getattr(args, "name", None),
                                          json=False, wait=True, timeout=300))
        if rc != EXIT_OK:
            return _fail(args, "自动启动失败", rc)

    def _restart():
        cmd_stop(argparse.Namespace(id=args.id, name=args.name, json=False, force=True))
        time.sleep(3)
        cmd_start(argparse.Namespace(id=args.id, name=args.name, json=False,
                                     wait=True, timeout=300))

    out = br.run_batch(port, prompt, args.times,
                       restart_every=args.restart_every,
                       on_restart=_restart, log=print)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"\n完成：成功 {out['done']}／失败 {out['failed']}（共 {args.times} 次）")
        for e in out["errors"][:5]:
            print(f"  · {e}")
    return EXIT_OK if out["ok"] else EXIT_ERR


def _fail(args, msg, code):
    if getattr(args, "json", False):
        print(json.dumps({"error": msg}, ensure_ascii=False))
    else:
        print(msg, file=sys.stderr)
    return code


# ---------------------------------------------------------------- 入口
CMDS = {"list", "start", "stop", "status", "info", "logs", "batch"}


def build_parser():
    ap = argparse.ArgumentParser(prog="ComfyHub", description="ComfyHub 命令行（无 GUI）")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--id", type=int, help="实例序号（见 list）")
        p.add_argument("--name", help="实例名称")
        p.add_argument("--json", action="store_true", help="输出单行 JSON")

    p = sub.add_parser("list", help="列出实例")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("start", help="启动实例")
    common(p)
    p.add_argument("--wait", action="store_true", help="等待 API 就绪")
    p.add_argument("--timeout", type=int, default=180, help="等待超时秒数（默认 180）")

    p = sub.add_parser("stop", help="停止实例")
    common(p)
    p.add_argument("--force", action="store_true", help="强制结束（默认已是强杀）")

    p = sub.add_parser("status", help="查看状态")
    common(p)

    p = sub.add_parser("info", help="查看生效配置与启动命令")
    common(p)

    p = sub.add_parser("batch", help="批量执行同一工作流（防 OOM 可定时重启）")
    common(p)
    p.add_argument("--workflow", required=True, help="API 格式工作流 JSON（ComfyUI: Export (API)）")
    p.add_argument("--times", type=int, default=1, help="执行次数")
    p.add_argument("--restart-every", type=int, default=0, dest="restart_every",
                   help="每执行 N 次重启实例释放显存（0=不重启）")
    p.add_argument("--autostart", action="store_true", help="实例未运行则自动启动")

    p = sub.add_parser("logs", help="查看实例日志")
    common(p)
    p.add_argument("-n", type=int, default=100, help="输出行数（默认 100）")
    return ap


def main(argv=None) -> int:
    # 统一 UTF-8 输出：windowed 打包的 exe 默认用系统代码页(GBK)，管道/终端里会乱码
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return EXIT_ARG
    fn = {"list": cmd_list, "start": cmd_start, "stop": cmd_stop,
          "status": cmd_status, "info": cmd_info, "logs": cmd_logs,
          "batch": cmd_batch}[args.cmd]
    try:
        return fn(args)
    except KeyboardInterrupt:
        return EXIT_ERR
    except Exception as e:
        return _fail(args, f"执行失败: {e}", EXIT_ERR)


if __name__ == "__main__":
    sys.exit(main())
