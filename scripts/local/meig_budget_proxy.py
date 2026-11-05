#!/usr/bin/env python3
"""Account-wide meigsmart budget gate; no request bodies or keys are logged."""

import datetime as dt
import json
import os
import threading
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")


class BudgetDenied(Exception):
    """An exhausted or unverifiable account budget; do not forward upstream."""


class BudgetLedger:
    def __init__(self, path, cap=200_000_000, clock=time.time, stale_seconds=90):
        self.path, self.cap, self.clock = Path(path), cap, clock
        self.stale_seconds, self.lock = stale_seconds, threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data = {
            "date": self.day(),
            "records": {},
            "pending": {},
            "roles": {},
            "models": {},
        }
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        self.last_sync = 0  # A restart must re-validate the authoritative day.

    def day(self):
        return dt.datetime.fromtimestamp(self.clock(), BJ).date().isoformat()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def reconcile(self, records, full=True):
        with self.lock:
            if self.data["date"] != self.day():
                # Keep the previous daily totals, without modifying any Hermes database.
                self._save()
                archive = self.path.with_name(
                    self.path.stem + "-" + self.data["date"] + ".json"
                )
                os.replace(self.path, archive)
                self.data = {
                    "date": self.day(),
                    "records": {},
                    "pending": {},
                    "roles": {},
                    "models": {},
                }
            if full:
                self.data["records"] = records
            else:
                self.data["records"].update(records)
            for key, pending in list(self.data["pending"].items()):
                if pending.get("request_id") in self.data["records"] and pending.get(
                    "complete"
                ):
                    if not pending.get("usage_known", False):
                        self._credit(
                            pending,
                            self.data["records"][pending["request_id"]]["tokens"],
                        )
                    del self.data["pending"][key]
            self.last_sync = self.clock()
            self.data["last_sync"] = self.last_sync
            self._save()

    def status(self):
        with self.lock:
            account = sum(v["tokens"] for v in self.data["records"].values())
            pending = sum(v["tokens"] for v in self.data["pending"].values())
            fresh = (
                self.data["date"] == self.day()
                and self.last_sync > 0
                and 0 <= self.clock() - self.last_sync <= self.stale_seconds
            )
            return {
                "date": self.data["date"],
                "cap": self.cap,
                "account_tokens": account,
                "pending_tokens": pending,
                "effective_tokens": account + pending,
                "docker_tokens": sum(self.data["roles"].values()),
                "roles": dict(self.data["roles"]),
                "models": dict(self.data["models"]),
                "fresh": fresh,
                "last_sync": self.last_sync,
                "blocked": not fresh or account + pending >= self.cap,
            }

    def reserve(self, tokens, role, model):
        with self.lock:
            status = self.status()
            if not status["fresh"]:
                raise BudgetDenied(
                    "meigsmart budget data unavailable or stale; use next fallback"
                )
            if tokens <= 0 or status["effective_tokens"] + tokens > self.cap:
                raise BudgetDenied(
                    "meigsmart daily token budget exhausted or insufficient; use next fallback"
                )
            key = uuid.uuid4().hex
            self.data["pending"][key] = {
                "tokens": tokens,
                "role": role,
                "model": model,
                "complete": False,
            }
            self._save()  # Durable reservation BEFORE the network side effect.
            return key

    def _credit(self, pending, usage):
        pending["tokens"] = max(0, int(usage))
        pending["usage_known"] = True
        role, model = pending["role"], pending["model"]
        self.data["roles"][role] = self.data["roles"].get(role, 0) + pending["tokens"]
        self.data["models"][model] = (
            self.data["models"].get(model, 0) + pending["tokens"]
        )

    def complete(self, key, request_id, usage):
        with self.lock:
            pending = self.data["pending"].get(key)
            if pending is None or pending.get("complete"):
                return
            pending.update(request_id=request_id, complete=True, usage_known=False)
            if usage is not None:
                self._credit(pending, usage)
            # Unknown usage retains its reservation until the account log confirms it.
            if request_id in self.data["records"]:
                if not pending["usage_known"]:
                    self._credit(pending, self.data["records"][request_id]["tokens"])
                del self.data["pending"][key]
            self._save()


