from __future__ import annotations

import json
from typing import Any, Callable


class ToolRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.schemas: list[dict[str, Any]] = []

    def register(self, name: str, description: str, parameters: dict[str, Any], handler: Callable[[dict[str, Any]], Any]) -> None:
        self.handlers[name] = handler
        self.schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.handlers:
            raise ValueError(f"Unknown tool: {name}")
        return self.handlers[name](arguments)

    def schemas_for_model(self) -> list[dict[str, Any]]:
        return self.schemas
