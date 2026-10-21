"""Plaintext Responses reasoning must survive history and compaction for thinking tool use."""

from types import SimpleNamespace

import pytest

from agent.codex_responses_adapter import (
    _chat_messages_to_responses_input, _normalize_codex_response, _preflight_codex_api_kwargs,
)
from agent.context_compressor import _prune_stale_reasoning_replay
from agent.transports.codex import ResponsesApiTransport


_ISSUER = "other:https://responses.example.invalid/v1"
_MODEL = "deepseek-v4-flash"
_TEXT = "  Keep this complete reasoning.\nIncluding its trailing whitespace.  "
_CONTENT = [{"type": "reasoning_text", "text": _TEXT}]


@pytest.mark.parametrize("tool_call", [False, True], ids=["ordinary-turn-with-tools", "tool-result"])
@pytest.mark.parametrize("encrypted", ["", "relay-blob"], ids=["plaintext", "plaintext-with-encrypted"])
def test_plaintext_reasoning_round_trips_into_next_request_with_tools(tool_call, encrypted):
    # Thinking endpoints require complete previous reasoning even on turns that made no tool call.
    tail = (
        SimpleNamespace(type="function_call", id="fc_1", call_id="call_1", status="completed",
                        name="local_probe", arguments="{}")
        if tool_call else
        SimpleNamespace(type="message", id="msg_1", role="assistant", status="completed",
                        content=[SimpleNamespace(type="output_text", text="OK")])
    )
    normalized, finish_reason = _normalize_codex_response(
        SimpleNamespace(status="completed", output=[
            SimpleNamespace(type="reasoning", id="rs_1", status="completed", encrypted_content=encrypted,
                            summary=[], content=[SimpleNamespace(type="reasoning_text", text=_TEXT)]),
            tail,
        ]), issuer_kind=_ISSUER, issuer_model=_MODEL,
    )
    assert normalized.reasoning == _TEXT.strip()
    assert normalized.codex_reasoning_items
    assistant = {"role": "assistant", "content": normalized.content,
                 "codex_reasoning_items": normalized.codex_reasoning_items,
                 "codex_message_items": normalized.codex_message_items}
    history = [{"role": "user", "content": "Probe"}, assistant]
    if tool_call:
        assert finish_reason == "tool_calls"
        assistant["tool_calls"] = [{"id": "call_1", "call_id": "call_1", "type": "function",
                                    "function": {"name": "local_probe", "arguments": "{}"}}]
        history.append({"role": "tool", "tool_call_id": "call_1", "content": "local-only-ok"})
    else:
        assert finish_reason == "stop"
        history.append({"role": "user", "content": "Next"})
    kwargs = ResponsesApiTransport().build_kwargs(
        _MODEL, history,
        [{"type": "function", "function": {"name": "local_probe", "parameters": {"type": "object"}}}],
        base_url="https://responses.example.invalid/v1", provider="custom",
    )
    wire = _preflight_codex_api_kwargs(kwargs)["input"]
    reasoning = [item for item in wire if item["type"] == "reasoning"]
    expected_reasoning = {"type": "reasoning", "content": _CONTENT, "summary": []}
    if encrypted:
        expected_reasoning.update(encrypted_content="relay-blob", summary=[])
    assert reasoning == [expected_reasoning]
    assert "_issuer_kind" not in str(wire) and "_issuer_model" not in str(wire)
    if tool_call:
        assert [item["type"] for item in wire] == ["message", "reasoning", "function_call", "function_call_output"]
        assert wire[-2]["call_id"] == wire[-1]["call_id"] == "call_1"
    else:
        assert [item["type"] for item in wire] == ["message", "reasoning", "message", "message"]


def test_compaction_keeps_plaintext_reasoning_but_prunes_stale_encrypted_items():
    # Losing a retained assistant turn's plaintext reasoning makes the next tools request HTTP 400.
    plaintext = {"type": "reasoning", "id": "rs_plain", "content": _CONTENT,
                 "_issuer_kind": _ISSUER, "_issuer_model": _MODEL}
    messages = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "OK", "codex_reasoning_items": [
            plaintext, {"type": "reasoning", "encrypted_content": "stale-blob"},
            {"type": "compaction", "encrypted_content": "old-checkpoint"},
        ]},
        {"role": "user", "content": "Second"},
        {"role": "assistant", "content": "OK", "codex_reasoning_items": [
            {"type": "compaction", "encrypted_content": "new-checkpoint"},
        ]},
        {"role": "user", "content": "Next"},
    ]
    _prune_stale_reasoning_replay(messages)
    assert messages[1]["codex_reasoning_items"] == [plaintext]
    assert messages[3]["codex_reasoning_items"] == [
        {"type": "compaction", "encrypted_content": "new-checkpoint"},
    ]


