# -*- coding: utf-8 -*-
"""ai_tools.py - AI 助手工具层（2026-11-03）

「AI 助手」页（方案 A）与 `ComfyHub.exe --mcp` 外挂模式（方案 B）共用的工具层。
Python 直接调当前实例的 ComfyUI HTTP API，不经过任何中间进程。

设计原则（借鉴 comfyui-mcp 的两条思想，实现全部独立）：
  1. 单工具多动作，控制工具总数；
  2. 破坏性操作（取消任务）做验证与降级提示。

每个工具 = dict(name, desc, schema, fn)；fn(args, ctx) 返回可 JSON 序列化的 dict。
ctx 携带 cfg / 实例信息 / 可选 UI 钩子（下载中心），保证 MCP 模式（无 Qt）同样可用。
"""
import copy
import json
import os
import time
import urllib.request

from app import ai_skills
from app import api_base as apib
from app import model_store
from app import workflow_scan

# 只读工具：同一参数在短时间内重复调用可以直接复用结果（2026-11-14）
# 模型经常会「忘了刚查过」再查一遍 get_health / list_models，两次都真去打接口。
# 注意 wait_job / ask_user 虽然不写盘，但语义是「等」和「问」，不能缓存。
READONLY_TOOLS = frozenset({
    "get_health", "get_logs", "queue_list", "list_models", "search_models",
    "list_repo_files", "list_workflows", "check_workflow", "inspect_workflow",
    "get_job", "recall_history", "list_templates", "web_search",
})

CACHE_TTL_S = 30.0      # 只读结果复用时长；窗口更短意义不大（一次提问就有几十秒）
from app.ui2api import build as ui2api_build, fetch_obj_info


# ---------------------------------------------------------------- 基础设施
class ToolCtx:
    """工具执行上下文；MCP 模式下 mainwin=None"""

    def __init__(self, cfg: dict, inst: dict, mainwin=None, log=print,
                 profile=None, chat_id: str = "", cancel=None):
        self.cfg = cfg or {}
        self.inst = inst or {}
        self.chat_id = str(chat_id or "")   # 会话 id：recall_history 回查用
        self.mainwin = mainwin            # GUI 模式才有：用于后台下载队列
        self.log = log
        # 取消信号（threading.Event）：长耗时工具（启停实例、等任务）要能被打断。
        # 2026-11-15 事故：助手调 control_instance(start) 等 180s，用户点「停止」
        # 后工具里压根不检查，界面上的「新建对话」又因为 worker 还在而静默返回
        # —— 用户看到的就是「整个软件卡死」。
        self.cancel = cancel
        self.api = apib.api_base(self.inst) or f"http://127.0.0.1:{int(self.inst.get('port') or 8188)}"
        self.comfyui_path = self.inst.get("comfyui_path") or ""
        # 实例解释器：官方模板库（comfyui_workflow_templates*）在它的
        # site-packages 里，扫模板时要靠它定位
        self.python_path = self.inst.get("python_path") or ""
        # 2026-11-07：跟着实例设置里的「模型目录」走，别硬编码 models/
        from app.model_paths import model_root
        self.models_dir = model_root(self.comfyui_path, self.inst.get("models_dir") or "")
        self.workflows_dir = (os.path.join(self.comfyui_path, "user", "default", "workflows")
                              if self.comfyui_path else "")
        self.output_dir = os.path.join(self.comfyui_path, "output") if self.comfyui_path else ""
        # 2026-11-11 会话工作台：绑定的默认工作流 / 模型名。
        # 工作流名缺省时由 _wf_path 兜底——不同会话跑不同模态（视频/生图/音乐），
        # 靠模型自己每次去 list_workflows 挑，实测会挑错模态，也白烧一轮 token。
        p = profile if isinstance(profile, dict) else {}
        self.default_workflow = str(p.get("workflow") or "").strip()
        self.default_model = str(p.get("model") or "").strip()
        # 只读工具结果缓存（2026-11-14）：一次提问内同参数重复调用直接复用。
        # 挂在 ctx 上而不是模块级，跨会话/跨提问天然隔离，不会串数据。
        self._cache = {}                  # key -> (ts, result)
        self.cache_hits = 0               # 供日志/统计

    def ok(self, **kw):
        kw.setdefault("ok", True)
        return kw

    def fail(self, msg: str, **kw):
        kw.update({"ok": False, "error": msg})
        return kw


class InstanceDown(RuntimeError):
    """实例端口无人监听（WinError 10061）：工具层统一翻译成人话，别把 urlopen 栈丢给用户"""

    def __init__(self, port):
        self.port = port
        super().__init__(f"实例未运行：端口 {port} 无监听")


def _http(ctx: ToolCtx, path: str, data: dict = None, timeout: int = 30) -> dict:
    """直连实例；基址与认证统一由 api_base 决定（本地绕系统代理、远程带 token）

    2026-11-23（0.8.39）：原来在这里硬拼 http://127.0.0.1:{port}，远程实例
    根本连不上。现在本地/远程同一入口，只有 inst 的字段不同。
    """
    try:
        return apib.request_json(ctx.inst, path, data, timeout=timeout)
    except apib.ApiError as e:
        msg = str(e)
        txt = msg.lower()
        if (e.status == 0 and ("连接被拒绝" in msg or "10061" in txt
                              or "refused" in txt)):
            raise InstanceDown(int(ctx.inst.get("port") or 8188)) from e
        raise


