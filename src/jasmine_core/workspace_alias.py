"""Canonical git remotes and confirmed workspace aliases.

The raw origin is never stored or logged. Userinfo is stripped before a key
is built, and only the host's case is folded. Confirmation is a hub-local
write; resolve itself does not insert an alias, a remote, or a project.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from . import clock, db, errors, ids

_MAX_ORIGIN = 2048
_MAX_ROOT = 4096
_URL_SCHEMES = frozenset({"ssh", "https", "http", "git"})
_SCP_RE = re.compile(r"^(?:(?P<userinfo>[^@\s]+)@)?(?P<host>[^:@/\s]+):(?P<path>\S+)$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

OriginFor = Callable[[str], str | None]


class ConfirmError(Exception):
    """The operator's confirmation was refused. The message has no raw origin."""


@dataclass(frozen=True)
class ResolveOutcome:
    status: str
    project_id: str | None
    alias_conflict: bool
    matched_by: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "created": False,
            "alias_conflict": self.alias_conflict,
            "matched_by": self.matched_by,
        }


def normalize_root(value: Any) -> str:
    """POSIX absolute path as stored. Do not resolve it and do not open it."""
    if (not isinstance(value, str) or "\x00" in value or not value.startswith("/")
            or value.startswith("//") or len(value) > _MAX_ROOT):
        raise errors.InvalidRequest("root must be an absolute path", field="root")
    if value != "/":
        value = value.rstrip("/")
    if not value.startswith("/"):
        raise errors.InvalidRequest("root must be an absolute path", field="root")
    return value


def classify_origin(origin: str | None) -> tuple[str, str | None]:
    """Return ``(remote_kind, remote_key)``. ``remote_key`` is set only for ``url``.

    ``file:`` URLs and local paths are ``local``. Empty input is ``absent``.
    The returned key never contains userinfo.
    """
    if origin is None or origin == "":
        return "absent", None
    if not isinstance(origin, str) or len(origin) > _MAX_ORIGIN or "\x00" in origin:
        raise errors.InvalidRequest("origin is not a usable git remote", field="origin")
    text = origin.strip()
    if text == "":
        return "absent", None
    if any(char in text for char in "\r\n\t"):
        raise errors.InvalidRequest("origin is not a usable git remote", field="origin")
    if text.startswith("/") or _DRIVE_RE.match(text):
        return "local", None
    if "://" in text:
        return _classify_url(text)
    scp = _classify_scp(text)
    if scp is not None:
        return scp
    return "local", None


