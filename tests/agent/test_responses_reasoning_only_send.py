"""Responses continuation reasoning must survive the generic thinking-only send filter."""

from types import SimpleNamespace

import pytest

from agent.codex_responses_adapter import _normalize_codex_response, _preflight_codex_api_kwargs
from agent.transports.codex import ResponsesApiTransport
from run_agent import AIAgent


_ISSUER = "other:https://responses.example.invalid/v1"
_MODEL = "deepseek-v4-flash"
_TEXT = "  Complete partial reasoning.\nKeep its whitespace.  "
_CONTENT = [{"type": "reasoning_text", "text": _TEXT}]


@pytest.mark.parametrize("kind", ["plaintext", "encrypted", "both"])
@pytest.mark.parametrize("nudge", [False, True], ids=["first-continuation", "nudged-continuation"])
def test_reasoning_only_incomplete_reaches_next_responses_request(kind, nudge):
    item = SimpleNamespace(
        type="reasoning", id="rs_partial", status="incomplete",
        encrypted_content="opaque-partial" if kind != "plaintext" else None,
        content=[SimpleNamespace(type="reasoning_text", text=_TEXT)] if kind != "encrypted" else [],
        summary=[SimpleNamespace(type="summary_text", text="Partial thinking.")],
    )
    normalized, finish_reason = _normalize_codex_response(
        SimpleNamespace(status="incomplete", output=[item],
                        incomplete_details={"reason": "max_output_tokens"}),
        issuer_kind=_ISSUER, issuer_model=_MODEL,
    )
    assert finish_reason == "incomplete"
    interim = {
        "role": "assistant", "content": "", "finish_reason": finish_reason,
        "reasoning": normalized.reasoning, "reasoning_content": normalized.reasoning,
        "codex_reasoning_items": normalized.codex_reasoning_items,
    }
    history = [{"role": "user", "content": "Continue the task"}, interim]
    if nudge:
        history.append({"role": "user", "content": "Continue; produce the result"})
    api_history = AIAgent._drop_thinking_only_and_merge_users(
        history, drop_codex_reasoning_items=False,
    )
    assert any(message is interim for message in api_history)
    kwargs = ResponsesApiTransport().build_kwargs(
        _MODEL, api_history, base_url="https://responses.example.invalid/v1", provider="custom",
    )
    wire = _preflight_codex_api_kwargs(kwargs)["input"]
    reasoning = [item for item in wire if item["type"] == "reasoning"]
    expected = {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Partial thinking."}]}
    if kind != "encrypted":
        expected["content"] = _CONTENT
    if kind != "plaintext":
        expected["encrypted_content"] = "opaque-partial"
    assert reasoning == [expected]
    assert [item["type"] for item in wire] == ["message", "reasoning", "message"] + (["message"] if nudge else [])
    assert interim["codex_reasoning_items"] == normalized.codex_reasoning_items


@pytest.mark.parametrize("native_item", [
    {"type": "reasoning", "content": _CONTENT},
    {"type": "reasoning", "encrypted_content": "opaque"},
    {"type": "reasoning", "content": _CONTENT, "encrypted_content": "opaque"},
])
def test_cross_protocol_still_drops_reasoning_only_native_carriers(native_item):
    history = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "", "reasoning": "Partial thinking.",
         "codex_reasoning_items": [native_item]},
        {"role": "user", "content": "Next"},
    ]
    assert AIAgent._drop_thinking_only_and_merge_users(
        history, drop_codex_reasoning_items=True,
    ) == [{"role": "user", "content": "First\n\nNext"}]
    assert len(history) == 3


@pytest.mark.parametrize("sidecar", [
    None, [], [{}], [{"type": "reasoning"}],
    [{"type": "reasoning", "encrypted_content": ""}],
    [{"type": "reasoning", "encrypted_content": 42}],
    [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": ""}]}],
    [{"type": "reasoning", "content": [{"type": "output_text", "text": "Wrong part type"}]}],
    [{"type": "message", "content": _CONTENT}],
])
def test_invalid_sidecar_does_not_keep_generic_thinking_only_assistant(sidecar):
    message = {"role": "assistant", "content": "", "reasoning": "Generic thinking.",
               "codex_reasoning_items": sidecar}
    assert AIAgent._is_thinking_only_assistant(message, drop_codex_reasoning_items=False)
