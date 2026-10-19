@echo off
chcp 65001 > nul
title ComfyHub 打包 - PyInstaller
cd /d %~dp0

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
.venv_build\Scripts\pyinstaller.exe ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name ComfyHub ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --hidden-import PySide6.QtSvg ^
    main.py

if errorlevel 1 (
    echo 打包失败！请检查上方报错。
    pause
    exit /b 1
)

echo [3/3] 整理产物...
rem 复制图标到发布目录（运行时"关于"页可用）
if not exist dist\ComfyHub\assets mkdir dist\ComfyHub\assets
copy /y assets\icon.ico dist\ComfyHub\assets\ > nul
rem 打成便携 zip
.venv_build\Scripts\python.exe -c "import shutil; shutil.make_archive('dist/ComfyHub-portable', 'zip', 'dist', 'ComfyHub')"

echo.
echo 完成！产物:
echo   便携目录: dist\ComfyHub\ComfyHub.exe
echo   便携压缩包: dist\ComfyHub-portable.zip
echo   安装包用 Inno Setup 编译 installer.iss
pause
