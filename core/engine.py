from __future__ import annotations

import threading
from typing import Any, Callable

from ai.llm import LLMClient
from media import store as media_store
from config import find_mmproj, CONVERSATIONS_PATH, MAX_AGENT_STEPS
from conversation.manager import ConversationManager
from ingestion.manager import IngestionManager
from knowledge.store import KnowledgeStore
from retrieval.search import SearchEngine
from tools.builtins import register_builtins
from tools.registry import ToolRegistry
from .agent import AgentLoop


class KoraEngine:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.store = KnowledgeStore()
        self.conversations = ConversationManager(CONVERSATIONS_PATH)
        self.search = SearchEngine(self.store, self.llm)
        self.ingestion = IngestionManager(self.store, self.llm)
        self.tools = ToolRegistry()
        register_builtins(self.tools, self.store, self.search, self.conversations)
        self.lock = threading.RLock()
        self.progress = {"active": False, "stage": "idle", "message": "Ready."}

    def set_progress(self, stage: str, message: str) -> None:
        self.progress = {"active": True, "stage": stage, "message": message}

    def finish_progress(self) -> None:
        self.progress = {"active": False, "stage": "idle", "message": "Ready."}

    def sync(self) -> dict[str, Any]:
        with self.lock:
            self.set_progress("indexing", "Indexing workspace.")
            try:
                return self.ingestion.sync(lambda state: self.set_progress("indexing", f"{state['current']}/{state['total']} — {state['status']}: {state['file']}"))
            finally:
                self.finish_progress()

    def _append_user(self, session_id: str, text: str, attachments: list[dict[str, Any]] | None, voice: bool = False) -> None:
        message: dict[str, Any] = {"role": "user", "content": text}
        if attachments:
            message["attachments"] = attachments
        if voice:
            message["voice"] = True
        self.conversations.append(session_id, message)

    def chat(self, session_id: str, user_text: str, attachments: list[dict[str, Any]] | None = None, voice: bool = False) -> dict[str, Any]:
        with self.lock:
            if not session_id:
                session_id = self.conversations.create()
            self._append_user(session_id, user_text, attachments, voice)
            history = media_store.to_llm_messages(self.conversations.get(session_id), multimodal_ok=bool(find_mmproj()))
            agent = AgentLoop(self.llm, self.tools, MAX_AGENT_STEPS, self.set_progress)
            try:
                result = agent.run(history)
                self.conversations.append(session_id, {"role": "assistant", "content": result["answer"], "sources": result.get("sources", [])})
                return {"session_id": session_id, **result}
            finally:
                self.finish_progress()

    def chat_stream(self, session_id: str, user_text: str, attachments: list[dict[str, Any]] | None = None, voice: bool = False):
        """Streaming counterpart to chat(). Yields the same {"type": "delta", ...}
        events AgentLoop.run_stream() produces, then a final {"type": "final",
        "session_id": ..., "answer": ..., "sources": ..., "steps": ...} event
        once the assistant's reply has been generated and saved to history --
        same persistence contract as chat(), just observable as it happens.
        """
        with self.lock:
            if not session_id:
                session_id = self.conversations.create()
            self._append_user(session_id, user_text, attachments, voice)
            history = media_store.to_llm_messages(self.conversations.get(session_id), multimodal_ok=bool(find_mmproj()))
            agent = AgentLoop(self.llm, self.tools, MAX_AGENT_STEPS, self.set_progress)
            try:
                answer, sources, steps = "", [], 0
                for event in agent.run_stream(history):
                    if event["type"] == "delta":
                        yield event
                    else:
                        answer = event["answer"]
                        sources = event.get("sources", [])
                        steps = event.get("steps", 0)
                self.conversations.append(session_id, {"role": "assistant", "content": answer, "sources": sources})
                yield {"type": "final", "session_id": session_id, "answer": answer, "sources": sources, "steps": steps}
            finally:
                self.finish_progress()
