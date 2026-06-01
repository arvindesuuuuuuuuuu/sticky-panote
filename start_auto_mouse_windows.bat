@echo off
setlocal

set "APP_DIR=%~dp0"
set "PID_FILE=%APP_DIR%auto_mouse_move_windows.pid"
set "LOG_FILE=%APP_DIR%auto_mouse_move_windows.log"
set "NOTIFY_SCRIPT=%APP_DIR%notify_auto_mouse_windows.vbs"

if exist "%PID_FILE%" (
    for /f "usebackq delims=" %%P in ("%PID_FILE%") do (
        tasklist /FI "PID eq %%P" 2>NUL | find "%%P" >NUL
        if not errorlevel 1 (
            echo Auto Mouse Move is already running.
            exit /b 0
        )
    )
    del "%PID_FILE%" >NUL 2>NUL
)

where py >NUL 2>NUL
if errorlevel 1 (
    where python >NUL 2>NUL
    if errorlevel 1 (
        echo Python is not installed or not in PATH. > "%LOG_FILE%"
        echo Python is not installed or not in PATH.
        exit /b 1
    )
    start "Auto Mouse Move" /min cmd /d /c ""python" "%APP_DIR%auto_mouse_move_windows.py" >> "%LOG_FILE%" 2>&1"
) else (
    start "Auto Mouse Move" /min cmd /d /c ""py" -3 "%APP_DIR%auto_mouse_move_windows.py" >> "%LOG_FILE%" 2>&1"
)

echo Auto Mouse Move started.
start "" wscript.exe "%NOTIFY_SCRIPT%" "Auto Mouse Move started."
