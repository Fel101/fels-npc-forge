@echo off
rem Updates NPC Forge to its newest release on GitHub. It shows what will change and asks first, and keeps your library,
rem config.json, local\ and .venv. --check only says whether there is a newer release, --dry-run shows the plan, and --undo
rem puts back the version the last update replaced. Close NPC Forge before you run it.
setlocal
cd /d "%~dp0"
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py"
)
if not defined PY (
  echo Python was not found. Run setup.bat first.
  pause
  exit /b 1
)
rem An update can replace this file while it runs, and cmd.exe reads a batch file as it goes, so the rest is one block that
rem cmd.exe has read whole before Python starts. call reads the exit code when the line runs, not when the block was read.
(
  "%PY%" scripts\update.py %*
  call set result=%%errorlevel%%
  pause
  call exit /b %%result%%
)
