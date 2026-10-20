from __future__ import annotations

import json
import re
from typing import Any, Callable

from ai.prompts import SYSTEM_PROMPT
from ai.llm import LLMClient
from tools.registry import ToolRegistry


class AgentLoop:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps: int = 8, progress: Callable[[str, str], None] | None = None) -> None:
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.progress = progress or (lambda stage, message: None)

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{value:,.10f}".rstrip("0").rstrip(".")
        return str(value)

    @classmethod
    def _clean_final_answer(cls, content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return "I couldn't produce an answer."

        text = re.sub(r"<\|[^>]+\|>", "", text)
        text = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", text, flags=re.IGNORECASE)
        if "\\n" in text or "\\t" in text:
            text = text.replace("\\n", "\n").replace("\\t", "\t")
        text = text.replace('\\"', '"').strip()

        candidate = text
        if candidate.startswith("```") and candidate.endswith("```"):
            candidate = re.sub(r"^```(?:json|text)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate).strip()
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and "result" in parsed:
            return f"The result is {cls._format_value(parsed['result'])}."

        # Normalize the pseudo-separator syntax that some local model builds can emit.
        text = re.sub(r"\\+(time|date_time|datetime|date|calculate|calculator|result)\b\s*", r"\1: ", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(time|date_time|datetime|date|calculate|calculator|result)\\+\s*", r"\1: ", text, flags=re.IGNORECASE)
        return text.strip() or "I couldn't produce an answer."

    def _run_tool_calls(self, working: list[dict[str, Any]], tool_calls: list[dict[str, Any]], used_sources: list[dict[str, Any]], assistant_content: str = "") -> None:
        working.append({"role": "assistant", "content": assistant_content, "tool_calls": tool_calls})
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name")
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                self.progress("tool", f"Using {name}.")
                result = self.tools.execute(name, arguments)
                if name in {"search_knowledge", "get_document"}:
                    if isinstance(result, list):
                        for item in result:
                            if isinstance(item, dict) and item.get("record_id"):
                                used_sources.append({k: item.get(k) for k in ("record_id", "source", "path", "page", "sheet", "row", "slide", "section")})
                if name == "calculate" and isinstance(result, dict) and "result" in result:
                    # Give the model a natural-language calculation result instead of raw JSON.
                    result_text = (
                        f"The calculation for {result.get('expression', '')} produced {self._format_value(result['result'])}. "
                        "Use that result in your final answer and do not expose this internal tool message."
                    )
                else:
                    result_text = json.dumps(result, ensure_ascii=False)
            except Exception as exc:
                result_text = json.dumps({"error": str(exc)}, ensure_ascii=False)
            working.append({"role": "tool", "tool_call_id": call.get("id", name), "content": result_text})

    def run(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        working = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
        used_sources: list[dict[str, Any]] = []

        for step in range(self.max_steps):
            self.progress("thinking", f"Understanding your request...")
            response = self.llm.chat(working, self.tools.schemas_for_model())
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                content = self._clean_final_answer(message.get("content") or "")
                return {"answer": content, "sources": used_sources, "steps": step + 1}

            self._run_tool_calls(working, tool_calls, used_sources, message.get("content") or "")

        return {
            "answer": "I could not complete the required information-gathering steps within the available reasoning cycle.",
            "sources": used_sources,
            "steps": self.max_steps,
        }

    def run_stream(self, messages: list[dict[str, Any]]):
        """Streaming counterpart to run().

        Yields {"type": "delta", "text": ...} chunks live as the model
        produces the *final* answer (the step with no tool_calls). Steps
        where the model calls a tool are still executed synchronously and
        are not streamed -- there's nothing user-facing to show until the
        tool result comes back and the model reacts to it, so those steps
        behave exactly like run()'s tool-calling loop, and progress is
        still reported through self.progress() for the existing "thinking"
        indicator. The loop ends with a single {"type": "final", ...} event
        carrying the same shape run() returns.
        """
        working = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
        used_sources: list[dict[str, Any]] = []

        for step in range(self.max_steps):
            self.progress("thinking", f"Understanding your request...")
            content_parts: list[str] = []
            tool_calls_acc: dict[int, dict[str, Any]] = {}
            is_tool_step = False

            for chunk in self.llm.chat_stream(working, self.tools.schemas_for_model()):
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                delta_tool_calls = delta.get("tool_calls")
                delta_content = delta.get("content")

                if delta_tool_calls:
                    is_tool_step = True
                    for tc in delta_tool_calls:
                        idx = tc.get("index", 0)
                        slot = tool_calls_acc.setdefault(idx, {"id": tc.get("id") or f"call_{idx}", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]

                if delta_content:
                    content_parts.append(delta_content)
                    if not is_tool_step:
                        yield {"type": "delta", "text": delta_content}

            if is_tool_step and tool_calls_acc:
                tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                self._run_tool_calls(working, tool_calls, used_sources, "".join(content_parts))
                continue

            content = self._clean_final_answer("".join(content_parts))
            yield {"type": "final", "answer": content, "sources": used_sources, "steps": step + 1}
            return

        yield {
            "type": "final",
            "answer": "I could not complete the required information-gathering steps within the available reasoning cycle.",
            "sources": used_sources,
            "steps": self.max_steps,
        }
