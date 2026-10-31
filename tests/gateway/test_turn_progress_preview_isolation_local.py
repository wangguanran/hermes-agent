"""A concurrent profile must not change another turn's verbose argument limit."""
import json
import queue
from types import SimpleNamespace

from agent.display import get_tool_preview_max_len, set_tool_preview_max_len
from gateway.config import Platform
from gateway.run_turn_runner import TurnRunner
from gateway.turn_context import TurnContext


def _message(limit, global_limit):
    old = get_tool_preview_max_len()
    try:
        set_tool_preview_max_len(global_limit)
        adapter = SimpleNamespace(supports_code_blocks=True)
        runner = SimpleNamespace(_delivery_adapter_for=lambda source: adapter)
        ctx = TurnContext(source=SimpleNamespace(platform=Platform.FEISHU),
                          progress_mode="verbose", progress_queue=queue.Queue())
        ctx.tool_preview_length = limit
        args = {"operations": [{"action": "patch", "name": "example", "new_string": "x" * 250}]}
        TurnRunner(runner, ctx)._progress_build_message("skill_manage", "", args)
        text = ctx.progress_queue.get_nowait()
        return text, args
    finally:
        set_tool_preview_max_len(old)


def test_unlimited_turn_ignores_other_profile_cap():
    text, args = _message(0, 40)
    body = text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(body) == args


def test_capped_turn_ignores_other_profile_unlimited():
    text, _ = _message(40, 0)
    assert "..." in text
