"""Harness-only contract tests, NOT real-engine integration verification.

Run without installs or sockets: python3 -B -m unittest -v test_adapter
This file also acts as the fake stdio child when passed --harness.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapter


def harness():
    """Authored protocol behavior only; no real memory operations are simulated as verified."""
    def emit(value):
        print(json.dumps(value), flush=True)

    request = json.loads(sys.stdin.readline())
    mode = request["params"].get("harness", "normal")
    if mode == "die":
        return
    if mode == "bad":
        print("not JSON", flush=True)
        return
    if mode == "oversize":
        print("x" * (adapter.MAX_FRAME + 1), flush=True)
        return
    if mode == "hang":
        # Deliberately ignores EOF; teardown must terminate and reap this child.
        time.sleep(60)
        return
    if request["method"] == "memorize":
        for call_id, method in (("source", "source.read"), ("reflection", "model.reflect")):
            emit({"callId": call_id, "method": method, "params": {"episodeId": "authored-test"}})
            if mode == "retire":
                emit({"cancelCall": call_id})
                emit({"id": 1, "error": {"code": "CALLBACK_TIMEOUT", "message": "Harness timeout"}})
                return
            answer = json.loads(sys.stdin.readline())
            if answer.get("method") == "cancel":
                emit({"id": 2, "result": {"cancelled": True}})
                emit({"cancelCall": call_id})
                emit({"id": 1, "error": {"code": "CANCELLED", "message": "Harness cancelled"}})
                return
            if "error" in answer:
                emit({"id": 1, "error": answer["error"]})
                return
        emit({"id": 1, "result": {"harnessOnly": True}})
    else:
        emit({"id": 1, "result": {"method": request["method"], "params": request["params"],
                                  "argv": sys.argv[2:]}})


class MemorySocket:
    """Exercise BaseHTTPRequestHandler parsing and wire output without networking."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)


