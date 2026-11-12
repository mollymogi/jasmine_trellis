"""Hub-local tailnet enrollment and revoke, against a temporary database."""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jasmine_core import SCHEMA_VERSION, auth, db, ids, registry
from jasmine_core.clock import parse_rfc3339
from jasmine_core.events import EventStore
from jasmine_core.migrations import migrate
from jasmine_core.models import NewEvent

ROOT = Path(__file__).resolve().parents[1]
ENROLL = ROOT / "scripts/deployment/enroll-tailnet-client.py"
REVOKE = ROOT / "scripts/deployment/revoke-tailnet-client.py"
HAPPENED = "2026-11-12T00:00:00.000000Z"


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(module, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


class Enrollment(unittest.TestCase):
    def setUp(self):
        self.enroll = load_script(ENROLL, "enroll_tailnet_client")
        self.revoke = load_script(REVOKE, "revoke_tailnet_client")
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.database = self.root / "core.db"
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        conn = db.connect(self.database)
        migrate(conn)
        reg = registry.Registry(conn)
        self.hub_host, self.hub_human = ids.new_id("hst"), ids.new_id("act")
        with db.transaction(conn):
            reg.upsert_host(self.hub_host, display_name="hub")
            reg.upsert_actor(self.hub_human, kind="human", display_name="browser human",
                             home_host_id=self.hub_host)
        self.hub_key = auth.Auth(conn).issue_key(
            actor_id=self.hub_human, label="hub browser", scopes=["events:write"])
        conn.close()
        self.db_before = self.database.read_bytes()

    def enroll_client(self, *agents: str, display_name: str = "VM-0-9-ubuntu",
                      platform: str | None = "linux") -> tuple[int, str, str]:
        argv = ["--db", str(self.database), "--display-name", display_name, "--confirm",
                "--bundle", str(self.bundle)]
        if platform is not None:
            argv.extend(["--platform", platform])
        for agent in agents:
            argv.extend(["--agent", agent])
        return run(self.enroll, argv)

    def assert_silent_about(self, token: str, *texts: str) -> None:
        for text in texts:
            if token and token in text:
                self.fail("token appeared in output")

    def test_three_keys_are_pinned_to_the_new_host_and_not_the_hub_human(self):
        code, out, err = self.enroll_client("codex", "grok")
        self.assertEqual(code, 0, err)
        self.assertFalse(err)
        result = json.loads(out)
        host_id = result["host_id"]
        self.assertNotEqual(host_id, self.hub_host)
        self.assertTrue(ids.is_id(host_id, "hst"))
        self.assertEqual(self.enroll.CORE_URL, "https://mercuryliumac-mini.tail07df76.ts.net")
        self.assertFalse(self.enroll.CORE_URL.endswith("/"))
        conn = db.connect(self.database)
        try:
            client = conn.execute("SELECT * FROM tailnet_clients WHERE host_id = ?", (host_id,)).fetchone()
            self.assertEqual(client["status"], "active")
            self.assertEqual(client["display_name"], "VM-0-9-ubuntu")
            host = conn.execute("SELECT kind FROM hosts WHERE host_id = ?", (host_id,)).fetchone()
            self.assertEqual(host["kind"], "workstation")
            human = conn.execute("SELECT * FROM actors WHERE actor_id = ?", (result["human_actor_id"],)).fetchone()
            self.assertEqual(human["kind"], "human")
            self.assertEqual(human["home_host_id"], host_id)
            self.assertNotEqual(human["actor_id"], self.hub_human)
            humans = conn.execute(
                "SELECT actor_id FROM actors WHERE kind = 'human' AND home_host_id = ?", (host_id,)
            ).fetchall()
            self.assertEqual([row["actor_id"] for row in humans], [human["actor_id"]])
            rows = conn.execute(
                "SELECT k.label, k.scopes, k.bound_host_id, k.actor_id, k.revoked_at, a.kind, a.home_host_id"
                " FROM api_keys k JOIN actors a USING (actor_id) WHERE k.bound_host_id = ?"
                " ORDER BY k.label",
                (host_id,),
            ).fetchall()
            self.assertEqual(len(rows), 6)
            by_label = {row["label"]: row for row in rows}
            for agent in ("codex", "grok"):
                system = by_label[f"{agent} native system VM-0-9-ubuntu"]
                human_key = by_label[f"{agent} native user prompts VM-0-9-ubuntu"]
                memory = by_label[f"{agent} native Memory hint VM-0-9-ubuntu"]
                self.assertEqual(json.loads(system["scopes"]), ["events:write", "objects:write"])
                self.assertEqual(system["kind"], "system")
                self.assertEqual(system["home_host_id"], host_id)
                self.assertEqual(system["actor_id"], result["agents"][agent]["system_actor_id"])
                self.assertEqual(json.loads(human_key["scopes"]), ["events:write"])
                self.assertEqual(human_key["actor_id"], human["actor_id"])
                self.assertNotEqual(human_key["actor_id"], self.hub_human)
                self.assertEqual(json.loads(memory["scopes"]), ["memory:recall"])
                self.assertEqual(memory["actor_id"], system["actor_id"])
                for row in (system, human_key, memory):
                    self.assertEqual(row["bound_host_id"], host_id)
                    self.assertIsNone(row["revoked_at"])
                binding_path = self.bundle / f"{agent}-conversation-device.json"
                document = json.loads(binding_path.read_text())
                self.assertEqual(document["core_url"], self.enroll.CORE_URL)
                self.assertEqual(document["host_id"], host_id)
                self.assertFalse(document["memory_hint"])
                self.assertNotIn("project_id", document)
                for kind in ("system", "human", "memory"):
                    key_path = self.bundle / f"{agent}-conversation-{kind}.key"
                    self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
                    self.assertEqual(document[{"system": "api_key_file", "human": "human_api_key_file",
                                               "memory": "memory_key_file"}[kind]], str(key_path))
                    principal = auth.Auth(conn).authenticate("Bearer " + key_path.read_text().strip())
                    self.assertEqual(principal.bound_host_id, host_id)
                    self.assert_silent_about(key_path.read_text().strip(), out, err, binding_path.read_text())
                self.assertEqual(stat.S_IMODE(binding_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.bundle.stat().st_mode), 0o700)
            hub = conn.execute(
                "SELECT revoked_at, bound_host_id FROM api_keys WHERE key_id = ?", (self.hub_key["key_id"],)
            ).fetchone()
            self.assertIsNone(hub["revoked_at"])
            self.assertIsNone(hub["bound_host_id"])
            self.assert_silent_about(self.hub_key["token"], out, err)
            for path in self.bundle.iterdir():
                self.assertEqual(path.parent.resolve(), self.bundle.resolve())
                self.assertNotIn(self.hub_key["token"], path.read_text())
        finally:
            conn.close()

    def test_a_symlink_bundle_writes_nothing(self):
        link = self.root / "link"
        link.symlink_to(self.root / "missing-target")
        before = self.database.read_bytes()
        code, out, err = run(self.enroll, [
            "--db", str(self.database), "--display-name", "liuxu", "--confirm",
            "--bundle", str(link), "--platform", "linux", "--agent", "codex",
        ])
        self.assertNotEqual(code, 0)
        self.assertFalse(out)
        self.assertIn("symlink", err)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / "missing-target").exists())

    def test_a_failed_key_write_revokes_issued_keys(self):
        def fail_write(path, data):
            raise OSError("short write")

        self.enroll._write_private = fail_write
        code, out, err = self.enroll_client("codex")
        self.assertEqual(code, 1)
        self.assertFalse(out)
        self.assertIn("those keys were revoked", err)
        self.assert_silent_about(self.hub_key["token"], out, err)
        conn = db.connect(self.database)
        try:
            clients = conn.execute("SELECT host_id, status FROM tailnet_clients").fetchall()
            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0]["status"], "revoked")
            keys = conn.execute(
                "SELECT revoked_at FROM api_keys WHERE bound_host_id = ?", (clients[0]["host_id"],)
            ).fetchall()
            self.assertEqual(len(keys), 3)
            self.assertTrue(all(row["revoked_at"] for row in keys))
            hub = conn.execute(
                "SELECT revoked_at FROM api_keys WHERE key_id = ?", (self.hub_key["key_id"],)
            ).fetchone()
            self.assertIsNone(hub["revoked_at"])
        finally:
            conn.close()

    def test_windows_enrollment_writes_nothing(self):
        cases = (
            ["--display-name", "WIN-DRUN38VBEEM", "--platform", "linux"],
            ["--display-name", "liuxu", "--platform", "windows"],
            ["--display-name", "my-windows-box"],
        )
        for extra in cases:
            with self.subTest(extra=extra):
                before = self.database.read_bytes()
                argv = ["--db", str(self.database), "--confirm", "--bundle", str(self.bundle),
                        "--agent", "codex", *extra]
                code, out, err = run(self.enroll, argv)
                self.assertNotEqual(code, 0)
                self.assertFalse(out)
                self.assertIn("Windows enrollment is refused", err)
                self.assertEqual(self.database.read_bytes(), before)
                self.assertEqual(list(self.bundle.iterdir()), [])

    def test_revoke_marks_keys_and_the_client_and_leaves_events(self):
        code, out, err = self.enroll_client("codex")
        self.assertEqual(code, 0, err)
        host_id = json.loads(out)["host_id"]
        human_id = json.loads(out)["human_actor_id"]
        conn = db.connect(self.database)
        try:
            spec = NewEvent(
                event_type="user.prompt", source_system="test", actor_id=human_id, actor_kind="human",
                host_id=host_id, payload={"text": "keep me"}, occurred_at=parse_rfc3339(HAPPENED),
                client_occurred_at=parse_rfc3339(HAPPENED), source_event_id="enroll-revoke-1",
            )
            with db.transaction(conn):
                event, replayed = EventStore(conn, schema_version=SCHEMA_VERSION).append(spec)
            self.assertFalse(replayed)
            event_id = event["event_id"]
        finally:
            conn.close()
        code, out, err = run(self.revoke, ["--db", str(self.database), "--host-id", host_id, "--confirm"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["keys_revoked"], 3)
        conn = db.connect(self.database)
        try:
            keys = conn.execute(
                "SELECT revoked_at FROM api_keys WHERE bound_host_id = ?", (host_id,)
            ).fetchall()
            self.assertEqual(len(keys), 3)
            self.assertTrue(all(row["revoked_at"] for row in keys))
            client = conn.execute("SELECT status, host_id FROM tailnet_clients WHERE host_id = ?", (host_id,)).fetchone()
            self.assertEqual(client["status"], "revoked")
            self.assertEqual(client["host_id"], host_id)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM hosts WHERE host_id = ?", (host_id,)).fetchone())
            stored = conn.execute("SELECT payload_json FROM events WHERE event_id = ?", (event_id,)).fetchone()
            self.assertEqual(json.loads(stored["payload_json"])["text"], "keep me")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"], 1)
            hub = conn.execute(
                "SELECT revoked_at FROM api_keys WHERE key_id = ?", (self.hub_key["key_id"],)
            ).fetchone()
            self.assertIsNone(hub["revoked_at"])
        finally:
            conn.close()
        self.bundle = self.root / "second"
        self.bundle.mkdir()
        code, out, err = self.enroll_client("claude", display_name="liuxu")
        self.assertEqual(code, 0, err)
        self.assertNotEqual(json.loads(out)["host_id"], host_id)


if __name__ == "__main__":
    unittest.main()
