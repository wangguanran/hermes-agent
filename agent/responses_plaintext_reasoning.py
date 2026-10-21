"""Lossless plaintext reasoning carriers for Responses-compatible thinking endpoints."""

from typing import Any


def reasoning_content(item: Any) -> list[dict[str, str]]:
    """Keep complete reasoning text, including whitespace required for exact replay."""
    parts = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
    if not isinstance(parts, list):
        return []
    result = []
    for part in parts:
        kind = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if kind == "reasoning_text" and isinstance(text, str) and text:
            result.append({"type": "reasoning_text", "text": text})
    return result


def has_reasoning_payload(item: Any) -> bool:
    """Whether a sidecar can carry native reasoning through continuation filtering."""
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return False
    encrypted = item.get("encrypted_content")
    return bool(reasoning_content(item)) or isinstance(encrypted, str) and bool(encrypted.strip())


def apply_legacy_echo(message: dict, native_items: list[dict]) -> list[dict]:
    """Map an existing echo into the wire copy without recreating foreign native state."""
    echo = message.get("reasoning_content")
    if not isinstance(echo, str) or not echo or any(reasoning_content(item) for item in native_items):
        return native_items
    content = [{"type": "reasoning_text", "text": echo}]
    for index in range(len(native_items) - 1, -1, -1):
        if native_items[index].get("type") == "reasoning":
            result = list(native_items)
            result[index] = {**result[index], "content": content}
            return result
    if not message.get("codex_reasoning_items"):
        return [{"type": "reasoning", "content": content, "summary": []}]
    return native_items
