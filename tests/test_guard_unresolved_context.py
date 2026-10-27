"""ADR 0035: a missing tool/action/path is insufficient only when an active rule matches on it.

Component level over real HTTP (Guard API and the host-hook route). These tests do not
establish native agent behaviour.
"""

from __future__ import annotations

from hosthook_support import HostHookHttp


class GuardUnresolvedContext(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        self.guard_key = self.key(["guard:check"], label="guard")

    def guard(self, **body):
        code, out = self.call("POST", "/v1/guard/check",
                              {"project_id": self.project, "task_id": self.task, **body}, token=self.guard_key)
        self.assertEqual(code, 200, out)
        return out

    def before(self, tool: str, native_tool: str, **payload):
        code, out = self.send(self.request("tool.before", {"tool": tool, "native_tool": native_tool,
                                                           "input": {"command": "x"}, **payload}))
        self.assertEqual(code, 201, out)
        return out

    def test_without_rules_unresolved_calls_are_allowed(self) -> None:
        for tool, native in (("shell", "Bash"), ("web", "WebFetch"), ("mcp", "mcp__x__y"), ("other", "TodoWrite")):
            with self.subTest(native=native):
                out = self.before(tool, native)
                self.assertEqual((out["decision"], out["guard"]["decision"]), ("allow", "allow"))
        self.assertEqual(self.guard()["decision"], "allow")

    def test_rule_on_missing_action_still_confirms(self) -> None:
        rule = self.rule("no-rm", "DENY", {"tool_category": "shell", "action": "rm"})
        out = self.guard(tool="Bash", tool_category="shell")
        self.assertEqual((out["decision"], out["uncertain"][0]["rule_id"]), ("confirm", rule))
        self.assertEqual(self.before("shell", "Bash")["decision"], "ask")

    def test_rule_on_missing_path_still_confirms(self) -> None:
        self.rule("no-etc", "DENY", {"path_prefix": "/etc"})
        self.assertEqual(self.guard(tool="Bash", tool_category="shell")["decision"], "confirm")
        self.assertEqual(self.before("web", "WebFetch")["decision"], "ask")

    def test_rule_on_present_dimension_decides_without_unresolved_ones(self) -> None:
        self.rule("no-shell", "DENY", {"tool_category": "shell"})
        self.assertEqual(self.before("shell", "Bash")["decision"], "deny")
        self.assertEqual(self.before("web", "WebFetch")["decision"], "allow")

    def test_category_mismatch_excludes_rule_on_missing_action(self) -> None:
        self.rule("no-rm", "DENY", {"tool_category": "shell", "action": "rm"})
        self.assertEqual(self.before("mcp", "mcp__x__y")["decision"], "allow")

    def test_uncertain_context_rule_cannot_force_confirm(self) -> None:
        self.rule("mention-rm", "CONTEXT", {"action": "rm"})
        out = self.guard(tool="Bash", tool_category="shell")
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(len(out["uncertain"]), 1)

    def test_missing_task_with_scoped_rule_still_confirms(self) -> None:
        self.rule("no-rm", "DENY", {"tool_category": "shell", "action": "rm"})
        code, out = self.call("POST", "/v1/guard/check", {"tool": "Bash", "tool_category": "shell", "action": "ls"},
                              token=self.guard_key)
        self.assertEqual((code, out["decision"]), (200, "confirm"))
