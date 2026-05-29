@echo off
setlocal

set "APP_DIR=%~dp0"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_FILE=%STARTUP_DIR%\Auto Mouse Move.vbs"

if not exist "%STARTUP_DIR%" (
    echo Windows Startup folder was not found.
    exit /b 1
)

(
    echo Set shell = CreateObject^("WScript.Shell"^)
    echo shell.Run """%APP_DIR%start_auto_mouse_windows.bat""", 0, False
) > "%STARTUP_FILE%"

echo Auto Mouse Move will start when you sign in.
