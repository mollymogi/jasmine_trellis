"""Write-time semantic supersession of observed memories.

When a new observed memory lands, its closest older observed memories in the
same workspace are compared with it by the model: does the new one replace,
enrich, confirm or conflict with each? An old observed memory is retired only
when the new one replaces it *entirely* — an old memory with any point the new
one does not speak to stays. Authority (rules, confirmed decisions) is never a
candidate: an observation cannot retire what the user confirmed.

It runs in the memory worker's idle time, one new memory per pass, after the
memory is in the engine. Nothing before the first run is reconsidered: the
cursor starts at the moment the feature is first switched on. A model outage
holds the cursor and backs off; it never skips a memory.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from .. import clock, db
from . import brain
from .types import OBSERVED_MEMORY_TYPES, RecallRequest

CURSOR_KEY = "memory_supersede_cursor"
COOLDOWN_KEY = "memory_supersede_cooldown_until"
COOLDOWN_MIN, COOLDOWN_MAX = 300, 3600
MIN_RELEVANCE = 0.5
MAX_CANDIDATES = 5
MAX_RETIRE = 2
RECALL_TOKENS = 2400
TIMEOUT_SECONDS = 45
RELATIONS = ("REPLACES", "ENRICHES", "CONFIRMS", "CONFLICTS", "UNRELATED")

PROMPT = """你在维护一个用户的长期记忆库。下面是一条【新记忆】和若干条同项目的【旧记忆】。
逐条判断新记忆与每条旧记忆的关系：
- REPLACES：新记忆就同一件事给出了新的决定/事实，使旧记忆的结论不再成立（例如旧“默认用 A”，新“改用 B”）。
- ENRICHES：新记忆补充了旧记忆，两者都仍然成立。
- CONFIRMS：说的是同一件事，结论一致。
- CONFLICTS：说法互相矛盾，但看不出新记忆是有意推翻旧记忆。
- UNRELATED：不是同一件事。
fully_covered：只有当旧记忆里的**每一个要点**都被新记忆覆盖或推翻时才为 true；
旧记忆只要还有一点新记忆没涉及（例如旧记忆讲了两件事、新记忆只改了其中一件），就必须为 false。
拿不准时选更保守的关系，fully_covered 用 false。
只输出 JSON：{"judgements":[{"id":"<旧记忆 id>","relation":"REPLACES|ENRICHES|CONFIRMS|CONFLICTS|UNRELATED","fully_covered":true|false,"reason":"<一句中文>"}]}
"""


class JudgeUnavailable(Exception):
    """The model could not be reached or did not answer in time."""


def gateway_endpoint() -> str:
    return brain.gateway_base() + "/v1/chat/completions"


def magpie_judge(new: dict[str, Any], old: list[dict[str, Any]],
                 log: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Ask the brain chain (GPT first, next model on refusal or spent quota)."""
    payload = {
        "new": {"text": new["sanitized_text"], "type": new["memory_type"], "at": new["created_at"]},
        "old": [{"id": o["memory_id"], "text": o["sanitized_text"], "type": o["memory_type"],
                 "at": o["created_at"]} for o in old],
    }

    def valid(data: dict[str, Any]) -> None:
        if not isinstance(data.get("judgements"), list):
            raise ValueError("no judgements list")

    try:
        answer = brain.ask_json(PROMPT, json.dumps(payload, ensure_ascii=False),
                                timeout=TIMEOUT_SECONDS, validate=valid, log=log)
    except brain.BrainUnavailable as exc:
        raise JudgeUnavailable(str(exc)) from exc
    return [dict(j, model=answer.model) for j in answer.data["judgements"] if isinstance(j, dict)]


