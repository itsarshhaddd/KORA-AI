"""Chooses the AI runtime settings for this computer (cached for the session).

Manual overrides, all optional:
    KORA_PROFILE=cpu         never use the GPU
    KORA_PROFILE=low         small context / fewer threads (gentlest on the system)
    KORA_FORCE_GPU=1         also use integrated GPUs (Intel UHD/Iris, AMD APUs)
    KORA_CHAT_CTX_SIZE, KORA_CHAT_THREADS, KORA_CHAT_GPU_LAYERS, ... (see config.py)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading

import config
from hardware import GB, Hardware, Profile, build_profile, detect_hardware

log = logging.getLogger("kora.autotune")
_lock = threading.Lock()
_cache: dict[str, Profile] = {}
_hw: Hardware | None = None


def _size_gb(path) -> float:
    try:
        return path.stat().st_size / GB
    except OSError:
        return 0.0


def _apply_overrides(p: Profile) -> Profile:
    over = {}
    for field, value in (("chat_ctx", config.CHAT_CTX_SIZE), ("embed_ctx", config.EMBED_CTX_SIZE),
                         ("chat_threads", config.CHAT_THREADS), ("embed_threads", config.EMBED_THREADS),
                         ("chat_gpu_layers", config.CHAT_GPU_LAYERS), ("embed_gpu_layers", config.EMBED_GPU_LAYERS)):
        if value is not None:
            over[field] = value
    if _env_profile() == "low":
        over.setdefault("chat_ctx", min(p.chat_ctx, 4096))
        over.setdefault("chat_threads", max(1, p.chat_threads // 2))
    return dataclasses.replace(p, **over) if over else p


def load_prefs() -> dict:
    """Choice saved from the Settings > System page: {"profile": auto|low|cpu, "force_gpu": bool}."""
    try:
        data = json.loads(config.HARDWARE_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    profile = str(data.get("profile", "auto")).lower()
    return {"profile": profile if profile in ("auto", "low", "cpu") else "auto", "force_gpu": bool(data.get("force_gpu", False))}


def save_prefs(profile: str, force_gpu: bool) -> dict:
    prefs = {"profile": profile if profile in ("auto", "low", "cpu") else "auto", "force_gpu": bool(force_gpu)}
    config.HARDWARE_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.HARDWARE_PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def _env_profile() -> str:
    """Environment variable beats the saved choice (handy for support/testing)."""
    return (os.getenv("KORA_PROFILE") or load_prefs()["profile"]).lower()


def _compute(mode: str) -> Profile:
    global _hw
    if _hw is None:
        _hw = detect_hardware()
    chat = _size_gb(config.MODELS_DIR / config.CHAT_MODEL) or 3.0
    embed = _size_gb(config.MODELS_DIR / config.EMBED_MODEL) or 0.3
    mm_path = config.find_mmproj_file()
    mm = _size_gb(mm_path) if mm_path else 0.0
    force_gpu = os.getenv("KORA_FORCE_GPU") == "1" or load_prefs()["force_gpu"]
    env_mode = _env_profile()
    return _apply_overrides(build_profile(_hw, chat, embed, mm, "cpu" if (mode == "cpu" or env_mode == "cpu") else "auto", force_gpu))


def get_profile() -> Profile:
    """Best settings for this machine. Never raises: falls back to a safe CPU profile."""
    with _lock:
        if "main" in _cache:
            return _cache["main"]
        try:
            profile = _compute("auto")
            log.info("Hardware: %s", _hw)
            log.info("AI profile: %s", profile.summary)
            for warning in profile.warnings:
                log.warning(warning)
            try:
                (config.LOG_DIR / "hardware.json").write_text(
                    json.dumps({"hardware": dataclasses.asdict(_hw), "profile": dataclasses.asdict(profile)}, indent=2), encoding="utf-8")
            except OSError:
                pass
        except Exception:  # noqa: BLE001
            log.exception("Hardware detection failed; using a safe CPU profile")
            profile = Profile(tier="cpu-lite", mode="cpu", chat_ctx=4096, embed_ctx=4096, chat_threads=2, embed_threads=2, use_mmproj=False,
                              chat_ubatch=256, summary="safe fallback (detection failed)")
        config.MMPROJ_ENABLED = profile.use_mmproj
        _cache["main"] = profile
        return profile


def get_cpu_profile() -> Profile:
    """CPU-only settings, used if the GPU backend fails to start."""
    main = get_profile()
    if main.mode == "cpu":
        return main
    with _lock:
        if "cpu" not in _cache:
            try:
                _cache["cpu"] = _compute("cpu")
            except Exception:  # noqa: BLE001
                log.exception("CPU profile failed")
                _cache["cpu"] = Profile(tier="cpu-lite", mode="cpu", chat_threads=2, embed_threads=2, use_mmproj=False, chat_ubatch=256)
        return _cache["cpu"]


def describe(active=None) -> dict:
    """Everything the Settings > System page shows (JSON-friendly)."""
    profile = get_profile()
    hw = _hw
    overrides = sorted(k for k in os.environ if k.startswith("KORA_") and k in (
        "KORA_PROFILE", "KORA_FORCE_GPU", "KORA_CHAT_CTX_SIZE", "KORA_EMBED_CTX_SIZE", "KORA_CHAT_THREADS",
        "KORA_EMBED_THREADS", "KORA_CHAT_GPU_LAYERS", "KORA_EMBED_GPU_LAYERS"))
    info = {
        "hardware": dataclasses.asdict(hw) if hw else None,
        "profile": dataclasses.asdict(profile),
        "prefs": load_prefs(),
        "env_overrides": overrides,
        "log_dir": str(config.LOG_DIR),
        "running": None,
    }
    if active is not None:
        run = active.profile
        info["running"] = {"label": active.label, "backend": active.exe.parent.name if active.exe.parent != config.RUNTIME_DIR else "runtime",
                           "mode": run.mode, "tier": run.tier, "ctx": run.chat_ctx, "threads": run.chat_threads,
                           "gpu_layers": run.chat_gpu_layers, "vision_audio": run.use_mmproj}
    return info
