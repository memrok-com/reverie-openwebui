"""Private Reverie stdio bridge; stdlib only, no generated episode data.

Run: python3 -B adapter.py
REVERIE_ENGINE defaults to /probe/reverie-engine; REVERIE_DATA to /data.
Each job uses a separate engine (including status, which reads persisted state).
Status therefore does not expose another process's in-flight operation field.
Completed jobs expire after five minutes and may be evicted earlier at capacity.
"""

import collections
import hmac
import json
import math
import os
import re
import selectors
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MAX_BODY = 1024 * 1024
MAX_FRAME = 1024 * 1024
MAX_JOBS = 128
MAX_ACTIVE = 16
JOB_SECONDS = 900
RETENTION_SECONDS = 300
METHODS = {"ping", "when", "retrieve", "inspect", "memorize", "status", "forget"}
CHARACTER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z", re.ASCII)


class Problem(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Non-finite JSON number")

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number")
        return result

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def valid_error(value):
    return (isinstance(value, dict) and set(value) == {"code", "message"}
            and isinstance(value["code"], str) and isinstance(value["message"], str))


class Job:
    def __init__(self, character, method, params):
        self.id = uuid.uuid4().hex
        self.character, self.method = character, method
        self.lock = threading.Lock()
        self.view = {"state": "pending"}
        self.callback = None
        self.cancelling = False
        self.stop = threading.Event()
        self.outgoing = collections.deque([
            ("request", encode({"id": 1, "method": method, "params": params}) + b"\n")])
        self.finished = None
        self.thread = None
        self.process = None

    def snapshot(self):
        with self.lock:
            return self.view

    def reply(self, value):
        if (not isinstance(value, dict)
                or set(value) not in ({"callId", "result"}, {"callId", "error"})
                or not isinstance(value["callId"], str)
                or ("error" in value and not valid_error(value["error"]))):
            raise Problem(400, "INVALID_CALLBACK", "Expected exactly callId and result or error.")
        with self.lock:
            if self.cancelling or self.callback is None or value["callId"] != self.callback["callId"]:
                raise Problem(409, "STALE_CALLBACK", "No matching outstanding callback.")
            if len(self.outgoing) >= 4:
                raise Problem(503, "PIPE_CAPACITY", "The engine input queue is full.")
            self.outgoing.append(("callback", encode(value) + b"\n"))
            self.callback = None
            self.view = {"state": "pending"}

    def cancel(self):
        with self.lock:
            if self.view["state"] != "done" and not self.cancelling:
                self.cancelling = True
                self.callback = None
                self.view = {"state": "pending"}
                # Discard replies not yet sent. Preserve the initial request so
                # even an immediate cancel follows the engine's reservation.
                self.outgoing = collections.deque(item for item in self.outgoing if item[0] == "request")
                self.outgoing.append(("cancel", encode({"id": 2, "method": "cancel", "params": {}}) + b"\n"))

    def frame(self, value):
        if not isinstance(value, dict):
            raise ValueError("Engine frame must be an object")
        with self.lock:
            if set(value) == {"cancelCall"} and isinstance(value["cancelCall"], str):
                if self.callback and self.callback["callId"] == value["cancelCall"]:
                    self.callback = None
                    self.view = {"state": "pending"}
                return False
            if "callId" in value:
                if (set(value) != {"callId", "method", "params"}
                        or not isinstance(value["callId"], str)
                        or value["method"] not in ("source.read", "model.reflect")
                        or not isinstance(value["params"], dict) or self.callback is not None):
                    raise ValueError("Invalid or overlapping engine callback")
                if not self.cancelling:
                    self.callback = value
                    self.view = {"state": "callback", "callback": value}
                return False
            if type(value.get("id")) is int and value["id"] == 2:
                return False  # Cancellation acknowledgement is not the main outcome.
            if (type(value.get("id")) is not int or value["id"] != 1
                    or set(value) not in ({"id", "result"}, {"id", "error"})
                    or ("error" in value and not valid_error(value["error"]))):
                raise ValueError("Invalid engine response")
            self.callback = None
            self.view = {"state": "done", **{k: v for k, v in value.items() if k != "id"}}
            return True

    def fail(self, code, message):
        with self.lock:
            if self.view["state"] != "done":
                self.callback = None
                self.view = {"state": "done", "error": {"code": code, "message": message}}

    def run(self, executable, data, timeout):
        proc = None
        try:
            data.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                [executable, "--stdio", "--character", self.character,
                 "--database", str(data / (self.character + ".sqlite"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0, close_fds=True,
                env={key: value for key, value in os.environ.items() if key != "REVERIE_ADAPTER_TOKEN"})
            self.process = proc
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
            deadline = time.monotonic() + timeout
            incoming, outgoing = b"", b""
            kind, sent = None, False
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                writing = False
                while True:
                    if self.stop.is_set():
                        self.fail("ADAPTER_SHUTDOWN", "The adapter is shutting down.")
                        break
                    if time.monotonic() >= deadline:
                        self.fail("ENGINE_TIMEOUT", "The engine job exceeded its time limit.")
                        break
                    with self.lock:
                        if not outgoing and self.outgoing:
                            kind, outgoing = self.outgoing.popleft()
                            sent = False
                    if outgoing and not writing:
                        selector.register(proc.stdin, selectors.EVENT_WRITE)
                        writing = True
                    if not outgoing and writing:
                        selector.unregister(proc.stdin)
                        writing = False
                    done = False
                    for key, _ in selector.select(0.02):
                        if key.fileobj is proc.stdin:
                            try:
                                with self.lock:
                                    if self.cancelling and kind == "callback" and not sent:
                                        outgoing = b""
                                    else:
                                        # A partial JSON line must finish before cancel.
                                        # The engine owns the atomic publication check.
                                        count = os.write(proc.stdin.fileno(), outgoing)
                                        sent = sent or count > 0
                                        outgoing = outgoing[count:]
                            except BlockingIOError:
                                pass
                            continue
                        chunk = os.read(proc.stdout.fileno(), 65536)
                        if not chunk:
                            self.fail("ENGINE_DIED", "The engine closed stdout before returning a result.")
                            done = True
                            break
                        incoming += chunk
                        while b"\n" in incoming:
                            line, incoming = incoming.split(b"\n", 1)
                            if len(line) > MAX_FRAME:
                                raise ValueError("Engine frame exceeds limit")
                            if self.frame(decode(line)):
                                done = True
                                break
                        if len(incoming) > MAX_FRAME:
                            raise ValueError("Engine frame exceeds limit")
                        if done:
                            break
                    if done:
                        break
        except OSError:
            self.fail("ENGINE_UNAVAILABLE" if proc is None else "ENGINE_IO_ERROR",
                      "Cannot start engine." if proc is None else "Engine pipe failed.")
        except (ValueError, TypeError, RecursionError):
            self.fail("ENGINE_PROTOCOL_ERROR", "The engine emitted an invalid or oversized frame.")
        finally:
            if proc is not None:
                # EOF requests normal engine shutdown; escalation bounds teardown.
                proc.stdin.close()
                try:
                    proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                proc.stdout.close()
            with self.lock:
                self.outgoing.clear()
                self.finished = time.monotonic()


class Bridge:
    def __init__(self, executable=None, data=None, timeout=JOB_SECONDS):
        self.executable = executable or os.environ.get("REVERIE_ENGINE", "/probe/reverie-engine")
        self.data = Path(data or os.environ.get("REVERIE_DATA", "/data"))
        self.timeout = timeout
        self.lock = threading.Lock()
        self.jobs = {}
        self.closed = False

    def start(self, value):
        if (not isinstance(value, dict) or set(value) != {"character", "method", "params"}
                or not isinstance(value["character"], str) or not CHARACTER.fullmatch(value["character"])
                or not isinstance(value["method"], str) or value["method"] not in METHODS
                or not isinstance(value["params"], dict)):
            raise Problem(400, "INVALID_JOB", "Expected safe ASCII character, supported method and params object.")
        with self.lock:
            self.prune(evict=True)
            if self.closed:
                raise Problem(503, "ADAPTER_SHUTDOWN", "The adapter is shutting down.")
            active = [job for job in self.jobs.values() if job.finished is None]
            # Status may run beside an ordinary job; one status per character is bounded too.
            if any(job.character == value["character"] and
                   (job.method == "status") == (value["method"] == "status") for job in active):
                raise Problem(409, "CHARACTER_BUSY", "A job of this kind is already running for this character.")
            if len(active) >= MAX_ACTIVE or len(self.jobs) >= MAX_JOBS:
                raise Problem(503, "JOB_CAPACITY", "The adapter job limit is reached.")
            job = Job(value["character"], value["method"], value["params"])
            job.thread = threading.Thread(target=job.run, args=(self.executable, self.data, self.timeout))
            self.jobs[job.id] = job
            job.thread.start()
            return {"job": job.id}

    def prune(self, evict=False):
        now = time.monotonic()
        for identity, job in list(self.jobs.items()):
            if job.finished is not None and now - job.finished >= RETENTION_SECONDS:
                del self.jobs[identity]
        if evict and len(self.jobs) >= MAX_JOBS:
            for identity, job in list(self.jobs.items()):
                if job.finished is not None:
                    del self.jobs[identity]
                    break

    def get(self, identity):
        with self.lock:
            self.prune()
            job = self.jobs.get(identity)
            if job is None:
                raise Problem(404, "JOB_NOT_FOUND", "Unknown or expired job.")
            return job

    def close(self):
        with self.lock:
            self.closed = True
            jobs = list(self.jobs.values())
            for job in jobs:
                job.stop.set()
        for job in jobs:
            job.thread.join()


class Server(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, address, bridge):
        token = os.environ.get("REVERIE_ADAPTER_TOKEN", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", token, re.ASCII):
            raise ValueError("REVERIE_ADAPTER_TOKEN must be 32+ URL-safe ASCII characters.")
        self.authorization = ("Bearer " + token).encode("ascii")
        self.bridge = bridge
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        request.settimeout(5)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log callback/source contents.

    def body(self):
        sizes = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,10}", sizes[0]):
            raise Problem(400, "INVALID_BODY", "One Content-Length is required; transfer encoding is unsupported.")
        size = int(sizes[0])
        if size > MAX_BODY:
            raise Problem(413, "BODY_TOO_LARGE", "Request body exceeds limit.")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise Problem(400, "INVALID_BODY", "Incomplete request body.")
        try:
            return decode(raw)
        except (ValueError, RecursionError):
            raise Problem(400, "INVALID_JSON", "Expected valid UTF-8 JSON.")

    def respond(self, status, value):
        raw = encode(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def dispatch(self):
        parts = self.path.split("/")
        if self.command == "GET" and self.path == "/health":
            return {"ok": True}
        credentials = self.headers.get_all("Authorization", [])
        supplied = credentials[0].encode("utf-8") if len(credentials) == 1 else b""
        if not hmac.compare_digest(supplied, self.server.authorization):
            raise Problem(401, "UNAUTHORIZED", "Adapter authentication required.")
        if self.command == "POST" and self.path == "/jobs":
            return self.server.bridge.start(self.body())
        if len(parts) in (3, 4) and parts[1] == "jobs" and re.fullmatch(r"[0-9a-f]{32}", parts[2]):
            job = self.server.bridge.get(parts[2])
            if self.command == "GET" and len(parts) == 3:
                return job.snapshot()
            if self.command == "POST" and len(parts) == 4:
                if parts[3] == "callback":
                    job.reply(self.body())
                    return {"ok": True}
                if parts[3] == "cancel":
                    job.cancel()
                    return {"ok": True}
        raise Problem(404, "NOT_FOUND", "Unknown endpoint.")

    def handle_request(self):
        try:
            value = self.dispatch()
            self.respond(200, value)
        except Problem as error:
            self.respond(error.status, {"error": {"code": error.code, "message": error.message}})
        except (TimeoutError, ConnectionError):
            self.close_connection = True

    do_GET = handle_request
    do_POST = handle_request


def main():
    bridge = Bridge()
    try:
        server = Server(("0.0.0.0", 8766), bridge)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    stopping = threading.Event()

    def stop(signum, frame):
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        bridge.close()
        server.server_close()


if __name__ == "__main__":
    main()
