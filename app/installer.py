# -*- coding: utf-8 -*-
"""安装计划引擎（对标管理大师 workflow.requirements 机制，但完全本地化）

步骤类型与管理大师兼容：
  git_clone / python_package / python_cmd / model_url / cmd
来源：
  1) 工作流扫描结果自动生成（常见插件内置映射表）
  2) 手动导入 JSON 步骤清单（可兼容管理大师导出格式）
执行流程：快照 → 逐步执行 → 完成快照（失败即停，可回滚）
"""
import json
import os

from app.bootstrap import find_git, download_multi, PYPI_MIRRORS
from app.tools import Rescue
from app import instance as inst_lib
from app.winsub import run

# ---------------- 常见插件映射表：节点名 -> GitHub 仓库 ----------------
KNOWN_PLUGIN_REPOS = {
    "UnetLoaderGGUF": "https://github.com/city96/ComfyUI-GGUF",
    "UnetLoaderGGUFAdvanced": "https://github.com/city96/ComfyUI-GGUF",
    "CLIPLoaderGGUF": "https://github.com/city96/ComfyUI-GGUF",
    "DualCLIPLoaderGGUF": "https://github.com/city96/ComfyUI-GGUF",
    "VHS_VideoCombine": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
    "VHS_LoadVideo": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
    "ImageResizeKJv2": "https://github.com/kijai/ComfyUI-KJNodes",
    "GetImageSizeAndCount": "https://github.com/kijai/ComfyUI-KJNodes",
    "EmptyLatentImagePresetsNoSD": "https://github.com/kijai/ComfyUI-KJNodes",
    "Power Lora Loader (rgthree)": "https://github.com/rgthree/rgthree-comfy",
    "Reroute (rgthree)": "https://github.com/rgthree/rgthree-comfy",
    "Fast Groups Bypasser (rgthree)": "https://github.com/rgthree/rgthree-comfy",
    "Any Switch (rgthree)": "https://github.com/rgthree/rgthree-comfy",
    "Seed (rgthree)": "https://github.com/rgthree/rgthree-comfy",
    "Context Switch (rgthree)": "https://github.com/rgthree/rgthree-comfy",
    "ImpactWildcardProcessor": "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
    "FaceDetailer": "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
    "UltralyticsDetectorProvider": "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
    "IPAdapterAdvanced": "https://github.com/cubiq/ComfyUI_IPAdapter_plus",
    "IPAdapterModelLoader": "https://github.com/cubiq/ComfyUI_IPAdapter_plus",
    "InstantIDModelLoader": "https://github.com/cubiq/ComfyUI_InstantID",
    "ApplyInstantID": "https://github.com/cubiq/ComfyUI_InstantID",
    "ControlNetApplySEGS": "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
    "WanVideoBlockList": "https://github.com/kijai/ComfyUI-WanVideoWrapper",
    "WanVideoModelLoader": "https://github.com/kijai/ComfyUI-WanVideoWrapper",
    "SUPIR_upscale": "https://github.com/kijai/ComfyUI-SUPIR",
    "Florence2ModelLoader": "https://github.com/kijai/ComfyUI-Florence2",
    "CR Text Concatenate": "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes",
    "CR Aspect Ratio": "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes",
    "easy poseNode": "https://github.com/yolain/ComfyUI-Easy-Use",
    "easy applyLora": "https://github.com/yolain/ComfyUI-Easy-Use",
    "PulidFluxModelLoader": "https://github.com/balazik/ComfyUI-PuLID-Flux",
    "ApplyPulidFlux": "https://github.com/balazik/ComfyUI-PuLID-Flux",
    "LayerUtility: ImageHub": "https://github.com/chflame163/ComfyUI_LayerStyle",
    "LayerMask: MaskEdgeUltra": "https://github.com/chflame163/ComfyUI_LayerStyle",
    "TeaCache": "https://github.com/welltop-cn/ComfyUI-TeaCache",
    "PreviewDownload": "https://github.com/welltop-cn/ComfyUI-TeaCache",
    "SimpleCalculatorKJ": "https://github.com/kijai/ComfyUI-KJNodes",
    "ComfyNumberConvert": "https://github.com/kijai/ComfyUI-KJNodes",
    "INTConstant": "https://github.com/kijai/ComfyUI-KJNodes",
    "ResizeImageMaskNode": "https://github.com/kijai/ComfyUI-KJNodes",
    "GetImageSize": "https://github.com/kijai/ComfyUI-KJNodes",
    # LTX 系列节点（LTXVideo 官方插件）
    "LTXVPreprocess": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "EmptyLTXVLatentVideo": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVImgToVideoInplace": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVConditioning": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVScheduler": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "ManualSigmas": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVLatentUpsampler": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVConcatAVLatent": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVSeparateAVLatent": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVAudioVAEDecode": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTXVEmptyLatentAudio": "https://github.com/Lightricks/ComfyUI-LTXVideo",
    "LTX2SamplingPreviewOverride": "https://github.com/Lightricks/ComfyUI-LTXVideo",
}


