@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m pip install --upgrade pywebview pyinstaller
if errorlevel 1 goto :fail
py -3 -m PyInstaller --noconfirm --onefile --windowed --name FocusLog --icon NONE --add-data "static;static" app.py
if errorlevel 1 goto :fail
echo.
echo 完成: dist\FocusLog.exe
echo データは exe と同じ場所の data\focuslog.db に保存されます。
pause
exit /b 0
:fail
echo ビルドに失敗しました。
pause
exit /b 1
