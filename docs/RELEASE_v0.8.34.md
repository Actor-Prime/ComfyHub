# ComfyHub v0.8.34

## 新增

- **界面状态改由数据目录保存**：模型页筛选、AI 页右栏开合这些偏好原来写在注册表 `HKCU\Software\LeiMengStudio\ComfyHub`，现在改存 `%APPDATA%\ComfyHub\ui_state.ini`（便携模式跟着 `data\` 目录走）。老用户升级后首次启动自动迁移旧值，迁移完把旧注册表键删掉。

## 优化

- **新建实例向导不再写死 F 盘**：自动探测改成按本机实际存在的盘符生成候选路径（`X:\ComfyUI`、`X:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI`、`X:\AI\ComfyUI`）。官方桌面版装在 D/E 盘的机器以前扫不到，现在能认出来。
- 英文界面补齐 13 条漏译（AI 页示例文案、插件依赖自检整套按钮、背景模糊、本地模型提示）。

## 修复

- **修卸载后注册表残留**：卸载脚本此前只删了安装器自己写的 `Software\ComfyHub`，程序运行期写的 `Software\LeiMengStudio\ComfyHub` 一直留着，Geek Uninstaller 这类工具会报「发现残留痕迹」。现在两处都删，空的发布者父键也一并收掉（同发布者将来有别的产品不会被误删）；Windows 兼容性助手留下的 `AppCompatFlags\...\Store` 条目也在卸载末尾清掉 —— 那条放前面删会被系统重新写回。
- **修安装向导「完成」按钮点不下去**：扫描/验证还没结束时按钮看着能点却没反应。现在未完成时直接隐藏，按钮位显示「准备中…」，扫描通过才换回「完成」。

---

**给已经卸载过的用户**：残留是旧版卸载程序留下的，装完这版再卸一次就干净了；不想重装可以直接删注册表项 —— `reg delete "HKCU\Software\LeiMengStudio" /f`。
