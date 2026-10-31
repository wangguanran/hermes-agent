"""Canonical JSON formatting for verbose tool-progress arguments."""

import json

from agent.display import verbose_tool_args_text
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.stream_events import ToolCallChunk


ARGS = {"code": "x = a_b\nprint(**kw)"}


class _Adapter(BasePlatformAdapter):
    supports_code_blocks = True

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="unused")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


def _json_body(rendered, opening):
    assert rendered.startswith(opening), rendered
    assert rendered.endswith("\n```") or rendered.endswith("```")
    start = len(opening)
    end = rendered.rfind("\n```")
    assert end >= start, rendered
    return rendered[start:end]


def test_feishu_verbose_args_are_indented_json_in_json_fence():
    rendered = verbose_tool_args_text(ARGS, 0, True, platform=Platform.FEISHU)
    body = _json_body(rendered, "```json\n")
    assert body.startswith("{\n  \"code\": "), body
    assert json.loads(body) == ARGS


def test_plain_verbose_args_are_indented_valid_json_without_fence():
    rendered = verbose_tool_args_text(ARGS, 0, False, platform=Platform.TELEGRAM)
    assert rendered.startswith("{\n  \"code\": "), rendered
    assert "```" not in rendered
    assert json.loads(rendered) == ARGS


def test_embedded_backtick_fences_are_safely_broken_inside_json():
    rendered = verbose_tool_args_text(
        {"code": "see ```**kw**```"}, 0, True, platform=Platform.FEISHU
    )
    body = _json_body(rendered, "```json\n")
    assert "```" not in body
    assert "\u200b" in body
    assert "\u200b" in json.loads(body)["code"]


def test_streamed_feishu_verbose_args_share_json_formatting():
    adapter = _Adapter(PlatformConfig(enabled=True, token="***"), Platform.FEISHU)
    rendered = adapter.format_tool_event(
        ToolCallChunk("execute_code", args=ARGS), mode="verbose"
    )
    assert rendered is not None
    from agent.display import get_tool_emoji
    assert rendered.startswith(f"{get_tool_emoji('execute_code', default='⚙️')} execute_code"), rendered
    body = _json_body(rendered[rendered.index("```json\n"):], "```json\n")
    assert json.loads(body) == ARGS


def test_streamed_slack_verbose_args_keep_untagged_code_fence():
    adapter = _Adapter(PlatformConfig(enabled=True, token="***"), Platform.SLACK)
    rendered = adapter.format_tool_event(
        ToolCallChunk("execute_code", args=ARGS), mode="verbose"
    )
    assert rendered is not None
    assert "```json" not in rendered
    assert "```\n{" in rendered
    body = rendered.rsplit("```\n", 1)[-1].removesuffix("\n```")
    assert json.loads(body) == ARGS