def normalize_records(rows):
    """New API may renumber public log IDs per page: request_id is the identity."""
    records = {}
    for row in rows:
        if row.get("type", 2) != 2 or not row.get("model_name"):
            continue
        tokens = int(row.get("prompt_tokens") or 0) + int(
            row.get("completion_tokens") or 0
        )
        if not tokens:
            continue
        rid = row.get("request_id")
        if not rid or tokens < 0:
            raise ValueError("invalid consumption record")
        value = {
            "tokens": tokens,
            "model": row["model_name"],
            "created_at": int(row["created_at"]),
        }
        if rid in records and records[rid] != value:
            raise ValueError("conflicting consumption records")
        records[rid] = value
    return records


class UsageObserver:
    """Bounded in-memory parsing only; the original bytes are forwarded unchanged."""

    def __init__(self, streaming):
        self.streaming, self.buffer, self.tokens = streaming, b"", None

    def observe(self, obj):
        usage = obj.get("usage") or (obj.get("response") or {}).get("usage")
        if usage:
            self.tokens = int(
                usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            ) + int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)

    def feed(self, chunk):
        self.buffer += chunk
        if self.streaming:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if line.startswith(b"data:"):
                    try:
                        self.observe(json.loads(line[5:].strip()))
                    except (ValueError, TypeError, AttributeError):
                        pass
        if len(self.buffer) > 4 * 1024 * 1024:
            self.buffer = b""  # Unknown usage stays conservatively reserved.

    def finish(self):
        if not self.streaming:
            try:
                self.observe(json.loads(self.buffer))
            except (ValueError, TypeError, AttributeError):
                pass
        return self.tokens