def _log_tail(path: str, lines: int, keyword: str = "") -> list:
    """读文件尾部 N 行（编码容错：aki 包日志可能混多编码）"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 512 * 1024))       # 最多回读 512KB
            chunk = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    arr = chunk.splitlines()
    arr = arr[-lines:] if len(arr) > lines else arr
    if keyword:
        low = keyword.lower()
        arr = [ln for ln in arr if low in ln.lower()]
    return arr


# ---------------------------------------------------------------- 工具实现
def _probe_ok(port: int, inst: dict | None = None) -> bool:
    """轻探针：实例 HTTP 是否就绪（不带 InstanceDown 翻译，失败返回 False）

    0.8.39：远程实例走完整 /system_stats（带认证、可 https），不能拼 127.0.0.1。
    """
    if inst is not None:
        try:
            if apib.is_remote(inst):
                return apib.probe(inst, timeout=4)
        except Exception:
            pass
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/system_stats", headers={"User-Agent": "ComfyHub"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=4):
            return True
    except Exception:
        return False


def _t_control_instance(args, ctx: ToolCtx):
    """启动/停止/重启当前实例。

    线程纪律：ComfyProcess 内部是 QProcess，只能在创建它的 UI 线程操作；
    start/stop 通过 QTimer.singleShot(0, mainwin, fn) 编组回主线程执行，
    本工具线程只做轮询等待（is_running 是状态枚举的只读检查，跨线程读可接受）。
    """
    action = str(args.get("action") or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        return ctx.fail("action 只支持 start / stop / restart")
    # 远程实例（0.8.39）：进程不在这台机器上，启停必须由用户到远端操作。
    # 明确拒绝比「假装成功」好——否则模型会回报「已启动」，用户去远端一看没动。
    if apib.is_remote(ctx.inst):
        return ctx.fail("这是远程实例（%s），本机无法启停它的进程。"
                        "请到那台机器上启动/停止 ComfyUI（云镜像在平台控制台操作）；"
                        "本机可以直接提交任务、查队列、取结果。"
                        % (apib.api_base(ctx.inst) or "未填地址"))
    mw = ctx.mainwin
    if mw is None or not hasattr(mw, "procs"):
        return ctx.fail("无法启停实例：当前没有主界面句柄（--mcp 外挂模式）。"
                        "请在 ComfyHub 窗口里点启动，或直接对我说「启动实例」时保持窗口打开")
    from PySide6.QtCore import QTimer      # 延迟导入：MCP 无 Qt 环境不炸
    procs = mw.procs
    inst = ctx.inst
    iid = inst["id"]

    def _ui(fn):
        QTimer.singleShot(0, mw, fn)       # 编组回 mainwin 所在的 UI 线程

    def _console(text: str):
        """把 AI 的操作写进控制台（2026-11-16）

        用户反馈「AI 说启动了，控制台什么都没有」——控制台原本只收实例自身
        的 stdout，AI 发的启停指令完全不留痕，出问题无从对账。
        """
        page = getattr(mw, "console_page", None)
        if page is None or not hasattr(page, "append_line"):
            return
        _ui(lambda: page.append_line(text))

    def _brief_tail():
        lines = [t for _, t in procs.tail(iid, 10)][-3:]
        return " | ".join(ln.strip() for ln in lines if ln.strip()) or "（无日志）"

    # ---- stop ----
    if action == "stop":
        if not procs.is_running(inst):
            _console("[AI] 停止实例 %s：本来就没在运行" % (inst.get("name") or ""))
            return ctx.ok(action="stop", already=True, msg="实例本来就未运行")
        _console("[AI] 停止实例 %s …" % (inst.get("name") or ""))
        _ui(lambda: procs.get(inst).stop())
        for _ in range(30):                          # 最多 15s
            if not procs.is_running(inst):
                break
            if _cancelled(ctx):
                return ctx.ok(action="stop", cancelled=True,
                              msg="已按停止，停止指令已发出（后台继续收尾）")
            time.sleep(0.5)
        stopped = not procs.is_running(inst)
        return ctx.ok(action="stop", stopped=stopped,
                      msg="实例已停止" if stopped else
                      f"停止超时（15s），进程可能卡住。日志尾部：{_brief_tail()}")

    # ---- start：已在跑就直接回报 ----
    if action == "start" and procs.is_running(inst):
        port = procs.actual_port(inst) or int(inst.get("port") or 8188)
        return ctx.ok(action="start", already=True, port=port,
                      msg=f"实例已在运行（端口 {port}），无需重复启动")

    # ---- restart：先停旧的 ----
    if action == "restart":
        r = _t_control_instance({"action": "stop"}, ctx)
        if not (r.get("stopped") or r.get("already")):
            return ctx.fail("重启失败：旧实例停不下来。"
                            + str(r.get("error") or r.get("msg") or ""))
        time.sleep(1.0)                              # 给端口释放留点时间

    # AI 触发的启动不要弹浏览器：就绪瞬间抢焦点会把正在对话的用户打断
    # （2026-11-14 实测，Windows 上直接顶掉 ComfyHub 前台）
    _console("[AI] 启动实例 %s …" % (inst.get("name") or ""))
    _ui(lambda: procs.get(inst).start(inst, open_browser=False))
    for _ in range(20):                              # 最多 10s 等进程起来
        if procs.is_running(inst):
            break
        if _cancelled(ctx):
            return ctx.ok(action=action, cancelled=True,
                          msg="已按停止，启动请求已发出但不再等待")
        time.sleep(0.5)
    if not procs.is_running(inst):
        return ctx.fail("启动失败：进程没有起来（常见原因：Python 路径无效、"
                        f"端口全被占用）。日志尾部：{_brief_tail()}")

    # ---- 等 HTTP 就绪（模型导入可能要几十秒）----
    timeout_s = max(30, min(int(args.get("timeout_s") or 180), 300))
    deadline = time.time() + timeout_s
    port = int(inst.get("port") or 8188)
    ready = False
    while time.time() < deadline:
        if _cancelled(ctx):
            # 别再占着 AI 会话等下去了：进程该起还是会起，让模型用 get_health 复查
            return ctx.ok(action=action, cancelled=True,
                          pid=procs.get(inst).pid,
                          port=procs.actual_port(inst) or port,
                          msg="已按停止，不再等待 HTTP 就绪（进程仍在后台启动，"
                              "过一会儿可用 get_health 复查）")
        if not procs.is_running(inst):
            # 进程自己退了（缺依赖/端口冲突/崩溃）—— 等满 180s 毫无意义，
            # 直接把日志尾部交给模型，比一句「未就绪」有用得多
            return ctx.fail("实例进程启动后立即退出（不是慢，是失败了）。"
                            f"日志尾部：{_brief_tail()}")
        ap = procs.actual_port(inst) or port         # 端口被占自动顺延后的真实端口
        port = ap
        if _probe_ok(ap, inst):
            ready = True
            break
        time.sleep(2.0)
    pid = procs.get(inst).pid
    if not ready:
        return ctx.ok(action=action, pid=pid, port=port, ready=False,
                      msg=f"进程已启动（PID {pid}）但 {timeout_s}s 内 HTTP 未就绪"
                          "（大模型首次加载较慢），稍后可用 get_health 复查")
    shifted = port != int(inst.get("port") or 8188)
    return ctx.ok(action=action, pid=pid, port=port, ready=True,
                  msg=f"实例已就绪：http://127.0.0.1:{port}"
                      + ("（注意：端口被占自动顺延了，其他工具若连不上以这个端口为准）"
                         if shifted else ""))


def _t_health(args, ctx: ToolCtx):
    stats = _http(ctx, "/system_stats", timeout=15)
    q = _http(ctx, "/queue", timeout=15)
    sysinfo = stats.get("system") or {}
    dev = (stats.get("devices") or [{}])[0]
    return ctx.ok(
        comfyui_version=sysinfo.get("comfyui_version"),
        python_version=sysinfo.get("python_version"),
        pytorch_version=sysinfo.get("pytorch_version"),
        gpu=dev.get("name"),
        vram_free_gb=round((dev.get("vram_free") or 0) / 1024 ** 3, 2),
        vram_total_gb=round((dev.get("vram_total") or 0) / 1024 ** 3, 2),
        queue_running=len(q.get("queue_running") or []),
        queue_pending=len(q.get("queue_pending") or []),
    )


def _t_logs(args, ctx: ToolCtx):
    # 远程实例的日志在远端磁盘上，本机读不到（0.8.39 明确告知，别报「文件不存在」）
    if apib.is_remote(ctx.inst):
        return ctx.fail("远程实例的日志在远端机器上，本机无法读取。"
                        "请在该机器上查看 ComfyUI 控制台输出，或使用云平台提供的日志面板。")
    path = os.path.join(ctx.comfyui_path, "user", f"comfyui_{ctx.inst.get('port') or 8188}.log")
    if not os.path.isfile(path):
        return ctx.fail(f"日志文件不存在：{path}")
    arr = _log_tail(path, int(args.get("lines") or 50), str(args.get("keyword") or ""))
    return ctx.ok(log_file=path, lines=arr)


def _t_recall_history(args, ctx: ToolCtx):
    """在被压缩掉的早期对话里按关键词回查原文（2026-11-14）

    摘要必然丢细节。压完之后用户问「刚才那个报错具体怎么说」，
    模型可以来这里捞原文，而不是重新问一遍用户。
    """
    kw = str(args.get("keyword") or "").strip()
    if not kw:
        return ctx.fail("keyword 必填：要回查什么（模型名 / 文件名 / 报错关键词都行）")
    cid = str(getattr(ctx, "chat_id", "") or "")
    if not cid:
        return ctx.fail("当前会话没有可回查的历史片段（只有对话被压缩过才会有）")
    try:
        limit = max(1, min(20, int(args.get("limit") or 6)))
    except (TypeError, ValueError):
        limit = 6
    from app import ai_chat_store
    rows = ai_chat_store.search_parts(cid, kw, limit=limit)
    if not rows:
        return ctx.fail(f"已压缩的早期对话里没有包含「{kw}」的内容；"
                        "换个更短的关键词，或直接问用户")
    return ctx.ok(matches=len(rows), items=rows,
                  note="这些是摘要之前的原文片段，可据此回答，不必再问用户")


def _t_queue_list(args, ctx: ToolCtx):
    q = _http(ctx, "/queue", timeout=15)
    running = [{"prompt_id": it[1], "number": it[0]} for it in (q.get("queue_running") or [])]
    pending = [{"prompt_id": it[1], "number": it[0]} for it in (q.get("queue_pending") or [])]
    return ctx.ok(running=running, pending=pending)


def _t_queue_cancel(args, ctx: ToolCtx):
    _http(ctx, "/interrupt", data={}, timeout=15)
    time.sleep(1.0)
    q = _http(ctx, "/queue", timeout=15)
    still = len(q.get("queue_running") or [])
    if still:
        return ctx.ok(stopped=False,
                      note=f"中断已发出但仍有 {still} 个任务在跑——ComfyUI 只在采样步间响应中断，"
                           f"长步进任务需再等或重启实例")
    return ctx.ok(stopped=True)


def _t_models_list(args, ctx: ToolCtx):
    """优先实例 API（能覆盖 extra_model_paths 挂载目录），失败退回文件系统扫描"""
    try:
        data = _http(ctx, "/models", timeout=20)
        if isinstance(data, dict):
            out = {k: len(v or []) for k, v in data.items() if isinstance(v, list)}
            detail = {k: (v or [])[:20] for k, v in data.items() if isinstance(v, list)}
            return ctx.ok(source="api", counts=out, samples=detail)
    except Exception:
        pass
    root = ctx.models_dir
    if not root or not os.path.isdir(root):
        return ctx.fail(f"模型目录不存在：{root}")
    # 2026-11-08：文件系统兜底也把 extra_model_paths 挂载目录算进来，
    # 否则 API 挂了以后 AI 报的模型数量会比界面少一半
    from app import model_paths
    out, samples = {}, {}
    for cat_name, dirs in model_paths.model_dirs(
            ctx.comfyui_path, ctx.inst.get("models_dir") or "",
            model_paths.SRC_ALL,
            model_paths.mount_root_of(ctx.inst)).items():
        fs = []
        for d in dirs:
            try:
                fs += [f for f in os.listdir(d)
                       if os.path.isfile(os.path.join(d, f))]
            except OSError:
                continue
        if fs:
            out[cat_name] = len(fs)
            samples[cat_name] = fs[:20]
    return ctx.ok(source="filesystem", counts=out, samples=samples,
                  note="含实例模型根与 extra_model_paths 挂载目录")


def _t_models_search(args, ctx: ToolCtx):
    kw = str(args.get("keyword") or "").strip()
    if not kw:
        return ctx.fail("keyword 不能为空")
    res = model_store.search_models(kw, limit=int(args.get("limit") or 10))
    items = [{"id": m["id"], "downloads": m["downloads"], "pipeline": m["pipeline"]}
             for m in res]
    if not items:
        # 空结果别只回空数组：模型会拿同一关键词反复重试直到轮数耗尽
        # （2026-11-06 实测：search_models 连调 8 次仍是 0 仓库）
        return ctx.ok(keyword=kw, results=[], ok=True,
                      hint="没搜到这个关键词。HF 只索引仓库名与描述，中文关键词命中率很低："
                           "改用英文品牌词（qwen / flux / wan / sd3 / pony / hunyuan），"
                           "或先用 list_models 看本地已经有哪些；"
                           "不要用同样的关键词重复搜索。")
    return ctx.ok(keyword=kw, results=items, next_cursor=getattr(res, "next_cursor", None),
                  note="用 list_repo_files 查看某仓库的权重文件")


def _t_repo_files(args, ctx: ToolCtx):
    rid = str(args.get("repo_id") or "").strip()
    if not rid:
        return ctx.fail("repo_id 不能为空")
    files = model_store.list_weight_files(rid)
    return ctx.ok(repo_id=rid, files=files,
                  note="文件可直接 download_model 下载")


def _t_model_download(args, ctx: ToolCtx):
    rid = str(args.get("repo_id") or "").strip()
    fp = str(args.get("file_path") or "").strip()
    if not rid or not fp:
        return ctx.fail("repo_id 与 file_path 必填")
    cat = str(args.get("category") or "").strip() or model_store.guess_category(fp)
    dest = os.path.join(ctx.models_dir, cat) if ctx.models_dir else ""
    if not dest:
        return ctx.fail("实例未配置 comfyui_path")
    os.makedirs(dest, exist_ok=True)
    if ctx.mainwin is not None:
        dl = getattr(getattr(ctx.mainwin, "models_page", None), "dl_center", None)
        if dl is not None:
            # 2026-11-12 修：本工具跑在 _AgentWorker 线程里，直接 dl.enqueue()
            # 会在非 GUI 线程 new QWidget，行挂不上界面（用户看不到进度）。
            # request_download() 用信号排队回 UI 线程，任何线程都能安全调用。
            dl.request_download([(rid, fp, dest)], source="ai")
            return ctx.ok(queued=True, dest=dest,
                          note="已加入模型库下载中心（模型库页面右上角 ⬇ 可看进度/取消），"
                               "下载完成后会刷新模型列表；如需确认完成可再查文件")
    model_store.download_file(rid, fp, dest)
    return ctx.ok(queued=False, dest=os.path.join(dest, os.path.basename(fp)),
                  note="同步下载完成")


def _t_workflows_list(args, ctx: ToolCtx):
    d = ctx.workflows_dir
    if not d or not os.path.isdir(d):
        return ctx.fail(f"工作流目录不存在：{d}")
    out = []
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        if fn.lower().endswith(".json") and os.path.isfile(p):
            out.append({"name": fn, "size_kb": round(os.path.getsize(p) / 1024, 1),
                        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return ctx.ok(directory=d, workflows=out[:30], total=len(out))


def _t_workflow_check(args, ctx: ToolCtx):
    name = str(args.get("name") or "").strip()
    path = os.path.join(ctx.workflows_dir, name) if ctx.workflows_dir else ""
    if not name or not os.path.isfile(path):
        return ctx.fail(f"工作流不存在：{name}（用 list_workflows 查列表）")
    r = workflow_scan.analyze_workflow(ctx.comfyui_path, path, log=ctx.log)
    return ctx.ok(
        name=name,
        kernel_version=r.get("kernel_ver"),
        node_types=r.get("class_types"),
        required_plugins=r.get("required"),
        missing_or_unknown=r.get("missing"),
        models_needed=r.get("models"),
    )


def _t_workflow_run(args, ctx: ToolCtx):
    name = _wf_name(args)
    path = os.path.join(ctx.workflows_dir, name) if ctx.workflows_dir else ""
    if not name or not os.path.isfile(path):
        return ctx.fail(f"工作流不存在：{name or '(空)'}（用 list_workflows 查列表）")
    with open(path, encoding="utf-8") as f:
        wf = json.load(f)
    if isinstance(wf.get("nodes"), list):          # UI 格式 → 转 API
        obj = fetch_obj_info(ctx.api, inst=ctx.inst)
        prompt, warns = ui2api_build(wf, obj)
    elif isinstance(wf, dict) and any(isinstance(v, dict) and "class_type" in v
                                      for v in wf.values()):
        prompt, warns = wf, ["API 格式，直接提交"]   # 已是 API 格式
    else:
        return ctx.fail("工作流格式无法识别（既无 nodes 数组，也无 class_type）")
    body = {"prompt": prompt, "client_id": "comfyhub-ai"}
    r = _http(ctx, "/prompt", data=body, timeout=60)
    pid = r.get("prompt_id")
    if pid:
        _SUBMIT_TS[str(pid)] = time.time()      # 用来判断出图是新文件还是缓存旧图
        if len(_SUBMIT_TS) > 200:               # 别无限增长
            for k in list(_SUBMIT_TS)[:100]:
                _SUBMIT_TS.pop(k, None)
    return ctx.ok(prompt_id=pid, queue_number=r.get("number"),
                  node_errors=r.get("node_errors") or {},
                  warnings=warns,
                  note="用 get_job 查询进度与出图结果")


_SUBMIT_TS = {}     # prompt_id -> 提交时间（用于识别“缓存命中、本次没真出图”）


def _try_fetch_view(ctx, im: dict) -> str:
    """本地没有这个文件时，向实例要一份（0.8.39）

    远程实例/SSH 隧道实例的产物根本不在本机磁盘上，只 `isfile` 会全部判 missing，
    AI 就会回报「出图完成但文件不存在」。这里改走 ComfyUI 的 GET /view 下载到
    本地远程产物目录，返回真实本地路径——后续 os.startfile、二次处理都能用。
    本机实例同样受益：用户自定义过 --output-directory 时也能取到图。
    """
    fn = str((im or {}).get("filename") or "")
    if not fn:
        return ""
    try:
        return apib.fetch_view(
            ctx.inst, fn, (im or {}).get("subfolder") or "",
            (im or {}).get("type") or "output",
            dest_dir=apib.remote_output_dir(ctx.inst, ctx.cfg))
    except Exception:
        return ""


def _collect_images(ctx, outputs, since=None) -> tuple:
    """从 history.outputs 抽图片绝对路径，并**逐个验存在性 + 新鲜度**。

    2026-11-06 用户报障：AI 报「出图完成 + 路径」，但文件夹里没有文件。
    三类情况分开返回，模型就没法再瞎报：
      · found   —— 文件真存在，且（给了 since 时）是本次运行之后写的
      · stale   —— 文件存在但 mtime 早于本次提交 → ComfyUI 缓存命中，没生成新图
      · missing —— history 说有这么张图，磁盘上根本没有
    """
    found, missing, stale = [], [], []
    for o in (outputs or {}).values():
        for im in o.get("images", []):
            sub = (im.get("subfolder") or "").replace("\\", "/").strip("/")
            p = os.path.join(ctx.output_dir, sub, im.get("filename", ""))
            if not os.path.isfile(p):
                # 本地没有 → 走 /view 要一份（远程实例的唯一出路，0.8.39）
                got = _try_fetch_view(ctx, im)
                if got:
                    found.append(got)
                    continue
                missing.append(p)
                continue
            if since is not None:
                try:
                    if os.path.getmtime(p) < since - 5:   # 5s 容差
                        stale.append(p)
                        continue
                except OSError:
                    pass
            found.append(p)
    return found, missing, stale


def _t_job_status(args, ctx: ToolCtx):
    pid = str(args.get("prompt_id") or "").strip()
    if not pid:
        return ctx.fail("prompt_id 必填")
    try:
        h = _http(ctx, f"/history/{pid}", timeout=15)
    except Exception as e:
        return ctx.fail(f"查询失败：{e}")
    entry = (h or {}).get(pid)
    if not entry:
        q = _http(ctx, "/queue", timeout=15)
        running = [it[1] for it in (q.get("queue_running") or [])]
        pending = [it[1] for it in (q.get("queue_pending") or [])]
        if pid in running:
            return ctx.ok(state="running")
        if pid in pending:
            return ctx.ok(state="pending")
        return ctx.ok(state="not_found",
                      note="历史与队列都没有它（实例重启会清空历史）")
    st = entry.get("status") or {}
    imgs, missing_imgs, stale_imgs = _collect_images(
        ctx, entry.get("outputs"), since=_SUBMIT_TS.get(pid))
    err = ""
    for m in st.get("messages", []):
        if m[0] == "execution_error":
            d = m[1]
            err = f"{d.get('node_type')}#{d.get('node_id')}: {d.get('exception_message', '')[:300]}"
    out = ctx.ok(state=st.get("status_str"), images=imgs, error=err,
                 verified=bool(imgs))
    if missing_imgs:
        out["missing_images"] = missing_imgs
        out["note"] = ("history 报告了图片但磁盘上不存在（可能被移动/删除）"
                       "——不要把这些路径当出图结果")
    if stale_imgs:
        out["stale_images"] = stale_imgs
        out["note"] = ("命中了 ComfyUI 缓存，本次没有生成新图（提示词/参数与上次相同），"
                       "这些是旧文件；要新图请改提示词或种子")
    return out



# ---------------------------------------------------------------- 工作流编辑（AI 出图闭环）
TEXT_NODE_HINTS = ("textencode", "cliptextencode", "t5", "prompt")
LATENT_NODE_HINTS = ("emptylatent", "emptysd3latent", "emptysdxllatent", "imageparams")
SAMPLER_HINTS = ("ksampler", "sampler")


def _wf_name(args) -> str:
    """取工作流名：模型偶尔会把参数写成 workflow / file / workflow_name，
    这里统一兜住，别让它白失败三轮（2026-11-06 实测：连续 3 次参数形状错）"""
    for k in ("name", "workflow", "workflow_name", "file", "filename"):
        v = str(args.get(k) or "").strip()
        if v:
            return v
    return ""


def _wf_path(ctx, name):
    name = str(name or "").strip()
    if name and not name.lower().endswith(".json"):
        name += ".json"
    # 会话工作台绑定的默认工作流（2026-11-11）：没给名字、或给的名字在目录里
    # 根本不存在，一律回落到绑定值。模型经常把工作流名记错/编造，
    # 直接报错会白烧一轮工具往返；而回落到错的模态更难查，所以只认用户钉死的那一个。
    dw = str(getattr(ctx, "default_workflow", "") or "").strip()
    if dw:
        if not dw.lower().endswith(".json"):
            dw += ".json"
        dw = os.path.basename(dw)
        dp = os.path.join(ctx.workflows_dir, dw)
        cur = os.path.join(ctx.workflows_dir, os.path.basename(name)) if name else ""
        if ctx.workflows_dir and os.path.isfile(dp) and (not name or not os.path.isfile(cur)):
            name, cur = dw, dp
    if not name:
        try:
            names = [f for f in os.listdir(ctx.workflows_dir) if f.endswith(".json")]
        except Exception:
            names = []
        hint = "、".join(sorted(names)[:12]) if names else "（目录里没有工作流）"
        raise ValueError(f"工作流名不能为空。可选：{hint}")
    p = os.path.join(ctx.workflows_dir, os.path.basename(name))
    if not os.path.isfile(p):
        raise FileNotFoundError(f"工作流不存在：{name}（用 list_workflows 查列表）")
    return p


def _is_api_wf(wf):
    """API 格式：顶层 {node_id: {class_type, inputs}}，没有 UI 的 nodes 数组。

    2026-11-18 模拟实测：minimal_sd_txt2img.json 就是这种，旧版 inspect/patch
    只认 UI 格式 → node_count=0、patch 报「节点不存在」，AI 改不了提示词。
    """
    return (isinstance(wf, dict) and not isinstance(wf.get("nodes"), list)
            and any(isinstance(v, dict) and "class_type" in v for v in wf.values()))


def _iter_nodes(wf):
    """遍历 (key, node)：UI 顶层 key=节点id；子图 key='sg:<子图id>:<节点id>'；
    API 格式 key=顶层节点id（node 带 class_type/inputs，无 widgets_values）。"""
    if _is_api_wf(wf):
        for k, v in wf.items():
            if isinstance(v, dict) and "class_type" in v:
                yield str(k), v
        return
    for n in wf.get("nodes") or []:
        yield str(n.get("id")), n
    for sg in (wf.get("definitions") or {}).get("subgraphs") or []:
        for n in sg.get("nodes") or []:
            yield f"sg:{sg.get('id')}:{n.get('id')}", n


def _node_hint(ntype: str) -> str:
    t = (ntype or "").lower()
    if any(k in t for k in LATENT_NODE_HINTS):
        return "画布尺寸"
    if any(k in t for k in SAMPLER_HINTS):
        return "采样器"
    if any(k in t for k in TEXT_NODE_HINTS):
        return "提示词"
    if "loader" in t or "load" in t:
        return "模型加载"
    return ""


def _t_inspect_workflow(args, ctx: ToolCtx):
    """给 AI 看工作流结构：哪些节点能改、当前值是什么"""
    path = _wf_path(ctx, _wf_name(args))
    with open(path, encoding="utf-8") as f:
        wf = json.load(f)
    api = _is_api_wf(wf)
    nodes = []
    for key, n in _iter_nodes(wf):
        # 空字段不写（2026-11-14）：33 个节点里 title 大多为空，
        # 每条都带 "title": "" 纯浪费预算，导致真内容被裁掉
        ntype = n.get("class_type") if api else n.get("type")
        item = {"key": key, "type": ntype}
        if (not api) and n.get("title"):
            item["title"] = n["title"]
        hint = _node_hint(ntype)
        if hint:
            item["hint"] = hint
        if api:
            # API 格式没有 widgets_values，值在 inputs 里（键名即字段名）
            ins = n.get("inputs") or {}
            item["inputs"] = {k: (str(v)[:120] + "…") if isinstance(v, str) and len(str(v)) > 120
                              else v for k, v in ins.items()}
        else:
            wv = n.get("widgets_values")
            vals = []
            if isinstance(wv, list):
                vals = [(str(v)[:120] + "…") if isinstance(v, str) and len(str(v)) > 120 else v
                        for v in wv]
            if vals:
                item["widgets"] = vals
        nodes.append(item)
    has_sub = bool((wf.get("definitions") or {}).get("subgraphs"))
    if api:
        note = ("API 格式：patch_workflow 用 ops[].field 指定 inputs 里的键名"
                "（如 text/seed/steps），node 填上面的 key，不要用 index。")
    else:
        note = ("节点 key 直接用于 patch_workflow 的 ops[].node；widgets 顺序即 index。"
                + (" 该工作流含子图，子图节点的 key 形如 sg:<子图id>:<节点id>。" if has_sub else ""))
    return ctx.ok(
        name=os.path.basename(path), node_count=len(nodes), nodes=nodes, note=note,
    )


def _widget_index(obj_info, ntype, field):
    """按字段名找 widget 序号（object_info 的 required/optional 顺序）"""
    info = obj_info.get(ntype) or {}
    idx = 0
    for sec in ("required", "optional"):
        for k, spec in (info.get("input", {}).get(sec) or {}).items():
            if not isinstance(spec, list) or not spec:
                continue
            t = spec[0]
            is_w = isinstance(t, list) or (isinstance(t, str)
                                           and (t in ("INT", "FLOAT", "STRING", "BOOLEAN")
                                                or t in ("COMBO", "COMFY_DYNAMICCOMBO_V3")))
            if not is_w:
                continue
            if k == field:
                return idx
            idx += 1
            if k == "seed" or (len(spec) > 1 and isinstance(spec[1], dict)
                               and spec[1].get("control_after_generate")):
                idx += 1        # control_after_generate 占一个槽
    return None


def _t_patch_workflow(args, ctx: ToolCtx):
    """改工作流副本（绝不覆盖原文件）：ops=[{node, index|field, value}]"""
    src_path = _wf_path(ctx, _wf_name(args))
    ops = args.get("ops") or args.get("patches") or args.get("changes") or []
    if isinstance(ops, dict):            # 单个 op 没套数组
        ops = [ops]
    if not isinstance(ops, list) or not ops:
        return ctx.fail('ops 不能为空，格式：[{"node":"34","index":0,"value":"新提示词"}]')
    # 模型有时把 ops 写成字符串数组（说明它没读 schema）——直接给出可抄的格式
    bad = [x for x in ops if not isinstance(x, dict)]
    if bad:
        return ctx.fail("ops 的元素必须是对象，不是字符串。正确格式："
                        '[{"node":"节点id","index":0,"value":"值"}]；'
                        f"收到的是：{str(bad[0])[:60]}")
    with open(src_path, encoding="utf-8") as f:
        wf = json.load(f)
    api = _is_api_wf(wf)
    index = {k: n for k, n in _iter_nodes(wf)}
    obj = None
    applied, failed = [], []
    for op in ops:
        key = str(op.get("node") or op.get("key") or op.get("node_id") or "").strip()
        node = index.get(key)
        if node is None:
            # 兜底：模型有时把 class_type 当 node 传（如 "CLIPTextEncode"），
            # 唯一匹配就直接用，多个就报清楚让模型挑 key（2026-11-18 模拟实测）
            hits = [k for k, n in index.items()
                    if str(n.get("class_type") or n.get("type") or "") == key]
            if len(hits) == 1:
                key, node = hits[0], index[hits[0]]
            elif len(hits) > 1:
                failed.append(f"节点类型 {key} 有多个（key: {'、'.join(hits)}），"
                              "请用 inspect 的 key 指定具体节点")
                continue
            else:
                failed.append(f"节点 {key} 不存在")
                continue
        if api:
            # API 格式：值在 inputs 里，按字段名改；index 也容忍（映射到 inputs 键序）
            ins = node.get("inputs")
            if not isinstance(ins, dict):
                ins = {}
                node["inputs"] = ins
            field = str(op.get("field") or "").strip()
            if not field and op.get("index") is not None:
                keys = list(ins.keys())
                i = int(op.get("index") or 0)
                if 0 <= i < len(keys):
                    field = keys[i]
                else:
                    failed.append(f"节点 {key} 没有第 {i} 个输入字段"
                                  f"（现有：{'、'.join(keys) or '空'}）")
                    continue
            if not field:
                failed.append(f"节点 {key} 缺 field：API 格式要写 inputs 里的键名"
                              "（如 text/seed/steps），不是 index")
                continue
            if field not in ins:
                failed.append(f"节点 {key} 没有字段 {field}"
                              f"（现有：{'、'.join(ins.keys()) or '空'}）")
                continue
            ins[field] = op.get("value")
            applied.append(f"{key}.{field}={str(op.get('value'))[:40]}")
            continue
        wv = node.setdefault("widgets_values", [])
        if not isinstance(wv, list):
            wv = []
            node["widgets_values"] = wv
        idx = op.get("index")
        if idx is None and op.get("field"):
            if obj is None:
                obj = fetch_obj_info(ctx.api, inst=ctx.inst)
            idx = _widget_index(obj, node.get("type"), str(op["field"]))
            if idx is None:
                failed.append(f"节点 {key} 找不到字段 {op.get('field')}")
                continue
        idx = int(idx or 0)
        while len(wv) <= idx:
            wv.append("")
        wv[idx] = op.get("value")
        # widgets_values_named 与数组由前端同一循环产出、键序即 widget 序
        # （2026-11-18 H3 实测：两者值会分叉，只改数组在「named 优先」的
        #  前端里 patch 等于没改）→ 按键序同步
        named = node.get("widgets_values_named")
        if isinstance(named, dict):
            nkeys = list(named.keys())
            if idx < len(nkeys):
                named[nkeys[idx]] = op.get("value")
        applied.append(f"{key}.#{idx}={str(op.get('value'))[:40]}")
    if not applied:
        return ctx.fail("没有任何改动生效：" + "；".join(failed))
    stem = os.path.splitext(os.path.basename(src_path))[0]
    out_name = stem + "_ai.json"
    out_path = os.path.join(ctx.workflows_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=2)
    return ctx.ok(name=out_name, applied=applied, failed=failed,
                  note="改的是副本，原工作流未被修改；用 run_workflow 跑这个新名字")


def _t_list_templates(args, ctx: ToolCtx):
    """检索工作流模板：本地库 + 官方模板库（2026-11-15）

    为什么不让模型直接写工作流 JSON：实测 2B 级模型会编节点名与字段
    （KSampler 缺 model/positive/negative、cfg 写成 cfg_scale）。改成
    「选模板 + 填参数」，图结构由代码保证，模型只做语言理解。

    官方模板（pip 的 comfyui_workflow_templates + ComfyUI/blueprints）一并入库，
    每个候选带上缺模型数——官方模板引用的模型动辄 10GB+，不先提示，模型会
    直接 apply 然后跑出「模型不存在」。
    """
    from app import workflow_lib
    q = str(args.get("query") or args.get("keyword") or "").strip()
    tpls = workflow_lib.scan(ctx.comfyui_path, include_official=True,
                             python_path=getattr(ctx, "python_path", ""))
    if not tpls:
        return ctx.fail("工作流库里没有可用模板（目录为空？）")
    try:
        limit = max(1, min(10, int(args.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5
    hits = workflow_lib.search(tpls, q, limit=limit) if q else []
    if not hits:            # 检索无命中：给最近修改的几个兜底，别让模型空手而归
        hits = tpls[:limit]
    inst = workflow_lib.installed_models(ctx.comfyui_path,
                                         ctx.inst.get("models_dir") or "")
    out = []
    for t in hits:
        brief = workflow_lib.brief(t, with_slots=True)
        brief["source"] = t.get("source") or "local"
        miss = workflow_lib.missing_models(t, inst)
        lack = [m for m in miss if not m["ok"]]
        brief["models_ok"] = len(miss) - len(lack)
        brief["models_total"] = len(miss)
        if lack:
            brief["missing"] = ["%s(%s)" % (m["file"], m["dir"]) for m in lack[:4]]
            brief["model_hint"] = workflow_lib.model_note(t, miss)
        out.append(brief)
    return ctx.ok(
        total=len(tpls),
        local=sum(1 for t in tpls if t.get("source") == "local"),
        official=sum(1 for t in tpls if t.get("source") != "local"),
        candidates=out,
        note="挑一个 name，用 apply_template 只改参数；不要输出节点结构。"
             "missing 里的模型没装时，先告诉用户要下什么（可用 download_model 或让用户在模型库下载）",
    )


def _t_apply_template(args, ctx: ToolCtx):
    """用模板生成新工作流（只改值，结构由守卫保证不变）"""
    from app import workflow_lib
    name = str(args.get("name") or "").strip()
    edits = args.get("edits") or args.get("params") or {}
    if not name:
        return ctx.fail("缺少 name（用 list_templates 查模板名）")
    found = workflow_lib.find_template(name, ctx.comfyui_path,
                                       getattr(ctx, "python_path", ""))
    src = found.get("path") or ""
    if not src or not os.path.isfile(src):
        return ctx.fail(f"模板不存在：{name}（用 list_templates 查）")
    if not isinstance(edits, dict) or not edits:
        return ctx.fail('edits 不能为空，格式：{"prompt": "...", "width": 832}')
    with open(src, encoding="utf-8") as f:
        wf = json.load(f)
    try:
        new, applied, warns = workflow_lib.apply_edits(wf, edits)
    except ValueError as e:
        return ctx.fail(str(e))
    if not applied:
        return ctx.fail("没有一项参数被应用：" + "；".join(warns or ["edits 的键都不在模板里"]))
    problems = workflow_lib.validate(new)
    stem = os.path.splitext(os.path.basename(src))[0]
    out_name = stem + "_tpl.json"
    out_path = os.path.join(ctx.workflows_dir, out_name)
    os.makedirs(ctx.workflows_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=2)
    # 缺模型提示：官方模板尤其需要（用户装的是量化版，官方模板认的是另一份文件名）
    tpl = workflow_lib.describe(src, source=found.get("source") or "local")
    inst = workflow_lib.installed_models(ctx.comfyui_path,
                                         ctx.inst.get("models_dir") or "")
    miss = workflow_lib.missing_models(tpl or {}, inst)
    lack = [m for m in miss if not m["ok"]]
    res = ctx.ok(
        name=out_name,
        source=found.get("source") or "local",
        applied=["%s=%s" % (a["field"], str(a["value"])[:40]) for a in applied],
        warnings=warns,
        problems=problems,
        note=("结构未改动，可先 check_workflow 体检再 run_workflow"
              if not problems else "静态校验发现问题，先别跑，按 problems 修正参数"),
    )
    if lack:
        res["missing_models"] = ["%s(%s)" % (m["file"], m["dir"]) for m in lack]
        res["model_hint"] = workflow_lib.model_note(tpl or {}, miss)
        res["note"] += "；另有 %d 个模型没装，先按 model_hint 补齐再跑" % len(lack)
    return res


def _t_import_workflow(args, ctx: ToolCtx):
    """把用户粘贴的 JSON 存成工作流（支持 UI 格式与 API 格式）"""
    raw = args.get("json_text") or ""
    name = str(args.get("name") or "").strip()
    if not raw.strip():
        return ctx.fail("json_text 不能为空")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return ctx.fail(f"JSON 解析失败：{e}")
    if not isinstance(data, dict):
        return ctx.fail("JSON 必须是对象")
    if isinstance(data.get("nodes"), list):
        fmt = "ui"
    elif any(isinstance(v, dict) and "class_type" in v for v in data.values()):
        fmt = "api"
    else:
        return ctx.fail("无法识别的格式：既不是 ComfyUI UI 工作流（含 nodes 数组），"
                        "也不是 API 格式（含 class_type）")
    if not name:
        name = "导入工作流"
    name = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "导入工作流"
    if not name.lower().endswith(".json"):
        name += ".json"
    path = os.path.join(ctx.workflows_dir, name)
    os.makedirs(ctx.workflows_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return ctx.ok(name=name, format=fmt, path=path,
                  note="API 格式可直接由 run_workflow 提交（内部会识别），UI 格式会先转换")


def _t_wait_job(args, ctx: ToolCtx):
    """等任务跑完（轮询），返回出图绝对路径。省得模型反复自己查。"""
    pid = str(args.get("prompt_id") or "").strip()
    if not pid:
        return ctx.fail("prompt_id 必填")
    limit = max(10, min(1800, int(args.get("timeout_s") or 300)))
    t0 = time.time()
    while time.time() - t0 < limit:
        if _cancelled(ctx):
            return ctx.ok(state="cancelled", images=[],
                          note="用户已停止等待；任务可能还在跑，可用 get_job 复查")
        try:
            h = _http(ctx, f"/history/{pid}", timeout=15)
        except InstanceDown:
            raise
        except Exception:
            h = {}
        entry = (h or {}).get(pid)
        if entry:
            st = entry.get("status") or {}
            imgs, missing_imgs, stale_imgs = _collect_images(
                ctx, entry.get("outputs"), since=_SUBMIT_TS.get(pid))
            err = ""
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    d = m[1]
                    err = f"{d.get('node_type')}#{d.get('node_id')}: " \
                          f"{str(d.get('exception_message'))[:300]}"
            out = ctx.ok(state=st.get("status_str"), images=imgs, error=err,
                         verified=bool(imgs), elapsed_s=round(time.time() - t0, 1))
            if missing_imgs:
                out["missing_images"] = missing_imgs
                out["note"] = ("history 报告了图片但磁盘上不存在（可能被移动/删除）"
                               "——不要把这些路径当出图结果，"
                               "可按 missing_images 里的文件名去 output 目录搜一下")
            if stale_imgs:
                out["stale_images"] = stale_imgs
                out["note"] = ("命中了 ComfyUI 缓存，本次没有生成新图"
                               "（提示词/参数与上次完全一样），返回的是旧文件；"
                               "要新图请改动提示词或随机种子")
            return out
        time.sleep(2)
    return ctx.ok(state="timeout", images=[],
                  note=f"等了 {limit}s 还没完成，可用 get_job 再查")


def _t_web_search(args, ctx: ToolCtx):
    """联网搜索（2026-11-15）

    本地 2B 模型的知识停在训练时，用户问「H3 要多少显存」这类新东西，它会
    一本正经地编。给它一个真能查的工具，比在提示词里喊「别编」有用。
    返回只给标题/链接/摘要——正文塞进小模型上下文只会挤掉真正的问题。
    """
    from app import web_search
    q = str(args.get("query") or args.get("q") or "").strip()
    if not q:
        return ctx.fail("query 不能为空")
    try:
        limit = max(1, min(10, int(args.get("limit") or 6)))
    except (TypeError, ValueError):
        limit = 6
    key = ""
    try:
        key = ((ctx.cfg.get("settings") or {}).get("tavily_key")
               or os.environ.get("TAVILY_API_KEY") or "")
    except Exception:
        key = os.environ.get("TAVILY_API_KEY") or ""
    res = web_search.search(q, limit=limit, tavily_key=key)
    if not res.get("ok"):
        return ctx.fail(res.get("error") or "搜索失败")
    return ctx.ok(engine=res.get("engine"), count=len(res["results"]),
                  results=res["results"],
                  note="引用事实时带上 url；结果里没有的内容不要臆测。"
                       "查到的模型名/文件名可以直接用于 search_models 或 download_model")


def _t_ask_user(args: dict, ctx: ToolCtx) -> dict:
    """向用户呈现选择项（UI 渲染成可点按钮）；本身无副作用。

    2026-11-11：助手反问（要不要启动实例/用哪个工作流）此前只能用户打字回，
    对齐 WorkBuddy 的选项卡交互——模型调本工具给选项，UI 在回复下方渲染按钮。
    """
    options = [str(o).strip() for o in (args.get("options") or [])
               if str(o).strip()][:6]
    return {"ok": True, "options": options,
            "note": "选项按钮已展示给用户，用户点击或输入后会作为下一条消息发来；"
                    "本轮回复到此为止即可"}


TOOLS = [
    {"name": "get_health", "desc": "实例体检：ComfyUI/torch 版本、GPU 与显存、队列深度。排查或派任务前先调这个。",
     "schema": {"type": "object", "properties": {}, "required": []}, "fn": _t_health},
    {"name": "control_instance", "desc": "启动/停止/重启当前 ComfyUI 实例（action: start/stop/restart）。"
                                         "实例没在跑时直接调它拉起来，不要让用户去工作台手动点。",
     "schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["start", "stop", "restart"]},
         "timeout_s": {"type": "integer", "description": "启动后等 HTTP 就绪的秒数，默认 180"}},
         "required": ["action"]}, "fn": _t_control_instance},
    {"name": "get_logs", "desc": "读实例日志尾部（可按关键词过滤）。报错时用它找 traceback。",
     "schema": {"type": "object", "properties": {
         "lines": {"type": "integer", "description": "尾部行数，默认 50"},
         "keyword": {"type": "string", "description": "只保留含该关键词的行"}}, "required": []},
     "fn": _t_logs},
    {"name": "queue_list", "desc": "查看当前队列：正在跑与排队的任务 prompt_id。",
     "schema": {"type": "object", "properties": {}, "required": []}, "fn": _t_queue_list},
    {"name": "queue_cancel", "desc": "中断当前正在执行的任务。",
     "schema": {"type": "object", "properties": {}, "required": []}, "fn": _t_queue_cancel},
    {"name": "list_models", "desc": "列出实例已装的模型（按分类计数与样例）。",
     "schema": {"type": "object", "properties": {}, "required": []}, "fn": _t_models_list},
    {"name": "search_models", "desc": "在 HuggingFace（hf-mirror 镜像）搜索模型仓库。",
     "schema": {"type": "object", "properties": {
         "keyword": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["keyword"]},
     "fn": _t_models_search},
    {"name": "list_repo_files", "desc": "列出某模型仓库里的权重文件（配合 download_model）。",
     "schema": {"type": "object", "properties": {
         "repo_id": {"type": "string", "description": "如 Comfy-Org/Qwen-Image-2.1"}},
         "required": ["repo_id"]}, "fn": _t_repo_files},
    {"name": "download_model", "desc": "下载模型文件到实例模型目录（大文件走下载中心后台队列）。",
     "schema": {"type": "object", "properties": {
         "repo_id": {"type": "string"}, "file_path": {"type": "string"},
         "category": {"type": "string", "description": "checkpoints/loras/vae 等，缺省自动猜测"}},
         "required": ["repo_id", "file_path"]}, "fn": _t_model_download},
    {"name": "list_workflows", "desc": "列出实例工作流目录里的工作流文件。",
     "schema": {"type": "object", "properties": {}, "required": []}, "fn": _t_workflows_list},
    {"name": "check_workflow", "desc": "静态体检某工作流：节点注册、插件依赖、所需模型清单（不执行）。",
     "schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "list_workflows 里的文件名"}}, "required": ["name"]},
     "fn": _t_workflow_check},
    {"name": "run_workflow", "desc": "把工作流提交到实例执行（自动转 API 格式），返回 prompt_id。",
     "schema": {"type": "object", "properties": {
         "name": {"type": "string"}}, "required": ["name"]}, "fn": _t_workflow_run},
    {"name": "inspect_workflow", "desc": "读取工作流结构：每个节点的 key、类型、widgets 当前值。"
                                        "改提示词/尺寸前先用它确认改哪个节点。",
     "schema": {"type": "object", "properties": {
         "name": {"type": "string"}}, "required": ["name"]}, "fn": _t_inspect_workflow},
    {"name": "patch_workflow", "desc": "修改工作流副本（不覆盖原文件），返回新文件名 _ai.json。"
                                      "ops 示例：[{\"node\":\"34\",\"index\":0,\"value\":\"新提示词\"}]，"
                                      "也可用 field 指定字段名（如 width/height/seed/steps）。",
     "schema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "ops": {"type": "array", "items": {"type": "object",
                                            "properties": {"node": {"type": "string"},
                                                           "index": {"type": "integer"},
                                                           "field": {"type": "string"},
                                                           "value": {}},
                                            "required": ["node", "value"]}}},
         "required": ["name", "ops"]}, "fn": _t_patch_workflow},
    {"name": "list_templates",
     "desc": "按需求检索**本地工作流模板**（含用途标签与可改参数）。做新工作流时先调它，"
             "拿到候选后交给 apply_template；不要自己编节点结构（小模型编出来必然是坏的）。",
     "schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "用户要的效果/用途，如「三视图 竖幅」「面部修复」"},
         "limit": {"type": "integer", "description": "候选数量，默认 5"}},
         "required": []}, "fn": _t_list_templates},
    {"name": "apply_template",
     "desc": "用模板生成工作流副本（_tpl.json）：只改参数值，绝不增删节点/改连线，"
             "结构被改动会自动中止。edits 的键用 role：prompt/negative/width/height/seed/steps/cfg。",
     "schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "list_templates 返回的模板名（含 .json）"},
         "edits": {"type": "object", "description": "如 {\"prompt\":\"银发少女\",\"width\":832,\"height\":1216}"}},
         "required": ["name", "edits"]}, "fn": _t_apply_template},
    {"name": "import_workflow", "desc": "把用户给的 JSON 保存成工作流（自动识别 UI 格式/API 格式）。",
     "schema": {"type": "object", "properties": {
         "json_text": {"type": "string"}, "name": {"type": "string"}},
         "required": ["json_text"]}, "fn": _t_import_workflow},
    {"name": "web_search",
     "desc": "联网搜索（Bing/DDG，有 Key 时走 Tavily）。用于查模型显存要求、节点插件名、"
             "ComfyUI 新版本变化等自己不确定的事实——不要凭记忆编。返回标题+链接+摘要。",
     "schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "搜索词，中文英文都行"},
         "limit": {"type": "integer", "description": "结果条数，默认 6"}},
         "required": ["query"]}, "fn": _t_web_search},
    {"name": "wait_job", "desc": "等任务跑完并返回出图绝对路径（内部轮询，别自己反复 get_job）。",
     "schema": {"type": "object", "properties": {
         "prompt_id": {"type": "string"}, "timeout_s": {"type": "integer"}},
         "required": ["prompt_id"]}, "fn": _t_wait_job},
    {"name": "get_job", "desc": "按 prompt_id 查任务状态：排队/执行中/完成/报错，完成时给出成图绝对路径。",
     "schema": {"type": "object", "properties": {
         "prompt_id": {"type": "string"}}, "required": ["prompt_id"]}, "fn": _t_job_status},
    {"name": "recall_history",
     "desc": "在**已被压缩的早期对话**里按关键词回查原文片段。摘要提到某事但细节不够时用它；"
             "会话没压缩过会返回失败（那就说明后面的历史都在上下文里）。",
     "schema": {"type": "object", "properties": {
         "keyword": {"type": "string",
                     "description": "关键词，越短命中率越高（模型名/文件名/报错词）"},
         "limit": {"type": "integer", "description": "最多返回几条，默认 6"}},
         "required": ["keyword"]}, "fn": _t_recall_history},
    {"name": "ask_user", "desc": "需要用户在 2~4 个明确选项里做选择或确认时使用"
                                "（如「要不要启动实例」「用哪个工作流」）；选项会渲染成可点按钮，"
                                "用户点了自动作为回复。开放性问题不要用它，直接文字提问。",
     "schema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "向用户提的问题（一句话，正文里正常写出来）"},
         "options": {"type": "array", "items": {"type": "string"},
                     "description": "2~4 个选项，每项 ≤12 字，用户点击即作为回复发出"}},
         "required": ["options"]}, "fn": _t_ask_user},
]


def build_ctx(cfg: dict, inst: dict, mainwin=None, log=print,
              profile=None, chat_id: str = "", cancel=None) -> ToolCtx:
    return ToolCtx(cfg, inst, mainwin=mainwin, log=log, profile=profile,
                   chat_id=chat_id, cancel=cancel)


def _cancelled(ctx) -> bool:
    """用户是否已按「停止」（长循环里每轮都该查一次）"""
    ev = getattr(ctx, "cancel", None)
    try:
        return bool(ev is not None and ev.is_set())
    except Exception:
        return False


def schemas(only=None) -> list:
    """OpenAI function-calling 格式的 tools 参数

    only：允许的业务工具名集合（None = 不限制）。
    按会话勾选的技能收窄工具表（2026-11-14）：19 个工具的 schema 约 2800 token，
    每轮都得带；收窄后既省这些固定开销，也让模型少在不相干的工具里选错。
    元能力（get_health / ask_user / recall_history）永远保留。
    """
    pool = TOOLS
    if only is not None:
        keep = set(only) | set(ai_skills.BASE_TOOLS)
        pool = [t for t in TOOLS if t["name"] in keep]
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["desc"], "parameters": t["schema"]}}
        for t in pool]


def _cache_key(name: str, args: dict) -> str:
    try:
        return name + "\x00" + json.dumps(args or {}, sort_keys=True,
                                          ensure_ascii=False)
    except Exception:
        return ""


def execute(name: str, args: dict, ctx: ToolCtx) -> dict:
    """执行工具。只读工具同参数 30 秒内复用结果；任何写操作清空缓存。

    为什么要复用：模型常有「忘了刚查过」的行为，同一轮问答里把 get_health
    调两遍。第二次真打接口既慢又刷日志，还可能因为两次时间点不同给出矛盾的
    数字（队列深度变了），反而干扰判断。
    """
    key = ""
    if name in READONLY_TOOLS and isinstance(ctx, ToolCtx):
        key = _cache_key(name, args)
        if key:
            got = ctx._cache.get(key)
            if got and (time.time() - got[0]) < CACHE_TTL_S:
                ctx.cache_hits += 1
                res = copy.deepcopy(got[1])
                if isinstance(res, dict):
                    res["_cached"] = True
                return res
    result = _execute_uncached(name, args, ctx)
    if isinstance(ctx, ToolCtx):
        if name in READONLY_TOOLS:
            if key and isinstance(result, dict) and result.get("ok"):
                ctx._cache[key] = (time.time(), copy.deepcopy(result))
        elif name != "ask_user":
            # 写过东西了（启动/停止/下载/跑工作流/改工作流…）→ 之前读的全可能过期
            ctx._cache.clear()
    return result


def _execute_uncached(name: str, args: dict, ctx: ToolCtx) -> dict:
    for t in TOOLS:
        if t["name"] == name:
            try:
                return t["fn"](args or {}, ctx)
            except InstanceDown as e:
                return {"ok": False, "code": "instance_down",
                        "error": f"实例未运行：端口 {e.port} 无监听（连接被拒绝）",
                        "hint": "可调用 control_instance(action='start') 自行启动实例，"
                                "等返回 ready 后继续原任务，无需用户手动操作"}
            except (ValueError, FileNotFoundError) as e:
                # 参数/路径类错误：直接给模型看人话，别糊一层 "ValueError:"
                return {"ok": False, "error": str(e)}
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": f"未知工具：{name}"}


def current_instance(cfg: dict) -> dict:
    """取当前实例：last_instance_id → 第一个"""
    insts = cfg.get("instances") or []
    if not insts:
        return {}
    last = (cfg.get("last_instance_id")
            or (cfg.get("settings") or {}).get("last_instance_id"))
    for i in insts:
        if i.get("id") == last:
            return i
    return insts[0]
