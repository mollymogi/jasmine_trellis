"""ADR 0039: the work record (session Checkpoints) and the resume block.

Over a real Core (the Claude capture harness): capture writes the record with
its system key, the hint handler reads the block with its `memory:recall` key.
"""
import io
import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from test_claude_session_capture import ClaudeCapture
from jasmine_core import auth, clock, cli, db, worklog
from jasmine_core.capture import claude_session, codex_session, install_claude, memory_hint, worklog_client
from jasmine_core.hosthook.binding import load_device


class WorklogHooks(ClaudeCapture):
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
        self.binding_path.write_text(json.dumps(body))
        self.binding_path.chmod(0o600)
        memory_hint.set_enabled(self.binding_path, True)
        self.device = load_device(self.binding_path)

    # -- helpers ---------------------------------------------------------------

    def capture(self, event, **kwargs):
        return claude_session.capture_device(self.packet(event, **kwargs), self.device, self.home)

    def turn_of(self, text, reply):
        self.turn = str(uuid.uuid4())
        self.typed(text)
        accepted = self.capture("UserPromptSubmit", prompt=text)
        self.assertEqual(accepted["status"], "accepted", accepted)
        self.capture("Stop", last_assistant_message=reply)
        return accepted

    def new_session(self):
        """A second Claude session in the same directory."""
        self.sid = str(uuid.uuid4())
        self.turn = str(uuid.uuid4())
        self.transcript = self.folder / (self.sid + ".jsonl")
        self.transcript.write_text("")
        self.transcript.chmod(0o600)

    def checkpoints(self):
        code, page = self.call("GET", "/v1/session-checkpoints", token=self.admin_token)
        self.assertEqual(code, 200, page)
        return page["session_checkpoints"]

    def hint(self, event, **kwargs):
        return claude_session.memory_hint_device(self.packet(event, **kwargs), self.device, self.home)

    def finished_session(self):
        self.capture("SessionStart", source="startup")
        self.turn_of("把第四段的转场改成硬切", "已改成硬切，导出在 out/4.mp4。")
        self.turn_of("再把字幕调大一号", "字幕已调到 48 号。")
        return self.capture("SessionEnd", reason="other")

    # -- A: the work record ------------------------------------------------------

    def test_session_end_writes_one_checkpoint_of_what_the_session_did(self):
        ended = self.finished_session()
        self.assertEqual(ended["worklog"]["status"], "recorded", ended)
        rows = self.checkpoints()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["reason"], row["agent"], row["turns"]), ("SESSION_END", "claude", 2))
        self.assertTrue(row["session_checkpoint_id"].startswith("sck_"))
        self.assertEqual([p["text"] for p in row["record"]["prompts"]], ["把第四段的转场改成硬切", "再把字幕调大一号"])
        self.assertEqual(row["record"]["last_reply"], "字幕已调到 48 号。")
        self.assertEqual(row["record"]["workspace_state"], {"git": False})
        self.assertEqual(row["family_root"], row["workspace_root"])
        # Delivered again: the same record, not a second one.
        again = self.capture("SessionEnd", reason="other")
        self.assertTrue(again["worklog"]["replayed"])
        self.assertEqual(len(self.checkpoints()), 1)
        # Its Event is in the session, written by Core.
        _, events = self.call("GET", "/v1/events?session_id=" + ended["session_id"], token=self.admin_token)
        made = [e for e in events["events"] if e["event_type"] == "session.checkpointed"]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["payload"]["session_checkpoint_id"], row["session_checkpoint_id"])

    def test_precompact_writes_a_checkpoint_and_no_conversation_event(self):
        self.capture("SessionStart", source="startup")
        first = self.turn_of("跑一遍渲染", "渲染完成。")
        compacted = self.capture("PreCompact", trigger="auto")
        self.assertEqual(compacted["worklog"]["status"], "recorded", compacted)
        self.assertEqual([r["reason"] for r in self.checkpoints()], ["PRE_COMPACT"])
        _, events = self.call("GET", "/v1/events?session_id=" + first["session_id"], token=self.admin_token)
        self.assertEqual([e["event_type"] for e in events["events"]],
                         ["session.started", "user.prompt", "assistant.message", "session.checkpointed"])

    def test_a_raw_event_cannot_pose_as_a_checkpoint(self):
        token = self.key(["events:write"])
        code, body = self.call("POST", "/v1/events", {
            "event_type": "session.checkpointed", "source_system": "anything", "host_id": self.device.host_id,
            "payload": {"text": ""}}, token=token)
        self.assertEqual(code, 400, body)

    def test_checkpoint_rows_are_append_only(self):
        self.finished_session()
        conn = db.connect(self.db_path)
        try:
            for statement in ("UPDATE session_checkpoints SET turns=0", "DELETE FROM session_checkpoints"):
                with self.assertRaises(Exception):
                    with db.transaction(conn):
                        conn.execute(statement)
        finally:
            conn.close()

    def test_the_next_session_recovers_a_session_that_never_ended(self):
        self.capture("SessionStart", source="startup")
        self.turn_of("先导出 480p 预览", "预览已导出。")
        quiet = self.sid
        self.new_session()
        # Not yet idle: it may still be running, so nothing is recovered.
        started = self.capture("SessionStart", source="startup")
        self.assertEqual(started["worklog"], {**started["worklog"], "status": "ok", "recovered": 0})
        with patch.object(worklog, "IDLE", timedelta(0)):
            started = self.capture("SessionStart", source="startup")
        self.assertEqual(started["worklog"]["recovered"], 1, started)
        rows = self.checkpoints()
        self.assertEqual([(r["reason"], r["native_session_id"]) for r in rows], [("RECOVERED", quiet)])
        with patch.object(worklog, "IDLE", timedelta(0)):
            self.assertEqual(self.capture("SessionStart", source="startup")["worklog"]["recovered"], 0)

    def test_recovery_reaches_past_ten_covered_sessions_and_keeps_batch_limit(self):
        quiet = set()
        for _ in range(6):
            self.capture("SessionStart", source="startup")
            self.turn_of("继续导出预览", "预览已导出。")
            quiet.add(self.sid)
            self.new_session()
        for _ in range(10):
            self.finished_session()
            self.new_session()
        with patch.object(worklog, "IDLE", timedelta(0)):
            first = self.capture("SessionStart", source="startup")
            self.assertEqual(first["worklog"]["recovered"], 5, first)
            second = self.capture("SessionStart", source="startup")
            self.assertEqual(second["worklog"]["recovered"], 1, second)
            third = self.capture("SessionStart", source="startup")
            self.assertEqual(third["worklog"]["recovered"], 0, third)
        recovered = {r["native_session_id"] for r in self.checkpoints() if r["reason"] == "RECOVERED"}
        self.assertEqual(recovered, quiet)

    # -- B: the resume block -----------------------------------------------------------

    def test_a_new_session_starts_with_what_the_last_one_did(self):
        self.finished_session()
        self.new_session()
        text, receipt = self.hint("SessionStart", source="startup")
        self.assertEqual((receipt["status"], receipt["trigger"]), ("ok", "session_start"), receipt)
        self.assertIn("[JASMINE RECENT WORK - NON AUTHORITATIVE]", text)
        self.assertIn("再把字幕调大一号", text)
        self.assertIn("字幕已调到 48 号。", text)
        self.assertIn("已结束", text)
        self.assertLessEqual(len(text), worklog.BUDGET)
        # Once per session.
        text, receipt = self.hint("SessionStart", source="clear")
        self.assertIsNone(text)
        self.assertEqual(receipt["reason"], "already_injected")

    def test_resume_and_compact_starts_get_nothing(self):
        self.finished_session()
        self.new_session()
        for source in ("resume", "compact"):
            with patch.object(worklog_client, "recent") as asked:
                text, receipt = self.hint("SessionStart", source=source)
            self.assertIsNone(text)
            self.assertEqual(receipt["reason"], "not_a_new_session")
            asked.assert_not_called()

    def test_the_first_prompt_asking_what_was_done_gets_the_block(self):
        self.finished_session()
        self.new_session()
        self.typed("记不记得我刚才在做什么")
        text, receipt = self.hint("UserPromptSubmit", prompt="记不记得我刚才在做什么")
        # No verified turn yet, so no Memory hint, but the resume block comes.
        self.assertEqual(receipt["reason"], "no_verified_turn_in_session")
        self.assertEqual(receipt["recent_activity"]["trigger"], "prompt")
        self.assertIn("把第四段的转场改成硬切", text)

    def test_an_ordinary_prompt_gets_no_block(self):
        self.finished_session()
        self.new_session()
        with patch.object(worklog_client, "recent") as asked:
            text, receipt = self.hint("UserPromptSubmit", prompt="这首歌这次发布到哪个账号？")
        asked.assert_not_called()
        self.assertIsNone(text)
        self.assertNotIn("recent_activity", receipt)

    def test_the_current_session_is_never_its_own_previous_work(self):
        self.capture("SessionStart", source="startup")
        self.turn_of("先导出 480p 预览", "预览已导出。")
        text, receipt = self.hint("SessionStart", source="startup")
        self.assertIsNone(text)
        self.assertEqual(receipt["status"], "empty")

    def test_codex_work_in_the_same_directory_is_shown_to_claude(self):
        codex = self.native_root / "codex.jsonl"
        codex.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": "codex-1", "cwd": str(self.native_root), "source": "vscode"}}) + "\n")

        def native(event, **extra):
            return {"hook_event_name": event, "session_id": "codex-1", "turn_id": "turn-1",
                    "cwd": str(self.native_root), "transcript_path": str(codex), **extra}

        for packet in (native("SessionStart", source="startup"),
                       native("UserPromptSubmit", prompt="用 ComfyUI 跑 10 秒的测试"),
                       native("Stop", last_assistant_message="10 秒测试已提交。")):
            self.assertEqual(codex_session.capture_device(packet, self.device)["status"], "accepted")
        text, receipt = self.hint("SessionStart", source="startup")
        self.assertIn("上一次：Codex", text)
        self.assertIn("用 ComfyUI 跑 10 秒的测试", text)

    def codex_main(self, event, cwd=None, **extra):
        """Codex's real capture entry point, as its synchronous hook runs it."""
        cwd = Path(cwd or self.native_root)
        transcript = cwd / "codex-session.jsonl"
        if not transcript.exists():
            transcript.write_text(json.dumps({"type": "session_meta", "payload": {
                "id": "codex-1", "cwd": str(cwd), "source": "vscode"}}) + "\n")
        native = {"hook_event_name": event, "session_id": "codex-1", "turn_id": str(uuid.uuid4()),
                  "cwd": str(cwd), "transcript_path": str(transcript), **extra}
        receipts = self.native_root / "codex-receipts.jsonl"
        argv = ["capture", "--device-binding", str(self.binding_path), "--receipts", str(receipts)]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(native))), redirect_stdout(out):
            code = codex_session.main()
        row = json.loads(receipts.read_text().splitlines()[-1])
        return code, out.getvalue(), row

    def test_a_new_codex_session_learns_what_this_claude_session_did(self):
        self.finished_session()
        code, out, row = self.codex_main("SessionStart", source="startup")
        self.assertEqual(code, 0, row)
        printed = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(printed["hookEventName"], "SessionStart")
        self.assertIn("上一次：Claude", printed["additionalContext"])
        self.assertIn("再把字幕调大一号", printed["additionalContext"])
        self.assertEqual(row["recent_activity"]["trigger"], "session_start")
        self.assertNotIn("再把字幕调大一号", json.dumps(row, ensure_ascii=False))
        # A resumed Codex session gets nothing.
        _, out, row = self.codex_main("SessionStart", source="resume")
        self.assertEqual(out, "")
        self.assertNotIn("recent_activity", row)

    def test_a_codex_prompt_asking_what_was_done_gets_the_block(self):
        self.finished_session()
        self.codex_main("SessionStart", source="resume")
        _, out, row = self.codex_main("UserPromptSubmit", prompt="我刚才在做什么")
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("把第四段的转场改成硬切", context)
        self.assertEqual(row["recent_activity"]["trigger"], "prompt")
        # An ordinary prompt gets no block.
        with patch.object(worklog_client, "recent") as asked:
            self.codex_main("UserPromptSubmit", prompt="这首歌这次发布到哪个账号？")
        asked.assert_not_called()

    def test_a_repository_inside_the_workspace_sees_the_workspace_session(self):
        """Claude started in a plain folder; Codex opens the git repository inside it."""
        self.finished_session()
        repo = self.native_root / "repo"
        (repo / ".git").mkdir(parents=True)
        _, out, row = self.codex_main("SessionStart", cwd=repo, source="startup")
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("再把字幕调大一号", context)
        self.assertIn(f"上一次的会话在 {self.native_root.resolve()}", context)

    def test_the_hint_main_prints_a_session_start_envelope(self):
        self.finished_session()
        self.new_session()
        receipts = self.native_root / "receipts.jsonl"
        argv = ["claude-capture", "--device-binding", str(self.binding_path), "--receipts", str(receipts),
                "--claude-home", str(self.home), "--memory-hint"]
        out = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdin", io.StringIO(json.dumps(self.packet("SessionStart", source="startup")))), \
                redirect_stdout(out):
            self.assertEqual(claude_session.main(), 0)
        printed = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(printed["hookEventName"], "SessionStart")
        self.assertIn("再把字幕调大一号", printed["additionalContext"])
        row = json.loads(receipts.read_text().splitlines()[-1])
        self.assertEqual(row["status"], "recent_activity")
        self.assertNotIn("再把字幕调大一号", json.dumps(row, ensure_ascii=False))

    def test_installer_registers_precompact_capture_and_the_session_start_hint(self):
        target = self.home / "settings.json"
        launcher = self.native_root / "claude-capture.py"
        launcher.write_text("pass")
        receipts = self.native_root / "receipts.jsonl"
        options = {"python": "/usr/bin/python3", "launcher": launcher, "confirmed": True}
        install_claude.install(self.home, self.binding_path, receipts, **options)
        install_claude.set_memory_hint(self.home, self.binding_path, receipts, enabled=True, **options)
        hooks = json.loads(target.read_text())["hooks"]
        self.assertIn("PreCompact", hooks)
        start = [h for g in hooks["SessionStart"] for h in g["hooks"]]
        hint = [h for h in start if h.get("statusMessage") == install_claude.HINT_LABEL]
        self.assertEqual(len(hint), 1)
        self.assertNotIn("async", hint[0])
        self.assertEqual(shlex.split(hint[0]["command"])[-1], "--memory-hint")
        # Disabling the hint removes it from both events; capture stays.
        install_claude.set_memory_hint(self.home, self.binding_path, receipts, enabled=False, **options)
        hooks = json.loads(target.read_text())["hooks"]
        for event in ("SessionStart", "UserPromptSubmit"):
            self.assertFalse(any(h.get("statusMessage") == install_claude.HINT_LABEL
                                 for g in hooks[event] for h in g["hooks"]))
        install_claude.install(self.home, self.binding_path, receipts, disable=True, **options)
        self.assertNotIn("hooks", json.loads(target.read_text()))

    def two_workspaces(self):
        """A finished Claude session here, then a later one in another folder."""
        self.finished_session()
        # Not nearby (a sibling tree), so it is another workspace, not a neighbour.
        other = Path(tempfile.mkdtemp(prefix="jasmine-other-"))
        self.addCleanup(shutil.rmtree, other, True)
        self.new_session()
        self.capture("SessionStart", source="startup", cwd=str(other))
        self.typed("另一个目录里的事", cwd=str(other))
        self.capture("UserPromptSubmit", prompt="另一个目录里的事", cwd=str(other))
        self.capture("Stop", last_assistant_message="好的", cwd=str(other))
        self.capture("SessionEnd", reason="other", cwd=str(other))
        return other

    def store(self):
        from jasmine_core import SCHEMA_VERSION

        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return worklog.WorklogStore(conn, schema_version=SCHEMA_VERSION)

    def test_global_scope_orders_every_workspace_by_time(self):
        self.two_workspaces()
        body = {"host_id": self.device.host_id, "workspace_root": str(self.native_root.resolve()),
                "exclude_native_session_id": "nobody"}
        everywhere = self.store().recent({**body, "scope": "global"}, may_read=lambda p: True)
        self.assertEqual(everywhere["status"], "ok", everywhere)
        self.assertEqual(len(everywhere["sessions"]), 2)
        text = everywhere["text"]
        self.assertIn("范围：全部工作区", text)
        # The newer session (in the other folder) comes first.
        self.assertLess(text.index("上一次"), text.index("另一个目录里的事"))
        self.assertIn("再把字幕调大一号", text)  # the older session, as a line of the other sessions
        # The default stays this workspace only.
        here = self.store().recent(body, may_read=lambda p: True)
        self.assertEqual(len(here["sessions"]), 1)
        self.assertNotIn("另一个目录里的事", here["text"])

    def test_agent_filter_applies_inside_either_scope(self):
        self.two_workspaces()
        body = {"host_id": self.device.host_id, "workspace_root": str(self.native_root.resolve()),
                "exclude_native_session_id": "nobody"}
        claude = self.store().recent({**body, "scope": "global", "agent": "claude"}, may_read=lambda p: True)
        self.assertEqual(len(claude["sessions"]), 2)
        self.assertIn("只包含 Claude 的会话", claude["text"])
        codex = self.store().recent({**body, "scope": "global", "agent": "codex"}, may_read=lambda p: True)
        self.assertEqual((codex["status"], codex["text"]), ("empty", None))
        # Time is still the order inside a single agent's sessions.
        self.assertEqual(claude["sessions"][0]["agent"], "claude")

    def test_scope_and_agent_are_checked_before_any_query(self):
        from jasmine_core import errors

        body = {"host_id": self.device.host_id, "workspace_root": str(self.native_root)}
        with self.assertRaises(errors.InvalidRequest):
            self.store().recent({**body, "scope": "everywhere"}, may_read=lambda p: True)
        with self.assertRaises(errors.InvalidRequest):
            self.store().recent({**body, "agent": "gpt"}, may_read=lambda p: True)

    def test_the_list_endpoint_and_worklog_command_filter_by_agent(self):
        self.two_workspaces()
        code, page = self.call("GET", "/v1/session-checkpoints?agent=claude", token=self.admin_token)
        self.assertEqual((code, page["count"]), (200, 2), page)
        code, page = self.call("GET", "/v1/session-checkpoints?agent=codex", token=self.admin_token)
        self.assertEqual((code, page["count"]), (200, 0))
        code, page = self.call("GET", "/v1/session-checkpoints?agent=gpt", token=self.admin_token)
        self.assertEqual(code, 400, page)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["worklog", "--db", str(self.db_path), "--since", "1d", "--agent", "codex"])
        self.assertEqual(out.getvalue().strip(), "no work record in this period")

    def test_worklog_command_lists_the_records(self):
        self.finished_session()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["worklog", "--db", str(self.db_path), "--since", "1d",
                                       "--workspace", str(self.native_root)]), 0)
        printed = out.getvalue()
        self.assertIn("SESSION_END", printed)
        self.assertIn("最后：再把字幕调大一号", printed)


