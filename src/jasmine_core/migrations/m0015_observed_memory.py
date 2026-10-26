"""Observed Memory: typed, trust-labelled items admitted from conversations (ADR 0034).

`memory_items` and `memory_jobs` are rebuilt, because SQLite cannot alter a
CHECK or a UNIQUE constraint in place and both tables carry the two this ADR
changes:

* `memory_type` widens from the five Authority-derived types to nine; the four
  new `OBSERVED_*` types are only legal with `admission='OBSERVED'`, and the
  original five only with `admission='AUTHORITY'`.
* `memory_items.source_event_id` was UNIQUE, so one message could hold at most
  one memory. It becomes `(source_event_id, candidate_index)`: one item per
  extracted candidate. Authority items keep `candidate_index=0`.
* `memory_jobs` was UNIQUE on `(source_event_id, kind)`. With several items per
  source that would let the first item's job stand in for the others, so the job
  key becomes `(memory_id, kind)` — one job per item per kind, which is what the
  old key meant while items and sources were one-to-one.

The rebuild runs inside the migration's single transaction with foreign keys
still on. `defer_foreign_keys` moves the check of every child row
(`memory_item_sources`, `memory_job_attempts`, `memory_query_trace_items`) to
COMMIT; the tables are recreated under their original names and refilled with
the same primary keys before then, so every reference resolves again and the
deferred check passes. Dropping a table fires no DELETE trigger, so the
"never deleted" triggers are not involved; they are recreated unchanged, with
the new identity columns added to the immutable set.

`memory_observed_admissions` is the ledger of which Interpretations the observed
source has already examined, with what it admitted and refused. It is what makes
admission idempotent per Interpretation without an Event-sequence cursor: an
Interpretation finishes minutes after its source Event is committed, so the P4
recovery cursor has usually passed it already.
"""

from __future__ import annotations

NAME = "m0015_observed_memory"
VERSION = 15

_AUTHORITY_TYPES = ("'CONFIRMED_DECISION','STABLE_PROJECT_FACT','RESOLVED_ISSUE',"
                    "'VERIFIED_OUTCOME','FAILURE_LESSON'")
_OBSERVED_TYPES = ("'OBSERVED_CONVENTION','OBSERVED_DECISION','OBSERVED_CORRECTION',"
                   "'OBSERVED_ACCEPTANCE'")

_ITEM_COPY_COLUMNS = (
    "memory_id, bank_id, scope_kind, project_id, task_id, memory_type, document_id,"
    " source_event_id, evidence_ids_json, origin_rule_id, origin_rule_version,"
    " origin_event_id, content_sha256, sanitized_text, status, pinned, revision,"
    " engine_state, receipt_json, last_error_code, safe_error_summary, remote_deleted_at,"
    " lifecycle_reason, last_recalled_at, recall_count, created_at, updated_at"
)

_JOB_COLUMNS = (
    "job_id, kind, memory_id, source_event_id, project_id, task_id, memory_type, bank_id,"
    " document_id, content_sha256, status, retry_count, max_attempts, next_attempt_at,"
    " lease_owner, lease_expires_at, claim_generation, last_error_code, safe_error_summary,"
    " receipt_json, created_at, updated_at, completed_at"
)

