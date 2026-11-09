"""ADR 0042: Antigravity conversations, the work record and the resume block.

Real Core HTTP; Antigravity's transcript is a fixture in its own layout
(`~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/logs/transcript_full.jsonl`).
"""
import io
import json
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_codex_session_capture import ConversationCapture
from jasmine_core import auth, db
from jasmine_core.capture import antigravity_session, install_antigravity, memory_hint, worklog_client
from jasmine_core.hosthook.binding import load_device
from jasmine_core.hosthook.install import InstallError


class AntigravityCapture(ConversationCapture):
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
        body.pop("project_id")
        body["memory_key_file"] = str(self.memory_key)
        body["spool_dir"] = str(self.native_root / "spool")
        self.binding_path.write_text(json.dumps(body))
        self.binding_path.chmod(0o600)
        memory_hint.set_enabled(self.binding_path, True)
        self.device = load_device(self.binding_path)
        self.gemini = self.native_root / "gemini"
        self.receipts = self.native_root / "antigravity-receipts.jsonl"
        self.new_session()

    # -- Antigravity's side ------------------------------------------------------

    def new_session(self):
        self.sid = str(uuid.uuid4())
        self.transcript = (self.gemini / "antigravity-cli" / "brain" / self.sid / ".system_generated" / "logs"
                           / "transcript_full.jsonl")
        self.transcript.parent.mkdir(parents=True, mode=0o700)
        self.transcript.write_text("")
        self.steps = 0

    def step(self, **body):
        index = self.steps
        self.steps += 1
        row = {"step_index": index, "status": "DONE", "created_at": f"2026-11-09T02:{index:02d}:00Z", **body}
        with self.transcript.open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return index

    def user(self, text, source="USER_EXPLICIT"):
        return self.step(type="USER_INPUT", source=source,
                         content=f"<USER_REQUEST>\n{text}\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nlocal time\n</ADDITIONAL_METADATA>")

    def answer(self, text, **extra):
        return self.step(type="PLANNER_RESPONSE", source="MODEL", content=text, **extra)

    def payload(self, **extra):
        return {"conversationId": self.sid, "workspacePaths": [str(self.native_root)],
                "transcriptPath": str(self.transcript), "modelName": "auto", **extra}

    def capture(self, event, **extra):
        return antigravity_session.capture_device(event, self.payload(**extra), self.device, self.gemini)

    def main(self, event, **extra):
        argv = ["agy-hook", "--event", event, "--device-binding", str(self.binding_path),
                "--receipts", str(self.receipts), "--gemini-home", str(self.gemini)]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(self.payload(**extra)))), \
                redirect_stdout(out):
            code = antigravity_session.main()
        rows = self.receipts.read_text().splitlines() if self.receipts.exists() else []
        return code, json.loads(out.getvalue()), json.loads(rows[-1]) if rows else None

    def prompt(self, text, **extra):
        first = self.user(text) + 1
        return self.main("PreInvocation", invocationNum=0, initialNumSteps=first, lastUserInput=text, **extra)

    def exchange(self, text, reply):
        self.assertEqual(self.prompt(text)[2]["status"], "accepted")
        self.answer(reply)
        self.assertEqual(self.main("Stop", terminationReason="NO_TOOL_CALL", lastUserInput=text)[2]["status"],
                         "accepted")

    def events(self, session_id):
        _, page = self.call("GET", "/v1/events?session_id=" + session_id, token=self.admin_token)
        return page["events"]

    # -- conversations -------------------------------------------------------------

    def test_prompt_and_reply_are_recorded_verbatim(self):
        start = self.capture("SessionStart")
        first = self.user("  把封面\n换成第二张") + 1
        prompt = self.capture("PreInvocation", invocationNum=0, initialNumSteps=first, lastUserInput="  把封面\n换成第二张")
        self.answer("先看一下目录。", tool_calls=[{"name": "run_command", "args": {"CommandLine": "ls"}}])
        self.step(type="GENERIC", source="MODEL", content="tool output")
        self.answer("已换成第二张。")
        reply = self.capture("Stop", terminationReason="NO_TOOL_CALL")
        self.assertEqual({start["session_id"], prompt["session_id"], reply["session_id"]}, {start["session_id"]})
        events = self.events(start["session_id"])
        self.assertEqual([e["event_type"] for e in events], ["session.started", "user.prompt", "assistant.message"])
        self.assertEqual([e["payload"]["text"] for e in events[1:]], ["  把封面\n换成第二张", "已换成第二张。"])
        self.assertEqual([e["actor_kind"] for e in events], ["system", "human", "system"])
        self.assertTrue(all(e["source_system"] == antigravity_session.SOURCE for e in events))
        self.assertEqual(events[1]["payload"]["native_timestamp"], "2026-11-09T02:00:00Z")
        again = self.capture("PreInvocation", invocationNum=0, initialNumSteps=first, lastUserInput="  把封面\n换成第二张")
        self.assertTrue(again["replayed"])

    def test_only_turn_boundaries_and_finished_replies_count(self):
        self.user("hi")
        code, out, row = self.main("PreInvocation", invocationNum=2, initialNumSteps=1, lastUserInput="hi")
        self.assertEqual((code, out, row), (0, {}, None))  # no Core request, no receipt
        self.answer("partial")
        self.assertEqual(self.capture("Stop", terminationReason="USER_CANCELED")["status"], "ignored")
        self.assertEqual(self.capture("Stop", terminationReason="ERROR")["status"], "ignored")

    def test_unverified_turns_record_nothing(self):
        first = self.user("scheduled", source="SYSTEM_SCHEDULED") + 1
        self.assertEqual(self.capture("PreInvocation", invocationNum=0, initialNumSteps=first,
                                      lastUserInput="scheduled")["status"], "ignored")
        first = self.user("真的话") + 1
        self.assertEqual(self.capture("PreInvocation", invocationNum=0, initialNumSteps=first,
                                      lastUserInput="不是这句")["status"], "ignored")
        elsewhere = self.native_root / "elsewhere.jsonl"
        elsewhere.write_text(self.transcript.read_text())
        self.assertEqual(self.capture("PreInvocation", invocationNum=0, initialNumSteps=first, lastUserInput="真的话",
                                      transcriptPath=str(elsewhere))["status"], "ignored")
        self.transcript.chmod(0o666)
        self.assertEqual(self.capture("PreInvocation", invocationNum=0, initialNumSteps=first,
                                      lastUserInput="真的话")["status"], "ignored")
        with self.assertRaises(ValueError):
            self.capture("SessionStart", conversationId="not-a-uuid")
        _, events = self.call("GET", "/v1/events?limit=100", token=self.admin_token)
        self.assertFalse([e for e in events["events"] if e["source_system"] == antigravity_session.SOURCE])

    # -- ADR 0039 --------------------------------------------------------------------

    def test_a_new_session_starts_with_the_resume_block(self):
        self.main("SessionStart")
        self.exchange("把第四段的转场改成硬切", "已改成硬切。")
        self.exchange("再把字幕调大一号", "字幕已调到 48 号。")
        self.new_session()
        _, out, row = self.main("SessionStart")
        step = out["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("上一次：Antigravity", step)
        self.assertIn("再把字幕调大一号", step)
        self.assertEqual((row["worklog"]["status"], row["recent_activity"]["trigger"]), ("ok", "session_start"))
        self.assertNotIn("再把字幕调大一号", json.dumps(row, ensure_ascii=False))
        # The first prompt does not repeat it; asking about an agent does.
        with patch.object(worklog_client, "recent") as asked:
            self.prompt("把封面换成第三张")
        asked.assert_not_called()
        _, out, _ = self.prompt("antigravity 刚才在做什么")
        self.assertIn("只包含 Antigravity 的会话", out["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_nothing_is_injected_without_the_opt_in(self):
        self.main("SessionStart")
        self.exchange("把第四段的转场改成硬切", "已改成硬切。")
        memory_hint.set_enabled(self.binding_path, False)
        self.new_session()
        _, out, row = self.main("SessionStart")
        self.assertEqual(out, {})
        self.assertNotIn("recent_activity", row)

    # -- the installer -----------------------------------------------------------------

    def test_installer_owns_one_named_hook(self):
        home = self.native_root / "gemini-config"
        home.mkdir()
        target = home / "hooks.json"
        target.write_text(json.dumps({"lint-checker": {"PostToolUse": []}}))
        launcher = self.native_root / "launcher.py"
        launcher.write_text("pass")
        opts = {"python": sys.executable, "launcher": launcher, "gemini_home": self.gemini}
        with self.assertRaises(InstallError):
            install_antigravity.install(home, self.binding_path, self.receipts, **opts)
        memory_hint.set_enabled(self.binding_path, False)
        with self.assertRaises(InstallError):
            install_antigravity.set_memory_hint(home, self.binding_path, enabled=True, confirmed=True)
        self.assertEqual(install_antigravity.install(home, self.binding_path, self.receipts, confirmed=True, **opts)["status"],
                         "installed")
        self.assertEqual(install_antigravity.install(home, self.binding_path, self.receipts, confirmed=True, **opts)["status"],
                         "unchanged")
        document = json.loads(target.read_text())
        self.assertEqual(document["lint-checker"], {"PostToolUse": []})
        entry = document[install_antigravity.NAME]
        self.assertEqual(sorted(entry), ["PreInvocation", "SessionStart", "Stop"])  # never PreToolUse
        self.assertTrue(entry["PreInvocation"][0]["command"].endswith("--event PreInvocation"))
        self.assertEqual(install_antigravity.set_memory_hint(home, self.binding_path, enabled=True,
                                                             confirmed=True)["status"], "memory_hint_enabled")
        self.assertEqual(install_antigravity.install(home, self.binding_path, self.receipts, confirmed=True,
                                                     disable=True, **opts)["status"], "disabled")
        self.assertEqual(json.loads(target.read_text()), {"lint-checker": {"PostToolUse": []}})
        self.assertFalse(load_device(self.binding_path).memory_hint)
        target.unlink()
        target.symlink_to(self.native_root / "other.json")
        with self.assertRaises(InstallError):
            install_antigravity.install(home, self.binding_path, self.receipts, confirmed=True, **opts)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(AntigravityCapture(name) for name in AntigravityCapture.__dict__ if name.startswith("test_"))
