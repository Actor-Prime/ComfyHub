# AMD Radeon 780M 跑 ComfyUI + qwen-image 2.1 实操手册

> 目标机器画像：Ryzen 7040/8040 系列 + Radeon 780M 核显（gfx1103）+ 16GB 内存
> 目标：本地跑通 qwen-image 2.1 生图
> 编写日期：2026-11-19

---

## 0. 先跑诊断，别急着装

```bash
# 用 ComfyHub 便携 Python 跑（或任意 Python 3.8+）
"%APPDATA%\ComfyHub\tools\python\python.exe" amd_gpu_doctor.py
```

它会输出：内存/显卡/驱动/gfx 架构、后端建议、以及**这台机器该用哪一档 qwen-image 2.1 配置**。
装任何东西之前先看这份报告，避免白下 5GB。

如果手上没有目标机器，可以用模拟：

```bash
python amd_gpu_doctor.py --ram 16     # 假装这台是 16GB
```

---

## 1. 三条路线，按优先级选

| 路线 | 速度 | 是否需要 GPL 组件 | 前置条件 |
|---|---|---|---|
| **A. ROCm + HSA_OVERRIDE** | 最快 | **否** ✅ | torch ROCm wheel |
| B. DirectML | 约 A 的一半 | 否 | 必须 Python 3.12 |
| C. 纯 CPU | 最慢 | 否 | 无 |

**默认走 A**。理由：HSA_OVERRIDE 让 gfx1103 用 gfx1100 的 kernel，
而 torch 的 ROCm wheel 本身就打包了 gfx1100 —— **不需要任何第三方库文件**，
从而绕开了社区 ROCmLibs 的 GPL-3.0 许可问题。

只有 A 失败时，才考虑用社区 ROCmLibs（见 §5），且必须注意许可约束。

---

## 2. 路线 A：ROCm + HSA_OVERRIDE（推荐）

### 2.1 前置条件

- Windows 11 22H2 以上
- AMD 驱动：Adrenalin 25.x 或更新（低于此版本 HIP runtime 会初始化失败）
- ComfyHub 便携 Python 3.13（已自带，无需另装）

### 2.2 安装 ROCm 版 torch

ROCm wheel 是按 **gfx 架构**分发的。780M 是 gfx1103，但它兼容 gfx1100
（同为 RDNA3，wave size 32，指令集兼容），所以装 **gfx110X-all** 这一组。

```bash
cd /d <ComfyUI目录>
set PIP=pip

REM 先卸掉现有的 CPU/CUDA 版 torch，避免混装
%PIP% uninstall -y torch torchvision torchaudio

REM 装 ROCm 版（--extra-index-url 保留 PyPI 作为依赖源，否则依赖拉不到）
%PIP% install --extra-index-url https://rocm.nightlies.amd.com/v2/gfx110X-all/ ^
       --pre --no-cache-dir torch torchvision torchaudio
```

若上面的命令因为 `--pre` 引入了不兼容的依赖版本，改用**精确 URL**
（先去 `https://rocm.nightlies.amd.com/v2/gfx110X-all/torch/` 找最新的
`...-cp313-cp313-win_amd64.whl`）：

```bash
%PIP% install --no-cache-dir ^
  "https://rocm.nightlies.amd.com/v2/gfx110X-all/torch/torch-2.9.1%2Brocm7.13.0a20260513-cp313-cp313-win_amd64.whl"
```

> ⚠️ **为什么不用 repo.radeon.com 的稳定版**：那条渠道只发 `cp312`，
> 而 ComfyHub 便携 Python 是 3.13，装不上。nightly 才有 `cp313-win_amd64`。
> 代价是 nightly 属 alpha 构建，稳定性不如稳定版。

### 2.3 设置 HSA_OVERRIDE（**关键一步，不设必失败**）

```bash
REM 当前会话生效
set HSA_OVERRIDE_GFX_VERSION=11.0.0

REM 永久生效（用户级，可选）
setx HSA_OVERRIDE_GFX_VERSION 11.0.0
```

作用：让 ROCm 认为自己在为 gfx1100 工作，从而加载 gfx1100 的 kernel，
而不是因为找不到 gfx1103 的 kernel 直接报
`HSA_STATUS_ERROR_INVALID_ISA` / `hipErrorNoBinaryForGpu`。

### 2.4 验证

