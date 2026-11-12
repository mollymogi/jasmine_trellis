"""Workspace alias resolution. A client origin is not permission to join a bank."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jasmine_core import auth, db, ids, registry
from jasmine_core.api.request import Request
from jasmine_core.api.server import Application
from jasmine_core.migrations import migrate
from jasmine_core.workspace_alias import canonical_remote_key, classify_origin
from test_bound_host import SECRET, write_secret

ACTOR = "act_01K742SG00CBKP25A9ETPBTRMJ"
SCOPES = ["objects:write", "events:write", "memory:recall"]
SECRET_ORIGIN = "https://user:ghp_secret@github.com/Org/Repo.git"
SCP_TOKEN_ORIGIN = "git@github.com:Org/Repo.git?token=ghp_secret"
CANONICAL = "github.com/Org/Repo"
PORT_KEY = "gitlab.example:2222/group/repo"
VECTORS = (
    ("git@github.com:Org/Repo.git", "url", "github.com/Org/Repo"),
    ("ssh://git@github.com/Org/Repo.git", "url", "github.com/Org/Repo"),
    ("ssh://git@github.com:22/Org/Repo.git", "url", "github.com/Org/Repo"),
    ("https://github.com/Org/Repo/", "url", "github.com/Org/Repo"),
    (SECRET_ORIGIN, "url", "github.com/Org/Repo"),
    ("https://gitlab.com/group/sub/repo.git", "url", "gitlab.com/group/sub/repo"),
    ("ssh://git@gitlab.example:2222/group/repo.git", "url", "gitlab.example:2222/group/repo"),
    (SCP_TOKEN_ORIGIN, "url", "github.com/Org/Repo"),
    ("git@github.com:Org/Repo.git#fragment", "url", "github.com/Org/Repo"),
    ("file:///home/a/repo", "local", None),
    ("/home/a/repo", "local", None),
    ("", "absent", None),
    (None, "absent", None),
)
ROOT = Path(__file__).resolve().parents[1]
CONFIRM = ROOT / "scripts" / "deployment" / "confirm-tailnet-workspace.py"
BACKFILL = ROOT / "scripts" / "deployment" / "backfill-workspace-aliases.py"


class CanonicalOriginTests(unittest.TestCase):
    def test_vectors_drop_userinfo_and_default_ports(self) -> None:
        for origin, kind, key in VECTORS:
            with self.subTest(origin=origin):
                found_kind, found_key = classify_origin(origin)
                self.assertEqual(found_kind, kind)
                self.assertEqual(found_key, key)
                if found_key is not None:
                    self.assertNotIn("ghp_secret", found_key)
                    self.assertNotIn("user:", found_key)
                    self.assertNotIn("@", found_key)

    def test_host_case_is_folded_and_the_path_is_not(self) -> None:
        kind, key = classify_origin("git@GitHub.com:Org/Repo.git")
        self.assertEqual((kind, key), ("url", "github.com/Org/Repo"))
        kind, key = classify_origin("https://GitLab.com/Group/Sub/Repo.git")
        self.assertEqual((kind, key), ("url", "gitlab.com/Group/Sub/Repo"))

    def test_stored_canonical_keys_round_trip(self) -> None:
        self.assertEqual(canonical_remote_key("github.com/Org/Repo"), CANONICAL)
        self.assertEqual(canonical_remote_key("github.com/Org/Repo.git"), CANONICAL)
        self.assertEqual(canonical_remote_key(PORT_KEY), PORT_KEY)
        self.assertEqual(canonical_remote_key(PORT_KEY + ".git"), PORT_KEY)
        self.assertEqual(canonical_remote_key(SCP_TOKEN_ORIGIN), CANONICAL)
        self.assertNotIn("ghp_secret", canonical_remote_key(SCP_TOKEN_ORIGIN))


class WorkspaceAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="jasmine-alias-")
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
            reg.upsert_host(self.host, display_name="client-a")
            reg.upsert_host(self.other, display_name="client-b")
            reg.upsert_actor(ACTOR, kind="human", display_name="tester", home_host_id=self.host)
        issuer = auth.Auth(self.conn)
        self.key = issuer.issue_key(
            actor_id=ACTOR, label="pinned-a", scopes=SCOPES, bound_host_id=self.host)
        self.other_key = issuer.issue_key(
            actor_id=ACTOR, label="pinned-b", scopes=SCOPES, bound_host_id=self.other)
        self._saved_flag = os.environ.get("JASMINE_TAILNET_CLIENTS")
        os.environ["JASMINE_TAILNET_CLIENTS"] = "1"
        self.addCleanup(self._restore_flag)
        write_secret(self.data_dir, SECRET)
        self.app = Application(self.db_path)

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

    def resolve(self, host: str, root: str, origin, kind: str, *, token: str) -> tuple[int, dict]:
        return self.call(
            "POST", "/v1/workspaces/resolve",
            {"host_id": host, "root": root, "origin": origin, "remote_kind": kind},
            token=token, gateway=SECRET)

    def project(self, name: str, description: str = "") -> str:
        status, body = self.call(
            "POST", "/v1/projects",
            {"name": name, "description": description, "host_id": self.host},
            token=self.key["token"])
        self.assertEqual(status, 201, body)
        return body["object"]["project_id"]

    def confirm(self, host: str, root: str, remote_key: str, project_id: str | None = None,
                **extra: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable, str(CONFIRM), "--db", str(self.db_path), "--host-id", host,
            "--root", root, "--remote-key", remote_key, "--confirm",
        ]
        if project_id is not None:
            command.extend(["--project-id", project_id])
        for name, value in extra.items():
            command.append("--" + name.replace("_", "-"))
            if value != "":
                command.append(value)
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def alias_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM workspace_aliases").fetchone()["n"]

    def remote_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM workspace_remotes").fetchone()["n"]

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]

    def stored_text(self) -> str:
        chunks = [self.db_path.read_bytes().decode("utf-8", "replace")]
        for suffix in ("-wal", "-shm"):
            side = Path(str(self.db_path) + suffix)
            if side.exists():
                chunks.append(side.read_bytes().decode("utf-8", "replace"))
        return "\n".join(chunks)

    def test_alias_remote_key_is_not_unique(self) -> None:
        unique_remote = []
        for index in self.conn.execute("PRAGMA index_list('workspace_aliases')"):
            if not index["unique"]:
                continue
            columns = [
                info["name"] for info in self.conn.execute(f"PRAGMA index_info('{index['name']}')")
            ]
            if columns == ["remote_key"]:
                unique_remote.append(index["name"])
        self.assertEqual(unique_remote, [])

    def test_userinfo_is_not_stored_or_returned(self) -> None:
        before_projects = self.conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        status, body = self.resolve(self.host, "/srv/secret", SECRET_ORIGIN, "url", token=self.key["token"])
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["project_id"])
        self.assertEqual(body["status"], "confirmation_required")
        self.assertFalse(body["created"])
        rendered = json.dumps(body)
        self.assertNotIn("ghp_secret", rendered)
        self.assertNotIn(SECRET_ORIGIN, rendered)
        self.assertEqual(self.alias_count(), 0)
        self.assertEqual(self.remote_count(), 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"], before_projects)
        self.assertNotIn("ghp_secret", self.stored_text())
        project_id = self.project("secret-bank")
        confirmed = self.confirm(self.host, "/srv/secret", SECRET_ORIGIN, project_id)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertNotIn("ghp_secret", confirmed.stdout + confirmed.stderr)
        stored = self.conn.execute("SELECT remote_key FROM workspace_remotes").fetchone()["remote_key"]
        self.assertEqual(stored, CANONICAL)
        self.assertNotIn("ghp_secret", self.stored_text())

    def test_two_hosts_share_one_remote_only_after_confirm(self) -> None:
        project_id = self.project("shared")
        root_a, root_b = "/srv/host-a", "/srv/host-b"
        origin = "git@github.com:Org/Repo.git"
        first, first_body = self.resolve(self.host, root_a, origin, "url", token=self.key["token"])
        self.assertEqual(first, 200, first_body)
        self.assertIsNone(first_body["project_id"])
        self.assertFalse(first_body["alias_conflict"])
        self.assertEqual(self.alias_count(), 0)
        second, second_body = self.resolve(
            self.other, root_b, origin, "url", token=self.other_key["token"])
        self.assertEqual(second, 200, second_body)
        self.assertIsNone(second_body["project_id"])
        self.assertEqual(self.alias_count(), 0)
        confirmed_a = self.confirm(self.host, root_a, origin, project_id)
        self.assertEqual(confirmed_a.returncode, 0, confirmed_a.stderr)
        self.assertEqual(self.alias_count(), 1)
        again, again_body = self.resolve(
            self.other, root_b, origin, "url", token=self.other_key["token"])
        self.assertEqual(again, 200, again_body)
        self.assertIsNone(again_body["project_id"])
        self.assertTrue(again_body["alias_conflict"])
        self.assertNotIn(project_id, json.dumps(again_body))
        self.assertEqual(self.alias_count(), 1)
        confirmed_b = self.confirm(self.other, root_b, origin, project_id)
        self.assertEqual(confirmed_b.returncode, 0, confirmed_b.stderr)
        self.assertEqual(self.remote_count(), 1)
        left, left_body = self.resolve(self.host, root_a, origin, "url", token=self.key["token"])
        right, right_body = self.resolve(
            self.other, root_b, origin, "url", token=self.other_key["token"])
        self.assertEqual(left, 200, left_body)
        self.assertEqual(right, 200, right_body)
        self.assertEqual(left_body["status"], "ok")
        self.assertEqual(right_body["status"], "ok")
        self.assertEqual(left_body["project_id"], project_id)
        self.assertEqual(right_body["project_id"], project_id)
        self.assertFalse(left_body["created"])
        self.assertEqual(self.alias_count(), 2)

    def test_unregistered_remote_on_a_confirmed_alias_conflicts(self) -> None:
        project_id = self.project("bank")
        confirmed = self.confirm(self.host, "/srv/bank", "git@github.com:Org/Repo.git", project_id)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        before = self.remote_count()
        status, body = self.resolve(
            self.host, "/srv/bank", "git@github.com:Other/Repo.git", "url", token=self.key["token"])
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "alias_conflict")
        self.assertIsNone(body["project_id"])
        self.assertIsNone(body["error"]["details"]["project_id"])
        self.assertNotIn(project_id, json.dumps(body))
        self.assertEqual(self.remote_count(), before)
        keys = {row["remote_key"] for row in self.conn.execute("SELECT remote_key FROM workspace_remotes")}
        self.assertEqual(keys, {CANONICAL})

    def test_absent_origin_keeps_a_confirmed_alias_and_does_not_touch_remotes(self) -> None:
        project_id = self.project("bank")
        confirmed = self.confirm(self.host, "/srv/bank", "git@github.com:Org/Repo.git", project_id)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        before = self.remote_count()
        status, body = self.resolve(self.host, "/srv/bank", None, "absent", token=self.key["token"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["project_id"], project_id)
        self.assertEqual(self.remote_count(), before)
        self.assertIsNone(self.conn.execute(
            "SELECT remote_key FROM workspace_remotes WHERE remote_key=''").fetchone())

    def test_tailnet_event_without_an_alias_inserts_nothing(self) -> None:
        project_id = self.project("unaliased")
        before = self.event_count()
        touched = self.conn.execute(
            "SELECT last_used_at FROM api_keys WHERE key_id=?", (self.key["key_id"],)
        ).fetchone()["last_used_at"]
        status, body = self.call(
            "POST", "/v1/events",
            {"event_type": "user.prompt", "source_system": "test", "host_id": self.host,
             "project_id": project_id, "payload": {"text": "unaliased prompt"},
             "occurred_at": "2026-09-29T12:00:00.000000Z"},
            token=self.key["token"], gateway=SECRET)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "project_not_aliased")
        self.assertEqual(self.event_count(), before)
        self.assertEqual(self.conn.execute(
            "SELECT last_used_at FROM api_keys WHERE key_id=?", (self.key["key_id"],)
        ).fetchone()["last_used_at"], touched)
        for path, payload in {
            "/v1/sessions": {"project_id": project_id, "host_id": self.host},
            "/v1/memory/hint": {
                "project_id": project_id, "native_session_id": "native", "prompt": "hint",
                "host_id": self.host,
            },
            "/v1/continuity/recent": {"host_id": self.host, "workspace_root": "/srv/bank"},
            "/v1/session-checkpoints/recover": {"host_id": self.host, "workspace_root": "/srv/bank"},
            "/v1/session-checkpoints": {
                "host_id": self.host, "project_id": project_id, "native_session_id": "native",
                "reason": "SESSION_END", "workspace_root": "/srv/bank",
            },
        }.items():
            refused, refused_body = self.call(
                "POST", path, payload, token=self.key["token"], gateway=SECRET)
            self.assertEqual(refused, 403, path)
            self.assertEqual(refused_body["error"]["code"], "project_not_aliased", path)
        self.assertEqual(self.event_count(), before)

    def test_host_mismatch_and_unknown_fields_write_nothing(self) -> None:
        before = self.alias_count()
        status, body = self.resolve(
            self.other, "/srv/bank", "git@github.com:Org/Repo.git", "url", token=self.key["token"])
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "host_mismatch")
        status, body = self.call(
            "POST", "/v1/workspaces/resolve",
            {"host_id": self.host, "root": "/srv/bank", "origin": None, "remote_kind": "absent",
             "project_id": "prj_01K742SG00CBKP25A9ETPBTRMJ"},
            token=self.key["token"], gateway=SECRET)
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertEqual(self.alias_count(), before)
        self.assertNotIn("ghp_secret", self.stored_text())

    def test_unset_flag_still_disables_tailnet_resolve(self) -> None:
        os.environ.pop("JASMINE_TAILNET_CLIENTS", None)
        status, body = self.resolve(
            self.host, "/srv/bank", None, "absent", token=self.key["token"])
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "tailnet_disabled")

    def test_confirm_accepts_a_stored_canonical_key(self) -> None:
        project_id = self.project("bank")
        first = self.confirm(self.host, "/srv/port", PORT_KEY + ".git", project_id)
        self.assertEqual(first.returncode, 0, first.stderr)
        stored = self.conn.execute("SELECT remote_key FROM workspace_remotes").fetchone()["remote_key"]
        self.assertEqual(stored, PORT_KEY)
        again = self.confirm(self.other, "/srv/port-b", stored, project_id)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.remote_count(), 1)
        plain = self.confirm(self.host, "/srv/plain", CANONICAL, project_id)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        dotted = self.confirm(self.other, "/srv/plain-b", CANONICAL + ".git", project_id)
        self.assertEqual(dotted.returncode, 0, dotted.stderr)
        keys = {row["remote_key"] for row in self.conn.execute("SELECT remote_key FROM workspace_remotes")}
        self.assertEqual(keys, {PORT_KEY, CANONICAL})

    def test_scp_query_token_is_not_stored_or_printed(self) -> None:
        project_id = self.project("bank")
        confirmed = self.confirm(self.host, "/srv/scp", SCP_TOKEN_ORIGIN, project_id)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertNotIn("ghp_secret", confirmed.stdout + confirmed.stderr)
        stored = self.conn.execute("SELECT remote_key FROM workspace_remotes").fetchone()["remote_key"]
        self.assertEqual(stored, CANONICAL)
        self.assertNotIn("ghp_secret", self.stored_text())
        repo = self.data_dir / "scp-repo"
        repo.mkdir()
        self.project("local", str(repo))
        from jasmine_core.workspace_alias import backfill_aliases

        conflicts = backfill_aliases(
            self.conn, host_id=self.host,
            origin_for=lambda root: SCP_TOKEN_ORIGIN if root == str(repo) else None)
        text = "\n".join(conflicts)
        self.assertNotIn("ghp_secret", text)
        self.assertNotIn("token=", text)
        self.assertIn(CANONICAL, text)
        keys = {row["remote_key"] for row in self.conn.execute("SELECT remote_key FROM workspace_remotes")}
        self.assertEqual(keys, {CANONICAL})
        self.assertEqual(
            self.conn.execute(
                "SELECT project_id FROM workspace_remotes WHERE remote_key=?", (CANONICAL,)
            ).fetchone()["project_id"],
            project_id)
        self.assertNotIn("ghp_secret", self.stored_text())

    def test_confirm_refuses_a_remote_owned_by_another_project(self) -> None:
        first = self.project("first")
        second = self.project("second")
        origin = "git@github.com:Org/Repo.git"
        self.assertEqual(self.confirm(self.host, "/srv/first", origin, first).returncode, 0)
        refused = self.confirm(self.other, "/srv/second", origin, second)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.alias_count(), 1)
        self.assertEqual(
            self.conn.execute("SELECT project_id FROM workspace_remotes").fetchone()["project_id"],
            first)

    def test_create_project_ignores_a_client_project_id(self) -> None:
        decoy = "prj_01K742SG00CBKP25A9ETPBTRMJ"
        before = self.conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        created = self.confirm(
            self.host, "/srv/fresh", "git@github.com:Org/Fresh.git", decoy,
            create_project="", actor_id=ACTOR)
        self.assertEqual(created.returncode, 0, created.stderr)
        minted = created.stdout.strip()
        self.assertNotEqual(minted, decoy)
        self.assertTrue(minted.startswith("prj_"))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"], before + 1)
        alias = self.conn.execute(
            "SELECT project_id, remote_key FROM workspace_aliases WHERE host_id=?", (self.host,)
        ).fetchone()
        self.assertEqual(alias["project_id"], minted)
        self.assertEqual(alias["remote_key"], "github.com/Org/Fresh")

    def test_backfill_skips_ambiguous_paths_and_foreign_remotes(self) -> None:
        shared = self.data_dir / "shared"
        shared.mkdir()
        alone = self.data_dir / "alone"
        alone.mkdir()
        one = self.project("one", str(shared))
        two = self.project("two", str(shared))
        solo = self.project("solo", str(alone))
        other = self.project("other")
        self.conn.execute(
            "INSERT INTO workspace_remotes (remote_key, project_id, created_at) VALUES (?, ?, ?)",
            ("github.com/Other/Repo", other, "2026-11-12T00:00:00.000000Z"),
        )
        from jasmine_core.workspace_alias import backfill_aliases

        def origins(root: str) -> str | None:
            if root == str(alone):
                return "github.com/Other/Repo"
            return None

        conflicts = backfill_aliases(self.conn, host_id=self.host, origin_for=origins)
        text = "\n".join(conflicts)
        self.assertIn(str(shared), text)
        self.assertIn(one, text)
        self.assertIn(two, text)
        self.assertEqual(
            {row["remote_key"] for row in self.conn.execute("SELECT remote_key FROM workspace_remotes")},
            {"github.com/Other/Repo"})
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM workspace_aliases WHERE root=?", (str(shared),)).fetchone())
        registered = self.conn.execute(
            "SELECT project_id FROM workspace_remotes WHERE remote_key=?",
            ("github.com/Other/Repo",)).fetchone()["project_id"]
        self.assertEqual(registered, other)
        alias = self.conn.execute(
            "SELECT project_id, remote_key FROM workspace_aliases WHERE root=?",
            (str(alone),)).fetchone()
        self.assertEqual(alias["project_id"], solo)
        self.assertIsNone(alias["remote_key"])
        self.assertIn("github.com/Other/Repo", text)

    def test_backfill_script_registers_one_local_origin_without_userinfo(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        repo = self.data_dir / "repo"
        repo.mkdir()
        init = subprocess.run(
            ["git", "init"], cwd=repo, capture_output=True, text=True, check=False)
        self.assertEqual(init.returncode, 0, init.stderr)
        added = subprocess.run(
            ["git", "remote", "add", "origin", SECRET_ORIGIN],
            cwd=repo, capture_output=True, text=True, check=False)
        self.assertEqual(added.returncode, 0, added.stderr)
        project_id = self.project("repo", str(repo))
        completed = subprocess.run(
            [sys.executable, str(BACKFILL), "--db", str(self.db_path), "--host-id", self.host],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("ghp_secret", completed.stdout + completed.stderr)
        stored = self.conn.execute(
            "SELECT project_id FROM workspace_remotes WHERE remote_key=?", (CANONICAL,)
        ).fetchone()
        self.assertEqual(stored["project_id"], project_id)
        self.assertNotIn("ghp_secret", self.stored_text())

    def test_mismatched_remote_project_is_rejected_by_the_trigger(self) -> None:
        project_id = self.project("bank")
        other = self.project("other")
        self.conn.execute(
            "INSERT INTO workspace_remotes (remote_key, project_id, created_at) VALUES (?, ?, ?)",
            (CANONICAL, project_id, "2026-11-12T00:00:00.000000Z"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO workspace_aliases"
                " (host_id, root, project_id, remote_key, confirmed, created_at)"
                " VALUES (?, '/srv/nope', ?, ?, 1, '2026-11-12T00:00:00.000000Z')",
                (self.host, other, CANONICAL),
            )


if __name__ == "__main__":
    unittest.main()
