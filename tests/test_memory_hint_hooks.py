"""ADR 0036 phase A, hook side: Codex and Claude Code hint delivery and installers.

Over a real Core (the capture test harness), so the hint key, the scope and
`POST /v1/memory/hint` are the real ones; the engine is absent, which is the
fail-open case. Text delivery is checked with a stub client.
"""
import io
import json
import shlex
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_claude_session_capture import ClaudeCapture
from jasmine_core import auth, db
from jasmine_core.capture import claude_session, codex_session, install_claude, install_codex, memory_hint
from jasmine_core.hosthook.binding import BindingError, load_binding, load_device
from jasmine_core.hosthook.install import InstallError

HINT = "[JASMINE RELEVANT MEMORY - NON AUTHORITATIVE]\n- 决策 · 用户陈述 · 2026-10-26 — 就用 v3。\n[/JASMINE RELEVANT MEMORY]"


class StubClient:
    calls = []
    answer = {"status": "ok", "text": HINT, "items": [{}], "trace_id": "mtr_x", "non_authoritative": True}
    error = None

    def __init__(self, url, token, timeout):
        StubClient.calls.append({"url": url, "timeout": timeout})

    def post(self, path, body):
        StubClient.calls[-1].update(path=path, body=body)
        if StubClient.error:
            raise StubClient.error
        return StubClient.answer


