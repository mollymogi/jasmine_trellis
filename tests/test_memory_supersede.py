"""Write-time supersession: only a full replacement of an older observed memory retires it."""
import sqlite3
import unittest
from types import SimpleNamespace

from jasmine_core.memory import supersede
from jasmine_core.memory import brain
from jasmine_core.memory.supersede import JudgeUnavailable, Superseder

ACTOR, HOST, PROJECT = "act_x", "hst_x", "prj_x"


class _Store:
    def __init__(self, conn):
        self.conn = conn

    def item(self, memory_id):
        row = self.conn.execute("SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
        return dict(row) if row else None


class _Adapter:
    def __init__(self):
        self.hits = []

    def recall(self, request):
        return SimpleNamespace(items=tuple(
            SimpleNamespace(memory_id=m, scope_kind=scope, relevance=rel) for m, scope, rel in self.hits))


class _Lifecycle:
    def __init__(self, conn):
        self.conn, self.retired = conn, []

    def obsolete(self, memory_id, body, **_):
        self.retired.append((memory_id, body["reason"]))
        self.conn.execute("UPDATE memory_items SET status='OBSOLETE' WHERE memory_id=?", (memory_id,))


class SupersedeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE core_meta (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("CREATE TABLE memory_items (memory_id TEXT, project_id TEXT, admission TEXT,"
                          " status TEXT, engine_state TEXT, memory_type TEXT, sanitized_text TEXT,"
                          " pinned INTEGER, revision INTEGER, created_at TEXT)")
        self.conn.execute("INSERT INTO core_meta VALUES (?, ?)", (supersede.CURSOR_KEY, "2026-11-21T00:00:00Z|"))
        self.adapter, self.life, self.calls = _Adapter(), _Lifecycle(self.conn), []
        self.answer = []
        self.clock = 1000.0

        def judge(new, old):
            self.calls.append((new["memory_id"], [o["memory_id"] for o in old]))
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        self.sup = Superseder(self.conn, _Store(self.conn), self.adapter, self.life,
                              actor_id=ACTOR, host_id=HOST, judge=judge, now=lambda: self.clock)

    def add(self, memory_id, at, text="x", admission="OBSERVED", pinned=0):
        self.conn.execute("INSERT INTO memory_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (memory_id, PROJECT, admission, "ACTIVE", "RETAINED", "OBSERVED_DECISION",
                           text, pinned, 1, at))

    def test_full_replacement_retires_the_older_memory(self):
        self.add("mem_old", "2026-11-20T00:00:00Z", "生图默认用 gpt image2")
        self.add("mem_new", "2026-11-21T01:00:00Z", "生图改用 flux")
        self.adapter.hits = [("mem_new", "project", 0.99), ("mem_old", "project", 0.8)]
        self.answer = [{"id": "mem_old", "relation": "REPLACES", "fully_covered": True, "reason": "改用 flux"}]
        result = self.sup.tick()
        self.assertEqual(result["retired"], ["mem_old"])
        self.assertTrue(self.life.retired[0][1].startswith("superseded_by:mem_new"))
        self.assertEqual(self.sup.tick()["status"], "idle")

    def test_partial_or_non_replacing_judgements_retire_nothing(self):
        self.add("mem_a", "2026-11-20T00:00:00Z")
        self.add("mem_b", "2026-11-20T00:00:01Z")
        self.add("mem_new", "2026-11-21T01:00:00Z")
        self.adapter.hits = [("mem_a", "project", 0.8), ("mem_b", "project", 0.8)]
        self.answer = [{"id": "mem_a", "relation": "REPLACES", "fully_covered": False},
                       {"id": "mem_b", "relation": "CONFLICTS", "fully_covered": True}]
        self.assertEqual(self.sup.tick()["retired"], [])
        self.assertEqual(self.life.retired, [])

    def test_only_older_relevant_unpinned_observed_project_memories_are_candidates(self):
        self.add("mem_new", "2026-11-21T01:00:00Z")
        self.add("mem_low", "2026-11-20T00:00:00Z")
        self.add("mem_auth", "2026-11-20T00:00:00Z", admission="AUTHORITY")
        self.add("mem_pin", "2026-11-20T00:00:00Z", pinned=1)
        self.add("mem_glob", "2026-11-20T00:00:00Z")
        self.add("mem_later", "2026-11-22T00:00:00Z")
        self.add("mem_ok", "2026-11-20T00:00:00Z")
        self.adapter.hits = [("mem_low", "project", 0.3), ("mem_auth", "project", 0.9),
                             ("mem_pin", "project", 0.9), ("mem_glob", "global", 0.9),
                             ("mem_later", "project", 0.9), ("mem_ok", "project", 0.9)]
        self.sup.tick()
        self.assertEqual(self.calls, [("mem_new", ["mem_ok"])])

    def test_no_candidates_skips_the_model(self):
        self.add("mem_new", "2026-11-21T01:00:00Z")
        self.assertEqual(self.sup.tick()["candidates"], 0)
        self.assertEqual(self.calls, [])

    def test_an_outage_holds_the_cursor_and_backs_off(self):
        self.add("mem_old", "2026-11-20T00:00:00Z")
        self.add("mem_new", "2026-11-21T01:00:00Z")
        self.adapter.hits = [("mem_old", "project", 0.9)]
        self.answer = JudgeUnavailable("URLError")
        self.assertEqual(self.sup.tick()["status"], "judge_unavailable")
        self.assertEqual(self.sup.tick()["status"], "cooling")
        self.clock += supersede.COOLDOWN_MIN + 1
        self.answer = [{"id": "mem_old", "relation": "REPLACES", "fully_covered": True}]
        self.assertEqual(self.sup.tick()["retired"], ["mem_old"])

    def test_any_http_refusal_backs_off(self):
        import io, urllib.error
        from unittest.mock import patch
        error = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b""))
        brain._bench.clear()
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(JudgeUnavailable):
                supersede.magpie_judge({"sanitized_text": "a", "memory_type": "t", "created_at": "x"}, [])

    def test_gateway_follows_magpie_gateway_file(self):
        import json, tempfile, pathlib
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as home:
            cfg = pathlib.Path(home, ".config/magpie"); cfg.mkdir(parents=True)
            cfg.joinpath("gateway.json").write_text(json.dumps({"addr": "127.0.0.1:4000"}))
            with patch("pathlib.Path.home", return_value=pathlib.Path(home)):
                self.assertEqual(supersede.gateway_endpoint(), "http://127.0.0.1:4000/v1/chat/completions")
                cfg.joinpath("gateway.json").write_text(json.dumps({"addr": "10.0.0.1:4000"}))
                self.assertIn("127.0.0.1:3425", supersede.gateway_endpoint())

    def test_memories_before_the_cursor_are_never_reconsidered(self):
        self.add("mem_old", "2026-11-19T00:00:00Z")
        self.add("mem_older", "2026-11-18T00:00:00Z")
        self.adapter.hits = [("mem_older", "project", 0.9)]
        self.assertEqual(self.sup.tick()["status"], "idle")


if __name__ == "__main__":
    unittest.main()