class Superseder:
    def __init__(self, conn, store, adapter, lifecycle, *, actor_id: str, host_id: str,
                 judge: Callable[[dict, list], list] | None = None,
                 log: Callable[[str], None] | None = None,
                 now: Callable[[], float] = time.time) -> None:
        self._conn, self._store, self._adapter, self._lifecycle = conn, store, adapter, lifecycle
        self._actor, self._host, self._log, self._now = actor_id, host_id, log, now
        self._judge = judge or (lambda new, old: magpie_judge(new, old, log=log))

    # -- cursor ------------------------------------------------------------

    def _cursor(self) -> tuple[str, str]:
        value = db.get_meta(self._conn, CURSOR_KEY)
        if value is None:
            value = clock.now_rfc3339() + "|"
            with db.transaction(self._conn):
                db.set_meta(self._conn, CURSOR_KEY, value)
        created, _, memory_id = value.partition("|")
        return created, memory_id

    def _advance(self, item: dict[str, Any]) -> None:
        with db.transaction(self._conn):
            db.set_meta(self._conn, CURSOR_KEY, f"{item['created_at']}|{item['memory_id']}")

    def _cooling(self) -> bool:
        until = db.get_meta(self._conn, COOLDOWN_KEY)
        return bool(until) and float(until.partition("|")[0]) > self._now()

    def _back_off(self) -> None:
        previous = db.get_meta(self._conn, COOLDOWN_KEY) or ""
        last = previous.partition("|")[2]
        seconds = min(COOLDOWN_MAX, int(last) * 2) if last else COOLDOWN_MIN
        with db.transaction(self._conn):
            db.set_meta(self._conn, COOLDOWN_KEY, f"{self._now() + seconds}|{seconds}")

    def _clear_cooldown(self) -> None:
        if db.get_meta(self._conn, COOLDOWN_KEY):
            with db.transaction(self._conn):
                self._conn.execute("DELETE FROM core_meta WHERE key=?", (COOLDOWN_KEY,))

    # -- one pass ----------------------------------------------------------

    def _next(self) -> dict[str, Any] | None:
        created, memory_id = self._cursor()
        placeholders = ",".join("?" * len(OBSERVED_MEMORY_TYPES))
        row = self._conn.execute(
            f"SELECT memory_id FROM memory_items WHERE admission='OBSERVED' AND status='ACTIVE' "
            f"AND engine_state='RETAINED' AND project_id IS NOT NULL "
            f"AND memory_type IN ({placeholders}) "
            f"AND (created_at > ? OR (created_at = ? AND memory_id > ?)) "
            f"ORDER BY created_at, memory_id LIMIT 1",
            (*OBSERVED_MEMORY_TYPES, created, created, memory_id)).fetchone()
        return self._store.item(row["memory_id"]) if row else None

    def candidates(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        outcome = self._adapter.recall(RecallRequest(
            project_id=item["project_id"], actor_id=self._actor, query=item["sanitized_text"],
            types=tuple(OBSERVED_MEMORY_TYPES), max_tokens=RECALL_TOKENS))
        found: list[dict[str, Any]] = []
        for recalled in outcome.items:
            if len(found) >= MAX_CANDIDATES:
                break
            if recalled.memory_id == item["memory_id"] or recalled.scope_kind != "project":
                continue
            if recalled.relevance is None or recalled.relevance < MIN_RELEVANCE:
                continue
            old = self._store.item(recalled.memory_id)
            if (old is None or old["status"] != "ACTIVE" or old["admission"] != "OBSERVED"
                    or old["pinned"] or old["created_at"] >= item["created_at"]
                    or any(o["memory_id"] == old["memory_id"] for o in found)):
                continue
            found.append(old)
        return found

    def tick(self) -> dict[str, Any]:
        if self._cooling():
            return {"status": "cooling"}
        item = self._next()
        if item is None:
            return {"status": "idle"}
        old = self.candidates(item)
        retired: list[str] = []
        if old:
            try:
                judgements = self._judge(item, old)
            except JudgeUnavailable as exc:
                self._back_off()
                return {"status": "judge_unavailable", "memory_id": item["memory_id"], "error": str(exc)}
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                judgements = []  # a malformed answer retires nothing
                if self._log:
                    self._log(f"supersede: unreadable judgement for {item['memory_id']}: {exc}")
            by_id = {o["memory_id"]: o for o in old}
            for judgement in judgements:
                target = by_id.pop(str(judgement.get("id")), None)
                if (target is None or len(retired) >= MAX_RETIRE
                        or judgement.get("relation") != "REPLACES"
                        or judgement.get("fully_covered") is not True):
                    continue
                reason = (f"superseded_by:{item['memory_id']} [{judgement.get('model') or '?'}]: "
                          f"{str(judgement.get('reason') or '')[:200]}")
                try:
                    self._lifecycle.obsolete(target["memory_id"], {
                        "expected_revision": target["revision"], "reason": reason},
                        actor_id=self._actor, actor_kind="system", host_id=self._host)
                    retired.append(target["memory_id"])
                except Exception as exc:  # a concurrent edit wins; nothing to undo
                    if self._log:
                        self._log(f"supersede: could not retire {target['memory_id']}: {exc}")
        self._clear_cooldown()
        self._advance(item)
        return {"status": "done", "memory_id": item["memory_id"], "candidates": len(old),
                "retired": retired}