def test_transient_encrypted_item_keeps_complete_plaintext_reasoning():
    normalized, _ = _normalize_codex_response(SimpleNamespace(status="completed", output=[
        SimpleNamespace(type="reasoning", id="rs_tmp_probe", encrypted_content="transient-blob",
                        summary=[], content=[SimpleNamespace(type="reasoning_text", text=_TEXT)]),
        SimpleNamespace(type="message", role="assistant", status="completed",
                        content=[SimpleNamespace(type="output_text", text="OK")]),
    ]), issuer_kind=_ISSUER, issuer_model=_MODEL)
    assert normalized.codex_reasoning_items == [{
        "type": "reasoning", "content": _CONTENT, "summary": [],
        "_issuer_kind": _ISSUER, "_issuer_model": _MODEL,
    }]


@pytest.mark.parametrize("issuer,model,want_count", [
    (_ISSUER, _MODEL, 1),
    ("other:https://different.example.invalid/v1", _MODEL, 0),
    (_ISSUER, "different-model", 0),
])
def test_plaintext_reasoning_replay_obeys_issuer_and_model(issuer, model, want_count):
    history = [{"role": "assistant", "content": "OK", "codex_reasoning_items": [
        {"type": "reasoning", "content": _CONTENT, "_issuer_kind": _ISSUER, "_issuer_model": _MODEL},
    ]}]
    wire = _chat_messages_to_responses_input(
        history, current_issuer_kind=issuer, current_issuer_model=model,
    )
    reasoning = [item for item in wire if item["type"] == "reasoning"]
    assert len(reasoning) == want_count
    if want_count:
        assert reasoning == [{"type": "reasoning", "content": _CONTENT}]


