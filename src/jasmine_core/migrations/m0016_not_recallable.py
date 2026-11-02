"""Retained items the engine never extracted become NOT_RECALLABLE (ADR 0037).

A retain the engine accepted but turned into zero memory units is final. The
extractor does not produce a unit from a short acknowledgement such as
"不需要补录。", so a retry changes nothing. Before this migration those items
stayed `RETAIN_FAILED` and their jobs `FAILED`, and health reported Memory as
broken while the rest of the pipeline was healthy.

`memory_items` is rebuilt, because SQLite cannot alter a CHECK in place. Its
`engine_state` CHECK gains `NOT_RECALLABLE`. The rebuild follows m0015: foreign
keys in the child tables are deferred to COMMIT, the rows are copied through a
temporary table with the same primary keys, and the three triggers are recreated
unchanged.

The rows that match the rule are then converted in the same transaction: the
item moves to `NOT_RECALLABLE` and its retain job moves to `DONE`, with a receipt
that says why. Nothing is deleted and no identity column changes.
"""

from __future__ import annotations

NAME = "m0016_not_recallable"
VERSION = 16

_AUTHORITY_TYPES = ("'CONFIRMED_DECISION','STABLE_PROJECT_FACT','RESOLVED_ISSUE',"
                    "'VERIFIED_OUTCOME','FAILURE_LESSON'")
_OBSERVED_TYPES = ("'OBSERVED_CONVENTION','OBSERVED_DECISION','OBSERVED_CORRECTION',"
                   "'OBSERVED_ACCEPTANCE'")

ITEM_COLUMNS = (
    "memory_id, bank_id, scope_kind, project_id, task_id, memory_type, document_id,"
    " source_event_id, candidate_index, admission, trust, interpretation_id,"
    " evidence_ids_json, origin_rule_id, origin_rule_version, origin_event_id,"
    " content_sha256, sanitized_text, status, pinned, revision, engine_state,"
    " receipt_json, last_error_code, safe_error_summary, remote_deleted_at,"
    " lifecycle_reason, last_recalled_at, recall_count, created_at, updated_at"
)


STATEMENTS = (
    "PRAGMA defer_foreign_keys = ON",
    f"CREATE TABLE m0016_items_copy AS SELECT {ITEM_COLUMNS} FROM memory_items",
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
                                'DELETE_PENDING','DELETED','DELETE_FAILED',
                                'NOT_RECALLABLE')),
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
    f"INSERT INTO memory_items ({ITEM_COLUMNS}) SELECT {ITEM_COLUMNS} FROM m0016_items_copy",
    "DROP TABLE m0016_items_copy",
    "CREATE INDEX ix_memory_items_project ON memory_items(project_id, status, created_at, memory_id)",
    "CREATE INDEX ix_memory_items_bank ON memory_items(bank_id, status, created_at)",
    "CREATE INDEX ix_memory_items_type ON memory_items(memory_type, status, created_at)",
    "CREATE INDEX ix_memory_items_recall ON memory_items(status, pinned, last_recalled_at)",
    "CREATE INDEX ix_memory_items_interpretation ON memory_items(interpretation_id)",
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
    # Convert only what the rule matches. The item goes first, so the job update
    # below selects exactly the items it has just changed.
    "UPDATE memory_items SET engine_state='NOT_RECALLABLE'"
    " WHERE engine_state='RETAIN_FAILED' AND last_error_code='engine_extracted_no_units'"
    " AND EXISTS (SELECT 1 FROM memory_jobs j WHERE j.memory_id=memory_items.memory_id"
    " AND j.kind='retain' AND j.status='FAILED' AND j.retry_count>=j.max_attempts"
    " AND j.last_error_code='engine_extracted_no_units')",
    "UPDATE memory_jobs SET status='DONE', last_error_code=NULL, safe_error_summary=NULL,"
    " receipt_json='{\"status\":\"not_recallable\",\"reason\":\"engine_extracted_no_units\"}',"
    " completed_at=updated_at"
    " WHERE kind='retain' AND status='FAILED' AND retry_count>=max_attempts"
    " AND last_error_code='engine_extracted_no_units'"
    " AND memory_id IN (SELECT memory_id FROM memory_items WHERE engine_state='NOT_RECALLABLE')",
    # Empty extraction is an accepted engine response, not an outage. Preserve
    # unrelated errors and the timestamps of actual retain/recall successes.
    "UPDATE memory_engine_status SET last_error_code=NULL, safe_error_summary=NULL"
    " WHERE last_error_code='engine_extracted_no_units'"
    " AND NOT EXISTS (SELECT 1 FROM memory_items WHERE engine_state='RETAIN_FAILED'"
    " AND last_error_code='engine_extracted_no_units')",
)