def canonical_remote_key(value: str | None) -> str | None:
    """A hub-supplied key, or a raw origin reduced to a key. Empty stays empty."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise errors.InvalidRequest("remote_key must be a string", field="remote_key")
    text = value.strip()
    if text == "":
        return None
    if _looks_like_raw_origin(text):
        kind, key = classify_origin(text)
        if kind != "url" or not key:
            raise errors.InvalidRequest("remote_key is not a git remote", field="remote_key")
        return key
    host, sep, path = text.partition("/")
    if not sep or not host or not path or _forbidden_key_part(host) or _forbidden_key_part(path):
        raise errors.InvalidRequest("remote_key is not canonical", field="remote_key")
    cleaned = _repo_path(path)
    if cleaned is None:
        raise errors.InvalidRequest("remote_key must be host/path", field="remote_key")
    return f"{_fold_hostport(host)}/{cleaned}"


def resolve_request(conn, body: dict[str, Any]) -> ResolveOutcome:
    """Read-only resolve. A conflict is ``status == "alias_conflict"``.

    This function does not raise that conflict. The HTTP handler maps that
    status to :class:`errors.AliasConflict`.
    """
    missing = [name for name in ("host_id", "root", "origin", "remote_kind") if name not in body]
    if missing:
        raise errors.InvalidRequest("missing fields: " + ", ".join(missing), fields=missing)
    host_id = body["host_id"]
    if not isinstance(host_id, str) or not ids.is_id(host_id, "hst"):
        raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")
    root = normalize_root(body["root"])
    origin = body["origin"]
    if origin is not None and not isinstance(origin, str):
        raise errors.InvalidRequest("origin must be a string or null", field="origin")
    remote_kind = body["remote_kind"]
    if remote_kind not in {"url", "local", "absent"}:
        raise errors.InvalidRequest(
            "remote_kind must be url, local, or absent", field="remote_kind")
    kind, remote_key = classify_origin(origin)
    if kind != remote_kind:
        raise errors.InvalidRequest("remote_kind does not match origin", field="remote_kind")
    return _resolve(conn, host_id=host_id, root=root, remote_key=remote_key, remote_kind=kind)


def project_confirmed(conn, host_id: str, project_id: str) -> bool:
    if not isinstance(project_id, str):
        return False
    row = conn.execute(
        "SELECT 1 FROM workspace_aliases WHERE host_id=? AND project_id=? AND confirmed=1",
        (host_id, project_id),
    ).fetchone()
    return row is not None


def root_confirmed(conn, host_id: str, root: Any) -> bool:
    try:
        normalized = normalize_root(root)
    except errors.InvalidRequest:
        return False
    row = conn.execute(
        "SELECT 1 FROM workspace_aliases WHERE host_id=? AND root=? AND confirmed=1",
        (host_id, normalized),
    ).fetchone()
    return row is not None


def confirmed_project_ids(conn, host_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT project_id FROM workspace_aliases WHERE host_id=? AND confirmed=1",
        (host_id,),
    )
    return {row["project_id"] for row in rows}


def confirm_workspace(conn, *, host_id: str, root: str, remote_key: str | None,
                      project_id: str | None, create_project: bool,
                      actor_id: str | None) -> str:
    """Write one confirmed alias. A conflicting remote is left untouched.

    ``--create-project`` ignores ``project_id`` and mints a new empty project.
    Returns the project id that was confirmed.
    """
    _require_tables(conn)
    if not isinstance(host_id, str) or not ids.is_id(host_id, "hst"):
        raise ConfirmError("host_id must be a hst_ id")
    if conn.execute("SELECT 1 FROM hosts WHERE host_id=?", (host_id,)).fetchone() is None:
        raise ConfirmError("host_id is not registered")
    try:
        normalized = normalize_root(root)
        key = canonical_remote_key(remote_key)
    except errors.InvalidRequest as exc:
        raise ConfirmError(exc.message) from None
    existing = _remote_project(conn, key) if key else None
    if create_project:
        if existing is not None:
            raise ConfirmError("remote_key is already registered to a different project")
        if not actor_id:
            raise ConfirmError("create-project requires an actor_id")
        project_id = _create_empty_project(conn, host_id=host_id, root=normalized, actor_id=actor_id)
    else:
        if not isinstance(project_id, str) or not ids.is_id(project_id, "prj"):
            raise ConfirmError("project_id must name an existing project")
        if conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone() is None:
            raise ConfirmError("project_id must name an existing project")
        if existing is not None and existing != project_id:
            raise ConfirmError("remote_key is already registered to a different project")
    assert project_id is not None
    try:
        with db.transaction(conn):
            if key:
                current = _remote_project(conn, key)
                if current is None:
                    conn.execute(
                        "INSERT INTO workspace_remotes (remote_key, project_id, created_at)"
                        " VALUES (?, ?, ?)",
                        (key, project_id, clock.now_rfc3339()),
                    )
                elif current != project_id:
                    raise ConfirmError("remote_key is already registered to a different project")
            _upsert_alias(conn, host_id=host_id, root=normalized, project_id=project_id,
                          remote_key=key)
    except sqlite3.IntegrityError:
        raise ConfirmError("alias was not written") from None
    return project_id


def backfill_aliases(conn, *, host_id: str,
                     origin_for: OriginFor | None = None) -> list[str]:
    """Confirm hub paths from project descriptions. Does not open the network.

    One project for a normalized path is confirmed. Several projects on one
    path are reported and left unregistered. A remote key that already points
    elsewhere is reported and the remote row is not changed.
    """
    _require_tables(conn)
    if not isinstance(host_id, str) or not ids.is_id(host_id, "hst"):
        raise ConfirmError("host_id must be a hst_ id")
    if conn.execute("SELECT 1 FROM hosts WHERE host_id=?", (host_id,)).fetchone() is None:
        raise ConfirmError("host_id is not registered")
    reader = origin_for or local_origin
    groups: dict[str, list[str]] = {}
    for row in conn.execute("SELECT project_id, description FROM projects"):
        description = row["description"]
        if not isinstance(description, str) or not description.startswith("/"):
            continue
        try:
            root = normalize_root(description)
        except errors.InvalidRequest:
            continue
        if not os.path.isdir(root):
            continue
        groups.setdefault(root, []).append(row["project_id"])
    conflicts: list[str] = []
    plans: list[tuple[str, str, str | None]] = []
    for root in sorted(groups):
        project_ids = sorted(set(groups[root]))
        if len(project_ids) != 1:
            conflicts.append(
                "conflict: path " + root + " has projects " + ",".join(project_ids))
            continue
        plans.append((root, project_ids[0], _accepted_remote_key(reader(root))))
    with db.transaction(conn):
        for root, project_id, key in plans:
            registered = _remote_project(conn, key) if key else None
            alias_key = key
            if key and registered is not None and registered != project_id:
                conflicts.append(
                    "conflict: remote_key " + key + " path " + root
                    + " project " + project_id + " registered to " + registered)
                alias_key = None
            elif key and registered is None:
                conn.execute(
                    "INSERT INTO workspace_remotes (remote_key, project_id, created_at) VALUES (?, ?, ?)",
                    (key, project_id, clock.now_rfc3339()),
                )
            _upsert_alias(conn, host_id=host_id, root=root, project_id=project_id,
                          remote_key=alias_key)
    return conflicts


def local_origin(root: str) -> str | None:
    """``git remote get-url`` against a local config. Failure is an absent remote."""
    try:
        completed = subprocess.run(
            ["git", "-C", root, "remote", "get-url", "origin"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    raw = completed.stdout.decode("utf-8", "replace").strip()
    try:
        kind, key = classify_origin(raw)
    except errors.InvalidRequest:
        return None
    if kind != "url":
        return None
    return key


def _resolve(conn, *, host_id: str, root: str, remote_key: str | None,
             remote_kind: str) -> ResolveOutcome:
    alias = conn.execute(
        "SELECT project_id, remote_key FROM workspace_aliases"
        " WHERE host_id=? AND root=? AND confirmed=1",
        (host_id, root),
    ).fetchone()
    matched_by = "git-origin" if remote_kind == "url" else None
    remote_project = _remote_project(conn, remote_key) if remote_key else None
    if alias is not None:
        if not remote_key:
            if _alias_agrees(conn, alias):
                return ResolveOutcome("ok", alias["project_id"], False, matched_by)
            return ResolveOutcome("alias_conflict", None, True, matched_by)
        same_key = bool(alias["remote_key"]) and remote_key == alias["remote_key"]
        if same_key and _alias_agrees(conn, alias):
            return ResolveOutcome("ok", alias["project_id"], False, matched_by)
        # A non-empty key that is new, or that belongs to another project, does
        # not keep the old project_id and does not update workspace_remotes.
        return ResolveOutcome("alias_conflict", None, True, matched_by)
    if remote_key and remote_project is not None:
        return ResolveOutcome("confirmation_required", None, True, matched_by)
    return ResolveOutcome("confirmation_required", None, False, matched_by)


def _alias_agrees(conn, alias) -> bool:
    if not alias["remote_key"]:
        return True
    return _remote_project(conn, alias["remote_key"]) == alias["project_id"]


def _remote_project(conn, remote_key: str | None) -> str | None:
    if not remote_key:
        return None
    row = conn.execute(
        "SELECT project_id FROM workspace_remotes WHERE remote_key=?", (remote_key,)
    ).fetchone()
    return row["project_id"] if row else None


def _upsert_alias(conn, *, host_id: str, root: str, project_id: str,
                  remote_key: str | None) -> None:
    if remote_key and _remote_project(conn, remote_key) != project_id:
        raise ConfirmError("alias project_id must match workspace_remotes")
    conn.execute(
        "INSERT INTO workspace_aliases"
        " (host_id, root, project_id, remote_key, confirmed, created_at)"
        " VALUES (?, ?, ?, ?, 1, ?)"
        " ON CONFLICT(host_id, root) DO UPDATE SET"
        " project_id=excluded.project_id, remote_key=excluded.remote_key, confirmed=1",
        (host_id, root, project_id, remote_key, clock.now_rfc3339()),
    )


def _create_empty_project(conn, *, host_id: str, root: str, actor_id: str) -> str:
    from .migrations import current_version
    from .models import NewObject
    from .objects import ObjectStore

    name = root.rstrip("/").rsplit("/", 1)[-1] or "workspace"
    try:
        result = ObjectStore(conn, schema_version=current_version(conn)).create(
            NewObject.project({
                "name": name,
                "description": root,
                "host_id": host_id,
                "source_system": "hub-workspace-confirm",
            }),
            actor_id=actor_id,
        )
    except errors.CoreError as exc:
        raise ConfirmError(exc.message) from None
    return result["object"]["project_id"]


def _require_tables(conn) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_aliases'"
    ).fetchone()
    if row is None:
        raise ConfirmError("workspace_aliases is missing; migrate core.db first")


def _looks_like_raw_origin(text: str) -> bool:
    if "://" in text or text.startswith("/") or text.startswith("git@") or "@" in text:
        return True
    host = text.split("/", 1)[0]
    if ":" not in host:
        return False
    _host, port = host.rsplit(":", 1)
    return not port.isdigit()


def _fold_hostport(hostport: str) -> str:
    host, sep, port = hostport.rpartition(":")
    if sep and port.isdigit():
        folded = host.lower()
        if not folded or not port:
            raise errors.InvalidRequest("remote_key must be host/path", field="remote_key")
        return f"{folded}:{port}"
    folded = hostport.lower()
    if not folded or "/" in folded:
        raise errors.InvalidRequest("remote_key must be host/path", field="remote_key")
    return folded


def _classify_url(text: str) -> tuple[str, str | None]:
    try:
        parts = urlsplit(text)
        scheme = (parts.scheme or "").lower()
        if scheme == "file":
            return "local", None
        if scheme not in _URL_SCHEMES:
            raise errors.InvalidRequest("origin is not a usable git remote", field="origin")
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise errors.InvalidRequest("origin is not a usable git remote", field="origin") from None
    if not host:
        raise errors.InvalidRequest("origin is not a usable git remote", field="origin")
    if (scheme == "ssh" and port == 22) or (scheme == "https" and port == 443):
        port = None
    path = _repo_path(parts.path)
    if path is None:
        raise errors.InvalidRequest("origin is not a usable git remote", field="origin")
    suffix = f":{port}" if port else ""
    return "url", f"{host.lower()}{suffix}/{path}"


def _classify_scp(text: str) -> tuple[str, str | None] | None:
    matched = _SCP_RE.match(text)
    if matched is None:
        return None
    host = matched.group("host")
    if not host or _DRIVE_RE.match(text):
        return None
    path = _repo_path(matched.group("path"))
    if path is None:
        return None
    return "url", f"{host.lower()}/{path}"


def _forbidden_key_part(part: str) -> bool:
    # Slash stays: a canonical key's path is host/group/repo, not a single segment.
    return any(char in part for char in "@\\?# \t")


def _accepted_remote_key(value: str | None) -> str | None:
    """Reduce a backfill origin to a stored key. A raw origin is not printed."""
    if not value:
        return None
    try:
        return canonical_remote_key(value)
    except errors.InvalidRequest:
        return None


def _repo_path(path: str) -> str | None:
    cleaned = path.strip().replace("\\", "/")
    # Query and fragment can carry a token. Drop them before the .git suffix,
    # or `Repo.git?token=…` would keep both the token and `.git`.
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]
    cleaned = cleaned.lstrip("/").rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4].rstrip("/")
    if not cleaned or "\x00" in cleaned or any(char.isspace() for char in cleaned):
        return None
    return cleaned
