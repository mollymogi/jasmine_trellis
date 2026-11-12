"""Loopback allowlist in front of Core."""

from __future__ import annotations

import argparse
import json
import os
import re
import socketserver
import stat
import sys
import threading
import time
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import ids
from .canonical import looks_like_credential

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 18788
#: 384 KiB. Above models.MAX_TEXT_BYTES so a wrapped event still fits; over this
#: the handler answers and closes without reading the rest.
MAX_BODY_BYTES = 384 * 1024
SECRET_NAME = "tailnet-gateway.key"
GATEWAY_HEADER = "X-Jasmine-Gateway"
_ALLOW: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/v1/health"),
    ("POST", "/v1/workspaces/resolve"),
    ("POST", "/v1/sessions"),
    ("POST", "/v1/events"),
    ("POST", "/v1/session-checkpoints"),
    ("POST", "/v1/session-checkpoints/recover"),
    ("POST", "/v1/memory/hint"),
    ("POST", "/v1/continuity/recent"),
})
_TEMPLATE_RE = re.compile(r"/v1(?:/[a-z0-9][a-z0-9-]*)+")
_ENCODED_BREAK_RE = re.compile(r"%0[0ad]", re.IGNORECASE)
_JSON_TYPE = "application/json; charset=utf-8"


class GatewayStartupError(Exception):
    """Startup refused. Messages never include the secret or request bodies."""


def secret_path(data_dir: Path) -> Path:
    return Path(data_dir) / "config" / "credentials" / SECRET_NAME


