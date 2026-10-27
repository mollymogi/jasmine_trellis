"""prepare-agent-capture.py: the Memory hint key for a binding whose key metadata is missing.

An older setup created the Codex device binding without
`credentials/codex-conversation-keys.json` (found on local-item6, 2026-10-27).
The script must find the capture actors from the binding's own key files, or
stop with nothing written.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from jasmine_core import auth, db, ids, registry
from jasmine_core.migrations import migrate

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deployment/prepare-agent-capture.py"
HOST = ids.new_id("hst")


def load_script():
    spec = importlib.util.spec_from_file_location("prepare_agent_capture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MissingMetadata(unittest.TestCase):
    def setUp(self):
        self.prepare = load_script()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.credentials = self.root / "credentials"
        self.credentials.mkdir(mode=0o700)
        self.database = self.root / "core.db"
        conn = db.connect(self.database)
        migrate(conn)
        reg = registry.Registry(conn)
        self.system, self.human, self.other = ids.new_id("act"), ids.new_id("act"), ids.new_id("act")
        with db.transaction(conn):
            reg.upsert_actor(self.system, kind="system", display_name="Codex capture")
            reg.upsert_actor(self.human, kind="human", display_name="browser human")
            reg.upsert_actor(self.other, kind="system", display_name="unrelated system")
        self.keys = {
            "system": auth.Auth(conn).issue_key(actor_id=self.system, label="daily-codex-conversation-system",
                                                scopes=["objects:write", "events:write"]),
            "human": auth.Auth(conn).issue_key(actor_id=self.human, label="daily-codex-conversation-human",
                                               scopes=["events:write"]),
        }
        conn.close()
        for kind, key in self.keys.items():
            self.write_key(kind, key["token"])
        self.binding = self.root / "codex-conversation-device.json"
        self.binding.write_text(json.dumps({
            "version": 1, "core_url": "http://127.0.0.1:18787", "host_id": HOST,
            "api_key_file": str(self.credentials / "codex-conversation-system.key"),
            "human_api_key_file": str(self.credentials / "codex-conversation-human.key"),
            "inject_context": False, "on_unavailable": "allow"}))
        self.binding.chmod(0o600)
        self.runtime = {"database": str(self.database)}
        self.metadata = self.credentials / "codex-conversation-keys.json"
        self.memory_key = self.credentials / "codex-conversation-memory.key"

    def write_key(self, kind, token):
        path = self.credentials / f"codex-conversation-{kind}.key"
        path.write_text(token + "\n")
        path.chmod(0o600)

    def run_prepare(self):
        self.prepare._memory_key(self.runtime, "codex", self.binding, self.credentials)

    def hint_key_actor(self):
        conn = db.connect(self.database)
        try:
            return auth.Auth(conn).authenticate("Bearer " + self.memory_key.read_text().strip())
        finally:
            conn.close()

    def test_metadata_is_reconstructed_from_the_bindings_own_keys(self):
        self.run_prepare()
        meta = json.loads(self.metadata.read_text())
        self.assertEqual((meta["system_actor_id"], meta["human_actor_id"]), (self.system, self.human))
        self.assertEqual(meta["system"]["key_id"], self.keys["system"]["key_id"])
        self.assertEqual(meta["reconstructed_from"], "device binding key files")
        text = self.metadata.read_text()
        self.assertNotIn(self.keys["system"]["token"], text)
        self.assertNotIn(self.keys["human"]["token"], text)
        self.assertEqual(self.metadata.stat().st_mode & 0o777, 0o600)
        principal = self.hint_key_actor()
        self.assertEqual(principal.actor_id, self.system)
        self.assertEqual(set(principal.scopes), {"memory:recall"})
        self.assertEqual(json.loads(self.binding.read_text())["memory_key_file"], str(self.memory_key))
        # Idempotent: nothing is issued or rewritten a second time.
        before = (self.metadata.read_bytes(), self.memory_key.read_bytes(), self.binding.read_bytes())
        self.run_prepare()
        self.assertEqual(before, (self.metadata.read_bytes(), self.memory_key.read_bytes(), self.binding.read_bytes()))

    def test_existing_metadata_is_used_as_is(self):
        self.metadata.write_text(json.dumps({"system_actor_id": self.other, "human_actor_id": self.human}))
        self.metadata.chmod(0o600)
        self.run_prepare()
        self.assertEqual(self.hint_key_actor().actor_id, self.other)
        self.assertNotIn("reconstructed_from", json.loads(self.metadata.read_text()))

    def assert_refused(self, message):
        before = self.binding.read_bytes()
        with self.assertRaisesRegex(ValueError, message):
            self.run_prepare()
        self.assertFalse(self.metadata.exists())
        self.assertFalse(self.memory_key.exists())
        self.assertEqual(self.binding.read_bytes(), before)

    def test_a_revoked_or_unknown_system_key_is_refused(self):
        conn = db.connect(self.database)
        with db.transaction(conn):
            conn.execute("UPDATE api_keys SET revoked_at='2026-10-27T00:00:00Z' WHERE key_id=?",
                         (self.keys["system"]["key_id"],))
        conn.close()
        self.assert_refused("system capture key is unknown or revoked")
        self.write_key("system", "not-a-real-token")
        self.assert_refused("system capture key is unknown or revoked")

    def test_keys_of_the_wrong_actor_kind_or_scope_are_refused(self):
        # The prompt key in the system slot: a human actor.
        self.write_key("system", self.keys["human"]["token"])
        self.assert_refused("does not belong to a system capture actor")

    def test_a_system_key_without_capture_scopes_is_refused(self):
        conn = db.connect(self.database)
        narrow = auth.Auth(conn).issue_key(actor_id=self.system, label="narrow", scopes=["events:write"])
        conn.close()
        self.write_key("system", narrow["token"])
        self.assert_refused("does not belong to a system capture actor")


if __name__ == "__main__":
    unittest.main()
