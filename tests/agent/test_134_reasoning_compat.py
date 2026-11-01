from agent.codex_responses_adapter import _chat_messages_to_responses_input


def test_134_deepseek_legacy_tool_call_gets_existing_space_pad():
    messages = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "probe", "arguments": "{}"}}
    ]}]
    wire = _chat_messages_to_responses_input(
        messages, current_issuer_kind="other:https://relay.example.invalid/v1",
        current_issuer_model="deepseek-v4-flash",
    )
    assert [x for x in wire if x["type"] == "reasoning"] == [
        {"type": "reasoning", "summary": [], "content": [{"type": "reasoning_text", "text": " "}]}
    ]
