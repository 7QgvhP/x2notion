@echo off
rem Called by Windows Task Scheduler. Runs headless and writes to logs\.
rem Uses pythonw.exe so no console window appears.
rem --remove-from-x deletes imported bookmarks from X (no prompt when unattended).
cd /d "%~dp0.."
".venv\Scripts\pythonw.exe" -m src.main run --log --remove-from-x
