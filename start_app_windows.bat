@echo off
cd /d "%~dp0"
py -3 -m pip install --quiet pywebview
py -3 app.py
if errorlevel 1 pause
