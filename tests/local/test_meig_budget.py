"""Contract tests for the work-container account-wide budget gate (stdlib only)."""

import importlib.util
import pathlib
import unittest

MODULE = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts/local/meig_budget_proxy.py"
)


def load(test):
    test.assertTrue(MODULE.exists(), "meigsmart budget gate is not implemented")
    spec = importlib.util.spec_from_file_location("meig_budget_proxy", MODULE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BudgetTests(unittest.TestCase):
    def test_account_aggregation_counts_cache_once_and_dedupes_request_ids(self):
        mod = load(self)
        rows = [
            {
                "id": 1,
                "request_id": "a",
                "type": 2,
                "model_name": "deepseek",
                "created_at": 10,
                "prompt_tokens": 90,
                "completion_tokens": 10,
                "other": '{"cache_tokens":80}',
            },
            {
                "id": 2,
                "request_id": "a",
                "type": 2,
                "model_name": "deepseek",
                "created_at": 10,
                "prompt_tokens": 90,
                "completion_tokens": 10,
            },
            {
                "id": 1,
                "request_id": "b",
                "type": 2,
                "model_name": "qwen",
                "created_at": 11,
                "prompt_tokens": 20,
                "completion_tokens": 5,
            },
            {
                "id": 3,
                "request_id": "login",
                "type": 7,
                "model_name": "",
                "prompt_tokens": 999,
                "completion_tokens": 0,
            },
        ]
        records = mod.normalize_records(rows)
        self.assertEqual(sum(v["tokens"] for v in records.values()), 125)
        self.assertEqual(set(records), {"a", "b"})


class LedgerTests(unittest.TestCase):
    def test_shared_reservations_reconcile_and_reset_fail_closed(self):
        mod = load(self)
        self.assertTrue(
            hasattr(mod, "BudgetLedger"), "shared reservation ledger is missing"
        )
        import tempfile
        from concurrent.futures import ThreadPoolExecutor

        root = MODULE.parents[2] / "test-runtime"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            clock = [1793880000.0]
            ledger = mod.BudgetLedger(
                pathlib.Path(tmp) / "state.json",
                cap=200,
                clock=lambda: clock[0],
                stale_seconds=90,
            )
            with self.assertRaises(mod.BudgetDenied):
                ledger.reserve(20, "work", "model")
            ledger.reconcile(
                {"known": {"tokens": 100, "model": "x", "created_at": int(clock[0])}}
            )
            with ThreadPoolExecutor(max_workers=2) as pool:

                def reserve(_):
                    try:
                        return ledger.reserve(60, "work", "model")
                    except mod.BudgetDenied:
                        return None

                tokens = list(pool.map(reserve, range(2)))
            self.assertEqual(sum(v is not None for v in tokens), 1)
            token = next(v for v in tokens if v)
            ledger.complete(token, "new-request", 40)
            self.assertEqual(ledger.status()["effective_tokens"], 140)
            ledger.reconcile(
                {
                    "known": {"tokens": 100, "model": "x", "created_at": int(clock[0])},
                    "new-request": {
                        "tokens": 40,
                        "model": "model",
                        "created_at": int(clock[0]),
                    },
                }
            )
            self.assertEqual(ledger.status()["effective_tokens"], 140)
            self.assertEqual(ledger.status()["docker_tokens"], 40)
            restored = mod.BudgetLedger(
                pathlib.Path(tmp) / "state.json",
                cap=200,
                clock=lambda: clock[0],
                stale_seconds=90,
            )
            with self.assertRaises(mod.BudgetDenied):
                restored.reserve(1, "driver", "model")
            clock[0] += 91
            with self.assertRaises(mod.BudgetDenied):
                ledger.reserve(1, "driver", "model")
            clock[0] += 86400
            with self.assertRaises(mod.BudgetDenied):
                ledger.reserve(1, "project", "model")
            ledger.reconcile({})
            self.assertIsNotNone(ledger.reserve(199, "project", "model"))
        root.rmdir()


class ProxyTests(unittest.TestCase):
    def test_http_blocks_without_forwarding_and_passes_stream_unchanged(self):
        mod = load(self)
        self.assertTrue(
            hasattr(mod, "create_server"), "request-entry budget enforcement is missing"
        )
        import http.server
        import json
        import tempfile
        import threading
        import urllib.error
        import urllib.request

        calls = []
        wire = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":90,"output_tokens":10}}}\n\ndata: [DONE]\n\n'

        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers["Content-Length"])))
                if json.loads(calls[-1]).get("model") == "redirect":
                    self.send_response(302)
                    self.send_header(
                        "Location", f"http://127.0.0.1:{self.server.server_port}/leak"
                    )
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("X-Oneapi-Request-Id", "upstream-rid")
                self.end_headers()
                self.wfile.write(wire)

            def log_message(self, format, *args):
                pass

        root = MODULE.parents[2] / "test-runtime"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            ledger = mod.BudgetLedger(pathlib.Path(tmp) / "state.json", cap=100000)
            upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
            proxy = mod.create_server(
                ledger, f"http://127.0.0.1:{upstream.server_port}", ["test-key"], port=0
            )
            for server in [upstream, proxy]:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            try:

                def send(key="test-key", model="test"):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{proxy.server_port}/v1/responses",
                        data=json.dumps(
                            {"model": model, "max_output_tokens": 10, "stream": True}
                        ).encode(),
                        headers={
                            "Authorization": "Bearer " + key,
                            "Content-Type": "application/json",
                            "X-Hermes-Budget-Profile": "driver-engineer",
                        },
                    )
                    return urllib.request.urlopen(request, timeout=5)

                ledger.reconcile(
                    {"full": {"tokens": 100000, "model": "test", "created_at": 1}}
                )
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    send()
                self.assertEqual(denied.exception.code, 429)
                denied.exception.close()
                self.assertEqual(calls, [])
                ledger.reconcile({})
                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    send("wrong-key")
                self.assertEqual(unauthorized.exception.code, 401)
                unauthorized.exception.close()
                with send() as response:
                    self.assertEqual(response.read(), wire)
                self.assertEqual(len(calls), 1)
                self.assertEqual(ledger.status()["docker_tokens"], 100)
                self.assertEqual(ledger.status()["roles"], {"driver-engineer": 100})
                self.assertEqual(
                    next(iter(ledger.data["pending"].values()))["request_id"],
                    "upstream-rid",
                )
                ledger.reconcile(
                    {
                        "upstream-rid": {
                            "tokens": 100,
                            "model": "test",
                            "created_at": 123,
                        }
                    }
                )
                self.assertEqual(ledger.status()["pending_tokens"], 0)
                self.assertEqual(ledger.status()["effective_tokens"], 100)
                with self.assertRaises(urllib.error.HTTPError) as redirected:
                    send(model="redirect")
                self.assertEqual(redirected.exception.code, 302)
                redirected.exception.close()
            finally:
                for server in [proxy, upstream]:
                    server.shutdown()
                    server.server_close()
        root.rmdir()


