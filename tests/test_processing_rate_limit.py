"""A rate-limited provider cools the whole daily queue down instead of failing messages.

Real Core kernels and the daily-queue harness; the provider is a labelled
simulation that raises the same `ProviderFailure` codes the Codex and local CLI
providers raise.
"""
import json
import unittest

import test_conversation_processing as daily
from test_interpretations import output

from jasmine_core import db
from jasmine_core.conversation_processing import (COOLDOWN_MAX, COOLDOWN_MIN, COOLDOWN_SECONDS,
                                                  COOLDOWN_UNTIL, RATE_LIMIT_ATTEMPTS)
from jasmine_core.interpreter_provider import ProviderFailure

PAST = "2000-01-01T00:00:00.000000Z"


class _DailyHarness(daily.Processing):
    def setUp(self):
        super().setUp()
        del self.fail  # the harness's provider switch shadows TestCase.fail
        self.outcomes = []

        def extract(source):
            if self.outcomes and self.outcomes.pop(0) == "rate_limited":
                raise ProviderFailure("provider_rate_limited")
            result = output(source)
            result["candidates"][0].update(kind="NO_STRUCTURE", content=source["text"])
            return json.dumps(result)

        self.fake.action = extract


for _name in dir(daily.Processing):
    if _name.startswith("test_"):
        setattr(_DailyHarness, _name, None)


class RateLimitCooldown(_DailyHarness):
    def expire_cooldown(self):
        with db.transaction(self.conn):
            db.set_meta(self.conn, COOLDOWN_UNTIL, PAST)

    def row(self, event):
        return self.conn.execute("SELECT * FROM conversation_processing WHERE event_id=?",
                                 (event,)).fetchone()

    def test_a_rate_limit_requeues_the_message_and_pauses_the_queue(self):
        event = self.event("你好")
        self.outcomes = ["rate_limited"]
        result = self.worker.tick()
        self.assertEqual(result["status"], "RATE_LIMITED")
        row = self.row(event)
        self.assertEqual((row["status"], row["error_code"], row["lease_owner"]),
                         ("PENDING", "provider_rate_limited", None))
        self.assertEqual(db.get_meta(self.conn, COOLDOWN_SECONDS), str(COOLDOWN_MIN))
        self.assertEqual(self.worker.cooldown_until(), result["cooldown_until"])
        calls = self.fake.calls
        idle = self.worker.tick()
        self.assertEqual(idle, {"status": "idle", "cooldown_until": result["cooldown_until"]})
        self.assertEqual(self.fake.calls, calls, "no provider call while cooling down")
        self.assertEqual(self.worker.list()["cooldown_until"], result["cooldown_until"])
        change = self.conn.execute(
            "SELECT action FROM conversation_processing_changes WHERE event_id=? ORDER BY seq DESC",
            (event,)).fetchone()
        self.assertEqual(change["action"], "RATE_LIMITED")

    def test_the_message_is_retried_after_the_cooldown_and_success_resets_the_backoff(self):
        event = self.event("你好")
        self.outcomes = ["rate_limited"]
        self.worker.tick()
        self.expire_cooldown()
        self.assertEqual(self.worker.tick()["status"], "NO_ACTION")
        self.assertEqual(self.row(event)["status"], "NO_ACTION")
        self.assertEqual(db.get_meta(self.conn, COOLDOWN_SECONDS), "0")

    def test_backoff_doubles_to_a_cap_and_the_message_fails_after_the_attempt_budget(self):
        event = self.event("你好")
        seen = []
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            self.outcomes = ["rate_limited"]
            result = self.worker.tick()
            seen.append(int(db.get_meta(self.conn, COOLDOWN_SECONDS)))
            self.expire_cooldown()
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.row(event)["error_code"], "provider_rate_limited")
        self.assertEqual(seen[:RATE_LIMIT_ATTEMPTS - 1],
                         [min(COOLDOWN_MIN * 2 ** i, COOLDOWN_MAX)
                          for i in range(RATE_LIMIT_ATTEMPTS - 1)])

    def test_a_manual_retry_restores_the_attempt_budget(self):
        event = self.event("你好")
        for _ in range(RATE_LIMIT_ATTEMPTS):
            self.outcomes = ["rate_limited"]
            self.worker.tick()
            self.expire_cooldown()
        self.assertEqual(self.row(event)["status"], "FAILED")
        self.worker.retry(event, actor_id=self.human)
        self.outcomes = ["rate_limited"]
        self.assertEqual(self.worker.tick()["status"], "RATE_LIMITED")

    def test_other_provider_failures_still_fail_without_a_cooldown(self):
        event = self.event("你好")

        def timeout(source):
            raise ProviderFailure("provider_timeout")

        self.fake.action = timeout
        self.assertEqual(self.worker.tick()["status"], "FAILED")
        self.assertEqual(self.row(event)["error_code"], "provider_timeout")
        self.assertIsNone(self.worker.cooldown_until())


if __name__ == "__main__":
    unittest.main()
