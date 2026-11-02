# -*- coding: utf-8 -*-
"""ComfyUI 启动 + 出图验收（验收标准出图验收（验收标准③④）：
启动真实实例（不报错不闪退）→ 用模型市场下载的 DreamShaper 出图 → 验证图内容没崩坏。

链路：Popen 实例 python main.py → 探活 / → /object_info 确认模型被识别 →
POST /prompt 最简 txt2img → 轮询 /history → /view 取图 → PIL 验证（尺寸/非纯色）→
日志扫 Traceback → 优雅停机。

用法：.venv_build/Scripts/python.exe tests/walk_generate_image.py
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "analysis", "walkthrough")
os.makedirs(OUT, exist_ok=True)

PY = r"F:\ComfyUI-aki-v3\python\python.exe"
TARGET = r"F:\ComfyUI-aki-v3\ComfyUI"
PORT = 8299
CKPT = "DreamShaper_2.52.safetensors"
LOG = os.path.join(OUT, "generate_e2e.log")
T0 = time.time()
FAILS = []


def log(msg):
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check(name, cond, detail=""):
    if not cond:
        FAILS.append(name)
    log(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    if os.path.exists(LOG):
        os.remove(LOG)
    log(f"启动实例：{TARGET} @ {PORT}（{PY}）")
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    raw_log = os.path.join(OUT, "comfy_run_raw.log")
    lf = open(raw_log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-s", "main.py", "--port", str(PORT), "--listen", "127.0.0.1"],
        cwd=TARGET, stdout=lf, stderr=subprocess.STDOUT,
        creationflags=flags, env=env)

    base = f"http://127.0.0.1:{PORT}"
    try:
        # ---- 1. 探活（不闪退：进程活着 + HTTP 应答）----
        up = False
        t0 = time.time()
        while time.time() - t0 < 240:
            if proc.poll() is not None:
                check("实例启动（进程存活）", False, f"提前退出 code={proc.returncode}")
                return
            try:
                with urllib.request.urlopen(base + "/", timeout=3) as r:
                    up = r.status == 200
                    break
            except Exception:
                time.sleep(2)
        check("实例启动（HTTP 探活）", up, f"{time.time()-t0:.0f}s 无响应")
        if not up:
            return

        # ---- 2. 模型被识别（市场下载的 checkpoint 在位）----
        info = http_json(base + "/object_info/CheckpointLoaderSimple")
        names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        check("DreamShaper 被 ComfyUI 识别", CKPT in names,
              f"共 {len(names)} 个模型")

        # ---- 3. 最简 txt2img 出图 ----
        prompt = {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": CKPT}},
            "2": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1],
                             "text": "a mountain landscape, sunny day, photorealistic"}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["1", 1], "text": "low quality, blurry"}},
            "4": {"class_type": "KSampler",
                  "inputs": {"model": ["1", 0], "positive": ["2", 0],
                             "negative": ["3", 0], "latent_image": ["5", 0],
                             "seed": 42, "steps": 8, "cfg": 7.0,
                             "sampler_name": "euler", "scheduler": "normal",
                             "denoise": 1.0}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 512, "height": 512, "batch_size": 1}},
            "6": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage",
                  "inputs": {"images": ["6", 0], "filename_prefix": "ComfyHubE2E"}},
        }
        resp = http_json(base + "/prompt", {"prompt": prompt})
        pid = resp.get("prompt_id")
        check("工作流提交（无节点错误）",
              bool(pid) and not resp.get("node_errors"),
              str(resp.get("node_errors"))[:200])

        # ---- 4. 轮询出图 ----
        img_meta = None
        t0 = time.time()
        while time.time() - t0 < 480:
            hist = http_json(base + f"/history/{pid}")
            entry = hist.get(pid)
            if entry and entry.get("outputs"):
                outs = entry["outputs"].get("7") or {}
                imgs = outs.get("images") or []
                if imgs:
                    img_meta = imgs[0]
                    break
            time.sleep(2)
        check("出图完成（history 有输出）", img_meta is not None)
        if not img_meta:
            return

        # ---- 5. 取图 + 内容没崩坏验证 ----
        q = (f"/view?filename={urllib.request.quote(img_meta['filename'])}"
             f"&subfolder={urllib.request.quote(img_meta.get('subfolder', ''))}"
             f"&type={img_meta.get('type', 'output')}")
        png_path = os.path.join(OUT, "e2e_output.png")
        with urllib.request.urlopen(base + q, timeout=60) as r:
            blob = r.read()
        with open(png_path, "wb") as f:
            f.write(blob)
        log(f"出图文件：{img_meta['filename']}（{len(blob) / 1048576:.1f}MB）→ {png_path}")

        ok_png = blob[:8] == b"\x89PNG\r\n\x1a\n"
        check("PNG 文件头有效", ok_png)
        # 头解析尺寸（不依赖 PIL）
        w, h = struct.unpack(">II", blob[16:24]) if ok_png else (0, 0)
        check("尺寸 512x512", (w, h) == (512, 512), f"{w}x{h}")
        # 像素内容检查：解压 IDAT 看是不是纯色崩坏（用 PIL，.venv_build 没有就跳过深度检查）
        try:
            from PIL import Image
            im = Image.open(png_path).convert("RGB")
            ex = im.getextrema()
            spread = [hi - lo for lo, hi in ex]
            check("像素非纯色（内容没崩坏）", min(spread) > 8, f"extrema={ex}")
        except ImportError:
            log("（本环境无 PIL，跳过像素深度检查）")

        # ---- 6. 日志无致命 Traceback（不报错）----
        # 白名单噪声：ComfyUI-Manager 启动拉 raw.githubusercontent.com 被墙的
        # 连接栈、可选后端（triton/comfy_kitchen）探测——都不影响运行，不算报错
        lf.flush()
        with open(raw_log, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        blocks, cur = [], []
        for ln in raw.splitlines():
            if ln.startswith("Traceback"):
                if cur:
                    blocks.append(cur)
                cur = [ln]
            elif cur:
                if ln.startswith((" ", "\t", "^")) or ln.strip() == "":
                    cur.append(ln)
                else:
                    cur.append(ln)      # 终止异常行并入本块（链式异常内层栈判噪声用）
                    blocks.append(cur)
                    cur = []
        if cur:
            blocks.append(cur)
        benign_keys = ("raw.githubusercontent.com", "manager_server.py",
                       "manager_util.py", "comfy_kitchen", "triton",
                       "Cannot connect to host", "ConnectionResetError",
                       "ClientConnectorError", "sslproto.py", "start_tls")
        bad = [b for b in blocks
               if not any(k in ln for k in benign_keys for ln in b)]
        check("运行日志无致命 Traceback（联网噪声除外）", not bad,
              (bad[0][0][:60] + " …") if bad else "")

        # ---- 7. CUDA 可用性（记录用）----
        try:
            stats = http_json(base + "/system_stats")
            dev = (stats.get("devices") or [{}])[0]
            log(f"设备：{dev.get('name')} | vram {dev.get('vram_total', 0) / 1e9:.0f}GB"
                f" | torch {dev.get('torch_version')}")
        except Exception as e:
            log(f"（system_stats 取失败：{e}）")
    finally:
        log("停机…")
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        lf.close()

    log("=" * 50)
    log(f"结论：{'✅ 全部通过' if not FAILS else '❌ ' + str(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