class CollectorTests(unittest.TestCase):
    def test_unknown_usage_is_accounted_when_gateway_log_arrives(self):
        import tempfile

        mod = load(self)
        root = MODULE.parents[2] / "test-runtime"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            ledger = mod.BudgetLedger(pathlib.Path(tmp) / "state.json", cap=10000)
            ledger.reconcile({})
            key = ledger.reserve(100, "project-assistant", "test")
            ledger.complete(key, "unknown-usage-request", None)
            self.assertEqual(ledger.status()["pending_tokens"], 100)
            ledger.reconcile(
                {
                    "unknown-usage-request": {
                        "tokens": 40,
                        "model": "test",
                        "created_at": 123,
                    }
                }
            )
            self.assertEqual(ledger.status()["pending_tokens"], 0)
            self.assertEqual(ledger.status()["docker_tokens"], 40)
            self.assertEqual(ledger.status()["roles"], {"project-assistant": 40})
        root.rmdir()

    def test_fixed_window_pagination_rejects_incomplete_results(self):
        mod = load(self)
        self.assertTrue(
            hasattr(mod, "collect_records"),
            "authoritative gateway pagination is missing",
        )
        from urllib.parse import parse_qs, urlparse

        rows = [
            {
                "id": i % 100 + 1,
                "request_id": f"r{i}",
                "type": 2,
                "model_name": "test",
                "created_at": 123,
                "prompt_tokens": 10,
                "completion_tokens": 1,
            }
            for i in range(101)
        ]

        def request(path):
            q = parse_qs(urlparse(path).query)
            self.assertEqual(q["start_timestamp"], ["100"])
            self.assertEqual(q["end_timestamp"], ["200"])
            page = int(q["p"][0])
            return {
                "success": True,
                "data": {"total": 101, "items": rows[(page - 1) * 100 : page * 100]},
            }

        records = mod.collect_records(request, 100, 200)
        self.assertEqual(len(records), 101)
        self.assertEqual(sum(v["tokens"] for v in records.values()), 1111)
        with self.assertRaises(ValueError):
            mod.collect_records(
                lambda _: {"success": True, "data": {"total": 2, "items": rows[:1]}},
                100,
                200,
            )