# ---------------- 步骤生成 ----------------
def plan_from_scan(scan_result: dict, log=print, resolve_online: bool = True,
                   ai_settings: dict = None) -> dict:
    """从 workflow_scan.analyze_workflow 的结果生成安装计划。
    返回 {steps: [...], upgrades: [...], unresolvable: [...], models: [...]}

    节点解析链（2026-10-31 增强，未命中逐级下钻）：
      1) KNOWN_PLUGIN_REPOS      内置常见插件映射 → git_clone 步骤
      2) core_nodes 知识表        新内核核心节点 → upgrades（升级内核，别找插件）
      3) plugin_store 映射        ComfyUI-Manager extension-node-map（6000+ 插件）
      4) AI 接入（可选，需配置）   结果标「待验证」
      5) 全落空 → unresolvable   （UI 给搜索链接）
    模型解析链：model_resolve 已知表/repack 仓库树 → 可下载直链
    （hf-mirror + ModelScope 双源），再落空走 AI，最后才「需手动下载」。
    """
    from app import core_nodes, model_resolve
    steps, unresolvable, upgrades = [], [], []
    # AI 兜底预算（2026-11-01）：AI 是同步网络请求，批量扫描时逐项问会把界面拖死
    # （实测开启 AI 后 UI 回归矩阵从 4.5 分钟涨到 11 分钟超时）。限制条数 + 超时。
    ai_budget = [6]

    def _ai_take() -> bool:
        if ai_budget[0] <= 0:
            return False
        ai_budget[0] -= 1
        return True
    for node in scan_result.get("missing", {}).get("__unknown__", []):
        repo = KNOWN_PLUGIN_REPOS.get(node)
        if repo:
            steps.append({
                "type": "git_clone",
                "content": repo,
                "reason": f"节点 {node}",
            })
            continue
        minv = core_nodes.known_core_min_version(node)
        if minv:
            kind, detail = core_nodes.classify_node(
                node, scan_result.get("comfyui_path", ""))
            if kind == "local_core":
                continue   # 版本已达标——核心节点自带，无需任何安装
            upgrades.append({"node": node, "min_version": minv,
                             "kernel_ver": scan_result.get("kernel_ver", "")})
            continue
        if resolve_online:
            try:
                from app import plugin_store
                sugg = plugin_store.suggest_for_classes([node], log=log)
                if sugg and sugg[0].get("urls"):
                    steps.append({
                        "type": "git_clone",
                        "content": sugg[0]["urls"][0],
                        "reason": f"节点 {node}",
                    })
                    continue
            except Exception as e:
                log(f"[计划] 插件映射查询失败: {e}")
        if ai_settings and _ai_take():
            from app import ai_assist
            if ai_assist.enabled(ai_settings):
                guess = ai_assist.ask_node_origin(node, ai_settings, log=log)
                if guess:
                    steps.append({
                        "type": "git_clone",
                        "content": guess,
                        "reason": f"节点 {node}（🤖 AI推断·待验证）",
                        "ai": True,
                    })
                    continue
        unresolvable.append(node)

    models = []
    for kind, files in scan_result.get("models", {}).items():
        for f in files:
            hit = model_resolve.resolve_file(f, online=resolve_online, log=log)
            if not hit and ai_settings and _ai_take():
                from app import ai_assist
                if ai_assist.enabled(ai_settings):
                    g = ai_assist.ask_model_source(f, ai_settings, log=log)
                    if g:
                        hit = {"repo": g["repo"], "path": g["path"],
                               "subdir": g["path"].split("/")[0]
                               if "/" in g["path"] else "checkpoints",
                               "ai": True}
            if hit:
                models.append({
                    "category": kind, "file": f,
                    "status": f"✅ 可下载（{hit['repo']}）"
                              + ("·🤖AI待验证" if hit.get("ai") else ""),
                    "downloadable": True, "source": hit,
                })
            else:
                models.append({
                    "category": kind, "file": f,
                    "status": "需手动下载（无直链，可搜 HF/ModelScope）",
                    "downloadable": False, "source": None,
                })
    if ai_settings and ai_budget[0] <= 0:
        log("[计划] AI 调用已达本次上限（6 项）；其余未识别项留待手动处理，"
            "重新扫描可再获得额度")
    return {"steps": steps, "upgrades": upgrades,
            "unresolvable": unresolvable, "models": models}


