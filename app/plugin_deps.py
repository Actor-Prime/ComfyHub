# -*- coding: utf-8 -*-
"""插件依赖自检（2026-11-14 用户报障引出）

背景：共享 Python 只装了 ComfyUI 本体的 requirements.txt，`custom_nodes/*`
里每个插件自带的 requirements.txt 从来没人管。结果是插件加载时
`ModuleNotFoundError: No module named 'cv2'`，节点不注册，工作流一提交就
HTTP 400（`Exception when validating node: '78:70'`），用户完全看不出原因。

这里只做纯逻辑（不碰 UI、不起线程）：
  · 扫 custom_nodes/*/requirements.txt
  · 与目标解释器已装的包比对
  · 过滤掉「底座包」（torch/numpy 等）——插件常钉死旧版本，直接装会把
    CUDA 版 torch 换成 CPU 版，等于毁掉整个环境
UI 在 env_page 的「依赖管理」tab 里调用。
"""
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

# ---------------------------------------------------------------- 底座包保护
# 这些包由 ComfyHub 统一管（按显卡装 cu130/cu126 的 torch），
# 插件 requirements 里出现一律跳过 —— 装了就可能是 CPU 版覆盖 CUDA 版，
# 表现为「实例能启动、一出图就崩」，比缺包更难查。
BASE_SKIP = {
    "torch", "torchvision", "torchaudio", "torchtext", "torchsde",
    "numpy", "xformers", "triton", "pytorch-triton", "flash-attn", "flash_attn",
}

# 体积/耗时明显偏大的包：补装前给用户一个心理准备（MB 是粗略量级）
HEAVY = {
    "onnxruntime-gpu": 300, "onnxruntime": 60, "mediapipe": 60,
    "modelscope": 120, "openai-whisper": 800, "easyocr": 40,
    "pyarrow": 40, "diffusers": 40, "openimageio": 30, "decord": 25,
    "transparent-background": 120, "scikit-image": 30, "matplotlib": 30,
    "opencv-python": 60, "opencv-python-headless": 60, "peft": 30,
    "albumentations": 25, "groundingdino-py": 60, "segment-anything": 10,
}

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

# ----------------------------------------------------- 补装过程的三个时间尺度
# 2026-11-22 用户报障「补装卡住了」：原实现 capture_output=True，pip 的输出
# 被整段缓冲，最后一次性吐 8 行 —— 「解析回溯 20 分钟」和「2 秒就失败」
# 在界面上长得一模一样，用户只能干等。改成流式之后这三档才有意义：
IDLE_WARN = 300        # 连 5 分钟没有任何输出 → 日志提示 + 按钮换「取消并终止」
IDLE_KILL = 600        # 再没输出到 10 分钟 → 直接中止这一个插件（快速失败）
PLUGIN_TIMEOUT = 1800  # 单个插件的总上限（原来 7200 秒，等 2 小时毫无意义）


def norm(name: str) -> str:
    """包名归一化：pip 认为 a_b 与 a-b 是同一个包"""
    return str(name or "").strip().lower().replace("_", "-")


def parse_requirements(path: str) -> list:
    """读 requirements.txt → [{raw, name, kind}]

    kind:
      · install  普通包（可自动补装）
      · special  git+/http/-e/本地路径（要 git，自动补装跳过，列出来给用户）
      · comment  注释/空行（丢弃）
    """
    items = []
    try:
        lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
    except OSError:
        return items
    for line in lines:
        raw = line.split("#")[0].strip()
        if not raw:
            continue
        if raw.startswith("-"):
            # -r / -e / --index-url 之类：不参与自动补装
            items.append({"raw": raw, "name": "", "kind": "special"})
            continue
        if raw.startswith(("git+", "http://", "https://", "hg+", "svn+", "bzr+")):
            items.append({"raw": raw, "name": "", "kind": "special"})
            continue
        m = _REQ_NAME.match(raw)
        if not m:
            items.append({"raw": raw, "name": "", "kind": "special"})
            continue
        items.append({"raw": raw, "name": norm(m.group(1)), "kind": "install"})
    return items


