@echo off
setlocal
rem Sets up NPC Forge: a Python environment in .venv with its few packages (Pillow, numpy, xatlas).
rem Nothing is started and no model is downloaded. Python 3.10 or newer; 3.12 is tried first.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
set "FOUND="
for %%V in (3.12 3.13 3.11 3.10) do if not defined FOUND (
    py -%%V -c "import sys" >nul 2>&1 && set "FOUND=%%V"
)
if defined FOUND (
    py -%FOUND% -m venv .venv || goto failed
    goto install
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 (
    echo Python 3.10 or newer was not found. Install Python 3.12 from python.org, then run setup.bat again.
    exit /b 1
)
python -m venv .venv || goto failed
:install
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
echo.
echo NPC Forge is set up. Start ComfyUI, then double-click start.bat.
exit /b 0
:failed
echo Setup did not finish: see the message above.
exit /b 1
