"""Durable daily interpretation queue; existing Truth and Memory tables unchanged."""
NAME='m0014_conversation_processing'
VERSION=14
STATEMENTS=(
 """CREATE TABLE conversation_processing (
 event_id TEXT PRIMARY KEY REFERENCES events(event_id),
 status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','PENDING_REVIEW','APPLIED','NO_ACTION','FAILED','REJECTED')),
 interpretation_id TEXT REFERENCES interpretations(interpretation_id),
 resolution_id TEXT REFERENCES resolutions(resolution_id),
 attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
 lease_owner TEXT, lease_until TEXT, error_code TEXT,
 queued_at TEXT NOT NULL, updated_at TEXT NOT NULL
 )""",
 'CREATE INDEX ix_conversation_processing_queue ON conversation_processing(status,queued_at)',
 """CREATE TABLE conversation_processing_changes (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL REFERENCES events(event_id),
 action TEXT NOT NULL,actor_id TEXT REFERENCES actors(actor_id),detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),created_at TEXT NOT NULL
 )""",
 """CREATE TRIGGER trg_conversation_processing_changes_update BEFORE UPDATE ON conversation_processing_changes
 BEGIN SELECT RAISE(ABORT,'processing changes are append-only'); END""",
 """CREATE TRIGGER trg_conversation_processing_changes_delete BEFORE DELETE ON conversation_processing_changes
 BEGIN SELECT RAISE(ABORT,'processing changes are append-only'); END""",
)
