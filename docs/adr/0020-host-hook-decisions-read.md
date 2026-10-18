# ADR 0020: Read back recorded host-hook decisions

Status: Accepted for the P1 enforcement view. Extends ADR 0017 (the host-hook API) with one read-only route.
Date: 2026-10-18. Schema: unchanged (13). No migration.

## 1. Why

The dashboard must show what enforcement actually decided, per hook call (P1 traceability: from a rule to the
decision it produced). Core already stores each decision in `host_hook_deliveries.response_json`, but nothing reads it back.

## 2. Decision

Add `GET /v1/host-hooks/deliveries` (read only), scope `events:read`.

- Query: `limit` (1–100, default 50), `before_seq` (page cursor, rows older than this `rowid`), `decision` (`allow|deny|ask|none`).
- Returns newest first: `deliveries[]` with `seq`, `delivery_id`, `event`, `agent`, `host_id`, `trust`, `created_at`,
  `decision`, `guard_decision`, `reason`, `degraded`, `matched_rules[]` (`rule_id`, `rule_key`, `version`, `enforcement`, `severity`),
  plus `count`, `has_more`, `next_before_seq`.
- Tool inputs, transcripts and paths are never returned: the table keeps only digests, so none can be read back.

## 3. Consequences

- The dashboard's path allowlist gains exactly this one path (`web/server/core-paths.json`). The contract tests that pin
  the allowlist are updated by +1, each naming this ADR. Any further widening needs its own ADR.
- A `deny` here is Core's decision, not proof that an agent was stopped. Native effect stays a Gate matter (ADR 0005, P1-T10).
