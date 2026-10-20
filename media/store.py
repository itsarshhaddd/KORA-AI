"""Storage and LLM-message helpers for chat attachments (images + audio).

Files live in data/media/<id>.<ext>. Conversations only keep small metadata
({id, kind, name, mime}) so history stays light; the bytes are re-read when a
message is turned into an OpenAI-style multimodal payload for llama-server.
"""
from __future__ import annotations

import base64
import io
import re
import uuid
from pathlib import Path
from typing import Any

from config import MEDIA_DIR, MEDIA_IMAGE_MAX_EDGE

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3"}  # llama.cpp decodes wav/mp3; the browser converts everything else to wav
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class MediaError(ValueError):
    pass


def classify(filename: str, mime: str = "") -> str | None:
    ext = Path(filename or "").suffix.lower()
    if mime.startswith("image/") or ext in IMAGE_EXTS:
        return "image"
    if mime.startswith("audio/") or ext in AUDIO_EXTS:
        return "audio"
    return None


def _prepare_image(raw: bytes) -> tuple[bytes, str]:
    """Downscale big photos (keeps vision-token cost and request size sane) and
    flatten to JPEG/PNG. Falls back to the original bytes if Pillow can't read it."""
    try:
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        img.load()
        if max(img.size) > MEDIA_IMAGE_MAX_EDGE:
            img.thumbnail((MEDIA_IMAGE_MAX_EDGE, MEDIA_IMAGE_MAX_EDGE))
        out = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img.convert("RGBA").save(out, "PNG")
            return out.getvalue(), ".png"
        img.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue(), ".jpg"
    except Exception as exc:
        raise MediaError(f"Could not read that image: {exc}") from exc


def save_upload(filename: str, mime: str, raw: bytes) -> dict[str, Any]:
    kind = classify(filename, mime)
    if kind is None:
        raise MediaError("Only images (png, jpg, webp, gif) and audio (wav, mp3) are supported.")
    if not raw:
        raise MediaError("Empty file.")
    if kind == "image":
        raw, ext = _prepare_image(raw)
    else:
        ext = ".mp3" if Path(filename or "").suffix.lower() == ".mp3" or mime in ("audio/mpeg", "audio/mp3") else ".wav"
    media_id = uuid.uuid4().hex
    (MEDIA_DIR / f"{media_id}{ext}").write_bytes(raw)
    return {"id": media_id, "kind": kind, "name": Path(filename or f"{kind}{ext}").name, "ext": ext}


def path_for(media_id: str) -> Path | None:
    if not _ID_RE.match(str(media_id or "")):
        return None
    for candidate in MEDIA_DIR.glob(f"{media_id}.*"):
        return candidate
    return None


def describe(media_id: str) -> dict[str, Any] | None:
    path = path_for(media_id)
    if not path:
        return None
    ext = path.suffix.lower()
    kind = "image" if ext in IMAGE_EXTS else "audio"
    return {"id": media_id, "kind": kind, "ext": ext}


def image_part(raw: bytes, ext: str) -> dict[str, Any]:
    mime = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}}


def audio_part(raw: bytes, ext: str) -> dict[str, Any]:
    return {"type": "input_audio", "input_audio": {"data": base64.b64encode(raw).decode(), "format": "mp3" if ext == ".mp3" else "wav"}}


def media_parts(media_id: str) -> dict[str, Any] | None:
    path = path_for(media_id)
    if not path:
        return None
    raw = path.read_bytes()
    return audio_part(raw, path.suffix.lower()) if path.suffix.lower() in AUDIO_EXTS else image_part(raw, path.suffix.lower())


def to_llm_messages(history: list[dict[str, Any]], multimodal_ok: bool = True) -> list[dict[str, Any]]:
    """Turn stored conversation messages into llama-server chat messages.

    Only the *latest* user turn carries real image/audio bytes; earlier turns get a
    short text placeholder so a long chat with photos doesn't blow the context window.
    """
    last_user = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=-1)
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(history):
        role = msg.get("role")
        if role not in ("user", "assistant", "system", "tool"):
            continue
        content = msg.get("content", "")
        atts = msg.get("attachments") or []
        if role == "user" and atts:
            if i == last_user and multimodal_ok:
                parts: list[dict[str, Any]] = []
                for att in atts:
                    part = media_parts(att.get("id", ""))
                    if part:
                        parts.append(part)
                parts.append({"type": "text", "text": content or "(no text — respond to the attached media)"})
                out.append({"role": "user", "content": parts})
                continue
            notes = ", ".join(f"[{a.get('kind', 'file')}: {a.get('name', 'attachment')}]" for a in atts)
            content = f"{notes} {content}".strip()
        out.append({"role": role, "content": content})
    return out