def plugin_dirs(comfyui_path: str) -> list:
    """custom_nodes 下的插件目录（跳过 Manager 的 .disabled 与隐藏目录）"""
    root = os.path.join(comfyui_path or "", "custom_nodes")
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == "__pycache__":
            continue
        d = os.path.join(root, name)
        if os.path.isdir(d):
            out.append((name, d))
    return out


def installed_packages(python_exe: str, timeout: int = 120) -> dict:
    """目标解释器已装的包 {归一化包名: 版本}（走 importlib.metadata，比 pip list 快）"""
    code = ("import json,importlib.metadata as m;"
            "print(json.dumps({d.metadata['Name']: d.version "
            "for d in m.distributions() if d.metadata['Name']}))")
    r = subprocess.run([python_exe, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "读取已装包失败").strip()[:300])
    data = json.loads(r.stdout or "{}")
    return {norm(k): v for k, v in data.items()}


def incomplete_reason(path: str) -> str:
    """插件目录是不是「装坏了的空壳」→ 返回原因（正常返回空串）

    2026-11-22 现场：`custom_nodes\\minimax-h3` 目录在、requirements.txt 里
    12 个依赖也全齐，所以自检显示「齐 (12)」；实际它没有 `__init__.py`，
    ComfyUI 启动就打 `[Errno 2] ... __init__.py` + IMPORT FAILED。
    用户看到的是「这个插件没问题」，日志里却一直加载失败 —— 必须单独标出来。

    全部静态判定（不跑解释器，秒级完成）。三种形态：
      · 目录是空的；
      · 没有 `__init__.py`，且顶层连一个 .py 都没有（老式「多个 .py 平铺」的
        插件不报，宁可漏报不误报）；
      · 多套了一层同名目录（`X\\X\\__init__.py` 存在而 `X\\__init__.py` 不存在）。
    """
    try:
        entries = os.listdir(path)
    except OSError as e:
        return "目录读不了：%s" % e
    if not entries:
        return "目录是空的（一个文件都没有）"
    base = os.path.basename(os.path.abspath(path).rstrip("\\/"))
    if not os.path.isfile(os.path.join(path, "__init__.py")):
        inner = os.path.join(path, base, "__init__.py")
        if os.path.isfile(inner):
            return "多套了一层同名目录（%s\\%s\\__init__.py）" % (base, base)
        if not any(e.lower().endswith(".py") for e in entries):
            return "缺少 __init__.py（目录里也没有任何 .py 文件）"
    return ""


def scan(comfyui_path: str, installed: dict) -> list:
    """逐插件比对 → [{name, path, total, missing, special, heavy, base, incomplete}]

    missing 里只放「能自动补装且确实没装」的包；底座包单独放 base，只提示不装。
    incomplete 非空 = 目录本身坏了（缺 __init__.py 之类），补依赖也修不好。
    """
    rows = scan_dirs(plugin_dirs(comfyui_path), installed)
    # 没有 requirements.txt、但目录是空壳的插件也要露头：它照样会 IMPORT FAILED，
    # 只是原来看不见（旧逻辑要求目录里有 requirements.txt 才建行）。
    known = {r["path"] for r in rows}
    for name, d in plugin_dirs(comfyui_path):
        if d in known:
            continue
        why = incomplete_reason(d)
        if not why:
            continue
        rows.append({
            "name": name, "path": d, "req_file": "",
            "total": 0, "missing": [], "special": [], "base": [], "heavy": [],
            "incomplete": why,
        })
    rows.sort(key=lambda r: (not r.get("incomplete"), -len(r["missing"]),
                             r["name"].lower()))
    return rows