TOKEN = "synthetic-only-adapter-token-000000000000"


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.real_popen = subprocess.Popen

        def fake_popen(argv, **kwargs):
            return self.real_popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                    "--harness", *argv[1:]], **kwargs)

        self.patcher = patch.object(adapter.subprocess, "Popen", side_effect=fake_popen)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.bridge = adapter.Bridge("harness-only", self.temp.name, timeout=3)
        self.addCleanup(self.bridge.close)

    def start(self, method="ping", character="mara", params=None):
        return self.bridge.get(self.bridge.start({"character": character, "method": method,
                                                  "params": params or {}})["job"])

    def wait(self, job, state="done", call=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = job.snapshot()
            if value["state"] == state and (call is None or value["callback"]["callId"] == call):
                return value
            time.sleep(0.005)
        self.fail(f"Job did not reach {state}: {job.snapshot()}")

    def http(self, method, path, value=None, raw=None, headers=None):
        body = adapter.encode(value) if raw is None and value is not None else (raw or b"")
        header_lines = headers if headers is not None else [f"Content-Length: {len(body)}"]
        if headers is None:
            header_lines.append("Authorization: Bearer " + TOKEN)
        request = (f"{method} {path} HTTP/1.1\r\nHost: harness\r\n" +
                   "\r\n".join(header_lines) + "\r\n\r\n").encode() + body
        sock = MemorySocket(request)
        adapter.Handler(sock, ("harness", 0), SimpleNamespace(bridge=self.bridge, authorization=("Bearer " + TOKEN).encode()))
        head, payload = sock.output.getvalue().split(b"\r\n\r\n", 1)
        status = int(head.split(b" ")[1])
        self.assertIn(b"Connection: close", head)
        self.assertIn(f"Content-Length: {len(payload)}".encode(), head)
        return status, json.loads(payload)

    def test_unauthenticated_job_is_rejected_before_json(self):
        status, value = self.http("POST", "/jobs", raw=b"{", headers=["Content-Length: 1"])
        self.assertEqual(status, 401)
        self.assertEqual(self.bridge.jobs, {})

    def test_bad_auth_never_looks_up_jobs_or_parses_bodies(self):
        with patch.object(self.bridge, 'get') as get, patch.object(self.bridge, 'start') as start:
            for credential in ([], ['Authorization: Bearer wrong'],
                               ['Authorization: Bearer ' + TOKEN] * 2):
                for method, path in [('POST', '/jobs'), ('GET', '/jobs/' + '0' * 32),
                                     ('POST', '/jobs/' + '0' * 32 + '/callback'),
                                     ('POST', '/jobs/' + '0' * 32 + '/cancel')]:
                    self.assertEqual(self.http(method, path, raw=b'{', headers=credential)[0], 401)
            get.assert_not_called()
            start.assert_not_called()

    def test_http_health_job_and_repeatable_result(self):
        self.assertEqual(self.http("GET", "/health"), (200, {"ok": True}))
        status, value = self.http("POST", "/jobs", {"character": "mara", "method": "ping", "params": {}})
        self.assertEqual(status, 200)
        self.assertEqual(set(value), {"job"})
        job = self.bridge.get(value["job"])
        result = self.wait(job)
        self.assertEqual(result["result"]["method"], "ping")
        self.assertIn(str(Path(self.temp.name) / "mara.sqlite"), result["result"]["argv"])
        for _ in range(3):
            self.assertEqual(self.http("GET", "/jobs/" + job.id), (200, result))

    def test_forget_is_allowed_and_reaches_engine_without_callbacks(self):
        result = self.wait(self.start('forget', params={'episodeId':'synthetic-scene'}))
        self.assertEqual(result['result']['method'], 'forget')
        self.assertEqual(result['result']['params'], {'episodeId':'synthetic-scene'})

    def test_full_callback_frames_and_exact_replies(self):
        job = self.start("memorize")
        value = self.wait(job, "callback", "source")
        self.assertEqual(value["callback"], {"callId": "source", "method": "source.read",
                                            "params": {"episodeId": "authored-test"}})
        self.assertEqual(self.http("GET", "/jobs/" + job.id), (200, value))
        endpoint = f"/jobs/{job.id}/callback"
        for bad in ({"callId": "wrong", "result": {}},
                    {"callId": "source", "result": {}, "extra": True},
                    {"callId": "source", "result": {}, "error": {}},
                    {"callId": "source", "error": {"code": 5, "message": "bad"}}):
            self.assertIn(self.http("POST", endpoint, bad)[0], (400, 409))
        self.assertEqual(self.http("POST", endpoint, {"callId": "source", "result": {}}),
                         (200, {"ok": True}))
        self.wait(job, "callback", "reflection")
        self.assertEqual(self.http("POST", endpoint, {"callId": "source", "result": {}})[0], 409)
        job.reply({"callId": "reflection", "result": "authored harness reply"})
        self.assertEqual(self.wait(job), {"state": "done", "result": {"harnessOnly": True}})

    def test_callback_error_passes_through(self):
        job = self.start("memorize")
        self.wait(job, "callback")
        error = {"code": "HOST_FAILURE", "message": "Authored harness failure"}
        job.reply({"callId": "source", "error": error})
        self.assertEqual(self.wait(job), {"state": "done", "error": error})

    def test_cancel_retires_callback_and_ignores_rpc_two(self):
        job = self.start("memorize")
        self.wait(job, "callback")
        job.reply({"callId": "source", "result": {}})
        self.wait(job, "callback", "reflection")
        self.assertEqual(self.http("POST", f"/jobs/{job.id}/cancel"), (200, {"ok": True}))
        self.assertEqual(self.http("POST", f"/jobs/{job.id}/callback",
                                   {"callId": "reflection", "result": "late"})[0], 409)
        self.assertEqual(self.wait(job)["error"]["code"], "CANCELLED")
        self.assertEqual(self.http("POST", f"/jobs/{job.id}/cancel"), (200, {"ok": True}))

    def test_unsolicited_cancel_call_retires_callback(self):
        job = self.start("memorize", params={"harness": "retire"})
        self.assertEqual(self.wait(job)["error"]["code"], "CALLBACK_TIMEOUT")
        with self.assertRaises(adapter.Problem):
            job.reply({"callId": "source", "result": {}})

    def test_cancel_discards_unsent_reply_and_preserves_request_order(self):
        job = adapter.Job("mara", "memorize", {})
        job.frame({"callId": "reflection", "method": "model.reflect", "params": {}})
        job.reply({"callId": "reflection", "result": "late reflection"})
        job.cancel()
        self.assertEqual([kind for kind, _ in job.outgoing], ["request", "cancel"])
        self.assertEqual(adapter.decode(job.outgoing[-1][1]), {"id": 2, "method": "cancel", "params": {}})
        job.frame({"callId": "late", "method": "model.reflect", "params": {}})
        self.assertEqual(job.snapshot(), {"state": "pending"})
        with self.assertRaises(adapter.Problem):
            job.reply({"callId": "late", "result": {}})

    def test_status_can_complete_while_reflection_waits(self):
        job = self.start("memorize")
        self.wait(job, "callback")
        with self.assertRaises(adapter.Problem) as error:
            self.start("retrieve")
        self.assertEqual(error.exception.code, "CHARACTER_BUSY")
        status = self.start("status")
        self.assertEqual(self.wait(status)["result"]["method"], "status")
        self.assertEqual(job.snapshot()["state"], "callback")

    def test_invalid_jobs_and_paths(self):
        for character in ("../escape", "", "a/b", "a.b", "é", "a" * 65, "a\n", 42):
            with self.assertRaises(adapter.Problem):
                self.start(character=character)
        for value in ([], {}, {"character": "mara", "method": "clearMemory", "params": {}},
                      {"character": "mara", "method": [], "params": {}},
                      {"character": "mara", "method": "ping", "params": []}):
            self.assertEqual(self.http("POST", "/jobs", value)[0], 400)
        self.assertEqual(self.http("GET", "/jobs/" + "0" * 32)[0], 404)
        self.assertEqual(self.http("GET", "/jobs/../../etc")[0], 404)

    def test_body_limits_and_strict_json(self):
        for raw in (b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'"\xff"'):
            self.assertEqual(self.http("POST", "/jobs", raw=raw)[0], 400)
        self.assertEqual(self.http("POST", "/jobs", headers=["Authorization: Bearer " + TOKEN])[0], 400)
        self.assertEqual(self.http("POST", "/jobs", headers=["Authorization: Bearer " + TOKEN, "Content-Length: 0", "Content-Length: 0"])[0], 400)
        self.assertEqual(self.http("POST", "/jobs", headers=["Authorization: Bearer " + TOKEN, "Content-Length: 0", "Transfer-Encoding: chunked"])[0], 400)
        self.assertEqual(self.http("POST", "/jobs", headers=["Authorization: Bearer " + TOKEN, f"Content-Length: {adapter.MAX_BODY + 1}"])[0], 413)

    def test_death_bad_frames_and_unavailable(self):
        for index, (mode, code) in enumerate((("die", "ENGINE_DIED"), ("bad", "ENGINE_PROTOCOL_ERROR"),
                                              ("oversize", "ENGINE_PROTOCOL_ERROR"))):
            job = self.start(character=f"mara{index}", params={"harness": mode})
            self.assertEqual(self.wait(job)["error"]["code"], code)
        with patch.object(adapter.subprocess, "Popen", side_effect=FileNotFoundError):
            job = self.start(character="missing")
            self.assertEqual(self.wait(job)["error"]["code"], "ENGINE_UNAVAILABLE")

    def test_timeout_and_shutdown_reap_children(self):
        self.bridge.timeout = 0.15
        job = self.start(params={"harness": "hang"})
        self.assertEqual(self.wait(job)["error"]["code"], "ENGINE_TIMEOUT")
        job.thread.join(2)
        self.assertFalse(job.thread.is_alive())
        self.assertIsNotNone(job.process.poll())
        self.bridge.timeout = 3
        job = self.start("memorize", character="shutdown")
        self.wait(job, "callback")
        began = time.monotonic()
        self.bridge.close()
        self.assertLess(time.monotonic() - began, 2)
        self.assertEqual(job.snapshot()["error"]["code"], "ADAPTER_SHUTDOWN")
        self.assertIsNotNone(job.process.poll())
        self.assertFalse(job.thread.is_alive())
        with self.assertRaises(adapter.Problem):
            self.start()

    def test_active_and_retention_bounds(self):
        with patch.object(adapter, "MAX_ACTIVE", 1):
            job = self.start("memorize")
            self.wait(job, "callback")
            with self.assertRaises(adapter.Problem) as error:
                self.start(character="other")
            self.assertEqual(error.exception.code, "JOB_CAPACITY")
        job.cancel()
        self.wait(job)
        job.thread.join(2)
        with patch.object(adapter, "MAX_JOBS", 1):
            self.assertIs(self.bridge.get(job.id), job)  # Polling never capacity-evicts.
            replacement = self.start(character="other")
            self.assertEqual(len(self.bridge.jobs), 1)
            self.wait(replacement)
            replacement.thread.join(2)
        with patch.object(adapter, "RETENTION_SECONDS", 0):
            with self.assertRaises(adapter.Problem):
                self.bridge.get(replacement.id)

    def test_environment_defaults_and_overrides(self):
        with patch.dict(os.environ, {"REVERIE_ENGINE": "/authored/engine", "REVERIE_DATA": "/authored/data"}):
            bridge = adapter.Bridge()
        self.assertEqual(bridge.executable, "/authored/engine")
        self.assertEqual(bridge.data, Path("/authored/data"))


if __name__ == "__main__":
    if "--harness" in sys.argv:
        harness()
    else:
        unittest.main()
