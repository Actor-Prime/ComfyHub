@echo off
chcp 65001 > nul
title ComfyHub portable zip
rem ============================================================
rem Portable package for self-update:
rem   dist\ComfyHub  ->  dist\ComfyHub_Portable_<version>.zip
rem Upload this zip together with the Setup exe on the Gitee release,
rem otherwise portable installs cannot self-update.
rem Usage: packaging\build_portable_zip.bat
rem ============================================================
cd /d "%~dp0.."

if not exist "dist\ComfyHub\ComfyHub.exe" (
    echo [ERROR] dist\ComfyHub\ComfyHub.exe not found, run build_exe.bat first
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python -c "import sys;sys.path.insert(0,chr(46));from app.config import APP_VERSION;print(APP_VERSION)"') do set "VER=%%v"
if "%VER%"=="" (
    echo [ERROR] cannot read APP_VERSION
    pause
    exit /b 1
)

set "OUT=dist\ComfyHub_Portable_%VER%.zip"
if exist "%OUT%" del /f /q "%OUT%"
echo Packing %OUT% ...
python -c "import shutil,os; os.makedirs(r'dist', exist_ok=True); shutil.make_archive(r'dist/ComfyHub_Portable_%VER%', 'zip', r'dist/ComfyHub'); print('ok')"
if not exist "%OUT%" (
    echo [ERROR] packing failed
    pause
    exit /b 1
)
echo.
echo Done: %OUT%
echo Remember: upload this zip with the Setup exe so portable installs can self-update.
pause
