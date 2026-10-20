from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from config import CHAT_URL, EMBED_BATCH_SIZE, EMBED_DOC_PREFIX, EMBED_QUERY_PREFIX, EMBED_URL


class LLMClientError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, timeout: int = 300) -> None:
        self.timeout = timeout

    def _post(self, base: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{base}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LLMClientError(f"Local LLM service unavailable: {exc}") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMClientError(f"Invalid LLM response: {exc}") from exc

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
        return self._post(CHAT_URL, "/v1/chat/completions", payload)

    def chat_stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None):
        """Stream chat completion chunks from llama-server's OpenAI-compatible SSE endpoint.

        Yields each parsed `data: {...}` chunk as a dict (the raw OpenAI-shaped
        delta payload) so the caller can inspect both `delta.content` and
        `delta.tool_calls` fragments itself. A `data: [DONE]` sentinel line
        ends the stream and is swallowed here rather than yielded.
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
        request = urllib.request.Request(
            f"{CHAT_URL}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
        except urllib.error.URLError as exc:
            raise LLMClientError(f"Local LLM service unavailable: {exc}") from exc

    def transcribe(self, audio_part: dict[str, Any], language_hint: str = "") -> str:
        """Ask the multimodal chat model to transcribe one audio clip (<= ~30 s)."""
        hint = f" The speaker may be using {language_hint}." if language_hint else ""
        messages = [
            {"role": "system", "content": "You are a precise speech-to-text engine. Output only the exact words spoken, with natural punctuation. No commentary, no quotes, no labels. If there is no intelligible speech, output nothing."},
            {"role": "user", "content": [audio_part, {"type": "text", "text": "Transcribe this audio." + hint}]},
        ]
        payload = {"messages": messages, "temperature": 0.0, "stream": False, "max_tokens": 400}
        result = self._post(CHAT_URL, "/v1/chat/completions", payload)
        try:
            return str(result["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError(f"Invalid transcription response: {exc}") from exc

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        """Embed texts. kind is "document" (indexing) or "query" (searching);
        the matching EmbeddingGemma task prefix is prepended automatically."""
        if not texts:
            return []
        prefix = EMBED_QUERY_PREFIX if kind == "query" else EMBED_DOC_PREFIX
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [prefix + t for t in texts[start:start + EMBED_BATCH_SIZE]]
            result = self._post(EMBED_URL, "/v1/embeddings", {"input": batch})
            data = result.get("data") or []
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors.extend(item["embedding"] for item in ordered)
        return vectors
