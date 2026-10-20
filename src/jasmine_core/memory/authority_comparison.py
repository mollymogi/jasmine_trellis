"""Read-only linked-version comparison for the Inspector (ADR 0022)."""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import clock


def compare_authority(conn: sqlite3.Connection, item: dict[str, Any], *,
                      may_read_authority: bool) -> dict[str, Any]:
    """Compare the explicit Rule link, never text semantics or recall eligibility."""
    result: dict[str, Any] = {
        "status": "UNVERIFIABLE", "reason": "origin_unavailable",
        "sampled_at": clock.now_rfc3339(), "historical_rule": None, "current_rule": None,
    }

    def verdict(status: str, reason: str) -> dict[str, Any]:
        return {**result, "status": status, "reason": reason}

    if not may_read_authority:
        return verdict("UNAVAILABLE", "authority_read_required")
    if item["memory_type"] != "CONFIRMED_DECISION":
        return verdict("NOT_APPLICABLE", "not_a_confirmed_decision")
    if not item["origin_rule_id"] or not item["origin_rule_version"]:
        return verdict("UNVERIFIABLE", "origin_link_missing")
    # One statement binds status, revision, scope and both version bodies to
    # one SQLite snapshot even if a concurrent writer supersedes the Rule.
    row = conn.execute(
        "SELECT r.*, v.content AS current_content, v.origin_event_id AS current_origin, "
        "v.change_event_id AS current_change, h.version AS historical_version, "
        "h.content AS historical_content, h.origin_event_id AS historical_origin, "
        "h.change_event_id AS historical_change, c.change_event_id AS status_event "
        "FROM rules r LEFT JOIN rule_versions v ON v.rule_id=r.rule_id "
        "AND v.version=r.current_version LEFT JOIN rule_versions h ON h.rule_id=r.rule_id "
        "AND h.version=? LEFT JOIN rule_changes c ON c.rule_id=r.rule_id "
        "AND c.revision=r.revision WHERE r.rule_id=?",
        (item["origin_rule_version"], item["origin_rule_id"]),
    ).fetchone()
    if row is None:
        return verdict("UNVERIFIABLE", "origin_rule_unavailable")
    kind = row["scope_kind"]
    scope_matches = (
        (kind == "global" and row["project_id"] is None and row["task_id"] is None)
        or (kind == "project" and row["project_id"] == item["project_id"]
            and row["project_id"] is not None and row["task_id"] is None)
        or (kind == "task" and row["project_id"] == item["project_id"]
            and row["task_id"] == item["task_id"] and row["task_id"] is not None)
    )
    if not scope_matches:
        return verdict("UNVERIFIABLE", "origin_scope_mismatch")
    if row["historical_version"] is None or row["current_content"] is None:
        return verdict("UNVERIFIABLE", "rule_version_unavailable")
    result["historical_rule"] = {
        "version": row["historical_version"], "content": row["historical_content"],
        "origin_event_id": row["historical_origin"], "change_event_id": row["historical_change"],
    }
    result["current_rule"] = {
        "rule_id": row["rule_id"], "version": row["current_version"],
        "revision": row["revision"], "status": row["status"],
        "scope": {"kind": kind, "project_id": row["project_id"], "task_id": row["task_id"]},
        "content": row["current_content"], "origin_event_id": row["current_origin"],
        "change_event_id": row["current_change"], "status_event_id": row["status_event"],
    }
    if row["status"] == "RETIRED":
        return verdict("ORIGIN_RETIRED", "retired_decision")
    if row["status"] != "ACTIVE":
        return verdict("UNVERIFIABLE", "origin_not_active")
    if row["current_version"] != item["origin_rule_version"]:
        return verdict("VERSION_CHANGED", "superseded_decision")
    return verdict("VERSION_MATCH", "same_linked_version_only")
