"""GET /v1/host-hooks/deliveries: the recorded hook decisions, read back (component level).

Uses the same scratch Core as the host-hook suites. These checks show what Core returns for
recorded deliveries; they do not show that any real agent honoured a decision.
"""

from __future__ import annotations

import json

from hosthook_support import HOST, HostHookHttp

PATH = "/v1/host-hooks/deliveries"


class DeliveriesRead(HostHookHttp):
    def setUp(self) -> None:
        super().setUp()
        self.deny_rule = self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})

    def test_empty_then_newest_first_with_decision_and_matched_rules(self) -> None:
        code, body = self.call("GET", PATH, token=self.manage)
        self.assertEqual((code, body["deliveries"], body["has_more"]), (200, [], False), body)

        self.tool_before("rm", ["/protected/a"])     # DENY rule
        self.tool_before("rm", ["/elsewhere/a"])     # allow
        self.send(self.request("session.end", {}))   # decision none
        code, body = self.call("GET", PATH, token=self.manage)
        self.assertEqual(code, 200, body)
        self.assertEqual([d["decision"] for d in body["deliveries"]], ["none", "allow", "deny"])
        denied = body["deliveries"][2]
        self.assertEqual(denied["agent"], "fixture-agent")
        self.assertEqual(denied["event"], "tool.before")
        self.assertEqual(denied["guard_decision"], "deny")
        self.assertEqual([m["rule_id"] for m in denied["matched_rules"]], [self.deny_rule])
        self.assertEqual(denied["matched_rules"][0]["enforcement"], "DENY")

    def test_decision_filter_and_paging(self) -> None:
        self.tool_before("rm", ["/protected/a"])
        self.tool_before("rm", ["/protected/b"])
        self.tool_before("rm", ["/elsewhere/a"])
        code, body = self.call("GET", PATH + "?decision=deny", token=self.manage)
        self.assertEqual((code, len(body["deliveries"])), (200, 2), body)
        self.assertTrue(all(d["decision"] == "deny" for d in body["deliveries"]))

        code, first = self.call("GET", PATH + "?limit=2", token=self.manage)
        self.assertEqual((len(first["deliveries"]), first["has_more"]), (2, True), first)
        code, second = self.call("GET", f"{PATH}?limit=2&before_seq={first['next_before_seq']}", token=self.manage)
        self.assertEqual((len(second["deliveries"]), second["has_more"]), (1, False), second)
        seen = [d["delivery_id"] for d in first["deliveries"] + second["deliveries"]]
        self.assertEqual(len(set(seen)), 3)

    def test_bad_parameters_are_refused(self) -> None:
        for query in ("decision=maybe", "limit=0", "limit=101", "limit=abc", "before_seq=-1"):
            with self.subTest(query=query):
                code, refusal = self.call("GET", f"{PATH}?{query}", token=self.manage)
                self.assertEqual((code, refusal["error"]["code"]), (400, "invalid_request"), refusal)

    def test_needs_events_read(self) -> None:
        token = self.key(["objects:read"])
        code, refusal = self.call("GET", PATH, token=token)
        self.assertEqual((code, refusal["error"]["code"]), (403, "forbidden_scope"), refusal)

    def test_a_p1_guard_report_reads_back_as_the_decision_with_its_rule(self) -> None:
        from types import SimpleNamespace

        from jasmine_core.capture import p1_codex_hook as hook

        def post(path: str, body: dict) -> dict:
            self.assertEqual(path, "/v1/host-hooks/events")
            code, out = self.send(body)
            self.assertEqual(code, 201, out)
            return out

        config = {"host_id": HOST, "project_id": self.project, "task_id": self.task, "step_id": self.step}
        native = {"hook_event_name": "PreToolUse", "session_id": "codex-session-9", "turn_id": "turn-9",
                  "tool_use_id": "use-9", "tool_name": "Bash", "tool_input": {"command": "rm /protected/a"}}
        check = self.server_guard("rm", "/protected/a")
        hook._report_decision(SimpleNamespace(post=post), config, native, "deny", "blocked", check)
        code, body = self.call("GET", PATH, token=self.manage)
        top = body["deliveries"][0]
        self.assertEqual((top["agent"], top["event"], top["decision"]), ("codex", "tool.before", "deny"))
        self.assertEqual([m["rule_id"] for m in top["matched_rules"]], [self.deny_rule])
        self.assertNotIn("protected/a", json.dumps(body))

    def server_guard(self, action: str, path: str | None = None) -> dict:
        token = self.key(["guard:check"])
        code, out = self.call("POST", "/v1/guard/check", {
            "project_id": self.project, "task_id": self.task, "tool": "Bash",
            "tool_category": "shell", "action": action, **({"path": path} if path else {})}, token=token)
        self.assertEqual(code, 200, out)
        return out

    def decision_report(self, check: dict | None = None, *, decision: str = "deny") -> dict:
        payload = {"decision": decision, "reason": "hook selected this output", "tool_input_sha256": "b" * 64}
        if check is not None:
            payload["guard"] = {key: check[key] for key in ("decision", "request_sha256", "matched")}
        return self.request("tool.before", {"decision_report": payload})

    def test_category_verify_and_confirm_keep_the_native_choice(self) -> None:
        for action, enforcement, native in (("pwd", "VERIFY", "allow"), ("true", "CONFIRM", "deny")):
            with self.subTest(enforcement=enforcement):
                rule = self.rule(f"category-{action}", enforcement, {"tool_category": "shell", "action": action})
                check = self.server_guard(action)
                self.assertEqual(check["decision"], enforcement.lower())
                request = self.decision_report(check, decision=native)
                code, out = self.send(request)
                self.assertEqual((code, out["decision"], out["guard"]["matched"][0]["rule_id"]),
                                 (201, native, rule))

    def test_rule_changes_do_not_recompute_the_reported_snapshot(self) -> None:
        check = self.server_guard("rm", "/protected/a")
        code, out = self.call("POST", f"/v1/rules/{self.deny_rule}/retire",
                              {"host_id": HOST, "expected_revision": 2}, token=self.manage)
        self.assertEqual(code, 200, out)
        self.assertEqual(self.server_guard("rm", "/protected/a")["decision"], "allow")
        code, out = self.send(self.decision_report(check))
        self.assertEqual((code, out["decision"], out["guard"]["decision"]), (201, "deny", "deny"))
        self.assertEqual(out["guard"]["matched"][0]["rule_id"], self.deny_rule)

    def test_local_denial_replay_and_changed_content(self) -> None:
        request = self.decision_report()
        code, out = self.send(request)
        self.assertEqual((code, out["decision"], out["recorded"]["evidence_ids"]), (201, "deny", []))
        self.assertNotIn("guard", out)
        self.assertEqual(self.send(request)[0], 200)
        changed = {**request, "payload": {"decision_report": {**request["payload"]["decision_report"], "tool_input_sha256": "c" * 64}}}
        self.assertEqual(self.send(changed)[0], 409)

    def test_decision_reports_require_system_actor_even_on_replay(self) -> None:
        request = self.decision_report()
        self.assertEqual(self.send(request)[0], 201)
        code, out = self.call("POST", "/v1/host-hooks/events", request, token=self.human_hook_token)
        self.assertEqual((code, out["error"]["code"]), (403, "forbidden_actor_kind"))

    def test_invalid_snapshots_cannot_claim_permission(self) -> None:
        from copy import deepcopy
        from test_hosthook_schemas import load, validate

        valid = self.decision_report(self.server_guard("rm", "/protected/a"))
        self.assertEqual(validate(valid, load("host-hook-request.schema.json")), [])
        for edit in (lambda p: p.update(decision="allow"), lambda p: p.update(guard=None),
                     lambda p: p.update(command="secret"), lambda p: p.update(tool_input_sha256="bad"),
                     lambda p: p["guard"].update(request_sha256="bad"),
                     lambda p: p["guard"]["matched"][0].update(version=True)):
            request = deepcopy(valid)
            edit(request["payload"]["decision_report"])
            self.assertEqual(self.send(request)[0], 400)

    def test_tool_inputs_are_never_returned(self) -> None:
        self.tool_before("rm", ["/protected/a"])
        code, body = self.call("GET", PATH, token=self.manage)
        text = json.dumps(body)
        self.assertNotIn("protected/a", text)
        self.assertNotIn('"input"', text)
        self.assertNotIn('"command"', text)
        for item in body["deliveries"]:
            self.assertEqual(set(item), {"seq", "delivery_id", "event", "agent", "host_id", "trust", "created_at",
                                         "decision", "guard_decision", "reason", "degraded", "matched_rules", "decision_source"})
