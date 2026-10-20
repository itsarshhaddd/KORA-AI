from __future__ import annotations

import os
import sys
from pathlib import Path



def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = project_root()
DATA_DIR = ROOT / "data"
WORKSPACE_DIR = ROOT / "workspace"
MODELS_DIR = ROOT / "models"
MEDIA_DIR = DATA_DIR / "media"
RUNTIME_DIR = ROOT / "runtime"
# All logs (app, crash, llama-server chat/embedding) live here.
LOG_DIR = ROOT / "logs"
# Persistent profile dir for the pywebview window (cookies/localStorage), so
# the app.js appearance settings (kora_appearance) survive app restarts
# instead of pywebview's default "private_mode" wiping them each launch.
WEBVIEW_STORAGE_DIR = RUNTIME_DIR / "webview_storage"
WEB_DIR = ROOT / "web"
INDEX_PATH = DATA_DIR / "knowledge_index.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
ACTIVITY_PATH = DATA_DIR / "activity.json"
SETTINGS_PATH = DATA_DIR / "kora_new_settings.json"
# Performance choice saved from Settings > System (read by autotune at startup).
HARDWARE_PREFS_PATH = DATA_DIR / "hardware_prefs.json"

# ---------------------------------------------------------------------------
# MODEL FILES -- change these to switch models (e.g. a smaller one for
# lower-end PCs). Put the .gguf file in the "models" folder and set the name
# below to match the filename EXACTLY (including the .gguf extension).
# You can also override without editing this file via env vars
# KORA_CHAT_MODEL / KORA_EMBED_MODEL.
# ---------------------------------------------------------------------------
CHAT_MODEL = os.getenv("KORA_CHAT_MODEL", "gemma-4-E2B-it-Q4_K_M.gguf")
EMBED_MODEL = os.getenv("KORA_EMBED_MODEL", "embeddinggemma-300m-Q4_0.gguf")
# Pooling must match the embedding model: "mean" for EmbeddingGemma (and most
# BERT-style models), "last" for Qwen3-Embedding, "cls" for some others.
EMBED_POOLING = os.getenv("KORA_EMBED_POOLING", "mean")
# EmbeddingGemma expects task prefixes on its input (see its model card).
# Set both to "" when switching to a model that doesn't use prefixes.
EMBED_QUERY_PREFIX = os.getenv("KORA_EMBED_QUERY_PREFIX", "task: search result | query: ")
EMBED_DOC_PREFIX = os.getenv("KORA_EMBED_DOC_PREFIX", "title: none | text: ")
# Max texts sent per /v1/embeddings request while indexing.
EMBED_BATCH_SIZE = int(os.getenv("KORA_EMBED_BATCH", "8"))
CHAT_URL = os.getenv("KORA_CHAT_URL", "http://127.0.0.1:8080")
# Multimodal projector for Gemma 4 E2B (the file that lets it see images and
# hear audio). Download the matching "mmproj-*.gguf" for your Gemma 4 E2B build
# into models/. Leave blank to auto-detect any mmproj*.gguf; if none exists the
# app runs text-only and the image / voice buttons are disabled.
MMPROJ_MODEL = os.getenv("KORA_MMPROJ_MODEL", "")
MEDIA_IMAGE_MAX_EDGE = int(os.getenv("KORA_IMAGE_MAX_EDGE", "1280"))
# Gemma's audio encoder handles clips up to ~30 s; the UI splits longer audio.
AUDIO_CHUNK_SECONDS = int(os.getenv("KORA_AUDIO_CHUNK_SECONDS", "30"))
EMBED_URL = os.getenv("KORA_EMBED_URL", "http://127.0.0.1:8081")
API_HOST = os.getenv("KORA_HOST", "127.0.0.1")
API_PORT = int(os.getenv("KORA_PORT", "5175"))
MAX_AGENT_STEPS = int(os.getenv("KORA_MAX_AGENT_STEPS", "8"))
TOP_K = int(os.getenv("KORA_TOP_K", "8"))

# ---------------------------------------------------------------------------
# AI RUNTIME TUNING -- AUTOMATIC.
# KORA detects your CPU / RAM / GPU (NVIDIA, AMD, Intel) at startup and picks
# the context size, GPU offload and thread count itself (see autotune.py and
# hardware.py), so it runs on laptops without hogging the machine. The values
# below are None = "auto". Set the env var only to force a value manually.
# ---------------------------------------------------------------------------
def _opt_int(name: str) -> int | None:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


CHAT_CTX_SIZE = _opt_int("KORA_CHAT_CTX_SIZE")
EMBED_CTX_SIZE = _opt_int("KORA_EMBED_CTX_SIZE")
CHAT_GPU_LAYERS = _opt_int("KORA_CHAT_GPU_LAYERS")
EMBED_GPU_LAYERS = _opt_int("KORA_EMBED_GPU_LAYERS")
CHAT_THREADS = _opt_int("KORA_CHAT_THREADS")
EMBED_THREADS = _opt_int("KORA_EMBED_THREADS")
CHAT_PARALLEL_SLOTS = int(os.getenv("KORA_CHAT_PARALLEL", "1"))
EMBED_PARALLEL_SLOTS = int(os.getenv("KORA_EMBED_PARALLEL", "1"))
# Set by autotune: False when the PC is too small to also load the vision/audio projector.
MMPROJ_ENABLED = True

for _path in (DATA_DIR, MEDIA_DIR, WORKSPACE_DIR, MODELS_DIR, RUNTIME_DIR, WEBVIEW_STORAGE_DIR, LOG_DIR):
    _path.mkdir(parents=True, exist_ok=True)


def find_mmproj() -> Path | None:
    """The projector file if image/voice input is available on this machine (autotune may switch it off on low-RAM PCs)."""
    return find_mmproj_file() if MMPROJ_ENABLED else None


def find_mmproj_file() -> Path | None:
    """Resolve the multimodal projector file (explicit name, else first mmproj*.gguf)."""
    if MMPROJ_MODEL:
        candidate = MODELS_DIR / MMPROJ_MODEL
        return candidate if candidate.exists() else None
    matches = sorted(MODELS_DIR.glob("mmproj*.gguf"))
    return matches[0] if matches else None
