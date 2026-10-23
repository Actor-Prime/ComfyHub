# ComfyHub 项目结构说明

## 目录树

```
ComfyHub/
├── main.py                  # 程序入口（构建 QApplication、加载主题、启动主窗口）
├── requirements.txt         # 开发/构建依赖（运行时不依赖这些，启动器是自包含 exe）
├── README.md                # 项目说明
├── LICENSE                  # 许可证
├── DISCLAIMER.txt           # 免责声明
├── .gitignore
│
├── app/                     # 应用源码
│   ├── bootstrap.py         # 环境引导核心：下载/解压/装 Python/依赖/torch/验证（无 UI 依赖）
│   ├── config.py            # 配置读写、版本号、常量、实例判定 is_comfyui_dir()
│   ├── installer.py         # 环境快照 / 备份还原 / 救援
│   ├── instance.py          # 实例数据模型（增删改查、端口分配）
│   ├── process.py           # ComfyUI 进程启动/停止/日志采集
│   ├── proc_manager.py      # 进程管理器（多实例调度）
│   ├── sysinfo.py           # 硬件探测（显卡/显存/驱动 → 推荐 torch 变体）
│   ├── tools.py             # 外部工具探测（git / 便携 Python / 便携 Git）
│   ├── workflow_scan.py     # 工作流节点扫描
│   ├── workers.py           # QThread Worker 统一封装（swap_worker / wait_all）
│   ├── winsub.py            # 子进程无窗口封装（CREATE_NO_WINDOW）
│   ├── log.py               # 崩溃黑匣子、防御性日志
│   └── ui/                  # 界面层
│       ├── theme.py         # 全局 QSS 主题
│       ├── nav.py           # 侧栏导航项枚举
│       ├── main_window.py   # 主窗口（导航 + 页面栈 + 托盘监工）
│       ├── setup_wizard.py  # 一键安装向导（4 步步进制）
│       ├── dialogs.py       # 通用对话框
│       └── pages/           # 各功能页
│           ├── home_page.py
│           ├── comfyui_page.py
│           ├── instance_page.py
│           ├── tools_page.py
│           ├── env_page.py
│           ├── models_page.py
│           ├── plugins_page.py
│           ├── workflow_page.py
│           ├── console_page.py
│           └── settings_page.py
│
├── assets/                  # 图标与图片资源（打包时随包携带）
├── docs/                    # 设计与开发文档
├── packaging/               # 打包配置
│   ├── ComfyHub.spec        # PyInstaller 配置
│   ├── build_exe.bat        # 一键打包便携 exe
│   └── installer.iss        # Inno Setup 安装包脚本
└── tests/                   # 自动化验证脚本（不参与打包）
    ├── smoke.py             # 冒烟测试（窗口构造 + 控件抽查）
    ├── e2e_install.py       # 端到端安装流程（带 faulthandler 抓 C 层崩溃）
    ├── verify_parent_scan.py# 父目录扫描 + 路径同步验证
    ├── sim_core_flow.py     # 安装链路模拟（源码→依赖→torch→可运行验证）
    ├── sim_new_instance.py  # 新建实例全流程
    └── sim_run_instance.py  # 启动实例 + HTTP 探活
```

## 分层原则

| 层 | 目录 | 约束 |
|---|---|---|
| 业务核心 | `app/*.py`（除 ui/） | **不得 import PySide6**，可在无 GUI 环境运行与测试 |
| 界面 | `app/ui/` | 只做展示与交互，重活交给 Worker |
| 打包 | `packaging/` | 只放构建相关，不入应用包 |
| 验证 | `tests/` | 可随时删除，不影响功能 |

## 开发命令

```bash
# 依赖（开发环境）
pip install -r requirements.txt

# 本地运行（源码）
python main.py

# 冒烟测试
python tests/smoke.py

# 打包便携版
packaging\build_exe.bat
# 或手动：pyinstaller --noconfirm --windowed --name ComfyHub --icon assets/icon.ico ^
#         --add-data "assets;assets" --hidden-import PySide6.QtSvg ^
#         --distpath dist --workpath build main.py
```

## 关键约定（踩坑总结）

1. **Qt 控件只能在主线程操作**：后台线程一律通过 `Signal` 更新 UI（`_LogPage.sig_ui_log`）。
2. **探测零副作用**：扫描/检测路径时**绝不创建文件夹**，创建只发生在点「开始安装」之后。
3. **Worker 三件套**：创建 → `sig_done.connect(...)` → `start()`，缺一必卡死；统一走 `_start_scan()` 等方法。
4. **不允许在信号回调栈内 disconnect 正在发信号的 sender**（用 `QTimer.singleShot(0, ...)` 延迟）。
5. **长任务必须流式输出**（pip 逐行、下载每 2 秒进度），否则"正常运行"与"卡死"无法区分。
6. **有状态的下载不做激进切换**：选定源下到底，只有真正失败才换源，避免进度回退死循环。
7. **子进程必须加 `CREATE_NO_WINDOW`**，否则打包版会弹黑框。
8. **C 层崩溃靠 `faulthandler`**：`crash.log` 会记录调用栈，不要靠猜。
