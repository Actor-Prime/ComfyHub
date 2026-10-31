@echo off
chcp 65001 > nul
title ComfyHub 打包 - PyInstaller
rem ★ 本脚本位于 packaging\ 下，先切到项目根目录（后续路径均相对根目录）
cd /d "%~dp0.."

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
rem 产物目录被占用时先清理（常见：上次运行的进程未退出）
taskkill /IM ComfyHub.exe /F > nul 2>&1
if exist dist\ComfyHub (
    timeout /t 2 /nobreak > nul
    rd /s /q dist\ComfyHub 2> nul
)
.venv_build\Scripts\pyinstaller.exe ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name ComfyHub ^
    --icon "%~dp0..\assets\icon.ico" ^
    --add-data "%~dp0..\assets;assets" ^
    --hidden-import PySide6.QtSvg ^
    --distpath "%~dp0..\dist" ^
    --workpath "%~dp0..\build" ^
    --specpath "%~dp0..\build" ^
    "%~dp0..\main.py"

if errorlevel 1 (
    echo 打包失败！请检查上方报错。
    pause
    exit /b 1
)

rem ---- 代码签名（可选）：配置了证书才签，未配置自动跳过（见 packaging\sign.bat 头部）----
call "%~dp0sign.bat" "dist\ComfyHub\ComfyHub.exe"
if errorlevel 1 (
    echo 签名失败！请检查证书配置（SIGN_THUMBPRINT / SIGN_PFX）。
    pause
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
pause
