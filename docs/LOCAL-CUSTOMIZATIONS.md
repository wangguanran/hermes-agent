# Hermes Local Customizations

This manifest covers source changes in the personal fork; it does not authorize copying profile data or changing live profile settings.

## Baseline and branch

- Upstream stable baseline: `v0.22.5`, commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e`.
- Personal fork branch: `local/customizations-20261027`.
- Replay the local commits in their existing ancestry order after selecting a verified upstream stable baseline. Do not deploy upstream `main` as a substitute for the stable channel.

## Repository changes

- `cron/local_report_footer.py`, `cron/scheduler.py`, and corresponding tests — preserve the local scheduled-report footer and timing behavior.
- `hermes_cli/local_profile_tool_defaults.py`, `hermes_cli/profiles.py`, `tools/delegate_tool.py`, and corresponding tests — keep delegation disabled when creating or cloning profiles; retain other role-specific policy.
- `agent/codex_responses_adapter.py`, `agent/chat_completion_helpers.py`, `agent/error_classifier.py`, `agent/transports/codex.py`, `run_agent.py`, `agent/responses_plaintext_reasoning.py`, and corresponding tests — retain Responses reasoning needed for subsequent tool requests and classify related validation failures.
- `agent/agent_runtime_helpers.py`, `agent/display.py`, `agent/tool_executor.py`, `gateway/platforms/base.py`, `gateway/run_turn.py`, `gateway/run_turn_progress.py`, `gateway/turn_context.py`, `plugins/platforms/feishu/adapter.py`, `hermes_cli/profiles.py`, and corresponding tests — preserve terminal progress/result formatting, Feishu thread anchoring, and the profile-creation policy.
- `pm/store.py` and `tests/pm/test_tree_digest_finder_metadata.py` — ignore Finder `.DS_Store` metadata when computing the package tree digest. This addresses macOS Finder artifacts; do not include this behavior in Windows/134 deployment payloads unless target-specific evidence requires it.

## Migration and verification

- Start from the exact verified target baseline. Preserve and review each target's uncommitted files before replay; back up only the scoped source and host-specific changes before any write.
- Apply only compatible commits, in ancestry order. Resolve conflicts explicitly; never reset, force-overwrite, or copy a dirty tree as a release.
- Run the repository test files associated with the replayed changes in an isolated test environment, then run target-host regressions for platform-specific behavior. Unit tests do not prove a running Gateway loaded the changes or that live messaging works.
- Keep Mac-only behavior out of Windows and Linux deployment payloads. Preserve Windows profile/tool constraints and Linux host-specific reasoning changes unless separately reconciled and tested.
- A source update does not authorize a Gateway restart. Follow the host's approved service path and verify the new process, startup time, loaded version, and live platform behavior independently.

## Excluded state

This source manifest does not include `.env` credentials, `config.yaml`, profiles, memories, sessions, cron jobs, skills, plugins, or other personal/runtime data. Those remain host-local and must not be copied as part of this source update.