def create_server(ledger, upstream, api_keys, port=18181):
    import hmac
    import http.server
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never emit URLs, headers, prompts, responses or API keys.

        def send_json(self, status, data, extra=None):
            wire = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(wire)))
            for key, value in (extra or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(wire)

        def authorized(self):
            supplied = self.headers.get("Authorization", "")
            return any(
                hmac.compare_digest(supplied, "Bearer " + key)
                for key in api_keys
                if key
            )

        def do_GET(self):
            if self.path == "/healthz":
                self.send_json(200, ledger.status())
            elif self.path == "/v1/models" and self.authorized():
                self.forward(None)
            else:
                self.send_json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self.authorized():
                self.send_json(401, {"error": {"message": "unauthorized"}})
                return
            if self.path not in {"/v1/responses", "/v1/chat/completions"}:
                self.send_json(404, {"error": {"message": "unsupported operation"}})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 32 * 1024 * 1024:
                    raise ValueError("invalid request size")
                body = self.rfile.read(size)
                obj = json.loads(body)
                output = int(
                    obj.get("max_output_tokens")
                    or obj.get("max_completion_tokens")
                    or obj.get("max_tokens")
                    or 65536
                )
                if not 0 < output <= 2_000_000:
                    raise ValueError("invalid output budget")
                role = self.headers.get("X-Hermes-Budget-Profile", "unattributed")
                if role not in {
                    "default",
                    "driver-engineer",
                    "project-assistant",
                    "unattributed",
                }:
                    role = "unattributed"
                key = ledger.reserve(
                    len(body) + output, role, str(obj.get("model") or "unknown")
                )
            except BudgetDenied as exc:
                self.send_json(
                    429,
                    {
                        "error": {
                            "message": str(exc),
                            "type": "insufficient_quota",
                            "code": "insufficient_quota",
                        }
                    },
                    {"Retry-After": "30"},
                )
                return
            except (ValueError, TypeError):
                self.send_json(400, {"error": {"message": "invalid request"}})
                return
            self.forward(body, key)

        def forward(self, body, key=None):
            headers = {
                "Authorization": self.headers.get("Authorization", ""),
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            }
            for name in [
                "Accept",
                "User-Agent",
                "OpenAI-Beta",
                "X-Request-Id",
                "X-Client-Request-Id",
            ]:
                if self.headers.get(name):
                    headers[name] = self.headers[name]
            request = urllib.request.Request(
                upstream.rstrip("/") + self.path, data=body, headers=headers
            )
            request_id, observer, started = "", None, False
            try:
                try:
                    response = opener.open(request, timeout=180)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    request_id = response.headers.get(
                        "X-Request-Id"
                    ) or response.headers.get("X-Oneapi-Request-Id", "")
                    observer = UsageObserver(
                        "text/event-stream" in response.headers.get("Content-Type", "")
                    )
                    self.send_response(int(response.status or 502))
                    for name in [
                        "Content-Type",
                        "X-Request-Id",
                        "X-Oneapi-Request-Id",
                        "Retry-After",
                    ]:
                        if response.headers.get(name):
                            self.send_header(name, response.headers[name])
                    self.end_headers()
                    started = True
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        observer.feed(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (OSError, urllib.error.URLError):
                if not started:
                    self.send_json(
                        502,
                        {"error": {"message": "meigsmart upstream connection failed"}},
                    )
            finally:
                if key:
                    ledger.complete(
                        key, request_id, observer.finish() if observer else None
                    )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def collect_records(request, start, end):
    from urllib.parse import urlencode

    rows, total, page = [], None, 1
    while total is None or len(rows) < total:
        query = urlencode(
            {
                "p": page,
                "page_size": 100,
                "type": 2,
                "start_timestamp": start,
                "end_timestamp": end,
            }
        )
        result = request("/api/log/self?" + query)
        if not result.get("success"):
            raise ValueError("gateway log query failed")
        data = result["data"]
        expected = int(data["total"])
        if total is not None and total != expected:
            raise ValueError("gateway log snapshot changed; retry next collection")
        total = expected
        items = data["items"]
        if (not items and len(rows) < total) or (
            page == 1 and len(items) != min(total, 100)
        ):
            raise ValueError("incomplete gateway log page")
        rows.extend(items)
        if len(rows) > total or page > 2000:
            raise ValueError("invalid gateway log pagination")
        page += 1
    ids = [r.get("request_id") for r in rows]
    if not all(ids) or len(set(ids)) != total:
        raise ValueError(
            "duplicate or missing request identities in gateway pagination"
        )
    return normalize_records(rows)


class GatewayCollector:
    def __init__(self, ledger, request):
        self.ledger, self.request = ledger, request
        self.last_end, self.last_full = 0, 0

    def refresh(self):
        now = self.ledger.clock()
        today = dt.datetime.fromtimestamp(now, BJ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_day, end = int(today.timestamp()), int(now)
        full = (
            self.ledger.data["date"] != self.ledger.day()
            or not self.last_end
            or now - self.last_full >= 300
        )
        start = start_day if full else max(start_day, self.last_end - 300)
        records = collect_records(self.request, start, end)
        # Reject a date-crossing collection instead of labelling yesterday as today.
        if self.ledger.day() != today.date().isoformat():
            raise ValueError("date changed while collecting gateway logs")
        self.ledger.reconcile(records, full=full)
        self.last_end = end
        if full:
            self.last_full = now


def main():
    import argparse
    import importlib.util
    import signal
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    root = Path(config["hermes_home"])
    credentials = {}
    for line in (root / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            credentials[name.strip()] = value.strip().strip('"').strip("'")
    spec = importlib.util.spec_from_file_location(
        "budget_gw_session", root / "scripts/gw_session.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state_dir = root / "cache/meig-budget-live"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ledger = BudgetLedger(
        state_dir / "state.json",
        cap=int(config["cap"]),
        stale_seconds=int(config["stale_seconds"]),
    )

    def request(path):
        return module.authed_request(
            credentials["MEIG_GATEWAY_USER"],
            credentials["MEIG_GATEWAY_PASS"],
            "GET",
            path,
            timeout=20,
            cache_path=str(state_dir / "gateway-auth.json"),
            revoke_others=False,
        )

    collector = GatewayCollector(ledger, request)
    stop = threading.Event()

    def refresh():
        try:
            collector.refresh()
            return True
        except Exception as exc:
            # Never echo upstream errors, which can contain authentication material.
            print(
                "meig budget collection failed: " + type(exc).__name__,
                file=sys.stderr,
                flush=True,
            )
            return False

    ok = refresh()
    if args.once:
        print(json.dumps(ledger.status(), ensure_ascii=False))
        return 0 if ok else 1
    api_key = credentials.get("MEIGSMART_API_KEY")
    if not api_key:
        raise ValueError("meigsmart API credential missing")
    server = create_server(
        ledger, config["upstream"], [api_key], port=int(config["port"])
    )

    def poll():
        while not stop.wait(int(config["poll_seconds"])):
            refresh()

    threading.Thread(target=poll, daemon=True).start()

    def shutdown(signum, frame):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print("meig budget proxy ready on loopback", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
