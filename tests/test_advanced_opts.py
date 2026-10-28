# -*- coding: utf-8 -*-
"""对齐绘世「高级选项」补齐的启动参数回归（2026-10-28）

覆盖：
  1. medvram 修复：中显存发 --normalvram（新版 ComfyUI 已无 --medvram）
  2. 多卡选择：--cuda-device（显式卡号才加，自动/-1/None 不加）
  3. 计算精度三档：自动(无)/FP16/FP32，旧 force_fp16 布尔兼容
  4. 新开关：--cpu-vae / --deterministic / --force-channels-last / --multi-user
  5. CUDA 分配器三档：自动(无)/强制启用/禁用
  6. 预览 auto 档 → --preview-method auto
  7. detect_gpus 在本机的健壮性（有卡返回 list，字段齐全）
用法: .venv_build/Scripts/python.exe tests/test_advanced_opts.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.process import build_command, detect_gpus   # noqa: E402
from app.config import VRAM_MODES                     # noqa: E402

res = []


def check(n, ok, d=""):
    res.append((n, ok, d))
    print(f"  {'✅' if ok else '❌'} {n}" + (f"  [{d}]" if d else ""))


def mk(**kw):
    base = {"comfyui_path": r"F:\x\ComfyUI", "python_path": "python.exe",
            "port": 8188, "device": "gpu", "auto_open_browser": True}
    base.update(kw)
    return base


print("=== 1. medvram 修复 ===")
check("中显存映射 --normalvram", VRAM_MODES["中显存"] == "--normalvram",
      VRAM_MODES["中显存"])
cmd = build_command(mk(vram_mode="中显存"))
check("命令含 --normalvram", "--normalvram" in cmd)
check("命令不含 --medvram", "--medvram" not in cmd)
for name in ("低显存", "高显存", "禁用显存优化"):
    c = build_command(mk(vram_mode=name))
    check(f"{name} → {VRAM_MODES[name]}", VRAM_MODES[name] in c)

print("\n=== 2. 多卡选择 --cuda-device ===")
check("自动(cuda_device=None)不加", "--cuda-device"
      not in build_command(mk(cuda_device=None)))
check("cuda_device=-1 不加", "--cuda-device"
      not in build_command(mk(cuda_device=-1)))
c = build_command(mk(cuda_device=1))
check("cuda_device=1 → --cuda-device 1",
      "--cuda-device" in c and c[c.index("--cuda-device") + 1] == "1")
check("CPU 模式忽略 cuda_device", "--cuda-device"
      not in build_command(mk(device="cpu", cuda_device=0)))

print("\n=== 3. 计算精度三档 ===")
check("自动不加参数", "--force-fp16" not in build_command(mk(precision="自动"))
      and "--force-fp32" not in build_command(mk(precision="自动")))
check("FP16 → --force-fp16", "--force-fp16" in build_command(mk(precision="强制 FP16")))
check("FP32 → --force-fp32", "--force-fp32" in build_command(mk(precision="强制 FP32")))
check("旧 force_fp16=True 兼容", "--force-fp16" in build_command(mk(force_fp16=True)))
check("precision 优先于旧字段",
      "--force-fp32" in build_command(mk(precision="强制 FP32", force_fp16=True)))

print("\n=== 4. 新开关 ===")
for key, flag in [("cpu_vae", "--cpu-vae"), ("deterministic", "--deterministic"),
                  ("channels_last", "--force-channels-last"),
                  ("multi_user", "--multi-user")]:
    check(f"{key}=True → {flag}", flag in build_command(mk(**{key: True})))
    check(f"{key}=False 不加", flag not in build_command(mk(**{key: False})))

print("\n=== 5. CUDA 分配器三档 ===")
check("自动不加", "--cuda-malloc" not in build_command(mk(cuda_malloc="自动"))
      and "--disable-cuda-malloc" not in build_command(mk(cuda_malloc="自动")))
check("强制启用 → --cuda-malloc",
      "--cuda-malloc" in build_command(mk(cuda_malloc="强制启用")))
check("禁用 → --disable-cuda-malloc",
      "--disable-cuda-malloc" in build_command(mk(cuda_malloc="禁用")))

print("\n=== 6. 预览 auto 档 ===")
c = build_command(mk(preview_method="自动"))
check("自动 → --preview-method auto",
      "--preview-method" in c and c[c.index("--preview-method") + 1] == "auto")
c = build_command(mk(preview_method="TAESD（精细）"))
check("TAESD → taesd", c[c.index("--preview-method") + 1] == "taesd")
check("无 → 不加", "--preview-method" not in build_command(mk(preview_method="无")))

print("\n=== 7. detect_gpus 健壮性 ===")
gpus = detect_gpus()
check("返回 list", isinstance(gpus, list))
if gpus:
    g = gpus[0]
    check("字段齐全(id/name/vram_mib)",
          all(k in g for k in ("id", "name", "vram_mib")))
    names = [str(x["id"]) + ":" + x["name"] for x in gpus]
    print("    本机检测到 %d 卡: %s" % (len(gpus), names))
else:
    print("    本机未检测到 CUDA 卡（或 nvidia-smi 不可用），返回空 list — 也可接受")

print("\n=== 8. 生成引擎统一下拉（2026-10-29 对齐绘世）===")
check("engine=auto 不加设备参数",
      "--cpu" not in build_command(mk(engine_backend="auto"))
      and "--cuda-device" not in build_command(mk(engine_backend="auto"))
      and "--directml" not in build_command(mk(engine_backend="auto")))
c = build_command(mk(engine_backend="cuda", engine_index=1))
check("cuda+index=1 → --cuda-device 1",
      "--cuda-device" in c and c[c.index("--cuda-device") + 1] == "1")
check("cuda+index=None 不加 --cuda-device",
      "--cuda-device" not in build_command(mk(engine_backend="cuda", engine_index=None)))
c = build_command(mk(engine_backend="dml", engine_index=0))
check("dml+index=0 → --directml 0",
      "--directml" in c and c[c.index("--directml") + 1] == "0")
c = build_command(mk(engine_backend="dml", engine_index=None))
check("dml+无index → 裸 --directml", "--directml" in c)
check("dml 裸参数后不跟数字", "--directml" == c[-1] or
      not c[c.index("--directml") + 1].isdigit())
check("cpu → --cpu", "--cpu" in build_command(mk(engine_backend="cpu")))
check("cpu 忽略 engine_index",
      "--cuda-device" not in build_command(mk(engine_backend="cpu", engine_index=0)))
# 旧字段迁移（无 engine_backend 时）
check("旧 device=cpu → --cpu（无 backend 字段）",
      "--cpu" in build_command(mk(device="cpu")))
check("旧 cuda_device=1 → --cuda-device 1（无 backend 字段）",
      "--cuda-device" in build_command(mk(cuda_device=1)))
check("backend 字段优先于旧 device",
      "--cpu" not in build_command(mk(device="cpu", engine_backend="cuda", engine_index=0)))

print("\n=== 9. Cross Attention 五档（对齐绘世，含 Sage 新增）===")
c = build_command(mk(cross_attn="Sage Attention"))
check("Sage → --use-sage-attention", "--use-sage-attention" in c)
check("Sage 不带其他注意力参数",
      not any(x.startswith("--use-") and x.endswith("cross-attention") for x in c))
c = build_command(mk(cross_attn="自动（xFormers 优先）"))
check("xFormers 优先 → 不加参数（环境自选）",
      not any("cross-attention" in x for x in c))
check("旧值「自动」仍兼容 → 不加参数",
      not any("cross-attention" in x for x in build_command(mk(cross_attn="自动"))))
for name, flag in [("PyTorch SDP", "--use-pytorch-cross-attention"),
                   ("Quad（省显存）", "--use-quad-cross-attention"),
                   ("Split（更快）", "--use-split-cross-attention")]:
    check(f"{name} → {flag}", flag in build_command(mk(cross_attn=name)))

print("\n" + "=" * 44)
bad = [n for n, ok, _ in res if not ok]
print(f"结果: {len(res) - len(bad)}/{len(res)} 通过")
for n in bad:
    print("  ❌", n)
sys.exit(1 if bad else 0)
