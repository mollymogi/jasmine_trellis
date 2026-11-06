"""ADR 0039 for Grok: the work record, and the resume block delivered on a tool call.

Grok discards what SessionStart and UserPromptSubmit hooks print, so the block is
staged and handed to the model by the next PreToolUse. Over a real Core.
"""
import io
import json
import time
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

from test_grok_session_capture import GrokCapture
from jasmine_core import auth, db
from jasmine_core.capture import grok_session, install_grok, memory_hint, worklog_client
from jasmine_core.hosthook.binding import load_device


class GrokResume(GrokCapture):
    def setUp(self):
        super().setUp()
        conn = db.connect(self.db_path)
        system = conn.execute("SELECT actor_id FROM api_keys WHERE label='capture'").fetchone()[0]
        key = auth.Auth(conn).issue_key(actor_id=system, label="memory hint", scopes=["memory:recall"])["token"]
        conn.close()
        self.memory_key = self.native_root / "memory.key"
        self.memory_key.write_text(key)
        self.memory_key.chmod(0o600)
        body = json.loads(self.binding_path.read_text())
        body["memory_key_file"] = str(self.memory_key)
        body["spool_dir"] = str(self.native_root / "spool")
        self.binding_path.write_text(json.dumps(body))
        self.binding_path.chmod(0o600)
        memory_hint.set_enabled(self.binding_path, True)
        self.device = load_device(self.binding_path)
        self.receipts = self.native_root / "grok-receipts.jsonl"

    # -- helpers ---------------------------------------------------------------

    def packet(self, event, **extra):
        names = {"PreCompact": "pre_compact", "PreToolUse": "pre_tool_use"}
        if event not in names:
            return super().packet(event, **extra)
        body = {"hookEventName": names[event], "hook_event_name": event, "sessionId": self.sid,
                "cwd": str(self.native_root), "timestamp": "2026-10-22T09:00:00Z", **extra}
        if event == "PreCompact":
            body["transcriptPath"] = str(self.folder / "updates.jsonl")
        return body

    def main(self, event, **extra):
        argv = ["grok", "--device-binding", str(self.binding_path), "--receipts", str(self.receipts),
                "--grok-home", str(self.home)]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(self.packet(event, **extra)))), \
                redirect_stdout(out):
            code = grok_session.main()
        rows = self.receipts.read_text().splitlines() if self.receipts.exists() else []
        return code, out.getvalue().strip(), json.loads(rows[-1]) if rows else None

    def exchange(self, text, reply):
        self.turn_id = str(uuid.uuid4())
        self.assertEqual(self.main("UserPromptSubmit", prompt=text, promptId=self.turn_id)[0], 0)
        self.main("Stop", lastAssistantMessage=reply, stopHookActive=False, promptId=self.turn_id)

    def new_session(self):
        self.sid = str(uuid.uuid4())
        self.folder = self.folder.parent / self.sid
        self.folder.mkdir(mode=0o700)
        self.summary = self.folder / "summary.json"
        self.summary.write_text(json.dumps({"info": {"id": self.sid, "cwd": str(self.native_root)}}))

    def finished_session(self):
        self.main("SessionStart", source="new")
        self.exchange("把第四段的转场改成硬切", "已改成硬切。")
        self.exchange("再把字幕调大一号", "字幕已调到 48 号。")
        return self.main("SessionEnd", reason="exit")

    def checkpoints(self):
        code, page = self.call("GET", "/v1/session-checkpoints", token=self.admin_token)
        self.assertEqual(code, 200, page)
        return page["session_checkpoints"]

    # -- the work record ---------------------------------------------------------

    def test_session_end_and_precompact_write_checkpoints(self):
        _, out, row = self.finished_session()
        self.assertEqual(out, "{}")
        self.assertEqual(row["worklog"]["status"], "recorded", row)
        self.main("SessionStart", source="load")
        _, out, row = self.main("PreCompact", trigger="auto")
        self.assertEqual((out, row["native_hook_event"], row["worklog"]["reason"]), ("{}", "PreCompact", "PRE_COMPACT"))
        self.assertEqual(sorted(c["reason"] for c in self.checkpoints()), ["PRE_COMPACT", "SESSION_END"])
        # PreCompact is a Checkpoint, not a conversation Event.
        _, events = self.call("GET", "/v1/events?limit=100", token=self.admin_token)
        self.assertNotIn("pre_compact", json.dumps([e["payload"] for e in events["events"]]))

    # -- the resume block --------------------------------------------------------

    def test_a_new_session_gets_the_block_on_its_first_tool_call_once(self):
        self.finished_session()
        self.new_session()
        _, out, row = self.main("SessionStart", source="new")
        self.assertEqual(out, "{}")  # Grok would discard it anyway
        self.assertEqual(row["recent_activity"]["trigger"], "session_start")
        self.assertTrue(row["staged"])
        self.assertNotIn("再把字幕调大一号", json.dumps(row, ensure_ascii=False))
        _, out, row = self.main("PreToolUse", toolName="read_file", toolInput={"path": "a"})
        context = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "PreToolUse")
        self.assertIn("上一次：Grok", context["additionalContext"])
        self.assertIn("再把字幕调大一号", context["additionalContext"])
        self.assertNotIn("decision", json.loads(out))  # never decides the call
        self.assertEqual(row["status"], "delivered")
        before = self.receipts.read_text()
        self.assertEqual(self.main("PreToolUse", toolName="read_file")[1], "{}")
        self.assertEqual(self.receipts.read_text(), before)  # no receipt per tool call

    def test_a_start_staged_before_grok_persisted_it_still_gets_the_block(self):
        self.finished_session()
        self.new_session()
        self.summary.unlink()
        start = self.packet("SessionStart", source="new")
        start.pop("transcriptPath")
        with patch.object(GrokResume, "packet", lambda self, event, **extra: start):
            self.assertEqual(self.main("SessionStart")[2]["status"], "pending")
        self.summary.write_text(json.dumps({"info": {"id": self.sid, "cwd": str(self.native_root)}}))
        _, _, row = self.main("UserPromptSubmit", prompt="这首歌这次发布到哪个账号？", promptId=str(uuid.uuid4()))
        self.assertEqual((row["start_source"], row["recent_activity"]["trigger"]), ("new", "session_start"))
        self.assertIn("再把字幕调大一号", json.loads(self.main("PreToolUse")[1])["hookSpecificOutput"]["additionalContext"])

    def test_a_prompt_asking_about_recent_work_is_staged(self):
        self.finished_session()
        self.new_session()
        self.main("SessionStart", source="load")
        _, _, row = self.main("UserPromptSubmit", prompt="grok 刚才在做什么", promptId=str(uuid.uuid4()))
        self.assertEqual(row["recent_activity"]["trigger"], "prompt")
        self.assertIn("只包含 Grok 的会话", json.loads(self.main("PreToolUse")[1])["hookSpecificOutput"]["additionalContext"])
        # An ordinary prompt stages no block.
        with patch.object(worklog_client, "recent") as asked:
            self.main("UserPromptSubmit", prompt="把封面换成第二张", promptId=str(uuid.uuid4()))
        asked.assert_not_called()

    def test_nothing_is_staged_without_the_opt_in(self):
        self.finished_session()
        memory_hint.set_enabled(self.binding_path, False)
        self.new_session()
        _, _, row = self.main("SessionStart", source="new")
        self.assertNotIn("recent_activity", row)
        self.assertEqual(self.main("PreToolUse")[1], "{}")

    # -- the installer -----------------------------------------------------------

    def test_installer_adds_the_delivery_hook_only_with_the_hint(self):
        launcher = self.native_root / "launcher.py"
        launcher.write_text("pass")
        opts = {"python": "/usr/bin/python3", "launcher": launcher}
        target = self.home / "hooks" / install_grok.NAME
        events = lambda: sorted(json.loads(target.read_text())["hooks"])
        memory_hint.set_enabled(self.binding_path, False)
        with self.assertRaises(install_grok.InstallError):
            install_grok.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=True, confirmed=True, **opts)
        install_grok.install(self.home, self.binding_path, self.receipts, confirmed=True, **opts)
        self.assertEqual(events(), ["PreCompact", "SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"])
        self.assertEqual(install_grok.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=True,
                                                      confirmed=True, **opts)["status"], "memory_hint_enabled")
        self.assertIn("PreToolUse", events())
        handler = json.loads(target.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(handler["timeout"], 3)
        self.assertNotIn("matcher", json.loads(target.read_text())["hooks"]["PreToolUse"][0])
        install_grok.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=False, confirmed=True, **opts)
        self.assertNotIn("PreToolUse", events())
        install_grok.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=True, confirmed=True, **opts)
        install_grok.install(self.home, self.binding_path, self.receipts, confirmed=True, disable=True, **opts)
        self.assertFalse(load_device(self.binding_path).memory_hint)  # the hint never outlives capture


class StagedContext(GrokResume):
    def test_parts_merge_and_a_stale_block_is_dropped(self):
        spool = self.native_root / "spool"
        worklog_client.stage(spool, self.sid, resume="R1", hint="H1")
        worklog_client.stage(spool, self.sid, hint="H2")
        self.assertEqual(worklog_client.take_staged(spool, self.sid), "R1\n\nH2")
        self.assertIsNone(worklog_client.take_staged(spool, self.sid))
        worklog_client.stage(spool, self.sid, resume="old")
        later = time.time() + worklog_client.STAGED_SECONDS + 1
        self.assertIsNone(worklog_client.take_staged(spool, self.sid, now=later))
        self.assertFalse(worklog_client.stage(spool, "../escape", resume="x"))
        self.assertIsNone(worklog_client.take_staged(spool, "../escape"))


def load_tests(loader, tests, pattern):
    import unittest
    suite = unittest.TestSuite()
    for case in (GrokResume, StagedContext):
        suite.addTests(case(name) for name in case.__dict__ if name.startswith("test_"))
    return suite
