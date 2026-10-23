# Human memory catalog and intake audit — 2026-10-24

Scope: readable saved-memory list/detail and an expandable explanation of why
conversation capture, semantic processing, reviews and saved memories differ.
No Memory eligibility, extraction policy, provider/model configuration, approvals,
worker, Hook or backend API behavior is changed.

## Why eight items

A read-only audit at 2026-10-24 00:17 Asia/Taipei confirmed that the browser's eight
visible items equal the database's eight items. This was not a pagination cap or
an actor-visibility discrepancy. All eight had ACTIVE lifecycle and RETAINED engine
state, with eight DONE retain jobs. This records existing successful write receipts;
it is not a fresh recall or verification of every remote document.

Seven items date from October 12: five evidence-backed results, one failure lesson
and one confirmed decision from earlier validation/operations. One October 23 item
is a stable project fact derived from a later approved project convention. Existing
historical/test-oriented content was retained, not deleted or relabeled silently.

The same audit found 149 conversation-processing records: 30 APPLIED, 43 FAILED,
48 NO_ACTION and 28 PENDING_REVIEW. Applied actions were 29 CREATE_TASK and one
CREATE_RULE, so “applied” does not mean “saved as long-term memory.” Of the failures,
31 were provider_timeout, 11 were output-validation failures and one provider_error.
These failures remain unresolved; this UI change does not retry, approve or
reclassify them. The live counts may advance after this dated sample.

The processing and Memory populations have different sources and scopes. Their
counts must not be subtracted as if all processed prompts should become memories.

## UI and contract behavior

- Default list shows masked content, named project, Chinese memory type, lifecycle,
  engine-write state and time. Search examines masked loaded content; project,
  type and lifecycle filters are sent to Core. Current query counts are labeled
  as loaded counts. Existing keyset paging uses limit/after_memory_id; a full page
  exposes load more, and a short page proves that query is exhausted.
- Core's non_authoritative declaration survives pagination, including empty pages.
  It is not invented if any page omits it. Refused, not-found, dependency-pending,
  empty and next-page failures remain distinct; scope changes cancel old responses.
- The intake explanation loads processing and engine statistics only when expanded.
  Permission failure leaves the memory list intact and does not fabricate zero
  failures. Applied task operations are explicitly distinguished from Memory writes.
- Details show sanitized memory text without opening the raw-record sheet. Current
  Authority comparisons retain their original validity checks and warning behavior;
  historical memory content remains separate from current requirements. Existing
  lifecycle writes and source/audit panels retain their contract and explicit gates.

## Verification and rollout

- Full frontend suite: 59 files / 674 tests passed. Eight new catalog tests cover
  content/Chinese labels, read-only default, deferred stage counts, optional-stat
  permission failure, filters/masked search, keyset paging/failure/authority flags,
  delayed-scope cancellation, lifecycle-versus-retention and visible detail content.
  Final affected tests: 63 passed. TypeScript/production build passed with the
  existing bundle-size advisory. No backend code changed in this task; Python was
  not rerun for this frontend-only change.
- Database-copy and formal Chrome both passed: eight rows, project names, type
  filter, detail/source access, expandable counts, light/dark/726px/390px layouts,
  no horizontal overflow, page errors or business POSTs. No fake new memories were
  added for display and no existing Memory lifecycle action was submitted.
- UI source: `afc5ded6a60fb2c5bb0aa10475250fd2dda53e1b`. Only built frontend assets
  were replaced, with the former bundle retained for rollback. Core's existing
  release pointer remains `6930a2818e18e084cda632748cd2f83c5593200e`; upstream
  backend fixes on the stacked PR branches were deliberately not deployed here.
- Only dashboard restarted. Core/Hindsight PIDs were unchanged, processing and
  Memory were not restarted, and schema remained 14. Memory item/job/attempt and
  rule/version/change table hashes matched before deployment and after formal
  browsing. Runtime configuration, Hooks, credentials, Memory provider, capture
  controls and selected extraction provider/model settings retained their hashes.

Private database copies, raw audit records, screenshots and rollback artifacts
remain outside Git. The adjacent manifest contains sanitized evidence bindings.
The memory inventory remains eight; extraction failures are a separate outstanding
operational issue, not evidence of a Hindsight write backlog.
