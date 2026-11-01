import json
import queue
from types import SimpleNamespace

from gateway.run_turn_runner import TurnRunner


def _turn(*, mode="verbose", enabled=True):
    progress_queue = queue.Queue()
    ctx = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="chat-1"),
        progress_mode=mode,
        tool_progress_enabled=enabled,
        progress_queue=progress_queue,
        log_queue=None,
        long_tool_hint_fired=[True],
        _run_still_current=lambda: True,
        _live_status_adapter=None,
        _live_status_mode="off",
        _native_slack_task_cards=False,
        last_tool=[None],
        last_was_terminal_block=[False],
        agent_holder=[None],
    )
    turn = TurnRunner(SimpleNamespace(_delivery_adapter_for=lambda _source: SimpleNamespace(supports_code_blocks=True)), ctx)
    return turn, progress_queue


def _messages(progress_queue):
    results = []
    while not progress_queue.empty():
        results.append(progress_queue.get_nowait())
    return results


def test_wecom_dm_progress_filters_secrets_and_host_paths():
    turn, progress_queue = _turn()
    turn._ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    turn._ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    command = "printf hello; echo token=super-secret-value-123456; ls /Users/example/private"
    result = json.dumps({"output": "hello\ntoken=super-secret-value-123456\n/Volumes/private/a.txt", "exit_code": 0})
    turn.progress_callback("tool.started", "terminal", None, {"command": command}, tool_call_id="c1")
    turn.progress_callback("tool.completed", "terminal", None, {"command": command},
                           result=result, is_error=False, tool_call_id="c1")
    messages = _messages(progress_queue)
    assert len(messages) == 2
    assert "printf hello" in messages[0]
    assert "hello" in messages[1]
    assert "super-secret-value-123456" not in "".join(messages)
    assert "/Users/example/private" not in "".join(messages)
    assert "/Volumes/private/a.txt" not in "".join(messages)


def test_wecom_dm_suppresses_sensitive_command_output():
    turn, progress_queue = _turn()
    turn._ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    turn._ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    turn.progress_callback("tool.started", "terminal", None, {"command": "printenv"}, tool_call_id="c2")
    turn.progress_callback("tool.completed", "terminal", None, None, tool_call_id="c2",
                           result=json.dumps({"output": "OPAQUE_VALUE=unguessable-private-value", "exit_code": 0}))
    messages = _messages(progress_queue)
    assert len(messages) == 2
    assert "unguessable-private-value" not in "".join(messages)
    assert "已隐藏" in messages[1]


def test_wecom_terminal_completion_uses_real_executor_args_without_call_id():
    turn, progress_queue = _turn()
    turn._ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    turn._ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    command = {"command": "printf hello"}
    turn.progress_callback("tool.started", "terminal", None, command)
    turn.progress_callback("tool.completed", "terminal", None, command,
                           result=json.dumps({"output": "hello", "exit_code": 0}))
    messages = _messages(progress_queue)
    assert len(messages) == 2
    assert "hello" in messages[1]
    assert "已隐藏" not in messages[1]


def test_wecom_private_progress_redacts_quoted_secrets_and_multiple_host_path_styles():
    turn, progress_queue = _turn()
    turn._ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    turn._ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    args = {"password": "alpha phrase omega", "paths": ["/home/alice/private.txt", "/home/rantu/ok.txt"]}
    turn.progress_callback("tool.started", "search_files", None, args)
    text = "".join(_messages(progress_queue))
    assert "alpha phrase omega" not in text
    assert "phrase omega" not in text
    assert "/home/alice/private.txt" not in text
    assert "/home/rantu/ok.txt" in text


def test_wecom_unmatched_sensitive_completion_fails_closed():
    turn, progress_queue = _turn()
    turn._ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    turn._ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    turn.progress_callback("tool.completed", "terminal", None, None,
                           result=json.dumps({"output": "opaque-secret-content", "exit_code": 0}))
    text = "".join(_messages(progress_queue))
    assert "opaque-secret-content" not in text
    assert "已隐藏" in text


def test_wecom_progress_redacts_exact_values_in_active_secret_scope():
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    turn, progress_queue = _turn()
    turn._ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    turn._ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    token = set_secret_scope({"SOME_API_KEY": "opaque-credential-value"})
    try:
        turn.progress_callback("tool.started", "web_search", None, {"query": "opaque-credential-value"})
        assert "opaque-credential-value" not in "".join(_messages(progress_queue))
    finally:
        reset_secret_scope(token)


