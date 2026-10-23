; ============================================================
; ComfyHub 安装程序（NSIS 3.11 + MUI2 + 暗色主题）
; 许可：本脚本原创，遵循项目 MIT 许可
; 编译：makensis ComfyHub.nsi
; ============================================================
Unicode true

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; ---------- 基本信息 ----------
!define APP_NAME       "ComfyHub"
!define APP_NAME_CN    "ComfyHub 多实例管理器"
!define APP_VERSION    "0.7.4"
!define APP_PUBLISHER  "LeiMengStudio"
!define APP_URL        "https://gitee.com/yzdydwdd/ComfyHub"
!define APP_EXE        "ComfyHub.exe"
!define UNINST_KEY     "Software\Microsoft\Windows\CurrentVersion\Uninstall\ComfyHub"
!define SRC_DIR        "..\dist\ComfyHub"
!define OUT_FILE       "..\dist\ComfyHub_Setup_${APP_VERSION}.exe"

Name "${APP_NAME_CN}"
BrandingText "${APP_NAME_CN} v${APP_VERSION}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "0.7.4.0"
VIAddVersionKey "ProductName"     "${APP_NAME_CN}"
VIAddVersionKey "FileDescription" "${APP_NAME_CN} 安装程序"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey "LegalCopyright"  "MIT License"

; ---------- 界面：暗色主题 ----------
!define MUI_ABORTWARNING
!define MUI_ICON   "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "..\assets\installer\header.bmp"
!define MUI_HEADERIMAGE_RIGHT
!define MUI_WELCOMEFINISHPAGE_BITMAP "..\assets\installer\welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "..\assets\installer\welcome.bmp"
!define MUI_BGCOLOR "0F141B"
!define MUI_TEXTCOLOR "FFFFFF"
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 ${APP_NAME_CN}"
!define MUI_WELCOMEPAGE_TEXT  "这个向导将引导你完成 ComfyHub 的安装。$\r$\n$\r$\nComfyHub 是 ComfyUI 多实例管理器：一键装环境、插件市场、启动模式、批量任务、任务通知。$\r$\n$\r$\n安装到当前用户目录，无需管理员权限。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 ${APP_NAME}"   
!define MUI_LICENSEPAGE_CHECKBOX            ; 用勾选框，必须打勾才能下一步
!define MUI_LICENSEPAGE_CHECKBOX_TEXT "我已阅读并同意以上条款，愿意承担相应使用责任"

!insertmacro MUI_PAGE_WELCOME
; 用户同意书：必须勾选"我接受"才能继续
!insertmacro MUI_PAGE_LICENSE "..\docs\DISCLAIMER_unicode.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ---------- onInit：读回安装路径 + 处理运行中的实例 ----------
Function .onInit
    ReadRegStr $0 HKCU "Software\${APP_NAME}" "InstallDir"
    ${If} $0 != ""
        StrCpy $INSTDIR $0
    ${EndIf}

    ; 用 CSV 输出判断进程：前 13 字符是否为 "ComfyHub.exe"
    ; （不比较 tasklist 的提示语，那会随系统语言变化而误判）
    nsExec::ExecToStack 'tasklist /FI "IMAGENAME eq ${APP_EXE}" /NH /FO CSV'
    Pop $0
    Pop $1
    StrCpy $2 $1 13
    ${If} $2 == '"${APP_EXE}"'
        IfSilent silentKill 0
        MessageBox MB_YESNO|MB_ICONQUESTION \
            "${APP_NAME} 正在运行。安装前需要先关闭它。$\r$\n$\r$\n现在自动结束它并继续？" \
            IDYES silentKill IDNO abortInstall
        abortInstall:
            Abort
        silentKill:
            nsExec::ExecToStack 'taskkill /IM ${APP_EXE} /F'
            Pop $0
            Pop $1
            Sleep 1500
    ${EndIf}
FunctionEnd

; ---------- 安装内容（不再让用户勾选：全装）----------
Section "ComfyHub 主程序" SEC_CORE
    SectionIn RO
    SetOutPath "$INSTDIR"
    File /r "${SRC_DIR}\*.*"
    File "..\LICENSE"
    File "..\THIRD_PARTY_LICENSES.txt"
    File "..\docs\CLI.md"
    File "..\docs\DISCLAIMER_unicode.txt"
    WriteRegStr HKCU "Software\${APP_NAME}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\${APP_NAME}" "Version" "${APP_VERSION}"
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME_CN}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
    WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "${APP_URL}"
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "快捷方式与文档"
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
    CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
SectionEnd

Section "文档"
    SetOutPath "$INSTDIR\docs"
    File "..\docs\COMPLIANCE.md"
    File "..\docs\COMPETITORS.md"
    SetOutPath "$INSTDIR"
SectionEnd

; ---------- 卸载 ----------
Function un.onInit
    ; 不再询问"确定要卸载吗"——从控制面板主动卸载已是明确意图
    ; 唯一的询问是卸载末尾的"是否同时删除配置目录"（默认删除）
FunctionEnd

Section "Uninstall"
    ; 结束进程并**等待它真正退出**（否则文件句柄未释放，删除会静默失败）
    nsExec::ExecToStack 'taskkill /IM ${APP_EXE} /F'
    Pop $0
    StrCpy $1 0
    wait_loop:
        Sleep 500
        IntOp $1 $1 + 1
        nsExec::ExecToStack 'tasklist /FI "IMAGENAME eq ${APP_EXE}" /NH /FO CSV'
        Pop $0
        Pop $2
        StrCpy $3 $2 13
        ${If} $3 == '"${APP_EXE}"'
        ${AndIf} $1 < 20
            Goto wait_loop
        ${EndIf}
        Sleep 300        ; 再给句柄释放留一点时间

    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\Uninstall.exe"
    Delete "$INSTDIR\LICENSE"
    Delete "$INSTDIR\THIRD_PARTY_LICENSES.txt"
    Delete "$INSTDIR\CLI.md"
    Delete "$INSTDIR\DISCLAIMER_unicode.txt"
    ; ★ 大量小文件（Qt _internal 有数千个）用系统 rd 批量删比 RMDir 快几十倍
    ;   （实测 NSIS 的 RMDir /r 删 118MB 要 5 分钟，rd 只要几秒）
    nsExec::ExecToStack 'rd /s /q "$INSTDIR\_internal"'
    Pop $0
    Sleep 200
    RMDir /r /REBOOTOK "$INSTDIR\_internal"    ; rd 失败时兜底
    RMDir /r /REBOOTOK "$INSTDIR\docs"
    RMDir "$INSTDIR"

    Delete "$DESKTOP\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"

    DeleteRegKey HKCU "${UNINST_KEY}"
    DeleteRegKey HKCU "Software\${APP_NAME}"

    ; ---- 配置目录：默认删除（用户要求），选"否"才保留 ----
    IfFileExists "$APPDATA\ComfyHub\*.*" 0 cfgDone
    IfSilent delCfg 0                          ; 静默模式 = 默认清理
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 \
        "是否同时删除配置目录？（含实例配置、启动模式、运行日志）" \
        IDYES delCfg IDNO keepCfg
    keepCfg:
        MessageBox MB_OK|MB_ICONINFORMATION \
            "配置目录已保留：$\r$\n$APPDATA\ComfyHub$\r$\n$\r$\n重装后无需重新配置。"
        Goto cfgDone
    delCfg:
        nsExec::ExecToStack 'rd /s /q "$APPDATA\ComfyHub"'
        Pop $0
        RMDir /r /REBOOTOK "$APPDATA\ComfyHub"
    cfgDone:
SectionEnd