STATEMENTS = (
    "PRAGMA defer_foreign_keys = ON",
    f"CREATE TABLE m0015_items_copy AS SELECT {_ITEM_COPY_COLUMNS} FROM memory_items",
    f"CREATE TABLE m0015_jobs_copy AS SELECT {_JOB_COLUMNS} FROM memory_jobs",
    "DROP TABLE memory_jobs",
    "DROP TABLE memory_items",
    f"""
    CREATE TABLE memory_items (
        memory_id            TEXT PRIMARY KEY,
        bank_id              TEXT NOT NULL,
        scope_kind           TEXT NOT NULL CHECK(scope_kind IN ('global','project')),
        project_id           TEXT REFERENCES projects(project_id),
        task_id              TEXT REFERENCES tasks(task_id),
        memory_type          TEXT NOT NULL CHECK(memory_type IN (
                                {_AUTHORITY_TYPES},
                                {_OBSERVED_TYPES})),
        document_id          TEXT NOT NULL UNIQUE,
        source_event_id      TEXT NOT NULL REFERENCES events(event_id),
        candidate_index      INTEGER NOT NULL DEFAULT 0 CHECK(candidate_index>=0),
        admission            TEXT NOT NULL DEFAULT 'AUTHORITY'
                                CHECK(admission IN ('AUTHORITY','OBSERVED')),
        trust                TEXT NOT NULL DEFAULT 'VERIFIED'
                                CHECK(trust IN ('VERIFIED','USER_STATED','AGENT_REPORTED')),
        interpretation_id    TEXT REFERENCES interpretations(interpretation_id),
        evidence_ids_json    TEXT NOT NULL CHECK(json_valid(evidence_ids_json)),
        origin_rule_id       TEXT REFERENCES rules(rule_id),
        origin_rule_version  INTEGER,
        origin_event_id      TEXT REFERENCES events(event_id),
        content_sha256       TEXT NOT NULL CHECK(length(content_sha256)=64),
        sanitized_text       TEXT NOT NULL,
        status               TEXT NOT NULL CHECK(status IN ('ACTIVE','OBSOLETE','DELETED')),
        pinned               INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
        revision             INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
        engine_state         TEXT NOT NULL DEFAULT 'PENDING_RETAIN' CHECK(engine_state IN (
                                'PENDING_RETAIN','RETAINED','RETAIN_FAILED',
                                'DELETE_PENDING','DELETED','DELETE_FAILED')),
        receipt_json         TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
        last_error_code      TEXT,
        safe_error_summary   TEXT,
        remote_deleted_at    TEXT,
        lifecycle_reason     TEXT,
        last_recalled_at     TEXT,
        recall_count         INTEGER NOT NULL DEFAULT 0 CHECK(recall_count>=0),
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL,
        CHECK ((scope_kind='global'  AND project_id IS NULL     AND task_id IS NULL) OR
               (scope_kind='project' AND project_id IS NOT NULL)),
        CHECK ((origin_rule_id IS NULL AND origin_rule_version IS NULL) OR
               (origin_rule_id IS NOT NULL AND origin_rule_version IS NOT NULL)),
        -- The tier is decided by the type, never by a caller's label: an
        -- Authority type cannot be written as observed, nor the reverse.
        CHECK ((admission='AUTHORITY' AND memory_type IN ({_AUTHORITY_TYPES})
                AND trust='VERIFIED' AND candidate_index=0) OR
               (admission='OBSERVED' AND memory_type IN ({_OBSERVED_TYPES})
                AND trust IN ('USER_STATED','AGENT_REPORTED')
                AND interpretation_id IS NOT NULL AND origin_rule_id IS NULL)),
        -- Automatic admission never writes the global bank (ADR 0034 §4).
        CHECK (admission='AUTHORITY' OR scope_kind='project'),
        UNIQUE (source_event_id, candidate_index),
        FOREIGN KEY(origin_rule_id, origin_rule_version)
            REFERENCES rule_versions(rule_id, version)
    )
    """,
    f"INSERT INTO memory_items ({_ITEM_COPY_COLUMNS}) SELECT {_ITEM_COPY_COLUMNS} FROM m0015_items_copy",
    f"""
    CREATE TABLE memory_jobs (
        job_id             TEXT PRIMARY KEY,
        kind               TEXT NOT NULL CHECK(kind IN ('retain','delete','invalidate')),
        memory_id          TEXT NOT NULL REFERENCES memory_items(memory_id),
        source_event_id    TEXT NOT NULL REFERENCES events(event_id),
        project_id         TEXT REFERENCES projects(project_id),
        task_id            TEXT REFERENCES tasks(task_id),
        memory_type        TEXT NOT NULL CHECK(memory_type IN (
                             {_AUTHORITY_TYPES},
                             {_OBSERVED_TYPES})),
        bank_id            TEXT NOT NULL,
        document_id        TEXT NOT NULL,
        content_sha256     TEXT NOT NULL CHECK(length(content_sha256)=64),
        status             TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','DONE','FAILED')),
        retry_count        INTEGER NOT NULL DEFAULT 0 CHECK(retry_count>=0),
        max_attempts       INTEGER NOT NULL CHECK(max_attempts>=1),
        next_attempt_at    TEXT NOT NULL,
        lease_owner        TEXT,
        lease_expires_at   TEXT,
        claim_generation   INTEGER NOT NULL DEFAULT 0,
        last_error_code    TEXT,
        safe_error_summary TEXT,
        receipt_json       TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL,
        completed_at       TEXT,
        CHECK (lease_owner IS NULL OR lease_expires_at IS NOT NULL),
        CHECK (status <> 'DONE' OR completed_at IS NOT NULL)
    )
    """,
    f"INSERT INTO memory_jobs ({_JOB_COLUMNS}) SELECT {_JOB_COLUMNS} FROM m0015_jobs_copy",
    "DROP TABLE m0015_items_copy",
    "DROP TABLE m0015_jobs_copy",
    # Indexes: the original set, plus the source/interpretation lookups.
    "CREATE INDEX ix_memory_items_project ON memory_items(project_id, status, created_at, memory_id)",
    "CREATE INDEX ix_memory_items_bank ON memory_items(bank_id, status, created_at)",
    "CREATE INDEX ix_memory_items_type ON memory_items(memory_type, status, created_at)",
    "CREATE INDEX ix_memory_items_recall ON memory_items(status, pinned, last_recalled_at)",
    "CREATE INDEX ix_memory_items_interpretation ON memory_items(interpretation_id)",
    "CREATE UNIQUE INDEX ux_memory_jobs_item ON memory_jobs(memory_id, kind)",
    "CREATE INDEX ix_memory_jobs_source ON memory_jobs(source_event_id, kind)",
    "CREATE INDEX ix_memory_jobs_due ON memory_jobs(status, next_attempt_at, job_id)",
    "CREATE INDEX ix_memory_jobs_lease ON memory_jobs(status, lease_expires_at, job_id)",
    "CREATE INDEX ix_memory_jobs_project ON memory_jobs(project_id, status, created_at, job_id)",
    "CREATE INDEX ix_memory_jobs_memory ON memory_jobs(memory_id, kind, created_at)",
    """
    CREATE TRIGGER trg_memory_items_no_delete
    BEFORE DELETE ON memory_items
    BEGIN
        SELECT RAISE(ABORT, 'memory items are never deleted; use the DELETED tombstone state');
    END
    """,
    """
    CREATE TRIGGER trg_memory_items_no_replace
    BEFORE INSERT ON memory_items
    WHEN EXISTS (SELECT 1 FROM memory_items WHERE memory_id = NEW.memory_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory items are append-only: memory_id already exists');
    END
    """,
    """
    CREATE TRIGGER trg_memory_items_identity_immutable
    BEFORE UPDATE OF memory_id, bank_id, scope_kind, project_id, task_id, memory_type,
                     document_id, source_event_id, candidate_index, admission, trust,
                     interpretation_id, content_sha256, sanitized_text,
                     evidence_ids_json, created_at
    ON memory_items
    BEGIN
        SELECT RAISE(ABORT, 'memory item identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_memory_jobs_no_delete
    BEFORE DELETE ON memory_jobs
    BEGIN
        SELECT RAISE(ABORT, 'memory jobs are never deleted; FAILED is the terminal state');
    END
    """,
    """
    CREATE TRIGGER trg_memory_jobs_no_replace
    BEFORE INSERT ON memory_jobs
    WHEN EXISTS (SELECT 1 FROM memory_jobs WHERE job_id = NEW.job_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory jobs are append-only: job_id already exists');
    END
    """,
    """
    CREATE TRIGGER trg_memory_jobs_identity_immutable
    BEFORE UPDATE OF job_id, kind, memory_id, source_event_id, project_id, task_id,
                     memory_type, bank_id, document_id, content_sha256, max_attempts, created_at
    ON memory_jobs
    BEGIN
        SELECT RAISE(ABORT, 'memory job identity is immutable; a retry may not change what is retained');
    END
    """,
    # -- The observed-admission ledger ------------------------------------
    """
    CREATE TABLE memory_observed_admissions (
        interpretation_id  TEXT PRIMARY KEY REFERENCES interpretations(interpretation_id),
        source_event_id    TEXT NOT NULL REFERENCES events(event_id),
        admitted_count     INTEGER NOT NULL CHECK(admitted_count>=0),
        refusals_json      TEXT NOT NULL CHECK(json_valid(refusals_json)),
        trigger            TEXT NOT NULL CHECK(trigger IN ('automatic','backfill')),
        created_at         TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_memory_observed_admissions_source ON memory_observed_admissions(source_event_id)",
    """
    CREATE TRIGGER trg_memory_observed_admissions_update
    BEFORE UPDATE ON memory_observed_admissions
    BEGIN
        SELECT RAISE(ABORT, 'observed admission records are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_observed_admissions_delete
    BEFORE DELETE ON memory_observed_admissions
    BEGIN
        SELECT RAISE(ABORT, 'observed admission records are append-only');
    END
    """,
)
