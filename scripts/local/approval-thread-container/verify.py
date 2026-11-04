#!/usr/bin/env python3
"""Offline container regression: real adapter/SDK requests, mocked SDK I/O only.

Run with a fresh writable HERMES_HOME, no real credentials, and --network=none.
This is not a live Feishu delivery or production Gateway acceptance test.
"""

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_home = Path(os.environ.get("HERMES_HOME", "")).resolve()
if _home.parent != Path("/validation"):
    raise SystemExit("Refusing to use a non-fixture HERMES_HOME")
for _directory in (_home, Path("/validation/home"), Path("/validation/scratch")):
    _directory.mkdir(parents=True, exist_ok=True, mode=0o700)

sys.path.insert(0, "/opt/hermes")

from agent.i18n import t
from gateway.config import PlatformConfig
from plugins.platforms.feishu.adapter import FeishuAdapter


class ApprovalThreadTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self):
        adapter = FeishuAdapter(PlatformConfig(enabled=True))
        adapter._client = MagicMock()
        adapter._admins = {"ou_approver"}
        return adapter

    async def expired(self, choice, reply_code, threaded):
        adapter = self.adapter()
        thread = "omt_original_task" if threaded else ""
        metadata = {"thread_id": thread, "reply_to_message_id": "om_task"} if thread else None
        card = SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_card"))
        followup = SimpleNamespace(success=lambda: reply_code == 0, code=reply_code)
        transport = adapter._feishu_send_with_retry
        with (
            patch.object(adapter, "_run_blocking", new_callable=AsyncMock,
                         side_effect=[card, followup]) as api,
            patch.object(adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
                         wraps=transport) as routed,
        ):
            result = await adapter.send_exec_approval(
                chat_id="oc_fixture", command="echo routing-fixture-not-executed",
                session_key="agent:main:feishu:group:oc_fixture:omt_original_task",
                metadata=metadata,
            )
            self.assertTrue(result.success)
            approval_id = next(iter(adapter._approval_state))
            if metadata:
                # Stored routing must be a snapshot, not an alias to caller metadata.
                metadata["thread_id"] = "omt_unrelated_task"
            with patch("tools.approval.resolve_gateway_approval", return_value=0):
                await adapter._resolve_approval(
                    approval_id, choice, "Fixture", open_id="ou_approver", chat_id="oc_fixture",
                )
        self.assertNotIn(approval_id, adapter._approval_state)
        self.assertEqual(api.await_count, 2, "No retry may create a new group-root message")
        self.assertIsNotNone(api.await_args)
        self.assertIsNotNone(routed.await_args)
        assert api.await_args is not None
        assert routed.await_args is not None
        assert adapter._client is not None
        method, request = api.await_args.args
        self.assertIs(method, adapter._client.im.v1.message.reply,
                      "Expired approval must reply, never create a new group-root message")
        self.assertEqual(request.message_id, "om_card")
        self.assertEqual(bool(request.request_body.reply_in_thread), bool(thread))
        self.assertIn(t("platform.shared.approval_expired"), request.request_body.content)
        self.assertEqual(routed.await_args.kwargs.get("reply_to"), "om_card")
        self.assertEqual(routed.await_args.kwargs.get("metadata"),
                         {"thread_id": thread} if thread else None)

    async def no_expiry(self, count, choice):
        adapter = self.adapter()
        adapter._approval_state[1] = {
            "session_key": "agent:main:feishu:group:oc_fixture:omt_original_task",
            "message_id": "om_card", "chat_id": "oc_fixture", "thread_id": "omt_original_task",
        }
        with (
            patch("tools.approval.resolve_gateway_approval", return_value=count),
            patch.object(adapter, "send", new_callable=AsyncMock) as send,
        ):
            await adapter._resolve_approval(
                1, choice, "Fixture", open_id="ou_approver", chat_id="oc_fixture",
            )
        self.assertNotIn(1, adapter._approval_state)
        send.assert_not_awaited()

    async def denied_callback(self, open_id, chat_id):
        adapter = self.adapter()
        adapter._approval_state[1] = {
            "session_key": "agent:main:feishu:group:oc_fixture:omt_original_task",
            "message_id": "om_card", "chat_id": "oc_fixture", "thread_id": "omt_original_task",
        }
        with (
            patch("tools.approval.resolve_gateway_approval") as resolve,
            patch.object(adapter, "send", new_callable=AsyncMock) as send,
        ):
            await adapter._resolve_approval(1, "once", "Fixture", open_id=open_id, chat_id=chat_id)
        resolve.assert_not_called()
        send.assert_not_awaited()
        self.assertIn(1, adapter._approval_state)


def expiry_case(choice, reply_code, threaded=True):
    async def test(self):
        await self.expired(choice, reply_code, threaded)
    return test


def no_expiry_case(count, choice):
    async def test(self):
        await self.no_expiry(count, choice)
    return test


def denied_case(open_id, chat_id):
    async def test(self):
        await self.denied_callback(open_id, chat_id)
    return test


for _choice in ("once", "session", "always"):
    for _code in (0, 230011, 231003):
        setattr(ApprovalThreadTests, f"test_expired_{_choice}_{_code}", expiry_case(_choice, _code))
    setattr(ApprovalThreadTests, f"test_resolved_{_choice}", no_expiry_case(1, _choice))
for _name, _test in {
    "test_denied_approval": no_expiry_case(0, "deny"),
    "test_expired_dm_no_thread": expiry_case("once", 0, False),
    "test_non_admin_rejected": denied_case("ou_intruder", "oc_fixture"),
    "test_wrong_chat_rejected": denied_case("ou_approver", "oc_other"),
}.items():
    setattr(ApprovalThreadTests, _name, _test)


if __name__ == "__main__":
    home = Path(os.environ["HERMES_HOME"])
    if not str(home).startswith("/validation/"):
        raise SystemExit("Refusing to use a non-fixture HERMES_HOME")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ApprovalThreadTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("APPROVAL_TEST_RESULT=" + json.dumps({
        "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "passed": result.wasSuccessful(), "mode": "offline-sdk-boundary",
    }, sort_keys=True), flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
