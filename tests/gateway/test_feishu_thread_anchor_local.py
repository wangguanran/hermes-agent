"""Regression: a native Feishu thread needs a message reply anchor."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugins.platforms.feishu.adapter import FeishuAdapter


def make_adapter():
    adapter = object.__new__(FeishuAdapter)
    adapter._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(reply="reply", create="create")
    )))
    adapter._fetch_last_message_in_thread = AsyncMock(return_value="om_anchor")
    adapter._run_blocking = AsyncMock(return_value="ok")
    adapter._build_reply_message_body = lambda **kw: kw
    adapter._build_reply_message_request = lambda msg, body: (msg, body)
    adapter._build_create_message_body = lambda **kw: kw
    adapter._build_create_message_request = lambda kind, body: (kind, body)
    return adapter


def send(adapter, metadata, reply_to=None):
    return asyncio.run(adapter._send_raw_message(
        chat_id="oc_target", msg_type="file", payload="{}", reply_to=reply_to, metadata=metadata
    ))


def test_thread_without_anchor_finds_last_and_replies():
    adapter = make_adapter()
    send(adapter, {"thread_id": "omt_topic"})
    assert adapter._fetch_last_message_in_thread.await_count == 1
    method, (anchor, body) = adapter._run_blocking.await_args.args
    assert method == "reply" and anchor == "om_anchor" and body["reply_in_thread"] is True


def test_existing_anchor_preserved():
    adapter = make_adapter()
    send(adapter, {"thread_id": "omt_topic", "reply_to_message_id": "om_existing"})
    assert not adapter._fetch_last_message_in_thread.await_count
    assert adapter._run_blocking.await_args.args[1][0] == "om_existing"


def test_non_thread_unchanged():
    adapter = make_adapter()
    send(adapter, {})
    assert not adapter._fetch_last_message_in_thread.await_count
    assert adapter._run_blocking.await_args.args[0] == "create"
    assert adapter._run_blocking.await_args.args[1][0] == "chat_id"


def test_lookup_failure_keeps_original_target_no_chat_leak():
    adapter = make_adapter()
    adapter._fetch_last_message_in_thread = AsyncMock(return_value=None)
    send(adapter, {"thread_id": "omt_topic"})
    method, (kind, body) = adapter._run_blocking.await_args.args
    assert method == "create" and kind == "thread_id" and body["receive_id"] == "omt_topic"


def test_explicit_reply_preserved():
    adapter = make_adapter()
    send(adapter, {"thread_id": "omt_topic"}, reply_to="om_explicit")
    assert not adapter._fetch_last_message_in_thread.await_count
    assert adapter._run_blocking.await_args.args[1][0] == "om_explicit"
