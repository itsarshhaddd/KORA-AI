from __future__ import annotations

import ast
import operator
from typing import Any

from knowledge.store import KnowledgeStore
from retrieval.search import SearchEngine
from conversation.manager import ConversationManager


_ALLOWED = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_calculate(expression: str) -> float | int:
    expression = str(expression or "").strip()
    if not expression:
        raise ValueError("Expression is empty")
    tree = ast.parse(expression, mode="eval")

    def visit(node: ast.AST):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED:
            return _ALLOWED[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED:
            return _ALLOWED[type(node.op)](visit(node.operand))
        raise ValueError("Only numeric arithmetic is allowed")

    result = visit(tree.body)
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def format_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.10f}".rstrip("0").rstrip(".")
    return str(value)


def register_builtins(registry, store: KnowledgeStore, search: SearchEngine, conversations: ConversationManager) -> None:
    registry.register(
        "search_knowledge",
        "Search the user's KORA workspace for relevant information. Use this whenever workspace data may be needed.",
        {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"]},
        lambda args: search.search(args["query"], int(args.get("top_k", 8))),
    )

    registry.register(
        "get_document",
        "Get all indexed records belonging to one document. Use after search identifies a document that needs broader context.",
        {"type": "object", "properties": {"document_id": {"type": "string"}}, "required": ["document_id"]},
        lambda args: store.records_for_document(args["document_id"]),
    )

    registry.register(
        "list_documents",
        "List documents currently indexed in the KORA workspace.",
        {"type": "object", "properties": {}},
        lambda args: store.document_list(),
    )

    registry.register(
        "calculate",
        "Perform a basic numeric calculation after the needed values have been established. The result is exact. Do not expose internal tool syntax in the final answer.",
        {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        lambda args: {"expression": str(args["expression"]), "result": safe_calculate(str(args["expression"]))},
    )

    registry.register(
        "get_conversation_context",
        "Retrieve earlier messages from the current conversation when the current question depends on previous turns.",
        {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]},
        lambda args: conversations.get(args["session_id"]),
    )
