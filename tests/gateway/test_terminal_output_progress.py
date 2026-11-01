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
