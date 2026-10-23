# ADR 0031: Missing Guard context matters only when a rule needs it

Status: Proposed. Date: 2026-10-23. Schema: unchanged (14). No migration.
Amends ADR 0005 (Deterministic Guard), ADR 0017 section 8 and ADR 0018 section 2.

## Problem

ADR 0005 says Guard returns `confirm` when information needed for the decision is
missing. The implementation read "needed" as "always": a request without `action`, or
without both `tool` and `tool_category`, returned `confirm` even when no active rule
existed. The Host Hook adapters resolve `action` only for simple shell commands and file
tools (ADR 0017 section 8). Every compound shell command (`|`, `&&`, `>`, `$(...)`, a
leading `VAR=`), and every web, MCP or other tool call, therefore came back `confirm`
with an empty rule set. Claude Code asked the user each time; Codex, which cannot ask,
turned it into `deny`. A bound Codex project could not run `git log | head`.

## Decision

A missing `tool`, `tool_category`, `action` or `path` is insufficient only for an active
rule whose matcher uses that dimension. Guard already evaluates each rule this way: a
rule that does not mismatch on a present dimension but uses a missing one is
`uncertain`. Guard now returns `confirm` for missing context only when:

1. an uncertain rule is DENY, CONFIRM or VERIFY (an uncertain CONTEXT rule cannot change
   the decision, the same exclusion the scope check already applies), or
2. the request lacks the Task/Project identity needed to load a scoped DENY, CONFIRM or
   VERIFY rule (unchanged).

A definite DENY still wins over both. `uncertain` in the response still lists every
uncertain rule, CONTEXT ones included.

| Request | Active rules | Before | After |
| --- | --- | --- | --- |
| compound shell, web, MCP, other | none | confirm | allow |
| compound shell | `{action: rm}` DENY | confirm | confirm |
| any call without a path | `{path_prefix: /etc}` DENY | confirm | confirm |
| compound shell | `{tool_category: shell}` DENY | deny | deny |
| MCP call | `{tool_category: shell, action: rm}` DENY | confirm | allow |
| compound shell | `{action: rm}` CONTEXT only | confirm | allow |
| no Task/Project | scoped DENY rule exists | confirm | confirm |

## Why this keeps ADR 0005's guarantee

ADR 0005 requires DENY and CONFIRM rules to have a non-empty matcher, and an unresolved
call must not be treated as a safe ALLOW. Any blocking rule that depends on an
unresolved dimension is uncertain and still yields `confirm`. An `allow` with unresolved
fields means only that no active blocking rule depends on them. Rules meant to cover
compound commands or non-shell tools should match on `tool_category` (ADR 0018), which
the adapters always send.

## Callers

* `POST /v1/guard/check`: observable change for requests that omit fields while no
  blocking rule uses them.
* Host Hook `tool.before` (ADR 0017): the case above. Receipts already stored in
  `host_hook_deliveries` replay their original response.
* P3 lifecycle adapter (`adapter/protocol.py`): always sends `tool`, `action` and `path`;
  unaffected.
* P1 Codex hook: refuses unresolved calls locally before Guard; unaffected.

## Verification

Component tests over real HTTP: `tests/test_guard_unresolved_context.py`. They do not
establish native behaviour. That a bound Codex session now runs compound commands needs a
separate real-conversation Gate (H3 form).