def scan_dirs(pairs: list, installed: dict) -> list:
    """同 scan，但插件目录由调用方给 [(name, dir)]

    为什么拆出来：向导装完 ComfyUI-Manager、插件页 git clone 完一个插件时，
    只需要查「刚弄进来的那几个」，不该为这一个去扫整个 custom_nodes。
    """
    out = []
    for name, d in pairs:
        req = os.path.join(d, "requirements.txt")
        if not os.path.isfile(req):
            continue
        items = parse_requirements(req)
        missing, special, base = [], [], []
        for it in items:
            if it["kind"] == "special":
                special.append(it["raw"])
                continue
            if it["name"] in BASE_SKIP:
                if it["name"] not in installed:      # 底座缺了也不该由插件来补
                    base.append(it["name"])
                continue
            if it["name"] in installed:
                continue
            missing.append({"name": it["name"], "raw": it["raw"]})
        out.append({
            "name": name,
            "path": d,
            "req_file": req,
            "total": len([i for i in items if i["kind"] == "install"]),
            "missing": missing,
            "special": special,
            "base": base,
            "heavy": [m["name"] for m in missing if m["name"] in HEAVY],
            "incomplete": incomplete_reason(d),
        })
    # 问题最大的排前面（目录坏了最优先，其次缺得多的）
    out.sort(key=lambda r: (not r.get("incomplete"), -len(r["missing"]),
                            r["name"].lower()))
    return out


def summarize(rows: list) -> dict:
    """给 UI 的一句话摘要"""
    bad = [r for r in rows if r["missing"]]
    broken = [r for r in rows if r.get("incomplete")]
    return {
        "plugins": len(rows),
        "bad": len(bad),
        "broken": len(broken),
        "missing": sum(len(r["missing"]) for r in bad),
        "heavy": sum(len(r["heavy"]) for r in bad),
        "names": [r["name"] for r in bad],
        "broken_names": [r["name"] for r in broken],
    }


def build_requirements(rows: list) -> tuple:
    """把选中行的缺失包拼成 requirements 文本

    返回 (text, skipped_base, skipped_special)：
      · text 里每行用原始声明（保留版本约束）
      · 同包被多个插件声明 → 只留第一次（版本冲突时先到先得，并在文本里注释）
    """
    seen, lines, skipped_base, skipped_special = set(), [], [], []
    for r in rows:
        for raw in r.get("special") or []:
            skipped_special.append(f"{r['name']}: {raw}")
        for b in r.get("base") or []:
            if b not in skipped_base:
                skipped_base.append(b)
        for m in r.get("missing") or []:
            if m["name"] in seen:
                continue
            seen.add(m["name"])
            lines.append(m["raw"])
    return "\n".join(lines) + ("\n" if lines else ""), skipped_base, skipped_special


def heavy_hint(names: list, limit: int = 4) -> str:
    """重包提示文案（估个大概体积，别让用户以为是一下子的小活）"""
    seen, parts, mb = set(), [], 0
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        mb += HEAVY.get(n, 0)
        parts.append(n)
    if not parts:
        return ""
    head = "、".join(parts[:limit]) + ("…" if len(parts) > limit else "")
    return f"含重包 {len(parts)} 个（{head}），预计下载约 {mb / 1024:.1f} GB" if mb >= 256 \
        else f"含较大包 {len(parts)} 个（{head}），预计下载约 {max(mb, 1)} MB"


# ------------------------------------------------------------- 安装参数（保护底座）
def write_constraints(installed: dict, dst: str, extra_blocks: dict = None) -> str:
    """把「当前已装版本」写成 pip 约束文件

    为什么需要：插件 requirements 里的包自己也会带版本约束，
    pip 解析时为了满足它可能把 transformers / huggingface_hub 等
    **已装的包降级**——ComfyUI 本体就跟着挂。约束文件把这些版本钉死，
    pip 只能去选「不与现有环境冲突」的版本，选不到就报错退出（可接受），
    而不是悄悄改坏环境。
    """
    blocks = dict(extra_blocks or {})
    lines = []
    for name, ver in sorted(installed.items()):
        if not ver or name in blocks:
            continue
        lines.append(f"{name}=={ver}")
    for name, ver in blocks.items():
        lines.append(f"{name}!={ver}" if ver else f"{name}")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return dst


