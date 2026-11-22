import io
import json
import socket
import unittest
import urllib.error
from unittest.mock import patch

from jasmine_core.memory import brain

CHAIN = ("codex/gpt-a", "zhipu/glm-b", "opencode-go/ds-c")


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reply(content):
    return Response(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


def quotas(entries):
    return Response(json.dumps({"data": entries}).encode())


class Chain(unittest.TestCase):
    def setUp(self):
        brain._bench.clear()
        brain._quota.update(at=-1e9, low=frozenset())
        self.calls = []
        self.script = {}
        self.quota = []
        patcher = patch("urllib.request.OpenerDirector.open", side_effect=self.open)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/v1/magpie/quotas"):
            return quotas(self.quota)
        model = json.loads(request.data)["model"]
        self.calls.append(model)
        outcome = self.script.get(model, '{"ok": true}')
        if isinstance(outcome, BaseException):
            raise outcome
        return reply(outcome)

    def ask(self, **kw):
        return brain.ask_json("sys", "user", models=CHAIN, **kw)

    def test_first_model_in_the_chain_answers(self):
        answer = self.ask()
        self.assertEqual((answer.model, answer.data), ("codex/gpt-a", {"ok": True}))
        self.assertEqual(self.calls, ["codex/gpt-a"])

    def test_a_refusal_moves_on_and_benches_the_model(self):
        self.script["codex/gpt-a"] = urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO())
        self.assertEqual(self.ask().model, "zhipu/glm-b")
        self.calls.clear()
        self.assertEqual(self.ask().model, "zhipu/glm-b")
        self.assertEqual(self.calls, ["zhipu/glm-b"])  # benched model is not retried at once

    def test_timeouts_and_unreadable_answers_move_on(self):
        self.script["codex/gpt-a"] = urllib.error.URLError(socket.timeout("timed out"))
        self.script["zhipu/glm-b"] = "sorry, I cannot"
        self.assertEqual(self.ask().model, "opencode-go/ds-c")

    def test_an_answer_failing_validation_moves_on(self):
        self.script["codex/gpt-a"] = '{"wrong": 1}'
        self.script["zhipu/glm-b"] = '{"judgements": []}'

        def valid(data):
            if "judgements" not in data:
                raise ValueError("missing")
        self.assertEqual(self.ask(validate=valid).model, "zhipu/glm-b")

    def test_a_spent_provider_goes_last(self):
        self.quota = [{"provider": "codex", "windows": [{"remaining": 3}]},
                      {"provider": "zhipu", "windows": [{"remaining": 80}, {"remaining": 0, "lane": "images"}]}]
        self.assertEqual(self.ask().model, "zhipu/glm-b")
        self.script["zhipu/glm-b"] = urllib.error.HTTPError("u", 403, "no", {}, io.BytesIO())
        self.script["opencode-go/ds-c"] = urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO())
        self.assertEqual(self.ask().model, "codex/gpt-a")  # spent, but still tried before giving up

    def test_gateway_down_stops_at_once(self):
        self.script["codex/gpt-a"] = urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        with self.assertRaises(brain.BrainUnavailable):
            self.ask()
        self.assertEqual(self.calls, ["codex/gpt-a"])

    def test_every_model_failing_is_unavailable(self):
        for model in CHAIN:
            self.script[model] = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO())
        with self.assertRaises(brain.BrainUnavailable):
            self.ask()
        with self.assertRaises(brain.BrainUnavailable):
            self.ask()  # all benched: nothing is called
        self.assertEqual(len(self.calls), 3)

    def test_chain_comes_from_the_environment(self):
        with patch.dict("os.environ", {"JASMINE_MEMORY_BRAIN_CHAIN": " a/x , b/y ,"}):
            self.assertEqual(brain.chain(), ("a/x", "b/y"))
        with patch.dict("os.environ", {"JASMINE_MEMORY_BRAIN_CHAIN": ""}):
            self.assertEqual(brain.chain()[0], "codex/gpt-6.2-sol")


if __name__ == "__main__":
    unittest.main()
