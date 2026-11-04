ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BASE_IMAGE_ID
LABEL io.hermes.local.approval-thread.patch-commit="11788b01683b8ac8f2e311d7006a2898086afd50" \
      io.hermes.local.approval-thread.patch-sha256="0cb9d48bf28a5aa5e354677a2c6c22115bc798bc4b10e470f1f6ca66bb2f8ab5" \
      io.hermes.local.approval-thread.base-image-id="${BASE_IMAGE_ID}" \
      io.hermes.local.approval-thread.stage="candidate-not-production"

COPY approval-thread.patch /opt/hermes-local/patches/approval-thread.patch
RUN /opt/hermes/.venv/bin/python -c 'import hashlib, pathlib, sys; p = pathlib.Path("/opt/hermes-local/patches/approval-thread.patch"); sys.exit(0 if hashlib.sha256(p.read_bytes()).hexdigest() == "0cb9d48bf28a5aa5e354677a2c6c22115bc798bc4b10e470f1f6ca66bb2f8ab5" else "Pinned approval-thread patch checksum mismatch")' \
    && cd /opt/hermes \
    && git apply --check --whitespace=error /opt/hermes-local/patches/approval-thread.patch \
    && git apply --whitespace=error /opt/hermes-local/patches/approval-thread.patch \
    && /opt/hermes/.venv/bin/python -c 'import pathlib; p = pathlib.Path("plugins/platforms/feishu/adapter.py"); compile(p.read_text(), str(p), "exec")'

COPY verify.py boot_smoke.py /opt/hermes-local/verification/
# Inherit the verified base's entrypoint, command, user and isolation policy.
# Tests override the entrypoint and use tmpfs only; never mount business data.
