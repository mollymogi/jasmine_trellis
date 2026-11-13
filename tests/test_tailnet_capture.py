"""Tailnet core_url is exact, and that origin never creates a project."""
import json
import os
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from jasmine_core import ids
from jasmine_core.api.client import ApiError, CoreClient, arm_hook_deadline, reset_hook_deadline
from jasmine_core.canonical import canonical_json
from jasmine_core.capture import codex_session, grok_session, memory_hint
from jasmine_core.capture.codex_session import PROJECT_SOURCE, bind_workspace
from jasmine_core.capture.hook_budget import HINT_FLOOR_SECONDS, RECENT_FLOOR_SECONDS
from jasmine_core.capture.worklog_client import git_remote_origin, recent
from jasmine_core.hosthook.binding import TAILNET_CORE_ORIGIN, BindingError, load_device

ORIGIN = TAILNET_CORE_ORIGIN


def _device(root: Path, url: str, host: str) -> Path:
    key = root / "system.key"
    key.write_text("not-a-real-token")
    key.chmod(0o600)
    memory = root / "memory.key"
    memory.write_text("not-a-real-token")
    memory.chmod(0o600)
    path = root / "device.json"
    path.write_text(json.dumps({
        "version": 1, "core_url": url, "host_id": host,
        "api_key_file": str(key), "memory_key_file": str(memory), "memory_hint": True,
        "on_unavailable": "allow", "spool_dir": str(root / "spool"),
    }))
    path.chmod(0o600)
    return path


class TailnetBinding(unittest.TestCase):
    def test_only_the_exact_origin_joins_loopback(self) -> None:
        host = ids.new_id("hst")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            accepted = load_device(_device(root, ORIGIN, host))
            self.assertEqual(accepted.core_url, ORIGIN)
            self.assertEqual(load_device(_device(root, "http://127.0.0.1:18787", host)).core_url,
                             "http://127.0.0.1:18787")
            rejected = [
                ORIGIN + "/", ORIGIN + ":443", ORIGIN + "/v1", ORIGIN + "?x=1", ORIGIN + "#frag",
                "https://user@mercuryliumac-mini.tail07df76.ts.net",
                "https://Mercuryliumac-mini.tail07df76.ts.net",
                "https://evil.example", "http://mercuryliumac-mini.tail07df76.ts.net",
                "https://127.0.0.1:443",
            ]
            for url in rejected:
                with self.subTest(url=url):
                    with self.assertRaises(BindingError):
                        load_device(_device(root, url, host))

    def test_the_origin_is_not_read_from_the_environment(self) -> None:
        host = ids.new_id("hst")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"JASMINE_TAILNET_CORE_URL": "https://evil.example",
                                          "JASMINE_TAILNET_CLIENTS": "1"}):
                self.assertEqual(load_device(_device(root, ORIGIN, host)).core_url, ORIGIN)
                with self.assertRaises(BindingError):
                    load_device(_device(root, "https://evil.example", host))


class _Script:
    def __init__(self, resolve):
        self.posts: list[tuple] = []
        self.resolve = resolve
        self.project_id = ids.new_id("prj")

    def client(self):
        script = self

        class Client:
            def __init__(self, url, key, timeout=5):
                self.timeout = timeout

            def post(self, path, body):
                script.posts.append((path, body, self.timeout))
                if path == "/v1/workspaces/resolve":
                    answer = script.resolve
                    if isinstance(answer, Exception):
                        raise answer
                    return answer
                if path == "/v1/projects":
                    return {"object": {"project_id": script.project_id}}
                raise AssertionError(path)

        return Client


