@echo off
rem Starts NPC Forge and opens it in your browser. ComfyUI must already be running.
cd /d "%~dp0"
rem The helper checks the configured port and handles a stalled existing process.
rem It runs in .venv when setup.bat made one, else in the Python on the PATH.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\start.py
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        py scripts\start.py
    ) else (
        python scripts\start.py
    )
)
if errorlevel 1 pause
