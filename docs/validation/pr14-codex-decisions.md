# PR #14 — actual Codex decisions visible in the dashboard

Date: 2026-10-18 (America/Los_Angeles).
Result: **PASS_NATIVE_DECISION_READBACK_SCOPED** and
**PASS_BROWSER_NATIVE_DECISIONS_SCOPED**.

## Change and review findings

The original report recomputed Guard with different inputs, so its recorded
answer could disagree with P1's real output. Matching inputs alone would still
leave a race if Authority changed between calls. ADR 0021 instead records the
actual selected output and its original Guard rule snapshot. P1 now includes
the shell category in its one execution check. Local parser refusals also have
a report, without inventing a matching rule. A failed report keeps the selected
allow/deny unchanged and leaves a trace; its HTTP timeout is one second.

Decision reports are system-only, including on replay. Inputs and paths are
not sent in the report. An input digest permits private native correlation and
detects changed content on replay. Core-computed and adapter-reported decisions
are identified separately in the dashboard. No schema migration is required.

## Actual native run

One actual Codex CLI **0.162.1** session, three turns, on macOS. Each case joins
the native session/turn/call, input digest, P1 trace and persisted delivery.

| Case | Actual result | Core readback |
| --- | --- | --- |
| HARD rule refuses a scratch file creation | Native PreToolUse occurred; no execution item or PostToolUse, file absent | `deny`, original `gate-denied` v1 DENY rule |
| Shell-category VERIFY rule, `pwd` | Command actually executed | `allow`, original `gate-category-verify` v1 VERIFY rule |
| Compound shell command | Native PreToolUse occurred; no execution item or PostToolUse, file absent | `deny`, no fabricated Guard/rule |

The rule setup is explicitly synthetic isolated policy configuration; the three
Codex turns, native hooks, execution/refusal observations and browser reads are
real. The CLI used its configured model. After reviewing and hashing the scratch
hook sources, the invocation used the official one-off
`--dangerously-bypass-hook-trust` option and a command-scoped trusted scratch
project. No persistent trust or user-level hook configuration was edited; the
workspace-write sandbox remained enabled. This is not a manual `/hooks` review.

An initial harness incorrectly counted the Guard trace and reporting trace as
two calls. Its failure report and original native capture were retained. The
corrected readback deduplicated by turn/call, verified the same original capture,
and resumed that real session for the other two cases. No product source or
acceptance criterion was changed to repair that bookkeeping error. Native
duration for that first capture is not reported.

## Actual browser

Built dashboard + its real allowlisted proxy + scratch Core, driven in real
Google Chrome **155.0.8059.80**. **18/18 checks PASS**:

- Rules and Conversation each show the same three real native delivery IDs.
- Both pages show two denials and one allow, with the expected rule versions.
- Both distinguish `Hook 上报` from a Core-computed answer.
- Both pages' deny filter retains exactly the two real denials.
- Decision requests all return 200; no browser page error.

Five private screenshots include the full pages, filtered pages and the compact
decision panel. Digests, native IDs, rule IDs and the 119-file source hash map are
in the [sanitized manifest](pr14-codex-decisions-manifest.json). Source files in
the deployed hook snapshot were compared byte-for-byte with the delivered code.
Raw transcripts, tokens, bindings and databases are not published. Owned scratch
services are stopped; credentials and the scratch database are deleted after
exporting the private decision receipts. The daily deployment is unchanged.

## Regression

- Focused P1 / host-hook / schema suites: **63 tests PASS**.
- Full Python suite with the existing canonical context venv launcher:
  **1782 tests, 0 failures, 11 existing skips**, 64.848 seconds.
- Frontend: **549 tests PASS**; TypeScript and production build PASS.
- Includes category VERIFY/CONFIRM, Authority change after the check, local
  parser refusals, report failure isolation, system-only replay, unchanged replay,
  changed-content conflicts, invalid snapshots and dashboard source labels.

## Limits

This verifies PR #14's decision-to-dashboard chain on one macOS installation.
It is not a fresh complete P1-T10 state/evidence/acceptance run, whole P6/V1
acceptance, production deployment, concurrency test or cross-device test.
A decision report alone never proves native enforcement or promotes INFO
tool evidence to PASS. Invalid binding/lease/credentials or an unavailable
reporting endpoint can still leave a reporting gap; no row is fabricated.
