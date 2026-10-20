@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE (
  echo [ERROR] Python 3.11+ is required.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" "%PYEXE%" -m venv .venv
if errorlevel 1 (
  echo [ERROR] Could not create virtual environment.
  pause
  exit /b 2
)

rem --- Architecture check: catches the classic "DLL load failed / %1 is not
rem     a valid Win32 application" error before it happens, which almost
rem     always means a 32-bit/64-bit mismatch or a venv copied from another PC.
rem     Uses a temp .py file instead of an inline -c string, and calls it via
rem     "call" with no leading quote, since a for /f command string that
rem     starts with a quote character confuses cmd's parser.
echo import platform, sys > "%TEMP%\kora_archcheck.py"
echo print(platform.architecture()[0]) >> "%TEMP%\kora_archcheck.py"
for /f "delims=" %%A in ('call .venv\Scripts\python.exe "%TEMP%\kora_archcheck.py"') do set "PYARCH=%%A"
del "%TEMP%\kora_archcheck.py" >nul 2>&1
echo [INFO] Detected Python architecture: %PYARCH%
if not "%PYARCH%"=="64bit" (
  echo [ERROR] Your .venv is using a %PYARCH% Python. KORA's dependencies
  echo          ^(pymupdf, etc.^) require 64-bit Python.
  echo          Delete the .venv folder, install 64-bit Python from python.org
  echo          ^(make sure you download the "Windows installer (64-bit)", not x86^),
  echo          and run this script again.
  pause
  exit /b 10
)

".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [ERROR] Dependency bootstrap failed.
  pause
  exit /b 3
)

if not exist "requirements.txt" (
  echo [ERROR] requirements.txt is missing from the KORA folder.
  pause
  exit /b 4
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Dependency installation failed.
  pause
  exit /b 5
)

rem --- Self-heal check: if a native extension (like pymupdf) was left over
rem     from a copied/broken venv, importing it now will throw the DLL error
rem     instead of later at runtime. Force a clean reinstall automatically.
".venv\Scripts\python.exe" -c "import pymupdf" >nul 2>nul
if errorlevel 1 (
  echo [WARN] pymupdf failed to import ^(likely a stale/mismatched binary^).
  echo         Reinstalling pymupdf cleanly...
  ".venv\Scripts\python.exe" -m pip uninstall pymupdf -y >nul 2>&1
  ".venv\Scripts\python.exe" -m pip install --no-cache-dir --force-reinstall pymupdf
  if errorlevel 1 (
    echo [ERROR] pymupdf reinstall failed.
    pause
    exit /b 11
  )
  ".venv\Scripts\python.exe" -c "import pymupdf" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] pymupdf still fails to import after reinstall.
    echo          Try deleting the .venv folder entirely and re-running setup.
    pause
    exit /b 12
  )
  echo [OK] pymupdf reinstalled successfully.
)

rem --- PDF export needs reportlab; confirm it imports so a failure shows up here, not at export time.
".venv\Scripts\python.exe" -c "import reportlab" >nul 2>nul
if errorlevel 1 (
  echo [WARN] reportlab failed to import - reinstalling for PDF export...
  ".venv\Scripts\python.exe" -m pip install --no-cache-dir --force-reinstall reportlab
  if errorlevel 1 (
    echo [ERROR] reportlab install failed. Chat export to PDF will not work.
    pause
    exit /b 13
  )
)

for %%D in (data data\media workspace models runtime) do if not exist "%%D" mkdir "%%D"

if not exist "runtime\llama-server.exe" (
  echo [ERROR] runtime\llama-server.exe is missing.
  pause
  exit /b 6
)
echo [INFO] Models folder must contain: the Gemma 4 E2B chat .gguf, the EmbeddingGemma .gguf, and ^(for image + voice^) an mmproj-*.gguf.
".venv\Scripts\python.exe" check_models.py
if errorlevel 1 (
  pause
  exit /b %errorlevel%
)

".venv\Scripts\python.exe" -m compileall -q ai api conversation core exporter ingestion knowledge media retrieval runtime_manager tools main.py config.py
if errorlevel 1 (
  echo [ERROR] Python compilation failed.
  pause
  exit /b 9
)

echo [OK] KORA Python engine environment is ready.
exit /b 0
