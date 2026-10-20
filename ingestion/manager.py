from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from .extractor import SUPPORTED_EXTENSIONS, extract_file, normalize_records

from knowledge.store import KnowledgeStore
from ai.llm import LLMClient


class IngestionManager:
    def __init__(self, store: KnowledgeStore, llm: LLMClient) -> None:
        self.store = store
        self.llm = llm

    @staticmethod
    def document_id(path: Path) -> str:
        return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]

    def scan(self) -> list[Path]:
        return sorted(
            [p for p in Path(self.store.path).parent.parent.joinpath("workspace").rglob("*")
             if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS],
            key=lambda p: str(p).lower(),
        )

    def sync(self, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        files = self.scan()
        existing = {str(Path(m.get("path", "")).resolve()): m for m in self.store.document_list()}
        seen: set[str] = set()
        indexed = failed = 0

        for number, path in enumerate(files, 1):
            resolved = str(path.resolve())
            seen.add(resolved)
            document_id = self.document_id(path)
            digest = self.store.file_hash(path)
            previous = existing.get(resolved)
            if previous and previous.get("file_hash") == digest:
                if progress:
                    progress({"current": number, "total": len(files), "file": str(path), "status": "unchanged"})
                continue

            try:
                raw_records = normalize_records(extract_file(path))
                records = []
                for index, record in enumerate(raw_records):
                    item = dict(record)
                    item["document_id"] = document_id
                    item["record_id"] = f"{document_id}:{index}:{record.get('record_id', index)}"
                    records.append(item)

                embeddings = self.llm.embed([r["content"] for r in records]) if records else []
                for record, embedding in zip(records, embeddings):
                    record["embedding"] = embedding

                metadata = {
                    "document_id": document_id,
                    "path": resolved,
                    "source": path.name,
                    "file_type": path.suffix.lower(),
                    "file_hash": digest,
                    "record_count": len(records),
                }
                self.store.replace_document(document_id, metadata, records)
                indexed += 1
                status = "indexed"
            except Exception as exc:
                failed += 1
                status = f"failed: {exc}"

            if progress:
                progress({"current": number, "total": len(files), "file": str(path), "status": status})

        for resolved, metadata in list(existing.items()):
            if resolved not in seen:
                self.store.remove_document(metadata["document_id"])

        return {"files": len(files), "indexed": indexed, "failed": failed}
