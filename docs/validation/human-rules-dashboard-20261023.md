# Human Rule dashboard — 2026-10-23

Scope: explain current rules and replace the default machine-oriented Rules view.
No Guard activation/repair, rule cleanup, schema migration or change to existing
Authority write semantics is included.

The initial formal read-only audit found four ACTIVE rules: two project-owned
and two task-owned, all CONTEXT. The old default effective-global query returned
no rows. The new catalog displays all four without requiring a project/Rule ID.
Old fixture content is retained as stored, escaped and credential-masked; no
existing record was deleted, reclassified or silently hidden as test data.

Validation records are completed in the adjacent manifest after browser/deployment
verification. Component tests cover catalog permissions, pagination, scope/status
validation and read-only business data; frontend tests cover human labels,
credential masking, exact source scope, explicit proposal/approval, retained
verification fields, read revisions and unchanged legacy advanced command bodies.
The existing proxy contract snapshots explicitly include the additive catalog GET.

Write acceptance uses a consistent **database copy**, not the formal database.
The browser sequence creates a synthetic UI proposal from an existing source,
approves it, supersedes it and retires it. These are component simulations using
copied credentials/data; they do not prove a fresh human decision or native Gate.
The formal browser verification is strictly read-only. Private data/screenshots,
raw responses and database backups remain outside Git.

## Results

- Full Python suite: 1901 run, 11 skipped, zero failures. Frontend: 57 files,
  657 tests passed. TypeScript/production build passed; existing bundle-size
  advisory retained. Final guided-form accessibility checks: five passed.
- Database-copy Chrome: propose → explicit approve → supersede → explicit retire
  passed against the real Core, with source scope and five lifecycle snapshots.
- Formal Chrome: all four approved rules, named project filter, source/detail
  dialog and usage guide passed. Light, dark and 390px layouts were inspected.
  No page errors, horizontal mobile overflow or business POSTs occurred.
- Deployed source: `7f2a566902b2b099714c71ec77a9fcd62e4526fe`. The Rule, version
  and lifecycle table hashes match before/after deployment and formal browsing.
  Hook files, credentials, Memory-provider and extraction model controls match.
  Hindsight remained running as PID 1590. No schema migration was needed.
- A deployment check initially treated a newly-started processing operation as
  failure and rolled the pointer back. The check was corrected to distinguish
  the independent worker from Core-owned calls; final rollout restarted only Core
  and dashboard. The processing worker's SIGTERM handler finishes its current
  operation gracefully; no in-flight provider was forcibly killed.

This page's catalog filter describes rule ownership. The unchanged effective-rule
endpoint additionally merges inherited global/project/task rules. Successful
rule approval does not prove Context delivery or native Guard enforcement.
