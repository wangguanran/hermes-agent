# Recover a relocated legacy Bot Chat binding

`scripts/repair_legacy_chat_binding.py` is a **local operator maintenance tool**, not a shipped Hermes CLI command. It preserves the original conversation instead of creating a replacement Bot Chat. Use it only for the explicitly selected profile and legacy conversation.

## Preconditions and refusals

- The canonical local receipt and its legacy proof must already be valid for the current profile. The authenticated principal must be the operator's current OS UID. Native session-ID migrations and cross-profile transfers are not supported.
- Only an ordinary GUI Bot Chat following its profile, without a launch-only credential or bypass-config policy, is supported.
- Pass the original home and destination workspace explicitly. A partially modified or unrelated source reference is refused rather than guessed.
- Every frozen private value must still exist in the current profile and match its original fingerprint. Its frozen and current endpoint bindings must also match. Terminal-scope secrets and old references lacking endpoint bindings are refused.
- The normal credential checks are unchanged. The tool calls the installed reference issuer to create a fresh, verified current-profile reference; it does not patch the old reference's home fields or modify profile/principal/legacy/lineage identity.

## Preview and apply

Run with a matching, dependency-complete Python from this installation. Preview is the default and is safe while the owner is live:

```sh
HERMES_DISABLE_LAZY_INSTALLS=1 <matching-python> scripts/repair_legacy_chat_binding.py \
  --home <canonical-profile-home> --old-home <original-home> \
  --session-id <original-legacy-session-id> --cwd <canonical-workspace>
```

For application, first obtain authorization for the actual affected work and use the installation's supported lifecycle to drain and stop the target Gateway. Do not use a delayed script, signals, a different channel or forced options to evade a lifecycle refusal. If the current agent is itself hosted by that Gateway, the final maintenance must run from an independently authorized operator context, not another client that merely attaches to the same owner.

Once the owner is stopped, repeat the same command with `--apply`. Both the high-level command and the transaction entry point reserve the real database home's native exclusive maintenance lock; a live owner is refused. Other database holders, including an incomplete holder scan, are refused too. There is no force mode.

The tool creates a new 0700 backup directory and a 0600 SQLite backup, validates integrity, then uses the existing workspace-policy transaction with a snapshot/revision fence. It checks the configuration and every message field, including binary sidecars, before committing; the receipt's immutable identity fields are compared in-transaction. Only workspace/git metadata, the renewed private reference, the necessary generation/revision fences and a separate maintenance audit record change. A verified repeat is a no-op.

## Acceptance and rollback

- Re-read the exact original session and policy, then restart through the approved lifecycle. Verify the new process and profile serving state.
- Open the **original** Bot Chat in Desktop. Verify history and a bounded real model reply; a policy renewal, HTTP 200 or snapshot test alone is not end-to-end acceptance.
- Verify a bounded read-only terminal command starts in the new workspace. Old terminal cwd records are process-local and disappear with the stopped owner.
- A transaction failure rolls back all its writes; the backup remains available. Do not restore a whole pre-repair database after accepting new messages or other work. Stop writing, preserve the new state and compare changes before choosing a scoped rollback.
- Never publish profile databases, snapshot contents, config values or credential fingerprints with this tool's generic source.

## Verification boundary

The regression assertions cover unchanged identity/configuration, verified renewal and retry, foreign principal/profile/native IDs, changed credentials/endpoints/references, native binary sidecars and owner-lock refusal at both entry points. A private production-snapshot rehearsal separately checks all unaffected tables/rows, cold credential recovery, stale-plan refusal and rollback after an injected audit-write failure.

Those probes are not a claim of successful production deployment or Desktop/model acceptance. The repository's standard pytest runner is the upstream unit-test path; on hosts whose prescribed scratch location conflicts with its hard-coded temporary root, record separate bounded contract-probe results rather than silently disabling real-home guards or claiming the canonical suite passed.
