@echo off
setlocal

set "STARTUP_FILE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Auto Mouse Move.vbs"

if exist "%STARTUP_FILE%" (
    del "%STARTUP_FILE%" >NUL 2>NUL
    echo Auto Mouse Move was removed from Windows startup.
) else (
    echo Auto Mouse Move is not installed in Windows startup.
)
