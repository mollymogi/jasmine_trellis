"""Session Checkpoints: an append-only record of what each session did (ADR 0039).

A Task-scoped Checkpoint (ADR 0011) needs a Task, and captured conversations
are never bound to one, so none had ever been written. A session Checkpoint is
taken per session instead, at PreCompact and SessionEnd, or recovered by the
next session when the agent ended without saying so. Each row references the
server-created `session.checkpointed` Event committed in the same transaction.

The record is derived from the session's own Events, so it adds no new text:
it is a dated, bounded view of what Core already holds. Rows are never updated.
Idempotency is the natural key: the same session, reason and covered Event
sequence is one Checkpoint, however often the hook is delivered.
"""

from __future__ import annotations

NAME = "m0017_session_checkpoints"
VERSION = 17

STATEMENTS = (
    """
    CREATE TABLE session_checkpoints (
        session_checkpoint_id TEXT PRIMARY KEY,
        event_id              TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        session_id            TEXT NOT NULL REFERENCES sessions(session_id),
        project_id            TEXT NOT NULL REFERENCES projects(project_id),
        host_id               TEXT NOT NULL REFERENCES hosts(host_id),
        actor_id              TEXT NOT NULL REFERENCES actors(actor_id),
        native_session_id     TEXT,
        agent                 TEXT NOT NULL,
        reason                TEXT NOT NULL CHECK(reason IN ('PRE_COMPACT','SESSION_END','RECOVERED')),
        workspace_root        TEXT NOT NULL,
        family_root           TEXT NOT NULL,
        last_event_seq        INTEGER NOT NULL CHECK(last_event_seq > 0),
        turns                 INTEGER NOT NULL CHECK(turns >= 0),
        started_at            TEXT,
        last_activity_at      TEXT,
        ended_at              TEXT,
        record_json           TEXT NOT NULL,
        record_sha256         TEXT NOT NULL,
        created_at            TEXT NOT NULL,
        UNIQUE(session_id, reason, last_event_seq)
    )
    """,
    "CREATE INDEX ix_session_checkpoints_family ON session_checkpoints(family_root, last_activity_at)",
    "CREATE INDEX ix_session_checkpoints_session ON session_checkpoints(session_id, last_event_seq)",
    """
    CREATE TRIGGER session_checkpoints_no_update BEFORE UPDATE ON session_checkpoints
    BEGIN SELECT RAISE(ABORT, 'session_checkpoints is append-only'); END
    """,
    """
    CREATE TRIGGER session_checkpoints_no_delete BEFORE DELETE ON session_checkpoints
    BEGIN SELECT RAISE(ABORT, 'session_checkpoints is append-only'); END
    """,
)
