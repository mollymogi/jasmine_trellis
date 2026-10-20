# ADR 0025: Read-only native Context input receipts

Status: implemented for opt-in isolated validation; scoped author verification, not full T04/P6 signoff. No schema change.

## Boundary

A built pack, emitted hook bytes and an assistant's response are distinct observations. Only a completed real native turn's exact same-turn `developer/input_text` record can establish input matching. The collector reuses ADR0013's `typed_input` matcher and bound native-session reader. It retains only session identity, turn identity, the matching Context input and native completion metadata in a private write-once receipt; the original full transcript remains private. It checks source, Task, host, pack/emission hash and completed native CLI output. Receipts are validated in an unpublished temporary directory, then published atomically without overwrite.

The receipt root is operator configured (`JASMINE_CONTEXT_DELIVERY_ROOT`), absolute, canonical, owner-only and not selectable by requests. The local file owner/collector is trusted; this does not protect against that owner forging host data. Public Raw Events, assistant claims, a client-supplied filename or a model-provided receipt body cannot register a receipt. Receipt reads reject symlinks, unsafe modes, malformed/oversized records and mismatched relationships. Existing adapter private-file and native-proof validators are reused.

`capture.context_probe` is an explicit per-invocation CLI probe for SessionStart/UserPromptSubmit. It uses the existing Host Hook Protocol, verifies exact saved pack bytes and `check-current` immediately before emission, and records `EMITTED_NOT_DELIVERY_PROOF`. It excludes unverified/ephemeral sources using ADR0024's persisted-header check. It is not installed into the workspace's user-trusted Desktop hooks and does not enable automatic interpretation or Memory extraction. Native delivery remains a separate later collection step.

## Read contract

`GET /v1/context/{ctx}/delivery` requires the same `context:read`, `checkpoint:read`, `objects:read`, `state:read`, `authority:read`, `evidence:read`, `events:read` scopes as Context inspection. No query fields or write method are accepted. Existing Context object validation and scope policy are retained; this adds no new project ACL claim. The proxy forwards the route only with the existing explicit `P6_ENABLE_P3_READS` switch.

Response: `{delivery: {context_pack_id, status, historical: true, current_use_proved: false, trust_boundary: "operator_owned_native_collector", record}}`. Status is one of `UNAVAILABLE` (no root configured), `NO_RECORD`, `INVALID_RECORD`, `MATCHED_NATIVE_INPUT`. `record` is null except for matching input and contains only source/native IDs, observed UTC time, Context digest, original rollout digest and receipt digest. Neither native transcript text, local paths, hook commands nor credentials are returned. Invalid evidence never becomes a missing record or a match. HTTP auth errors remain errors, not an empty collection.

The Core independently checks receipt structure, matching pack/source/Task/host/digests and native session/turn, same-turn developer input, one final completion and normalized completion time. Native 0.162.1 completion seconds are normalized to UTC; booleans/floats/out-of-range values are refused. Reads do not call Codex, Hindsight, Context build/check-current or change Truth, Memory, jobs or Events. Ordinary HTTP audit/key-use metadata may advance.

The Dashboard adds a concise receiver card with folded source/native identifiers. It labels a match as a historical host-collected observation, not current use or independent signoff. It treats unknown statuses, broken shapes and read failures as unverified; a built pack with no receipt stays unverified. The older frozen P3 contract is not rebound: this additive route is separately recorded in `web/contracts/p6-context-delivery.v1.json`.

## Acceptance

See `../validation/p6-native-context/RESULT.md` and its source/evidence manifest. The rehearsal uses a consistent backup of the isolated DB, a new Task and private workspace/ports/credentials. It does not restart Hindsight, modify daily services, change the existing Desktop Hook definitions or start the stopped P4 benefit experiment. Actual native CLI input matching and browser readback close this bounded receiver slice only. Full T04's original conversation → interpretation/resolution → Context → tool/Evidence/State trace and independent review remain separate.
