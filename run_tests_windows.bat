@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m unittest tests.test_server -v
) else (
    python -m unittest tests.test_server -v
)
echo.
if %errorlevel%==0 (echo すべてのテストに合格しました。) else (echo 失敗したテストがあります。上の出力を確認してください。)
pause
