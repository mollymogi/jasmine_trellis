# Human review dashboard — 2026-10-23

Scope: replace the default machine-oriented pending review grid and detail page
with a content-first queue and explicit human decisions. The pending collection,
resolver policy, request contracts, native capture, Guard and schema are unchanged.
This is an application page change; the shared Mogi UI primitives are reused.

## Human flow

- The queue shows a local row number, candidate content, named project, originating
  Agent, time, Chinese review reason and status. IDs and protocol diagnostics are
  available in details. The release warning banner is removed from these routes.
- Project filtering remains a server query; keyword/type filters describe only
  loaded suggestions. Pagination follows the server cursor; loaded count never
  claims a total. Source enrichment is cancellable and limited to four concurrent
  Event reads. Failure leaves the review visible with an unknown source label.
- A detail shows candidates, their scope, uncertainty/high-impact indicators,
  quoted evidence, the full original Event and a link to the correct session.
  The original source Agent is distinct from the model that extracted candidates.
- The existing narrow project-convention operation is available only when every
  candidate is an explicit PROJECT RULE/DECISION matching a human user.prompt's
  project. It creates NORMAL CONTEXT rules with empty matchers, never DENY rules.
  The operator sees consequences, selects a registered device, explains the
  decision and explicitly confirms. Server revisions/digest are returned verbatim.
- Other plans remain readable, with source clarification, rejection or advanced
  protocol planning available. They are not silently converted into rules or
  approved with default NO_ACTION payloads. Rejection retains source and audit.
- Existing audited advanced approval/rejection operations remain covered by their
  unchanged exact-body tests. Conflicts keep drafts; unknown results block another
  decision pending readback. No operation is retried automatically.

## Validation

- Full frontend suite: 58 files, 666 tests passed. Nine new human-review tests cover
  source identity/failure, read-only list, credential masking/search, cursor/failure
  retention, explicit narrow approval, rejection, conflicts, unknown results and
  refusal to widen inferred/cross-project/nonhuman candidates into project rules.
  Production TypeScript/build passed, with the existing bundle-size advisory.
  No Python code or API contract changed; the backend full suite was not rerun.
- Chrome against a consistent database copy: list, full source/session link,
  light/dark and 390px layouts passed; a copied pending review was rejected and
  read back. A separately labeled simulated-provider fixture was explicitly
  approved through the UI, then read back as an ACTIVE NORMAL CONTEXT project
  rule with an empty matcher. This is component acceptance, not a new real-user
  approval, model inference, Hindsight write or native Guard verification.
- Formal Chrome: all 27 pending rows were shown; light/dark, 390px, detail and
  source links passed, with no page errors, horizontal overflow or business POSTs.
- Deployed source: `6930a2818e18e084cda632748cd2f83c5593200e`. Only the dashboard
  process restarted. Existing review, review-change/request-key and rule/version/
  change table hashes matched before rollout and after formal browsing. Core and
  Hindsight PIDs were unchanged; processing and Memory services were not restarted.
  Capture Hooks, browser credentials, Memory configuration, capture controls and
  extraction provider/model controls were preserved. No schema migration occurred.

Private screenshots, credentials, copied database, deployment backup and raw proof
stay outside Git. The adjacent manifest binds sanitized results to evidence hashes.
Approval does not prove memory retention, Context delivery or Guard enforcement.