class WorklogPieces(unittest.TestCase):
    def test_a_worktree_belongs_to_its_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, tree, plain = Path(tmp) / "repo", Path(tmp) / "trees" / "x", Path(tmp) / "plain"
            (repo / ".git" / "worktrees" / "x").mkdir(parents=True)
            tree.mkdir(parents=True)
            plain.mkdir()
            (tree / ".git").write_text(f"gitdir: {repo}/.git/worktrees/x\n")
            self.assertEqual(worklog.family_root(str(tree)), str(repo))
            self.assertEqual(worklog.family_root(str(repo)), str(repo))
            self.assertEqual(worklog.family_root(str(plain)), str(plain))

    def test_nearby_folders_share_the_block_within_two_levels(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            base = Path(tmp).resolve()
            for path in (base / "a" / "b" / "c",):
                path.mkdir(parents=True)
            self.assertTrue(worklog.related(str(base), str(base / "a")))
            self.assertTrue(worklog.related(str(base), str(base / "a" / "b")))
            self.assertFalse(worklog.related(str(base), str(base / "a" / "b" / "c")))
            self.assertTrue(worklog.related(str(base / "a" / "b"), str(base)))
            self.assertFalse(worklog.related(str(base / "a"), str(base / "elsewhere")))
        # A session started in the home directory (or above) is nobody's previous work.
        self.assertFalse(worklog.related(str(Path.home()), str(Path.home() / "x")))
        self.assertFalse(worklog.related(str(Path.home().parent), str(Path.home() / "x")))

    def test_intent_matches_questions_about_recent_work_only(self):
        asks = ["记不记得我刚才在做什么", "我们上次做到哪了", "继续上次的", "刚刚在改什么来着", "进度怎么样",
                "What was I working on?", "where did we leave off"]
        not_asks = ["这首歌这次发布到哪个账号？", "/compact", "把进度条改成蓝色，并且在每个阶段结束时显示一次总耗时和剩余时间估计",
                    "<pasted_content>刚才在做什么</pasted_content>", "刚" * 201, ""]
        for text in asks:
            self.assertTrue(worklog_client.asks_about_recent_work(text), text)
        for text in not_asks:
            self.assertFalse(worklog_client.asks_about_recent_work(text), text)

    def test_workspace_change_is_described(self):
        then = {"git": True, "branch": "main", "head": "a" * 40, "dirty": ["a.py"], "dirty_count": 1, "complete": True}
        self.assertEqual(worklog.compare_states(then, dict(then)), "已观察的 Git 元数据一致（未比较文件内容）")
        now = {**then, "head": "b" * 40, "dirty": ["a.py", "b.md"], "dirty_count": 2}
        change = worklog.compare_states(then, now)
        self.assertIn("HEAD aaaaaaa → bbbbbbb", change)
        self.assertIn("新增 b.md", change)
        self.assertIsNone(worklog.compare_states(None, now))
        self.assertIn("未能确认", worklog.compare_states(then, {**now, "complete": False}))

    def test_workspace_state_marks_truncated_paths_incomplete(self):
        for paths in ([f"file-{i}" for i in range(51)], ["x" * 1025]):
            with self.subTest(paths=len(paths)):
                output = "# branch.head main\n# branch.oid " + "a" * 40 + "\n"
                output += "".join(f"? {path}\n" for path in paths)
                proc = subprocess.CompletedProcess([], 0, stdout=output.encode())
                with patch.object(worklog_client.subprocess, "run", return_value=proc):
                    state = worklog_client.workspace_state("/workspace")
                self.assertFalse(state["complete"])
                self.assertEqual(state["dirty_count"], len(paths))
                self.assertLessEqual(len(state["dirty"]), 50)
                self.assertTrue(all(len(path) <= 1024 for path in state["dirty"]))

    def test_prompts_lose_tags_and_pastes(self):
        text = worklog._clean("<pasted_content id=\"1\">长文</pasted_content>看一下&#x20;<ui-state>x</ui-state>", 160)
        self.assertEqual(text, "[粘贴内容] 看一下 [附加内容]")

    def test_the_block_fits_its_budget(self):
        now = clock.now()
        at = clock.to_rfc3339(now - timedelta(minutes=5))
        record = {"session_id": "ses_x", "agent": "codex", "turns": 400, "last_activity_at": at,
                  "prompts": [{"at": at, "text": "很长的一句话" * 30}] * 8, "tasks": ["任务" * 40] * 8,
                  "last_reply": "回复" * 400}
        text = worklog.render([record, record, record], family="/w", now=now)
        self.assertLessEqual(len(text), worklog.BUDGET)
        self.assertTrue(text.endswith("[/JASMINE RECENT WORK]"))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(WorklogHooks(name) for name in WorklogHooks.__dict__ if name.startswith("test_"))
    suite.addTests(loader.loadTestsFromTestCase(WorklogPieces))
    return suite