class SupervisorTests(unittest.TestCase):
    def test_budget_supervision_is_opt_in_per_container_home(self):
        import tempfile

        spec = importlib.util.spec_from_file_location(
            "work_service", MODULE.parent / "hermes_work_service.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(
            hasattr(mod, "budget_command"), "budget process supervision is missing"
        )
        root = MODULE.parents[2] / "test-runtime"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            home = pathlib.Path(tmp)
            self.assertIsNone(mod.budget_command(home, "python"))
            (home / "service").mkdir()
            (home / "service/meig-budget.json").write_text('{"enabled":true}')
            self.assertEqual(
                mod.budget_command(home, "python"),
                [
                    "python",
                    "-B",
                    str(home / "service/meig-budget-proxy.py"),
                    "--config",
                    str(home / "service/meig-budget.json"),
                ],
            )
        root.rmdir()

    def test_proxy_restarts_without_restarting_gateway(self):
        import os, subprocess, sys, tempfile, time

        root = MODULE.parents[2] / "test-runtime"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            home = pathlib.Path(tmp)
            (home / "service").mkdir()
            (home / "service/meig-budget.json").write_text('{"enabled":true}')
            executable = home / "fake-python"
            executable.write_text(
                "#!"
                + sys.executable
                + '\nimport os,sys,time,pathlib\nh=pathlib.Path(os.environ["HERMES_HOME"])\nrole="budget" if any("meig-budget-proxy.py" in a for a in sys.argv) else "gateway" if "gateway" in sys.argv else "desktop"\nwith (h/"starts").open("a") as f:f.write(role+"\\n")\nif role=="budget" and not (h/"failed-once").exists():\n (h/"failed-once").touch();raise SystemExit(7)\nwhile True:time.sleep(.1)\n'
            )
            executable.chmod(0o700)
            env = {
                **os.environ,
                "HERMES_HOME": str(home),
                "HERMES_SERVICE_PYTHON": str(executable),
            }
            proc = subprocess.Popen(
                [sys.executable, str(MODULE.parent / "hermes_work_service.py")],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    starts = (
                        (home / "starts").read_text().splitlines()
                        if (home / "starts").exists()
                        else []
                    )
                    if starts.count("budget") >= 2:
                        break
                    time.sleep(0.1)
                self.assertGreaterEqual(
                    starts.count("budget"), 2, "budget companion was not supervised"
                )
                self.assertEqual(starts.count("gateway"), 1)
            finally:
                proc.terminate()
                proc.wait(timeout=5)
        root.rmdir()


if __name__ == "__main__":
    unittest.main()
