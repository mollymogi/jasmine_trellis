"""Route table for the Host Hook Protocol endpoint (ADR 0017 section 3).

Follows the `memory.api` / `validation.reads` pattern: the shared route table
in `api.handlers` appends this tuple instead of growing a second dispatcher.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import errors
from ..api.request import Response
from .service import HostHookService

HOST_HOOK_PATH = "/v1/host-hooks/events"
DELIVERIES_PATH = "/v1/host-hooks/deliveries"
DECISIONS = ("allow", "deny", "ask", "none")


def post_event(request, core, principal, match):
    if request.query:
        raise errors.InvalidRequest("host hook events take no query parameters")
    status, body = HostHookService(core).handle(request.json_body(), principal)
    return Response(status, body)


def list_deliveries(request, core, principal, match):
    """Recorded hook decisions, newest first, for the dashboard's enforcement view (read only).

    Only what Core already kept is returned: the decision, the Guard's matched rules and
    the agent name. Tool inputs and transcripts were never stored (only their hashes), so
    none can be read back here.
    """
    decision = request.param("decision") or None
    if decision is not None and decision not in DECISIONS:
        raise errors.InvalidRequest(f"decision must be one of {', '.join(DECISIONS)}", field="decision")
    limit = _bounded_int(request, "limit", 50, 1, 100)
    before = _bounded_int(request, "before_seq", 0, 0, 2**62) or None
    rows = core.conn.execute(
        "SELECT rowid AS seq, delivery_id, event, agent_name, host_id, trust, response_json, created_at"
        " FROM host_hook_deliveries"
        " WHERE (? IS NULL OR rowid < ?) AND (? IS NULL OR json_extract(response_json, '$.decision') = ?)"
        " ORDER BY rowid DESC LIMIT ?",
        (before, before, decision, decision, limit + 1)).fetchall()
    has_more = len(rows) > limit
    items = [_delivery(row) for row in rows[:limit]]
    return Response(200, {"deliveries": items, "count": len(items), "has_more": has_more,
                          "next_before_seq": items[-1]["seq"] if has_more else None})


def _bounded_int(request, name: str, default: int, low: int, high: int) -> int:
    raw = request.param(name)
    if raw is None or raw == "":
        return default
    if not re.fullmatch(r"[0-9]+", raw):
        raise errors.InvalidRequest(f"{name} must be an integer", field=name)
    value = int(raw)
    if not low <= value <= high:
        raise errors.InvalidRequest(f"{name} must be between {low} and {high}", field=name)
    return value


def _delivery(row) -> dict[str, Any]:
    response = json.loads(row["response_json"])
    guard = response.get("guard") or {}
    matched = [{"rule_id": m.get("rule_id"), "rule_key": m.get("rule_key"), "version": m.get("version"),
                "enforcement": m.get("enforcement"), "severity": m.get("severity")}
               for m in guard.get("matched") or [] if isinstance(m, dict)]
    return {"seq": row["seq"], "delivery_id": row["delivery_id"], "event": row["event"],
            "agent": row["agent_name"], "host_id": row["host_id"], "trust": row["trust"],
            "created_at": row["created_at"], "decision": response.get("decision"),
            "guard_decision": guard.get("decision"), "reason": response.get("reason"),
            "degraded": response.get("degraded") or [], "matched_rules": matched,
            "decision_source": response.get("decision_source", "core_guard" if row["event"] == "tool.before" else None)}


def routes(Route):
    return (
        Route("POST", re.compile(r"^/v1/host-hooks/events$"), post_event, "hosthook:report"),
        Route("GET", re.compile(r"^/v1/host-hooks/deliveries$"), list_deliveries, "events:read"),
    )
