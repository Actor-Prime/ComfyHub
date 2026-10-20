<div align="center">

# ComfyHub

**轻量级 ComfyUI 多实例启动管理器**

一键安装环境 · 多实例管理 · 工作流智能安装 · 依赖管理 · 免梯国内源优化

`PySide6` · `便携免安装` · `~47MB`

> ⚠️ **实验性半成品项目（Work-in-Progress）**：功能持续迭代中，未经充分测试，
> 请勿以成品标准评估或用于生产环境。

</div>

---

## 📖 这是什么

ComfyHub 是一个面向**普通用户**的 ComfyUI 启动管理器，目标是把"部署 ComfyUI"从折腾变成点按钮：

- 🚀 **一键安装**：便携 Python + ComfyUI 源码 + pip 依赖 + PyTorch（按显卡自动匹配 CUDA/CPU 版）全自动装好，装完自动注册为实例
- 🎛️ **多实例管理**：多个 ComfyUI 环境并存，独立配置端口/显存策略/启动参数，一键启停
- 🧬 **工作流智能安装**：导入任意工作流 JSON → 自动扫描缺失的插件和模型 → 生成安装计划一键执行（安装前自动环境快照，可回滚）
- 📦 **依赖管理**：pip 包检测/搜索/卸载，六种方式安装新依赖（包名 / requirements.txt / whl 轮子 / python 脚本 / 常用依赖勾选 / 自定义命令）
- 🛡️ **环境备份救援**：一键备份还原 pip 环境；救援快照可回滚"装坏"的插件
- 🧠 **模型库**：分类标签页浏览模型文件，支持从网盘导入（复制/剪切）、直链下载（断点续传）
- 🇨🇳 **国内裸连友好**：npmmirror / 阿里云 / 华为云 / gh-proxy 多源轮换，全程无需科学上网

## 🖼️ 界面预览

