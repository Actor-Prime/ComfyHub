# ComfyHub 命令行模式（CLI）

无 GUI，适合服务器 / 自动化 / 定时任务。退出码：`0` 成功 · `1` 一般错误 · `2` 参数错误 · `3` 实例未运行 · `4` 超时

## 命令

```bash
ComfyHub.exe                                  # 无参数 → 启动图形界面
ComfyHub.exe list [--json]                    # 列出所有实例及运行状态
ComfyHub.exe info   [--json]                  # 查看生效配置与完整启动命令
ComfyHub.exe status [--json]                  # 运行状态（running / ready / PID）
ComfyHub.exe start  [--wait] [--timeout 180]  # 启动实例；--wait 等到 /system_stats 就绪
ComfyHub.exe stop                             # 停止实例
ComfyHub.exe logs   [-n 100]                  # 查看实例日志尾部
ComfyHub.exe batch  --workflow X.json --times 10 [--restart-every 5] [--autostart]
```

实例选择：`--id 0`（序号，见 list）或 `--name 主实例`；都不给则用上次使用的实例。

## 批量任务（防 OOM）

```bash
# 同一工作流跑 20 次，每 5 次重启 ComfyUI 释放显存碎片
ComfyHub.exe batch --workflow flux_api.json --times 20 --restart-every 5 --autostart
```

工作流必须是 **API 格式**（ComfyUI 里 `Workflow → Export (API)`）。
前端界面工作流（含 `nodes`/`links`）会被拒绝并提示正确导出方式。

## 脚本示例

```bash
:: 每天早上 8 点启动并检查
ComfyHub.exe start --wait --timeout 300
if errorlevel 1 echo 启动失败

:: 定时重启释放显存
ComfyHub.exe stop && timeout /t 5 && ComfyHub.exe start --wait
```

## 日志位置

- 实例运行日志：`%APPDATA%\ComfyHub\logs\instance_<id>.log`
- UI 操作审计：`%APPDATA%\ComfyHub\logs\ui_YYYYMMDD.log`
- 崩溃黑匣子：`%APPDATA%\ComfyHub\crash.log`
