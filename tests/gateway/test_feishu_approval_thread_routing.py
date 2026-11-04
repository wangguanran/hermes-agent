"""Approval follow-ups must stay in the conversation that owns the card."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.i18n import t
from gateway.config import PlatformConfig
from plugins.platforms.feishu.adapter import FeishuAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["once", "session", "always"])
@pytest.mark.parametrize("reply_code", [0, 230011, 231003])
async def test_expired_approval_replies_to_card_in_original_thread(choice, reply_code):
    adapter = FeishuAdapter(PlatformConfig(enabled=True))
    adapter._client = MagicMock()
    adapter._admins = {"ou_approver"}
    response = SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(message_id="om_approval_card"),
    )
    followup_response = SimpleNamespace(success=lambda: reply_code == 0, code=reply_code)
    with patch.object(
        adapter, "_run_blocking", new_callable=AsyncMock, side_effect=[response, followup_response],
    ) as api:
        result = await adapter.send_exec_approval(
            chat_id="oc_task_group",
            command="echo approval-routing-test",
            session_key="agent:main:feishu:group:oc_task_group:omt_original_task",
            metadata={"thread_id": "omt_original_task", "reply_to_message_id": "om_task"},
        )
        assert result.success
        approval_id = next(iter(adapter._approval_state))
        with patch("tools.approval.resolve_gateway_approval", return_value=0):
            await adapter._resolve_approval(
                approval_id, choice, "Approver", open_id="ou_approver", chat_id="oc_task_group",
            )

    assert approval_id not in adapter._approval_state
    assert api.await_count == 2
    assert api.await_args is not None
    method, request = api.await_args.args
    assert method is adapter._client.im.v1.message.reply
    assert request.message_id == "om_approval_card"
    assert request.request_body.reply_in_thread is True
    assert t("platform.shared.approval_expired") in request.request_body.content


@pytest.mark.asyncio
@pytest.mark.parametrize("count, choice", [(1, "once"), (1, "session"), (1, "always"), (0, "deny")])
async def test_resolved_or_denied_approval_does_not_send_expiry_notice(count, choice):
    adapter = FeishuAdapter(PlatformConfig(enabled=True))
    adapter._admins = {"ou_approver"}
    adapter._approval_state[1] = {
        "session_key": "agent:main:feishu:group:oc_task_group:omt_original_task",
        "message_id": "om_approval_card",
        "chat_id": "oc_task_group",
        "thread_id": "omt_original_task",
    }
    with (
        patch("tools.approval.resolve_gateway_approval", return_value=count),
        patch.object(adapter, "send", new_callable=AsyncMock) as send,
    ):
        await adapter._resolve_approval(
            1, choice, "Approver", open_id="ou_approver", chat_id="oc_task_group",
        )

    assert 1 not in adapter._approval_state
    send.assert_not_awaited()
