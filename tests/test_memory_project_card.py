"""Model-written project cards: cited lines only, rebuilt when sources change."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from jasmine_core.memory import brain, project_card
from test_memory_hint import Build

TEXTS = ("就用 v3 版本。", "采用本地作为授权中心。", "发布前先跑一遍完整测试。",
         "截图统一放到桌面的 shots 文件夹。", "渲染一律走远端 Windows。", "视频成片统一 1080p。")


class Answer:
    def __init__(self, data, model="fake/model"):
        self.data, self.model, self.tried = data, model, [model]


class Asker:
    def __init__(self, data=None, fail=False):
        self.calls, self.data, self.fail = [], data, fail

    def __call__(self, system, user, *, timeout, validate, log):
        self.calls.append(user)
        if self.fail:
            raise brain.BrainUnavailable("no model answered")
        data = self.data or {"summary": "一个测试项目，正在定版本。",
                             "lines": [{"text": "用 v3 版本", "sources": [1]},
                                       {"text": "本地是授权中心", "sources": [2, 99]},
                                       {"text": "没有依据的话", "sources": []},
                                       {"text": "编号不存在", "sources": [42]}]}
        validate(data)
        return Answer(data)


class ProjectCard(Build):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def write(self, ask, **kw):
        return project_card.write_card(self.conn, self.folder, self.project, "demo", (self.project,),
                                       now=time.time(), ask=ask, **kw)

    def test_only_cited_lines_survive_and_ids_are_kept(self):
        self.seed(*TEXTS)
        ask = Asker()
        result = self.write(ask)
        self.assertEqual(result["status"], "written")
        card = project_card.read_card(self.folder, self.project)
        self.assertEqual([l["text"] for l in card["lines"]], ["用 v3 版本", "本地是授权中心"])
        notes = {l.split("] ", 1)[0][1:]: l for l in ask.calls[0].splitlines() if l.startswith("[")}
        ids = {i["memory_id"] for i in self.observed_items()}
        for line in card["lines"]:
            self.assertTrue(all(s in ids or s.startswith("rule:") for s in line["sources"]), line)
        self.assertGreaterEqual(len(notes), len(TEXTS))
        text = project_card.render(card)
        self.assertTrue(text.startswith("[JASMINE PROJECT CARD - NON AUTHORITATIVE]"))
        self.assertIn("- 用 v3 版本", text)

    def test_unchanged_sources_skip_the_model_and_too_few_notes_write_nothing(self):
        self.seed(*TEXTS[:3])
        ask = Asker()
        self.assertEqual(self.write(ask)["status"], "too_few")
        self.assertEqual(ask.calls, [])
        self.seed(*TEXTS[3:])
        self.assertEqual(self.write(ask)["status"], "written")
        self.assertEqual(self.write(ask)["status"], "unchanged")
        self.assertEqual(len(ask.calls), 1)
        self.assertEqual(self.write(ask, force=True)["status"], "written")

    def test_answer_without_enough_cited_lines_is_rejected(self):
        self.seed(*TEXTS)
        with self.assertRaises(ValueError):
            self.write(Asker({"summary": "x", "lines": [{"text": "a", "sources": []}]}))
        self.assertIsNone(project_card.read_card(self.folder, self.project))

    def test_old_cards_are_hidden_and_budget_trims_lines(self):
        card = {"name": "demo", "generated_ts": time.time() - 15 * 86400, "summary": "s",
                "lines": [{"text": "a", "sources": ["m"]}]}
        self.assertIsNone(project_card.render(card))
        card["generated_ts"] = time.time()
        card["lines"] = [{"text": "长" * 80, "sources": ["m"]} for _ in range(7)]
        text = project_card.render(card, budget=400)
        self.assertLessEqual(len(text), 400)
        self.assertIn("长", text)

    def test_refresher_runs_one_project_then_waits_and_cools_down_when_no_model(self):
        self.seed(*TEXTS)
        ask = Asker()
        refresher = project_card.CardRefresher(self.conn, self.folder, ask=ask, hours=None)
        self.assertEqual(refresher.tick()["status"], "done")
        self.assertEqual(refresher.tick()["status"], "idle")
        self.assertEqual(len(ask.calls), 1)
        failing = project_card.CardRefresher(self.conn, Path(self.tmp.name) / "other",
                                             ask=Asker(fail=True), hours=None)
        self.assertEqual(failing.tick()["status"], "cooling")
        self.assertEqual(failing.tick()["status"], "cooling")
        night = project_card.CardRefresher(self.conn, self.folder, ask=ask,
                                           hours=range(25, 26))
        self.assertEqual(night.tick()["status"], "outside_window")


if __name__ == "__main__":
    unittest.main()
