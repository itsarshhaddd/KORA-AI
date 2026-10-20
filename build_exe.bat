@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rem ============================================================================
rem  Builds KORA.exe (PyInstaller, one-folder) into dist\KORA\
rem    build_exe.bat              normal build (no console window)
rem    build_exe.bat debug        same, but with a console window for troubleshooting
rem    build_exe.bat withmodels   also copy models\ into the build (several GB)
rem ============================================================================
set "DEBUG=0"
set "WITHMODELS=0"
for %%A in (%*) do (
  if /i "%%A"=="debug" set "DEBUG=1"
  if /i "%%A"=="withmodels" set "WITHMODELS=1"
)

if not exist ".venv\Scripts\python.exe" (
  echo [KORA] No .venv found - running setup first...
  call "%~dp0setup_kora.bat"
  if errorlevel 1 ( echo [ERROR] Setup failed. & pause & exit /b 1 )
)

echo [KORA] Installing build tools...
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 ( echo [ERROR] Could not install PyInstaller. & pause & exit /b 2 )

if "%DEBUG%"=="1" ( set "KORA_BUILD_CONSOLE=1" ) else ( set "KORA_BUILD_CONSOLE=0" )

echo [KORA] Building KORA.exe ^(this takes a few minutes^)...
".venv\Scripts\python.exe" -m PyInstaller kora.spec --noconfirm --clean
if errorlevel 1 ( echo [ERROR] PyInstaller build failed. & pause & exit /b 3 )

set "OUT=dist\KORA"
echo [KORA] Copying web UI and llama.cpp runtime next to the exe...
robocopy "web" "%OUT%\web" /E /NFL /NDL /NJH /NJS /NP >nul
if not exist "runtime\llama-server.exe" (
  echo [WARN] runtime\llama-server.exe not found - copy your llama.cpp runtime into %OUT%\runtime yourself.
) else (
  robocopy "runtime" "%OUT%\runtime" /E /XF *.log /XD webview_storage /NFL /NDL /NJH /NJS /NP >nul
)
for %%D in (data workspace models runtime logs) do if not exist "%OUT%\%%D" mkdir "%OUT%\%%D"

if "%WITHMODELS%"=="1" (
  echo [KORA] Copying models ^(large^)...
  robocopy "models" "%OUT%\models" /E /NFL /NDL /NJH /NJS /NP >nul
) else (
  echo [INFO] Models NOT copied. Put these in %OUT%\models before first run:
  echo          gemma-4-E2B-it-Q4_K_M.gguf, embeddinggemma-300m-Q4_0.gguf, mmproj-F16.gguf
)

> "%OUT%\README.txt" echo KORA - run KORA.exe. Keep the web, runtime and models folders next to it.
>> "%OUT%\README.txt" echo If something goes wrong, send the files in the logs folder: kora.log, crash.log, chat.log, embedding.log
>> "%OUT%\README.txt" echo GPU support: runtime\ may hold one llama.cpp build (a Vulkan build runs on NVIDIA, AMD and Intel GPUs).
>> "%OUT%\README.txt" echo Optional: runtime\cuda\ (NVIDIA CUDA build), runtime\cpu\ (CPU-only build) - KORA picks the best one automatically.
>> "%OUT%\README.txt" echo Needs the Microsoft WebView2 runtime (built into Windows 10/11).

echo.
echo [OK] Built: %CD%\%OUT%\KORA.exe
pause
exit /b 0
