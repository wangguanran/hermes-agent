#!/usr/bin/env python3
"""Start a fresh, offline Gateway; observe ready and its native idle exit.

The caller must provide --network=none, a read-only root and /validation tmpfs.
No production home, credentials, ports, profiles, or schedules are imported.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def identify(home):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1)
        client.connect(str(home / "gateway.sock"))
        client.sendall(b'{"protocol":1,"id":1,"verb":"identify"}\n')
        with client.makefile("rb") as stream:
            response = json.loads(stream.readline(131072))
    if response.get("ok") is False:
        return None
    return response.get("result", response)


def main():
    home = Path(os.environ.get("HERMES_HOME", "")).resolve()
    if home.parent != Path("/validation"):
        raise SystemExit("Refusing to use a non-fixture HERMES_HOME")
    for directory in (home, Path("/validation/home"), Path("/validation/scratch")):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = home / "config.yaml"
    if config.exists():
        raise SystemExit("Refusing to overwrite an existing fixture configuration")
    # Synthetic fixture only, not a hand-edit of any user's configuration.
    config.write_text(
        "gateway:\n  unmanaged_idle_exit_seconds: 3\n"
        "kanban:\n  dispatch_in_gateway: false\n  notifications_enabled: false\n"
        "  review_auto_dispatch: false\n  auto_subscribe: false\n",
        encoding="utf-8",
    )
    log = Path("/validation/scratch/gateway-boot.log")
    env = dict(os.environ, API_SERVER_ENABLED="false")
    command = [sys.executable, "-B", "-m", "hermes_cli.main", "gateway", "run", "--idle-exit"]
    ready = None
    exit_code = None
    with log.open("w", encoding="utf-8") as output:
        # This child belongs only to this empty fixture, never a production Gateway.
        child = subprocess.Popen(command, cwd="/opt/hermes", env=env,
                                 stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline and child.poll() is None:
                if (home / "gateway.sock").exists():
                    try:
                        data = identify(home)
                        if isinstance(data, dict) and data.get("state") == "ready":
                            ready = data
                            break
                    except (OSError, ValueError):
                        pass
                time.sleep(0.1)
            if ready is None:
                raise AssertionError("Fresh fixture Gateway did not reach control-socket ready")
            # The supported idle-exit policy must terminate it without a stop/restart command.
            exit_code = child.wait(timeout=30)
            if exit_code != 0:
                raise AssertionError(f"Fixture idle exit was not clean: {exit_code}")
        finally:
            if child.poll() is None:
                # Exact owned fixture child only; no process-name matching or production control.
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
    print("GATEWAY_BOOT_RESULT=" + json.dumps({
        "ready": True, "pid": ready.get("pid"), "exit_code": exit_code,
        "mode": "fresh-home-offline-native-idle-exit", "production_data_used": False,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path = Path("/validation/scratch/gateway-boot.log")
        if log_path.exists():
            print(log_path.read_text(encoding="utf-8", errors="replace")[-12000:], file=sys.stderr)
        raise