def test_compaction_keeps_active_turn_bridging_reasoning_and_latest_checkpoint():
    active_items = [{"type": "reasoning", "encrypted_content": "active-blob"}]
    messages = [
        {"role": "user", "content": "Prior turn"},
        {"role": "assistant", "content": "OK", "codex_reasoning_items": [
            {"type": "reasoning", "encrypted_content": "stale-blob"},
            {"type": "compaction", "encrypted_content": "latest-checkpoint"},
        ]},
        {"role": "user", "content": "Active turn"},
        {"role": "assistant", "content": "", "codex_reasoning_items": active_items,
         "tool_calls": [{"id": "call_1", "function": {"name": "local_probe", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "local-only-ok"},
        {"role": "assistant", "content": "OK", "codex_reasoning_items": active_items},
    ]
    _prune_stale_reasoning_replay(messages)
    assert messages[1]["codex_reasoning_items"] == [
        {"type": "compaction", "encrypted_content": "latest-checkpoint"},
    ]
    assert messages[3]["codex_reasoning_items"] == active_items
    assert messages[5]["codex_reasoning_items"] == active_items


def test_compaction_without_real_user_boundary_preserves_replay_items():
    items = [{"type": "reasoning", "content": _CONTENT},
             {"type": "reasoning", "encrypted_content": "context-blob"}]
    messages = [{"role": "assistant", "content": "Existing context", "codex_reasoning_items": items}]
    assert _prune_stale_reasoning_replay(messages) == 0
    assert messages[0]["codex_reasoning_items"] == items


@pytest.mark.parametrize("tool_call", [False, True], ids=["ordinary-final", "tool-call"])
@pytest.mark.parametrize("echo_text", [" ", _TEXT], ids=["no-thinking-space", "controlled-existing-text"])
def test_responses_maps_existing_reasoning_echo_field(tool_call, echo_text):
    assistant = {"role": "assistant", "content": "" if tool_call else "OK",
                 "reasoning_content": echo_text}
    history = [{"role": "user", "content": "Probe"}, assistant]
    if tool_call:
        assistant["tool_calls"] = [{"id": "call_1", "type": "function",
                                    "function": {"name": "local_probe", "arguments": "{}"}}]
        history.append({"role": "tool", "tool_call_id": "call_1", "content": "local-only-ok"})
    kwargs = ResponsesApiTransport().build_kwargs(
        _MODEL, history, [], provider="custom", base_url="https://responses.example.invalid/v1",
        reasoning_content_echo=True,
    )
    wire = _preflight_codex_api_kwargs(kwargs)["input"]
    assert [item for item in wire if item["type"] == "reasoning"] == [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": echo_text}], "summary": []},
    ]
    assert "codex_reasoning_items" not in assistant  # Send conversion must not change canonical history.
    assert assistant["reasoning_content"] == echo_text
    if tool_call:
        assert [item["type"] for item in wire][-3:] == ["reasoning", "function_call", "function_call_output"]


def test_responses_echo_is_explicit_and_does_not_recreate_foreign_native_reasoning():
    assistant = {"role": "assistant", "content": "OK", "reasoning_content": _TEXT,
                 "codex_reasoning_items": [{"type": "reasoning", "content": _CONTENT,
                    "_issuer_kind": "other:https://different.example.invalid/v1", "_issuer_model": _MODEL}]}
    transport = ResponsesApiTransport()
    for model, echo in [(_MODEL, True), (_MODEL, False), ("gpt-5", False)]:
        wire = _preflight_codex_api_kwargs(transport.build_kwargs(
            model, [assistant], [], provider="custom", base_url="https://responses.example.invalid/v1",
            reasoning_content_echo=echo,
        ))["input"]
        assert not any(item["type"] == "reasoning" for item in wire)
    # Even a legacy echo field is not sent unless the active provider's echo policy opts in.
    legacy = {"role": "assistant", "content": "OK", "reasoning_content": " "}
    wire = _preflight_codex_api_kwargs(transport.build_kwargs("gpt-5", [legacy], []))["input"]
    assert not any(item["type"] == "reasoning" for item in wire)


def test_whitespace_only_native_reasoning_survives_capture_and_preflight():
    normalized, _ = _normalize_codex_response(SimpleNamespace(status="completed", output=[
        SimpleNamespace(type="reasoning", id="rs_space", content=[SimpleNamespace(type="reasoning_text", text=" ")]),
        SimpleNamespace(type="message", role="assistant", status="completed",
                        content=[SimpleNamespace(type="output_text", text="OK")]),
    ]), issuer_kind=_ISSUER, issuer_model=_MODEL)
    history = [{"role": "assistant", "content": "OK", "codex_reasoning_items": normalized.codex_reasoning_items}]
    wire = _preflight_codex_api_kwargs(ResponsesApiTransport().build_kwargs(
        _MODEL, history, [], provider="custom", base_url="https://responses.example.invalid/v1",
    ))["input"]
    assert [item for item in wire if item["type"] == "reasoning"] == [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": " "}], "summary": []},
    ]


def test_agent_codex_builder_threads_existing_reasoning_echo_policy():
    from agent.chat_completion_helpers import _build_codex_kwargs

    transport = ResponsesApiTransport()
    agent = SimpleNamespace(
        model=_MODEL, provider="custom", base_url="https://responses.example.invalid/v1",
        api_mode="codex_responses", max_tokens=128, session_id="test-echo-policy",
        _get_transport=lambda: transport, _prepare_messages_for_non_vision_model=lambda messages: messages,
        _resolved_api_call_timeout=lambda: 10, _needs_thinking_reasoning_pad=lambda: True,
    )
    kwargs = _build_codex_kwargs(agent, [
        {"role": "assistant", "content": "OK", "reasoning_content": " "},
    ], [], {"enabled": True, "effort": "xhigh"}, {}, "test-echo-policy")
    wire = _preflight_codex_api_kwargs(kwargs)["input"]
    assert [item for item in wire if item["type"] == "reasoning"] == [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": " "}], "summary": []},
    ]


@pytest.mark.parametrize("summary", [None, [], [{"type": "summary_text", "text": "Brief summary"}]],
                         ids=["missing-summary", "empty-summary", "preserved-summary"])
def test_plaintext_preflight_includes_required_reasoning_summary(summary):
    # OpenAI-compatible strict Responses validators require summary even for plaintext reasoning.
    item = {"type": "reasoning", "content": _CONTENT}
    if summary is not None:
        item["summary"] = summary
    wire = _preflight_codex_api_kwargs({"model": "glm-5.3-flash", "instructions": "Synthetic probe", "input": [
        item, {"type": "message", "role": "assistant", "content": "OK"},
    ]})["input"]
    assert wire[0] == {"type": "reasoning", "summary": summary or [], "content": _CONTENT}
