# ADR 0022 — Read-only Memory / Authority comparison

Status: implemented candidate; scoped validation required. No schema migration.

`GET /v1/memory/items/{mem}` retains its `memory:read` and Memory project-ACL
checks. It adds `authority_comparison` after those checks. The comparison is
sampled from one SQLite SELECT joining the linked Rule and historical/current
versions. It never calls the engine or writes Rule, State, Memory or jobs.

The object has `status`, `reason`, `sampled_at`, `historical_rule` and
`current_rule`. Snapshots are null unless the token also has `authority:read`,
the origin belongs to the Memory's global/project/task scope, and both versions
resolve. Snapshots carry version, content and source/change Event IDs; current
also carries Rule ID, revision, status and scope.

Statuses: `VERSION_CHANGED` / `ORIGIN_RETIRED` mean the recorded decision is no
longer the current active source. `VERSION_MATCH` means only the linked version
is still ACTIVE, never semantic agreement or verification. `UNVERIFIABLE`
means missing origin/version, inaccessible or mismatched scope, absent Rule or
version, or non-active source. `UNAVAILABLE` (`authority_read_required`) does
not disclose the Rule. `NOT_APPLICABLE` covers other Memory types.

No text classifier is introduced. Unlinked prose may contradict Authority but
this bounded comparison cannot decide that. Lifecycle `recallable` still means
ACTIVE only; it is not permission to inject. Actual recall still uses the
existing provenance/filter policy. A pin never overrides that policy.

The Dashboard presents historical text and current Rule separately, with source
links and sampling time, prominent mismatch/retirement warnings, and no automatic
mutation or synthesis. Missing extension on an older Core is explicitly unavailable.
Trace `filtered` reasons remain as recorded at recall time, not today's state.
No schema, migration, Guard/recall policy or frozen contract/evidence is rewritten.
