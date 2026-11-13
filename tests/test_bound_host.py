"""Tailnet requests are pinned to api_keys.bound_host_id.

The gateway process is not started. Tests inject X-Jasmine-Gateway themselves
and use a temporary data directory plus a temporary database.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jasmine_core import auth, cli, db, errors, ids, registry
from jasmine_core.api.request import Request
from jasmine_core.api.server import Application, data_dir_for_db, serve
from jasmine_core.migrations import migrate
from jasmine_core.tailnet_gateway import GatewayStartupError, secret_path

SECRET = "gateway-test-secret"
WRONG = "wrong-gateway-header"
PROMPT = "bound-host prompt that must not be logged"
ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"
SCOPES = ["objects:write", "events:write", "memory:recall"]


def write_secret(data_dir: Path, secret: str, mode: int = 0o600) -> Path:
    path = secret_path(data_dir)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, (secret + "\n").encode())
    finally:
        os.close(descriptor)
    os.chmod(path, mode)
    return path


class BoundHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-bound-host-")
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        self.db_path = self.data_dir / "data" / "core.db"
        self.db_path.parent.mkdir()
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate(self.conn)
        self.host = ids.new_id("hst")
        self.other = ids.new_id("hst")
        reg = registry.Registry(self.conn)
        with db.transaction(self.conn):
            reg.upsert_host(self.host, display_name="client")
            reg.upsert_host(self.other, display_name="other")
            reg.upsert_actor(ACTOR, kind="human", display_name="tester", home_host_id=self.host)
            self.conn.execute(
                "INSERT INTO tailnet_clients"
                " (host_id, display_name, status, created_at)"
                " VALUES (?, 'client', 'active', '2026-11-12T00:00:00.000000Z')",
                (self.host,),
            )
        issuer = auth.Auth(self.conn)
        self.open_key = issuer.issue_key(actor_id=ACTOR, label="open", scopes=SCOPES)
        self.pinned_key = issuer.issue_key(
            actor_id=ACTOR, label="pinned", scopes=SCOPES, bound_host_id=self.host)
        self._saved_flag = os.environ.get("JASMINE_TAILNET_CLIENTS")
        os.environ["JASMINE_TAILNET_CLIENTS"] = "1"
        self.addCleanup(self._restore_flag)
        write_secret(self.data_dir, SECRET)
        self.app = Application(self.db_path)
        self.assertEqual(data_dir_for_db(self.db_path), self.data_dir.resolve())
        self.assertEqual(self.app.gateway_secret, SECRET)

    def _restore_flag(self) -> None:
        if self._saved_flag is None:
            os.environ.pop("JASMINE_TAILNET_CLIENTS", None)
        else:
            os.environ["JASMINE_TAILNET_CLIENTS"] = self._saved_flag

    def call(self, method: str, path: str, body=None, *, token: str | None = None,
             gateway: str | None = None) -> tuple[int, dict]:
        headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        if gateway is not None:
            headers["x-jasmine-gateway"] = gateway
        raw = b"" if body is None else json.dumps(body).encode()
        response = self.app.handle(Request(method=method, path=path, headers=headers, body=raw))
        payload = response.body if isinstance(response.body, dict) else {}
        return response.status, payload

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]

    def test_loopback_recovery_does_not_enforce_the_key_pin(self) -> None:
        for enabled in ("1", "0"):
            with self.subTest(tailnet_clients=enabled):
                os.environ["JASMINE_TAILNET_CLIENTS"] = enabled
                status, body = self.call(
                    "POST", "/v1/session-checkpoints/recover",
                    {"host_id": self.other, "workspace_root": "/tmp/jasmine-bound-host"},
                    token=self.pinned_key["token"])
                self.assertEqual(status, 200, body)
                self.assertEqual(body["count"], 0)

    def audit_text(self) -> str:
        rows = self.conn.execute("SELECT error_code, detail_json, path FROM audit_log").fetchall()
        return json.dumps([dict(row) for row in rows])

    def test_migration_leaves_existing_pins_null(self) -> None:
        self.assertIsNone(self.open_key["bound_host_id"])
        stored = self.conn.execute(
            "SELECT bound_host_id FROM api_keys WHERE key_id = ?", (self.open_key["key_id"],)
        ).fetchone()["bound_host_id"]
        self.assertIsNone(stored)
        self.assertEqual(self.pinned_key["bound_host_id"], self.host)

    def test_missing_header_keeps_null_key_behavior(self) -> None:
        before = self.event_count()
        status, body = self.call(
            "POST", "/v1/events", self._event(self.host), token=self.open_key["token"])
        self.assertEqual(status, 201)
        self.assertEqual(body["event"]["host_id"], self.host)
        self.assertEqual(self.event_count(), before + 1)

    def test_wrong_header_is_not_treated_as_local(self) -> None:
        before = self.event_count()
        status, body = self.call(
            "POST", "/v1/events", self._event(self.host),
            token=self.open_key["token"], gateway=WRONG)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "gateway_secret_mismatch")
        self.assertEqual(self.event_count(), before)
        self.assertIsNone(self._last_used(self.open_key["key_id"]))
        raw = self.audit_text()
        self.assertIn("gateway_secret_mismatch", raw)
        self.assertNotIn(WRONG, raw)
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(PROMPT, raw)
        health, health_body = self.call("GET", "/v1/health", gateway=WRONG)
        self.assertEqual(health, 403)
        self.assertEqual(health_body["error"]["code"], "gateway_secret_mismatch")

    def test_valid_header_rejects_an_unpinned_key(self) -> None:
        before = self.event_count()
        status, body = self.call(
            "POST", "/v1/events", self._event(self.host),
            token=self.open_key["token"], gateway=SECRET)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "gateway_key_not_pinned")
        self.assertEqual(self.event_count(), before)
        self.assertIsNone(self._last_used(self.open_key["key_id"]))
        self.assertNotIn(SECRET, self.audit_text())

    def test_mismatched_host_does_not_insert_an_event(self) -> None:
        before = self.event_count()
        status, body = self.call(
            "POST", "/v1/events", self._event(self.other),
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "host_mismatch")
        self.assertEqual(self.event_count(), before)
        self.assertNotIn(PROMPT, self.audit_text())
        self.assertNotIn(SECRET, self.audit_text())

    def test_matching_host_is_accepted_on_named_routes(self) -> None:
        status, project = self.call(
            "POST", "/v1/projects", {"name": "demo", "host_id": self.host},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, project)
        project_id = project["object"]["project_id"]
        self._alias(self.host, "/tmp/jasmine-bound-host", project_id)
        status, session = self.call(
            "POST", "/v1/sessions", {"project_id": project_id, "host_id": self.host},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, session)
        status, event = self.call(
            "POST", "/v1/events", self._event(self.host, project_id=project_id),
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, event)
        status, recent = self.call(
            "POST", "/v1/continuity/recent",
            {"host_id": self.host, "workspace_root": "/tmp/jasmine-bound-host", "scope": "global"},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 200, recent)
        status, recovered = self.call(
            "POST", "/v1/session-checkpoints/recover",
            {"host_id": self.host, "workspace_root": "/tmp/jasmine-bound-host"},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 200, recovered)
        self.assertEqual(recovered["count"], 0)

    def test_revoked_client_is_refused_on_alias_gated_writes(self) -> None:
        status, project = self.call(
            "POST", "/v1/projects", {"name": "revoked-demo", "host_id": self.host},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, project)
        project_id = project["object"]["project_id"]
        self._alias(self.host, "/tmp/jasmine-revoked", project_id)
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE tailnet_clients SET status='revoked', revoked_at=? WHERE host_id=?",
                ("2026-11-12T00:00:00.000000Z", self.host))
        bodies = {
            "/v1/sessions": {"project_id": project_id, "host_id": self.host},
            "/v1/events": self._event(self.host, project_id=project_id),
            "/v1/memory/hint": {
                "project_id": project_id, "native_session_id": "native",
                "prompt": PROMPT, "host_id": self.host,
            },
            "/v1/session-checkpoints": {
                "host_id": self.host, "project_id": project_id,
                "native_session_id": "native", "reason": "SESSION_END",
                "workspace_root": "/tmp/jasmine-revoked",
            },
            "/v1/session-checkpoints/recover": {
                "host_id": self.host, "workspace_root": "/tmp/jasmine-revoked",
            },
            "/v1/continuity/recent": {
                "host_id": self.host, "workspace_root": "/tmp/jasmine-revoked",
            },
        }
        before = self.event_count()
        for path, body in bodies.items():
            status, payload = self.call(
                "POST", path, body, token=self.pinned_key["token"], gateway=SECRET)
            self.assertEqual(status, 403, path)
            self.assertEqual(payload["error"]["code"], "project_not_aliased", path)
        self.assertEqual(self.event_count(), before)

    def test_confirmed_cross_path_client_recovers_and_resumes_its_sessions(self) -> None:
        hub_path = "/srv/hub/cross-path"
        client_path = "/tmp/jasmine-cross-path"
        status, project = self.call(
            "POST", "/v1/projects",
            {"name": "cross-path", "description": hub_path, "host_id": self.host},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, project)
        project_id = project["object"]["project_id"]
        self._alias(self.host, client_path, project_id)
        status, session = self.call(
            "POST", "/v1/sessions", {"project_id": project_id, "host_id": self.host},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, session)
        session_id = session["object"]["session_id"]
        occurred = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
        status, event = self.call(
            "POST", "/v1/events",
            self._event(self.host, project_id=project_id, session_id=session_id,
                        occurred_at=occurred),
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, event)
        status, recovered = self.call(
            "POST", "/v1/session-checkpoints/recover",
            {"host_id": self.host, "workspace_root": client_path},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 200, recovered)
        self.assertEqual(recovered["count"], 1, recovered)
        checkpoint = self.conn.execute(
            "SELECT session_id, project_id, host_id, workspace_root, family_root"
            " FROM session_checkpoints WHERE session_checkpoint_id=?",
            (recovered["recovered"][0],)).fetchone()
        self.assertEqual(checkpoint["session_id"], session_id)
        self.assertEqual(checkpoint["project_id"], project_id)
        self.assertEqual(checkpoint["host_id"], self.host)
        self.assertEqual(checkpoint["workspace_root"], client_path)
        self.assertEqual(checkpoint["family_root"], client_path)
        status, recent = self.call(
            "POST", "/v1/continuity/recent",
            {"host_id": self.host, "workspace_root": client_path},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 200, recent)
        self.assertEqual(recent["status"], "ok", recent)
        self.assertEqual([s["session_id"] for s in recent["sessions"]], [session_id])
        self.assertNotIn(hub_path, recent["text"])
        status, global_recent = self.call(
            "POST", "/v1/continuity/recent",
            {"host_id": self.host, "workspace_root": client_path, "scope": "global"},
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 200, global_recent)
        self.assertEqual([s["session_id"] for s in global_recent["sessions"]], [session_id])

    def test_mismatched_host_is_refused_on_every_named_route(self) -> None:
        before = self.event_count()
        bodies = {
            "/v1/projects": {"name": "nope", "host_id": self.other},
            "/v1/sessions": {"host_id": self.other},
            "/v1/events": self._event(self.other),
            "/v1/memory/hint": {
                "project_id": ids.new_id("prj"), "native_session_id": "native",
                "prompt": PROMPT, "host_id": self.other,
            },
            "/v1/continuity/recent": {
                "host_id": self.other, "workspace_root": "/tmp/jasmine-bound-host",
            },
            "/v1/session-checkpoints": {
                "host_id": self.other, "project_id": ids.new_id("prj"),
                "native_session_id": "native", "reason": "SESSION_END",
                "workspace_root": "/tmp/jasmine-bound-host",
            },
            "/v1/session-checkpoints/recover": {
                "host_id": self.other, "workspace_root": "/tmp/jasmine-bound-host",
            },
            "/v1/tasks": {"title": "nope", "host_id": self.other},
            "/v1/checkpoints": {"host_id": self.other},
            f"/v1/tasks/{ids.new_id('tsk')}/resume": {"host_id": self.other},
        }
        for path, body in bodies.items():
            status, payload = self.call(
                "POST", path, body, token=self.pinned_key["token"], gateway=SECRET)
            self.assertEqual(status, 403, path)
            self.assertEqual(payload["error"]["code"], "host_mismatch", path)
        self.assertEqual(self.event_count(), before)
        self.assertNotIn(PROMPT, self.audit_text())

    def test_unset_flag_rejects_writes_but_not_health(self) -> None:
        os.environ.pop("JASMINE_TAILNET_CLIENTS", None)
        before = self.event_count()
        status, body = self.call(
            "POST", "/v1/events", self._event(self.host),
            token=self.pinned_key["token"], gateway=SECRET)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "tailnet_disabled")
        self.assertEqual(self.event_count(), before)
        self.assertIsNone(self._last_used(self.pinned_key["key_id"]))
        health, health_body = self.call("GET", "/v1/health", gateway=SECRET)
        self.assertEqual(health, 200)
        self.assertEqual(health_body["status"], "ok")
        schema, schema_body = self.call("GET", "/v1/meta/schema", gateway=SECRET)
        self.assertEqual(schema, 403)
        self.assertEqual(schema_body["error"]["code"], "tailnet_disabled")
        self.assertNotIn(SECRET, self.audit_text())

    def test_issuing_route_does_not_accept_a_pin(self) -> None:
        admin = auth.Auth(self.conn).issue_key(actor_id=ACTOR, label="admin", scopes=["admin"])
        status, body = self.call(
            "POST", "/v1/auth/keys",
            {"label": "remote", "scopes": ["events:write"], "bound_host_id": self.host},
            token=admin["token"])
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertIn("bound_host_id", body["error"]["details"]["fields"])

    def _alias(self, host_id: str, root: str, project_id: str) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO workspace_aliases"
                " (host_id, root, project_id, remote_key, confirmed, created_at)"
                " VALUES (?, ?, ?, NULL, 1, '2026-11-12T00:00:00.000000Z')",
                (host_id, root, project_id),
            )

    def test_omitted_host_is_refused_on_every_named_route(self) -> None:
        before = self.event_count()
        bodies = {
            "/v1/projects": {"name": "nope"},
            "/v1/sessions": {},
            "/v1/events": {
                "event_type": "user.prompt", "source_system": "test",
                "payload": {"text": PROMPT}, "occurred_at": "2026-09-29T12:00:00.000000Z",
            },
            "/v1/memory/hint": {
                "project_id": ids.new_id("prj"), "native_session_id": "native",
                "prompt": PROMPT,
            },
            "/v1/continuity/recent": {"workspace_root": "/tmp/jasmine-bound-host"},
            "/v1/session-checkpoints": {
                "project_id": ids.new_id("prj"), "native_session_id": "native",
                "reason": "SESSION_END", "workspace_root": "/tmp/jasmine-bound-host",
            },
            "/v1/session-checkpoints/recover": {"workspace_root": "/tmp/jasmine-bound-host"},
            "/v1/tasks": {"title": "nope"},
            "/v1/checkpoints": {},
            f"/v1/tasks/{ids.new_id('tsk')}/resume": {},
        }
        for path, body in bodies.items():
            status, payload = self.call(
                "POST", path, body, token=self.pinned_key["token"], gateway=SECRET)
            self.assertEqual(status, 403, path)
            self.assertEqual(payload["error"]["code"], "host_mismatch", path)
        self.assertEqual(self.event_count(), before)
        self.assertIsNone(self._last_used(self.pinned_key["key_id"]))
        self.assertNotIn(PROMPT, self.audit_text())

    def test_bind_key_pins_an_existing_key_without_rotating_its_token(self) -> None:
        status, body = self.call(
            "POST", "/v1/events", self._event(self.host),
            token=self.open_key["token"], gateway=SECRET)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "gateway_key_not_pinned")
        code, output = self._cli([
            "bind-key", "--db", str(self.db_path),
            "--key-id", self.open_key["key_id"], "--host-id", self.host,
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {
            "key_id": self.open_key["key_id"],
            "bound_host_id": self.host,
            "previous_bound_host_id": None,
        })
        status, project = self.call(
            "POST", "/v1/projects", {"name": "after", "host_id": self.host},
            token=self.open_key["token"], gateway=SECRET)
        self.assertEqual(status, 201, project)

    def test_bind_key_reads_a_client_key_file(self) -> None:
        key_file = self.data_dir / "capture.key"
        key_file.write_text(self.open_key["token"] + "\n")
        code, output = self._cli([
            "bind-key", "--db", str(self.db_path),
            "--token-file", str(key_file), "--host-id", self.host,
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["bound_host_id"], self.host)
        principal = auth.Auth(self.conn).authenticate("Bearer " + self.open_key["token"])
        self.assertEqual(principal.bound_host_id, self.host)
        self.assertNotIn(self.open_key["token"], output)

    def test_bind_key_refuses_a_token_file_that_is_not_utf8(self) -> None:
        key_file = self.data_dir / "malformed.key"
        key_file.write_bytes(b"\xff\xfe\x00not a token")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([
                "bind-key", "--db", str(self.db_path),
                "--token-file", str(key_file), "--host-id", self.host,
            ])
        self.assertEqual(code, 3)
        self.assertNotIn("Traceback", err.getvalue())
        self.assertIn("invalid_request", err.getvalue())
        self.assertIsNone(self._last_used(self.open_key["key_id"]))

    def test_bind_key_refuses_an_unknown_malformed_or_revoked_key(self) -> None:
        issuer = auth.Auth(self.conn)
        with self.assertRaises(errors.NotFound) as unknown:
            issuer.bind_key(ids.new_id("key"), self.host)
        self.assertEqual(unknown.exception.code, "key_not_found")
        with self.assertRaises(errors.NotFound) as unknown_host:
            issuer.bind_key(self.open_key["key_id"], ids.new_id("hst"))
        self.assertEqual(unknown_host.exception.code, "host_not_found")
        with self.assertRaises(errors.InvalidRequest):
            issuer.bind_key("not-a-key", self.host)
        with self.assertRaises(errors.InvalidRequest):
            issuer.bind_token("", self.host)
        with self.assertRaises(errors.NotFound) as unknown_token:
            issuer.bind_token("not-a-token", self.host)
        self.assertEqual(unknown_token.exception.code, "key_not_found")
        revoked = issuer.issue_key(actor_id=ACTOR, label="revoked", scopes=["events:write"])
        issuer.revoke(revoked["key_id"])
        with self.assertRaises(errors.InvalidRequest) as refused:
            issuer.bind_key(revoked["key_id"], self.host)
        self.assertEqual(refused.exception.details["field"], "key_id")
        self.assertIsNone(self._last_used(self.open_key["key_id"]))

    def _event(self, host_id: str, **extra) -> dict:
        body = {
            "event_type": "user.prompt", "source_system": "test", "host_id": host_id,
            "payload": {"text": PROMPT}, "occurred_at": "2026-09-29T12:00:00.000000Z",
        }
        body.update(extra)
        return body

    def _last_used(self, key_id: str):
        return self.conn.execute(
            "SELECT last_used_at FROM api_keys WHERE key_id = ?", (key_id,)
        ).fetchone()["last_used_at"]

    def _cli(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()


class GatewaySecretStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-gateway-secret-")
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        self.db_path = self.data_dir / "data" / "core.db"
        self.db_path.parent.mkdir()

    def test_absent_secret_still_starts(self) -> None:
        app = Application(self.db_path)
        self.assertIsNone(app.gateway_secret)
        self.assertFalse(secret_path(self.data_dir).exists())

    def test_bad_mode_fails_startup_without_printing_the_secret(self) -> None:
        write_secret(self.data_dir, SECRET, mode=0o644)
        self.assertEqual(stat.S_IMODE(secret_path(self.data_dir).stat().st_mode), 0o644)
        with self.assertRaises(GatewayStartupError) as raised:
            Application(self.db_path)
        self.assertNotIn(SECRET, str(raised.exception))
        logs: list[str] = []
        with self.assertRaises(SystemExit) as exited:
            serve(self.db_path, port=0, log=logs.append)
        self.assertEqual(exited.exception.code, 3)
        text = "\n".join(logs)
        self.assertIn("refusing to start", text)
        self.assertNotIn(SECRET, text)

    def test_non_ascii_header_mismatches_and_latin1_secret_matches(self) -> None:
        secret = "caf\u00e9-gate"
        write_secret(self.data_dir, secret)
        conn = db.connect(self.db_path)
        migrate(conn)
        conn.close()
        app = Application(self.db_path)
        loaded = app.gateway_secret
        self.assertTrue(
            loaded is not None
            and secrets.compare_digest(loaded.encode("latin-1"), secret.encode("latin-1"))
        )

        def health(gateway: str) -> tuple[int, dict]:
            response = app.handle(Request(
                method="GET", path="/v1/health",
                headers={"x-jasmine-gateway": gateway},
            ))
            body = response.body if isinstance(response.body, dict) else {}
            return response.status, body

        status, body = health(secret)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "ok")
        status, body = health("caf\u00e9-nope")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "gateway_secret_mismatch")
        status, body = health("\U0001f642")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "gateway_secret_mismatch")
        self.assertNotEqual(status, 500)
        audited = db.connect(self.db_path)
        blob = " ".join(row[0] or "" for row in audited.execute("SELECT detail_json FROM audit_log"))
        audited.close()
        self.assertFalse(secret in blob)
        self.assertFalse("caf\u00e9-nope" in blob)
        self.assertFalse("\U0001f642" in blob)


if __name__ == "__main__":
    unittest.main()
