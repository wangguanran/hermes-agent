#!/usr/bin/env python3
"""Supervise one Hermes messaging gateway plus its Desktop backend.

Run as the final non-root business user under an init/reaper. A Desktop failure
must not restart or interrupt the messaging gateway. No profile data is read.
"""

from __future__ import annotations
import os, signal, subprocess, sys, time
import json
from pathlib import Path


def budget_command(home, executable):
    config = Path(home) / "service/meig-budget.json"
    if not config.exists() or not json.loads(config.read_text()).get("enabled", False):
        return None
    return [
        executable,
        "-B",
        str(config.with_name("meig-budget-proxy.py")),
        "--config",
        str(config),
    ]


def main() -> int:
    executable = os.environ.get("HERMES_SERVICE_PYTHON", "/opt/hermes/.venv/bin/python")
    profile = os.environ.get("HERMES_DESKTOP_PROFILE", "").strip()
    port = int(os.environ.get("HERMES_DESKTOP_PORT", "9119"))
    if not 1 <= port <= 65535 or (
        profile and not all(c.isalnum() or c in "-_" for c in profile)
    ):
        print("invalid desktop profile or port", file=sys.stderr, flush=True)
        return 2
    base = [executable, "-B", "-m", "hermes_cli.main"]
    gateway_cmd = base + ["gateway", "run", "--external-supervisor"]
    if os.environ.get("HERMES_GATEWAY_NO_SUPERVISE") == "1":
        gateway_cmd.append("--no-supervise")
    desktop_cmd = (
        base
        + (["-p", profile] if profile else [])
        + ["serve"]
        + (["--isolated"] if profile else [])
        + ["--host", "0.0.0.0", "--port", str(port), "--skip-build"]
    )
    children = []
    stop = False

    def request_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    def spawn(cmd):
        p = subprocess.Popen(cmd, start_new_session=True)
        children.append(p)
        return p

    def terminate(p):
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    gateway = desktop = budget = None
    budget_cmd = budget_command(
        os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")), executable
    )
    budget_restart_at = None
    budget_backoff = 1.0
    try:
        if budget_cmd:
            budget = spawn(budget_cmd)
        gateway = spawn(gateway_cmd)
        desktop = spawn(desktop_cmd)
        backoff = 1.0
        restart_at = None
        started = time.monotonic()
        while not stop:
            if (
                budget is not None
                and budget.poll() is not None
                and budget_restart_at is None
            ):
                print(
                    "Budget companion exited; restarting without interrupting gateway",
                    file=sys.stderr,
                    flush=True,
                )
                children.remove(budget)
                budget_restart_at = time.monotonic() + budget_backoff
                budget_backoff = min(budget_backoff * 2, 30.0)
            if budget_restart_at is not None and time.monotonic() >= budget_restart_at:
                budget = spawn(budget_cmd)
                budget_restart_at = None
            code = gateway.poll()
            if code is not None:
                print(
                    "messaging gateway exited; stopping Desktop backend",
                    file=sys.stderr,
                    flush=True,
                )
                return code if code > 0 else 1
            if desktop.poll() is not None and restart_at is None:
                print(
                    "Desktop backend exited; gateway remains running",
                    file=sys.stderr,
                    flush=True,
                )
                children.remove(desktop)
                if time.monotonic() - started > 60:
                    backoff = 1.0
                restart_at = time.monotonic() + backoff
                backoff = min(backoff * 2, 30.0)
            if restart_at is not None and time.monotonic() >= restart_at:
                desktop = spawn(desktop_cmd)
                started = time.monotonic()
                restart_at = None
            time.sleep(0.1)
        return 0
    finally:
        for p in children:
            terminate(p)
        deadline = time.monotonic() + 75
        for p in children:
            try:
                p.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()


if __name__ == "__main__":
    raise SystemExit(main())
