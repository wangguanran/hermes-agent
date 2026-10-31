"""Terminal completion progress carries only display-redacted command arguments."""

import json
from types import SimpleNamespace

from agent import tool_executor


def test_terminal_tool_completed_callback_receives_redacted_args(monkeypatch):
    from hermes_cli.observability import shared_metrics_harness

    monkeypatch.setattr(shared_metrics_harness, "observe_tool_outcome", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_executor, "observe_onboarding_result", lambda agent, name, args, result: result)
    monkeypatch.setattr(tool_executor, "maybe_persist_tool_result", lambda **kwargs: kwargs["content"])
    monkeypatch.setattr(tool_executor, "_record_persisted_path_for_stub", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_executor, "note_tool_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_executor, "make_tool_result_message", lambda *args, **kwargs: {"role": "tool", "content": "ok"})
    monkeypatch.setattr(tool_executor.lsp_deferred, "forward_to_agent", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_executor.plugin_live_notes, "attach_to_tool_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_executor, "stamp_data_use", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_executor, "_flush_session_db_after_tool_progress", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        tool_executor, "_redact_tool_args_for_display",
        lambda name, args: {"command": "printf [REDACTED]"},
    )

    events = []
    agent = SimpleNamespace(
        tool_progress_callback=lambda *args, **kwargs: events.append((args, kwargs)),
        verbose_logging=False,
        _current_tool="terminal",
        _touch_activity=lambda *args, **kwargs: None,
        _subdirectory_hints=SimpleNamespace(check_tool_call=lambda *args, **kwargs: None),
        _tool_result_content_for_active_model=lambda name, result: result,
        _trim_after_tool_batch=False,
    )
    ref = tool_executor._ToolCallRef("terminal", {"command": "printf secret"}, "task", "call-1", [])

    tool_executor._commit_tool_result(
        agent, [], ref, json.dumps({"output": "ok", "exit_code": 0}),
        budget=SimpleNamespace(resolve_threshold=lambda *_: 10000),
        tool_duration=0.1, is_error=False, blocked=False, effect_disposition=None,
    )

    completed = [event for event in events if event[0][0] == "tool.completed"]
    assert len(completed) == 1
    assert completed[0][0][3] == {"command": "printf [REDACTED]"}
