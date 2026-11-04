# Operator file-tool deny roots

## Local customization

- `agent/file_safety.py`: opt-in `HERMES_FILE_DENY_ROOTS` supplements native file-tool read and write checks with operator-selected exact files or directory trees.
- `tests/agent/test_operator_file_denials.py`: regression coverage for opt-in behaviour, file/directory boundaries, native defaults, symlink resolution, multiple roots, tilde expansion, root-directory handling and approval precedence.

The setting uses the platform path separator (`:` on Unix, `;` on Windows), like `HERMES_WRITE_SAFE_ROOT`. Unset or empty means no additional denials; native credential protections and the optional write sandbox remain unchanged. Entries are expanded with `expanduser` and resolved before comparison. A configured file name does not match a sibling with a longer name.

For a container with a single canonical home, for example:

```sh
HERMES_WRITE_SAFE_ROOT=/home/agent
HERMES_FILE_DENY_ROOTS=/home/agent/.lark-cli:/home/agent/.hermes/auth.json:/home/agent/.hermes/auth.lock
```

Add each served profile's sensitive files explicitly when they share a process. Do not treat independent bind aliases as canonical paths: filesystem `realpath` does not merge bind mounts. Prefer one canonical data mount, and test the actual deployed profile scope and file tools before enabling a broader write root.

An operator denial takes precedence over an approval-gated file write. Reads, writes and patch operations using the shared file-safety module are covered. Credential consumers that use their own internal filesystem access are intentionally unaffected, so token rotation and native session bookkeeping can continue.

This is defence in depth for file tools, **not an OS sandbox**. Terminal commands, other code and processes running as the same OS user are not contained by this setting. Preserve native approval policy and independent OS/container protection for secrets. Do not claim this setting isolates profiles from one another.

## Upgrade and rollback

Replay the scoped source change and its tests on the deployed baseline, run the native file-safety regression group and a no-credential multi-profile file-tool fixture, then build the target image. Enable the setting only for the authorized deployment. Other deployments remain unchanged when the setting is absent.

Removing the extra setting restores native policy, including native control-file write behaviour; it does not erase or migrate credentials. Configuration readback, isolated tests and activation in the running Gateway are separate acceptance steps.
