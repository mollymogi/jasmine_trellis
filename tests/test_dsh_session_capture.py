"""DeepSeek Harness capture: session directory check, publish, and profile install."""
import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_codex_session_capture import ConversationCapture
from jasmine_core import auth, db
from jasmine_core.capture import dsh_session, install_dsh, memory_hint
from jasmine_core.hosthook.binding import load_device
from jasmine_core.hosthook.install import InstallError
from jasmine_core.api.client import hook_deadline
from jasmine_core.hosthook.binding import TAILNET_CORE_ORIGIN


class DshCapture(ConversationCapture):
    def test_tailnet_launcher_shares_prompt_deadline_through_work_record(self):
        observed = []
        def capture(*args):
            observed.append(hook_deadline())
            return {"status": "accepted", "event_type": "user.prompt"}
        def work(*args):
            observed.append(hook_deadline())
            return "resume block"
        before = time.monotonic()
        with patch("sys.argv", ["dsh", "--device-binding", "unused", "--receipts", "unused",
                                "--dsh-home", "unused"]), \
             patch("sys.stdin", io.StringIO('{"event":"user.prompt"}')), \
             patch.object(dsh_session, "load_device", return_value=SimpleNamespace(core_url=TAILNET_CORE_ORIGIN)), \
             patch.object(dsh_session, "capture_device", side_effect=capture), \
             patch.object(dsh_session, "work_record", side_effect=work), \
             patch.object(dsh_session, "append_receipt"), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(dsh_session.main(), 0)
        self.assertEqual(observed[0], observed[1])
        self.assertGreaterEqual(observed[0], before + 4.0)
        self.assertLessEqual(observed[0], time.monotonic() + 4.0)
        self.assertIsNone(hook_deadline())
        self.assertEqual(json.loads(out.getvalue())["context"], "resume block")

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
        self.home = self.native_root / "dsh-home"
        self.cwd = str(self.native_root.resolve())
        self.sid = str(uuid4())
        self.turn = str(uuid4())
        folder = self.home / "sessions" / dsh_session.project_key(self.cwd) / self.sid
        folder.mkdir(parents=True, mode=0o700)
        os.chmod(folder, 0o700)
        os.chmod(folder.parent, 0o700)
        os.chmod(self.home / "sessions", 0o700)
        self.receipts = self.native_root / "dsh-receipts.jsonl"

    def packet(self, event, **extra):
        return {"event": event, "sessionID": self.sid, "cwd": self.cwd,
                "timestamp": "2026-11-12T03:00:00.000Z", "origin": None, "delegationDepth": 0, **extra}

    def test_project_key_matches_dsh_directories(self):
        self.assertEqual(dsh_session.project_key("/Volumes/Spare/videoMake"), "--Volumes-Spare-videoMake--")
        self.assertEqual(dsh_session.project_key("/Users/mercuryliu/Documents/ChatGPT/jasmine mesh"),
                         "--Users-mercuryliu-Documents-ChatGPT-jasmine~0020mesh--")

    def test_subagent_and_missing_directory_record_nothing(self):
        self.assertEqual(dsh_session.capture_device(self.packet("user.prompt", origin="subagent", messageID=self.turn, text="x"),
                                                    self.device, self.home)["status"], "ignored")
        other = self.packet("user.prompt", sessionID="session-" + str(uuid4()), messageID=self.turn, text="x")
        self.assertEqual(dsh_session.capture_device(other, self.device, self.home)["reason"], "unverified_session_origin")

    def test_prompt_and_reply_publish_once(self):
        start = dsh_session.capture_device(self.packet("session.created", starting=True), self.device, self.home)
        prompt = dsh_session.capture_device(self.packet("user.prompt", messageID=self.turn, text="原文", starting=True),
                                            self.device, self.home)
        reply = dsh_session.capture_device(self.packet("assistant.message", messageID=str(uuid4()), text="已记录"),
                                           self.device, self.home)
        self.assertEqual({start["session_id"], prompt["session_id"], reply["session_id"]}, {start["session_id"]})
        code, page = self.call("GET", "/v1/events?session_id=" + start["session_id"], token=self.admin_token)
        self.assertEqual(code, 200)
        self.assertEqual([event["event_type"] for event in page["events"]],
                         ["session.started", "user.prompt", "assistant.message"])
        self.assertEqual(page["events"][1]["payload"]["text"], "原文")
        self.assertTrue(all(event["source_system"] == dsh_session.SOURCE for event in page["events"]))

    def test_launcher_returns_context_slot(self):
        argv = ["dsh", "--device-binding", str(self.binding_path), "--receipts", str(self.receipts),
                "--dsh-home", str(self.home)]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(
                self.packet("user.prompt", messageID=self.turn, text="dsh 刚才在做什么")))), redirect_stdout(out):
            code = dsh_session.main()
        self.assertEqual(code, 0)
        self.assertIn("context", json.loads(out.getvalue()))
        self.assertEqual(json.loads(self.receipts.read_text().splitlines()[-1])["status"], "accepted")

    def test_installer_links_profiles_and_refuses_a_foreign_file(self):
        home = self.native_root / "dsh-install"
        profile = home / "profiles" / "web"
        profile.mkdir(parents=True)
        (profile / "package.json").write_text(json.dumps({"name": "dsh-profile-web", "dependencies": {}, "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}) + "\n")
        opts = {"python": sys.executable, "launcher": Path(dsh_session.__file__)}
        self.assertEqual(install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, **opts)["status"], "installed")
        self.assertEqual(install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, **opts)["status"], "unchanged")
        index = home / "jasmine-trellis" / "index.js"
        self.assertIn(install_dsh.MARKER, index.read_text())
        manifest = json.loads((profile / "package.json").read_text())
        self.assertIn(install_dsh.PACKAGE, manifest["dsh"]["profile"]["bundles"])
        self.assertTrue((profile / "node_modules" / "@local" / "jasmine-trellis").is_symlink())
        check = subprocess.run(["node", "--check", str(index)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(install_dsh.set_memory_hint(home, self.binding_path, enabled=False, confirmed=True)["status"],
                         "memory_hint_disabled")
        self.assertEqual(install_dsh.set_memory_hint(home, self.binding_path, enabled=True, confirmed=True)["status"],
                         "memory_hint_enabled")
        index.write_text("foreign\n")
        with self.assertRaises(InstallError):
            install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, **opts)
        index.write_text(install_dsh.render(python=sys.executable, launcher=Path(dsh_session.__file__),
                                            binding=self.binding_path, receipts=self.receipts, dsh_home=home))
        self.assertEqual(install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, disable=True, **opts)["status"],
                         "disabled")
        self.assertNotIn(install_dsh.PACKAGE, json.loads((profile / "package.json").read_text())["dsh"]["profile"]["bundles"])
        with self.assertRaises(InstallError):
            install_dsh.set_memory_hint(home, self.binding_path, enabled=True, confirmed=True)

    def test_installer_refuses_a_symlinked_plugin_directory(self):
        home = self.native_root / "dsh-symlinked-plugin"
        target = self.native_root / "dsh-symlinked-plugin-target"
        target.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        (home / install_dsh.PLUGIN_DIR).symlink_to(target, target_is_directory=True)
        opts = {"python": sys.executable, "launcher": Path(dsh_session.__file__)}
        with self.assertRaises(InstallError):
            install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, **opts)
        self.assertEqual(list(target.iterdir()), [])

    def test_disable_leaves_a_profile_created_after_install_alone(self):
        home = self.native_root / "dsh-disable-new-profile"
        installed = home / "profiles" / "web"
        installed.mkdir(parents=True)
        (installed / "package.json").write_text(json.dumps(
            {"name": "dsh-profile-web", "dependencies": {}, "dsh": {"profile": {"bundles": []}}}) + "\n")
        opts = {"python": sys.executable, "launcher": Path(dsh_session.__file__)}
        install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, **opts)
        later = home / "profiles" / "later"
        later.mkdir(parents=True)
        manifest = later / "package.json"
        original = json.dumps({"name": "dsh-profile-later"}) + "\n"
        manifest.write_text(original)
        install_dsh.install(home, self.binding_path, self.receipts, confirmed=True, disable=True, **opts)
        self.assertEqual(manifest.read_text(), original)
        self.assertEqual(list(later.glob("package.json.jasmine-backup-*")), [])
