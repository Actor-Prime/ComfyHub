; ============================================================
; ComfyHub Windows 安装包脚本（Inno Setup 6）
; 编译方法：安装 Inno Setup 6 后，用 ISCC 编译本文件，
;           或在 Inno Setup GUI 中打开本文件按 F9。
; 前置：先运行 build_exe.bat 生成 dist\ComfyHub\ 目录。
; ============================================================

#define MyAppName "ComfyHub"
#define MyAppVersion "0.5.84"   ; 与 app/config.py 的 APP_VERSION 保持一致
#define MyAppPublisher "Starmore"
#define MyAppExeName "ComfyHub.exe"

[Setup]
AppId={{8E6F3C2A-91B4-4E5D-9C77-2A1F0D6B5C01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 普通用户权限即可安装到 Program Files 需管理员；这里选择低权限安装到用户目录，免 UAC
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=ComfyHub-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 中文界面
ShowLanguageDialog=auto

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
; 打包整个 PyInstaller 产物目录
Source: "dist\ComfyHub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理日志等运行时残留（用户配置保留在 %APPDATA%\ComfyHub）
Type: filesandordirs; Name: "{app}"