| 工作台 | ComfyUI 管理 |
|---|---|
| ![工作台](https://gitee.com/yzdydwdd/ComfyHub/raw/master/docs/screenshots/workbench.png) | ![ComfyUI](https://gitee.com/yzdydwdd/ComfyHub/raw/master/docs/screenshots/comfyui.png) |

| 环境检测 | 模型库 |
|---|---|
| ![环境检测](https://gitee.com/yzdydwdd/ComfyHub/raw/master/docs/screenshots/env.png) | ![模型库](https://gitee.com/yzdydwdd/ComfyHub/raw/master/docs/screenshots/models.png) |

> 更多截图见 [docs/screenshots/](https://gitee.com/yzdydwdd/ComfyHub/raw/master/docs/screenshots/)

## 🚀 快速开始

### 方式一：下载便携版（推荐）

1. 从 [Releases](../../releases) 下载 `ComfyHub-portable.zip`（约 47MB）
2. 解压到任意目录，双击 `ComfyHub.exe`
3. 点「⚡ 一键安装」→ 选安装位置 → 喝杯咖啡 ☕
4. 点「▶ 启动」→ 浏览器打开 `http://127.0.0.1:8188`

> **无需安装**，无需预装 Python/Node/任何运行时；便携版解压即用，删除目录即卸载，无注册表残留。

### 方式二：已有 ComfyUI 环境

1. 打开 ComfyHub → 顶栏「＋ 添加」
2. 选择你的 ComfyUI 根目录（含 `main.py`）和 Python 解释器
3. 保存后即可纳入统一管理

### 方式三：从源码运行

```bash
git clone https://gitee.com/leimengstudio/ComfyHub.git
cd ComfyHub
pip install PySide6 psutil pillow -i https://mirrors.aliyun.com/pypi/simple/
python main.py
```

## ⚙️ 功能详解

### 一键安装向导

| 步骤 | 内容 | 来源 |
|---|---|---|
| 1/4 | 便携 Python 3.13（免安装版，自带 pip） | npmmirror（实测 7MB/s） |
| 2/4 | ComfyUI 源码 | GitHub（可配 ghproxy 代理） |
| 3/4 | pip 依赖 | 阿里云镜像 |
| 4/4 | PyTorch 按显卡匹配 | N卡驱动≥580→cu130 / ≥525→cu126 / 无N卡→CPU |

- 支持断点续传，中断重跑不重来
- 安装中按钮全锁定，防误触；失败可返回重试
- 已有环境自动检测跳过（源码存在/依赖已装则秒过）

### 高级启动设置（11 项）

启动端口 / 生成设备 GPU·CPU / 显存优化 / Cross Attention 优化 / 采样预览 / 强制 FP16 / 快速模式 / 禁用智能显存 / 局域网访问 / 自动打开浏览器 / 自定义启动参数

### 环境检测（7 项清单）

GIT / Python / PyTorch / PyTorch CUDA / 显卡 / 显卡驱动 / CPU——后台逐项检测，绿徽章通过红徽章异常。

### 工作流智能安装

```
选择工作流 JSON → 扫描节点 → 匹配本地插件/模型 → 生成安装计划
→ [可选] 环境快照 → git clone 插件 + pip 装依赖 + 下载模型 → 完成
```

支持 UI 导出和 API 两种格式；模型下载自动走 HF 镜像。

## 🔧 从源码构建便携版

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name ComfyHub --icon assets/icon.ico --add-data "assets;assets" --hidden-import PySide6.QtSvg main.py
```

产物在 `dist/ComfyHub/`，整个目录拷走即可分发。

## ❓ FAQ

**Q: 和绘世/TE 启动器/搅拌手有什么区别？**
A: ComfyHub 走便携免安装路线（47MB vs 搅拌手 94MB NSIS），无注册表污染；一键安装基于免安装 Python 方案，绕开了官方安装器的静默失败坑；全套下载源国内优先，裸连可用。

**Q: 下载需要挂 VPN 吗？**
A: 不需要。Python/Git/依赖走国内镜像；GitHub 源码可在设置配 ghproxy 代理；HuggingFace 自动切 hf-mirror.com；torch CUDA 直连较慢但支持断点续传。

**Q: 便携 Python 装在哪？会不会污染系统？**
A: `%APPDATA%\ComfyHub\tools\`，所有实例共享；删除该目录即卸载，不写注册表、不动系统 PATH。

**Q: 杀毒软件报毒？**
A: PyInstaller 打包的程序可能被误报，加入白名单即可；有条件可做代码签名彻底消除。

**Q: 崩溃了怎么排查？**
A: 看 `%APPDATA%\ComfyHub\crash.log`——内置黑匣子记录完整异常堆栈。

## 🗺️ 路线图

- [x] 多实例管理 / 一键安装 / 环境检测 / 依赖管理 / 备份救援
- [ ] 工作流依赖一键安装端到端实测（框架已就绪）
- [ ] CivitAI / HF 站内搜索下载
- [ ] ComfyUI 内核版本管理（切换 13MB 源码即切版本）
- [ ] 插件市场（Gitee 镜像索引）
- [ ] FluxGym LoRA 训练集成
- [ ] AMD 显卡支持

## ⚠️ 免责声明

本软件按"现状"提供，不附带任何明示或默示保证：

- 未签名程序可能触发 Windows SmartScreen 提示，点「仍运行」即可；介意可自行上传 virustotal.com 复核
- 「彻底删除」「备份还原」「快照回滚」等操作具有破坏性，请自行做好重要文件备份，开发者不对数据丢失承担责任
- 下载的 ComfyUI / 插件 / 模型版权归各自作者；使用模型生成内容请遵守当地法律法规
- **实验性半成品项目**：未经充分测试，请勿以成品标准评估或用于生产环境
- **本软件由 AI 辅助生成**，开发者不对代码版权归属作解释与承诺
- **商业用途责任自负**：允许任何使用，由此产生的一切后果与责任均与开发者无关

完整声明见 [DISCLAIMER.txt](DISCLAIMER.txt)。继续使用即表示已阅读并同意全部内容。

## 📄 开源协议

[MIT License](LICENSE)

---

<div align="center">

**ComfyHub** · 让每个人都能用上 ComfyUI

</div>
