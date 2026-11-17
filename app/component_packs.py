# -*- coding: utf-8 -*-
"""选装组件包：把「一整套跑得起来的东西」做成一条可下载清单（2026-11-15）

为什么需要它
    官方模板引用的模型动辄 30GB，散落在 ModelScope 的几个仓库里；用户要自己
    拼清单、找直链、按目录归类，任何一步错都会跑出「模型不存在」。组件包把这
    些固化下来：一条清单 → 一键下载 → 自动落到 models/<类别>/ → 顺手放一份
    起步工作流。

设计约束
    · 下载一律走 bootstrap.download_multi：断点续传 + 多源回退 + 多线程分段，
      不在本模块里另写一套 HTTP
    · 判断「已装」要看全（含 extra_model_paths 外挂库）：用户把模型放 D 盘时
      不能重复下 30GB
    · 只认文件大小，不认 sha256：这些权重 12~15GB，算 hash 要几十秒且上游
      不发布校验值；大小匹配 + 下载完整性校验已经够用
"""
import json
import os

from app import bootstrap
from app import mirror

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MS = "https://modelscope.cn/models"


def _ms_urls(repo: str, path: str) -> list:
    """同一个文件的两个源：ModelScope 优先（实测 8 线程 27~79MB/s），
    HF 端点在后面兜底（走 mirror.endpoint()，用户可在设置里换成自建反代；
    证书过期时 bootstrap 已有宽松 SSL 处理）"""
    return [
        "%s/%s/resolve/master/%s" % (_MS, repo, path),
        mirror.hf_url(repo, path),
    ]


# ---------------------------------------------------------------- 组件定义
PACKS = [
    {
        "id": "h3_w4a8_lowvram",
        "name": "MiniMax H3 · W4A8 低显存文生视频",
        "desc": "12GB 显存可跑，1344×768 一条约 150 秒（带音轨）。"
                "4bit W4A8 量化扩散模型 + NVFP4 文本编码器 + 双 VAE + 4 步加速 LoRA。",
        "tags": ["视频", "MiniMax H3", "低显存", "W4A8"],
        "requires": "ComfyUI 0.3.60+（w4a8 需要 comfy-kitchen 后端，内核已内置）",
        "note": "官方模板 video_minimax_h3_t2v 用的是 int8_convrot 版本，"
                "装本组件后请用随包的「H3-W4A8-低显存文生视频」工作流",
        "workflow": "H3-W4A8-低显存文生视频.json",
        "items": [
            {
                "file": "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors",
                "dir": "diffusion_models",
                "size": 12540858008,
                "role": "扩散模型（FL2VA：文生视频 / 首尾帧）",
                "urls": _ms_urls("Kijai/MiniMax-H3-experimental",
                                 "minimax_h3_fl2va_pruned_w4a8_mixed.safetensors"),
            },
            {
                "file": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "dir": "text_encoders",
                "size": 15687142551,
                "role": "文本编码器（Qwen3-VL-32B，NVFP4）",
                "urls": _ms_urls("Comfy-Org/MiniMax-H3",
                                 "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
            },
            {
                "file": "minimax_h3_lightvae_lite_vae_int8_convrot.safetensors",
                "dir": "vae",
                "size": 1036943392,
                "role": "视频 VAE（LightVAE int8，省显存）",
                "urls": _ms_urls("Kijai/MiniMax-H3-experimental",
                                 "minimax_h3_lightvae_lite_vae_int8_convrot.safetensors"),
            },
            {
                "file": "minimax_h3_audio_vae_fp32.safetensors",
                "dir": "vae",
                "size": 605254808,
                "role": "音频 VAE（H3 是音视频同出）",
                "urls": _ms_urls("Comfy-Org/MiniMax-H3",
                                 "vae/minimax_h3_audio_vae_fp32.safetensors"),
            },
            {
                "file": "minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors",
                "dir": "loras",
                "size": 366647000,
                "role": "4 步加速 LoRA（DMAD）",
                "urls": _ms_urls("Kijai/MiniMax-H3-experimental",
                                 "loras/minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors"),
            },
        ],
    },
]

_BY_ID = {p["id"]: p for p in PACKS}

# 大小判定容差：上游偶尔重打包（差几 MB），差太多就算没装
_SIZE_TOL = 0.02


def list_packs() -> list:
    return PACKS


def get_pack(pid: str) -> dict:
    return _BY_ID.get(str(pid or "").strip(), {})


def pack_bytes(pack: dict) -> int:
    return sum(int(i.get("size") or 0) for i in pack.get("items") or [])


def fmt_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024
    return "%.1f GB" % n


# ---------------------------------------------------------------- 路径与状态
def models_root(comfyui_path: str, models_dir: str = "") -> str:
    from app.model_paths import model_root
    return model_root(comfyui_path, models_dir or "")


def item_path(comfyui_path: str, models_dir: str, item: dict) -> str:
    """下载目标：<模型根>/<类别>/<文件名>"""
    root = models_root(comfyui_path, models_dir)
    return os.path.join(root, item["dir"], item["file"])


def _installed_index(comfyui_path: str, models_dir: str) -> dict:
    """{小写文件名: 绝对路径}（含外挂库），用于判断「其实已经装了」"""
    from app import workflow_lib
    idx = {}
    for low, dirs in (workflow_lib.installed_models(
            comfyui_path, models_dir or "") or {}).items():
        for d in dirs:
            p = os.path.join(d, low)
            if os.path.isfile(p):
                idx.setdefault(low, p)
                break
    return idx


def _size_ok(path: str, want: int) -> bool:
    try:
        got = os.path.getsize(path)
    except OSError:
        return False
    if want <= 0:
        return got > 0
    return abs(got - want) <= max(65536, int(want * _SIZE_TOL))


def state(pack_id: str, comfyui_path: str, models_dir: str = "") -> dict:
    """组件包当前状态：每项是否已就位、总共还需下载多少"""
    pack = get_pack(pack_id)
    if not pack:
        return {}
    idx = _installed_index(comfyui_path, models_dir)
    items, need = [], 0
    for it in pack["items"]:
        found = idx.get(it["file"].lower()) or ""
        done = bool(found) and _size_ok(found, it["size"])
        if not done:
            need += int(it["size"] or 0)
        items.append({
            "file": it["file"], "dir": it["dir"], "role": it.get("role", ""),
            "size": it["size"], "size_text": fmt_size(it["size"]),
            "done": done, "found_at": found,
            "dest": item_path(comfyui_path, models_dir, it),
        })
    ok = sum(1 for i in items if i["done"])
    return {"id": pack_id, "name": pack["name"], "items": items,
            "done": ok, "total": len(items), "ready": ok == len(items),
            "need_bytes": need, "need_text": fmt_size(need)}


def workflow_path(pack_id: str) -> str:
    pack = get_pack(pack_id)
    wf = pack.get("workflow") or ""
    if not wf:
        return ""
    p = os.path.join(_APP_DIR, "assets", "components", pack_id, wf)
    return p if os.path.isfile(p) else ""


def install_workflow(pack_id: str, workflows_dir: str) -> str:
    """把随包工作流放进实例的 workflows 目录（已存在不覆盖）"""
    src = workflow_path(pack_id)
    if not src or not workflows_dir:
        return ""
    os.makedirs(workflows_dir, exist_ok=True)
    dst = os.path.join(workflows_dir, os.path.basename(src))
    if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return dst
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)
    return dst


