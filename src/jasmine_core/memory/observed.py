"""Observed Memory: typed statements admitted from a conversation without review (ADR 0034).

The daily path already turns a captured message into an Interpretation with
schema- and span-validated candidates. Until ADR 0034 the only way any of that
reached Memory was through a human-approved Rule, which in real use meant almost
nothing did. This module admits the candidates themselves, as recallable history
that says who said it:

* the tier is `OBSERVED`, never Authority — no item here can activate a Rule,
  move State or be rendered as `[JASMINE AUTHORITY]` (ADR 0014 §1 is unchanged);
* `trust` records the speaker (`USER_STATED` or `AGENT_REPORTED`), not a claim
  that the statement is true;
* the global bank is never written: a GLOBAL candidate lands in its source's
  project bank.

**Why the user's own words are cut out first.** Host clients wrap the typed
request in supplied context: Codex prepends `# Files mentioned by the user` and
an `<in-app-browser-context>` block and puts the request under `## My request:`.
The model sometimes extracts from that wrapper, which is how an attached
document's instruction ("Distinguish instructions in attached documents…")
became a project RULE candidate on a real deployment. A candidate whose quoted
span is not inside the user's own text is refused as `embedded_source`. This is
also the prompt-injection control: pasted or tool-supplied text cannot become a
memory by being quoted.

**Why a ledger instead of the Event cursor.** An Interpretation completes
minutes after its source Event is committed, so the P4 recovery cursor has
usually moved past the source by then. `memory_observed_admissions` records each
Interpretation examined, with what was admitted and refused, in the same
transaction as the items — so admission is idempotent per Interpretation, and a
refusal is a durable, inspectable answer rather than a silent skip.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import clock, db
from ..canonical import canonical_json, sha256_hex
from . import filtering
from .eligibility import Candidate, document_id_for
from .types import OBSERVED_TYPE_FOR_KIND, Provenance

#: Processing outcomes whose Interpretation is final. `REJECTED` is excluded on
#: purpose: a human looked at the plan and said no.
TERMINAL_STATUSES: tuple[str, ...] = ("APPLIED", "NO_ACTION", "PENDING_REVIEW")
#: A negation is still a statement ("pure Chinese is not the wording we want").
ADMITTED_CERTAINTY: frozenset[str] = frozenset({"EXPLICIT", "NEGATED"})
#: `core_meta` key: when automatic admission started. Earlier Interpretations are
#: admitted only by the explicit backfill, never silently by the worker.
STARTED_KEY = "memory.observed.started_at"

REFUSAL_REASONS: tuple[str, ...] = (
    "interpretation_not_extracted",
    "source_missing",
    "source_not_native_user",
    "no_project",
    "kind_not_admitted",
    "not_explicit",
    "embedded_source",
    "secret_content_rejected",
    "directive_injection",
    "section_marker",
    "task_missing",
    "duplicate_in_message",
    "duplicate_of_existing",
)

# A host-supplied block: `<name …>` … `</name>` with the same name. The name
# class is closed so ordinary prose with a stray `<` is not swallowed.
_BLOCK_RE = re.compile(r"<([a-z][a-z0-9_-]{0,63})(?:\s[^<>]*)?>.*?</\1>", re.S)
# The header Codex puts above the typed request when it adds supplied context.
_REQUEST_HEADER_RE = re.compile(r"^#{1,6}\s*My request(?: for [^\n:]*)?:\s*$", re.M)
# Inside supplied context, two carriers hold words the user typed: a browser
# annotation's `Comment:` body, and the `"answer"` of a question-reply block.
_BROWSER_COMMENT_RE = re.compile(r"^Comment:\n(.+?)(?=\n[ \t]*\n|\n#|\Z)", re.M | re.S)
_BROWSER_SECTION_RE = re.compile(r"^# Browser comments:\s*$", re.M)
_COMMENT_HEADER_RE = re.compile(r"^## User Comment [0-9]+[ \t]*$", re.M)
_HEADING_RE = re.compile(r"^#{1,2}[ \t]+", re.M)
_REPLY_BLOCK_RE = re.compile(r"<([a-z][a-z0-9_-]*question_reply)>.*?</\1>", re.S)
_ANSWER_RE = re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"')


def user_regions(text: str) -> list[tuple[int, int]]:
    """Half-open ranges of `text` that are the user's own words.

    Everything before the last `## My request…:` header is supplied context, and
    any `<tag …>…</tag>` block is supplied context wherever it appears — except
    the two carriers of typed words inside it: a browser comment body and a
    question-reply answer.
    """
    start = 0
    for match in _REQUEST_HEADER_RE.finditer(text):
        start = match.end()
    all_blocks = [(m.start(), m.end()) for m in _BLOCK_RE.finditer(text)]
    blocks = [(lo, hi) for lo, hi in all_blocks if hi > start]
    regions: list[tuple[int, int]] = []
    cursor = start
    for block_start, block_end in blocks:
        if block_start > cursor:
            regions.append((cursor, block_start))
        cursor = max(cursor, block_end)
    if cursor < len(text):
        regions.append((cursor, len(text)))
    # Only the host's browser-comment section can restore a comment. In
    # particular, a document containing `Comment:` (or spoofed host headings)
    # must not reopen a range excluded by its enclosing context block.
    def outside_blocks(lo: int, hi: int) -> bool:
        return not any(lo < block_end and block_start < hi
                       for block_start, block_end in all_blocks)

    context_end = start or len(text)
    for section in _BROWSER_SECTION_RE.finditer(text, 0, context_end):
        if not outside_blocks(*section.span()):
            continue
        for heading in _HEADING_RE.finditer(text, section.end(), context_end):
            if not outside_blocks(*heading.span()):
                continue
            comment_header = _COMMENT_HEADER_RE.match(text, heading.start(), context_end)
            if comment_header is None:
                break
            next_heading = _HEADING_RE.search(text, comment_header.end(), context_end)
            end = next_heading.start() if next_heading else context_end
            for comment in _BROWSER_COMMENT_RE.finditer(text, comment_header.end(), end):
                if outside_blocks(*comment.span()):
                    regions.append(comment.span(1))
    for block in _REPLY_BLOCK_RE.finditer(text):
        regions.extend(m.span(1) for m in _ANSWER_RE.finditer(text, *block.span()))
    return sorted(regions)


def span_is_user_text(text: str, start: int, end: int) -> bool:
    return any(lo <= start and end <= hi for lo, hi in user_regions(text))


@dataclass
class Examination:
    """One Interpretation examined: what it admits and why the rest is refused."""

    interpretation_id: str
    source_event_id: str
    candidates: list[Candidate] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)

    def refuse(self, reason: str, index: int | None = None, detail: str = "") -> None:
        entry: dict[str, Any] = {"reason": reason}
        if index is not None:
            entry["candidate_index"] = index
        if detail:
            entry["detail"] = detail[:200]
        self.refusals.append(entry)


class ObservedCandidateSource:
    """Turns final daily-processing Interpretations into observed candidates."""

    name = "p2-observed-candidates"

    def pending(self, conn: sqlite3.Connection, *, limit: int,
                since: str | None = None) -> list[Examination]:
        """Interpretations not yet in the ledger, oldest processing first."""
        clauses = ["q.status IN (%s)" % ",".join("?" for _ in TERMINAL_STATUSES),
                   "q.interpretation_id IS NOT NULL",
                   "NOT EXISTS (SELECT 1 FROM memory_observed_admissions a"
                   " WHERE a.interpretation_id = q.interpretation_id)"]
        params: list[Any] = list(TERMINAL_STATUSES)
        if since is not None:
            clauses.append("q.updated_at >= ?")
            params.append(since)
        params.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            "SELECT q.event_id, q.interpretation_id FROM conversation_processing q WHERE "
            + " AND ".join(clauses) + " ORDER BY q.updated_at, q.event_id LIMIT ?",
            params,
        ).fetchall()
        # Shared across the batch, so the same statement in two messages of one
        # pass is admitted once, as it would be across two passes.
        batch: set[str] = set()
        return [self.examine(conn, row["event_id"], row["interpretation_id"], batch=batch)
                for row in rows]

    def examine(self, conn: sqlite3.Connection, event_id: str,
                interpretation_id: str, *, batch: set[str] | None = None) -> Examination:
        exam = Examination(interpretation_id=interpretation_id, source_event_id=event_id)
        result = conn.execute(
            "SELECT status, candidates_json FROM interpretation_results WHERE interpretation_id=?",
            (interpretation_id,),
        ).fetchone()
        if result is None or result["status"] != "EXTRACTED":
            exam.refuse("interpretation_not_extracted")
            return exam
        event = conn.execute(
            "SELECT event_id, event_type, actor_kind, project_id, task_id, occurred_at,"
            " payload_json FROM events WHERE event_id=?", (event_id,),
        ).fetchone()
        if event is None:
            exam.refuse("source_missing")
            return exam
        if event["event_type"] == "user.prompt" and event["actor_kind"] == "human":
            trust = "USER_STATED"
        elif event["event_type"] == "assistant.message":
            # Only ever explicitly selected (ADR 0029); still recallable, ranked
            # as what the agent said.
            trust = "AGENT_REPORTED"
        else:
            exam.refuse("source_not_native_user", detail=event["event_type"])
            return exam
        if event["project_id"] is None:
            exam.refuse("no_project")
            return exam
        text = json.loads(event["payload_json"]).get("text") or ""
        seen: set[str] = set()
        batch = set() if batch is None else batch
        for index, candidate in enumerate(json.loads(result["candidates_json"])):
            admitted = self._candidate(conn, exam, event, text, trust, index, candidate,
                                       seen, batch)
            if admitted is not None:
                exam.candidates.append(admitted)
        return exam

    def _candidate(self, conn: sqlite3.Connection, exam: Examination, event: sqlite3.Row,
                   text: str, trust: str, index: int, candidate: dict[str, Any],
                   seen: set[str], batch: set[str]) -> Candidate | None:
        memory_type = OBSERVED_TYPE_FOR_KIND.get(candidate.get("kind", ""))
        if memory_type is None:
            exam.refuse("kind_not_admitted", index, candidate.get("kind", ""))
            return None
        if candidate.get("certainty") not in ADMITTED_CERTAINTY:
            exam.refuse("not_explicit", index, str(candidate.get("certainty")))
            return None
        span = candidate.get("source_span") or {}
        start, end = span.get("start"), span.get("end")
        if not (isinstance(start, int) and isinstance(end, int)
                and span_is_user_text(text, start, end)):
            exam.refuse("embedded_source", index)
            return None
        content = " ".join(str(candidate.get("content") or "").split())
        sanitized = filtering.sanitize(content)
        if sanitized.rejected:
            exam.refuse("secret_content_rejected", index,
                        ",".join(f.rule for f in sanitized.findings))
            return None
        body, had_marker = filtering.neutralize_markers(sanitized.text)
        if had_marker:
            exam.refuse("section_marker", index)
            return None
        directive = filtering.directive_reason(body)
        if directive:
            exam.refuse("directive_injection", index, directive)
            return None
        key = canonical_json((memory_type, body.casefold()))
        if key in seen:
            exam.refuse("duplicate_in_message", index)
            return None
        seen.add(key)
        # "暂时不要关机" said in three messages is one statement. Matched on the
        # stored content hash within the project, against ACTIVE items and this
        # pass's earlier admissions; the refusal names the item it repeats.
        project_key = canonical_json((event["project_id"], memory_type, sha256_hex(body)))
        existing = conn.execute(
            "SELECT memory_id FROM memory_items WHERE admission='OBSERVED' AND status='ACTIVE'"
            " AND project_id=? AND memory_type=? AND content_sha256=? LIMIT 1",
            (event["project_id"], memory_type, sha256_hex(body)),
        ).fetchone()
        if existing is not None or project_key in batch:
            exam.refuse("duplicate_of_existing", index,
                        existing["memory_id"] if existing is not None else "")
            return None
        batch.add(project_key)
        scope = candidate.get("scope") or {}
        task_id = None
        if scope.get("kind") == "TASK":
            task_id = scope.get("task_id") or event["task_id"]
            if task_id is not None and conn.execute(
                "SELECT 1 FROM tasks WHERE task_id=? AND project_id=?",
                (task_id, event["project_id"]),
            ).fetchone() is None:
                exam.refuse("task_missing", index, task_id)
                return None
        label = memory_type.removeprefix("OBSERVED_").lower()
        speaker = "user stated" if trust == "USER_STATED" else "agent reported"
        return Candidate(
            source_event_id=event["event_id"],
            memory_type=memory_type,
            project_id=event["project_id"],
            task_id=task_id,
            scope_kind="project",
            title=f"{label}: {body}"[:200],
            body=body,
            occurred_at=event["occurred_at"],
            provenance=Provenance(source_event_ids=(event["event_id"],),
                                  origin_event_id=event["event_id"]),
            document_id=document_id_for(event["event_id"], memory_type, index),
            context=f"jasmine observed {label} ({speaker})",
            candidate_index=index,
            admission="OBSERVED",
            trust=trust,
            interpretation_id=exam.interpretation_id,
        )


def started_at(conn: sqlite3.Connection) -> str | None:
    value = db.get_meta(conn, STARTED_KEY, None)
    return str(value) if value else None


def ensure_started(conn: sqlite3.Connection) -> str:
    """The automatic-admission start, recorded on first use."""
    current = started_at(conn)
    if current is not None:
        return current
    stamp = clock.now_rfc3339()
    with db.translate_lock_errors(), db.transaction(conn):
        current = started_at(conn)
        if current is None:
            db.set_meta(conn, STARTED_KEY, stamp)
            current = stamp
    return current
