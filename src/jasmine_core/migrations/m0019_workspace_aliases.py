"""Tailnet clients and confirmed workspace aliases.

A git remote maps to one project in workspace_remotes. workspace_aliases may
contain several rows for that remote (one host and root each) and therefore
has no UNIQUE index on remote_key. The alias's project_id has to equal the
remote row when remote_key is set; SQLite foreign keys cannot compare those
two columns, so the triggers reject a mismatch before it is stored.
"""

from __future__ import annotations

NAME = "m0019_workspace_aliases"
VERSION = 19

STATEMENTS = (
    """
    CREATE TABLE tailnet_clients (
        host_id      TEXT PRIMARY KEY REFERENCES hosts(host_id),
        display_name TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
        created_at   TEXT NOT NULL,
        revoked_at   TEXT
    )
    """,
    """
    CREATE TABLE workspace_remotes (
        remote_key TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE workspace_aliases (
        host_id    TEXT NOT NULL REFERENCES hosts(host_id),
        root       TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        remote_key TEXT REFERENCES workspace_remotes(remote_key),
        confirmed  INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
        created_at TEXT NOT NULL,
        PRIMARY KEY (host_id, root)
    )
    """,
    """
    CREATE TRIGGER trg_workspace_alias_remote_project_insert
    BEFORE INSERT ON workspace_aliases
    WHEN NEW.remote_key IS NOT NULL
     AND NOT EXISTS (
        SELECT 1 FROM workspace_remotes
        WHERE remote_key = NEW.remote_key AND project_id = NEW.project_id
     )
    BEGIN
        SELECT RAISE(ABORT, 'alias project_id must match workspace_remotes');
    END
    """,
    """
    CREATE TRIGGER trg_workspace_alias_remote_project_update
    BEFORE UPDATE ON workspace_aliases
    WHEN NEW.remote_key IS NOT NULL
     AND NOT EXISTS (
        SELECT 1 FROM workspace_remotes
        WHERE remote_key = NEW.remote_key AND project_id = NEW.project_id
     )
    BEGIN
        SELECT RAISE(ABORT, 'alias project_id must match workspace_remotes');
    END
    """,
)
