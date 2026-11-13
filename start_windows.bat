@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 server.py --host 0.0.0.0
) else (
    python server.py --host 0.0.0.0
)
pause
