from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from config import EMBED_MODEL, INDEX_PATH, WORKSPACE_DIR


class KnowledgeStore:
    def __init__(self, path: Path = INDEX_PATH) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {"version": 1, "documents": {}, "records": []}
        self.load()

    def load(self) -> None:
        with self.lock:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("version", 1)
            self.data.setdefault("documents", {})
            self.data.setdefault("records", [])
            self._invalidate_if_embed_model_changed()

    def _invalidate_if_embed_model_changed(self) -> None:
        """Vectors from different embedding models are not comparable (different
        dimensions/spaces). If the model changed, drop old vectors and clear file
        hashes so the next sync re-embeds every document."""
        previous = self.data.get("embed_model")
        if previous == EMBED_MODEL:
            return
        if self.data["records"] or self.data["documents"]:
            for record in self.data["records"]:
                record.pop("embedding", None)
            for metadata in self.data["documents"].values():
                metadata["file_hash"] = None
            print(f"[KORA] Embedding model changed ({previous or 'unknown'} -> {EMBED_MODEL}); documents will be re-indexed.")
        self.data["embed_model"] = EMBED_MODEL
        self.save()

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)

    @staticmethod
    def file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def all_records(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.data.get("records", []))

    def records_for_document(self, document_id: str) -> list[dict[str, Any]]:
        return [r for r in self.all_records() if r.get("document_id") == document_id]

    def replace_document(self, document_id: str, metadata: dict[str, Any], records: list[dict[str, Any]]) -> None:
        with self.lock:
            self.data["documents"][document_id] = metadata
            self.data["records"] = [r for r in self.data["records"] if r.get("document_id") != document_id]
            self.data["records"].extend(records)
            self.save()

    def remove_document(self, document_id: str) -> None:
        with self.lock:
            self.data["documents"].pop(document_id, None)
            self.data["records"] = [r for r in self.data["records"] if r.get("document_id") != document_id]
            self.save()

    def document_for_path(self, path: Path) -> tuple[str, dict[str, Any]] | None:
        resolved = str(path.resolve())
        for document_id, metadata in self.data.get("documents", {}).items():
            if metadata.get("path") == resolved:
                return document_id, metadata
        return None

    def document_list(self) -> list[dict[str, Any]]:
        return list(self.data.get("documents", {}).values())
