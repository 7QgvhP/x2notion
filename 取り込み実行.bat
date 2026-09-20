@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem Asks for confirmation before deleting from X (console is interactive here).
".venv\Scripts\python.exe" -m src.main run --log --remove-from-x
echo.
pause
