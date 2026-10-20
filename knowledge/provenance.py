from __future__ import annotations

from typing import Any


def source_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "source": record.get("source"),
        "path": record.get("path"),
        "page": record.get("page"),
        "sheet": record.get("sheet"),
        "row": record.get("row"),
        "slide": record.get("slide"),
        "section": record.get("section"),
    }