def install_args(req_path: str, constraints_path: str, mirror: str = "") -> list:
    """pip install 参数（-c 约束、只在不必要时才升级）"""
    args = ["install", "-r", req_path, "-c", constraints_path,
            "--upgrade-strategy", "only-if-needed",
            "--disable-pip-version-check", "--no-input"]
    if mirror:
        args += ["-i", mirror, "--trusted-host", _host(mirror)]
    return args


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0]


# ------------------------------------------------------- 流式跑 pip（2026-11-22）
def _kill_tree(pid: int) -> None:
    """强杀整个进程树（pip 会拉子进程解 wheel）"""
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
        return
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def run_pip_stream(python_exe: str, args: list, on_line=None, on_state=None,
                   idle_warn: int = IDLE_WARN, idle_kill: int = IDLE_KILL,
                   total_timeout: int = PLUGIN_TIMEOUT, cancelled=None,
                   poll: float = 0.2) -> dict:
    """逐行流式跑 pip，带「没输出」判定（2026-11-22 用户报障「补装卡住」）

    为什么不用 subprocess.run：`capture_output=True` 会一直读到进程退出才返回，
    pip 卡在依赖回溯时界面上一行字都没有，用户只能猜；失败也同样是静默的。

    三个出口，全部有日志：
      · 连续 idle_warn 秒无输出 → on_state("idle")，UI 把按钮换成「取消并终止」，
        进程继续跑（可能只是在下大文件，别急着杀）；
      · 连续 idle_kill 秒无输出 → 杀进程树，idle=True；
      · 总时长超 total_timeout / cancelled() 为真 → 杀掉，分别置 timeout / cancelled。

    返回 {"code", "idle", "timeout", "cancelled", "lines"}；code=-1 表示被杀。
    """
    lines = []
    q = queue.Queue()

    try:
        proc = subprocess.Popen(
            [python_exe, "-m", "pip"] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            universal_newlines=True)
    except Exception as e:
        lines.append("启动 pip 失败：%s" % e)
        if on_line:
            on_line(lines[-1])
        return {"code": -1, "idle": False, "timeout": False,
                "cancelled": False, "lines": lines}

    def _reader(p, box):
        try:
            for ln in p.stdout:
                box.put(ln.rstrip("\r\n"))
        except Exception:
            pass
        finally:
            box.put(None)          # 结束哨兵

    th = threading.Thread(target=_reader, args=(proc, q), daemon=True)
    th.start()

    res = {"code": -1, "idle": False, "timeout": False, "cancelled": False,
           "lines": lines}
    t0 = time.time()
    last_out = t0
    warned = False
    try:
        while True:
            if cancelled is not None and cancelled():
                res["cancelled"] = True
                _kill_tree(proc.pid)
                break
            try:
                item = q.get(timeout=poll)
            except queue.Empty:
                now = time.time()
                silent = now - last_out
                if silent >= idle_kill:
                    res["idle"] = True
                    _kill_tree(proc.pid)
                    break
                if now - t0 >= total_timeout:
                    res["timeout"] = True
                    _kill_tree(proc.pid)
                    break
                if silent >= idle_warn and not warned:
                    warned = True
                    if on_state:
                        on_state("idle")
                    silent_note = ("… 已 %d 秒没有新输出（可能卡在依赖解析或大文件下载），"
                                   "可以点「取消并终止」" % int(silent))
                    lines.append(silent_note)
                    if on_line:
                        on_line(silent_note)
                continue
            if item is None:
                break
            last_out = time.time()
            lines.append(item)
            if on_line and item.strip():
                on_line(item)
            # 超总时长（有输出也要拦，不然一个超大包能跑一天）
            if time.time() - t0 > total_timeout:
                res["timeout"] = True
                _kill_tree(proc.pid)
                break
        try:
            proc.wait(timeout=15)
        except Exception:
            _kill_tree(proc.pid)
        res["code"] = proc.returncode if proc.returncode is not None else -1
    finally:
        try:
            if proc.poll() is None:
                _kill_tree(proc.pid)
        except Exception:
            pass
    return res


