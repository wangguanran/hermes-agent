"""Administrative budget routing must also cover frozen sessions (no network)."""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_cli.runtime_provider import frozen_runtime_config, resolve_runtime_provider


class FrozenBudgetRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "service").mkdir()
        (self.root / "service/meig-budget.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "hermes_home": str(self.root),
                    "upstream": "http://192.168.0.18",
                    "port": 18181,
                    "cap": 200000000,
                }
            )
        )
        self.config = {
            "model": {"provider": "meigsmart", "default": "deepseek-v4.1-flash"},
            "providers": {
                "meigsmart": {
                    "api": "http://192.168.0.18/v1",
                    "api_key": "synthetic-local-test-only",
                    "transport": "chat_completions",
                }
            },
        }

    def test_frozen_provider_cannot_bypass_enabled_budget(self):
        original = copy.deepcopy(self.config)
        with patch("hermes_constants.get_hermes_home", return_value=self.root):
            with frozen_runtime_config(self.config):
                runtime = resolve_runtime_provider(
                    requested="meigsmart", target_model="deepseek-v4.1-flash"
                )
        self.assertEqual(runtime["base_url"], "http://127.0.0.1:18181/v1")
        self.assertEqual(runtime["extra_headers"]["X-Hermes-Budget-Profile"], "default")
        self.assertEqual(self.config, original)

    def test_old_worker_policy_inherits_budget_routes_and_failovers(self):
        from gateway.session_policy import LocalSessionPolicy

        original = copy.deepcopy(self.config)
        original["model"] = {"provider": "cursor", "default": "auto"}
        original["fallback_providers"] = [
            {"provider": "meigsmart", "model": "deepseek-v4.1-flash"}
        ]
        original["auxiliary"] = {
            "compression": {"provider": "meigsmart", "model": "deepseek-v4.1-flash"}
        }
        original["agent"] = {
            "disabled_toolsets": ["delegation"],
            "reasoning_effort": "high",
        }
        policy = LocalSessionPolicy(
            source="kanban",
            platform="local",
            cwd=str(self.root),
            model="auto",
            toolsets=(),
            config_json=json.dumps(original),
            request_json="{}",
            terminal_json="{}",
        )
        with patch("hermes_constants.get_hermes_home", return_value=self.root):
            config, model = policy.effective()
        self.assertEqual(
            config["providers"]["meigsmart"]["api"], "http://127.0.0.1:18181/v1"
        )
        self.assertEqual(
            config["fallback_providers"],
            [
                {"provider": "meigsmart", "model": "deepseek-v4.1-flash"},
                {"provider": "openai-codex", "model": "gpt-6.1-sol"},
            ],
        )
        self.assertEqual(
            config["auxiliary"]["compression"]["fallback_chain"],
            [
                {"provider": "openai-codex", "model": "gpt-6.1-sol"},
            ],
        )
        self.assertEqual(config["model"], original["model"])
        self.assertEqual(config["agent"], original["agent"])
        self.assertEqual(model, "auto")
        self.assertEqual(json.loads(policy.config_json), original)

    def test_no_opt_in_and_disabled_opt_in_preserve_runtime(self):
        from hermes_cli.meig_budget_routing import (
            apply_runtime_budget,
            apply_budget_policy,
        )

        runtime = {
            "base_url": "http://192.168.0.18/v1",
            "api_key": "synthetic-test-only",
        }
        path = self.root / "service/meig-budget.json"
        path.unlink()
        self.assertIs(apply_runtime_budget(runtime, self.root), runtime)
        self.assertIs(apply_budget_policy(self.config, self.root), self.config)
        path.write_text('{"enabled": false}')
        self.assertIs(apply_runtime_budget(runtime, self.root), runtime)

    def test_other_endpoints_and_credentials_are_not_rebound(self):
        from hermes_cli.meig_budget_routing import apply_runtime_budget

        for url in [
            "https://chatgpt.com/backend-api/codex",
            "http://192.168.0.18:18080/v1",
            "http://192.168.0.18.evil.example/v1",
            "https://other.example/v1",
        ]:
            runtime = {"base_url": url, "api_key": "synthetic-test-only"}
            self.assertIs(apply_runtime_budget(runtime, self.root), runtime)
        credential = lambda: "synthetic-test-only"
        runtime = {"base_url": "http://192.168.0.18/v1", "api_key": credential}
        self.assertIs(apply_runtime_budget(runtime, self.root)["api_key"], credential)
        self.assertEqual(runtime["base_url"], "http://192.168.0.18/v1")

    def test_nested_profile_attribution_and_aliases(self):
        from hermes_cli.meig_budget_routing import (
            apply_runtime_budget,
            apply_budget_policy,
        )

        home = self.root / "profiles/driver-engineer"
        home.mkdir(parents=True)
        runtime = {
            "base_url": "http://192.168.0.18/v1",
            "extra_headers": {
                "x-hermes-budget-profile": "stale",
                "X-Unrelated": "keep",
            },
        }
        routed = apply_runtime_budget(runtime, home)
        self.assertEqual(
            routed["extra_headers"],
            {
                "X-Hermes-Budget-Profile": "driver-engineer",
                "X-Unrelated": "keep",
            },
        )
        config = copy.deepcopy(self.config)
        config["providers"]["meigsmart-chat"] = copy.deepcopy(
            config["providers"]["meigsmart"]
        )
        config["auxiliary"] = {
            "goal_judge": {"provider": "custom:meigsmart-chat", "model": "keep"}
        }
        routed = apply_budget_policy(config, home)
        self.assertEqual(
            routed["providers"]["meigsmart-chat"]["api"], "http://127.0.0.1:18181/v1"
        )
        self.assertEqual(routed["auxiliary"]["goal_judge"]["model"], "keep")
        self.assertEqual(
            routed["auxiliary"]["goal_judge"]["fallback_chain"][0]["provider"],
            "openai-codex",
        )

    def test_invalid_budget_settings_fail_closed(self):
        from hermes_cli.meig_budget_routing import apply_runtime_budget

        path = self.root / "service/meig-budget.json"
        path.write_text("{invalid")
        with self.assertRaises(ValueError):
            apply_runtime_budget({"base_url": "http://192.168.0.18/v1"}, self.root)
        path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "hermes_home": str(self.root),
                    "upstream": "http://192.168.0.18",
                    "port": True,
                }
            )
        )
        with self.assertRaises(ValueError):
            apply_runtime_budget({"base_url": "http://192.168.0.18/v1"}, self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
