"""Legacy native sidecars must retain the configured plaintext echo on replay."""

import pytest

from agent.codex_responses_adapter import _preflight_codex_api_kwargs
from agent.transports.codex import ResponsesApiTransport


@pytest.mark.parametrize("echo", [False, True])
def test_legacy_encrypted_sidecar_uses_existing_echo_only_when_enabled(echo):
    native = {"type": "reasoning", "encrypted_content": "opaque", "summary": []}
    message = {
        "role": "assistant",
        "content": "OK",
        "reasoning_content": "legacy text",
        "codex_reasoning_items": [native],
    }
    wire = _preflight_codex_api_kwargs(
        ResponsesApiTransport().build_kwargs(
            "deepseek-v4-flash",
            [message],
            provider="custom",
            base_url="https://relay.example.invalid/v1",
            reasoning_content_echo=echo,
        )
    )["input"]
    item = next(item for item in wire if item["type"] == "reasoning")
    assert item.get("content") == ([{"type": "reasoning_text", "text": "legacy text"}] if echo else None)
    assert "content" not in native
