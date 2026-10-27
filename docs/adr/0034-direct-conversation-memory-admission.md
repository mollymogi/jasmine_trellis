# ADR 0034: Direct conversation-to-Memory admission with typed, trust-labelled items

Status: implemented on `feat/memory-direct-admission` (schema 15); not yet
deployed. It amends ADR 0014 §4 (eligibility) and ADR 0029 (daily conversation →
Memory); it does not change ADR 0014 §1.

## Context

ADR 0029 made the daily path *Conversation → Interpreter → Resolver → Review →
Rule → Memory*. With real use on one local deployment (2026-10-26, 1,659 Events,
569 `user.prompt`, 411 Interpretations) the result was:

| Stage | Count |
| --- | --- |
| Memory items | 8, of which 6 are deployment self-test Evidence (`TEST PASS evidence … command: <sha>`, `the step reached FAILED`) and 2 carry human knowledge |
| Candidates by kind | `TASK_CREATE_OR_ATTACH` 136, `NO_STRUCTURE` 106, `CORRECTION` 20, `RULE` 14, `ACCEPTANCE` 8, `REQUIRED_CAPABILITY` 6, `DECISION` **0** |
| Reviews | 70 `PENDING`, 1 `APPROVED` over three days |
| Memory-eligible candidates still pending review | about 5 statements in 4 messages |

Three structural causes, none of them operational:

1. **Memory is a copy of Authority.** The only conversation route into Memory is
   an approved project/global `RULE` or `DECISION`. Those Rules are already
   loaded deterministically by P3, so Memory adds nothing that Authority does
   not already deliver. What a recall engine is for — what was tried, chosen,
   corrected or learned — is exactly what the gate refuses.
2. **Task scope is discarded.** Almost every candidate is task-scoped, and a
   task-scoped Rule is refused (`kind_not_memory_eligible`). "Use v3", "shot C
   uses the test02 HD reference", "tests run in the cloud, Linux first" never
   reach Memory.
3. **Human review is the admission gate, and it does not run.** Requiring a
   human command for every memory turns Memory into a review backlog.

The goal of not letting unverified text become authority is right. The mistake
is enforcing it at *Memory admission* instead of at *Authority activation*.

## Decision

### 1. Two admission tiers, one table

Every Memory item gets an `admission`:

| Admission | What it is | Gate |
| --- | --- | --- |
| `AUTHORITY` | the existing five types, unchanged (`CONFIRMED_DECISION`, `STABLE_PROJECT_FACT`, `RESOLVED_ISSUE`, `VERIFIED_OUTCOME`, `FAILURE_LESSON`) | ADR 0014 §4, unchanged |
| `OBSERVED` | a typed statement extracted from a captured conversation | §3 below — automatic, **no human review** |

ADR 0014 §1 still holds for both tiers: no Memory item, of either tier, may
activate, supersede or retire a Rule, move State, mint Evidence or raise a
revision. An `OBSERVED` item is recallable history, never a Guard input and never
rendered in `[JASMINE AUTHORITY]`.

### 2. Categories

`OBSERVED` items use four new `memory_type` values, mapped from the existing P2
candidate kinds. No Interpreter output schema change is required.

| Candidate kind | `memory_type` | Example |
| --- | --- | --- |
| `RULE` | `OBSERVED_CONVENTION` | 项目文档今后统一使用中文；武器、道具、背景保持统一画风 |
| `DECISION` | `OBSERVED_DECISION` | 就用 v3 |
| `CORRECTION` | `OBSERVED_CORRECTION` | 原视频时长不是 5 秒 |
| `ACCEPTANCE` | `OBSERVED_ACCEPTANCE` | the user accepted shot A's result |
| `TASK_CREATE_OR_ATTACH`, `REQUIRED_CAPABILITY`, `NO_STRUCTURE` | not admitted | the Task object already records the first; the second is a task binding whose content is a bare capability name (`remotion`, `subagent`); the third has no content |

`MEMORY_TYPES` becomes nine values; `REQUIRED_PROVENANCE` gains
`"interpretation_candidate"` for the four new ones. `type_not_requested` keeps
working as a recall facet.

### 3. Automatic admission rules

An `OBSERVED` candidate is admitted when all hold, otherwise refused with a
machine-readable reason recorded in the scan report:

