@echo off
setlocal

set "APP_DIR=%~dp0"
set "PID_FILE=%APP_DIR%auto_mouse_move_windows.pid"

if not exist "%PID_FILE%" (
    echo Auto Mouse Move is not running.
    exit /b 0
)

for /f "usebackq delims=" %%P in ("%PID_FILE%") do (
    tasklist /FI "PID eq %%P" 2>NUL | find "%%P" >NUL
    if errorlevel 1 (
        del "%PID_FILE%" >NUL 2>NUL
        echo Auto Mouse Move is not running.
        exit /b 0
    )

    taskkill /PID %%P /T /F >NUL 2>NUL
    del "%PID_FILE%" >NUL 2>NUL
    echo Auto Mouse Move stopped.
    exit /b 0
)
