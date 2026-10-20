# P6 native Context reception and Dashboard readback

Result: **PASS_NATIVE_CONTEXT_DELIVERY_SCOPED_AUTHOR_VERIFICATION** — real macOS Codex CLI input matching plus actual Core/Dashboard readback. This is author verification of the receiver slice, not independent signoff or the complete P6-T04/P6/V1 Gate.

## Delivered

- Opt-in per-invocation `--context-probe` reuses the existing Host Hook Protocol to build task-bound Context, verifies exact persisted text and fresh `check-current`, then emits it through `hookSpecificOutput.additionalContext`. Its receipt is explicitly `EMITTED_NOT_DELIVERY_PROOF`.
- A separate collector verifies a completed real native CLI turn, its exact same-turn developer input, the Core source/Task/host and emitted pack digest. It publishes a private, write-once receipt only after validation. Assistant text is never input proof.
- `GET /v1/context/{ctx}/delivery` reads and validates those local operator-owned receipts, returns metadata only and adds no schema or Truth-writing route. API scopes equal Context inspection. No request can choose a path or upload a receipt.
- The Context page leads with one concise receiver card, with details folded by default. Matching input, absent receipts, invalid receipts and API errors remain distinct. Historical delivery never asserts current use or an independent signature. The original budget record remains separate from reception.

## Actual native chain

| Record | Identifier |
| --- | --- |
| Native CLI / turn | `01a1571c-4f0d-7671-a974-88f6528880cd` / `01a1571c-5c8b-7cd1-925b-c7e7fc93bae7` |
| Isolated Task | `tsk_01M5BHG8MEQ7YXHHPGEW4QNFHA` |
| Original native prompt Event | `evt_R7NG3MT58Z72JWEGTNWAYR31K7` |
| Exact Context | `ctx_01M5BHRYXFN799A2KHY5VHZR99` |
| Rendered content digest | `8e3cf1a6df2674873d364c6e28438c4da3e83a3b264537a85f0af36547e0b3ae` |
| Pack size | 577 selected-encoding tokens / 1,825 UTF-8 bytes |

The operator-initiated CLI turn was asked to report the current Task title and project environment constraints from its provided Context, with no tool calls. The Context includes the project's previously user-confirmed isolation constraints, preserved in a consistent backup of the isolated database. The real native developer input matches all 1,825 bytes. The response also reported the correct Task and constraints, but that prose is supplementary and is not the reception proof.

The native version is CLI 0.162.1. This is not a new manually typed Desktop acceptance and does not install Context injection into the user's existing Desktop capture Hooks. The one-off reviewed CLI hook-trust bypass did not persist configuration or trust. Original P3 acceptance and the previous Desktop-capture acceptance retain their original evidence/limits.

## Validation

- 66 Context-related backend checks, 22 Codex checks, 197 host-hook regression checks and 11 proxy-contract checks passed. The 66 include 10 new receipt/API/probe/time cases with actual scratch HTTP. Component fixtures use explicitly fake tokenizers where stated; the native case used the existing pinned tiktoken 0.14.0 asset, verified offline.
- 596 frontend checks passed, including 12 new receiver-card cases; production build passed. The selected-encoding count does not measure hidden platform prompt tokens or establish the model's tokenizer.
- 10 actual Chrome/Core readback assertions passed: folded details, exact native IDs, source Event navigation, matching pack count, no-receipt rejection, preserved invalid-receipt rejection, historical limits and GET-only traffic. Thirteen Truth/Context/Memory/source tables have identical content hashes before and after the browser run. Normal HTTP audit and key-use metadata are outside that equality.
- Native transcripts stay private. The API response exposes neither transcript text nor local file paths or credentials. Receipt storage trusts the configured local owner/collector; it is not tamper-proof against that owner. No model-visible writable registration endpoint exists.
- Existing uncommitted conversation UI changes from the other user-directed chat were preserved and excluded from this commit. The evidence inventory binds the actual served build and its additional working-tree source digests; this is not represented as a clean-archive, independently signed integration result.

## Preserved unsuccessful attempts

The first native attempt used an invalid 20-second binding timeout; the existing binding contract permits at most 10. It produced failed emissions and no accepted receiver receipt. The second attempt established exact native input but found that this CLI records completion as Unix seconds, not an RFC3339 string. Its original invalid receipt is preserved and visibly rejected; normalization and pre-publication validation were added before the final native run. Early component failures also caught macOS temporary-path canonicalization and adapter-private-file exceptions; they were corrected before acceptance. A prior Codex regression run hit a test subprocess's 2-second timeout; its unchanged-code rerun passed all 22 checks. Both logs remain preserved; no unverified scheduling cause is asserted.

None of these diagnostics is relabelled a formal P7 FAIL Run, and none upgrades historical P3/P4/P6 evidence.

## Runtime continuity and remaining bounds

The first runtime boundary snapshot found no listeners on the old isolated Core/Dashboard ports 65015/65016, despite stale READY PIDs. Its worker was also absent when checked. Hindsight 53201 and PostgreSQL 53202 remained running throughout. The old isolated entry and worker were restored with their original DB, keys and ports as detached owned processes. Hindsight was connected as an already-running external engine; it was neither restarted nor adopted/killed by a new controller. Thus the recovered entry's process-start button is disabled rather than offering a misleading restart of an unowned process. This affects the isolated process-control mode only; the daily service is unchanged.

All 19 daily-service configuration/launcher/plist hashes and all four daily listener PID sets are unchanged. The existing Desktop Hook definition bytes are unchanged. Daily schema remains untouched; no schema migration was introduced. The original isolated DB is schema 13. The new Context rehearsal has its own DB, workspace, credentials and detached Core/Dashboard on 64338/64339; its receipt root is not enabled on the old entry. The existing Dashboard token also works on the consistent rehearsal backup. Hindsight is still PID 25204, PostgreSQL PID 25235; no P4 benefit experiment was started.

Still separate: full native original-message → Interpretation/Resolver → Rule/Task → Context → tool/Evidence/State trace, ordinary Desktop Context injection, compact/handoff integration of this view, independent review and full P6-T04/P6/V1 acceptance. This receipt neither changes Authority nor proves Memory recall/benefit.

Evidence index: `manifest.json`. The local rehearsal is left available for inspection at:
`http://127.0.0.1:64339/?ui=native-context-20261019#/context/ctx_01M5BHRYXFN799A2KHY5VHZR99`.