| Check | Refusal reason |
| --- | --- |
| the source is a `user.prompt` from a configured local native producer on this host (ADR 0029's `_eligible`) | `source_not_native_user` |
| the Interpretation is `EXTRACTED` and passed schema/span validation | `interpretation_not_extracted` |
| candidate `certainty` is `EXPLICIT` or `NEGATED` (a negation is still a statement) | `not_explicit` |
| the quoted span lies in the user's own text: after the last `## My request…:` header, outside any `<tag …>…</tag>` block — except the two carriers of typed words inside supplied context, a browser annotation's `Comment:` body and a `*question_reply` block's `"answer"` | `embedded_source` |
| the same type and content is not already an ACTIVE observed item of the project, nor admitted earlier in the same pass | `duplicate_of_existing` (names the item) |
| the existing secret filter and `directive_injection` / `section_marker` checks pass | existing reasons |

The Resolver disposition (`APPLIED`, `PENDING_REVIEW`, `NO_ACTION`) does **not**
affect admission. A candidate awaiting review is admitted as `OBSERVED` now and
may later also become Authority (§6).

Assistant messages are still never auto-processed (ADR 0029). An explicitly
selected assistant message may produce `OBSERVED` items with
`trust=AGENT_REPORTED`; it can never produce `AUTHORITY` items.

### 4. Trust and scope

New column `trust`, aligned with the Mesh evidence levels:

| `trust` | Source |
| --- | --- |
| `VERIFIED` | `AUTHORITY` items (Rule, Evidence or committed transition) |
| `USER_STATED` | the user's own words in a native `user.prompt` |
| `AGENT_REPORTED` | an explicitly selected assistant message |

Scope follows the candidate, never wider than the source:

* `TASK` → project bank, `task_id` set (already allowed by the existing CHECK).
* `PROJECT` → project bank, `task_id` NULL.
* `GLOBAL` → **project bank** of the source. The global bank keeps holding only
  explicitly authorised global content; automatic admission never writes it.

### 5. Recall ranking and rendering

This ranking is the Memory recall API's (`MemoryAdapter.recall`). The P3
Context Pack Memory slot (ADR 0012, `context/memory_provider.py`) is a separate
frozen contract that today accepts only Evidence-backed `VERIFIED_OUTCOME` and
`FAILURE_LESSON` — not even `CONFIRMED_DECISION` — and is unchanged here, so
observed items are not yet injected into Context Packs; extending that slot is a
follow-up with its own ADR (ADR 0036, proposed).

Recall still runs through the existing filters (provenance, foreign project,
obsolete, superseded/retired decision, duplicate, injection, budget). Ranking
inside the Memory partition becomes, in order: `pinned`, `admission`
(`AUTHORITY` before `OBSERVED`), task match (same `task_id` first), engine
relevance, recency. The Context section is unchanged
(`[JASMINE RELEVANT MEMORY - NON AUTHORITATIVE]`); each `OBSERVED` line carries
its category and `trust` so an Agent can tell "the user said" from "verified".
When an `OBSERVED` item conflicts with current Authority, the existing
Authority comparison (ADR 0022) shows Authority as winning.

Supersession is recency plus explicit lifecycle in v1: a newer item of the same
category and scope ranks above an older one, and the Memory page offers
"obsolete" (existing audited command). Automatic similarity-based supersession is
not part of this ADR.

### 6. Review becomes promotion, not admission

The Review queue and the human approve command are unchanged and still the only
way to create Authority. A reviewed Rule's origin Event is the `review.approved`
command, whose payload names the source message, Interpretation and candidate
index. When an ACTIVE `AUTHORITY` item has such an origin, the `OBSERVED` item
for exactly that `(source_event_id, interpretation_id, candidate_index)` is
withheld from recall with the new filter reason `promoted`. It is derived at
read time rather than written as a lifecycle change: no extra Event, no actor
identity needed by the worker, and it follows the Authority item if that is
later obsoleted. A "promote to rule" action on the Memory page is UI work left
for a follow-up; it must open the existing Review flow, not bypass it.

### 7. Granularity: one item per candidate

One message can carry several statements. `memory_items.source_event_id` and
`memory_jobs (source_event_id, kind)` are UNIQUE today, so a message could hold
at most one memory. Migration `m0015` (schema 15) rebuilds both tables:

* add `candidate_index INTEGER NOT NULL DEFAULT 0`, `admission TEXT NOT NULL
  DEFAULT 'AUTHORITY'`, `trust TEXT NOT NULL DEFAULT 'VERIFIED'`, nullable
  `interpretation_id` referencing `interpretations`;
* uniqueness becomes `(source_event_id, candidate_index)` on items and
  `(memory_id, kind)` on jobs — one job per item per kind, which is what the old
  `(source_event_id, kind)` meant while items and sources were one-to-one;
* CHECKs bind the tier to the type: observed items need an `interpretation_id`,
  a non-`VERIFIED` trust, no origin Rule and project scope;
* widen the `memory_type` CHECK to nine values; CHECK `admission='AUTHORITY'`
  iff `memory_type` is one of the original five;
* `document_id_for` takes the candidate index, so engine document ids stay
  deterministic per candidate;
* every append-only and immutability trigger is recreated, with the new columns
  added to the identity-immutable set.

Existing rows migrate as `AUTHORITY`, `candidate_index=0`, `trust='VERIFIED'`;
Authority document ids are unchanged, so re-scans land on the already-retained
remote documents. The rebuild runs in the migration's one transaction with
foreign keys on, using `PRAGMA defer_foreign_keys` so child rows
(`memory_item_sources`, `memory_job_attempts`, `memory_query_trace_items`) are
checked at COMMIT, after both tables are refilled under their original names.
Rehearsed on a `.backup` copy of the deployment above: every memory table's row
count unchanged, `foreign_key_check` empty, `integrity_check` ok, no checksum
drift. Production still needs the ADR 0029 procedure: stop writers, fresh
verified backup, migrate, restart.

### 8. Source and pipeline

`ObservedCandidateSource` (`memory/observed.py`) reads `conversation_processing`
rows in `APPLIED`, `NO_ACTION` or `PENDING_REVIEW` (not `FAILED`, and not
`REJECTED`, where a human said no) whose Interpretation is `EXTRACTED`, and emits
one candidate per admitted `candidate_index`. It runs beside the
`SourceRegistry` scan rather than inside it, because the registry is driven by
the Event-sequence cursor and an Interpretation finishes minutes after its
source Event is committed — the cursor has usually passed it. Instead m0015 adds
`memory_observed_admissions`, an append-only ledger keyed by
`interpretation_id` with admitted count, refusals and trigger, written in the
same transaction as the items. `JobStore.enqueue_observed` does that; the Memory
worker calls it every tick after the registry scan. Jobs, leases, retries and
the Hindsight transport are the existing P4 machinery; nothing new talks to the
engine.

### 9. Evidence-derived memories must say something

`VERIFIED_OUTCOME` / `FAILURE_LESSON` from Evidence must say what was verified:
`payload.summary` when present, otherwise the Step and Task titles the result was
recorded against ("TEST PASS for step “…” of task “…”"). Hook-recorded Evidence
carries no summary, so the titles are the normal case. Evidence with neither is
refused as `evidence_without_summary`; hashes and ids remain provenance, not the
body. Existing items are immutable, so their old hash-only bodies stay until the
operator obsoletes them through the audited lifecycle command.

### 10. DECISION extraction

The daily prompt (`jasmine.interpreter.daily.v1` → `.v2`, now defined once as
`interpreter_provider.daily_prompt()` for Codex and the local CLI clients) adds
that a choice between alternatives ("就用 v3", "go with plan A") is a `DECISION`
at the narrowest true scope; that one message's distinct statements are separate
candidates and `NO_STRUCTURE` is never combined with others (the
`no_structure_mixed` validation failure); and that candidate content stays in
the user's language, because observed items store it verbatim.
`prompt_version`/`prompt_digest` change as usual; already extracted
Interpretations are not re-run.

### 11. Backfill

Automatic admission records its start in `core_meta`
(`memory.observed.started_at`) on first use and only takes Interpretations whose
processing finished after it. Older ones are admitted by the explicit operator
command, which re-reads existing candidates (no model calls), is bounded per
call (`--limit`, at most 500), and is a dry run unless `--apply` is given:

```sh
jasmine-core memory --db <core.db> observed-backfill --limit 500          # report only
jasmine-core memory --db <core.db> observed-backfill --limit 500 --apply  # write
```

## Consequences

* Memory grows with use. A backfill dry run against a backup of the deployment
  above (256 finished Interpretations, 2026-10-26, retry batch still running)
  admits 32 items — 11 conventions, 14 corrections, 7 acceptances — and refuses
  2 wrapper quotes as `embedded_source` and 2 repeats as `duplicate_of_existing`;
  today there are 2 useful items. Applying it twice admits nothing the second
  time, and the one statement already approved as a Rule is withheld as
  `promoted`.
* Wrong or stale statements will be in Memory. That is accepted: they are
  labelled, ranked below Authority, obsoletable, and cannot block anything.
* Prompt-injection exposure moves from "cannot enter" to "enters, labelled,
  filtered at recall". §3's `embedded_source` and the existing
  `directive_injection` filter are the controls; Authority stays human-gated.
* The Review backlog stops blocking Memory; it remains the path to enforceable
  Rules.

## Not claimed

No Context Pack injection of observed items (§5), no automatic semantic
deduplication beyond exact content, no contradiction detection, no Memory page
UI for the new fields, no native enforcement change, no change to Guard, Context section markers or HARD Rule
loading, no assistant auto-processing, and no evidence that recall quality
improves until measured on a live deployment.
