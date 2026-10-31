@echo off
chcp 65001 > nul
title ComfyHub installer - NSIS
rem This script lives in packaging\, switch to the repo root first.
cd /d "%~dp0.."

echo ============================================
echo   ComfyHub installer build - NSIS + conditional signing
echo ============================================
echo.

rem ---- locate makensis: env MAKENSIS first, then bundled toolchain, then PATH ----
set "MAKENSIS_EXE=%MAKENSIS%"
if not "%MAKENSIS_EXE%"=="" goto have_ns
if exist "%~dp0..\tools\mingw64\bin\makensis.exe" set "MAKENSIS_EXE=%~dp0..\tools\mingw64\bin\makensis.exe"
if defined MAKENSIS_EXE goto have_ns
for /f "delims=" %%i in ('where makensis 2^>nul') do if not defined MAKENSIS_EXE set "MAKENSIS_EXE=%%i"
if not defined MAKENSIS_EXE (
    echo makensis.exe not found. Install NSIS or set the MAKENSIS env var.
    pause
    exit /b 1
)
:have_ns

echo [1/2] makensis compiling: %MAKENSIS_EXE%
"%MAKENSIS_EXE%" installer\ComfyHub.nsi
if errorlevel 1 (
    echo Installer compile FAILED, see output above.
    pause
    exit /b 1
)

echo [2/2] conditional signing, skipped when no cert is configured...
rem The output name carries the version - APP_VERSION in ComfyHub.nsi -
rem so pick the newest Setup exe in dist\ and never track versions here.
set "SETUP="
for /f "delims=" %%f in ('dir /b /o-d "dist\ComfyHub_Setup_*.exe" 2^>nul') do if not defined SETUP set "SETUP=dist\%%f"
if "%SETUP%"=="" (
    echo dist\ComfyHub_Setup_*.exe not found, skipping signing.
    goto end
)
call "%~dp0sign.bat" "%SETUP%"
if errorlevel 1 (
    echo Installer signing FAILED, check SIGN_THUMBPRINT or SIGN_PFX.
    pause
    exit /b 1
)

:end
echo.
echo Done: %SETUP%
pause