def pip_check(python_exe: str, timeout: int = 180) -> list:
    """`pip check`：列出现在环境里互相打架的包（中止/失败后提示用，只报不改）

    为什么不自动回滚：半装的包自动卸掉风险更大（可能带走别的插件的依赖），
    让用户看到清单，自己去依赖管理页处理更稳。
    """
    try:
        r = subprocess.run([python_exe, "-m", "pip", "check",
                            "--disable-pip-version-check"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except Exception as e:
        return ["pip check 没能跑起来：%s" % e]
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if not out:
        return []
    if "No broken requirements found" in out:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()][:12]


def install_for_dirs(python_exe: str, pairs: list, log=print, mirror: str = "",
                     timeout: int = 7200, installed: dict = None) -> dict:
    """给指定插件目录补装它们声明的依赖（带约束保护，失败不抛异常）

    和「依赖管理」页里手动补装是同一套保护，缺一条都会把环境改坏：
      · 只装「确实没装」的包 —— 已装版本一个都不动；
      · 一份 constraints 把当前全部已装版本钉死 —— pip 只能挑不冲突的版本，
        挑不到就报错退出，而不是把 torch / transformers 悄悄换掉。

    调用方：向导装完 ComfyUI-Manager、插件页 git clone 完一个插件。
    返回 {"ok", "dirs", "pkgs", "failed": [插件名], "skipped_base": [...],
          "special": [...], "error": ""}；ok=False 只是「没装全」，不阻塞调用方。
    """
    res = {"ok": True, "dirs": 0, "pkgs": 0, "failed": [],
           "skipped_base": [], "special": [], "error": ""}
    try:
        inst = installed if installed is not None else installed_packages(python_exe)
    except Exception as e:
        res.update(ok=False, error="读取已装包失败：%s" % e)
        return res
    rows = [r for r in scan_dirs(pairs, inst) if r["missing"]]
    if not rows:
        log("  插件依赖已齐，无需补装")
        return res
    tmp = tempfile.mkdtemp(prefix="ch_pdep_")
    try:
        cons = write_constraints(inst, os.path.join(tmp, "constraints.txt"))
        for i, r in enumerate(rows):
            res["dirs"] += 1
            for b in r.get("base") or []:
                if b not in res["skipped_base"]:
                    res["skipped_base"].append(b)
            for s in r.get("special") or []:
                res["special"].append("%s: %s" % (r["name"], s))
            text, _b, _s = build_requirements([r])
            if not text.strip():
                continue
            names = [m["name"] for m in r["missing"]]
            res["pkgs"] += len(names)
            log("  %s：补装 %d 个包（%s%s）"
                % (r["name"], len(names), "、".join(names[:4]),
                   "…" if len(names) > 4 else ""))
            req = os.path.join(tmp, "req_%d.txt" % i)
            with open(req, "w", encoding="utf-8") as f:
                f.write(text)
            try:
                p = subprocess.run(
                    [python_exe, "-m", "pip"] + install_args(req, cons, mirror),
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout)
            except Exception as e:
                res["failed"].append(r["name"])
                res["error"] = res["error"] or str(e)[:200]
                log("  ❌ %s 安装异常：%s" % (r["name"], e))
                continue
            if p.returncode != 0:
                res["failed"].append(r["name"])
                tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
                msg = (tail[-1] if tail else "未知错误").strip()[:200]
                res["error"] = res["error"] or msg
                log("  ⚠️ %s 有包没装上：%s" % (r["name"], msg))
            else:
                log("  ✅ %s 依赖装完" % r["name"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["ok"] = not res["failed"]
    return res


def verify(python_exe: str, names: list, timeout: int = 240) -> list:
    """装完自查：逐个 import，返回仍然导不进来的包（UI 上直接标红）

    包名 ≠ 模块名（groundingdino-py 的模块是 groundingdino），所以先用
    `importlib.metadata.packages_distributions()` 反查它装了哪些顶层模块，
    再逐个试 —— 靠「包名把 - 换 _」猜模块名会造出一堆假失败。
    """
    code = ("import importlib, json, sys\n"
            "import importlib.metadata as md\n"
            "names = json.loads(sys.argv[1])\n"
            "d2m = md.packages_distributions()\n"
            "bad = []\n"
            "for dist in names:\n"
            "    mods = d2m.get(dist) or [dist.replace('-', '_')]\n"
            "    ok = False\n"
            "    for m in mods:\n"
            "        try:\n"
            "            importlib.import_module(m); ok = True; break\n"
            "        except Exception:\n"
            "            continue\n"
            "    if not ok:\n"
            "        bad.append(dist)\n"
            "print(json.dumps(bad))\n")
    r = subprocess.run([python_exe, "-c", code, json.dumps(names)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    # 有些包 import 时会往 stderr 打一堆 FutureWarning/日志，退出码可能是 1，
    # 但 stdout 里的结果仍然有效 → 只看有没有 JSON，不硬看 returncode。
    for line in reversed((r.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            try:
                return json.loads(line)
            except Exception:
                continue
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "自检失败").strip()[:300])
    return []


# ------------------------------------------------------------------ 命令行入口
def _main(argv: list) -> int:
    """命令行自检/补装（排查现场问题用，UI 走 env_page）

    用法：
      python app/plugin_deps.py <ComfyUI目录> <python.exe>           只自检
      python app/plugin_deps.py <ComfyUI目录> <python.exe> --install 自检并补装
    """
    import sys
    import tempfile
    if len(argv) < 3:
        print(__doc__)
        return 2
    comfy, py = argv[1], argv[2]
    do_install = "--install" in argv
    inst = installed_packages(py)
    rows = scan(comfy, inst)
    s = summarize(rows)
    print(f"插件 {s['plugins']} 个，其中 {s['bad']} 个缺依赖，共缺 {s['missing']} 个包")
    for r in rows:
        if not r["missing"]:
            continue
        print(f"  · {r['name']}: 缺 {len(r['missing'])}/{r['total']}"
              f"  {', '.join(m['name'] for m in r['missing'])[:120]}")
        if r["base"]:
            print(f"    ! 底座缺包（不自动装）: {', '.join(r['base'])}")
        if r["special"]:
            print(f"    ! 需手工安装: {', '.join(r['special'])[:120]}")
    if not do_install:
        return 0
    tmp = tempfile.mkdtemp(prefix="pdep_")
    cons = write_constraints(inst, os.path.join(tmp, "constraints.txt"))
    mirror = "https://mirrors.aliyun.com/pypi/simple/"
    rc = 0
    for r in rows:
        if not r["missing"]:
            continue
        text, _, _ = build_requirements([r])
        if not text.strip():
            continue
        req = os.path.join(tmp, f"req_{r['name']}.txt")
        open(req, "w", encoding="utf-8").write(text)
        print(f"\n== 补装 {r['name']}：{len(r['missing'])} 个包")
        p = subprocess.run([py, "-m", "pip"] + install_args(req, cons, mirror))
        if p.returncode != 0:
            print(f"   ⚠️ {r['name']} 有包没装上（多为无轮子/版本冲突），其余插件继续")
            rc = 1
    names = sorted({m["name"] for r in rows for m in r["missing"]})
    bad = verify(py, names)
    print("\n装完自检：导不进来的包 =", bad or "无")
    return rc or (1 if bad else 0)


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))