```bash
python -c "import torch; print(torch.__version__); print('cuda:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

期望输出：

```
2.9.1+rocm7.13.0a...
cuda: True
AMD Radeon(TM) 780M Graphics
```

> ROCm 在 Python 侧伪装成 CUDA（`torch.cuda.*` 可用），这是设计如此，不是装错了。

如果 `cuda: False` → 检查 §2.3 的环境变量是否在**同一个**终端里设置；
如果报 `hipErrorNoBinaryForGpu` → HSA_OVERRIDE 没生效或被覆盖。

### 2.5 启动 ComfyUI

```bash
REM 不需要额外参数，ComfyUI 看到 torch.cuda.is_available()==True 就用 GPU
python main.py
```

如果频繁爆显存，再加 `--lowvram`：

```bash
python main.py --lowvram
```

---

## 3. 路线 B：DirectML（路线 A 不通时）

**硬约束：torch-directml 只发到 cp312，Python 3.13 装不上。**
必须先给这个实例单独准备一个 Python 3.12 解释器。

```bash
REM 用 3.12 便携版 Python
py -3.12 -m pip install torch-directml
```

代价：速度约为 ROCm 的一半。780M 实测 SD1.5 会从 28 秒变 70+ 秒。

**结论：能不用就不用。** 除非客户机器上装不了 ROCm（驱动太旧、系统限制等）。

---

## 4. qwen-image 2.1 模型清单（16GB 内存版）

按诊断脚本给出的档位下载。16GB 机器的推荐组合：

| 文件 | 大小 | 放到 | 来源 |
|---|---|---|---|
| **Q3_K_M GGUF**（或 Q4_K_M） | 3.17 GB | `models/unet/` | unsloth/Qwen-Image-2.1-GGUF |
| **qwen3vl_8b UD-Q4_K_XL** | 5.15 GB | `models/text_encoders/` | unsloth/Qwen3-VL-8B-Instruct-GGUF |
| **qwen_image_2.1_vae_bf16** | 0.68 GB | `models/vae/` | Comfy-Org/Qwen-Image-2.1 |
| Turbo LoRA（可选，8 步出图） | 小 | `models/loras/` | Comfy-Org/Qwen-Image-2.1 |

**合计约 9 GB**，16GB 内存装完还剩 1.5GB 余量 → 必须 768×768 起步。

### 不要导错的三个坑

1. **GGUF 插件用 leejet 的 fork，不是 city96 的**
   ```bash
   cd ComfyUI/custom_nodes
   REM 先删掉旧的
   rmdir /s /q ComfyUI-GGUF
   git clone https://github.com/leejet/ComfyUI-GGUF
   pip install --upgrade gguf
   ```
   用错插件会报 `Unknown model architecture!`。

2. **GGUF 放 `models/unet/`**，不是 `diffusion_models/`。
   放错位置下拉框里根本看不到。

3. **文本编码器加载节点的 `type` 必须选 `qwen_image`**。
   留默认值不会报错，但出图会崩坏（文字变鬼画符、颜色像泡过水）——
   这是最容易踩且最难查的坑。

---

## 5. 兜底：社区 ROCmLibs（A 彻底失败时才用）

`github.com/likelovewant/ROCmLibs-for-gfx1103-AMD780M-APU`

步骤（简述）：备份 `%HIP_PATH%\bin\rocblas\library` 与 `rocblas.dll` →
用对应 HIP SDK 版本的压缩包覆盖 → 重启。

> ⚠️ **许可红线**：该仓库是 **GPL-3.0**。项目铁律禁止 GPL/AGPL/SSPL。
> 因此：
> - ❌ 不能打进 ComfyHub 安装包
> - ❌ 不能由程序自动下载/镜像
> - ✅ 只能"告知用户该仓库存在、由用户自行获取"
>
> 交付时在文档里写清楚，别让客户以为这是产品自带组件。

---

## 6. 工作流关键设置（比装环境更容易翻车的地方）

| 设置项 | 值 | 原因 |
|---|---|---|
| 文本编码器 Cache 节点 `device` | **cpu** | 省 5~9GB 显存；编码器只跑一次，对速度几乎无影响 |
| prompt enhancer | **关闭** | 它会额外加载 9.5GB 的 Qwen3.5-9B，直接压垮 16GB 机器 |
| 分辨率 | **768×768 起步** | 分辨率翻倍，显存占用涨约 4 倍 |
| batch size | **1** | 多张是线性叠加内存 |
| CFG | **1**（Turbo） | 官方模板 CFG=1，负向提示词不起作用 |
| 步数 | Turbo 8 步 / 标准 40 步 | Turbo 省时间不省内存 |
| VAE | 必须 `qwen_image_2.1_vae_bf16` | 老的 Qwen-Image VAE 不兼容 2.1 |

**不要**在基础流程没跑通之前开：prompt enhancer、2K 分辨率、多图编辑。

---

## 7. 排错速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `HSA_STATUS_ERROR_INVALID_ISA` | gfx1103 不被支持 | 设 `HSA_OVERRIDE_GFX_VERSION=11.0.0` |
| `hipErrorNoBinaryForGpu` | 同上，或 override 值不对 | 试 `11.0.0`；gfx1103 也有人用 `11.0.3` |
| `torch.cuda.is_available()` 为 False | torch 装的是 CPU 版 | 重装 ROCm wheel，确认文件名含 `rocm` |
| `CUDA out of memory` | 编码器没卸载到 CPU | Cache 节点 device=cpu；开 `--lowvram` |
| 出图崩坏但**不报错** | 编码器 type 没选 `qwen_image` | 见 §4 第 3 条 |
| `Unknown model architecture!` | GGUF 插件用错 fork | 换 leejet 版 |
| GGUF 不在下拉框 | 文件放错目录 | 移到 `models/unet/`，刷新 |
| 生成极慢（30+ 分钟） | 分辨率过高 / 主干在内存显存间搬运 | 降到 768；提高 UMA 显存分配 |
| 系统卡死、内存 100% | 16GB 装不下当前档位 | 降一档（见 §4），关掉浏览器 |

---

## 8. 速度预期（务必先跟客户对齐）

同代 780M 实测（7840HS + 32GB DDR5-5600）：

| 任务 | 耗时 |
|---|---|
| SD1.5 512×768 / 25 步 | 28 秒 |
| SDXL 1024×1024 / 30 步 | 6 分 41 秒 |
| Flux.1-dev Q4 / 8 步 | 12 分 07 秒（内存峰值 21GB） |
| **qwen-image 2.1 Turbo 8 步（预估）** | **5 ~ 12 分钟/张** |
| **qwen-image 2.1 标准 40 步（预估）** | **20 ~ 40 分钟/张** |

核显的硬瓶颈是**内存带宽**（双通道 DDR5 约 83~89 GB/s，独显是 200~500+）。
这不是软件优化能解决的。

**交付话术建议**：这是"能出图"的配置，不是"能快速迭代"的配置。
如果要频繁调参出图，建议 Turbo 8 步 + 768×768 先跑通提示词，定稿后再出 1024。

---

## 9. 交付检查清单

- [ ] `amd_gpu_doctor.py` 跑过，报告留档
- [ ] 驱动版本 ≥ Adrenalin 25.x
- [ ] `HSA_OVERRIDE_GFX_VERSION=11.0.0` 已设（用户级，开机生效）
- [ ] `python -c "import torch; print(torch.cuda.is_available())"` → `True`
- [ ] 四个模型文件就位，目录正确
- [ ] leejet 版 ComfyUI-GGUF 已装
- [ ] 一张 768×768 的图跑通（10 分钟内）
- [ ] 客户已知悉速度预期
- [ ] 若要重装系统/换驱动，HSA_OVERRIDE 需重新设置

---

## 10. 产品内置路径（ComfyHub v0.8.32+ 起）

2026-11-19 起 ComfyHub 已内置 AMD 支持，不再需要手工敲命令。三处改动：

| 环节 | 行为 |
|---|---|
| 显卡识别 | `bootstrap.detect_gpu()` 认 AMD 商品名 → gfx 架构，纯 winreg 不依赖 wmi |
| torch 安装 | 向导步骤 4 自动选 `rocm-gfx1100` 等变体，从 `rocm.nightlies.amd.com` 装 |
| 启动注入 | `process.py` 每次启动按当前显卡判定，自动注入 `HSA_OVERRIDE_GFX_VERSION` |

**780M（gfx1103）走的是「家族内核兼容」路径**（`bootstrap.rocm_plan`）：

- gfx1103 不在 AMD 的 Windows ROCm 官方列表
- 但它与 gfx1100 同属 RDNA3（wave32、指令集兼容）
- 所以用 `gfx110X-all` 的 wheel + `HSA_OVERRIDE_GFX_VERSION=11.0.0`
- 代码里标为 `native=False`，UI 文案写「可试 ROCm 加速（需实测）」，不承诺一定成功

**限制（务必知道）**：

1. **torchaudio 不装**：ROCm 的 Windows 构建经常缺这个包，装了会卡在下载
2. **`torch.version.cuda` 是 None**：ROCm 版靠 `torch.version.hip` 识别，
   `process.py` 的启动门卫已特判，否则 AMD 实例会被当成「CPU 版 torch」拦下
3. **国内无镜像**：AMD nightlies 没有阿里/上交镜像，3~5GB 只能直连（有断点续传）
4. **DirectML 这条兜底路在当前架构下不可用**：`torch-directml` 的 Windows
   wheel 只有 `cp37~cp312`，而便携 Python 是 **3.13**。要给老卡（RX 6000）上
   DML，必须另装一个 Python 3.12 解释器做实例隔离——尚未实现。

### 现场如果产品路径失败，按这个顺序退

1. 手动在实例环境里设 `HSA_OVERRIDE_GFX_VERSION=11.0.0` 再启动（排除注入失败）
2. 换 `11.0.3`（部分 Phoenix 批次需要这个值）
3. `python -c "import torch;print(torch.version.hip, torch.cuda.is_available())"`
   → hip 有值但 is_available 为 False 说明内核不匹配
4. 退回纯 CPU 出图（本文档第 5 节的 SDXL / Flux 方案）
