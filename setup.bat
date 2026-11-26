@echo off
setlocal
rem Sets up NPC Forge: a Python environment in .venv with its few packages (Pillow, numpy, xatlas).
rem Nothing is started and no model is downloaded. Run it again to finish a setup that was interrupted.
rem .venv is made from NPC Forge's own Python in python\ when it is there (Setup.exe puts it there), else from this
rem computer's Python 3.10 or newer (3.12 is tried first), else from NPC Forge's own Python, fetched now (scripts\get_python.ps1).
rem stdin is closed so that a py launcher that offers to install a missing version cannot wait for an answer.
cd /d "%~dp0"
rem Windows PowerShell gets its own module path: one inherited from PowerShell 7 names modules it cannot load.
set "PSModulePath="
set "PYTHON="
if exist "python\python.exe" set "PYTHON=python\python.exe"
if not defined PYTHON for %%V in (3.12 3.13 3.11 3.10) do if not defined PYTHON (
    py -%%V -c "import sys" <nul >nul 2>&1 && set "PYTHON=py -%%V"
)
if not defined PYTHON (
    python -c "import sys; sys.exit(sys.version_info < (3, 10))" <nul >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON (
    echo Python 3.10 or newer was not found on this computer.
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\get_python.ps1
    if errorlevel 1 goto no_python
    set "PYTHON=python\python.exe"
)
%PYTHON% scripts\setup_core.py || goto failed
echo.
echo NPC Forge is set up. Double-click start.bat to open it; it can help you set up ComfyUI from there.
exit /b 0
:no_python
echo.
echo Setup could not get Python for NPC Forge; the reason is above. Check the internet connection and run setup.bat again,
echo or install Python 3.12 from python.org and run setup.bat again.
exit /b 1
:failed
echo Setup did not finish: see the message above.
exit /b 1