# ---------------------------------------------------------------- 安装
def install(pack_id: str, comfyui_path: str, models_dir: str = "", log=print,
            on_progress=None, cancel=None, only_missing: bool = True) -> dict:
    """下载组件包。返回 {ok, done, failed, skipped, files}

    cancel: threading.Event，置位后在「当前文件下完」时停下（不破坏 .part，
            下次接着续传）。故意不做段级中断——那会让 .part 变成稀疏空洞。
    """
    pack = get_pack(pack_id)
    if not pack:
        return {"ok": False, "error": "未知组件包：%s" % pack_id}
    st = state(pack_id, comfyui_path, models_dir)
    todo = [i for i in st["items"] if not (only_missing and i["done"])]
    skipped = st["done"] if only_missing else 0
    log("[组件] %s：%d 个文件，待下载 %s"
        % (pack["name"], len(st["items"]), fmt_size(st["need_bytes"])))

    done_files, failed = [], []
    for n, it in enumerate(todo, 1):
        if cancel is not None and cancel.is_set():
            log("[组件] 已取消，剩余文件下次继续")
            break
        src = next(x for x in pack["items"] if x["file"] == it["file"])
        dest = it["dest"]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        log("[组件 %d/%d] %s（%s）→ models/%s"
            % (n, len(todo), it["file"], it["size_text"], it["dir"]))
        if on_progress:
            on_progress({"file": it["file"], "index": n, "total": len(todo),
                         "done": len(done_files) + skipped, "all": st["total"]})
        ok = bootstrap.download_multi(src["urls"], dest, log=log,
                                      expected_sha=src.get("sha256"))
        if ok:
            done_files.append(it["file"])
        else:
            failed.append(it["file"])
            log("[组件] 失败：%s（进度已保留，重试会续传）" % it["file"])
    wf = install_workflow(pack_id, os.path.join(comfyui_path,
                                                "user", "default", "workflows"))
    if wf:
        log("[组件] 起步工作流已放入：%s" % wf)
    after = state(pack_id, comfyui_path, models_dir)
    return {"ok": not failed and after["ready"],
            "downloaded": done_files, "failed": failed, "skipped": skipped,
            "workflow": wf, "state": after}


def json_dump_state(pack_id: str, comfyui_path: str, models_dir: str = "") -> str:
    return json.dumps(state(pack_id, comfyui_path, models_dir),
                      ensure_ascii=False, indent=2)
