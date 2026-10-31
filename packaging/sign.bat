@echo off
chcp 65001 > nul
rem ============================================================
rem ComfyHub code signing (conditional). Skips automatically when
rem no certificate is configured, so the build chain never blocks.
rem Usage: sign.bat <file-to-sign>
rem
rem Certificate config (env vars, pick one):
rem   SIGN_THUMBPRINT      SHA1 thumbprint of a code-signing cert in
rem                        the Windows certificate store (recommended)
rem   SIGN_PFX             path to a .pfx file, use with SIGN_PFX_PASSWORD
rem   SIGN_PFX_PASSWORD    pfx password
rem Optional:
rem   SIGN_TIMESTAMP_URL   timestamp server, default http://timestamp.digicert.com
rem   SIGNTOOL             full path to signtool.exe, auto-detected by default
rem
rem Signs with /fd SHA256 /tr <timestamp> /td SHA256, then verifies.
rem NOTE: always keep the timestamp switch, or the signature dies with the cert.
rem ============================================================
setlocal
set "TARGET=%~1"
if "%TARGET%"=="" (
    echo [sign] usage: sign.bat ^<file-to-sign^>
    exit /b 2
)
if not exist "%TARGET%" (
    echo [sign] file not found: %TARGET%
    exit /b 2
)

rem ---- no cert configured - skip, this is the normal path ----
if "%SIGN_THUMBPRINT%"=="" if "%SIGN_PFX%"=="" (
    echo [sign] no signing cert configured - SIGN_THUMBPRINT or SIGN_PFX - skipping: %TARGET%
    exit /b 0
)

rem ---- locate signtool ----
set "SIGNTOOL_EXE=%SIGNTOOL%"
if not "%SIGNTOOL_EXE%"=="" goto have_tool
for /f "delims=" %%i in ('where signtool 2^>nul') do if not defined SIGNTOOL_EXE set "SIGNTOOL_EXE=%%i"
if defined SIGNTOOL_EXE goto have_tool
rem Windows SDK: enumerate version dirs via dir (plain FOR glob does not match
rem directories here); path is built from %ProgramFiles(x86)% so no literal
rem drive path can be mangled; last match wins = highest version in dir order
for /f "delims=" %%v in ('dir /b "%ProgramFiles(x86)%\Windows Kits\10\bin\10.0.*" 2^>nul') do (
    if exist "%ProgramFiles(x86)%\Windows Kits\10\bin\%%v\x64\signtool.exe" set "SIGNTOOL_EXE=%ProgramFiles(x86)%\Windows Kits\10\bin\%%v\x64\signtool.exe"
)
if not defined SIGNTOOL_EXE (
    echo [sign] signtool.exe not found. Install the Windows SDK or set SIGNTOOL.
    exit /b 1
)
:have_tool

set "TS_URL=%SIGN_TIMESTAMP_URL%"
if "%TS_URL%"=="" set "TS_URL=http://timestamp.digicert.com"

echo [sign] signing: %TARGET%
if not "%SIGN_THUMBPRINT%"=="" (
    "%SIGNTOOL_EXE%" sign /sha1 %SIGN_THUMBPRINT% /fd SHA256 /tr "%TS_URL%" /td SHA256 "%TARGET%"
) else (
    "%SIGNTOOL_EXE%" sign /f "%SIGN_PFX%" /p "%SIGN_PFX_PASSWORD%" /fd SHA256 /tr "%TS_URL%" /td SHA256 "%TARGET%"
)
if errorlevel 1 (
    echo [sign] signing FAILED: %TARGET%
    exit /b 1
)

rem Advisory verify: signature was already applied successfully above. A chain
rem error here is EXPECTED with a self-signed cert until its root certificate
rem is imported into the Trusted Root store - warn, do not fail the build.
"%SIGNTOOL_EXE%" verify /pa "%TARGET%" 2>&1
if errorlevel 1 (
    echo [sign] verify warning: signature is on the file but its chain is not
    echo [sign] trusted yet. Expected for self-signed certs until the root
    echo [sign] certificate is imported into Trusted Root. Not a build error.
)
echo [sign] done: %TARGET%
exit /b 0
