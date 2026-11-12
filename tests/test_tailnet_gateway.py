"""Loopback allowlist gateway in front of a stub upstream, not a live Core."""

from __future__ import annotations

import json
import os
import plistlib
import socket
import stat
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jasmine_core import ids
from jasmine_core.tailnet_gateway import (
    GATEWAY_HOST,
    GATEWAY_PORT,
    MAX_BODY_BYTES,
    _GatewayServer,
    _Handler,
    _require_private_secret,
    load_gateway_secret,
    open_gateway,
    secret_path,
)

SECRET = "gateway-test-secret"
ORIGIN = "https://github.com/acme/secret-repo.git"
CALLER = "Bearer caller-token"
FORGED = "forged-gateway-header"
PLIST = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "deployment"
    / "com.jasminecore.v1.local.tailnet-gateway.plist"
)


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self._take()

    def do_POST(self) -> None:  # noqa: N802
        self._take()

    def _take(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append({  # type: ignore[attr-defined]
            "method": self.command,
            "target": self.path,
            "gateway": self.headers.get("X-Jasmine-Gateway"),
            "authorization": self.headers.get("Authorization"),
            "body": body,
        })
        if self.path.split("?", 1)[0] == "/v1/workspaces/resolve":
            status, payload = 404, b'{"error":{"code":"not_found"}}'
        else:
            status, payload = 200, b'{"status":"ok"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Request-Id", "req-stub")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class GatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._tmp())
        self.data = self.tmp / "data"
        self.log = self.tmp / "gateway.access.log"
        write_secret(self.data, SECRET)
        self.stub = ThreadingHTTPServer((GATEWAY_HOST, 0), _StubHandler)
        self.stub.seen = []  # type: ignore[attr-defined]
        self.stub_thread = threading.Thread(
            target=self.stub.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        self.stub_thread.start()
        upstream = f"http://{GATEWAY_HOST}:{self.stub.server_address[1]}"
        self.server = open_gateway(self.data, upstream, port=0, access_log=self.log)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        descriptor = getattr(self.server, "access_log_fd", None)
        if descriptor is not None:
            os.close(descriptor)
            self.server.access_log_fd = None  # type: ignore[attr-defined]
        self.server.server_close()
        self.stub.shutdown()
        self.stub_thread.join(timeout=2)
        self.stub.server_close()
        os.chmod(self.tmp, 0o700)
        for child in sorted(self.tmp.rglob("*"), reverse=True):
            if child.is_dir():
                os.chmod(child, 0o700)
        import shutil
        shutil.rmtree(self.tmp)

    def _tmp(self) -> str:
        import tempfile
        return tempfile.mkdtemp(prefix="jasmine-gateway-")

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        target = f"http://{GATEWAY_HOST}:{self.port}{path}"
        req = urllib.request.Request(target, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def log_lines(self) -> list[dict[str, str]]:
        text = self.log.read_text()
        lines = []
        for raw in text.splitlines():
            fields = {}
            for part in raw.split():
                key, value = part.split("=", 1)
                fields[key] = value
            lines.append(fields)
        return lines

    def test_cap_constant_is_384_kib(self) -> None:
        self.assertEqual(MAX_BODY_BYTES, 393216)
        self.assertEqual(GATEWAY_PORT, 18788)

    def test_listening_socket_is_loopback_and_other_addresses_are_refused(self) -> None:
        self.assertEqual(self.server.socket.getsockname()[0], "127.0.0.1")
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.server.socket.family, socket.AF_INET)
        for host in ("0.0.0.0", "localhost", "::1", "10.0.0.1"):
            with self.assertRaises(Exception) as caught:
                open_gateway(self.data, f"http://{GATEWAY_HOST}:9", host=host, port=0, access_log=self.tmp / f"{host}.log")
            self.assertIn("refusing to bind", str(caught.exception))
        with self.assertRaises(Exception) as caught:
            _GatewayServer(("0.0.0.0", 0), _Handler)
        self.assertIn("refusing to bind", str(caught.exception))

    def test_secret_must_exist_be_owned_by_this_user_and_be_mode_0600(self) -> None:
        missing = self.tmp / "missing-data"
        with self.assertRaises(Exception) as caught:
            load_gateway_secret(missing)
        self.assertIn("missing", str(caught.exception))
        path = secret_path(self.data)
        path.chmod(0o644)
        with self.assertRaises(Exception) as caught:
            load_gateway_secret(self.data)
        self.assertIn("0600", str(caught.exception))
        path.chmod(0o400)
        with self.assertRaises(Exception) as caught:
            load_gateway_secret(self.data)
        self.assertIn("0600", str(caught.exception))
        path.chmod(0o600)
        self.assertEqual(load_gateway_secret(self.data), SECRET)
        path.write_bytes("secret-\u20ac\n".encode())
        path.chmod(0o600)
        with self.assertRaises(Exception) as caught:
            load_gateway_secret(self.data)
        self.assertIn("latin-1", str(caught.exception))
        self.assertNotIn("\u20ac", str(caught.exception))
        path.write_bytes((SECRET + "\n").encode())
        path.chmod(0o600)
        info = os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, os.getuid() + 1, os.getgid(), 4, 0, 0, 0))
        with self.assertRaises(Exception) as caught:
            _require_private_secret(info)
        self.assertIn("owner", str(caught.exception))
        link = self.tmp / "link.key"
        other = self.tmp / "other-data"
        write_secret(other, SECRET)
        link.symlink_to(secret_path(other))
        linked = self.tmp / "linked-data" / "config" / "credentials"
        linked.mkdir(parents=True)
        (linked / "tailnet-gateway.key").symlink_to(secret_path(other))
        with self.assertRaises(Exception) as caught:
            load_gateway_secret(linked.parent.parent)
        self.assertIn("missing", str(caught.exception))

    def test_allowlist_forwards_and_strips_client_gateway_header(self) -> None:
        host_id = ids.new_id("hst")
        body = json.dumps({"host_id": host_id, "origin": ORIGIN}).encode()
        status, payload = self.request(
            "POST",
            "/v1/events",
            body,
            {"Authorization": CALLER, "X-Jasmine-Gateway": FORGED, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["status"], "ok")
        seen = self.stub.seen  # type: ignore[attr-defined]
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["gateway"], SECRET)
        self.assertNotEqual(seen[0]["gateway"], FORGED)
        self.assertEqual(seen[0]["authorization"], CALLER)
        self.assertEqual(seen[0]["body"], body)
        line = self.log_lines()[-1]
        self.assertEqual(line["method"], "POST")
        self.assertEqual(line["path"], "/v1/events")
        self.assertEqual(line["status"], "200")
        self.assertEqual(line["host_id"], host_id)
        self.assertNotIn("reason", line)
        self.assertIn("latency_ms", line)
        text = self.log.read_text()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(CALLER, text)
        self.assertNotIn(FORGED, text)
        self.assertNotIn(ORIGIN, text)
        self.assertNotIn("github.com", text)
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o600)

    def test_secret_is_the_value_read_at_startup(self) -> None:
        secret_path(self.data).write_bytes(b"rotated-secret\n")
        secret_path(self.data).chmod(0o600)
        status, _ = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        seen = self.stub.seen  # type: ignore[attr-defined]
        self.assertEqual(seen[-1]["gateway"], SECRET)

    def test_resolve_is_forwarded_when_upstream_returns_404(self) -> None:
        status, payload = self.request("POST", "/v1/workspaces/resolve", b"{}")
        self.assertEqual(status, 404)
        self.assertIn(b"not_found", payload)
        self.assertEqual(self.stub.seen[-1]["target"], "/v1/workspaces/resolve")  # type: ignore[attr-defined]
        self.assertNotIn("reason", self.log_lines()[-1])

    def test_denied_paths_are_not_forwarded(self) -> None:
        host_id = ids.new_id("hst")
        body = json.dumps({"host_id": host_id, "origin": ORIGIN}).encode()
        cases = (
            ("GET", "/v1/meta/schema", None),
            ("POST", "/v1/projects", body),
            ("POST", "/v1/auth/keys", body),
            ("GET", "/v1/events", None),
            ("GET", "/v1/memory/items", None),
            ("POST", "/v1/memory/reflect", body),
            ("GET", "/v1/authority/rules", None),
            ("POST", "/v1/events/extra", body),
            ("POST", "/v1/session-checkpoints/recover/extra", body),
            ("GET", "/v1/health/../health", None),
            ("POST", "/v1/health", body),
            ("GET", "/v1/events?path=/v1/health", None),
        )
        before = len(self.stub.seen)  # type: ignore[attr-defined]
        for method, path, payload in cases:
            status, raw = self.request(method, path, payload)
            self.assertEqual(status, 403, path)
            self.assertEqual(json.loads(raw)["error"]["code"], "not_allowlisted")
        self.assertEqual(len(self.stub.seen), before)  # type: ignore[attr-defined]
        schema = [line for line in self.log_lines() if line["path"] == "/v1/meta/schema"]
        self.assertEqual(schema[-1]["status"], "403")
        self.assertEqual(schema[-1]["reason"], "not_allowlisted")
        projects = [line for line in self.log_lines() if line["path"] == "/v1/projects"]
        self.assertEqual(projects[-1]["host_id"], host_id)
        text = self.log.read_text()
        self.assertNotIn(ORIGIN, text)
        self.assertNotIn("github.com", text)

    def test_query_does_not_widen_the_match_but_exact_paths_do(self) -> None:
        status, _ = self.request("GET", "/v1/health?origin=" + ORIGIN)
        self.assertEqual(status, 200)
        seen = self.stub.seen[-1]  # type: ignore[attr-defined]
        self.assertTrue(seen["target"].startswith("/v1/health?"))
        self.assertNotIn(ORIGIN, self.log.read_text())
        self.assertEqual(self.log_lines()[-1]["path"], "/v1/health")
        for method, path in (
            ("POST", "/v1/sessions"),
            ("POST", "/v1/session-checkpoints"),
            ("POST", "/v1/session-checkpoints/recover"),
            ("POST", "/v1/memory/hint"),
            ("POST", "/v1/continuity/recent"),
        ):
            status, _ = self.request(method, path, b"{}")
            self.assertEqual(status, 200, path)
            self.assertEqual(self.stub.seen[-1]["target"], path)  # type: ignore[attr-defined]

    def test_oversized_body_is_413_and_is_not_read_or_forwarded(self) -> None:
        header = (
            "POST /v1/events HTTP/1.1\r\n"
            f"Host: {GATEWAY_HOST}\r\n"
            f"Content-Length: {MAX_BODY_BYTES + 1}\r\n"
            "Content-Type: application/json\r\n"
            "X-Jasmine-Gateway: forged\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        before = len(self.stub.seen)  # type: ignore[attr-defined]
        with socket.create_connection((GATEWAY_HOST, self.port), timeout=2) as connection:
            connection.sendall(header.encode("ascii"))
            connection.settimeout(2)
            response = bytearray()
            while chunk := connection.recv(4096):
                response.extend(chunk)
        self.assertTrue(response.startswith(b"HTTP/1.1 413 "))
        self.assertEqual(len(self.stub.seen), before)  # type: ignore[attr-defined]
        line = self.log_lines()[-1]
        self.assertEqual(line["status"], "413")
        self.assertEqual(line["reason"], "body_too_large")
        self.assertEqual(line["path"], "/v1/events")
        self.assertNotIn("host_id", line)
        peek = b'{"host_id":"hst_should_not_be_parsed","origin":"' + ORIGIN.encode() + b'"}'
        denied = (
            "POST /v1/meta/schema HTTP/1.1\r\n"
            f"Host: {GATEWAY_HOST}\r\n"
            f"Content-Length: {MAX_BODY_BYTES + 1}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode() + peek
        with socket.create_connection((GATEWAY_HOST, self.port), timeout=2) as connection:
            connection.sendall(denied)
            connection.settimeout(2)
            response = bytearray()
            while chunk := connection.recv(4096):
                response.extend(chunk)
        self.assertTrue(response.startswith(b"HTTP/1.1 413 "))
        self.assertEqual(len(self.stub.seen), before)  # type: ignore[attr-defined]
        line = self.log_lines()[-1]
        self.assertEqual(line["reason"], "body_too_large")
        self.assertNotIn("host_id", line)
        self.assertNotIn(ORIGIN, self.log.read_text())
        self.assertNotIn("hst_should_not_be_parsed", self.log.read_text())

    def test_body_at_the_cap_is_forwarded_and_one_byte_over_is_not(self) -> None:
        host_id = ids.new_id("hst")
        prefix = ('{"host_id":"%s","pad":"' % host_id).encode()
        suffix = b'"}'
        body = prefix + (b"a" * (MAX_BODY_BYTES - len(prefix) - len(suffix))) + suffix
        self.assertEqual(len(body), MAX_BODY_BYTES)
        status, _ = self.request("POST", "/v1/memory/hint", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(self.stub.seen[-1]["body"], body)  # type: ignore[attr-defined]
        self.assertEqual(self.log_lines()[-1]["host_id"], host_id)
        oversized = body + b"a"
        header = (
            f"POST /v1/memory/hint HTTP/1.1\r\nHost: {GATEWAY_HOST}\r\n"
            f"Content-Length: {len(oversized)}\r\nConnection: close\r\n\r\n"
        ).encode()
        before = len(self.stub.seen)  # type: ignore[attr-defined]
        with socket.create_connection((GATEWAY_HOST, self.port), timeout=2) as connection:
            connection.sendall(header)
            connection.settimeout(2)
            status_line = connection.recv(64)
        self.assertTrue(status_line.startswith(b"HTTP/1.1 413 "))
        self.assertEqual(len(self.stub.seen), before)  # type: ignore[attr-defined]

    def test_expect_continue_is_skipped_when_the_body_will_be_refused(self) -> None:
        header = (
            "POST /v1/events HTTP/1.1\r\n"
            f"Host: {GATEWAY_HOST}\r\n"
            f"Content-Length: {MAX_BODY_BYTES + 1}\r\n"
            "Expect: 100-continue\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        before = len(self.stub.seen)  # type: ignore[attr-defined]
        with socket.create_connection((GATEWAY_HOST, self.port), timeout=2) as connection:
            connection.sendall(header.encode("ascii"))
            connection.settimeout(2)
            response = bytearray()
            while chunk := connection.recv(4096):
                response.extend(chunk)
        self.assertFalse(response.startswith(b"HTTP/1.1 100"))
        self.assertTrue(response.startswith(b"HTTP/1.1 413 "))
        self.assertEqual(len(self.stub.seen), before)  # type: ignore[attr-defined]
        self.assertEqual(self.log_lines()[-1]["reason"], "body_too_large")

    def test_non_ascii_target_is_400_without_a_traceback(self) -> None:
        request = (
            b"GET /v1/health?q=\xff HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        before = len(self.stub.seen)  # type: ignore[attr-defined]
        with socket.create_connection((GATEWAY_HOST, self.port), timeout=2) as connection:
            connection.sendall(request)
            connection.settimeout(2)
            response = bytearray()
            while chunk := connection.recv(4096):
                response.extend(chunk)
        self.assertTrue(response.startswith(b"HTTP/1.1 400 "))
        self.assertIn(b"bad_request", response)
        self.assertEqual(len(self.stub.seen), before)  # type: ignore[attr-defined]
        self.assertEqual(self.log_lines()[-1]["reason"], "bad_request")
        self.assertEqual(self.log_lines()[-1]["status"], "400")

    def test_deeply_nested_json_still_writes_an_access_line(self) -> None:
        body = (b"[" * 10000) + (b"]" * 10000)
        self.assertLess(len(body), MAX_BODY_BYTES)
        status, _ = self.request("POST", "/v1/projects", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        line = self.log_lines()[-1]
        self.assertEqual(line["status"], "403")
        self.assertEqual(line["reason"], "not_allowlisted")
        self.assertNotIn("host_id", line)

    def test_credential_shaped_paths_are_not_logged(self) -> None:
        status, _ = self.request("GET", "/v1/sk-fixturepathsecret")
        self.assertEqual(status, 403)
        text = self.log.read_text()
        self.assertNotIn("sk-fixturepathsecret", text)
        self.assertEqual(self.log_lines()[-1]["path"], "unlisted")

    def test_non_id_host_field_is_not_logged(self) -> None:
        body = json.dumps({"host_id": ORIGIN, "origin": ORIGIN}).encode()
        status, _ = self.request("POST", "/v1/projects", body)
        self.assertEqual(status, 403)
        line = self.log_lines()[-1]
        self.assertNotIn("host_id", line)
        self.assertNotIn(ORIGIN, self.log.read_text())

    def test_plist_is_disabled_and_matches_core_keepalive(self) -> None:
        job = plistlib.loads(PLIST.read_bytes())
        self.assertEqual(job["Label"], "com.jasminecore.v1.local.tailnet-gateway")
        self.assertIs(job["Disabled"], True)
        self.assertIs(job["KeepAlive"], True)
        self.assertIs(job["RunAtLoad"], True)
        self.assertEqual(job["ThrottleInterval"], 5)
        self.assertEqual(job["Umask"], 63)
        arguments = job["ProgramArguments"]
        self.assertIn("18788", arguments)
        self.assertIn("127.0.0.1", arguments)
        self.assertIn("http://127.0.0.1:18787", arguments)
        text = PLIST.read_text()
        self.assertNotIn("tailscale", text.lower())
        self.assertNotIn("/Users/", text)
        self.assertNotIn("Funnel", text)


def write_secret(data_dir: Path, secret: str) -> None:
    path = secret_path(data_dir)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, (secret + "\n").encode())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


if __name__ == "__main__":
    unittest.main()