class TailnetBind(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root.mkdir(exist_ok=True)
        self.host = ids.new_id("hst")
        self.confirmed = ids.new_id("prj")
        self.addCleanup(self.tmp.cleanup)

    def _bind(self, url, resolve, *, timeout=2, source=codex_session.SOURCE, entry=bind_workspace):
        device = load_device(_device(self.root, url, self.host))
        script = _Script(resolve)
        native = {"cwd": str(self.root)}
        with patch("jasmine_core.capture.worklog_client.git_remote_origin", return_value=(None, "absent")), \
                patch("jasmine_core.capture.codex_session.CoreClient", script.client()), \
                patch("jasmine_core.capture.codex_session.read_key", return_value="k"):
            try:
                bound = entry(native, device, source=source, timeout=timeout)
            except Exception:
                bound = None
                raise
            else:
                return bound, script
            finally:
                self.last = script

    def test_tailnet_adopts_only_ok_and_never_posts_the_old_key(self) -> None:
        root = codex_session.workspace_root(str(self.root))
        old = canonical_json([PROJECT_SOURCE, self.host, str(root)])
        grok_old = canonical_json([grok_session.SOURCE + "-workspace", self.host, str(root)])
        bound, script = self._bind(ORIGIN, {
            "status": "ok", "project_id": self.confirmed, "alias_conflict": False, "created": False})
        self.assertEqual(bound.project_id, self.confirmed)
        self.assertEqual([row[0] for row in script.posts], ["/v1/workspaces/resolve"])
        dumped = json.dumps(script.posts)
        self.assertNotIn(old, dumped)
        self.assertNotIn(grok_old, dumped)
        self.assertNotIn("/v1/projects", dumped)

    def test_grok_entry_uses_the_same_resolve_only_path(self) -> None:
        bound, script = self._bind(ORIGIN, {
            "status": "ok", "project_id": self.confirmed, "alias_conflict": False},
            source=grok_session.SOURCE, entry=grok_session.bind_workspace)
        self.assertEqual(bound.project_id, self.confirmed)
        self.assertEqual([row[0] for row in script.posts], ["/v1/workspaces/resolve"])

    def test_non_ok_resolve_fails_open_without_a_project(self) -> None:
        cases = [
            {"status": "confirmation_required", "project_id": None, "alias_conflict": True},
            {"status": "confirmation_required", "project_id": self.confirmed, "alias_conflict": False},
            {"status": "alias_conflict", "project_id": None, "alias_conflict": True},
            {"status": "ok", "project_id": None, "alias_conflict": False},
            {"status": "ok", "project_id": "not-a-project", "alias_conflict": False},
            {"status": "ok", "project_id": self.confirmed, "alias_conflict": True},
        ]
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self._bind(ORIGIN, body)
                self.assertEqual([row[0] for row in self.last.posts], ["/v1/workspaces/resolve"])
                self.assertNotIn("source_event_id", json.dumps(self.last.posts))

    def test_conflict_and_timeout_do_not_post_the_old_key(self) -> None:
        with self.assertRaises(ApiError):
            self._bind(ORIGIN, ApiError(409, {"error": {"code": "alias_conflict"}}))
        self.assertEqual([row[0] for row in self.last.posts], ["/v1/workspaces/resolve"])
        with self.assertRaises(TimeoutError):
            self._bind(ORIGIN, TimeoutError("timed out"))
        self.assertEqual([row[0] for row in self.last.posts], ["/v1/workspaces/resolve"])

    def test_loopback_without_an_alias_keeps_todays_project_key_and_timeout(self) -> None:
        bound, script = self._bind(
            "http://127.0.0.1:18787",
            {"status": "confirmation_required", "project_id": None, "alias_conflict": False},
            timeout=2)
        root = codex_session.workspace_root(str(self.root))
        old = canonical_json([PROJECT_SOURCE, self.host, str(root)])
        self.assertEqual(bound.project_id, script.project_id)
        self.assertEqual([row[0] for row in script.posts], ["/v1/workspaces/resolve", "/v1/projects"])
        body = script.posts[1][1]
        self.assertEqual(body["source_system"], PROJECT_SOURCE)
        self.assertEqual(body["source_event_id"], old)
        self.assertGreater(script.posts[1][2], 1.5)
        self.assertLessEqual(script.posts[1][2], 2)

    def test_loopback_confirmed_alias_does_not_send_the_old_key(self) -> None:
        bound, script = self._bind("http://127.0.0.1:9", {
            "status": "ok", "project_id": self.confirmed, "alias_conflict": False})
        self.assertEqual(bound.project_id, self.confirmed)
        self.assertEqual([row[0] for row in script.posts], ["/v1/workspaces/resolve"])

    def test_git_failure_does_not_post_resolve(self) -> None:
        device = load_device(_device(self.root, ORIGIN, self.host))
        for exc in (TimeoutError("git"), OSError("git"), ValueError("unclassifiable")):
            with self.subTest(exc=type(exc).__name__):
                with patch("jasmine_core.capture.worklog_client.git_remote_origin", side_effect=exc), \
                        patch("jasmine_core.capture.codex_session.CoreClient") as client:
                    with self.assertRaises(type(exc)):
                        bind_workspace({"cwd": str(self.root)}, device)
                    client.assert_not_called()

    def test_loopback_git_failure_keeps_the_project_fallback(self) -> None:
        device = load_device(_device(self.root, "http://127.0.0.1:18787", self.host))
        for exc in (subprocess.TimeoutExpired("git", 0.5), TimeoutError("git"), OSError("git"),
                    ValueError("unclassifiable")):
            with self.subTest(exc=type(exc).__name__):
                script = _Script({"status": "confirmation_required", "project_id": None,
                                  "alias_conflict": False})
                with patch("jasmine_core.capture.worklog_client.git_remote_origin", side_effect=exc), \
                        patch("jasmine_core.capture.codex_session.CoreClient", script.client()), \
                        patch("jasmine_core.capture.codex_session.read_key", return_value="k"):
                    bound = bind_workspace({"cwd": str(self.root)}, device)
                self.assertEqual(bound.project_id, script.project_id)
                self.assertEqual([row[0] for row in script.posts],
                                 ["/v1/workspaces/resolve", "/v1/projects"])
                self.assertIsNone(script.posts[0][1]["origin"])
                self.assertEqual(script.posts[0][1]["remote_kind"], "absent")

    def test_claude_prompt_keeps_resume_when_resolve_is_not_ok(self) -> None:
        from jasmine_core.capture import claude_session
        device = load_device(_device(self.root, ORIGIN, self.host))
        root = str(self.root)
        native = {"event": "UserPromptSubmit", "sid": "sid", "turn": "turn", "cwd": root,
                  "text": "刚才在做什么"}
        script = _Script({"status": "confirmation_required", "project_id": None, "alias_conflict": True})
        with patch.object(claude_session, "_native", return_value=native), \
                patch.object(claude_session, "_session_verified", return_value=("turn", root)), \
                patch.object(claude_session, "_recent", return_value=("resume block", {"status": "ok"})), \
                patch("jasmine_core.capture.worklog_client.git_remote_origin", return_value=(None, "absent")), \
                patch("jasmine_core.capture.codex_session.CoreClient", script.client()), \
                patch("jasmine_core.capture.codex_session.read_key", return_value="k"):
            text, receipt = claude_session.memory_hint_device(
                {"hook_event_name": "UserPromptSubmit"}, device, self.root)
        self.assertEqual(text, "resume block")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error_type"], "ValueError")
        self.assertEqual([row[0] for row in script.posts], ["/v1/workspaces/resolve"])

    def test_loopback_conflict_does_not_create_a_project(self) -> None:
        with self.assertRaises(ApiError):
            self._bind("http://127.0.0.1:9", ApiError(409, {"error": {"code": "alias_conflict"}}))
        self.assertEqual([row[0] for row in self.last.posts], ["/v1/workspaces/resolve"])


class TailnetClock(unittest.TestCase):
    def test_absolute_deadline_covers_headers_and_success_or_error_bodies(self) -> None:
        for status in (200, 403):
            for trickle in (False, True):
                with self.subTest(status=status, trickle=trickle):
                    stop = threading.Event()

                    class SlowHandler(BaseHTTPRequestHandler):
                        def do_GET(self):
                            body = b'{"error":{"code":"slow"}}'
                            if not trickle and stop.wait(0.65):
                                return
                            self.send_response(status)
                            self.send_header("Content-Length", str(len(body)))
                            self.end_headers()
                            try:
                                if trickle:
                                    for byte in body:
                                        if stop.wait(0.2):
                                            return
                                        self.wfile.write(bytes([byte]))
                                        self.wfile.flush()
                                elif not stop.wait(0.65):
                                    self.wfile.write(body)
                            except OSError:
                                pass  # The timed-out client may have closed already.

                        def log_message(self, *args):
                            pass

                    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
                    worker = threading.Thread(target=server.serve_forever, daemon=True)
                    worker.start()
                    started = time.monotonic()
                    token = arm_hook_deadline(started + 1.0)
                    try:
                        with self.assertRaises(TimeoutError):
                            CoreClient(f"http://127.0.0.1:{server.server_port}").get("/slow")
                        self.assertLess(time.monotonic() - started, 1.25)
                    finally:
                        reset_hook_deadline(token)
                        stop.set()
                        server.shutdown()
                        server.server_close()
                        worker.join(timeout=2)

    def test_a_dead_route_waits_once_for_the_remaining_budget(self) -> None:
        calls = []

        def boom(req, timeout=0):
            calls.append(timeout)
            raise urllib.error.URLError(TimeoutError("timed out"))

        token = arm_hook_deadline(time.monotonic() + 0.4)
        try:
            with patch.object(urllib.request, "urlopen", boom):
                with self.assertRaises(urllib.error.URLError):
                    CoreClient(ORIGIN, "token", timeout=5).post("/v1/workspaces/resolve", {})
                with self.assertRaises(urllib.error.URLError):
                    CoreClient(ORIGIN, "token", timeout=5).post("/v1/events", {})
        finally:
            reset_hook_deadline(token)
        self.assertEqual(len(calls), 2)  # two different calls, neither retried
        self.assertLessEqual(calls[0], 0.4)
        self.assertGreater(calls[0], 0)
        self.assertLess(calls[1], calls[0])

    def test_an_exhausted_clock_does_not_open_a_socket(self) -> None:
        token = arm_hook_deadline(time.monotonic() - 1)
        try:
            with patch.object(urllib.request, "urlopen") as opened:
                with self.assertRaises(TimeoutError):
                    CoreClient("http://127.0.0.1:9", "token", timeout=5).get("/v1/health")
                opened.assert_not_called()
        finally:
            reset_hook_deadline(token)

    def test_loopback_publish_timeouts_stay_5s_and_3s(self) -> None:
        import inspect
        self.assertIn("timeout=5", inspect.getsource(codex_session.capture))
        self.assertIn("timeout=3", inspect.getsource(grok_session._publish))

    def test_git_remote_stays_at_half_a_second(self) -> None:
        scrubbed = {"PATH": os.environ.get("PATH", ""), "GIT_TERMINAL_PROMPT": "0"}
        with patch("jasmine_core.capture.worklog_client.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 0.5)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                git_remote_origin("/tmp/not-a-repo")
        self.assertLessEqual(run.call_args.kwargs["timeout"], 0.5)
        self.assertEqual(run.call_args.kwargs["env"], scrubbed)
        self.assertEqual(run.call_args.args[0][:4], ["git", "-C", "/tmp/not-a-repo", "remote"])
        with patch("jasmine_core.capture.worklog_client.subprocess.run", side_effect=OSError("git")):
            with self.assertRaises(OSError):
                git_remote_origin("/tmp/not-a-repo")
        with patch("jasmine_core.capture.worklog_client.subprocess.run",
                   return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=b"\x00")):
            with self.assertRaises(ValueError):
                git_remote_origin("/tmp/not-a-repo")
        token = arm_hook_deadline(time.monotonic() + 0.2)
        try:
            with patch("jasmine_core.capture.worklog_client.subprocess.run",
                       return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")) as run:
                self.assertEqual(git_remote_origin("/tmp/not-a-repo"), (None, "absent"))
            self.assertLessEqual(run.call_args.kwargs["timeout"], 0.2)
            self.assertEqual(run.call_args.kwargs["env"], scrubbed)
        finally:
            reset_hook_deadline(token)
        token = arm_hook_deadline(time.monotonic() - 1)
        try:
            with patch("jasmine_core.capture.worklog_client.subprocess.run") as run:
                with self.assertRaises(TimeoutError):
                    git_remote_origin("/tmp/not-a-repo")
            run.assert_not_called()
        finally:
            reset_hook_deadline(token)

    def _tailnet_binding(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        return load_device(_device(root, ORIGIN, ids.new_id("hst"))), root

    def test_recent_starts_only_at_1_9s_and_hint_only_at_1_2s_after(self) -> None:
        binding, root = self._tailnet_binding()
        short = arm_hook_deadline(time.monotonic() + 1.5)
        try:
            with patch("jasmine_core.capture.worklog_client.CoreClient") as client:
                text, receipt = recent(binding, workspace_root=root, native_session_id="native-1",
                                       deadline=time.monotonic() + 5)
            self.assertIsNone(text)
            self.assertEqual(receipt["reason"], "no_time_left")
            client.assert_not_called()
            self.assertLess(1.5, RECENT_FLOOR_SECONDS)
            self.assertGreaterEqual(1.5, HINT_FLOOR_SECONDS)
        finally:
            reset_hook_deadline(short)
        hint_token = arm_hook_deadline(time.monotonic() + 1.5)
        try:
            client = Mock()
            client.return_value.post.return_value = {
                "text": "hint", "non_authoritative": True, "status": "ok", "items": []}
            text, receipt = memory_hint.request(
                binding, project_id=ids.new_id("prj"), native_session_id="native-1", prompt="刚才在做什么",
                client_factory=client)
            self.assertEqual(text, "hint")
            client.return_value.post.assert_called_once()
            self.assertGreaterEqual(client.call_args.kwargs["timeout"], HINT_FLOOR_SECONDS)
        finally:
            reset_hook_deadline(hint_token)
        tight = arm_hook_deadline(time.monotonic() + 1.0)
        try:
            client = Mock()
            text, receipt = memory_hint.request(
                binding, project_id=ids.new_id("prj"), native_session_id="native-1", prompt="刚才在做什么",
                client_factory=client)
            self.assertIsNone(text)
            self.assertEqual(receipt["reason"], "no_time_left")
            client.assert_not_called()
        finally:
            reset_hook_deadline(tight)

    def test_loopback_recent_keeps_the_short_prompt_budget(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        binding = load_device(_device(root, "http://127.0.0.1:9", ids.new_id("hst")))
        with patch("jasmine_core.capture.worklog_client.CoreClient") as client, \
                patch("jasmine_core.capture.worklog_client.workspace_state", return_value={"git": False}):
            client.return_value.post.return_value = {"text": "resume", "non_authoritative": True, "status": "ok",
                                                     "sessions": []}
            text, receipt = recent(binding, workspace_root=root, native_session_id="native-1",
                                   deadline=time.monotonic() + 0.8)
        self.assertEqual(text, "resume")
        client.return_value.post.assert_called_once()
        self.assertNotEqual(receipt.get("reason"), "no_time_left")


if __name__ == "__main__":
    unittest.main()
