"""Pre-flight check: verifies the models named in config.py exist in models/."""
import sys
from config import CHAT_MODEL, EMBED_MODEL, MODELS_DIR, find_mmproj

missing = [n for n in (CHAT_MODEL, EMBED_MODEL) if not (MODELS_DIR / n).exists()]
for n in missing:
    print(f"[ERROR] Missing models\\{n}  (set the exact filename in config.py)")
if not missing and not find_mmproj():
    print("[NOTE] No mmproj*.gguf in models\\ -- KORA will run text-only. Add the Gemma 4 E2B mmproj file to enable image + voice input.")
sys.exit(2 if missing else 0)
