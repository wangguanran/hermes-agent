import json
from types import SimpleNamespace
from unittest.mock import patch

from tools.delegate_tool import delegate_task


def test_profile_denied_delegation_blocks_internal_spawn():
    parent = SimpleNamespace(disabled_toolsets=["delegation"], _delegate_depth=0)
    with patch("tools.delegate_tool._normalize_task_list", return_value=([], "guard bypassed")) as normalize:
        result = json.loads(delegate_task(goal="must not start", parent_agent=parent))
    assert result.get("error")
    assert "disabled" in result["error"].lower()
    normalize.assert_not_called()


def test_profile_denied_delegation_keeps_control_access():
    parent = SimpleNamespace(disabled_toolsets=["delegation"], _delegate_depth=0)
    with patch("tools.delegate_tool._handle_control_action", return_value='{"status":"ok"}') as control:
        assert json.loads(delegate_task(action="list", parent_agent=parent))["status"] == "ok"
    control.assert_called_once()
