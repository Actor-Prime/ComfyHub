# -*- coding: utf-8 -*-
"""实例信息：版本读取、插件扫描、模型统计"""
import ast
import os
import subprocess

from app.winsub import run


def read_comfyui_version(comfyui_path: str) -> str:
    """从 comfyui_version.py 读取内核版本"""
    f = os.path.join(comfyui_path, "comfyui_version.py")
    if not os.path.isfile(f):
        return "未知（无 comfyui_version.py）"
    try:
        tree = ast.parse(open(f, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if getattr(t, "id", "") == "__version__":
                        return ast.literal_eval(node.value)
    except Exception:
        pass
    return "解析失败"


def is_valid_comfyui(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "main.py"))


def list_custom_nodes(comfyui_path: str) -> list:
    """扫描已安装的自定义节点插件
    返回 [(插件名, 是否git仓库, 启用状态), ...]
    """
    nodes_dir = os.path.join(comfyui_path, "custom_nodes")
    result = []
    if not os.path.isdir(nodes_dir):
        return result
    for name in sorted(os.listdir(nodes_dir)):
        p = os.path.join(nodes_dir, name)
        if not os.path.isdir(p) or name.startswith("__"):
            continue
        is_git = os.path.isdir(os.path.join(p, ".git"))
        disabled = name.endswith(".disabled")
        result.append((name, is_git, not disabled))
    return result


def git_pull_dir(repo_path: str) -> tuple:
    """对单个 git 仓库执行 pull，返回 (是否成功, 输出摘要)"""
    git = _find_git()
    if not git:
        return False, "未找到 git.exe"
    try:
        r = run(
            [git, "-C", repo_path, "pull", "--ff-only"],
            capture_output=True, timeout=120, text=True, encoding="utf-8", errors="replace",
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        # 常见输出精简
        for kw in ("Already up to date", "Already up-to-date"):
            if kw in out:
                return True, "已是最新"
        return r.returncode == 0, out[:300]
    except Exception as e:
        return False, str(e)


def git_clone(url: str, dest: str) -> tuple:
    """克隆插件仓库到 custom_nodes"""
    git = _find_git()
    if not git:
        return False, "未找到 git.exe，请安装 Git 或在设置中指定路径"
    try:
        r = run(
            [git, "clone", "--depth", "1", url, dest],
            capture_output=True, timeout=600, text=True, encoding="utf-8", errors="replace",
        )
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()[:300]
    except Exception as e:
        return False, str(e)


def git_repo_info(repo_path: str) -> dict:
    """读取仓库当前分支 / 短 commit / 最近提交时间（纯本地，不联网）"""
    info = {"branch": "", "commit": "", "date": ""}
    git = _find_git()
    if not git:
        return info
    try:
        r = run([git, "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, timeout=15, text=True,
                encoding="utf-8", errors="replace")
        info["branch"] = (r.stdout or "").strip() if r.returncode == 0 else ""
        r = run([git, "-C", repo_path, "log", "-1", "--format=%h|%cd",
                 "--date=format:%Y-%m-%d %H:%M"],
                capture_output=True, timeout=15, text=True,
                encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        if r.returncode == 0 and "|" in out:
            info["commit"], info["date"] = out.split("|", 1)
    except Exception:
        pass
    return info


def git_remote_refs(url: str) -> tuple:
    """ls-remote 列出远端分支和 tag（联网）
    返回 (refs: [(显示名, 短commit)], 错误信息)；显示名形如 branch:main / tag:v1.2"""
    git = _find_git()
    if not git:
        return [], "未找到 git.exe"
    try:
        r = run([git, "ls-remote", "--heads", "--tags", url],
                capture_output=True, timeout=60, text=True,
                encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return [], ((r.stderr or "").strip() or "ls-remote 失败")[:200]
        refs = []
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            sha, ref = parts
            if ref.startswith("refs/tags/"):
                # ^{} 是 annotated tag 的解引用条目，跳过避免重复
                t = ref[len("refs/tags/"):]
                if t.endswith("^{}"):
                    continue
                refs.append(("tag:" + t, sha[:8]))
            elif ref.startswith("refs/heads/"):
                refs.append(("branch:" + ref[len("refs/heads/"):], sha[:8]))
        return refs, ""
    except Exception as e:
        return [], str(e)


def git_checkout_ref(repo_path: str, ref: str) -> tuple:
    """fetch 后切换到指定分支/tag。ref 形如 'main' / 'v1.2'"""
    git = _find_git()
    if not git:
        return False, "未找到 git.exe"
    try:
        r = run([git, "-C", repo_path, "fetch", "--tags", "origin"],
                capture_output=True, timeout=300, text=True,
                encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return False, "拉取远端失败: " + ((r.stderr or "").strip())[:200]
        r = run([git, "-C", repo_path, "checkout", ref],
                capture_output=True, timeout=60, text=True,
                encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, out[:200]
    except Exception as e:
        return False, str(e)


def _find_git() -> str | None:
    # 优先系统 Git，其次 ComfyHub 便携 MinGit（bootstrap 统一管理）
    try:
        from app.bootstrap import find_git
        return find_git()
    except Exception:
        pass
    for c in (r"C:\Program Files\Git\cmd\git.exe",
              r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if os.path.isfile(c):
            return c
    from shutil import which
    return which("git")


# 模型文件扩展名白名单（txt 占位符等非模型文件不计入统计）
MODEL_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft",
              ".bin", ".onnx", ".engine", ".trt", ".llamafile"}


def is_model_file(fn: str) -> bool:
    return os.path.splitext(fn)[1].lower() in MODEL_EXTS


def model_stats(comfyui_path: str, models_dir: str = "",
                source: str = "all", mount_root: str = "") -> list:
    """统计各类模型数量与体积（仅模型格式文件）

    2026-11-07：不再只扫 <comfyui_path>/models —— ComfyUI 的 extra_model_paths.yaml
    会把外部盘符的模型库挂进来（秋葉整合包常见），只扫内置目录的话用户在
    ComfyUI 里看得到、在启动器里看不到（用户报障）。
    2026-11-08：source 可限定 builtin（实例内置）/ extra（外部挂载）/ all；
    mount_root 是实例设置里「启用挂载模型目录」选的那个根（按类别自动映射）。
    返回 [(类别, 文件数, 体积GB), ...]
    """
    from app import model_paths
    result = []
    if not comfyui_path and not mount_root:
        return result
    for cat in model_paths.model_dirs(comfyui_path, models_dir, source, mount_root):
        count, total = 0, 0
        for d in model_paths.dirs_for(comfyui_path, cat, models_dir, source,
                                      mount_root):
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    if not is_model_file(fn):
                        continue
                    fp = os.path.join(root, fn)
                    try:
                        total += os.path.getsize(fp)
                        count += 1
                    except OSError:
                        pass
        if count:
            result.append((cat, count, total / 1024 ** 3))
    return sorted(result)


def open_in_explorer(path: str):
    """打开目录；传入文件时在资源管理器里选中它（2026-11-07 双击模型行用）"""
    if os.path.isdir(path):
        os.startfile(path)
    elif os.path.isfile(path):
        try:
            # /select, 后面必须紧跟路径；explorer 对引号敏感，交给列表参数处理
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            try:
                os.startfile(os.path.dirname(path))
            except Exception:
                pass
