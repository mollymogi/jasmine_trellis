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
