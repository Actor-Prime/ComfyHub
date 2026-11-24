@echo off
chcp 65001 > nul
title ComfyHub 打包 - PyInstaller
rem This script lives in packaging\ -- cd to the project root first;
rem every path below is relative to the root.
cd /d "%~dp0.."
rem Pause only for interactive runs: CI sets CI=1, and a paused script
rem in CI blocks the agent/runner until it is killed.

echo ============================================
echo   ComfyHub 便携版打包（PyInstaller onedir）
echo ============================================
echo.

if not exist .venv_build (
    echo [1/3] 创建打包用虚拟环境...
    python -m venv .venv_build
    .venv_build\Scripts\python.exe -m pip install -q --upgrade pip
    .venv_build\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
) else (
    echo [1/3] 复用已有打包环境 .venv_build
)

echo [2/3] PyInstaller 打包中（约 2~5 分钟）...
rem Clean the output dir first: a ComfyHub.exe still running locks its files.
taskkill /IM ComfyHub.exe /F > nul 2>&1
if exist dist\ComfyHub (
    timeout /t 2 /nobreak > nul
    rd /s /q dist\ComfyHub 2> nul
)
rem 0.8.40 (remote ops, paramiko): cryptography's _rust*.pyd, bcrypt's
rem _bcrypt*.pyd and OpenSSL's libcrypto-*.dll are Rust/cffi extension binaries.
rem UPX-compressing them makes the frozen app die with "DLL load failed", which
rem never reproduces in dev mode -- extremely hard to diagnose. The three
rem --upx-exclude flags below are mandatory; do not delete them to save a few MB
rem (PyInstaller defaults to upx=True).
rem NOTE: this is ONE command line continued with ^, so no rem line may be
rem inserted in the middle -- the rationale lives here.
rem NOTE: keep this whole file ASCII-only in rem lines. cmd re-reads batch lines
rem with the OEM code page, and UTF-8 CJK bytes there can be decoded into
rem stray & or | characters, silently splitting the command line.
.venv_build\Scripts\pyinstaller.exe ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name ComfyHub ^
    --icon "%~dp0..\assets\icon.ico" ^
    --add-data "%~dp0..\assets;assets" ^
    --hidden-import PySide6.QtSvg ^
    --upx-exclude "_rust*.pyd" ^
    --upx-exclude "_bcrypt*.pyd" ^
    --upx-exclude "libcrypto-*.dll" ^
    --distpath "%~dp0..\dist" ^
    --workpath "%~dp0..\build" ^
    --specpath "%~dp0..\build" ^
    "%~dp0..\main.py"

if errorlevel 1 (
    echo 打包失败！请检查上方报错。
    if not defined CI pause
    exit /b 1
)

rem ---- Code signing (optional): runs only when a cert is configured;
rem see the header of packaging\sign.bat. ----
call "%~dp0sign.bat" "dist\ComfyHub\ComfyHub.exe"
if errorlevel 1 (
    echo 签名失败！请检查证书配置（SIGN_THUMBPRINT / SIGN_PFX）。
    if not defined CI pause
    exit /b 1
)

echo [3/3] 整理产物...
if not exist dist\ComfyHub\assets mkdir dist\ComfyHub\assets
copy /y assets\icon.ico dist\ComfyHub\assets\ > nul
.venv_build\Scripts\python.exe -c "import shutil; shutil.make_archive('dist/ComfyHub-portable', 'zip', 'dist', 'ComfyHub')"

echo.
echo 完成！产物:
echo   便携目录:   dist\ComfyHub\ComfyHub.exe
echo   便携压缩包: dist\ComfyHub-portable.zip
echo   安装包:     运行 packaging\build_installer.bat（makensis 编译 + 条件签名）
if not defined CI pause
