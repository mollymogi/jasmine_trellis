# Daily conversation processing API extension (schema 14)

This additive extension does not alter frozen P2/P4 contracts.

- `GET /v1/conversation-processing`: `interpretations:read`, `events:read`,
  `resolutions:read`, `memory:read`. Optional exact `project_id`, `session_id`,
  `event_id`, `status`, `limit` (1–100), `offset` (>=0). Returns items, count,
  counts, started_at, worker_configured, last_tick_at, automatic_scope, limit,
  offset. Items include original source IDs/text, candidates, Review and resolution
  links, effect, actual Memory item and job states, attempt and error code.
- `POST /v1/conversation-processing/enqueue`: `interpretations:manage`,
  human/system principal. JSON must contain at least one of `event_id`,
  `session_id`, `project_id` and no other fields. Combined scope must match.
  Exact Event may be a user or assistant message; session/project backfill includes
  user prompts only. Accepts <=200 eligible unprocessed local native messages.
  Source-key deduplication is durable. Returns 202 `{queued, scope}`.
- `POST /v1/conversation-processing/{event_id}/retry`: same management scope and
  principal requirement, empty JSON object. Only FAILED entries become PENDING;
  a processing audit is appended. It does not replace/delete old Interpretations.

Projection states: PENDING, PROCESSING, PENDING_REVIEW, APPLIED, NO_ACTION, FAILED,
REJECTED. APPLIED is not a Memory receipt. `effect=TASK_ONLY` creates no long-term
Memory source. MEMORY_ELIGIBLE waits for the existing Memory worker. Only a read
back ACTIVE/RETAINED Memory item is shown as stored. Pending/retrying/failed engine
jobs remain visible and use the existing Memory queue management surface.

The dashboard proxy requires `P6_ENABLE_CONVERSATION_PROCESSING=1` and a same-origin
POST for both writes. The system worker is configured by the operator, never by a
browser body. The daily route does not expose audit/admin APIs or bypass review.
