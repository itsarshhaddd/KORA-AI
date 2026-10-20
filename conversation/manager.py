from __future__ import annotations

import json
import time
import threading
import uuid
from pathlib import Path
from typing import Any


class ConversationManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.sessions: dict[str, list[dict[str, Any]]] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Sessions must be a {session_id: [messages]} dict. Older/corrupt
            # files have shown up here as a bare "[]" list, which silently
            # breaks every dict op (.setdefault, membership, etc.) the first
            # time a chat is sent. Treat anything non-dict as empty instead
            # of crashing the whole /api/chat endpoint.
            self.sessions = data if isinstance(data, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sessions, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def create(self) -> str:
        session_id = uuid.uuid4().hex
        with self.lock:
            self.sessions[session_id] = []
            self.save()
        return session_id

    def get(self, session_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.sessions.setdefault(session_id, []))

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        message.setdefault("ts", time.time())
        with self.lock:
            self.sessions.setdefault(session_id, []).append(message)
            self.save()

    def list(self) -> list[dict[str, Any]]:
        return [{"id": sid, "messages": msgs} for sid, msgs in self.sessions.items()]
