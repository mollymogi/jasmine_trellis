# ADR 0024: Opt-in native Codex conversation capture

Status: implemented for operator-bound isolated environments; native CLI acceptance passed; ordinary Desktop activation awaits user hook trust.

## Decision

Use existing `/v1/sessions` and `/v1/events` APIs to register server-assigned Core sessions and attach verbatim native user prompts and last assistant messages. The capture adapter reads native `SessionStart`, `UserPromptSubmit`, `Stop`, and `SessionEnd` payloads. It does not scrape transcripts, infer messages, run an interpreter, approve rules, or write Hindsight. No API contract or schema change is required, and the running Core/Hindsight need not restart.

An operator-owned binding selects one loopback Core, project, optional task, and private key files. System credentials carry only `objects:write` and `events:write`; the separate prompt credential carries `events:write` for human attribution. Native ownership and text cannot select destinations or approve Authority. These are local single-user trust boundaries, not a multi-user ACL.

Registration and message events have deterministic event IDs. Start/resume/retries reuse the session; identical text in different native turns remains distinct. The adapter checks its configured directory boundary; `--exact-root` excludes nested repositories from workspace-level hooks. Missing native assistant text, recursive Stop and subagent events do not invent a main-thread answer. Errors produce a failed private receipt and a visible hook warning; this adapter has no durable offline queue and makes no offline-delivery claim. `NATIVE_HOOK` describes the claimed channel; acceptance additionally binds actual CLI transcript, session/turn IDs and API readback. SessionEnd is an immutable event; the existing P0 session projection is not rewritten to a closed status.

## Codex hook loading and trust

Codex linked worktrees load hooks from the common repository. The isolated launcher reads the actual `hooks/list` inventory, refuses managed hooks, disables inherited non-managed hooks for that invocation, then supplies exactly four isolated lifecycle hooks through explicit configuration overrides. Exec receives the overrides in its own argument parser. Nothing writes persisted hook trust. Normal interactive use requires the user's `/hooks` review; controlled acceptance alone used the official one-off reviewed-hook bypass.

Workspace Desktop hooks are installed only at `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/.codex/`, capture that exact working directory, and exclude the nested daily V1 repository. The read-only inventory confirms all four are currently untrusted. A real newly created Desktop conversation completed but yielded no capture receipt; ordinary Desktop auto-capture is not accepted yet.

## Long-term memory remains separate

The explicitly started isolated Memory worker scans committed eligible sources. Ordinary prompts, assistant messages and task-scoped decisions do not become project memory. In the acceptance run, the operator approved a project-scoped decision expressing the user's existing instruction to leave daily services unchanged. Its native prompt origin, approval event, job, real Hindsight document and extracted memory unit were read back. This verifies eligible-source retention, not automatic semantic interpretation, native context injection, recall benefit or full P4/V1 acceptance.

The worker recovery cursor was explicitly advanced past the existing synthetic demonstration history before activation, with the prior/new cursor recorded. Synthetic records stay labelled synthetic and are not retroactively sent to the engine as real conversation evidence. The worker and Hindsight remain running in the isolated environment.