def test_wecom_dm_non_editing_adapter_delivers_progress_without_consuming_stream():
    import asyncio
    from unittest.mock import AsyncMock
    from gateway.config import PlatformConfig
    from plugins.platforms.wecom.adapter import APP_CMD_SEND, WeComAdapter

    turn, progress_queue = _turn()
    ctx = turn._ctx
    ctx.source = SimpleNamespace(platform="wecom", chat_type="dm", chat_id="private")
    ctx.user_config = {"display": {"platforms": {"wecom": {"private_tool_details": True}}}}
    ctx.progress_grouping, ctx._progress_reply_to, ctx._progress_metadata = "separate", "reply", {}
    ctx._cleanup_progress, ctx._cleanup_msg_ids = False, []
    adapter = WeComAdapter(PlatformConfig(enabled=True))
    adapter._last_chat_req_ids["private"] = "cached-stream-request"
    adapter._send_request = AsyncMock(return_value={"errcode": 0})
    adapter._send_reply_request = AsyncMock(return_value={"errcode": 0})

    async def typing(*args, **kwargs):
        pass

    adapter.send_typing = typing
    turn._runner._delivery_adapter_for = lambda _source: adapter
    progress_queue.put("tool started")
    progress_queue.put("tool result")

    async def run():
        task = asyncio.create_task(turn.send_progress_messages())
        await asyncio.sleep(0.1)
        task.cancel()
        await task

    asyncio.run(run())
    calls = adapter._send_request.await_args_list
    assert [call.args[1]["markdown"]["content"] for call in calls] == ["tool started", "tool result"]
    assert all(call.args[0] == APP_CMD_SEND for call in calls)
    adapter._send_reply_request.assert_not_awaited()


def test_verbose_terminal_command_is_followed_immediately_by_its_result():
    turn, progress_queue = _turn()
    command = "printf 'first line\\nsecond line\\n'"
    result = json.dumps({"output": "first line\nsecond line\n", "exit_code": 0, "error": None})

    turn.progress_callback("tool.started", "terminal", command, {"command": command})
    turn.progress_callback(
        "tool.completed", "terminal", None, {"command": command},
        result=result, is_error=False,
    )

    messages = _messages(progress_queue)
    assert len(messages) == 2
    assert command in messages[0]
    assert "Command:" not in messages[1]
    assert command not in messages[1]
    assert "first line\nsecond line" in messages[1]
    assert messages[1].startswith("```text\n")
    assert "Terminal result" not in messages[1]
    assert "Output (stdout/stderr):" not in messages[1]


def test_verbose_terminal_completion_redacts_output_before_chat_egress(monkeypatch):
    turn, progress_queue = _turn()
    redacted = []

    def redact_for_egress(text):
        redacted.append(text)
        return text.replace("secret-value", "[redacted]")

    import agent.redact
    monkeypatch.setattr(agent.redact, "redact_for_egress", redact_for_egress)
    result = json.dumps({"output": "token=secret-value", "exit_code": 0, "error": None})

    turn.progress_callback(
        "tool.completed", "terminal", None, {"command": "printf token"},
        result=result, is_error=False,
    )

    messages = _messages(progress_queue)
    assert len(redacted) == 1
    assert redacted == ["token=secret-value"]
    assert "secret-value" not in "".join(messages)
    assert "[redacted]" in "".join(messages)


def test_verbose_terminal_completion_chunks_long_output_without_dropping_text():
    turn, progress_queue = _turn()
    output = "x" * 7000
    result = json.dumps({"output": output, "exit_code": 0, "error": None})

    turn.progress_callback(
        "tool.completed", "terminal", None, {"command": "generate-large-output"},
        result=result, is_error=False,
    )

    messages = _messages(progress_queue)
    assert len(messages) >= 2
    combined = "".join(message.split("```text\n", 1)[1].split("\n```", 1)[0] for message in messages)
    assert combined == output


def test_terminal_completion_is_not_reported_when_progress_is_disabled_or_not_verbose():
    result = json.dumps({"output": "must stay hidden", "exit_code": 0, "error": None})
    for mode, enabled in (("verbose", False), ("all", True)):
        turn, progress_queue = _turn(mode=mode, enabled=enabled)
        turn.progress_callback(
            "tool.completed", "terminal", None, {"command": "echo hidden"},
            result=result, is_error=False,
        )
        assert _messages(progress_queue) == []


def test_separate_progress_cancel_drains_all_pending_messages():
    import asyncio
    turn, progress_queue = _turn()
    progress_queue.put("command")
    progress_queue.put("output")
    sent = []
    async def send(st, msg):
        sent.append(msg)
    turn._send_progress_text = send
    st = SimpleNamespace(can_edit=False, progress_lines=[], progress_msg_id=None)
    asyncio.run(turn._drain_progress_on_cancel(st))
    assert sent == ["command", "output"]


def test_separate_progress_sends_fast_successive_items_in_order():
    import asyncio
    turn, progress_queue = _turn()
    class Adapter:
        async def edit_message(self):
            pass
    turn._runner._delivery_adapter_for = lambda source: Adapter()
    st = SimpleNamespace(can_edit=False, progress_lines=[], progress_msg_id=None)
    turn._progress_edit_state = lambda adapter: st
    sent = []
    async def send_or_edit(state, msg):
        sent.append(msg)
        return True
    async def no_typing(state):
        pass
    turn._progress_send_or_edit = send_or_edit
    turn._progress_restore_typing = no_typing
    progress_queue.put("command")
    progress_queue.put("output")
    async def run():
        task = asyncio.create_task(turn.send_progress_messages())
        await asyncio.sleep(0.2)
        task.cancel()
        await task
    asyncio.run(run())
    assert sent == ["command", "output"]


def test_non_terminal_completion_does_not_echo_tool_result():
    turn, progress_queue = _turn()
    turn.progress_callback(
        "tool.completed", "read_file", None, {"path": "private.txt"},
        result="file contents", is_error=False,
    )

    assert _messages(progress_queue) == []
