from __future__ import annotations

import math
import re
from typing import Any

from config import TOP_K
from knowledge.store import KnowledgeStore
from ai.llm import LLMClient


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w]+", text.lower()))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class SearchEngine:
    def __init__(self, store: KnowledgeStore, llm: LLMClient) -> None:
        self.store = store
        self.llm = llm

    def search(self, query: str, top_k: int = TOP_K) -> list[dict[str, Any]]:
        records = self.store.all_records()
        if not records:
            return []
        query_embedding = self.llm.embed([query], kind="query")[0]
        qtokens = _tokens(query)
        scored = []
        for record in records:
            content = record.get("content", "")
            tokens = _tokens(content)
            lexical = len(qtokens & tokens) / max(len(qtokens), 1)
            semantic = _cosine(query_embedding, record.get("embedding", []))
            score = 0.75 * semantic + 0.25 * lexical
            scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        for score, record in scored[:top_k]:
            results.append({
                "record_id": record.get("record_id"),
                "content": record.get("content", ""),
                "source": record.get("source"),
                "path": record.get("path"),
                "page": record.get("page"),
                "sheet": record.get("sheet"),
                "row": record.get("row"),
                "slide": record.get("slide"),
                "section": record.get("section"),
                "score": round(float(score), 5),
            })
        return results
