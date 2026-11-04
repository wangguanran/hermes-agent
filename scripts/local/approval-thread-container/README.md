# Approval-thread container candidates

This self-contained recipe bundles the reviewed, adapter-only Feishu
approval-thread patch exported from
`11788b01683b8ac8f2e311d7006a2898086afd50` as `approval-thread.patch`.
It adds only that patch to an existing, verified image. It does not replace the
full source checkout, update dependencies, change the base entrypoint, or deploy
anything to production. The patch identifier in a candidate tag is not a claim
that the image's entire tree equals that commit.

## Build inputs and safety

1. Record the production container IDs, start times, image IDs and Compose
   services. Inspect dependency differences before selecting bases. Preserve
   intentional image variants; a patch deployment is not permission to change
   SDKs.
2. Use this directory as the build context. Its `.dockerignore` is an allowlist
   for the Dockerfile, reviewed `approval-thread.patch`, `verify.py` and
   `boot_smoke.py`; no external patch export or generated staging asset is
   required. The Dockerfile verifies the bundled patch against its pinned
   SHA-256 and checks that it applies cleanly.
3. Compute the selected base's image ID. Supply it as `BASE_IMAGE_ID` and the
   verified local image name as `BASE_IMAGE`. Build with `--pull=false
   --network=none` and a new candidate tag; refuse to overwrite an existing tag.
   The Dockerfile fails on a checksum mismatch, an unknown source shape or an
   already-applied patch.
4. Read back the resulting image. Confirm the original rootfs layer prefix and
   `Entrypoint`, `Cmd`, `User`, `Volumes`, `WorkingDir` and `Env` are preserved.
   Read the patch/base labels as an overlay receipt, not a whole-tree Git SHA.

No business homes, credentials, backups or Compose `.env` belong in the build
context. The `.dockerignore` is an allowlist, not just a list of known secrets.
The scoped `.gitattributes` preserves the bundled patch's exact bytes: a blank
unified-diff context line intentionally contains one space. `git apply
--whitespace=error` still checks the actual source additions during the build.

## Offline validation

First run `verify.py` against both unmodified base images. The missing expiry
routing must fail with assertion failures, not missing imports or skipped tests.
Then run the **same script** inside each candidate image. It needs only the
runtime's standard library and installed Hermes/Feishu dependencies, not pytest.

The verifier exercises the real adapter and SDK request builders. Only the SDK
I/O boundary and approval resolver are mocked. It checks:

- Expired once/session/always approvals with reply codes 0, 230011 and 231003.
- Replies target the original card, retain the original task's thread, never
  create a group-root fallback, and survive mutation of the caller's metadata.
- Non-threaded DM replies remain non-threaded.
- Resolved/denied approvals do not issue expiry notices.
- Non-admin and wrong-chat callbacks neither resolve nor send notices.

Run it in a fresh container with a read-only root, `--network=none`, non-root
`--user 501:20`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, no published
ports, no business mounts, and restart policy `no`. Provide writable tmpfs at
`/validation`, `/tmp`, and every image-declared VOLUME (currently `/opt/data`),
with the target user's ownership. This prevents anonymous volume creation.

Set `HERMES_HOME=/validation/hermes`, `HOME=/validation/home`,
`TMPDIR=/validation/scratch`, `PYTHONDONTWRITEBYTECODE=1` and
`HERMES_DISABLE_LAZY_INSTALLS=1`. Override the image's production init entrypoint
with `/opt/hermes/.venv/bin/python` for these diagnostics. Run:

- `/opt/hermes-local/verification/verify.py`
- `/opt/hermes-local/verification/boot_smoke.py`

`boot_smoke.py` launches the real Gateway in that empty fixture, reads
control-socket `identify` until `state=ready`, then verifies the native unmanaged
idle-exit policy exits with status 0. It imports no production configuration,
credentials, messaging consumers, profiles or business schedules. Cleanup is
limited to its exact owned child if the test fails; successful runs use no
stop/restart command or forced signal.

Machine-readable stdout markers are `APPROVAL_TEST_RESULT=` and
`GATEWAY_BOOT_RESULT=`. Save full outputs plus image/isolation receipts locally.
Validate enumerated targets and aggregate actual result counts in code.

## What passing does not prove

These are offline SDK-boundary and fresh-Gateway startup tests, not real Feishu
delivery, production role readiness or production init/network/mount topology
acceptance. The isolation init entrypoint is preserved but not exercised by
these tests. A subsequent authorized production cutover must recheck active
work and unapplied Compose changes, use the verified images, preserve role
boundaries and credentials, then verify new processes and real task-thread
message routing. Do not start a second consumer of a production bot for testing.

A separate local Compose **override file** can stage only the tested image
references. Validate its fully resolved model against the current production
model in memory, without printing secrets; the only differences must be those
explicit image fields. Do not edit the live Compose entrypoint or run `up`,
`restart` or `recreate` as part of preparation. Re-read production IDs and start
times before reporting that production was unchanged.
