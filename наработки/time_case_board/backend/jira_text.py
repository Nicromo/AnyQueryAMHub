"""Извлечение текста из полей Jira (ADF и строки)."""

from __future__ import annotations

from typing import Any


def adf_to_plain(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text") or "")
        parts: list[str] = []
        for c in node.get("content") or []:
            parts.append(adf_to_plain(c))
        if node.get("type") == "paragraph" and parts:
            parts.append("\n")
        return "".join(parts)
    if isinstance(node, list):
        return "".join(adf_to_plain(x) for x in node)
    return ""


def description_plain(desc: Any) -> str:
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        if "content" in desc:
            return adf_to_plain(desc).strip()
        return str(desc)
    return ""

