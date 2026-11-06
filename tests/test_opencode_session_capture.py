"""ADR 0040: OpenCode conversations, the work record and the resume block.

Real Core HTTP; OpenCode's own database is a small fixture with its tables. The
plugin itself is checked by running it under Node with a stand-in launcher.
"""
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_codex_session_capture import ConversationCapture
from jasmine_core import auth, db
from jasmine_core.capture import install_opencode, memory_hint, opencode_session
from jasmine_core.hosthook.binding import load_device
from jasmine_core.hosthook.install import InstallError

SCHEMA = """
CREATE TABLE session (id text PRIMARY KEY, parent_id text, directory text NOT NULL);
CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL, data text NOT NULL);
CREATE TABLE part (id text PRIMARY KEY, message_id text NOT NULL, session_id text NOT NULL, data text NOT NULL);
"""


class OpenCodeCapture(ConversationCapture):
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
        self.data = self.native_root / "opencode-data"
        self.data.mkdir(mode=0o700)
        self.database = self.data / "opencode.db"
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript(SCHEMA)
        self.receipts = self.native_root / "opencode-receipts.jsonl"
        self.seq = 0
        self.sid = self.new_session()

    # -- OpenCode's side ---------------------------------------------------------

    def ident(self, prefix):
        self.seq += 1
        return f"{prefix}_{self.seq:012x}AbCdEfGhIjKlMn"

    def new_session(self, parent=None, directory=None):
        sid = self.ident("ses")
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO session VALUES (?,?,?)", (sid, parent, str(directory or self.native_root)))
        return sid

    def reply(self, text, *, sid=None, finished=True, extra_parts=()):
        mid = self.ident("msg")
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO message VALUES (?,?,?)", (mid, sid or self.sid, json.dumps(
                {"role": "assistant", "time": {"created": 1, **({"completed": 2} if finished else {})}})))
            for part in [{"type": "text", "text": text}, *extra_parts]:
                conn.execute("INSERT INTO part VALUES (?,?,?,?)", (self.ident("prt"), mid, sid or self.sid, json.dumps(part)))
        return mid

    def packet(self, event, **extra):
        return {"event": event, "sessionID": self.sid, "timestamp": "2026-11-06T09:00:00.000Z", **extra}

    def capture(self, event, **extra):
        return opencode_session.capture_device(self.packet(event, **extra), self.device, self.data)

    def main(self, event, **extra):
        argv = ["opencode", "--device-binding", str(self.binding_path), "--receipts", str(self.receipts),
                "--opencode-data", str(self.data)]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(self.packet(event, **extra)))), \
                redirect_stdout(out):
            code = opencode_session.main()
        return code, json.loads(out.getvalue())["context"], json.loads(self.receipts.read_text().splitlines()[-1])

    def exchange(self, text, answer):
        self.assertEqual(self.main("chat.message", messageID=self.ident("msg"), text=text)[2]["status"], "accepted")
        self.assertEqual(self.main("session.idle", messageID=self.reply(answer))[2]["status"], "accepted")

    def events(self, session_id):
        _, page = self.call("GET", "/v1/events?session_id=" + session_id, token=self.admin_token)
        return page["events"]

    # -- conversations -------------------------------------------------------------

    def test_prompt_and_reply_are_recorded_verbatim_under_the_workspace(self):
        start = self.capture("session.created")
        prompt = self.capture("chat.message", messageID=self.ident("msg"), text="  把封面\n换成第二张")
        reply = self.capture("session.idle", messageID=self.reply("已换成第二张。", extra_parts=[
            {"type": "text", "text": "Called the Read tool", "synthetic": True}, {"type": "tool", "tool": "read"}]))
        self.assertEqual({start["session_id"], prompt["session_id"], reply["session_id"]}, {start["session_id"]})
        events = self.events(start["session_id"])
        self.assertEqual([e["event_type"] for e in events], ["session.started", "user.prompt", "assistant.message"])
        self.assertEqual([e["payload"]["text"] for e in events[1:]], ["  把封面\n换成第二张", "已换成第二张。"])
        self.assertEqual([e["actor_kind"] for e in events], ["system", "human", "system"])
        self.assertTrue(all(e["source_system"] == opencode_session.SOURCE for e in events))
        _, projects = self.call("GET", "/v1/projects", token=self.admin_token)
        self.assertIn(str(self.native_root.resolve()), [p["description"] for p in projects["projects"]])
        # Replay is idempotent.
        again = self.capture("chat.message", messageID=prompt["native_turn_id"], text="  把封面\n换成第二张")
        self.assertTrue(again["replayed"])

    def test_subagent_unknown_and_unfinished_sessions_record_nothing(self):
        child = self.new_session(parent=self.sid)
        unknown = "ses_" + "Z" * 26
        for sid in (child, unknown):
            receipt = opencode_session.capture_device(self.packet("chat.message", sessionID=sid, messageID=self.ident("msg"),
                                                                   text="hi"), self.device, self.data)
            self.assertEqual(receipt["status"], "ignored")
        self.assertEqual(self.capture("session.idle", messageID=self.reply("half", finished=False))["status"], "ignored")
        # A message id of another session cannot pose as this prompt.
        other = self.new_session()
        foreign = self.reply("x", sid=other)
        self.assertEqual(self.capture("chat.message", messageID=foreign, text="hi")["status"], "ignored")
        self.assertEqual(self.capture("chat.message", messageID=self.ident("msg"), text="   ")["status"], "ignored")
        with self.assertRaises(ValueError):
            self.capture("chat.message", messageID="not-an-id", text="hi")
        _, events = self.call("GET", "/v1/events?limit=100", token=self.admin_token)
        self.assertFalse([e for e in events["events"] if e["source_system"] == opencode_session.SOURCE])

    def test_a_world_writable_database_is_not_trusted(self):
        self.database.chmod(0o666)
        self.assertEqual(self.capture("session.created")["status"], "ignored")

    # -- ADR 0039 --------------------------------------------------------------------

    def test_compaction_writes_a_checkpoint(self):
        self.capture("session.created")
        self.exchange("把第四段改成硬切", "已改。")
        _, context, row = self.main("session.compacting")
        self.assertIsNone(context)
        self.assertEqual(row["worklog"]["reason"], "PRE_COMPACT")

    def test_the_first_prompt_of_a_new_session_carries_the_resume_block(self):
        self.main("session.created")
        self.exchange("把第四段的转场改成硬切", "已改成硬切。")
        self.exchange("再把字幕调大一号", "字幕已调到 48 号。")
        self.sid = self.new_session()
        _, _, row = self.main("session.created")
        self.assertEqual(row["worklog"]["status"], "ok")  # the earlier session is recovered
        _, context, row = self.main("chat.message", messageID=self.ident("msg"), text="把封面换成第二张", starting=True)
        self.assertIn("上一次：OpenCode", context)
        self.assertIn("再把字幕调大一号", context)
        self.assertEqual(row["recent_activity"]["trigger"], "session_start")
        self.assertNotIn("再把字幕调大一号", json.dumps(row, ensure_ascii=False))
        # The next ordinary prompt carries nothing; asking does.
        self.assertIsNone(self.main("chat.message", messageID=self.ident("msg"), text="把封面换成第三张")[1])
        _, context, row = self.main("chat.message", messageID=self.ident("msg"), text="opencode 刚才在做什么")
        self.assertIn("只包含 OpenCode 的会话", context)

    def test_nothing_is_added_without_the_opt_in(self):
        self.main("session.created")
        self.exchange("把第四段的转场改成硬切", "已改成硬切。")
        memory_hint.set_enabled(self.binding_path, False)
        self.sid = self.new_session()
        self.main("session.created")
        _, context, row = self.main("chat.message", messageID=self.ident("msg"), text="我刚才在做什么", starting=True)
        self.assertIsNone(context)
        self.assertNotIn("recent_activity", row)

    # -- the installer and the plugin ------------------------------------------------

    def install(self, **kw):
        launcher = self.native_root / "launcher.py"
        if not launcher.exists():
            launcher.write_text("pass")
        return install_opencode.install(self.native_root / "oc-home", self.binding_path, self.receipts,
                                        python=sys.executable, launcher=launcher, data_home=self.data, **kw)

    def test_installer_owns_one_plugin_file(self):
        home = self.native_root / "oc-home"
        (home / "plugins").mkdir(parents=True)
        foreign = home / "plugins" / "herdr-agent-state.js"
        foreign.write_text("export default {}")
        with self.assertRaises(InstallError):
            self.install()
        self.assertEqual(self.install(confirmed=True)["status"], "installed")
        self.assertEqual(self.install(confirmed=True)["status"], "unchanged")
        target = home / "plugins" / install_opencode.NAME
        self.assertIn(install_opencode.MARKER, target.read_text())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(install_opencode.set_memory_hint(home, self.binding_path, enabled=False, confirmed=True)["status"],
                         "memory_hint_disabled")
        self.assertEqual(self.install(confirmed=True, disable=True)["status"], "disabled")
        self.assertFalse(target.exists())
        self.assertEqual(foreign.read_text(), "export default {}")
        with self.assertRaises(InstallError):
            install_opencode.set_memory_hint(home, self.binding_path, enabled=True, confirmed=True)
        target.write_text("// someone else's plugin")
        with self.assertRaises(InstallError):
            self.install(confirmed=True)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_plugin_forwards_events_and_attaches_the_context(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        seen = work / "seen.jsonl"
        launcher = work / "launcher.py"
        # Stands in for opencode_session: records what it was sent, answers prompts.
        launcher.write_text(
            "import json,sys\n"
            f"body=json.load(sys.stdin);open({str(seen)!r},'a').write(json.dumps(body)+'\\n')\n"
            "print(json.dumps({'context':'CTX' if body['event']=='chat.message' else None}))\n")
        plugin = work / "plugin.mjs"
        plugin.write_text(install_opencode.render(python=sys.executable, launcher=launcher, binding=self.binding_path,
                                                  receipts=self.receipts, data_home=self.data))
        driver = work / "drive.mjs"
        driver.write_text("""
import plugin from "./plugin.mjs";
const hooks = await plugin.server({});
const user = (id, text) => ({ message: { id, role: "user", time: { created: 1 } }, parts: [{ type: "text", text }] });
await hooks.event({ event: { type: "session.created", properties: { info: { id: "ses_main0000001", time: { created: 1 } } } } });
await hooks.event({ event: { type: "session.created", properties: { info: { id: "ses_child000001", parentID: "ses_main0000001" } } } });
const first = user("msg_0000000001", "hello");
await hooks["chat.message"]({ sessionID: "ses_main0000001" }, first);
const second = user("msg_0000000002", "again");
await hooks["chat.message"]({ sessionID: "ses_main0000001" }, second);
await hooks["chat.message"]({ sessionID: "ses_child000001" }, user("msg_0000000003", "child"));
await hooks["command.execute.before"]({ sessionID: "ses_main0000001", command: "init", arguments: "" });
await hooks["chat.message"]({ sessionID: "ses_main0000001" }, user("msg_0000000004", "template"));
await hooks.event({ event: { type: "message.updated", properties: { info: { id: "msg_0000000005", sessionID: "ses_main0000001", role: "assistant", time: { completed: 3 } } } } });
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "ses_main0000001" } } });
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "ses_main0000001" } } });
await hooks["experimental.session.compacting"]({ sessionID: "ses_main0000001" }, { context: [] });
console.log(JSON.stringify(first.parts.slice(1)));
""")
        out = subprocess.run(["node", str(driver)], capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(out.returncode, 0, out.stderr)
        added = json.loads(out.stdout)
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0]["text"], added[0]["synthetic"], added[0]["messageID"]), ("CTX", True, "msg_0000000001"))
        self.assertRegex(added[0]["id"], r"^prt_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        sent = [json.loads(line) for line in seen.read_text().splitlines()]
        self.assertEqual([(s["event"], s.get("messageID"), s.get("starting")) for s in sent], [
            ("session.created", None, None),
            ("chat.message", "msg_0000000001", True),
            ("chat.message", "msg_0000000002", False),
            ("session.idle", "msg_0000000005", None),
            ("session.compacting", None, None)])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OpenCodeCapture(name) for name in OpenCodeCapture.__dict__ if name.startswith("test_"))
