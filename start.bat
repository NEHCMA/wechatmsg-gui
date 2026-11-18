@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" run_wechat_export.py
