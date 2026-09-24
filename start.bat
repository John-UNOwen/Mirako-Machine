@echo off
setlocal

rem Launcher for Mirako Machine.
rem
rem First run builds a private environment in .venv and installs the dependencies into
rem it; every run after that just starts the bot. The environment is private on purpose:
rem several dependencies are pinned to exact versions, and dropping them into a shared
rem Python is how they collide with everything else installed there. Removing it later
rem is deleting the .venv folder.
rem
rem Nothing here compiles: every dependency is a prebuilt Windows wheel or pure Python,
rem so no C++ build tools are needed.

cd /d "%~dp0"

rem 3.13 by name rather than whatever "py" defaults to. It currently defaults to 3.14 on
rem a fresh install, which main.py refuses -- it supports 3.11 through 3.13.
set "PYTHON_TAG=-3.13"

if exist ".venv\Scripts\python.exe" goto :run

echo Setting up for first use. This downloads about 500 MB and takes a few minutes.
echo.

py %PYTHON_TAG% --version >nul 2>&1
if errorlevel 1 (
  echo   Python 3.13 was not found.
  echo.
  echo   Install it from https://www.python.org/downloads/release/python-3130/
  echo   and tick "Add python.exe to PATH" in the installer.
  echo.
  pause
  exit /b 1
)

py %PYTHON_TAG% -m venv .venv
if errorlevel 1 (
  echo   Could not create the environment in .venv
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   Installing the dependencies failed.
  echo.
  echo   The usual cause is the internet connection dropping during the download.
  echo   Run this again; if it keeps failing, the lines above say which package broke.
  echo.
  rem The half-built environment would otherwise be taken for a finished one next run.
  rmdir /s /q .venv
  pause
  exit /b 1
)

echo.
echo Setup finished.
echo.

:run
".venv\Scripts\python.exe" main.py %*
