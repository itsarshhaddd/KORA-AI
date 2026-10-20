@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [KORA] Starting Python engine...

if not exist "main.py" (
  echo [ERROR] main.py is missing from the KORA folder.
  pause
  exit /b 10
)
if not exist ".venv\Scripts\python.exe" (
  echo [KORA] Python environment missing. Running setup...
  call "%~dp0setup_kora.bat"
  if errorlevel 1 (
    echo [ERROR] Setup failed with code %errorlevel%.
    pause
    exit /b %errorlevel%
  )
)
".venv\Scripts\python.exe" check_models.py
if errorlevel 1 (
  pause
  exit /b %errorlevel%
)
if not exist "runtime\llama-server.exe" (
  echo [ERROR] Missing runtime\llama-server.exe
  pause
  exit /b 4
)

rem --- Pre-flight check: catches "DLL load failed / %1 is not a valid Win32
rem     application" before main.py crashes with a raw traceback. This almost
rem     always means the venv is 32-bit, or was copied from another machine
rem     and its native extensions (pymupdf, etc.) no longer match.
".venv\Scripts\python.exe" -c "import pymupdf" 2>nul
if errorlevel 1 (
  echo [KORA] pymupdf failed to import - environment looks broken or was
  echo        copied from another machine. Re-running setup to repair it...
  call "%~dp0setup_kora.bat"
  if errorlevel 1 (
    echo [ERROR] Repair via setup failed with code %errorlevel%.
    pause
    exit /b %errorlevel%
  )
)

echo [KORA] Launching Python engine...
".venv\Scripts\python.exe" main.py
set "RC=%errorlevel%"

echo.
if not "%RC%"=="0" echo [KORA] Engine exited with code %RC%.
pause
exit /b %RC%
