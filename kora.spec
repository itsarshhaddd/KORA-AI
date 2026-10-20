# PyInstaller spec for KORA  ->  dist/KORA/KORA.exe  (one-folder build)
#
# The exe is only the Python engine + window. The things that are big or that
# users swap out live NEXT TO the exe, because config.py resolves ROOT to the
# exe's folder when frozen:
#     KORA.exe   web/   runtime/ (llama-server.exe + dlls)   models/   data/   workspace/
# build_exe.bat copies web/ and runtime/ there for you.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None
ICON = "kora_logo.ico" if os.path.exists("kora_logo.ico") else None
CONSOLE = os.environ.get("KORA_BUILD_CONSOLE") == "1"   # set by `build_exe.bat debug`

datas, binaries, hiddenimports = [], [], []
# Packages that carry data files / native libs / lazy imports PyInstaller can't see on its own.
for pkg in ("webview", "pymupdf", "docx", "pptx", "openpyxl", "reportlab", "pytesseract", "xlrd", "pypdf", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:  # optional package not installed in this environment
        print(f"[kora.spec] skipping {pkg}: {exc}")

# KORA's own packages are imported normally, but list them so nothing is dropped.
for pkg in ("ai", "api", "conversation", "core", "exporter", "ingestion", "knowledge",
            "media", "retrieval", "runtime_manager", "tools"):
    hiddenimports += collect_submodules(pkg)
hiddenimports += ["autotune", "hardware", "app_logging", "winreg"]
hiddenimports += ["clr", "pythonnet", "bottle", "proxy_tools"]  # pywebview's Windows (WebView2) backend

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.tests", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="KORA",
    debug=False,
    strip=False,
    upx=False,                 # UPX corrupts some Windows DLLs and trips antivirus; leave off
    console=CONSOLE,
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="KORA")
