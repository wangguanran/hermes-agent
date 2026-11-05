#!/usr/bin/env python3
"""Bounded native failover acceptance: synthetic 503 -> exhausted gate -> real Codex.

Uses each authorized profile's existing credential resolution. No business tools,
private prompts, production credential failures, or production cap changes.
"""

import copy
import http.server
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading

sys.path.insert(0, "/opt/hermes")
import hermes_yaml
from hermes_cli.runtime_provider import frozen_runtime_config
from hermes_cli.web_server_profiles import _config_profile_scope
from run_agent import AIAgent

root = pathlib.Path("/home/agent/.hermes")
for line in (root / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

spec = importlib.util.spec_from_file_location(
    "meig_budget_proxy",
    pathlib.Path(__file__).parents[2] / "scripts/local/meig_budget_proxy.py",
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Unavailable(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        wire = b'{"error":{"message":"synthetic primary unavailable","type":"server_error"}}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(wire)))
        self.end_headers()
        self.wfile.write(wire)

    def log_message(self, format, *args):
        pass


candidate = root / "cache/scratch/meig-budget-candidate"
results = []
with tempfile.TemporaryDirectory(dir=candidate) as tmp:
    ledger = mod.BudgetLedger(pathlib.Path(tmp) / "state.json", cap=1)
    ledger.reconcile(
        {"synthetic-exhausted": {"tokens": 1, "model": "synthetic", "created_at": 1}}
    )
    primary = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Unavailable)
    gate = mod.create_server(
        ledger, "http://127.0.0.1:1", [os.environ["MEIGSMART_API_KEY"]], port=0
    )
    for server in [primary, gate]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for profile in ["default", "driver-engineer", "project-assistant"]:
            home = root if profile == "default" else root / "profiles" / profile
            config = hermes_yaml.safe_load((home / "config.yaml").read_text())
            config = copy.deepcopy(config)
            config["providers"]["meigsmart"]["api"] = (
                f"http://127.0.0.1:{gate.server_port}/v1"
            )
            config["agent"]["api_max_retries"] = 1
            chain = [
                {"provider": "meigsmart", "model": "deepseek-v4.1-flash"},
                {"provider": "openai-codex", "model": "gpt-6.1-sol"},
            ]
            config["fallback_providers"] = chain
            with (
                _config_profile_scope(None if profile == "default" else profile),
                frozen_runtime_config(config),
            ):
                agent = AIAgent(
                    base_url=f"http://127.0.0.1:{primary.server_port}/v1",
                    api_key="synthetic-test-key",
                    provider="custom",
                    requested_provider="custom",
                    api_mode="chat_completions",
                    model="synthetic-primary",
                    enabled_toolsets=[],
                    disabled_toolsets=["all"],
                    max_iterations=2,
                    max_tokens=128,
                    reasoning_config={"effort": "low", "enabled": True},
                    quiet_mode=True,
                    skip_context_files=True,
                    skip_memory=True,
                    skip_background_review=True,
                    load_soul_identity=False,
                    side_agent=True,
                    fallback_model=chain,
                    run_budget_seconds=100,
                    cwd=str(candidate),
                )
                assert not agent.tools, (
                    "acceptance probe must not expose business tools"
                )
                result = agent.run_conversation(
                    "Reply with exactly OK.",
                    system_message="You are a connectivity test. Reply OK; do not call tools.",
                )
                summary = {
                    "profile": profile,
                    "actual_provider": agent.provider,
                    "actual_model": agent.model,
                    "fallback_index": agent._fallback_index,
                    "response": result.get("final_response"),
                    "gate_forwarded_tokens": ledger.status()["docker_tokens"],
                }
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                results.append(summary)
                assert agent.provider == "openai-codex", summary
                assert agent.model == "gpt-6.1-sol", summary
                assert agent._fallback_index == 2, summary
                assert str(result.get("final_response", "")).strip() == "OK", summary
                assert ledger.status()["docker_tokens"] == 0, summary
    finally:
        for server in [primary, gate]:
            server.shutdown()
            server.server_close()
(candidate / "native-failover-results.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2)
)
assert len(results) == 3
