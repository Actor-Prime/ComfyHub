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


def model_stats(comfyui_path: str) -> list:
    """统计 models 下各类模型数量与体积（仅模型格式文件）
    返回 [(类别, 文件数, 体积GB), ...]
    """
    models_dir = os.path.join(comfyui_path, "models")
    result = []
    if not os.path.isdir(models_dir):
        return result
    for cat in sorted(os.listdir(models_dir)):
        p = os.path.join(models_dir, cat)
        if not os.path.isdir(p):
            continue
        count, total = 0, 0
        for root, _dirs, files in os.walk(p):
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
    return result


def open_in_explorer(path: str):
    if os.path.isdir(path):
        os.startfile(path)
