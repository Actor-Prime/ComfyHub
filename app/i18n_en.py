# -*- coding: utf-8 -*-
"""英文词表（完整版，2026-11-01 第二批）

键 = 源码里的中文原文，必须逐字一致（含标点、emoji、全角冒号）。
新增中文文案后，用 tests/_tmp_i18n_cover.py 复查漏译条数再补这里。
"""

EN_DICT = {
    # ================= 主窗 / 侧栏 / 顶栏 / 托盘 =================
    "ComfyHub — ComfyUI 多实例管理器": "ComfyHub — ComfyUI Multi-Instance Manager",
    "工作台": "Workbench",
    "插件管理": "Plugins",
    "环境管理": "Environment",
    "模型库": "Models",
    "工作流": "Workflows",
    "控制台": "Console",
    "系统设置": "Settings",
    "当前实例": "Current instance",
    "＋ 添加 ComfyUI": "+ Add ComfyUI",
    "新装 ComfyUI": "Install ComfyUI",
    "（无实例，点右上角添加）": "(no instance yet — add one at the top right)",
    "显示主窗口": "Show main window",
    "退出 ComfyHub": "Quit ComfyHub",
    "自动": "Auto",

    # ================= 设置页 =================
    "设置": "Settings",
    "网络": "Network",
    "目录": "Directories",
    "界面": "Interface",
    "关于": "About",
    "PyPI 镜像:": "PyPI mirror:",
    "GitHub 代理:": "GitHub proxy:",
    "Git Clone 代理:": "Git Clone proxy:",
    "留空直连；如 https://ghproxy.net/ （自动拼在 github URL 前）":
        "Empty = direct; e.g. https://ghproxy.net/ (prepended to GitHub URLs)",
    "git clone 专用代理（留空则同上）": "Proxy for git clone (empty = same as above)",
    "工具目录:": "Tools directory:",
    "留空 = 默认（%APPDATA%\\ComfyHub\\tools）": "Empty = default (%APPDATA%\\ComfyHub\\tools)",
    "浏览…": "Browse…",
    "恢复默认": "Reset to default",
    "语言:": "Language:",
    "English (Beta · 部分界面)": "English (Beta · partial UI)",
    "切换语言后需重启 ComfyHub 生效": "Restart ComfyHub to apply the language change",
    "主题:": "Theme:",
    "深色": "Dark",
    "浅色": "Light",
    "跟随系统": "Follow system",
    "自定义（半透明 + 背景图）": "Custom (translucent + wallpaper)",
    "「跟随系统」读取 Windows 深浅色设置，系统切换后自动跟着变":
        "“Follow system” reads the Windows light/dark setting and switches automatically",
    "关闭主窗口时:": "On window close:",
    "每次询问": "Ask every time",
    "直接退出程序": "Quit directly",
    "最小化到托盘": "Minimize to tray",
    "「每次询问」下首次点关闭按钮会弹窗选择，勾选『记住我的选择』后不再询问（随时可在此改回）":
        "With “Ask every time”, the first close click asks you; tick “Remember my choice” to stop asking (changeable here anytime)",
    "自定义背景": "Custom background",
    "背景图片:": "Background image:",
    "选择图片…": "Choose image…",
    "清除": "Clear",
    "（未选择，使用纯深色底）": "(none — plain dark background)",
    "面板不透明度:": "Panel opacity:",
    "面板越透明，背景图越清晰；改完点『保存设置』立即生效":
        "More transparency reveals more of the image; click Save settings to apply",
    "AI 接入（可选）": "AI assist (optional)",
    "启用 AI 接入（仅确定性查不到时调用）":
        "Enable AI assist (only when deterministic lookup fails)",
    "接口地址:": "Endpoint:",
    "OpenAI 兼容端点，如 https://api.deepseek.com/v1":
        "OpenAI-compatible endpoint, e.g. https://api.deepseek.com/v1",
    "API Key（仅存本机 config.json）": "API Key (stored locally in config.json)",
    "测试接口": "Test connection",
    "正在连接…": "Connecting…",
    "模型名，如 mimo-v2.6-pro / deepseek-chat": "Model, e.g. mimo-v2.6-pro / deepseek-chat",
    "模型名:": "Model:",
    "模型名，如 deepseek-chat / glm-4-flash": "Model, e.g. deepseek-chat / glm-4-flash",
    "保存设置": "Save settings",

    # ================= 关闭询问框 =================
    "关闭 ComfyHub？": "Close ComfyHub?",
    "请选择点击关闭按钮时的行为：": "Choose what the close button does:",
    "· 退出程序：关闭 ComfyHub（正在运行的 ComfyUI 实例会一并停止）\n· 最小化到托盘：程序在后台继续运行，实例不受影响":
        "· Quit: closes ComfyHub (running ComfyUI instances stop too)\n"
        "· Minimize to tray: keeps running in the background, instances keep running",
    "记住我的选择（可在「系统设置」中更改）": "Remember my choice (changeable in Settings)",
    "退出程序": "Quit",
    "取消": "Cancel",

    # ================= 工作台 =================
    "轻量 ComfyUI 多实例管理器 · 一键装环境 · 工作流智能安装":
        "Lightweight ComfyUI instance manager · one-click setup · smart workflow install",
    "当前实例: （未选择）": "Current instance: (none selected)",
    "实例目录": "Instance folder",
    "▶  启动": "▶  Start",
    "■  停止": "■  Stop",
    "🌐  打开网页": "🌐  Open Web UI",
    "⚙  管理此实例": "⚙  Manage this instance",

    # ================= ComfyUI 实例页 =================
    "▶ 启动": "▶ Start",
    "🌐 打开网页": "🌐 Open Web UI",
    "🔁 批量运行": "🔁 Batch run",
    "同一工作流跑多次，可设置每 N 次重启实例释放显存":
        "Run one workflow repeatedly, restarting every N runs to free VRAM",
    "未运行": "Stopped",
    "基础设置": "Basic settings",
    "修改目录": "Change folder",
    "高级设置": "Advanced settings",
    "CUDA：NVIDIA 显卡加速（推荐）\nDML：DirectML 后端（AMD/Intel 显卡可用；依赖 torch-directml）\nCPU：仅调试用，速度极慢":
        "CUDA: NVIDIA GPU acceleration (recommended)\n"
        "DML: DirectML backend (AMD/Intel GPUs; requires torch-directml)\n"
        "CPU: debugging only, extremely slow",
    "开启": "On",
    "例如: --reserve-vram 2": "e.g. --reserve-vram 2",
    "⚙️ 实例设置": "⚙️ Instance settings",
    "内核版本": "Kernel version",
    "🧬 内核版本": "🧬 Kernel version",
    "⬆ 更新至最新稳定版": "⬆ Update to latest stable",
    "刷新版本": "Refresh versions",
    "🗑 删除此实例（可选仅移出管理 / 彻底删除文件）":
        "🗑 Delete this instance (remove from list or delete files)",
    "正在拉取远端更新并加载版本列表…": "Fetching remote updates and loading versions…",
    "正在更新至最新稳定版…（含依赖安装，可能需要几分钟）":
        "Updating to the latest stable… (installs dependencies, may take minutes)",
    "自动（推荐）": "Auto (recommended)",
    "DML 设备检测中…": "Detecting DML devices…",
    "删除实例": "Delete instance",
    "⏳ 正在计算目录体积…": "⏳ Calculating folder size…",
    "🗑 正在后台删除，请勿关闭软件…": "🗑 Deleting in background — do not close the app…",
    "彻底删除实例": "Delete instance completely",
    "正在读取内核信息…": "Reading kernel info…",
    "切换并装依赖": "Switch & install deps",
    "该实例的 Python 环境未安装 torch-directml。\nAMD/Intel 显卡用户可安装后使用：pip install torch-directml":
        "torch-directml is not installed in this instance.\n"
        "AMD/Intel users can install it: pip install torch-directml",

    # ================= 控制台 =================
    "全局运行日志（全部实例）": "Global run log (all instances)",
    "自动滚动": "Auto scroll",
    "清屏": "Clear",
    "复制全部": "Copy all",

    # ================= 环境管理 =================
    "环境检测": "Environment check",
    "快照 / 备份": "Snapshots / Backups",
    "依赖管理": "Dependencies",
    "🔍 采集检测": "🔍 Run check",
    "快照/备份备注（可空）": "Snapshot/backup note (optional)",
    "创建环境备份": "Create backup",
    "创建救援快照": "Create rescue snapshot",
    "还原选中备份": "Restore selected backup",
    "回滚选中快照": "Roll back to selected snapshot",
    "📦 依赖检测": "📦 Check packages",
    "➕ 安装新依赖": "➕ Install packages",
    "🔍 搜索已装依赖…": "🔍 Search installed packages…",
    "依赖检测/安装日志…": "Package check / install log…",
    "首次加载需要点上方【依赖检测】；卸载前请先停止 ComfyUI。":
        "Click “Check packages” above on first load; stop ComfyUI before uninstalling.",
    "安装新依赖": "Install packages",
    "💡 温馨提示：可在【系统设置】切换国内镜像，提高安装速度！":
        "💡 Tip: switch to a China mirror in Settings to speed up installs!",
    "开始安装": "Start install",
    "输入依赖包名（空格分隔多个），如: opencv-python rembg":
        "Package names (space separated), e.g. opencv-python rembg",
    "根据名字安装": "Install by name",
    "requirements.txt 文件路径": "Path to requirements.txt",
    "根据 requirements.txt 安装": "Install from requirements.txt",
    "浏览轮子文件（.whl），如 torch-2.10.0+cu130-....whl":
        "Pick a wheel (.whl), e.g. torch-2.10.0+cu130-....whl",
    "浏览 .whl": "Choose .whl",
    "安装轮子文件": "Install wheel",
    "Python 脚本路径（如 get-pip.py）": "Path to a Python script (e.g. get-pip.py)",
    "通过 Python 文件安装": "Run Python script",
    "常用依赖": "Common packages",
    "pip 子命令与参数，如: install numpy --upgrade":
        "pip arguments, e.g. install numpy --upgrade",
    "执行命令": "Run command",
    "未检测": "Not checked",
    "检测中…": "Checking…",
    "卸载": "Uninstall",
    "等价于 pip install <包名>，走国内镜像。":
        "Same as pip install <package>, using the China mirror.",
    "等价于 pip install -r <文件>，适合批量补依赖。":
        "Same as pip install -r <file>, good for bulk installs.",
    "适合离线安装大包（torch 等），不受网络影响。":
        "Best for large offline packages (torch etc.), no network needed.",
    "以当前实例解释器运行该脚本（等价 python <文件>）。":
        "Run the script with this instance's interpreter (same as python <file>).",
    "以 python -m pip <你输入的内容> 执行，熟练玩家用。":
        "Runs python -m pip <your input> — for advanced users.",

    # ================= 模型库 =================
    "🔍 搜索": "🔍 Search",
    "📥 导入": "📥 Import",
    "把本地模型文件复制/剪切进当前类别的 models 目录":
        "Copy/move local model files into the current category folder",
    "📂 打开": "📂 Open",
    "打开当前类别（或 models 根）目录": "Open the current category (or models root) folder",
    "🔄 刷新": "🔄 Refresh",
    "直链下载（断点续传 · CivitAI / HuggingFace 链接）":
        "Direct download (resumable · CivitAI / HuggingFace links)",
    "模型直链 URL（CivitAI 直链带 &token=；HuggingFace 用 resolve/main 链接）":
        "Model direct URL (CivitAI needs &token=; HuggingFace uses resolve/main)",
    "导入方式": "Import mode",

    # ================= 模型市场 =================
    "开始下载": "Start download",
    "⬇ 下载": "⬇ Download",
    "🔗 复制": "🔗 Copy",
    "已入队 ✓": "Queued ✓",
    "已复制 ✓": "Copied ✓",
    "正在检索 HF 镜像社区同步仓库（模型名 + 文件名词组多策略）…":
        "Searching HF mirrors for synced repos (model + file-name keyword strategies)…",
    "输入关键词搜索，或点下方快捷词开始。":
        "Type a keyword, or pick a suggestion below.",
    "取消全部": "Cancel all",
    "📂 models 目录": "📂 models folder",
    "🛒 在线模型商店（免登录 · 直连可下）":
        "🛒 Online model store (no login · direct download)",
    "搜索 HuggingFace / CivitAI 模型仓库，勾选文件一键下到实例 models 目录":
        "Search HuggingFace / CivitAI repos and download files into the instance models folder",
    "中文搜索自动换词：写实→realistic · 二次元→anime · 国风→chinese style":
        "Chinese queries auto-map: 写实→realistic · 二次元→anime · 国风→chinese style",
    "加载更多 ↓": "Load more ↓",
    "加载中…": "Loading…",
    "勾选要下载的权重文件，选择目标分类目录：":
        "Select weight files to download and pick a target category:",
    "目标分类目录:": "Target category:",
    "（无模型名/文件名，无法自动检索，请手动在市场搜同名仓库）":
        "(No model/file name — search the same repo manually in the market)",
    "下载 ↓": "Download ↓",
    "HF 镜像暂未找到同步仓库——可点上方「🔗 复制」直链用工具下载":
        "No synced repo found on the HF mirror — use “🔗 Copy” above with another downloader",
    "已装": "Installed",

    # ================= 插件管理 =================
    "已安装": "Installed",
    "插件市场": "Marketplace",
    "启动模式:": "Startup profile:",
    "▶ 应用": "▶ Apply",
    "按此模式批量启用/停用插件（重启 ComfyUI 生效）":
        "Enable/disable plugins in bulk per this profile (restart ComfyUI to apply)",
    "💾 存为方案": "💾 Save as profile",
    "把当前插件的启停状态保存为一个模式":
        "Save the current plugin on/off state as a profile",
    "删除": "Delete",
    "当前 commit 短哈希；点「版本」可切换分支/tag":
        "Current short commit; click “Version” to switch branch/tag",
    "有节点冲突等故障时，「启用」后面会挂 ⚠ 图标，悬停可看详情":
        "On node conflicts a ⚠ icon follows “Enabled” — hover for details",
    "双击行 = 启用/禁用；调好后点「存为方案」保存成启动模式。":
        "Double-click a row to enable/disable; then “Save as profile”.",
    "🔍 检查节点冲突": "🔍 Check node conflicts",
    "🔄 更新全部": "🔄 Update all",
    "打开目录": "Open folder",
    "＋ Git URL 安装": "＋ Install from Git URL",
    "搜索插件名 / 作者 / 描述…（回车搜索）": "Search name / author / description… (Enter)",
    "刷新清单": "Refresh list",
    "尚未加载插件清单（切到此页会自动加载，首次约 10 秒）":
        "Plugin list not loaded yet (auto-loads on this page, ~10s the first time)",
    "热门优先": "Popular first",
    "名称 A→Z": "Name A→Z",
    "ComfyUI 官方 Registry 统计的下载量（万 = 10,000 次）\n无下载量数据的插件显示 GitHub 星数（⭐）或 —":
        "Download counts from the official ComfyUI Registry (万 = 10,000).\n"
        "Plugins without download data show GitHub stars (⭐) or —",
    "双击插件名可在浏览器打开仓库页面（单击仅选中）":
        "Double-click a plugin name to open its repo in the browser (single click selects)",
    "数据来源：ComfyUI-Manager 官方插件清单（6000+）。安装走 git clone，网络不佳时可在「系统设置」配置代理后点「刷新清单」。":
        "Source: official ComfyUI-Manager catalog (6000+). Installs use git clone; "
        "if the network is slow, set a proxy in Settings and click “Refresh list”.",
    "正在扫描节点冲突（只查已启用插件，静态解析不执行插件代码）…":
        "Scanning node conflicts (enabled plugins only, static parse, no code executed)…",
    "正在拉取插件清单…（首次约 10 秒，之后走缓存）":
        "Fetching plugin list… (~10s the first time, then cached)",
    "排序:": "Sort:",
    "（未选择实例）": "(no instance selected)",
    "这个实例还没有插件 —— 切到「插件市场」搜一个装上吧。":
        "No plugins in this instance yet — search one in “Marketplace”.",
    "获取远端版本失败": "Failed to get remote version",
    "该插件无 Git 地址（本地/手工安装）": "No Git URL for this plugin (local/manual install)",
    "该插件当前状态与所选启动模式不一致，点「▶ 应用」同步":
        "Plugin state differs from the selected profile — click “▶ Apply” to sync",
    "安装": "Install",

    # ================= 启动模式（profiles） =================
    "内核快照管理": "Kernel snapshots",
    "📥 从实例导入…": "📥 Import from instance…",
    "从 zip 导入…": "Import from zip…",
    "删除选中": "Delete selected",
    "从工作流创建启动模式": "Create profile from workflow",
    "—— 当前实例工作流目录 ——": "—— current instance workflows ——",
    "🔍 扫描节点": "🔍 Scan nodes",
    "启动模式名称（如：Qwen三视图）": "Profile name (e.g. Qwen turnarounds)",
    "模式名:": "Profile name:",
    "内核快照:": "Kernel snapshot:",
    "依赖模式:": "Dependency mode:",
    "L1=PYTHONPATH overlay（推荐）；L2=轻 venv 共享大件（冲突时兜底）":
        "L1 = PYTHONPATH overlay (recommended); L2 = light venv sharing big packages (fallback for conflicts)",
    "勾选要装进该模式的插件（核心节点无需安装）":
        "Pick the plugins to include in this profile (core nodes need no install)",
    "⚡ 创建启动模式": "⚡ Create profile",
    "⚡ 启动模式 · 快照隔离": "⚡ Startup profiles · snapshot isolation",
    "基础内核只存一份快照；每个工作流/插件集一个启动模式，插件与依赖互相隔离、模型全局共享":
        "One kernel snapshot; one profile per workflow/plugin set — plugins and deps isolated, models shared globally",
    "🧬 从工作流创建…": "🧬 Create from workflow…",
    "新建空白…": "New blank…",
    "复制…": "Duplicate…",
    "🔧 内核管理…": "🔧 Kernel manager…",
    "刷新模式列表": "Refresh profile list",
    "L1=PYTHONPATH overlay 压过共享包（推荐）；L2=轻 venv 共享 torch 大件（依赖冲突兜底）":
        "L1 = PYTHONPATH overlay overrides shared packages (recommended); L2 = light venv sharing torch (fallback for conflicts)",
    "隔离方式:": "Isolation:",
    "显存优化:": "VRAM optimization:",
    "额外参数:": "Extra arguments:",
    "空 = 全局共享模型目录": "Empty = globally shared models folder",
    "模型目录:": "Models folder:",
    "已装插件:": "Installed plugins:",
    "拷入工作流 JSON": "Copy workflow JSON in",
    "移除绑定（不删文件）": "Unbind (keeps files)",
    "绑定工作流:": "Bound workflow:",
    "备注:": "Note:",
    "💾 保存修改": "💾 Save changes",
    "💾 保存修改 *": "💾 Save changes *",
    "🩺 体检": "🩺 Health check",
    "临时端口起内核，核对绑定工作流节点是否全可加载":
        "Start the kernel on a temp port and verify every node in the bound workflow loads",
    "⏹ 停止": "⏹ Stop",

    # ================= 工作流 =================
    "🧬 工作流 · 智能安装": "🧬 Workflows · smart install",
    "⚡ 启动模式": "⚡ Startup profiles",
    "来源:": "Source:",
    "—— 从当前实例的工作流目录选择 ——": "—— pick from the current instance workflows ——",
    "刷新实例工作流列表": "Refresh instance workflow list",
    "浏览本地…": "Browse local…",
    "🔍 扫描依赖": "🔍 Scan dependencies",
    "💡 直接把 .json 工作流文件拖进窗口即可自动扫描；也可用上方下拉或「浏览本地…」选择":
        "💡 Drag a .json workflow into the window to scan automatically, or use the dropdown / “Browse local…”",
    "尚未生成计划": "No plan yet",
    "同时下载模型（有直链的才有效）": "Download models too (only those with direct links)",
    "⚡ 执行安装计划": "⚡ Run install plan",
    "停止": "Stop",
    "导入步骤清单…": "Import step list…",
    "🤖 AI 补全并执行": "🤖 AI complete & run",
    "按「内核 → 插件 → 模型」顺序补齐扫描出的全部缺口，执行前自动快照":
        "Fill every detected gap in order (kernel → plugins → models); a snapshot is taken first",
    "（实例工作流目录为空）": "(instance workflow folder is empty)",
    "⬇️ 下载": "⬇️ Download",

    # ================= 批量运行 =================
    "把同一个工作流连续跑多次；可设置每跑 N 次重启实例，释放显存碎片防 OOM。\n工作流需为 API 格式（ComfyUI 里 Workflow → Export (API)）。":
        "Run one workflow repeatedly; restart the instance every N runs to release VRAM "
        "fragmentation and avoid OOM.\nThe workflow must be in API format "
        "(Workflow → Export (API) in ComfyUI).",
    "选择工作流 JSON…": "Choose workflow JSON…",
    "实例未运行时自动启动": "Auto-start the instance if stopped",
    "▶ 开始": "▶ Start",
    "■ 停止": "■ Stop",
    "关闭": "Close",
    "工作流:": "Workflow:",
    "执行次数:": "Runs:",
    "每": "every",
    "次重启实例（0 = 不重启）": "runs restart the instance (0 = never)",

    # ================= 下载中心 =================
    "排队中": "Queued",
    "下载任务": "Downloads",
    "暂无下载任务": "No download tasks",

    # ================= 实例对话框 =================
    "例如: --reserve-vram 2 --fast": "e.g. --reserve-vram 2 --fast",
    "服务就绪后自动打开浏览器": "Open the browser once the server is ready",
    "实例名称:": "Instance name:",
    "ComfyUI 目录:": "ComfyUI directory:",
    "Python 路径:": "Python path:",
    "端口:": "Port:",
    "显存模式:": "VRAM mode:",
    "附加参数:": "Extra arguments:",
    "该目录下没有 main.py，不是有效的 ComfyUI 根目录。":
        "No main.py in that folder — not a valid ComfyUI root.",
    "请填写 Python 解释器路径。": "Please provide the Python interpreter path.",

    # ================= 安装向导 =================
    "正在安装环境": "Installing environment",
    "准备中…": "Preparing…",
    "跳过此步 ▶": "Skip this step ▶",
    "修复模式：检测 → 补装 → 复验（自动连跑）…":
        "Repair mode: detect → install → re-verify (runs automatically)…",
    "全新安装（空目录）：自动执行全部步骤…":
        "Fresh install (empty folder): running all steps…",
    "执行完成，正在复验…": "Done, re-verifying…",
    "全部步骤完成 —— 正在进行最终验证…": "All steps done — running final verification…",
    "全部步骤完成，最终验证通过 ✅": "All steps done, final verification passed ✅",
    "一键安装 ComfyUI 环境": "One-click ComfyUI environment setup",
    "选择安装位置": "Choose install location",
    "安装到:": "Install to:",
    "不填则使用安装目录名": "Empty = use the install folder name",
    "仍要添加（跳过修复，稍后到主界面手动处理）":
        "Add anyway (skip repair, handle it later in the main UI)",
    "检测到:": "Detected:",
    "➕ 选择新增 Python 解释器…（如其他实例的 python_embeded）":
        "➕ Add another Python interpreter… (e.g. another instance's python_embeded)",
    "Python 环境:": "Python environment:",
    "留空 = 自动选择（共享便携 Python）；也可浏览选择已有 python.exe":
        "Empty = auto (shared portable Python); or browse for an existing python.exe",
    "[共享 Python]": "[shared Python]",
    "解释器路径:": "Interpreter path:",
    "留空直连；GitHub 下载慢可填 https://ghproxy.net/ 等":
        "Empty = direct; if GitHub is slow use https://ghproxy.net/ etc.",
    "自动安装 PyTorch（按显卡自动选 CUDA/CPU 版，约 1.5~2.5GB）":
        "Install PyTorch automatically (CUDA/CPU picked by GPU, ~1.5–2.5GB)",
    "正在扫描，请稍候…": "Scanning, please wait…",
    "🔗 已选择跳过修复：点「仍要添加」直接注册（之后到主界面「依赖管理」再补）":
        "🔗 Repair skipped: click “Add anyway” to register (fix deps later in Dependencies)",
    "🔗 该实例缺依赖：点「下一步：修复」自动补装后再验证":
        "🔗 Missing dependencies: click “Next: Repair” to install and re-verify",
    "重新安装": "Reinstall",
    "最终验证执行失败": "Final verification failed to run",
    "🔄 重新验证": "🔄 Re-verify",
    "最终验证未通过": "Final verification did not pass",
    "[共享] 使用 ComfyHub 共享便携 Python（已安装，实例共用）":
        "[shared] Use ComfyHub's shared portable Python (installed, shared by instances)",
    "[共享] 重新安装便携 Python（覆盖共享版本，可改安装路径）":
        "[shared] Reinstall portable Python (overwrites the shared copy, path can change)",
    "[共享] 自动安装便携 Python（推荐，不污染系统）":
        "[shared] Install portable Python automatically (recommended, no system pollution)",
    "➕ 新建新实例…": "➕ New instance…",
    "🔗 添加已有实例（验证通过，点「完成」直接注册，不会下载任何东西）":
        "🔗 Add existing instance (verified — click Finish to register, nothing is downloaded)",
    "🔗 该实例缺少 Python 环境：点「下一步：修复」自动补装":
        "🔗 No Python environment: click “Next: Repair” to install it",
    "✅ 安装完成！点击“完成”自动添加为实例。":
        "✅ Install complete! Click “Finish” to register it as an instance.",
    "📦 安装": "📦 Install",
    "📦 全新安装（将在下方选定位置创建环境）":
        "📦 Fresh install (creates the environment at the location below)",
    "请选择实例:": "Choose an instance:",
    "✅ 修复完成——正在返回并重新验证实例…":
        "✅ Repair done — returning and re-verifying the instance…",
    "🔧 强制重装": "🔧 Force reinstall",
    "⬆ 更新": "⬆ Update",
    "跳过（保留现状）▶": "Skip (keep as is) ▶",
    "🔄 重试": "🔄 Retry",

    # ================= 模型市场（文件行） =================
    "文件行「⬇ 下载」走直链直传（国内网络可能超时，失败可点「🔗 复制」拿直链用工具下载）；下方自动检索 HF 镜像社区同步仓库兜底：":
        "“⬇ Download” streams over the direct link (may time out in China; if it fails, "
        "use “🔗 Copy” with an external downloader). HF mirror repos are searched below as a fallback:",
    "CivitAI 原始文件（⬇ 直链下载 / 🔗 复制直链）":
        "CivitAI original files (⬇ direct download / 🔗 copy link)",
    "HF 镜像同名同步仓库（可下载）": "Same-name synced repos on HF mirrors (downloadable)",
    "正在搜索 HF 镜像…": "Searching HF mirrors…",

    # ================= 环境检测说明 =================
    "检测 Git，插件安装/更新的基础设施": "Checks Git — required for plugin install/update",
    "管理当前实例的 Python 运行环境": "Manages the Python runtime of the current instance",
    "深度学习框架的版本检测": "Deep-learning framework version check",
    "检测 PyTorch 是否可用 GPU 加速": "Checks whether PyTorch can use GPU acceleration",
    "显卡": "GPU",
    "检测显卡型号与显存": "Checks GPU model and VRAM",
    "显卡驱动": "GPU driver",
    "检测驱动版本，判断支持的 CUDA 上限":
        "Checks the driver version to infer the max supported CUDA",
    "处理器 (CPU)": "CPU",
    "处理器型号": "CPU model",

    # ================= ComfyUI 实例设置项 =================
    "内核版本管理": "Kernel version management",
    "一键更新到最新稳定版，或按版本回滚（切换后自动安装配套依赖）":
        "One-click update to the latest stable, or roll back to a tag (dependencies install automatically)",
    "实例名称": "Instance name",
    "实例显示名称": "Display name of the instance",
    "根目录": "Root folder",
    "包含 main.py 的 ComfyUI 目录": "ComfyUI folder containing main.py",
    "模型目录": "Models folder",
    "checkpoints/loras/vae 等模型的存放位置":
        "Where checkpoints/loras/vae and friends are stored",
    "Python 路径": "Python path",
    "实例配套的解释器（建议用独立 venv）":
        "Interpreter for this instance (a dedicated venv is recommended)",
    "启动端口": "Launch port",
    "用于访问 ComfyUI 的 HTTP 端口（默认 8188）":
        "HTTP port for ComfyUI (default 8188)",
    "生成引擎": "Compute engine",
    "选择参与计算的硬件引擎：CUDA（NVIDIA 加速）/ DML（DirectML，AMD/Intel 可用）/ CPU（仅调试）":
        "Pick the compute backend: CUDA (NVIDIA) / DML (DirectML for AMD/Intel) / CPU (debug only)",
    "CPU/显存优化": "CPU/VRAM optimization",
    "按显存大小选择优化级别（低配选低显存）":
        "Optimization level by VRAM size (pick low VRAM on small cards)",
    "Cross Attention 优化": "Cross-attention optimization",
    "注意力算法：优先 xFormers（需环境已装），未装回落 SDP；Sage 需装 sageattention；省显存选 Quad":
        "Attention backend: xFormers if installed, else SDP; Sage needs sageattention; "
        "choose Quad to save VRAM",
    "采样预览": "Live preview",
    "出图过程中的实时预览画质（自动=ComfyUI 自己选；TAESD 更清晰但略耗性能）":
        "Preview quality while sampling (Auto = ComfyUI decides; TAESD is sharper but slower)",
    "计算精度": "Precision",
    "平衡性能与显存占用，缓解精度带来的黑图问题":
        "Balances speed and VRAM use; helps avoid black images caused by precision issues",
    "快速模式（激进提速）": "Fast mode (aggressive speed-up)",
    "启用 PyTorch 加速技巧，以一定质量代价换速度（新显卡提速明显，老卡慎用）":
        "Enables PyTorch speed tricks at some quality cost (great on new GPUs, risky on old ones)",
    "禁用智能显存（共享显存）": "Disable smart VRAM (shared memory)",
    "显存紧张/爆显存时开启，稍降速度换稳定（关闭可避免内存弥补导致的性能降级）":
        "Enable when VRAM is tight or OOM occurs — slightly slower but stable "
        "(disable to avoid slowdown from shared memory fallback)",
    "CPU 运行 VAE": "Run VAE on CPU",
    "显著降低峰值显存消耗，但会降低整体速度":
        "Greatly lowers peak VRAM use, but slows everything down",
    "稳定计算": "Deterministic",
    "使用速度较慢的确定性算法，保证结果可复现（可能无法让图像在所有情况下一致）":
        "Uses slower deterministic algorithms for reproducibility "
        "(images may still not be identical in every case)",
    "Channels-Last 优化": "Channels-Last",
    "改用 channels_last 内存格式，部分场景有推理提速（对性能影响未知）":
        "Uses the channels_last memory format; may speed up some cases (impact varies)",
    "CUDA 内存分配方案": "CUDA allocator",
    "更换底层 CUDA 内存分配器（内置异步分配器需 CUDA 11.4+）":
        "Switches the CUDA memory allocator (the built-in async one needs CUDA 11.4+)",
    "多用户模式": "Multi-user mode",
    "允许多个用户同时使用一个实例（每人独立存储空间）":
        "Let several users share one instance (separate storage per user)",
    "局域网访问": "LAN access",
    "允许同一局域网其他设备（手机/平板）打开 ComfyUI":
        "Let other devices on the LAN (phone/tablet) open ComfyUI",
    "Huggingface 离线模式": "Hugging Face offline mode",
    "关闭从 Huggingface 联网加载模型，启用后无法在线下载本地缺失的模型":
        "Disables online model loading from Hugging Face; missing models can't be downloaded while on",
    "自动打开": "Auto open",
    "启动成功后自动用浏览器打开 ComfyUI 界面":
        "Open the ComfyUI UI in the browser once it starts",
    "启动参数": "Launch arguments",
    "附加的命令行参数（空格分隔的高阶用法）":
        "Extra command-line arguments (space separated, advanced)",

    # ================= 工作台目录卡 =================
    "🗂️  根目录": "🗂️  Root",
    "实例根目录": "Instance root folder",
    "🧠  模型 models": "🧠  Models",
    "🧩  插件 custom_nodes": "🧩  Plugins (custom_nodes)",
    "📥  输入 input": "📥  Input",
    "📤  输出 output": "📤  Output",
    "🧬  工作流 workflows": "🧬  Workflows",

    # ================= 模型市场筛选 =================
    "本地": "Local",
    "HF 镜像": "HF mirror",
    "HF 官方": "HF official",
    "全部": "All",
    "搜索 HuggingFace 国内镜像：sdxl / flux lora / vae…（回车）":
        "Search the HuggingFace mirror: sdxl / flux lora / vae… (Enter)",

    # ================= 表格表头 =================
    "模式名": "Profile",
    "内核": "Kernel",
    "依赖": "Deps",
    "端口": "Port",
    "插件": "Plugins",
    "占用MB": "MB used",
    "状态": "Status",
    "条目": "Item",
    "来源/仓库": "Source/Repo",
    "操作": "Actions",
    "依赖名": "Package",
    "版本号": "Version",
    "类型": "Type",
    "名称（时间_备注）": "Name (time_note)",
    "下载量": "Downloads",
    "作者": "Author",
    "说明": "Description",
    "插件名": "Plugin",
    "Git 地址": "Git URL",
    "分支": "Branch",

    # ================= 长说明 =================
    "便携 Python / MinGit / PyTorch 的安装位置，留空用默认。修改后旧目录原样保留（不会自动搬迁），下次装机/修复生效。⚠️ 自定义路径不在卸载器清理范围，删该目录即卸载工具。":
        "Where portable Python / MinGit / PyTorch are installed; leave empty for the default. "
        "The old folder is kept after changing (nothing is moved) and the new path applies on the "
        "next install/repair. ⚠️ Custom paths are not cleaned by the uninstaller — deleting that "
        "folder uninstalls the tools.",
    "用于「未识别节点 / 无直链模型」的疑难推断，结果标『🤖 待验证』需人工确认后再用。国内推荐 DeepSeek / 智谱 / 通义（均为 OpenAI 兼容接口）。不配置则完全走本地确定性识别，不影响其他功能。":
        "Used for tricky cases (unknown nodes / models without direct links); results are marked "
        "“🤖 unverified” and need manual confirmation. DeepSeek / Zhipu / Tongyi are recommended "
        "in China (all OpenAI-compatible). Leave empty to rely purely on local deterministic "
        "detection — other features are unaffected.",
    "ComfyHub v0.8.06\nPySide6 轻量 ComfyUI 多实例管理器（46MB 便携包）\n\nPython 3.13 · PySide6 · MIT":
        "ComfyHub v0.8.06\nPySide6 lightweight ComfyUI multi-instance manager (46MB portable build)\n\n"
        "Python 3.13 · PySide6 · MIT",

    # ================= 首页系统卡拼装词 =================
    "内存": "Memory",
    "处理器": "CPU",
    "F 盘空间": "Disk F:",
    "占用": "Used",
    "核": "c",
    "线程": "t",
    "驱动": "Driver",
    "推荐": "recommended",
    "显存未知": "VRAM unknown",
    "未检测到": "not detected",
    "可用": "free",
    "共": "of",

    # ================= 其余表头 =================
    "版本": "Version",
    "更新时间": "Updated",
    "版本 ID": "Version ID",
    "路径": "Path",
    "基座标志": "Base flag",
    "名称": "Name",
    "大小": "Size",
    "时间": "Time",
    "备注": "Note",
    "状态/备注": "Status/Note",
    "路径/说明": "Path/Note",
    "ComfyUI 目录": "ComfyUI folder",

    # ================= 杂项 =================
    '启用': 'Enabled',
    '更新': 'Update',
    '打开': 'Open',
    '类别': 'Category',
    '文件数': 'Files',
    '总体积': 'Total size',
    '实例': 'Instance',
    # 日志图例（整段 HTML 做键，避免拆散样式）
    "<span style='color:#e8635a;'>■ 错误</span>&nbsp;&nbsp;<span style='color:#e8a33d;'>■ 警告</span>&nbsp;&nbsp;<span style='color:#7fa8d9;'>■ 信息</span>&nbsp;&nbsp;<span style='color:#4cd47c;'>■ 成功/出图</span>&nbsp;&nbsp;<span style='color:#e8871e;'>■ ComfyHub</span>": "<span style='color:#e8635a;'>■ Error</span>&nbsp;&nbsp;<span style='color:#e8a33d;'>■ Warning</span>&nbsp;&nbsp;<span style='color:#7fa8d9;'>■ Info</span>&nbsp;&nbsp;<span style='color:#4cd47c;'>■ Success/Image</span>&nbsp;&nbsp;<span style='color:#e8871e;'>■ ComfyHub</span>",

    # ================= 实例前缀拼装 =================
    "（未选择）": "(none selected)",
    "检测完成": "check finished",
}
