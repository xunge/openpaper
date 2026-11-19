#!/usr/bin/env python3
"""Idle-aware gateway in front of MinerU.

MinerU's api-server has no vLLM sleep endpoint. The only reliable way to return
its VRAM is to stop the API container. This gateway:

* forwards real MinerU requests to the backend containers;
* tracks real request activity (readiness probes do not count);
* after MINERU_IDLE_SECONDS with no requests and no in-flight work, stops
  `mineru-api` with Docker's API;
* on the next real request starts it again, waits for `/v1/health`, then proxies.

The file-parse container stays running: it does not hold the vLLM GPU, and it is
the entry point OpenPaper uses. Its internal call to mineru-api happens only
after the gateway has woken the API.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
API_CONTAINER = os.getenv("MINERU_API_CONTAINER", "mineru-api")
API_PORT = int(os.getenv("MINERU_GATEWAY_API_PORT", "18000"))
FILE_PARSE_PORT = int(os.getenv("MINERU_GATEWAY_FILE_PARSE_PORT", "18003"))
API_UPSTREAM = ("127.0.0.1", int(os.getenv("MINERU_API_UPSTREAM_PORT", "8000")))
FILE_PARSE_UPSTREAM = (
    "127.0.0.1",
    int(os.getenv("MINERU_FILE_PARSE_UPSTREAM_PORT", "8003")),
)
IDLE_SECONDS = float(os.getenv("MINERU_IDLE_SECONDS", "600"))
CHECK_INTERVAL = float(os.getenv("MINERU_IDLE_CHECK_INTERVAL", "15"))
WAKE_TIMEOUT = float(os.getenv("MINERU_WAKE_TIMEOUT_SECONDS", "600"))
FORWARD_TIMEOUT = float(os.getenv("MINERU_FORWARD_TIMEOUT_SECONDS", "1800"))
WARMUP_PDF = pathlib.Path(os.getenv("MINERU_WARMUP_PDF", "/app/warmup.pdf"))
WARMUP_ENABLED = os.getenv("MINERU_WARMUP", "1") not in {"0", "false", "False"}

_READY_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/",
    "/openapi",
    "/v1/health",
    "/health",
}

_lock = threading.Lock()
_wake_lock = threading.Lock()
_state = {
    "in_flight": 0,
    "last_active": time.monotonic(),
    "last_wake": 0.0,
    "sleeping": False,
    "api_running": True,
}


def _log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 30.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def _docker_request(method: str, path: str) -> tuple[int, bytes]:
    connection = UnixHTTPConnection(DOCKER_SOCKET, timeout=30)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def container_running() -> bool:
    status, payload = _docker_request("GET", f"/containers/{API_CONTAINER}/json")
    if status == 404:
        raise RuntimeError(f"Docker container not found: {API_CONTAINER}")
    if status != 200:
        raise RuntimeError(f"Docker inspect failed: HTTP {status}")
    data = json.loads(payload.decode("utf-8"))
    running = bool(data.get("State", {}).get("Running"))
    with _lock:
        _state["api_running"] = running
        _state["sleeping"] = not running
    return running


def start_api() -> None:
    if container_running():
        return
    _log(f"starting {API_CONTAINER} (cold start; waiting for /v1/health)")
    status, payload = _docker_request("POST", f"/containers/{API_CONTAINER}/start")
    if status not in (204, 304):
        raise RuntimeError(
            f"docker start {API_CONTAINER} failed: HTTP {status} "
            f"{payload[:300].decode('utf-8', 'replace')}"
        )
    deadline = time.monotonic() + WAKE_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{API_UPSTREAM[1]}/v1/health", timeout=5
            ) as response:
                if response.status == 200:
                    with _lock:
                        _state["api_running"] = True
                        _state["sleeping"] = False
                        _state["last_wake"] = time.monotonic()
                    _log(f"{API_CONTAINER} is awake")
                    warmup_api()
                    return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"{API_CONTAINER} did not become healthy in {WAKE_TIMEOUT:.0f}s")


def warmup_api() -> None:
    """Make the first parse initialize vLLM before forwarding the real request.

    `/v1/health` turns green before the lazy vLLM engine is built. A one-page
    warmup through file-parse absorbs that initialization; without it the first
    real file_parse can return "Engine core initialization failed".
    """
    if not WARMUP_ENABLED or not WARMUP_PDF.is_file():
        return
    payload = WARMUP_PDF.read_bytes()
    boundary = "----mineru-gateway-warmup"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="warmup.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    last_error = "unknown error"
    for attempt in range(1, 4):
        connection = http.client.HTTPConnection(
            *FILE_PARSE_UPSTREAM, timeout=FORWARD_TIMEOUT
        )
        try:
            connection.request(
                "POST", "/v1/file_parse", body=body, headers=headers
            )
            response = connection.getresponse()
            response.read()
            if response.status in (200, 202):
                _log(f"warmup parse ready after {attempt} attempt(s)")
                return
            last_error = f"HTTP {response.status}"
        except Exception as exc:
            last_error = str(exc)
        finally:
            connection.close()
        time.sleep(10)
    raise RuntimeError(f"MinerU warmup failed: {last_error}")


def ensure_awake() -> None:
    # Serialize cold starts so concurrent OCR requests do not start it twice.
    with _wake_lock:
        start_api()


def stop_api() -> None:
    if not container_running():
        return
    _log(f"idle for {IDLE_SECONDS:.0f}s; stopping {API_CONTAINER} to release VRAM")
    status, payload = _docker_request("POST", f"/containers/{API_CONTAINER}/stop?t=30")
    if status not in (204, 304):
        raise RuntimeError(
            f"docker stop {API_CONTAINER} failed: HTTP {status} "
            f"{payload[:300].decode('utf-8', 'replace')}"
        )
    with _lock:
        _state["api_running"] = False
        _state["sleeping"] = True


def idle_monitor() -> None:
    while True:
        time.sleep(CHECK_INTERVAL)
        with _lock:
            idle_for = time.monotonic() - _state["last_active"]
            busy = _state["in_flight"] > 0
            sleeping = _state["sleeping"]
        if busy or sleeping or idle_for < IDLE_SECONDS:
            continue
        # Serialize with wake/forward: a request that arrives between the
        # idle check and the stop waits on _wake_lock, then starts the API.
        with _wake_lock:
            with _lock:
                if _state["in_flight"] > 0:
                    continue
                if time.monotonic() - _state["last_active"] < IDLE_SECONDS:
                    continue
                _state["in_flight"] = 1  # reserve; prevents a concurrent stop
            try:
                stop_api()
            finally:
                with _lock:
                    _state["in_flight"] = 0


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class GatewayHandler(BaseHTTPRequestHandler):
    backend: tuple[str, int] = API_UPSTREAM
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        _log(f"{self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def _readiness(self) -> bool:
        path = self.path.split("?", 1)[0]
        if path == "/openapi.json":
            _json_response(
                self,
                {
                    "openapi": "3.0.0",
                    "info": {"title": "MinerU idle gateway", "version": "1.0"},
                    "paths": {},
                },
            )
            return True
        if path in ("/docs", "/docs/"):
            body = b"MinerU idle gateway. The OCR engine is started on demand."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return True
        if path in ("/v1/health", "/health"):
            with _lock:
                payload = {
                    "status": "ok",
                    "api_running": _state["api_running"],
                    "sleeping": _state["sleeping"],
                    "in_flight": _state["in_flight"],
                    "idle_seconds": round(
                        time.monotonic() - _state["last_active"], 1
                    ),
                }
            _json_response(self, payload)
            return True
        return False

    def _handle(self) -> None:
        if self._readiness():
            return

        with _lock:
            _state["in_flight"] += 1
        try:
            ensure_awake()
            self._forward()
        except Exception as exc:
            _log(f"proxy error: {exc}")
            try:
                _json_response(self, {"detail": str(exc)}, status=502)
            except Exception:
                pass
        finally:
            with _lock:
                _state["in_flight"] = max(0, _state["in_flight"] - 1)
                _state["last_active"] = time.monotonic()

    def _forward(self) -> None:
        length_header = self.headers.get("Content-Length")
        length = int(length_header) if length_header else 0
        body = self.rfile.read(length) if length else b""
        connection = http.client.HTTPConnection(*self.backend, timeout=FORWARD_TIMEOUT)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        if length:
            headers["Content-Length"] = str(length)
        try:
            connection.request(
                self.command,
                self.path,
                body=body if body else None,
                headers=headers,
            )
            response = connection.getresponse()
            response_body = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in {"transfer-encoding", "connection", "content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response_body)
        finally:
            connection.close()


class FileParseHandler(GatewayHandler):
    backend = FILE_PARSE_UPSTREAM


def main() -> None:
    _log(
        f"starting gateway: API :{API_PORT} -> {API_UPSTREAM[0]}:{API_UPSTREAM[1]}, "
        f"file-parse :{FILE_PARSE_PORT} -> {FILE_PARSE_UPSTREAM[0]}:{FILE_PARSE_UPSTREAM[1]}, "
        f"idle {IDLE_SECONDS:.0f}s"
    )
    try:
        container_running()
    except Exception as exc:
        _log(f"initial Docker check failed: {exc}")
    threading.Thread(target=idle_monitor, daemon=True).start()
    api_server = ThreadingHTTPServer(("0.0.0.0", API_PORT), GatewayHandler)
    parse_server = ThreadingHTTPServer(("0.0.0.0", FILE_PARSE_PORT), FileParseHandler)
    threading.Thread(target=api_server.serve_forever, daemon=True).start()
    parse_server.serve_forever()


if __name__ == "__main__":
    main()