def build_solve_queue(plan: dict, include_models: bool = True,
                      kernel_target: str = "stable", log=print) -> list:
    """把「升级内核 + 装插件 + 下模型」合成一条可执行队列（2026-11-01）。

    plan = plan_from_scan(...) 的返回值。顺序：内核 → 插件 → 模型。
    内核必须先升：插件依赖要按新内核版本来装；模型是纯文件搬运，放最后。
    同一批 upgrades 合并成一步（取最高 min_version 需求 → 直接上最新稳定版）。
    """
    steps = []
    ups = plan.get("upgrades") or []
    if ups:
        from app import core_nodes
        try:
            need = max((u.get("min_version") or "" for u in ups),
                       key=core_nodes.parse_ver)
        except Exception:
            need = ""
        nodes = ", ".join(u.get("node", "?") for u in ups)
        steps.append({
            "type": "upgrade_kernel",
            "content": kernel_target or "stable",
            "reason": f"节点 {nodes} 需内核 ≥ {need or '?'}",
        })
    steps.extend(plan.get("steps") or [])
    if include_models:
        for m in plan.get("models") or []:
            if not (m.get("downloadable") and m.get("source")):
                continue        # 无直链的模型本来就下不了，别塞进队列里当失败项
            s = m["source"]
            steps.append({
                "type": "model_url",
                "content": {"repo": s["repo"], "path": s["path"],
                            "subdir": s.get("subdir") or "checkpoints"},
                "reason": f"模型 {m['file']}"
                          + ("（🤖AI待验证）" if s.get("ai") else ""),
            })
    if steps:
        kinds = "、".join(f"{k}×{n}" for k, n in _count_kinds(steps).items())
        log(f"[补全] 队列 {len(steps)} 步：{kinds}")
    else:
        log("[补全] 没有需要补的项")
    return steps


def _count_kinds(steps: list) -> dict:
    out = {}
    for s in steps:
        out[s.get("type", "?")] = out.get(s.get("type", "?"), 0) + 1
    return out