def _require_private_secret(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise GatewayStartupError("gateway secret must be a regular file")
    if info.st_uid != os.getuid():
        raise GatewayStartupError("gateway secret has the wrong owner")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise GatewayStartupError("gateway secret mode must be 0600")


def load_gateway_secret(data_dir: Path) -> str:
    """Read the secret once. Missing, wrong owner, or mode other than 0600 fails."""
    path = secret_path(data_dir)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise GatewayStartupError("gateway secret is missing or not readable") from exc
    try:
        info = os.fstat(descriptor)
        _require_private_secret(info)
        if info.st_size > 4096:
            raise GatewayStartupError("gateway secret is too large")
        raw = os.read(descriptor, 4096)
    finally:
        os.close(descriptor)
    if b"\n" in raw[:-1] or b"\r" in raw or b"\x00" in raw:
        raise GatewayStartupError("gateway secret must be a single line")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GatewayStartupError("gateway secret must be a single line") from exc
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise GatewayStartupError("gateway secret is empty")
    try:
        text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise GatewayStartupError("gateway secret must be latin-1") from exc
    return text


def parse_upstream(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if (
        parts.scheme != "http"
        or parts.hostname != GATEWAY_HOST
        or not parts.port
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise GatewayStartupError("upstream must be http://127.0.0.1:port")
    return GATEWAY_HOST, parts.port


def path_template(path: str) -> str:
    """Allowlisted paths log as themselves. Anything else stays a fixed template.

    The query is never part of the template: it does not widen the allowlist,
    and it is where a git origin or token would otherwise land in the log.
    """
    if not _TEMPLATE_RE.fullmatch(path) or looks_like_credential(path):
        return "unlisted"
    return path


def _host_id_of(body: bytes) -> str | None:
    if not body or len(body) > MAX_BODY_BYTES:
        return None
    try:
        parsed = json.loads(body)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("host_id")
    if ids.is_id(value, "hst"):
        return str(value)
    return None


def _open_access_log(path: Path) -> int:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise GatewayStartupError("access log is not a writable regular file") from exc
    os.fchmod(descriptor, 0o600)
    return descriptor


class _GatewayServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        host = self.server_address[0]
        if host != GATEWAY_HOST:
            raise GatewayStartupError(f"refusing to bind {host}")
        # Skip BaseHTTPRequestHandler's reverse-DNS lookup; the bind address is fixed.
        socketserver.TCPServer.server_bind(self)
        self.server_name = GATEWAY_HOST
        self.server_port = self.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jasmine-gateway"
    sys_version = ""

    def handle_one_request(self) -> None:
        self.connection.settimeout(30)
        super().handle_one_request()

    def handle_expect_100(self) -> bool:
        # A 100 Continue tells the client to send the body. Skip it when that
        # body will be refused unread, or the 413 blocks on a full window.
        raw = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding"):
            return True
        if raw is not None and (not raw.isdigit() or len(raw) > 10 or int(raw) > MAX_BODY_BYTES):
            return True
        self.send_response_only(100)
        self.end_headers()
        return True

    def do_GET(self) -> None:  # noqa: N802
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy("OPTIONS")

    def do_TRACE(self) -> None:  # noqa: N802
        self._proxy("TRACE")

    def log_message(self, fmt: str, *args: Any) -> None:
        # The request target can carry a token or git origin. The access log is
        # the only request record, and it stores a path template instead.
        return

    def _proxy(self, method: str) -> None:
        started = time.monotonic()
        raw_target = self.path or ""
        if (
            "\r" in raw_target
            or "\n" in raw_target
            or "\x00" in raw_target
            or _ENCODED_BREAK_RE.search(raw_target)
        ):
            self._finish(400, "bad_target", b"", started)
            return
        parts = urlsplit(raw_target)
        if parts.scheme or parts.netloc:
            self._finish(400, "bad_target", b"", started)
            return
        length, problem = self._declared_length()
        if problem == "body_too_large":
            self._finish(413, "body_too_large", b"", started)
            return
        if problem:
            self._finish(400, problem, b"", started)
            return
        body = self.rfile.read(length) if length else b""
        if (method, parts.path) not in _ALLOW:
            self._finish(403, "not_allowlisted", body, started)
            return
        forward = parts.path if not parts.query else f"{parts.path}?{parts.query}"
        self._forward(method, forward, body, started)

    def _declared_length(self) -> tuple[int, str | None]:
        if self.headers.get("Transfer-Encoding"):
            return 0, "length_required"
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0, None
        # Refuse before any body read. A long digit string is not turned into an int
        # and is not treated as a reason to keep reading.
        if not raw.isdigit() or len(raw) > 10:
            return 0, "bad_content_length"
        length = int(raw)
        if length > MAX_BODY_BYTES:
            return 0, "body_too_large"
        return length, None

    def _forward(self, method: str, target: str, body: bytes, started: float) -> None:
        server: _GatewayServer = self.server  # type: ignore[assignment]
        host, port = server.upstream  # type: ignore[attr-defined]
        secret: str = server.gateway_secret  # type: ignore[attr-defined]
        headers = {
            GATEWAY_HEADER: secret,
            "Content-Length": str(len(body)),
        }
        authorization = self.headers.get("Authorization")
        if authorization is not None:
            if "\r" in authorization or "\n" in authorization or "\x00" in authorization:
                self._finish(400, "bad_authorization", body, started)
                return
            headers["Authorization"] = authorization
        content_type = self.headers.get("Content-Type")
        if content_type and "\r" not in content_type and "\n" not in content_type:
            headers["Content-Type"] = content_type
        connection = HTTPConnection(host, port, timeout=30)
        try:
            try:
                connection.request(method, target, body=body if body else None, headers=headers)
            except (UnicodeEncodeError, ValueError):
                self._finish(400, "bad_request", body, started)
                return
            try:
                upstream = connection.getresponse()
                payload = upstream.read()
                status = upstream.status
                response_headers: dict[str, str] = {}
                upstream_type = upstream.getheader("Content-Type")
                if upstream_type and "\r" not in upstream_type and "\n" not in upstream_type:
                    response_headers["Content-Type"] = upstream_type
                request_id = upstream.getheader("X-Request-Id")
                if (
                    request_id
                    and len(request_id) <= 128
                    and "\r" not in request_id
                    and "\n" not in request_id
                ):
                    response_headers["X-Request-Id"] = request_id
            except (OSError, HTTPException):
                self._finish(502, "upstream_unavailable", body, started)
                return
        finally:
            connection.close()
        self._send(status, payload, response_headers)
        self._access(method, path_template(urlsplit(target).path), status, started, None, _host_id_of(body))

    def _finish(self, status: int, reason: str, body: bytes, started: float) -> None:
        payload = json.dumps(
            {"error": {"code": reason, "message": reason}},
            separators=(",", ":"),
        ).encode("ascii")
        self._send(status, payload, {"Content-Type": _JSON_TYPE}, close=True)
        self._access(
            self.command,
            path_template(urlsplit(self.path or "").path),
            status,
            started,
            reason,
            _host_id_of(body),
        )

    def _send(self, status: int, payload: bytes, headers: dict[str, str], *, close: bool = True) -> None:
        self.close_connection = close
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        if close:
            self.send_header("Connection", "close")
        for key, value in headers.items():
            if key.lower() in ("content-length", "connection", "transfer-encoding", GATEWAY_HEADER.lower()):
                continue
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
        self.wfile.flush()

    def _access(
        self,
        method: str,
        template: str,
        status: int,
        started: float,
        reason: str | None,
        host_id: str | None,
    ) -> None:
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        fields = [
            f"ts={_utc_now()}",
            f"method={method}",
            f"path={template}",
            f"status={status}",
            f"latency_ms={latency_ms}",
        ]
        if reason:
            fields.append(f"reason={reason}")
        if host_id:
            fields.append(f"host_id={host_id}")
        line = " ".join(fields) + "\n"
        server: _GatewayServer = self.server  # type: ignore[assignment]
        secret: str = server.gateway_secret  # type: ignore[attr-defined]
        if secret and secret in line:
            line = line.replace(secret, "[REDACTED]")
        encoded = line.encode("ascii", "replace")
        with server.access_log_lock:  # type: ignore[attr-defined]
            os.write(server.access_log_fd, encoded)  # type: ignore[attr-defined]


def _utc_now() -> str:
    seconds = time.time()
    millis = int(seconds * 1000) % 1000
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{millis:03d}Z"


def open_gateway(
    data_dir: str | Path,
    upstream: str,
    *,
    host: str = GATEWAY_HOST,
    port: int = GATEWAY_PORT,
    access_log: str | Path | None = None,
) -> _GatewayServer:
    """Bind the allowlist proxy. Raises GatewayStartupError before listening on failure."""
    if host != GATEWAY_HOST:
        raise GatewayStartupError(f"refusing to bind {host}")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise GatewayStartupError("refusing to bind that port")
    root = Path(data_dir)
    secret = load_gateway_secret(root)
    upstream_address = parse_upstream(upstream)
    log_path = Path(access_log) if access_log is not None else root / "logs" / "tailnet-gateway.access.log"
    log_fd = _open_access_log(log_path)
    try:
        server = _GatewayServer((host, port), _Handler)
    except GatewayStartupError:
        os.close(log_fd)
        raise
    except OSError:
        os.close(log_fd)
        raise
    server.gateway_secret = secret  # type: ignore[attr-defined]
    server.upstream = upstream_address  # type: ignore[attr-defined]
    server.access_log_fd = log_fd  # type: ignore[attr-defined]
    server.access_log_lock = threading.Lock()  # type: ignore[attr-defined]
    return server


def serve(
    data_dir: str | Path,
    upstream: str,
    *,
    host: str = GATEWAY_HOST,
    port: int = GATEWAY_PORT,
    access_log: str | Path | None = None,
) -> None:
    """Run until interrupted. Blocks the calling thread."""
    server = open_gateway(data_dir, upstream, host=host, port=port, access_log=access_log)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return
    finally:
        _close_gateway(server)


def _close_gateway(server: _GatewayServer) -> None:
    descriptor = getattr(server, "access_log_fd", None)
    if descriptor is not None:
        os.close(descriptor)
        server.access_log_fd = None  # type: ignore[attr-defined]
    server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jasmine-tailnet-gateway")
    parser.add_argument("--data-dir", required=True, help="Core data directory (holds config/credentials)")
    parser.add_argument("--upstream", default=f"http://{GATEWAY_HOST}:18787")
    parser.add_argument("--host", default=GATEWAY_HOST)
    parser.add_argument("--port", type=int, default=GATEWAY_PORT)
    parser.add_argument("--access-log", default=None)
    args = parser.parse_args(argv)
    try:
        serve(
            args.data_dir,
            args.upstream,
            host=args.host,
            port=args.port,
            access_log=args.access_log,
        )
    except GatewayStartupError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc


if __name__ == "__main__":
    main()
