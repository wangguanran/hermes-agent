# Work-container meigsmart token budget and Codex failover

## Scope and status

Targets: the work container's `default`, `driver-engineer`, and `project-assistant` profiles only. Primary remains `cursor / auto`. Backups, in order, are `meigsmart / deepseek-v4.1-flash` and `openai-codex / gpt-6.1-sol`.

The fallback lists have been saved through the native configuration CLI and read back for all three profiles. The budget interception endpoint and service supervisor are **prepared, not activated**. Shared-container recreation was requested separately; no answer was received, so production has not been interrupted. Existing agents are not claimed to have reloaded their fallback chains.

## Assets

- `scripts/local/meig_budget_proxy.py`: loopback-only transparent Responses/Chat Completions proxy; authoritative account log collector, shared reservations, and per-role/model attribution.
- `scripts/local/hermes_work_service.py`: existing Gateway/Desktop supervisor with an optional, independently restarted budget companion. Use a work-specific mount; do not replace the shared supervisor used by other business containers.
- `scripts/local/meig-budget.example.json`: non-secret settings. Settings belong here, not in `.env`.
- `tests/local/test_meig_budget.py`: standard-library contracts, including real local HTTP streaming, fail-closed admission and subprocess supervision.
- `tests/local/verify_meig_native_failover.py`: opt-in, bounded acceptance using the installed Hermes implementation. A synthetic primary returns 503; a separate exhausted test gate returns 429 without forwarding; the real, independently authenticated Codex endpoint must answer for each profile. No business tools are enabled.

## Accounting and safety

Account scope is the logged-in user's entire meigsmart consumption, including other clients. Sum `prompt_tokens + completion_tokens`; cached input is already included. Ignore authentication logs and non-model records. The gateway can renumber `id` values per page; deduplicate and reconcile using the stable `request_id`. Consume `X-Oneapi-Request-Id` (or `X-Request-Id`) from upstream responses.

Refresh every 30 seconds with a five-minute overlap; run a complete daily reconciliation every five minutes. Fixed collection windows, pagination totals and identities must agree. Missing/stale data (90 seconds), startup before successful reconciliation, or an exhausted budget deny admission with an `insufficient_quota` 429 so native failover continues to Codex.

Persist a reservation before any model request. Reserve request-body bytes plus the requested maximum output (65,536 default when absent); replace it with returned usage and release it only when the corresponding account log is observed. Missing usage or request identity remains conservatively reserved; it must not silently become zero. This is a conservative estimate, not a tokenizer or image-token hard bound. Failed/unidentified calls can reduce available capacity until the next day. Other clients and delayed gateway logs mean this is **not an exact global account hard cap**.

State lives under the work root's `cache/meig-budget-live/`. JSON writes are atomic and fsynced; a single supervised proxy process is the sole writer. Do not run `--once` against that same state while the service is running; use the loopback `/healthz` endpoint for live readback. Cold-start `--once` is an operator verification mode, not a second scheduled writer. No shared SQLite/flock correctness is assumed for the macOS bind mount.

Authentication uses the work root's existing `.env` and `scripts/gw_session.py`. The proxy has its own refresh-session cache and passes `revoke_others=False`; it must not log out other clients. Never copy Codex refresh tokens from the host; use the independently authorized container root store and the native per-profile resolver. No request bodies, credentials or raw upstream errors are logged. Redirects are not followed with bearer credentials. No proxy port is published to the host.

Docker attribution starts at activation and records only intercepted requests; it is not a reconstructed history of all past Docker calls. Account data remains authoritative for the limit. Midnight is Asia/Shanghai; old in-flight requests may finish, but new admissions wait for a verified new-day snapshot. Daily account usage and per-role counters are archived before rollover.

## Activation gate (still pending)

1. Obtain explicit permission to recreate the shared work container. Re-read actual Compose, image ID and mounts, the complete served roster, active-work and unfinished admissions. No authorization to interrupt unrelated work is implied.
2. Keep the current image and all role/data/security settings. Install the proxy and its settings in the work root's `service/`; settings filename for supervision is `meig-budget.json`. Prepare a separate work supervisor file and replace **only** the work service's supervisor bind source, retaining its existing container target/command. Compare the full Compose model and assert other services unchanged.
3. Back up the latest profile configs (0600). For all configured aliases targeting meigsmart, use the native CLI to set `providers.<name>.api` to `http://127.0.0.1:18181/v1` and `providers.<name>.extra_headers` to `{"X-Hermes-Budget-Profile":"<profile>"}`. This includes root `meigsmart-chat`. Preserve transports, model catalogs, key references, Cursor primary, auxiliary settings and all unrelated fields/comments.
4. Recreate only the authorized work service after the final activity gate. Do not start a second production Gateway. Verify proxy liveness and fresh account data, exact profile routes, private Codex resolution, original mounts/security, platform connections, preserved cron/identity/history and real routing behavior. Keep acceptance probes free of business tools and private content.
5. Test under-budget meigsmart streaming/usage reconciliation and the exhausted native chain in separate test state. Test tool-history transport without flattening tool results into ordinary text. Direct-provider and synthetic failover results do not replace production messaging acceptance.
6. After any accepted production writes, preserve the new state before rollback. Roll back only this service/mount and the scoped config fields; do not restore entire stale profile snapshots or delete history/state.

## Verification commands

Run the stdlib tests from the checkout:

```sh
python3 tests/local/test_meig_budget.py
ruff check scripts/local/meig_budget_proxy.py scripts/local/hermes_work_service.py tests/local/test_meig_budget.py tests/local/verify_meig_native_failover.py
```

Run native failover acceptance only inside the authorized work-container environment, with the repository-relative test/script layout preserved. The verifier uses the container root and each profile's native scope; no real Cursor credential is invalidated. Its JSON results must enumerate exactly the three target profiles with `actual_provider=openai-codex`, `actual_model=gpt-6.1-sol`, fallback index 2, response `OK`, and zero forwarded test-gate tokens.