def import_plan(path: str) -> list:
    """导入步骤清单 JSON（兼容管理大师 requirements 结构）"""
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("requirements", "steps"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    raise ValueError("不是有效的步骤清单（需要 list 或含 requirements/steps 键）")


# ---------------- 执行器 ----------------
class PlanExecutor:
    """逐步执行安装计划；前后自动救援快照"""

    def __init__(self, inst: dict):
        self.inst = inst
        self.rescue = Rescue(inst["comfyui_path"])
        self.git = find_git()
        self.stopped = False

    def run(self, steps: list, log=print, on_step_done=None) -> dict:
        """按序执行。返回 {done: n, total: n, failed_step: idx|None, snapshot: name}"""
        snap = self.rescue.snapshot(log=log)   # 安装前自动快照（对标管理大师）
        done, failed = 0, None
        for i, step in enumerate(steps):
            if self.stopped:
                log(f"[计划] 已被用户停止（第 {i + 1} 步未执行）")
                break
            stype = step.get("type", "")
            content = step.get("content", "")
            log(f"[计划] 步骤 {i + 1}/{len(steps)}: {stype} → {content[:80]}")
            try:
                fn = {
                    "git_clone": self._git_clone,
                    "python_package": self._pip_install,
                    "model_url": self._model_url,
                    "cmd": self._run_cmd,
                    "python_cmd": self._run_py_cmd,
                    "upgrade_kernel": self._upgrade_kernel,
                }.get(stype)
                if fn is None:
                    raise ValueError(f"未知步骤类型: {stype}")
                fn(content, log)
                done += 1
                if on_step_done:
                    on_step_done(i, True)
            except Exception as e:
                log(f"[计划] ❌ 步骤失败: {e}")
                failed = i
                if on_step_done:
                    on_step_done(i, False)
                break
        log(f"[计划] 执行结束 {done}/{len(steps)}"
            + (f"，失败于第 {failed + 1} 步（可用救援快照 {snap} 回滚）" if failed is not None else ""))
        return {"done": done, "total": len(steps), "failed_step": failed, "snapshot": snap}

    # ---------- 各步骤实现 ----------
    def _git_clone(self, url: str, log):
        if not self.git:
            raise RuntimeError("未找到 Git（设置里先装便携 MinGit）")
        url = url.strip().rstrip("/")
        name = url.split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        dest = os.path.join(self.inst["comfyui_path"], "custom_nodes", name)
        if os.path.exists(dest):
            log(f"  插件已存在，跳过: {name}")
            return
        # 代理转换（对标管理大师 getProxyGitCloneUrl）
        proxy = (self.inst.get("git_proxy") or "").strip()
        real = proxy + url if proxy and url.startswith("https://github.com") else url
        r = run([self.git, "clone", "--depth", "1", "--progress",
                            real, dest], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "")[-200:])
        log(f"  插件克隆完成: {name}")
        # 顺手装插件依赖（对标管理大师）
        req = os.path.join(dest, "requirements.txt")
        if os.path.isfile(req):
            mirror = PYPI_MIRRORS["阿里云"]
            import subprocess
            r2 = run(
                [self.inst["python_path"], "-m", "pip", "install",
                 "-r", req, "-i", mirror, "--no-warn-script-location"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=3600)
            log("  插件依赖安装" + ("完成" if r2.returncode == 0 else "失败（可手动补装）"))

    def _pip_install(self, content, log):
        # content: 包名字符串或 "pkg1 pkg2"
        pkgs = content.split() if isinstance(content, str) else list(content)
        mirror = PYPI_MIRRORS["阿里云"]
        r = run(
            [self.inst["python_path"], "-m", "pip", "install", *pkgs,
             "-i", mirror, "--no-warn-script-location"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=7200)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "")[-200:])
        log(f"  依赖安装完成: {' '.join(pkgs)}")

    def _upgrade_kernel(self, content, log):
        """升级/切换 ComfyUI 内核并同步依赖（2026-11-01 AI 补全队列用）

        content: ""/"stable" = 更新到最新稳定版；其他值 = 切到该 tag
        ⚠ 调用前实例必须已停止（git checkout 会被运行中的进程锁文件挡住）
        """
        from app import bootstrap
        from app.tools import KernelManager
        km = KernelManager(self.inst["comfyui_path"])
        target = (content or "").strip() if isinstance(content, str) else "stable"
        if target in ("", "stable", "latest"):
            log("  更新至最新稳定版…")
            ok, msg = km.update_stable()
        else:
            log(f"  切换到内核 {target} …")
            ok, msg = km.switch_to(target)
        if not ok:
            raise RuntimeError((msg or "内核更新失败")[-200:])
        log(f"  内核已更新: {str(msg)[:160]}")
        log("  同步该版本要求的依赖…")
        bootstrap.install_comfyui_requirements(
            self.inst["python_path"], km.path, log=log)
        log("  内核依赖已同步")

    def _model_url(self, content, log):
        # content: URL 或 {url, subdir, filename} 或 {repo, path, subdir}
        # （repo/path 形态走 model_resolve 断点续传+「有进展就重置」重试，
        #   对付 hf-mirror 大文件周期性掐 TLS）
        if isinstance(content, dict) and content.get("repo"):
            from app import model_resolve
            fname = os.path.basename(content["path"])
            dest = os.path.join(self.inst["comfyui_path"], "models",
                                content.get("subdir") or "checkpoints")
            log(f"  下载 {fname} → {dest}（断点续传）")
            model_resolve.download_with_retry(
                content["repo"], content["path"], dest, log=log)
            log("  模型下载完成")
            return
        if isinstance(content, dict):
            url = content.get("url", "")
            subdir = content.get("subdir", "checkpoints")
            filename = content.get("filename", "")
        else:
            url, subdir, filename = content, "checkpoints", ""
        from app.tools import ModelDownloader
        d = ModelDownloader(self.inst["comfyui_path"])
        if not d.download(url, subdir, filename, log=log):
            raise RuntimeError("模型下载失败")

    def _run_cmd(self, content, log):
        r = run(content, shell=True, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=3600, cwd=self.inst["comfyui_path"])
        log("  cmd 输出: " + ((r.stdout or r.stderr or "")[-200:]))
        if r.returncode != 0:
            raise RuntimeError(f"cmd 退出码 {r.returncode}")

    def _run_py_cmd(self, content, log):
        r = run([self.inst["python_path"], "-c", content],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=3600,
                           cwd=self.inst["comfyui_path"])
        log("  输出: " + ((r.stdout or "")[-200:]))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "")[-200:])

    def stop(self):
        self.stopped = True