class MemoryHintHooks(ClaudeCapture):
    def setUp(self):
        super().setUp()
        StubClient.calls, StubClient.error = [], None
        StubClient.answer = dict(StubClient.answer)
        conn = db.connect(self.db_path)
        system = conn.execute("SELECT actor_id FROM api_keys WHERE label='capture'").fetchone()[0]
        key = auth.Auth(conn).issue_key(actor_id=system, label="memory hint", scopes=["memory:recall"])["token"]
        conn.close()
        self.memory_key = self.native_root / "memory.key"
        self.memory_key.write_text(key)
        self.memory_key.chmod(0o600)
        body = json.loads(self.binding_path.read_text())
        body["memory_key_file"] = str(self.memory_key)
        self.binding_path.write_text(json.dumps(body))
        self.binding_path.chmod(0o600)
        self.receipts = self.native_root / "receipts.jsonl"
        self.launcher = self.native_root / "claude-capture.py"
        self.launcher.write_text("pass")

    def enable(self):
        memory_hint.set_enabled(self.binding_path, True)
        return load_device(self.binding_path)

    # -- binding ------------------------------------------------------------

    def test_binding_flag_needs_a_key_and_is_device_owned(self):
        self.assertFalse(load_device(self.binding_path).memory_hint)
        self.assertTrue(self.enable().memory_hint)
        self.assertFalse(memory_hint.set_enabled(self.binding_path, True))
        self.assertEqual(self.binding_path.stat().st_mode & 0o777, 0o600)
        body = json.loads(self.binding_path.read_text())
        body.pop("memory_key_file")
        self.binding_path.write_text(json.dumps(body))
        with self.assertRaises(BindingError):
            load_device(self.binding_path)
        # A project file cannot switch the device's hint on.
        project = self.native_root / "project-binding.json"
        project.write_text(json.dumps({"version": 1, "memory_hint": True}))
        with self.assertRaises(BindingError):
            load_binding(project, self.binding_path)

    def test_enabling_without_a_hint_key_is_refused(self):
        body = json.loads(self.binding_path.read_text())
        body.pop("memory_key_file")
        self.binding_path.write_text(json.dumps(body))
        with self.assertRaises(InstallError):
            memory_hint.set_enabled(self.binding_path, True)

    # -- request -------------------------------------------------------------

    def test_request_is_silent_when_disabled_and_fails_open(self):
        self.assertEqual(memory_hint.request(load_device(self.binding_path), project_id=self.project,
                                             native_session_id="n", prompt="hello there",
                                             client_factory=StubClient)[0], None)
        self.assertEqual(StubClient.calls, [])
        device = self.enable()
        text, receipt = memory_hint.request(device, project_id=self.project, native_session_id="n",
                                            prompt="hello there", client_factory=StubClient)
        self.assertEqual(text, HINT)
        self.assertEqual(receipt["items"], 1)
        self.assertLessEqual(StubClient.calls[0]["timeout"], memory_hint.BUDGET_SECONDS)
        self.assertNotIn("prompt", json.dumps(receipt))
        StubClient.error = TimeoutError()
        text, receipt = memory_hint.request(device, project_id=self.project, native_session_id="n",
                                            prompt="hello there", client_factory=StubClient)
        self.assertEqual((text, receipt["status"]), (None, "failed"))
        StubClient.error = None
        StubClient.answer = {**StubClient.answer, "non_authoritative": None}
        self.assertIsNone(memory_hint.request(device, project_id=self.project, native_session_id="n",
                                              prompt="hello there", client_factory=StubClient)[0])

    def test_real_core_answers_the_hint_key_without_an_engine(self):
        device = self.enable()
        text, receipt = memory_hint.request(device, project_id=self.project, native_session_id="n",
                                            prompt="接下来的视频版本怎么定？")
        self.assertIsNone(text)
        self.assertIn(receipt["status"], ("empty", "degraded"))

    # -- Codex ---------------------------------------------------------------

    def codex_main(self, native):
        argv = ["capture", "--device-binding", str(self.binding_path), "--receipts", str(self.receipts)]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(native))), redirect_stdout(out):
            code = codex_session.main()
        return code, out.getvalue()

    def test_codex_prints_the_hint_only_for_an_accepted_prompt(self):
        accepted = {"status": "accepted", "event_type": "user.prompt", "project_id": self.project,
                    "session_id": "ses_x", "native_session_id": "native-1"}
        native = {"hook_event_name": "UserPromptSubmit", "prompt": "接下来的视频版本怎么定？"}
        hinted = (HINT, {"status": "ok", "items": 1})
        with patch.object(codex_session, "capture_device", return_value=dict(accepted)), \
                patch.object(memory_hint, "request", return_value=hinted) as asked:
            self.assertEqual(self.codex_main(native), (0, ""))  # opt-in is off
            asked.assert_not_called()
            self.enable()
            code, out = self.codex_main(native)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"],
                         {"hookEventName": "UserPromptSubmit", "additionalContext": HINT})
        self.assertEqual(asked.call_args.kwargs["session_id"], "ses_x")
        receipt = json.loads(self.receipts.read_text().splitlines()[-1])
        self.assertEqual(receipt["memory_hint"], {"status": "ok", "items": 1})
        self.assertNotIn(HINT, self.receipts.read_text())
        with patch.object(codex_session, "capture_device", return_value={"status": "ignored"}), \
                patch.object(memory_hint, "request", return_value=hinted) as asked:
            self.assertEqual(self.codex_main(native), (0, ""))
            asked.assert_not_called()

    def test_codex_installer_flips_only_the_device_flag(self):
        home = self.native_root / "codex"
        home.mkdir()
        with self.assertRaises(InstallError):  # capture first
            install_codex.set_memory_hint(home, self.binding_path, enabled=True, confirmed=True)
        install_codex.install(home, self.binding_path, self.receipts, confirmed=True)
        hooks = (home / "hooks.json").read_bytes()
        self.assertEqual(install_codex.set_memory_hint(home, self.binding_path, enabled=True, confirmed=True)["status"],
                         "memory_hint_enabled")
        self.assertEqual((home / "hooks.json").read_bytes(), hooks)  # Codex hook trust is kept
        self.assertTrue(load_device(self.binding_path).memory_hint)
        install_codex.install(home, self.binding_path, self.receipts, confirmed=True, disable=True)
        self.assertFalse(load_device(self.binding_path).memory_hint)

    # -- Claude --------------------------------------------------------------

    def verify_first_turn(self, text="第一轮：这首歌这次发布到哪个账号？"):
        """Real capture of a typed first turn, which records the session as verified."""
        self.typed(text)
        receipt = claude_session.capture_device(self.packet("UserPromptSubmit", prompt=text),
                                                load_device(self.binding_path), self.home)
        self.assertEqual(receipt["status"], "accepted", receipt)

    def next_turn(self, text="第二轮：开头用哪个视频版本？"):
        # Claude has not written this turn to the transcript yet while the
        # synchronous hint handler runs (observed live 2026-10-27).
        self.turn = str(__import__("uuid").uuid4())
        return self.packet("UserPromptSubmit", prompt=text)

    def test_claude_hint_needs_a_capture_verified_turn_in_the_session(self):
        packet = self.packet("UserPromptSubmit", prompt="这首歌这次发布到哪个账号？")
        self.assertEqual(claude_session.memory_hint_device(packet, load_device(self.binding_path), self.home)[1],
                         {"status": "disabled"})
        device = self.enable()
        # First turn: nothing verified yet, even if the transcript already has it.
        self.typed("这首歌这次发布到哪个账号？")
        with patch.object(memory_hint, "request") as asked:
            text, receipt = claude_session.memory_hint_device(packet, device, self.home)
        self.assertEqual((text, receipt["reason"]), (None, "no_verified_turn_in_session"))
        asked.assert_not_called()
        # Capture verifies that turn; the next turn gets a hint without waiting
        # for the transcript, which does not contain it yet.
        receipt = claude_session.capture_device(packet, load_device(self.binding_path), self.home)
        self.assertEqual(receipt["status"], "accepted", receipt)
        verified_turn = self.turn
        packet = self.next_turn()
        with patch.object(claude_session.time, "sleep", side_effect=AssertionError("must not wait")), \
                patch.object(memory_hint, "request", return_value=(HINT, {"status": "ok"})) as asked:
            text, receipt = claude_session.memory_hint_device(packet, device, self.home)
        self.assertEqual(text, HINT)
        self.assertEqual(receipt["verified_turn"], verified_turn)
        self.assertTrue(asked.call_args.kwargs["project_id"].startswith("prj_"))
        # The real Core answers too (no engine here, so no text).
        text, receipt = claude_session.memory_hint_device(packet, device, self.home)
        self.assertIsNone(text)
        self.assertIn(receipt["status"], ("empty", "degraded"))

    def test_claude_session_state_must_match_and_be_verified(self):
        device = self.enable()
        self.verify_first_turn()
        state_path = device.spool_dir / "claude-sessions" / (self.sid + ".json")
        original = state_path.read_text()
        packet = self.next_turn()
        cases = {
            "different workspace": lambda s: {**s, "cwd": "/somewhere/else"},
            "turn without typed-input proof": lambda s: {**s, "turns": {t: {"text_sha256": v["text_sha256"]}
                                                                    for t, v in s["turns"].items()}},
            "no turns": lambda s: {**s, "turns": {}},
        }
        for name, change in cases.items():
            state_path.write_text(json.dumps(change(json.loads(original))))
            state_path.chmod(0o600)
            with patch.object(memory_hint, "request") as asked:
                text, receipt = claude_session.memory_hint_device(packet, device, self.home)
            self.assertEqual((text, receipt["reason"]), (None, "no_verified_turn_in_session"), name)
            asked.assert_not_called()
        # A state file others can read is not trusted either.
        state_path.write_text(original)
        state_path.chmod(0o644)
        self.assertEqual(claude_session.memory_hint_device(packet, device, self.home)[1]["reason"],
                         "no_verified_turn_in_session")

    def test_claude_registration_uses_remaining_hint_budget(self):
        device = self.enable()
        self.verify_first_turn()
        packet = self.next_turn()
        with patch.object(claude_session, "bind_workspace", return_value=device) as bind, \
                patch.object(memory_hint, "request", return_value=(None, {"status": "empty"})) as asked:
            claude_session.memory_hint_device(packet, device, self.home, now=iter([0.0, 0.4]).__next__)
        self.assertAlmostEqual(bind.call_args.kwargs["timeout"], memory_hint.BUDGET_SECONDS - 0.4)
        self.assertEqual(asked.call_args.kwargs["deadline"], memory_hint.BUDGET_SECONDS)

    def test_claude_skips_registration_when_hint_budget_is_exhausted(self):
        device = self.enable()
        self.verify_first_turn()
        packet = self.next_turn()
        with patch.object(claude_session, "bind_workspace") as bind, \
                patch.object(memory_hint, "request") as asked:
            text, receipt = claude_session.memory_hint_device(
                packet, device, self.home, now=iter([0.0, memory_hint.BUDGET_SECONDS]).__next__)
        self.assertIsNone(text)
        self.assertEqual(receipt["reason"], "no_time_left")
        bind.assert_not_called()
        asked.assert_not_called()

    def test_claude_hint_main_always_exits_zero_with_valid_json(self):
        argv = ["claude-capture.py", "--device-binding", str(self.binding_path), "--receipts", str(self.receipts),
                "--claude-home", str(self.home), "--memory-hint"]
        for stdin in ("not json", json.dumps(self.packet("UserPromptSubmit", prompt="x"))):
            out = io.StringIO()
            with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
                self.assertEqual(claude_session.main(), 0)
            self.assertEqual(json.loads(out.getvalue()), {})
        rows = [json.loads(line) for line in self.receipts.read_text().splitlines()]
        self.assertTrue(all(row["status"] == "memory_hint" for row in rows))

    def test_claude_installer_adds_a_separate_synchronous_handler(self):
        target = self.home / "settings.json"
        foreign = {"type": "command", "command": "echo foreign"}
        target.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [foreign]}]}}))
        options = {"python": "/usr/bin/python3", "launcher": self.launcher, "confirmed": True}
        with self.assertRaises(InstallError):
            install_claude.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=True, **options)
        install_claude.install(self.home, self.binding_path, self.receipts, **options)
        result = install_claude.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=True, **options)
        self.assertEqual(result["status"], "memory_hint_enabled")
        self.assertTrue(load_device(self.binding_path).memory_hint)
        handlers = [h for g in json.loads(target.read_text())["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        hint = [h for h in handlers if h.get("statusMessage") == install_claude.HINT_LABEL]
        capture = [h for h in handlers if h.get("statusMessage") == install_claude.LABEL]
        self.assertEqual(len(hint), 1)
        self.assertNotIn("async", hint[0])
        self.assertEqual(hint[0]["timeout"], 3)
        self.assertEqual(shlex.split(hint[0]["command"])[-1], "--memory-hint")
        self.assertTrue(capture[0]["async"])
        self.assertIn(foreign, handlers)
        # Re-installing capture keeps the hint; disabling capture removes both.
        install_claude.install(self.home, self.binding_path, self.receipts, **options)
        handlers = [h for g in json.loads(target.read_text())["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertEqual(sum(h.get("statusMessage") == install_claude.HINT_LABEL for h in handlers), 1)
        self.assertTrue(load_device(self.binding_path).memory_hint)
        self.assertEqual(install_claude.set_memory_hint(self.home, self.binding_path, self.receipts, enabled=True,
                                                        **options)["status"], "unchanged")
        install_claude.install(self.home, self.binding_path, self.receipts, disable=True, **options)
        self.assertFalse(load_device(self.binding_path).memory_hint)
        self.assertEqual(json.loads(target.read_text()), {"hooks": {"UserPromptSubmit": [{"hooks": [foreign]}]}})


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(MemoryHintHooks(name) for name in MemoryHintHooks.__dict__ if name.startswith("test_"))
