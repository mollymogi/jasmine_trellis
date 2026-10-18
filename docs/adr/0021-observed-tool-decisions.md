# ADR 0021: Record the decision returned by a trusted tool hook

Status: Accepted for PR #14. Date: 2026-10-18. Schema: unchanged (13).

## Problem

Rechecking Guard while reporting a decision can disagree with the decision the
hook actually used, even with identical inputs if Authority changes between
requests. Local parser refusals do not have a Guard result at all.

## Contract

Extend protocol v1 additively with a `payload.decision_report` mode of `tool.before` on the existing
`POST /v1/host-hooks/events` (`hosthook:report`). Only a **system** actor bound to
the stated host may use this mode. Human and agent actors are refused,
including on replay. Ordinary `tool.before` requests keep their semantics.

The outer payload contains only `decision_report`. The report has exactly `decision` (`allow` or `deny`), `reason` (1–500 characters),
`tool_input_sha256` (the canonical input digest), and optional `guard`. A Guard snapshot contains `decision`
(`allow`/`deny`/`confirm`/`verify`), `request_sha256` and `matched`. Each matched
row contains the existing Guard fields `rule_id`, `rule_key`, `version`,
`enforcement`, `severity`, `origin_event_id`. Snapshots are validated, not
recomputed from current Authority. `deny`/`confirm` require a reported `deny`;
`allow`/`verify` require `allow`. Without Guard only a local `deny` is allowed.

Core stores the receipt in `host_hook_deliveries` and returns the reported
decision, reason and optional Guard snapshot (`capability: ADVISORY`). It does
not run Guard, create Evidence, mutate Truth or grant permission. The response adds
`decision_source: adapter_report`. ADR 0020 reads add `decision_source`:
`adapter_report` for this mode, `core_guard` for ordinary tool.before, null for
other events. This is a
trusted adapter's report of its chosen output, not proof the native client
honoured it. The dashboard identifies the source beside the event name and
describes both sources. The existing schema-13 CHECK remains unchanged; no
migration or rewriting of historical receipts is needed.

No tool inputs, command, paths or transcript are in a decision report. Only
native correlation IDs, an input digest and rule references are sent. Core keeps
the request hash, deterministic delivery ID and decision/rule snapshot; plaintext
native IDs and the input digest remain in private hook traces. The adapter uses a
separate deterministic delivery ID namespace and omits `occurred_at` so an
identical replay has identical bytes. Changed content conflicts with HTTP 409.
Reporting is best effort with a one-second HTTP timeout; failure is traced and
does not change the decision already selected by the hook. Outages and invalid
binding/lease/credentials can still leave a reporting gap; never invent a row.

## P1 adapter

P1 sends `tool_category: shell` to its one Guard check, maps confirm to native
deny and verify to native allow, then reports that exact choice. Parser refusals
and Guard errors report a generic local denial without a fabricated rule.
Reporting errors do not leak exception text or command input.

Native acceptance must join a genuine Codex PreToolUse, its output, the absence
of the denied file effect, the persisted delivery and a real dashboard row.
Component tests alone cannot establish that chain.
